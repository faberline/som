//! Deterministic prompt-to-DSL compilation under compiler selection policy v1.
//! This module uses loaded approved assets. It does not load files or models.
use crate::{Block, SomError, composed, flow, related};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

const POLICY_TEXT: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/contracts/compiler-v1/selection-policy.json"
));
static POLICY: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(POLICY_TEXT).expect("compiled compiler selection policy v1")
});

pub struct PlannerAssets<'a> {
    pub legacy: &'a BTreeMap<String, Block>,
    pub quality_blocks: &'a BTreeMap<String, Block>,
    pub current: &'a BTreeMap<String, Block>,
    pub quality_catalog: &'a Value,
    pub compact_catalog: &'a Value,
}

fn error(code: &'static str, detail: impl Into<String>) -> SomError {
    SomError {
        code,
        detail: detail.into(),
    }
}

fn string<'a>(value: &'a Value, context: &str) -> Result<&'a str, SomError> {
    value
        .as_str()
        .ok_or_else(|| error("INVALID_BLOCK", format!("{context}: expected string")))
}

fn array<'a>(value: &'a Value, context: &str) -> Result<&'a Vec<Value>, SomError> {
    value
        .as_array()
        .ok_or_else(|| error("INVALID_BLOCK", format!("{context}: expected list")))
}

fn object<'a>(value: &'a Value, context: &str) -> Result<&'a Map<String, Value>, SomError> {
    value
        .as_object()
        .ok_or_else(|| error("INVALID_BLOCK", format!("{context}: expected object")))
}

fn refs(names: &[&str]) -> Value {
    json!(names)
}

fn bind_map(value: &Value) -> BTreeMap<String, String> {
    value
        .as_object()
        .expect("compiled binding map")
        .iter()
        .map(|(key, value)| {
            (
                key.clone(),
                value.as_str().expect("compiled binding").into(),
            )
        })
        .collect()
}

fn entry(
    blocks: &BTreeMap<String, Block>,
    name: &str,
    id: &str,
    bindings: &BTreeMap<String, String>,
    dependencies: Value,
) -> Result<Value, SomError> {
    let block = blocks
        .get(id)
        .ok_or_else(|| error("UNSUPPORTED_BLOCK", format!("missing approved block {id}")))?;
    let params: Result<Map<String, Value>, SomError> = block
        .params
        .keys()
        .map(|key| {
            let binding = bindings.get(key).ok_or_else(|| {
                error(
                    "INVALID_REF",
                    format!("{id}: no approved binding for {key}"),
                )
            })?;
            Ok((key.clone(), json!({"$ref":binding})))
        })
        .collect();
    Ok(json!({"name":name,"id":id,"params":params?,"refs":dependencies}))
}

fn file(path: &str, blocks: Vec<Value>) -> Value {
    json!({"path":path,"blocks":blocks})
}

fn file_blocks_mut(file: &mut Value) -> &mut Vec<Value> {
    file["blocks"]
        .as_array_mut()
        .expect("constructed file blocks")
}

fn capability_matches(prompt: &Value, metadata: &Value) -> Result<bool, SomError> {
    Ok(object(&metadata["capabilities"], "block capabilities")?
        .iter()
        .all(|(key, expected)| prompt["capabilities"].get(key) == Some(expected)))
}

fn provider_matches(prompt: &Value, metadata: &Value, symbol: &str) -> Result<bool, SomError> {
    if metadata["provides"] != symbol || !capability_matches(prompt, metadata)? {
        return Ok(false);
    }
    if !metadata["query"].is_null() {
        let query = array(&metadata["query"], "block query")?;
        if query.len() != 2 {
            return Err(error("INVALID_BLOCK", "block query requires role and kind"));
        }
        let role = string(&query[0], "query role")?;
        let kind = string(&query[1], "query kind")?;
        if !["parent", "child"].contains(&role) {
            return Err(error("INVALID_BLOCK", "unknown query role"));
        }
        if related::query_kind(&prompt["resources"][role]) != kind {
            return Ok(false);
        }
    }
    Ok(true)
}

fn provider<'a>(
    prompt: &Value,
    catalog: &'a Value,
    symbol: &str,
) -> Result<(&'a str, &'a Value), SomError> {
    let mut selected = None;
    for (id, metadata) in object(&catalog["blocks"], "catalog blocks")? {
        if provider_matches(prompt, metadata, symbol)? {
            if selected.is_some() {
                return Err(error(
                    "INVALID_COMPOSITION",
                    format!("ambiguous provider: {symbol}"),
                ));
            }
            selected = Some((id.as_str(), metadata));
        }
    }
    selected.ok_or_else(|| error("INVALID_COMPOSITION", format!("absent provider: {symbol}")))
}

fn check_catalog_node(
    prompt: &Value,
    node: &Value,
    catalog: &Value,
    blocks: &BTreeMap<String, Block>,
) -> Result<(), SomError> {
    let id = string(&node["id"], "block ID")?;
    let metadata = catalog["blocks"]
        .get(id)
        .ok_or_else(|| error("UNSUPPORTED_BLOCK", format!("unapproved block {id}")))?;
    let symbol = string(&metadata["provides"], "provided symbol")?;
    let (primary, _) = provider(prompt, catalog, symbol)?;
    if primary != id {
        return Err(error(
            "INVALID_COMPOSITION",
            format!("wrong primary for {symbol}"),
        ));
    }
    let block = blocks
        .get(id)
        .ok_or_else(|| error("UNSUPPORTED_BLOCK", format!("missing approved block {id}")))?;
    let declared =
        serde_json::to_value(&block.params).map_err(|e| error("INVALID_BLOCK", e.to_string()))?;
    if declared != metadata["params"] {
        return Err(error(
            "INVALID_BLOCK",
            format!("parameter types differ for {id}"),
        ));
    }
    if node["params"] != metadata["bindings"] {
        return Err(error(
            "INVALID_REF",
            format!("parameter bindings differ for {id}"),
        ));
    }
    Ok(())
}

fn legacy_plan(prompt: &Value, version: u8, assets: &PlannerAssets<'_>) -> Result<Value, SomError> {
    let policy = &POLICY["legacy_versions"][version.to_string()];
    let bindings = bind_map(&policy["binding_map"]);
    let mut files = Vec::new();
    for specification in policy["files"].as_array().expect("compiled legacy files") {
        let mut nodes = Vec::new();
        for specification in specification["blocks"]
            .as_array()
            .expect("compiled legacy nodes")
        {
            let id = specification["id"].as_str().expect("compiled block ID");
            let block = assets.legacy.get(id).ok_or_else(|| {
                error("UNSUPPORTED_BLOCK", format!("missing approved block {id}"))
            })?;
            if serde_json::to_value(&block.params)
                .map_err(|e| error("INVALID_BLOCK", e.to_string()))?
                != specification["declared_params"]
            {
                return Err(error(
                    "INVALID_BLOCK",
                    format!("legacy parameter types differ for {id}"),
                ));
            }
            nodes.push(entry(
                assets.legacy,
                specification["name"].as_str().unwrap(),
                id,
                &bindings,
                specification["refs"].clone(),
            )?);
        }
        let path = specification["path"].as_str().unwrap();
        if path == "{package}/api.py" {
            let mut ordered = vec![nodes.iter().find(|n| n["name"] == "head").unwrap().clone()];
            for step in prompt["logic"].as_array().unwrap() {
                let action = policy["canonical_sentences"].as_array().unwrap().iter()
                    .find(|s| s["step"] == *step).ok_or_else(|| error("UNSUPPORTED_LOGIC", "unmapped legacy operation"))?["action"].as_str().unwrap();
                ordered.push(nodes.iter().find(|n| n["name"] == action).unwrap().clone());
            }
            ordered.push(nodes.iter().find(|n| n["name"] == "tail").unwrap().clone());
            nodes = ordered;
        }
        files.push(file(path, nodes));
    }
    let result = json!({"version":policy["dsl_version"],"files":files});
    // Legacy has no separate plan checker. This checks every typed path/binding;
    // the caller retains the existing assembler's structural and byte gates.
    crate::resolve_prompt_refs(prompt, &result)?;
    Ok(result)
}

fn scoped_operations(prompt: &Value) -> Result<Vec<(String, String)>, SomError> {
    if let Some(operations) = prompt["operations"].as_array() {
        return Ok(operations
            .iter()
            .map(|op| {
                (
                    op["scope"].as_str().unwrap().into(),
                    op["action"].as_str().unwrap().into(),
                )
            })
            .collect());
    }
    let sentences = POLICY["legacy_versions"]["5"]["canonical_sentences"]
        .as_array()
        .unwrap();
    prompt["logic"]
        .as_array()
        .unwrap()
        .iter()
        .map(|step| {
            let action = sentences
                .iter()
                .find(|s| s["step"] == step["step"])
                .ok_or_else(|| error("UNSUPPORTED_LOGIC", "unmapped related operation"))?["action"]
                .as_str()
                .unwrap();
            Ok((step["scope"].as_str().unwrap().into(), action.into()))
        })
        .collect()
}

fn selected_spec_id<'a>(specification: &'a Value, prompt: &Value) -> &'a str {
    if let Some(id) = specification["id"].as_str() {
        return id;
    }
    if let Some(query) = specification["id_by_query"].as_object() {
        let role = specification["role"].as_str().unwrap();
        return query[related::query_kind(&prompt["resources"][role])]
            .as_str()
            .unwrap();
    }
    for key in ["database", "auth", "storage", "workflow"] {
        if let Some(choices) = specification.get(&format!("id_by_{key}")) {
            let choice = if key == "database" {
                crate::business::database(prompt)
            } else {
                prompt["capabilities"][key].as_str().unwrap()
            };
            return choices[choice].as_str().unwrap();
        }
    }
    unreachable!("compiled node has one selection rule")
}

fn related_entry(
    blocks: &BTreeMap<String, Block>,
    name: &str,
    id: &str,
    role: Option<&str>,
    dependencies: Value,
    path: Option<&str>,
) -> Result<Value, SomError> {
    let policy = &POLICY["legacy_versions"]["5"];
    let mut bindings = bind_map(&policy["common_binding_map"]);
    if let Some(role) = role {
        bindings.extend(
            bind_map(&policy["role_binding_map"])
                .into_iter()
                .map(|(key, value)| (key, value.replace("{role}", role))),
        );
    }
    if let Some(path) = path {
        bindings.insert("path".into(), path.into());
    }
    entry(blocks, name, id, &bindings, dependencies)
}

fn related_spec(
    specification: &Value,
    prompt: &Value,
    blocks: &BTreeMap<String, Block>,
) -> Result<Value, SomError> {
    related_entry(
        blocks,
        specification["name"].as_str().unwrap(),
        selected_spec_id(specification, prompt),
        specification["role"].as_str(),
        specification["refs"].clone(),
        None,
    )
}

fn related_plan(prompt: &Value, blocks: &BTreeMap<String, Block>) -> Result<Value, SomError> {
    let policy = &POLICY["legacy_versions"]["5"];
    let operations = scoped_operations(prompt)?;
    let mut files = Vec::new();
    for specification in policy["files"].as_array().unwrap() {
        let mut nodes = Vec::new();
        for node in specification["nodes"].as_array().unwrap() {
            if node.get("repeat").is_some() {
                for (scope, action) in &operations {
                    let kind = if action == "list" {
                        Some(related::query_kind(&prompt["resources"][scope]))
                    } else {
                        None
                    };
                    let row = policy["legacy_api_selection_table"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .find(|row| {
                            row["database"] == crate::business::database(prompt)
                                && row["scope"] == scope.as_str()
                                && row["action"] == action.as_str()
                                && row["query"].as_str() == kind
                        })
                        .ok_or_else(|| {
                            error("INVALID_COMPOSITION", "no scoped operation primary")
                        })?;
                    let name = if scope == "batch" {
                        "batch".into()
                    } else {
                        format!("{scope}_{action}")
                    };
                    let path = if scope == "batch" {
                        "batch.path".into()
                    } else {
                        format!(
                            "{scope}.{}",
                            if ["get", "patch", "delete"].contains(&action.as_str()) {
                                "item_path"
                            } else {
                                "path"
                            }
                        )
                    };
                    nodes.push(related_entry(
                        blocks,
                        &name,
                        row["id"].as_str().unwrap(),
                        (scope != "batch").then_some(scope.as_str()),
                        refs(&["head"]),
                        Some(&path),
                    )?);
                }
            } else {
                nodes.push(related_spec(node, prompt, blocks)?);
            }
        }
        files.push(file(specification["path"].as_str().unwrap(), nodes));
    }
    if prompt.get("capabilities").is_some() {
        let overlay = &policy["business_overlay"];
        let app_index = files
            .iter()
            .position(|f| f["path"] == "{package}/api.py")
            .unwrap();
        files[app_index]["path"] = overlay["api_path"].clone();
        {
            let nodes = file_blocks_mut(&mut files[app_index]);
            nodes[0] = related_entry(
                blocks,
                "head",
                overlay["head_id_by_database"][crate::business::database(prompt)]
                    .as_str()
                    .unwrap(),
                None,
                refs(&[]),
                None,
            )?;
            nodes.insert(
                nodes.len() - 1,
                related_spec(&overlay["insert_before_api_tail"], prompt, blocks)?,
            );
            nodes.push(related_spec(&overlay["append_to_api"], prompt, blocks)?);
        }
        files = files
            .into_iter()
            .enumerate()
            .filter_map(|(index, f)| {
                (index == app_index || f["path"] != "{package}/app.py").then_some(f)
            })
            .collect();
        let integrations = &overlay["integrations_append_file"];
        let mut nodes: Vec<Value> = integrations["prefix"]
            .as_array()
            .unwrap()
            .iter()
            .map(|n| related_spec(n, prompt, blocks))
            .collect::<Result<_, _>>()?;
        let storage_nodes = if prompt["capabilities"]["storage"] == "none" {
            &integrations["storage_none"]
        } else {
            &integrations["storage_present"]
        };
        nodes.extend(
            storage_nodes
                .as_array()
                .unwrap()
                .iter()
                .map(|n| related_spec(n, prompt, blocks))
                .collect::<Result<Vec<_>, _>>()?,
        );
        files.push(file(integrations["path"].as_str().unwrap(), nodes));
        for f in &mut files {
            let path = f["path"].as_str().unwrap().to_owned();
            if path == "pyproject.toml" {
                f["blocks"] = json!([related_entry(
                    blocks,
                    "project",
                    overlay["project_id"].as_str().unwrap(),
                    None,
                    refs(&[]),
                    None
                )?]);
            } else if path == "README.md" {
                f["blocks"] = json!(
                    overlay["readme_nodes"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|n| related_spec(n, prompt, blocks))
                        .collect::<Result<Vec<_>, _>>()?
                );
            } else if path.starts_with("tests/") {
                let nodes = file_blocks_mut(f);
                nodes[0] = related_spec(&overlay["replace_test_head"], prompt, blocks)?;
                let last = nodes.len() - 1;
                nodes[last] = related_spec(&overlay["replace_test_behavior"], prompt, blocks)?;
            }
        }
        if crate::business::database(prompt) == "mongodb" {
            let mongo = &overlay["mongodb"];
            files.retain(|f| !mongo["omit_paths"].as_array().unwrap().contains(&f["path"]));
            for f in &mut files {
                let key = match f["path"].as_str().unwrap() {
                    "{package}/models.py" => Some("models_nodes"),
                    "{package}/database.py" => Some("database_nodes"),
                    path if path.starts_with("alembic/versions/") => {
                        f["path"] = mongo["migration_path"].clone();
                        Some("migration_nodes")
                    }
                    _ => None,
                };
                if let Some(key) = key {
                    f["blocks"] = json!(
                        mongo[key]
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(|n| related_spec(n, prompt, blocks))
                            .collect::<Result<Vec<_>, _>>()?
                    );
                }
            }
        }
    }
    Ok(json!({"version":"som.dsl.v2","files":files}))
}

fn route_names(prompt: &Value) -> Vec<String> {
    prompt["operations"]
        .as_array()
        .unwrap()
        .iter()
        .map(|o| {
            if o["scope"] == "batch" {
                "batch".into()
            } else {
                format!(
                    "{}_{}",
                    o["scope"].as_str().unwrap(),
                    o["action"].as_str().unwrap()
                )
            }
        })
        .collect()
}

fn composed_plan(
    prompt: &Value,
    assets: &PlannerAssets<'_>,
    blocks: &BTreeMap<String, Block>,
) -> Result<Value, SomError> {
    let mut result = related_plan(prompt, blocks)?;
    let routes = route_names(prompt);
    for f in result["files"].as_array_mut().unwrap() {
        let path = f["path"].as_str().unwrap().to_owned();
        for node in file_blocks_mut(f) {
            if let Some(alias) = POLICY["composed"]["emission_recipe"]["mongo_model_id_aliases"]
                .get(node["id"].as_str().unwrap())
            {
                node["id"] = alias.clone();
            }
            if [
                "{package}/models.py",
                "alembic/versions/{migration.revision}_{package}.py",
            ]
            .contains(&path.as_str())
                && node["name"] == "child"
            {
                node["refs"].as_array_mut().unwrap().push(json!("parent"));
            }
            if path == "{package}/app.py" {
                if node["name"] == "tail" {
                    node["refs"] = json!(
                        [
                            vec!["head".to_owned()],
                            routes.clone(),
                            vec!["workflow".to_owned()]
                        ]
                        .concat()
                    );
                }
                if node["name"] == "app" {
                    node["refs"] = refs(&["tail"]);
                }
            }
            if path.starts_with("tests/") {
                if node["name"] == "parent" || node["name"] == "child" {
                    node["refs"] = refs(&["head"]);
                }
                if node["name"] == "test" {
                    node["id"] = json!("test.composed.behavior");
                    node["params"] = json!({"routes":{"$ref":"contract.routes"}});
                }
            }
            check_catalog_node(prompt, node, assets.quality_catalog, blocks)?;
        }
    }
    composed::validate_plan_with(prompt, &result, assets.quality_catalog)?;
    Ok(result)
}

fn flow_primary(
    prompt: &Value,
    operation: &str,
    inverse: Option<&str>,
    family: &str,
    catalog: &Value,
) -> Result<String, SomError> {
    let choice = match family {
        "neutral" => None,
        _ => prompt["capabilities"][family].as_str(),
    };
    let matches: Vec<&Value> = POLICY["compact_flow"]["flow_primary_providers"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|row| {
            row["operation"] == operation
                && row["inverse"].as_str() == inverse
                && row["provider_family"] == family
                && row["provider_choice"].as_str() == choice
        })
        .collect();
    if matches.len() != 1 {
        return Err(error(
            "INVALID_COMPOSITION",
            "ambiguous or absent flow primary",
        ));
    }
    let id = matches[0]["id"].as_str().unwrap();
    let metadata = catalog["flow_blocks"]
        .get(id)
        .ok_or_else(|| error("UNSUPPORTED_BLOCK", format!("missing flow provider {id}")))?;
    if metadata["operation"] != operation
        || metadata["inverse"].as_str() != inverse
        || !capability_matches(prompt, metadata)?
    {
        return Err(error(
            "INVALID_COMPOSITION",
            "flow primary metadata differs",
        ));
    }
    // Standard/checked gets are distinct legal alternatives. The frozen compile
    // choice excludes checked providers, but rejects other duplicate primaries.
    let mut compatible = 0;
    for (candidate, meta) in object(&catalog["flow_blocks"], "flow providers")? {
        if candidate.ends_with(".checked") {
            continue;
        }
        let caps = object(&meta["capabilities"], "flow capability")?;
        let candidate_family = if caps.contains_key("storage") {
            "storage"
        } else if caps.contains_key("database") {
            "database"
        } else {
            "neutral"
        };
        if candidate_family == family
            && meta["operation"] == operation
            && meta["inverse"].as_str() == inverse
            && capability_matches(prompt, meta)?
        {
            compatible += 1;
        }
    }
    if compatible != 1 {
        return Err(error(
            "INVALID_COMPOSITION",
            "ambiguous or absent flow provider",
        ));
    }
    Ok(id.into())
}

fn flow_steps(prompt: &Value, steps: &Value, catalog: &Value) -> Result<Value, SomError> {
    let mut nodes = Vec::new();
    for node in steps.as_array().unwrap() {
        let mut item = node.as_object().unwrap().clone();
        let operation = item.remove("op").unwrap().as_str().unwrap().to_owned();
        let family = if operation == "if" {
            "neutral"
        } else if operation.starts_with("object.") {
            "storage"
        } else {
            "database"
        };
        item.insert(
            "id".into(),
            json!(flow_primary(prompt, &operation, None, family, catalog)?),
        );
        if operation == "transaction" {
            item.insert("steps".into(), flow_steps(prompt, &node["steps"], catalog)?);
        } else if operation == "if" {
            item.insert("then".into(), flow_steps(prompt, &node["then"], catalog)?);
            item.insert("else".into(), flow_steps(prompt, &node["else"], catalog)?);
        } else if let Some(inverse) = node["undo"].as_str() {
            item.insert(
                "undo".into(),
                json!(flow_primary(
                    prompt,
                    "undo",
                    Some(inverse),
                    family,
                    catalog
                )?),
            );
        }
        nodes.push(Value::Object(item));
    }
    Ok(json!(nodes))
}

fn metadata_node(
    prompt: &Value,
    id: &str,
    slot: &str,
    catalog: &Value,
    blocks: &BTreeMap<String, Block>,
) -> Result<Value, SomError> {
    let metadata = catalog["blocks"]
        .get(id)
        .ok_or_else(|| error("UNSUPPORTED_BLOCK", format!("missing metadata {id}")))?;
    let symbol = string(&metadata["provides"], "provided symbol")?;
    let name = symbol
        .split_once(':')
        .ok_or_else(|| error("INVALID_BLOCK", "malformed provider symbol"))?
        .1;
    let mut dependencies = Vec::new();
    for required in array(&metadata["requires"], "required symbols")? {
        let required = string(required, "required symbol")?;
        let (required_slot, required_name) = required
            .split_once(':')
            .ok_or_else(|| error("INVALID_BLOCK", "malformed requirement"))?;
        if required_slot == slot {
            dependencies.push(json!(required_name));
        }
    }
    let result = json!({"id":id,"name":name,"params":metadata["bindings"],"refs":dependencies});
    check_catalog_node(prompt, &result, catalog, blocks)?;
    Ok(result)
}

// Policy v1.1 preserves the public constructor's first-encounter order. The
// validator's BTreeSet is only a membership check and cannot set this order.
fn ordered_flow_helpers(tree: &Value) -> Vec<String> {
    fn push(value: &Value, seen: &mut BTreeSet<String>, result: &mut Vec<String>) {
        if let Some(id) = value.as_str() {
            if seen.insert(id.into()) {
                result.push(id.into());
            }
        }
    }
    fn walk(nodes: &Value, seen: &mut BTreeSet<String>, result: &mut Vec<String>) {
        for node in nodes.as_array().expect("constructed flow nodes") {
            push(&node["id"], seen, result);
            if let Some(steps) = node.get("steps") {
                walk(steps, seen, result);
            } else if let Some(then) = node.get("then") {
                walk(then, seen, result);
                walk(&node["else"], seen, result);
            } else {
                push(&node["undo"], seen, result);
            }
        }
    }
    let mut result = Vec::new();
    walk(&tree["steps"], &mut BTreeSet::new(), &mut result);
    result
}

fn compact_flow_plan(prompt: &Value, assets: &PlannerAssets<'_>) -> Result<Value, SomError> {
    let base = flow::base(prompt);
    let mut result = composed_plan(&base, assets, assets.current)?;
    result["version"] = json!(flow::DSL_VERSION);
    result["flow"] = json!({"name":prompt["flow"]["name"],"steps":flow_steps(prompt,&prompt["flow"]["steps"],assets.compact_catalog)?,"result":prompt["flow"]["result"]});
    let policy = &POLICY["compact_flow"]["emission_recipe"];
    let mut files = Vec::new();
    for mut f in result["files"].as_array().unwrap().iter().cloned() {
        if policy["omitted_files"]
            .as_array()
            .unwrap()
            .contains(&f["path"])
        {
            continue;
        }
        let slot = object(&assets.quality_catalog["slots"], "quality slots")?
            .iter()
            .find(|(_, path)| **path == f["path"])
            .map(|(slot, _)| slot.clone())
            .ok_or_else(|| error("INVALID_PATH", "unknown inherited file slot"))?;
        for node in file_blocks_mut(&mut f) {
            let final_id = policy["base_id_to_final_compact_id"]
                .get(node["id"].as_str().unwrap())
                .ok_or_else(|| error("UNSUPPORTED_BLOCK", "missing compact ID mapping"))?;
            node["id"] = final_id.clone();
            let metadata = assets.compact_catalog["blocks"]
                .get(final_id.as_str().unwrap())
                .ok_or_else(|| error("UNSUPPORTED_BLOCK", "missing compact block metadata"))?;
            node["params"] = metadata["bindings"].clone();
            check_catalog_node(&base, node, assets.compact_catalog, assets.current)?;
        }
        for addition in policy["ordered_additions"].as_array().unwrap() {
            if addition["slot"] != slot
                || addition["when_database"]
                    .as_str()
                    .is_some_and(|d| d != crate::business::database(prompt))
            {
                continue;
            }
            let id = format!("compact.{}", selected_spec_id(addition, prompt));
            let node = metadata_node(&base, &id, &slot, assets.compact_catalog, assets.current)?;
            let nodes = file_blocks_mut(&mut f);
            if slot == "migration" {
                let name = node["name"].clone();
                nodes.insert(nodes.len() - 1, node);
                nodes.last_mut().unwrap()["refs"]
                    .as_array_mut()
                    .unwrap()
                    .push(name);
            } else {
                nodes.push(node);
            }
        }
        if slot == "app" {
            let nodes = file_blocks_mut(&mut f);
            let names: Vec<Value> = nodes
                .iter()
                .filter(|node| node["name"] != "tail" && node["name"] != "app")
                .map(|node| node["name"].clone())
                .collect();
            nodes
                .iter_mut()
                .find(|node| node["name"] == "tail")
                .unwrap()["refs"] = json!(names);
        }
        f["path"] = assets.compact_catalog["slots"]
            .get(&slot)
            .ok_or_else(|| error("INVALID_PATH", "missing compact slot"))?
            .clone();
        files.push(f);
    }
    let mut helper_ids = vec![
        format!(
            "compact.workflow.flow.{}",
            prompt["capabilities"]["workflow"].as_str().unwrap()
        ),
        format!(
            "compact.api.flow.engine.{}",
            crate::business::database(prompt)
        ),
    ];
    helper_ids.extend(ordered_flow_helpers(&result["flow"]));
    let helpers = helper_ids
        .iter()
        .map(|id| {
            metadata_node(
                &base,
                id,
                "flow_helpers",
                assets.compact_catalog,
                assets.current,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    files.push(file(
        string(
            &assets.compact_catalog["slots"]["flow_helpers"],
            "flow helper path",
        )?,
        helpers,
    ));
    for f in &mut files {
        for node in file_blocks_mut(f) {
            let symbols: Vec<Value> = node["refs"]
                .as_array()
                .unwrap()
                .iter()
                .map(|name| json!({"symbol":name}))
                .collect();
            node["refs"] = json!(symbols);
        }
    }
    result["files"] = json!(files);
    flow::validate_plan_with(prompt, &result, assets.compact_catalog)?;
    Ok(result)
}

/// Produce an unresolved existing DSL using the frozen compiler-v1 policy.
/// The caller loads approved assets and retains the existing assembly gates.
pub fn plan(prompt: &Value, assets: &PlannerAssets<'_>) -> Result<Value, SomError> {
    crate::validate_prompt(prompt)?;
    primary_plan(prompt, assets).map(|dsl| crate::python_names::select_variants(prompt, dsl))
}

fn primary_plan(prompt: &Value, assets: &PlannerAssets<'_>) -> Result<Value, SomError> {
    match prompt["version"].as_str().unwrap() {
        "som.prompt.v1" => legacy_plan(prompt, 1, assets),
        "som.prompt.v2" => legacy_plan(prompt, 2, assets),
        "som.prompt.v3" => legacy_plan(prompt, 3, assets),
        "som.prompt.v4" => legacy_plan(prompt, 4, assets),
        "som.prompt.v5" => {
            let result = related_plan(prompt, assets.legacy)?;
            crate::resolve_prompt_refs(prompt, &result)?;
            Ok(result)
        }
        "som.prompt.v6" => composed_plan(prompt, assets, assets.quality_blocks),
        "som.prompt.v7" => compact_flow_plan(prompt, assets),
        _ => Err(error(
            "UNSUPPORTED_VERSION",
            "unsupported compiler prompt version",
        )),
    }
}
