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
pub const PROMPT_VERSION_V2: &str = "som.prompt.service.v2";
pub const JSON_FORMAT_V2: &str = "som.prompt-json.service.v2";
pub const DSL_VERSION_V2: &str = "som.dsl.service.v2";
pub const MAX_OPERATIONS: usize = 16;

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
pub struct PromptV2 {
    pub version: String,
    pub package: String,
    pub target: String,
    pub service: Service,
    pub operations: Vec<Operation>,
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
/// Checks the header shared by v1 and v2: the exact top-level keys (with
/// `operations_key` as the last one), the version, package, target and service.
fn header<'a>(
    value: &'a Value,
    operations_key: &str,
    version: &str,
) -> Result<&'a Map<String, Value>, Error> {
    let p = exact(
        value,
        &["version", "package", "target", "service", operations_key],
        "",
    )?;
    if string(p, "version", "")? != version {
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
    Ok(p)
}
pub fn validate(value: &Value) -> Result<Prompt, Error> {
    let p = header(value, "operation", PROMPT_VERSION)?;
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

/// One path or body segment of a Defer operation in the catalog.
enum Seg {
    Lit(&'static str),
    Param(&'static str),
}
/// A Defer operation that a v2 prompt may bind. Methods and paths match `contract/wire.json`.
struct Entry {
    name: &'static str,
    method: &'static str,
    path: &'static [Seg],
    body: Option<&'static str>,
    response: &'static str,
    authorization: &'static str,
}
impl Entry {
    fn params(&self) -> impl Iterator<Item = &'static str> {
        let path: &'static [Seg] = self.path;
        path.iter().filter_map(|segment| match segment {
            Seg::Param(name) => Some(*name),
            Seg::Lit(_) => None,
        })
    }
    fn path_string(&self) -> String {
        let parts: Vec<String> = self
            .path
            .iter()
            .map(|segment| match segment {
                Seg::Lit(literal) => (*literal).to_string(),
                Seg::Param(name) => format!("{{{name}}}"),
            })
            .collect();
        format!("/{}", parts.join("/"))
    }
}
static CATALOG: [Entry; 4] = [
    Entry {
        name: "task_status",
        method: "GET",
        path: &[
            Seg::Lit("v1"),
            Seg::Lit("queues"),
            Seg::Param("queue"),
            Seg::Lit("tasks"),
            Seg::Param("task_id"),
        ],
        body: None,
        response: "TaskStatusResponse",
        authorization: "queue-read",
    },
    Entry {
        name: "task_create",
        method: "POST",
        path: &[
            Seg::Lit("v1"),
            Seg::Lit("queues"),
            Seg::Param("queue"),
            Seg::Lit("tasks"),
        ],
        body: Some("CreateTask"),
        response: "none",
        authorization: "queue-write",
    },
    Entry {
        name: "queue_put",
        method: "PUT",
        path: &[Seg::Lit("v1"), Seg::Lit("queues"), Seg::Param("queue")],
        body: Some("QueuePolicy"),
        response: "QueueSnapshot",
        authorization: "queue-write",
    },
    Entry {
        name: "task_cancel",
        method: "DELETE",
        path: &[
            Seg::Lit("v1"),
            Seg::Lit("queues"),
            Seg::Param("queue"),
            Seg::Lit("tasks"),
            Seg::Param("task_id"),
        ],
        body: None,
        response: "none",
        authorization: "queue-write",
    },
];
fn entry(name: &str) -> Option<&'static Entry> {
    CATALOG.iter().find(|entry| entry.name == name)
}

pub fn validate_v2(value: &Value) -> Result<PromptV2, Error> {
    let p = header(value, "operations", PROMPT_VERSION_V2)?;
    let operations = p["operations"]
        .as_array()
        .filter(|items| (1..=MAX_OPERATIONS).contains(&items.len()))
        .ok_or_else(|| {
            Error::new(
                "INVALID_SCHEMA",
                "Between one and sixteen operations are required.",
                "/operations",
            )
        })?;
    let mut aliases = BTreeSet::new();
    let mut used = BTreeSet::new();
    for (i, operation) in operations.iter().enumerate() {
        let pointer = format!("/operations/{i}");
        let o = exact(operation, &["alias", "name", "inputs"], &pointer)?;
        let alias = string(o, "alias", &pointer)?;
        identifier(alias, &format!("{pointer}/alias"))?;
        if !aliases.insert(alias) {
            return Err(Error::new(
                "INVALID_VALUE",
                "Operation aliases must be unique.",
                &format!("{pointer}/alias"),
            ));
        }
        let name = string(o, "name", &pointer)?;
        let entry = entry(name).ok_or_else(|| {
            Error::new(
                "UNSUPPORTED_OPERATION",
                "Unknown operation.",
                &format!("{pointer}/name"),
            )
        })?;
        if !used.insert(name) {
            return Err(Error::new(
                "INVALID_VALUE",
                "An operation can be used once.",
                &format!("{pointer}/name"),
            ));
        }
        let expected_inputs = entry.params().count() + usize::from(entry.body.is_some());
        let inputs = o["inputs"]
            .as_array()
            .filter(|items| items.len() == expected_inputs)
            .ok_or_else(|| {
                Error::new(
                    "INVALID_SCHEMA",
                    "Wrong number of inputs for this operation.",
                    &format!("{pointer}/inputs"),
                )
            })?;
        let mut names = BTreeSet::new();
        let mut bound = BTreeSet::new();
        for (j, input) in inputs.iter().enumerate() {
            let input_pointer = format!("{pointer}/inputs/{j}");
            let fields = exact(input, &["name", "type", "parameter"], &input_pointer)?;
            let input_name = string(fields, "name", &input_pointer)?;
            identifier(input_name, &format!("{input_pointer}/name"))?;
            if !names.insert(input_name) {
                return Err(Error::new(
                    "INVALID_VALUE",
                    "Input names must be unique.",
                    &format!("{input_pointer}/name"),
                ));
            }
            let parameter = string(fields, "parameter", &input_pointer)?;
            let is_body = parameter == "body" && entry.body.is_some();
            let is_param = entry.params().any(|name| name == parameter);
            if !(is_body || is_param) || !bound.insert(parameter) {
                return Err(Error::new(
                    "INVALID_VALUE",
                    "Bind each parameter exactly once.",
                    &format!("{input_pointer}/parameter"),
                ));
            }
            let expected_type = if is_body {
                entry.body.expect("body checked above")
            } else {
                "string"
            };
            if string(fields, "type", &input_pointer)? != expected_type {
                return Err(Error::new(
                    "INVALID_VALUE",
                    "The input type does not match its parameter.",
                    &format!("{input_pointer}/type"),
                ));
            }
        }
    }
    serde_json::from_value(value.clone())
        .map_err(|_| Error::new("INVALID_SCHEMA", "Invalid service prompt.", ""))
}

pub fn plan_v2(prompt: &PromptV2) -> Value {
    let operations: Vec<Value> = prompt
        .operations
        .iter()
        .map(|operation| {
            let entry = entry(&operation.name).expect("validated operation");
            let inputs: Vec<&Input> = entry
                .params()
                .map(|parameter| {
                    operation
                        .inputs
                        .iter()
                        .find(|input| input.parameter == parameter)
                        .expect("validated parameter binding")
                })
                .collect();
            let request_body = match entry.body {
                None => json!(false),
                Some(body_type) => {
                    let input = operation
                        .inputs
                        .iter()
                        .find(|input| input.parameter == "body")
                        .expect("validated body binding");
                    json!({"name":input.name,"type":body_type,"content_type":"application/json"})
                }
            };
            let path_segments: Vec<Value> = entry
                .path
                .iter()
                .map(|segment| match segment {
                    Seg::Lit(literal) => json!({"literal":literal}),
                    Seg::Param(name) => json!({"param":name}),
                })
                .collect();
            json!({"alias":operation.alias,"name":operation.name,"method":entry.method,
                "path":entry.path_string(),"path_segments":path_segments,"inputs":inputs,
                "request_body":request_body,"response":entry.response,
                "errors":"native-status-body.v1","authorization":entry.authorization})
        })
        .collect();
    json!({"version":DSL_VERSION_V2,"package":prompt.package,"target":TARGET,
        "provider":{"id":"defer.http.v2","revision":DEFER_REVISION,"contract":CONTRACT},
        "operations":operations})
}
