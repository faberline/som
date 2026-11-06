//! Lower the NLP syntax events into the existing typed prompt and source map.
use super::{CompilerError, ParsedCnl, SourceMap, SourceSpan, error};
use faberline_nlp_cnl as nlp;
use nlp::{Block, BlockKind, Clause, EventKind, SyntaxEvent, SyntaxReport, Token};
use serde_json::{Value, json};
use std::collections::BTreeMap;

#[path = "cnl_lower_base.rs"]
mod base;

fn span(value: &nlp::SourceSpan) -> SourceSpan {
    SourceSpan {
        line: value.line,
        column: value.column,
        end_line: value.end_line,
        end_column: value.end_column,
        byte_start: value.byte_start,
        byte_end: value.byte_end,
    }
}

fn at(
    code: &str,
    location: &nlp::SourceSpan,
    pointer: Option<&str>,
    cause: impl Into<String>,
    allowed: impl Into<String>,
) -> CompilerError {
    error(code, span(location), pointer, cause, allowed)
}

fn scalar(token: &Token) -> Result<Value, CompilerError> {
    serde_json::from_str(&token.text).map_err(|_| {
        at(
            "CNL_SYNTAX",
            &token.span,
            None,
            "Invalid JSON scalar.",
            "Use one JSON scalar literal.",
        )
    })
}

#[derive(Clone, Copy, Default)]
struct Context {
    transaction: bool,
    branch_depth: usize,
}

struct Lowerer<'a> {
    report: &'a SyntaxReport,
    prompt: Value,
    map: SourceMap,
    version: usize,
    paths: BTreeMap<usize, String>,
    contexts: BTreeMap<usize, Context>,
    actions: usize,
    branches: usize,
    transactions: usize,
}

impl Lowerer<'_> {
    fn parent_path(&self, event: &SyntaxEvent) -> String {
        event
            .parent
            .map(|id| self.paths[&id].clone())
            .unwrap_or_default()
    }

    fn put(&mut self, path: &str, value: Value) {
        let (parent, key) = path.rsplit_once('/').expect("prompt property path");
        self.prompt
            .pointer_mut(parent)
            .expect("syntax parent exists")
            .as_object_mut()
            .expect("syntax parent is an object")
            .insert(key.to_owned(), value);
    }

    fn push(&mut self, path: &str, value: Value) -> String {
        let array = self
            .prompt
            .pointer_mut(path)
            .expect("syntax list exists")
            .as_array_mut()
            .expect("syntax list is an array");
        let item_path = format!("{path}/{}", array.len());
        array.push(value);
        item_path
    }

    fn locate(&mut self, path: &str, location: &nlp::SourceSpan) {
        self.map.insert(path.to_owned(), span(location));
    }

    fn context(&self, event: &SyntaxEvent) -> Context {
        event
            .parent
            .and_then(|id| self.contexts.get(&id))
            .copied()
            .unwrap_or_default()
    }

    fn node_list(&self, event: &SyntaxEvent) -> String {
        let path = self.parent_path(event);
        match event.parent.map(|id| &self.report.events[id].kind) {
            Some(EventKind::Begin(Block::Transaction { .. })) => format!("{path}/steps"),
            _ => path,
        }
    }

    fn value(&mut self, path: &str, source: &nlp::ValueSyntax) -> Result<Value, CompilerError> {
        use nlp::ValueKind;
        let value = match &source.kind {
            ValueKind::Input(name) => {
                self.locate(&format!("{path}/input"), &name.span);
                json!({"input": name.text})
            }
            ValueKind::Step { name, field } => {
                self.locate(&format!("{path}/step"), &name.span);
                self.locate(&format!("{path}/field"), &field.span);
                json!({"step": name.text, "field": scalar(field)?})
            }
            ValueKind::Literal { ty, value } => {
                self.locate(&format!("{path}/type"), &ty.span);
                self.locate(&format!("{path}/literal"), &value.span);
                json!({"type": ty.text, "literal": scalar(value)?})
            }
        };
        self.locate(path, &source.span);
        Ok(value)
    }

    fn predicate(
        &mut self,
        path: &str,
        predicate: &nlp::Predicate,
    ) -> Result<Value, CompilerError> {
        use nlp::Comparison;
        let op = match predicate.operator {
            Comparison::IsNull => "is_null",
            Comparison::Eq => "eq",
            Comparison::Ne => "ne",
            Comparison::Lt => "lt",
            Comparison::Le => "le",
            Comparison::Gt => "gt",
            Comparison::Ge => "ge",
        };
        let left = self.value(&format!("{path}/left"), &predicate.left)?;
        self.locate(&format!("{path}/op"), &predicate.operator_span);
        let mut condition = json!({"op": op, "left": left});
        if let Some(right) = &predicate.right {
            condition["right"] = self.value(&format!("{path}/right"), right)?;
        }
        Ok(condition)
    }

    fn flow_event(&mut self, index: usize, event: &SyntaxEvent) -> Result<(), CompilerError> {
        match &event.kind {
            EventKind::Begin(block) => self.begin_flow(index, event, block)?,
            EventKind::End(kind) => {
                let path = self.parent_path(event);
                match kind {
                    BlockKind::Steps | BlockKind::Then | BlockKind::Else => {
                        self.locate(&path, &event.span)
                    }
                    BlockKind::Transaction => self.locate(&format!("{path}/steps"), &event.span),
                    BlockKind::Flow | BlockKind::Step | BlockKind::Branch => {}
                    _ => unreachable!("non-flow end handled by base"),
                }
            }
            EventKind::Clause(clause) => {
                let path = self.parent_path(event);
                match clause {
                    Clause::Input { name, ty } => {
                        let path =
                            self.push("/flow/inputs", json!({"name": name.text, "type": ty.text}));
                        self.locate(&format!("{path}/name"), &name.span);
                        self.locate(&format!("{path}/type"), &ty.span);
                        if self.prompt["flow"]["inputs"].as_array().unwrap().len() > 8 {
                            return Err(at(
                                "SCOPE_TOO_LARGE",
                                &name.span,
                                Some("/flow/inputs"),
                                "Too many typed inputs.",
                                "Use at most eight inputs.",
                            ));
                        }
                    }
                    Clause::Argument { name, value } => {
                        let path = format!("{path}/args/{}", name.text);
                        let value = self.value(&path, value)?;
                        self.put(&path, value);
                    }
                    Clause::Undo { mode, value_span } => {
                        self.locate(&format!("{path}/args"), &event.span);
                        self.locate(&format!("{path}/undo"), value_span);
                        self.put(
                            &format!("{path}/undo"),
                            mode.as_ref().map_or(Value::Null, |t| json!(t.text)),
                        );
                    }
                    Clause::Export { name, yes, no } => {
                        let path = format!("{path}/exports/{}", name.text);
                        self.locate(&path, &name.span);
                        let yes = self.value(&format!("{path}/then"), yes)?;
                        let no = self.value(&format!("{path}/else"), no)?;
                        self.put(&path, json!({"then": yes, "else": no}));
                    }
                    Clause::Result { name, value } => {
                        let path = format!("/flow/result/{}", name.text);
                        self.locate(&format!("{path}/@key"), &name.span);
                        let value = self.value(&path, value)?;
                        self.put(&path, value);
                    }
                    Clause::NoInputs | Clause::NoExports | Clause::NoResults => {}
                    _ => unreachable!("non-flow clause handled by base"),
                }
            }
        }
        Ok(())
    }

    fn begin_flow(
        &mut self,
        index: usize,
        event: &SyntaxEvent,
        block: &Block,
    ) -> Result<(), CompilerError> {
        let parent = self.parent_path(event);
        let mut context = self.context(event);
        let path = match block {
            Block::Flow(name) => {
                self.put(
                    "/flow",
                    json!({"name": name.text, "inputs": [], "steps": [], "result": {}}),
                );
                self.locate("/flow/name", &name.span);
                self.locate("/flow", &event.span);
                "/flow".into()
            }
            Block::Steps => format!("{parent}/steps"),
            Block::Then | Block::Else => {
                context.branch_depth += 1;
                format!(
                    "{parent}/{}",
                    if matches!(block, Block::Then) {
                        "then"
                    } else {
                        "else"
                    }
                )
            }
            Block::Step { name, operation } => {
                let path = self.push(
                    &self.node_list(event),
                    json!({"name": name.text, "op": operation.text, "args": {}, "undo": null}),
                );
                self.locate(&path, &event.span);
                self.locate(&format!("{path}/name"), &name.span);
                self.locate(&format!("{path}/op"), &operation.span);
                self.actions += 1;
                if self.actions > 8 {
                    return Err(at(
                        "SCOPE_TOO_LARGE",
                        &operation.span,
                        Some(&format!("{path}/op")),
                        "Action budget exceeded.",
                        "Use at most eight action nodes.",
                    ));
                }
                path
            }
            Block::Transaction { name, keyword } => {
                let path = self.push(
                    &self.node_list(event),
                    json!({"name": name.text, "op": "transaction", "steps": []}),
                );
                self.locate(&path, &event.span);
                self.locate(&format!("{path}/name"), &name.span);
                self.locate(&format!("{path}/op"), &keyword.span);
                self.transactions += 1;
                if context.transaction {
                    return Err(at(
                        "INVALID_TRANSACTION",
                        &keyword.span,
                        Some(&format!("{path}/op")),
                        "Nested transactions are not allowed.",
                        "Use a nonnested transaction.",
                    ));
                }
                if self.transactions > 2 {
                    return Err(at(
                        "SCOPE_TOO_LARGE",
                        &keyword.span,
                        Some(&format!("{path}/op")),
                        "Too many transaction scopes.",
                        "Use at most two transactions.",
                    ));
                }
                context.transaction = true;
                path
            }
            Block::Branch {
                name,
                keyword,
                predicate,
            } => {
                let path = self.push(
                    &self.node_list(event),
                    json!({"name": name.text, "op": "if", "then": [], "else": [], "exports": {}}),
                );
                self.locate(&path, &event.span);
                self.locate(&format!("{path}/name"), &name.span);
                self.locate(&format!("{path}/op"), &keyword.span);
                self.branches += 1;
                if self.branches > 2 || context.branch_depth >= 2 {
                    return Err(at(
                        "SCOPE_TOO_LARGE",
                        &keyword.span,
                        Some(&format!("{path}/op")),
                        "Branch budget exceeded.",
                        "Use at most two branches and depth two.",
                    ));
                }
                let condition = self.predicate(&format!("{path}/condition"), predicate)?;
                self.put(&format!("{path}/condition"), condition);
                path
            }
            _ => unreachable!("non-flow begin handled by base"),
        };
        self.paths.insert(index, path);
        self.contexts.insert(index, context);
        Ok(())
    }
}

pub(super) fn lower(report: &SyntaxReport) -> Result<ParsedCnl, CompilerError> {
    if report.api_version != 2 {
        return Err(at(
            "ASSET_UNAVAILABLE",
            &report.eof,
            None,
            "Unsupported NLP syntax report interface.",
            "Use NLP syntax API version 2.",
        ));
    }
    if let Some(issue) = &report.issue {
        return Err(super::diagnostics::render(issue));
    }
    let mut lowerer = Lowerer {
        report,
        prompt: json!({}),
        map: BTreeMap::new(),
        version: 0,
        paths: BTreeMap::new(),
        contexts: BTreeMap::new(),
        actions: 0,
        branches: 0,
        transactions: 0,
    };
    for (index, event) in report.events.iter().enumerate() {
        if !lowerer.base_event(index, event)? {
            lowerer.flow_event(index, event)?;
        }
    }
    crate::validate_prompt(&lowerer.prompt)
        .map_err(|err| super::semantics::localize(&lowerer.prompt, &err, &lowerer.map))?;
    Ok(ParsedCnl {
        prompt: lowerer.prompt,
        source_map: lowerer.map,
    })
}
