use crate::Error;
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};

pub const DEFER_OPENAPI: &str = include_str!("../contract/defer.openapi.json");

const METHODS: [&str; 8] = [
    "get", "put", "post", "delete", "patch", "head", "options", "trace",
];
const SCHEMA_PREFIX: &str = "#/components/schemas/";

pub fn defer_document() -> Value {
    serde_json::from_str(DEFER_OPENAPI).expect("pinned Defer OpenAPI document")
}

/// The `operations[*].name` strings of a `som.prompt-json.service.v2` prompt, in order.
pub fn operation_ids(prompt: &Value) -> Vec<String> {
    prompt["operations"]
        .as_array()
        .map(|operations| {
            operations
                .iter()
                .filter_map(|op| op["name"].as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

/// Cut `document` down to the operations whose `operationId` is in `ids`,
/// plus the schemas those operations reference (transitively).
pub fn select_operations(document: &Value, ids: &[&str]) -> Result<Value, Error> {
    if ids.is_empty() {
        return Err(Error::new(
            "INVALID_SCHEMA",
            "At least one operationId is required.",
            "/operations",
        ));
    }
    let paths = document["paths"].as_object().ok_or_else(|| {
        Error::new(
            "INVALID_SCHEMA",
            "Defer document has no paths.",
            "/operations",
        )
    })?;

    let known: BTreeSet<&str> = paths
        .values()
        .flat_map(|item| {
            METHODS
                .iter()
                .filter_map(move |method| item.get(*method))
                .filter_map(|op| op["operationId"].as_str())
        })
        .collect();
    if let Some(unknown) = ids.iter().find(|id| !known.contains(**id)) {
        return Err(Error::new(
            "UNSUPPORTED_OPERATION",
            format!("Unknown operationId {unknown}."),
            "/operations",
        ));
    }

    let mut kept_paths = Map::new();
    for (path, item) in paths {
        let Some(item) = item.as_object() else {
            continue;
        };
        let mut kept = Map::new();
        let mut has_operation = false;
        for (key, value) in item {
            if METHODS.contains(&key.as_str()) {
                if value["operationId"]
                    .as_str()
                    .is_some_and(|id| ids.contains(&id))
                {
                    kept.insert(key.clone(), value.clone());
                    has_operation = true;
                }
            } else {
                kept.insert(key.clone(), value.clone());
            }
        }
        if has_operation {
            kept_paths.insert(path.clone(), Value::Object(kept));
        }
    }

    let source_schemas = document["components"]["schemas"].as_object();
    let mut schemas: BTreeMap<String, Value> = BTreeMap::new();
    let mut pending = collect_schema_refs(&Value::Object(kept_paths.clone()));
    while let Some(name) = pending.pop() {
        if schemas.contains_key(&name) {
            continue;
        }
        let Some(schema) = source_schemas.and_then(|s| s.get(&name)) else {
            continue;
        };
        pending.extend(collect_schema_refs(schema));
        schemas.insert(name, schema.clone());
    }

    Ok(json!({
        "openapi": document["openapi"],
        "info": document["info"],
        "paths": kept_paths,
        "components": {"schemas": schemas},
    }))
}

/// Every `#/components/schemas/<name>` reference whose name is not defined in the document.
pub fn unresolved_refs(document: &Value) -> BTreeSet<String> {
    let defined = document["components"]["schemas"].as_object();
    collect_schema_refs(document)
        .into_iter()
        .filter(|name| !defined.is_some_and(|s| s.contains_key(name)))
        .map(|name| format!("{SCHEMA_PREFIX}{name}"))
        .collect()
}

fn collect_schema_refs(value: &Value) -> Vec<String> {
    let mut out = Vec::new();
    collect_into(value, &mut out);
    out
}

fn collect_into(value: &Value, out: &mut Vec<String>) {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                match (key.as_str(), child.as_str()) {
                    ("$ref", Some(target)) => {
                        if let Some(name) = target.strip_prefix(SCHEMA_PREFIX) {
                            out.push(name.to_owned());
                        }
                    }
                    _ => collect_into(child, out),
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_into(item, out);
            }
        }
        _ => {}
    }
}
