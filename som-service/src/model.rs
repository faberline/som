use crate::Error;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::BTreeSet;

pub const PROMPT_VERSION: &str = "som.prompt.service.v1";
pub const CNL_FORMAT: &str = "som.cnl.service.v1";
pub const JSON_FORMAT: &str = "som.prompt-json.service.v1";
pub const DSL_VERSION: &str = "som.dsl.service.v1";
pub const TARGET: &str = "rust-service-client.v1";
pub const DEFER_REVISION: &str = "5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0";
pub const CONTRACT: &str = "defer.http@5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0";

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Prompt {
    pub version: String,
    pub package: String,
    pub target: String,
    pub service: Service,
    pub operation: Operation,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Service {
    pub name: String,
    pub contract: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Operation {
    pub alias: String,
    pub name: String,
    pub inputs: Vec<Input>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub name: String,
    #[serde(rename = "type")]
    pub ty: String,
    pub parameter: String,
}

pub fn exact<'a>(
    value: &'a Value,
    keys: &[&str],
    pointer: &str,
) -> Result<&'a Map<String, Value>, Error> {
    let object = value
        .as_object()
        .ok_or_else(|| Error::new("INVALID_SCHEMA", "Expected an object.", pointer))?;
    if object.len() != keys.len() || keys.iter().any(|key| !object.contains_key(*key)) {
        return Err(Error::new(
            "INVALID_SCHEMA",
            format!("Expected exactly these fields: {}.", keys.join(", ")),
            pointer,
        ));
    }
    Ok(object)
}
fn string<'a>(object: &'a Map<String, Value>, key: &str, parent: &str) -> Result<&'a str, Error> {
    object[key].as_str().ok_or_else(|| {
        Error::new(
            "INVALID_SCHEMA",
            "Expected a string.",
            &format!("{parent}/{key}"),
        )
    })
}
fn identifier(name: &str, pointer: &str) -> Result<(), Error> {
    const RESERVED: &[&str] = &[
        "as", "break", "const", "continue", "crate", "else", "enum", "extern", "false", "fn",
        "for", "if", "impl", "in", "let", "loop", "match", "mod", "move", "mut", "pub", "ref",
        "return", "self", "static", "struct", "super", "trait", "true", "type", "unsafe", "use",
        "where", "while", "async", "await", "dyn", "abstract", "become", "box", "do", "final",
        "macro", "override", "priv", "typeof", "unsized", "virtual", "yield", "try", "gen",
        "union", "new", "clone", "default", "drop", "fmt",
    ];
    if name.is_empty()
        || name.len() > 64
        || !name.as_bytes()[0].is_ascii_lowercase()
        || !name
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
        || RESERVED.contains(&name)
    {
        return Err(Error::new(
            "INVALID_VALUE",
            "Expected an unreserved lower-case Rust identifier of 1 to 64 bytes.",
            pointer,
        ));
    }
    Ok(())
}
pub fn validate(value: &Value) -> Result<Prompt, Error> {
    let p = exact(
        value,
        &["version", "package", "target", "service", "operation"],
        "",
    )?;
    if string(p, "version", "")? != PROMPT_VERSION {
        return Err(Error::new(
            "UNSUPPORTED_VERSION",
            "Unsupported service prompt version.",
            "/version",
        ));
    }
    let package = string(p, "package", "")?;
    identifier(package, "/package")?;
    if string(p, "target", "")? != TARGET {
        return Err(Error::new(
            "UNSUPPORTED_TARGET",
            "Only rust-service-client.v1 is supported.",
            "/target",
        ));
    }
    let s = exact(&p["service"], &["name", "contract"], "/service")?;
    if string(s, "name", "/service")? != "defer" {
        return Err(Error::new(
            "UNSUPPORTED_CONTRACT",
            "Only the pinned Defer service is supported.",
            "/service/name",
        ));
    }
    if string(s, "contract", "/service")? != CONTRACT {
        return Err(Error::new(
            "UNSUPPORTED_CONTRACT",
            "The service contract is not supported.",
            "/service/contract",
        ));
    }
    let o = exact(&p["operation"], &["alias", "name", "inputs"], "/operation")?;
    let alias = string(o, "alias", "/operation")?;
    identifier(alias, "/operation/alias")?;
    if string(o, "name", "/operation")? != "task_status" {
        return Err(Error::new(
            "UNSUPPORTED_OPERATION",
            "Only task_status is supported.",
            "/operation/name",
        ));
    }
    let inputs = o["inputs"]
        .as_array()
        .filter(|items| items.len() == 2)
        .ok_or_else(|| {
            Error::new(
                "INVALID_SCHEMA",
                "Exactly two named inputs are required.",
                "/operation/inputs",
            )
        })?;
    let mut names = BTreeSet::new();
    let mut parameters = BTreeSet::new();
    for (i, input) in inputs.iter().enumerate() {
        let pointer = format!("/operation/inputs/{i}");
        let fields = exact(input, &["name", "type", "parameter"], &pointer)?;
        let name = string(fields, "name", &pointer)?;
        identifier(name, &format!("{pointer}/name"))?;
        if !names.insert(name) {
            return Err(Error::new(
                "INVALID_VALUE",
                "Input names must be unique.",
                &format!("{pointer}/name"),
            ));
        }
        if string(fields, "type", &pointer)? != "string" {
            return Err(Error::new(
                "INVALID_VALUE",
                "Task-status inputs must have type string.",
                &format!("{pointer}/type"),
            ));
        }
        let parameter = string(fields, "parameter", &pointer)?;
        if !matches!(parameter, "queue" | "task_id") || !parameters.insert(parameter) {
            return Err(Error::new(
                "INVALID_VALUE",
                "Bind queue and task_id exactly once.",
                &format!("{pointer}/parameter"),
            ));
        }
    }
    serde_json::from_value(value.clone())
        .map_err(|_| Error::new("INVALID_SCHEMA", "Invalid service prompt.", ""))
}
pub fn plan(prompt: &Prompt) -> Value {
    let inputs: Vec<&Input> = ["queue", "task_id"]
        .iter()
        .map(|name| {
            prompt
                .operation
                .inputs
                .iter()
                .find(|i| i.parameter == *name)
                .expect("validated parameter binding")
        })
        .collect();
    json!({"version":DSL_VERSION,"package":prompt.package,"target":TARGET,
        "provider":{"id":"defer.http.task_status.v1","revision":DEFER_REVISION,"contract":CONTRACT},
        "operation":{"alias":prompt.operation.alias,"method":"GET","path":"/v1/queues/{queue}/tasks/{task_id}",
            "inputs":inputs,"response":"TaskStatusResponse","errors":"native-status-body.v1","authorization":"queue-read","request_body":false}})
}
