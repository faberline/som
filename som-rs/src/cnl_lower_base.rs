use super::*;

impl Lowerer<'_> {
    pub(super) fn base_event(
        &mut self,
        index: usize,
        event: &SyntaxEvent,
    ) -> Result<bool, CompilerError> {
        match &event.kind {
            EventKind::Begin(Block::Resource(role)) => {
                let path = match role {
                    nlp::ResourceRole::Single => "/resource",
                    nlp::ResourceRole::Parent => "/resources/parent",
                    nlp::ResourceRole::Child => "/resources/child",
                };
                if *role != nlp::ResourceRole::Single && self.prompt.get("resources").is_none() {
                    self.put("/resources", json!({}));
                }
                self.put(path, json!({"fields": []}));
                self.paths.insert(index, path.into());
            }
            EventKind::Begin(Block::Query) => {
                let path = format!("{}/query", self.parent_path(event));
                self.put(&path, json!({}));
                self.paths.insert(index, path);
            }
            EventKind::Begin(Block::Capabilities) => {
                self.put("/capabilities", json!({}));
                self.paths.insert(index, "/capabilities".into());
            }
            EventKind::Begin(Block::Routes) => {
                let path = if self.version >= 6 {
                    "/operations"
                } else {
                    "/logic"
                };
                self.put(path, json!([]));
                self.paths.insert(index, path.into());
            }
            EventKind::Begin(Block::Checks) => {
                self.put("/tests", json!([]));
                self.paths.insert(index, "/tests".into());
            }
            EventKind::End(BlockKind::Resource | BlockKind::Query | BlockKind::Routes) => {
                let path = self.parent_path(event);
                self.locate(&path, &event.span);
            }
            EventKind::End(BlockKind::Capabilities | BlockKind::Checks) => {}
            EventKind::Clause(Clause::Header | Clause::NoCapabilities | Clause::EndRequest) => {}
            EventKind::Clause(Clause::Version { number, token }) => {
                self.version = *number;
                self.put("/version", json!(format!("som.prompt.v{number}")));
                self.locate("/version", &token.span);
            }
            EventKind::Clause(Clause::Package(token)) => {
                self.put("/package", json!(token.text));
                self.locate("/package", &token.span);
            }
            EventKind::Clause(Clause::Contract { id, hash }) => {
                let hash_value = scalar(hash)?;
                if !["related-business.v1", "related-flow.v1"].contains(&id.text.as_str()) {
                    return Err(at(
                        "UNSUPPORTED_CONTRACT",
                        &id.span,
                        Some("/contract/id"),
                        "Unknown public contract.",
                        "Bind the public contract for this prompt version.",
                    ));
                }
                self.put("/contract", json!({"id": id.text, "sha256": hash_value}));
                self.locate("/contract/id", &id.span);
                self.locate("/contract/sha256", &hash.span);
            }
            EventKind::Clause(Clause::ResourceName(token)) => {
                let path = format!("{}/name", self.parent_path(event));
                self.put(&path, json!(token.text));
                self.locate(&path, &token.span);
            }
            EventKind::Clause(Clause::Table(token)) => {
                let path = format!("{}/table", self.parent_path(event));
                self.put(&path, json!(token.text));
                self.locate(&path, &token.span);
            }
            EventKind::Clause(Clause::Path(token)) => {
                let path = format!("{}/path", self.parent_path(event));
                self.put(&path, scalar(token)?);
                self.locate(&path, &token.span);
            }
            EventKind::Clause(Clause::Field {
                name,
                required,
                required_token,
                ty,
                unique,
                unique_token,
                default,
            }) => {
                let mut field = json!({
                    "name": name.text,
                    "required": required,
                    "type": ty.text,
                    "unique": unique,
                });
                if let Some(token) = default {
                    field["default"] = scalar(token)?;
                } else if !*required && self.version >= 3 {
                    field["default"] = Value::Null;
                }
                let fields = format!("{}/fields", self.parent_path(event));
                let path = self.push(&fields, field);
                self.locate(&path, &event.span);
                self.locate(&format!("{path}/name"), &name.span);
                self.locate(&format!("{path}/required"), &required_token.span);
                self.locate(&format!("{path}/type"), &ty.span);
                self.locate(&format!("{path}/unique"), &unique_token.span);
                if let Some(token) = default {
                    self.locate(&format!("{path}/default"), &token.span);
                } else if !*required && self.version >= 3 {
                    self.locate(&format!("{path}/default"), &event.span);
                }
            }
            EventKind::Clause(Clause::ExactFilter { field, value_span }) => {
                let path = format!("{}/filter_field", self.parent_path(event));
                self.put(
                    &path,
                    field
                        .as_ref()
                        .map_or(Value::Null, |token| json!(token.text)),
                );
                self.locate(&path, value_span);
            }
            EventKind::Clause(Clause::DateFilter { field, value_span }) => {
                let path = format!("{}/date_field", self.parent_path(event));
                self.put(
                    &path,
                    field
                        .as_ref()
                        .map_or(Value::Null, |token| json!(token.text)),
                );
                self.locate(&path, value_span);
            }
            EventKind::Clause(Clause::QueryLimit(token)) => {
                let path = format!("{}/max_limit", self.parent_path(event));
                let limit = token.text.parse::<u64>().map_err(|_| {
                    at(
                        "INVALID_VALUE",
                        &token.span,
                        Some(&path),
                        "Query limit is outside the integer range.",
                        "Use an integer from 20 through 100.",
                    )
                })?;
                self.put(&path, json!(limit));
                self.locate(&path, &token.span);
            }
            EventKind::Clause(Clause::Relation(token)) => {
                self.put(
                    "/relation",
                    json!({"foreign_key": token.text, "on_delete": "restrict"}),
                );
                self.locate("/relation/foreign_key", &token.span);
                self.locate("/relation/on_delete", &event.span);
            }
            EventKind::Clause(Clause::MigrationRevision(token)) => {
                self.put("/migration", json!({"revision": token.text}));
                self.locate("/migration/revision", &token.span);
            }
            EventKind::Clause(Clause::PreviousRevision(token)) => {
                if let Some(token) = token {
                    self.put("/migration/down_revision", json!(token.text));
                    self.locate("/migration/down_revision", &token.span);
                } else {
                    self.put("/migration/down_revision", Value::Null);
                    self.locate("/migration/down_revision", &event.span);
                }
            }
            EventKind::Clause(Clause::Capability { kind, value }) => {
                let key = match kind {
                    nlp::Capability::Database => "database",
                    nlp::Capability::Authentication => "auth",
                    nlp::Capability::Storage => "storage",
                    nlp::Capability::Workflow => "workflow",
                    nlp::Capability::WorkflowValue => "value_field",
                    nlp::Capability::WorkflowGroup => "group_field",
                };
                let path = format!("/capabilities/{key}");
                if let Some(token) = value {
                    self.put(&path, json!(token.text));
                    self.locate(&path, &token.span);
                } else {
                    self.put(&path, Value::Null);
                    self.locate(&path, &event.span);
                }
            }
            EventKind::Clause(Clause::Route(route)) => {
                let value = match route {
                    nlp::RouteSyntax::Legacy(sentence) => json!(sentence),
                    nlp::RouteSyntax::Scoped { scope, sentence } => {
                        json!({"scope": scope, "step": sentence})
                    }
                    nlp::RouteSyntax::Selected { scope, action } => {
                        json!({"scope": scope, "action": action})
                    }
                };
                let parent = self.parent_path(event);
                let path = self.push(&parent, value);
                self.locate(&path, &event.span);
                if self.version >= 6 {
                    self.locate(&format!("{path}/scope"), &event.span);
                    self.locate(&format!("{path}/action"), &event.span);
                }
            }
            EventKind::Clause(Clause::Check(sentence)) => {
                let parent = self.parent_path(event);
                let path = self.push(&parent, json!(sentence));
                self.locate(&path, &event.span);
            }
            _ => return Ok(false),
        }
        Ok(true)
    }
}
