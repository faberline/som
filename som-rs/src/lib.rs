use regex::Regex;
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fs;
use std::path::Path;

pub const PROMPT_VERSION: &str = "som.prompt.v1";
pub const PROMPT_VERSION_2: &str = "som.prompt.v2";
pub const PROMPT_VERSION_3: &str = "som.prompt.v3";
pub const PROMPT_VERSION_4: &str = "som.prompt.v4";
pub const DSL_VERSION: &str = "som.dsl.v1";
pub const DSL_VERSION_2: &str = "som.dsl.v2";
pub const LOGIC_STEPS: [&str; 5] = [
    "Create a row. Return 409 for a unique value conflict.",
    "Get a row by id. Return 404 if it does not exist.",
    "List rows in id order.",
    "Update supplied fields. Return 404 if the row does not exist.",
    "Delete a row. Return 404 if it does not exist.",
];
pub const TEST_CASE: &str = "Check CRUD, missing id, and unique conflict when supported.";
pub const LOGIC_STEPS_V2: [&str; 5] = [
    LOGIC_STEPS[0],
    LOGIC_STEPS[1],
    "List rows with an optional exact string filter, limit, and offset in id order.",
    LOGIC_STEPS[3],
    LOGIC_STEPS[4],
];
pub const TEST_CASE_V2: &str =
    "Check CRUD, exact filtering, page bounds, missing id, and unique conflict when supported.";
pub const LOGIC_STEPS_V3: [&str; 5] = [
    LOGIC_STEPS[0],
    LOGIC_STEPS[1],
    "List rows with an optional exact boolean filter, limit, and offset in id order.",
    LOGIC_STEPS[3],
    LOGIC_STEPS[4],
];
pub const TEST_CASE_V3: &str = "Check standalone CRUD, boolean filtering, page bounds, defaults, timestamps, missing id, and migration.";
pub const LOGIC_STEPS_V4: [&str; 5] = [
    LOGIC_STEPS[0],
    LOGIC_STEPS[1],
    "List rows with optional exact boolean and exclusive date-before filters, limit, and offset in id order.",
    LOGIC_STEPS[3],
    LOGIC_STEPS[4],
];
pub const TEST_CASE_V4: &str = "Check standalone CRUD, boolean and date-before filtering, page bounds, defaults, timestamps, missing id, and migration.";

pub mod model;
pub mod related;
pub mod business;
pub mod composed;
pub mod quality;
pub mod flow;
pub mod flow_codec;
pub mod flow_constraints;
pub mod flow_m3_model;
pub mod flow_compact;
pub mod flow_model;

#[derive(Debug, Clone)]
pub struct SomError {
    pub code: &'static str,
    pub detail: String,
}

impl SomError {
    fn new(code: &'static str, detail: impl Into<String>) -> Self {
        Self {
            code,
            detail: detail.into(),
        }
    }
}

impl std::fmt::Display for SomError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.detail)
    }
}
impl std::error::Error for SomError {}

fn object<'a>(
    value: &'a Value,
    keys: &[&str],
    where_: &str,
) -> Result<&'a Map<String, Value>, SomError> {
    let map = value
        .as_object()
        .ok_or_else(|| SomError::new("INVALID_SCHEMA", format!("{where_}: expected object")))?;
    let actual: HashSet<&str> = map.keys().map(String::as_str).collect();
    let wanted: HashSet<&str> = keys.iter().copied().collect();
    if actual != wanted {
        return Err(SomError::new(
            "INVALID_SCHEMA",
            format!("{where_}: expected {keys:?}"),
        ));
    }
    Ok(map)
}

fn ident(value: &Value, where_: &str) -> Result<String, SomError> {
    let text = value
        .as_str()
        .ok_or_else(|| SomError::new("INVALID_VALUE", format!("{where_}: expected identifier")))?;
    let re = Regex::new(r"^[A-Za-z_][A-Za-z_0-9]*$").unwrap();
    if !re.is_match(text)
        || matches!(
            text,
            "False"
                | "None"
                | "True"
                | "and"
                | "as"
                | "assert"
                | "async"
                | "await"
                | "break"
                | "class"
                | "continue"
                | "def"
                | "del"
                | "elif"
                | "else"
                | "except"
                | "finally"
                | "for"
                | "from"
                | "global"
                | "if"
                | "import"
                | "in"
                | "is"
                | "lambda"
                | "nonlocal"
                | "not"
                | "or"
                | "pass"
                | "raise"
                | "return"
                | "try"
                | "while"
                | "with"
                | "yield"
        )
    {
        return Err(SomError::new(
            "INVALID_VALUE",
            format!("{where_}: expected identifier"),
        ));
    }
    Ok(text.to_owned())
}

pub fn validate_prompt(prompt: &Value) -> Result<(), SomError> {
    if prompt["version"] == flow::VERSION { return flow::validate(prompt); }
    if prompt["version"] == composed::VERSION {
        return composed::validate(prompt);
    }
    if prompt["version"] == related::VERSION {
        return related::validate(prompt);
    }
    let (query, service, date_service) = match prompt.get("version").and_then(Value::as_str) {
        Some(PROMPT_VERSION) => (false, false, false),
        Some(PROMPT_VERSION_2) => (true, false, false),
        Some(PROMPT_VERSION_3) => (true, true, false),
        Some(PROMPT_VERSION_4) => (true, true, true),
        _ => {
            return Err(SomError::new(
                "UNSUPPORTED_VERSION",
                "unsupported prompt.version",
            ));
        }
    };
    let p = object(
        prompt,
        if query {
            &[
                "version",
                "package",
                "resource",
                "logic",
                "tests",
                "migration",
                "query",
            ]
        } else {
            &[
                "version",
                "package",
                "resource",
                "logic",
                "tests",
                "migration",
            ]
        },
        "prompt",
    )?;
    ident(&p["package"], "prompt.package")?;
    let r = object(
        &p["resource"],
        &["name", "table", "path", "fields"],
        "prompt.resource",
    )?;
    ident(&r["name"], "prompt.resource.name")?;
    ident(&r["table"], "prompt.resource.table")?;
    let path = r["path"].as_str().unwrap_or("");
    if !Regex::new(r"^/[a-z][a-z0-9_/-]*$").unwrap().is_match(path) {
        return Err(SomError::new(
            "INVALID_VALUE",
            "prompt.resource.path: expected a route path",
        ));
    }
    if date_service {
        fields_v4(&r["fields"], "prompt.resource.fields")?;
    } else if service {
        fields_v3(&r["fields"], "prompt.resource.fields")?;
    } else {
        fields(&r["fields"], "prompt.resource.fields")?;
    }
    for key in ["logic", "tests"] {
        let items = p[key]
            .as_array()
            .filter(|items| !items.is_empty())
            .ok_or_else(|| {
                SomError::new(
                    "INVALID_VALUE",
                    format!("prompt.{key}: expected non-empty list"),
                )
            })?;
        if items
            .iter()
            .any(|item| item.as_str().is_none_or(|text| text.trim().is_empty()))
        {
            return Err(SomError::new(
                "INVALID_VALUE",
                format!("prompt.{key}: expected non-empty strings"),
            ));
        }
    }
    let logic = p["logic"].as_array().unwrap();
    let actual: HashSet<&str> = logic.iter().filter_map(Value::as_str).collect();
    let steps = if date_service {
        LOGIC_STEPS_V4
    } else if service {
        LOGIC_STEPS_V3
    } else if query {
        LOGIC_STEPS_V2
    } else {
        LOGIC_STEPS
    };
    let expected: HashSet<&str> = steps.into_iter().collect();
    if logic.len() != LOGIC_STEPS.len() || actual != expected {
        return Err(SomError::new(
            "UNSUPPORTED_LOGIC",
            "prompt.logic: expected each supported step once",
        ));
    }
    let tests = p["tests"].as_array().unwrap();
    let expected_test = if date_service {
        TEST_CASE_V4
    } else if service {
        TEST_CASE_V3
    } else if query {
        TEST_CASE_V2
    } else {
        TEST_CASE
    };
    if tests.len() != 1 || tests[0].as_str() != Some(expected_test) {
        return Err(SomError::new(
            "UNSUPPORTED_TEST",
            "prompt.tests: unsupported test contract",
        ));
    }
    if query {
        let keys: &[&str] = if date_service {
            &["filter_field", "max_limit", "date_field"]
        } else {
            &["filter_field", "max_limit"]
        };
        let q = object(&p["query"], keys, "prompt.query")?;
        let filter_field = ident(&q["filter_field"], "prompt.query.filter_field")?;
        if date_service && ["before", "limit", "offset", "db"].contains(&filter_field.as_str()) {
            return Err(SomError::new(
                "INVALID_VALUE",
                "prompt.query.filter_field: reserved query name",
            ));
        }
        let valid = r["fields"].as_array().unwrap().iter().any(|field| {
            field["name"] == filter_field
                && field["type"] == if service { "bool" } else { "str" }
                && field["unique"] == false
                && (!service || field.get("default") == Some(&Value::Bool(false)))
        });
        if !valid {
            return Err(SomError::new(
                "INVALID_VALUE",
                "prompt.query.filter_field: expected a supported non-unique filter field",
            ));
        }
        if !matches!(q["max_limit"].as_u64(), Some(20..=100)) {
            return Err(SomError::new(
                "INVALID_VALUE",
                "prompt.query.max_limit: expected 20..100",
            ));
        }
        if date_service {
            let date_field = ident(&q["date_field"], "prompt.query.date_field")?;
            let date_fields: Vec<_> = r["fields"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|field| field["type"] == "date")
                .collect();
            if date_fields.len() != 1
                || date_fields[0]["name"] != date_field
                || date_fields[0]["required"] != false
                || date_fields[0]["unique"] != false
                || date_fields[0]["default"] != Value::Null
            {
                return Err(SomError::new(
                    "INVALID_VALUE",
                    "prompt.query.date_field: expected the sole optional non-unique date field with null default",
                ));
            }
        }
    }
    let m = object(
        &p["migration"],
        &["revision", "down_revision"],
        "prompt.migration",
    )?;
    ident(&m["revision"], "prompt.migration.revision")?;
    if !m["down_revision"].is_null() {
        ident(&m["down_revision"], "prompt.migration.down_revision")?;
    }
    if service && !m["down_revision"].is_null() {
        return Err(SomError::new(
            "INVALID_VALUE",
            "prompt.migration.down_revision: expected null",
        ));
    }
    Ok(())
}

pub fn resolve_prompt_refs(prompt: &Value, dsl: &Value) -> Result<Value, SomError> {
    if prompt["version"] == flow::VERSION { return flow::resolve(prompt, dsl); }
    validate_prompt(prompt)?;
    let related_values = if prompt["version"] == composed::VERSION {
        Some(composed::values(prompt)?)
    } else {
        (prompt["version"] == related::VERSION).then(|| related::values(prompt))
    };
    let query_prompt = [PROMPT_VERSION_2, PROMPT_VERSION_3, PROMPT_VERSION_4]
        .iter()
        .any(|version| prompt["version"] == *version);
    let mut result = dsl.clone();
    let files = result
        .get_mut("files")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| SomError::new("INVALID_SCHEMA", "dsl.files: expected a list"))?;
    for file in files {
        let file = file
            .as_object_mut()
            .ok_or_else(|| SomError::new("INVALID_SCHEMA", "dsl.files: expected file objects"))?;
        if let Some(path) = file.get("path").and_then(Value::as_str).map(str::to_owned) {
            let path = path
                .replace("{package}", prompt["package"].as_str().unwrap())
                .replace(
                    "{migration.revision}",
                    prompt["migration"]["revision"].as_str().unwrap(),
                );
            if path.contains('{') || path.contains('}') {
                return Err(SomError::new("INVALID_REF", "unknown path reference"));
            }
            file.insert("path".to_owned(), Value::String(path));
        }
        if let Some(blocks) = file.get_mut("blocks").and_then(Value::as_array_mut) {
            for block in blocks {
                if let Some(params) = block.get_mut("params").and_then(Value::as_object_mut) {
                    for value in params.values_mut() {
                        let ref_name = value
                            .as_object()
                            .filter(|map| map.len() == 1 && map.contains_key("$ref"))
                            .map(|map| map["$ref"].as_str().unwrap_or(""));
                        if let Some(ref_name) = ref_name {
                            if let Some(values) = &related_values {
                                *value = values.get(ref_name).ok_or_else(|| SomError::new("INVALID_REF", format!("unknown prompt parameter reference {ref_name}")))?.clone();
                                continue;
                            }
                            let replacement = match ref_name {
                                "package" => &prompt["package"],
                                "resource.name" => &prompt["resource"]["name"],
                                "resource.table" => &prompt["resource"]["table"],
                                "resource.path" => &prompt["resource"]["path"],
                                "resource.fields" => &prompt["resource"]["fields"],
                                "migration.revision" => &prompt["migration"]["revision"],
                                "migration.down_revision" => &prompt["migration"]["down_revision"],
                                "query.filter_field" if query_prompt => {
                                    &prompt["query"]["filter_field"]
                                }
                                "query.max_limit" if query_prompt => &prompt["query"]["max_limit"],
                                "query.date_field" if prompt["version"] == PROMPT_VERSION_4 => {
                                    &prompt["query"]["date_field"]
                                }
                                _ => {
                                    return Err(SomError::new(
                                        "INVALID_REF",
                                        format!("unknown prompt parameter reference {ref_name}"),
                                    ));
                                }
                            };
                            *value = replacement.clone();
                        }
                    }
                }
            }
        }
    }
    Ok(result)
}

#[derive(Debug, Clone, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Block {
    pub id: String,
    pub version: u32,
    pub imports: Vec<String>,
    pub params: BTreeMap<String, String>,
    pub template: String,
}

pub fn load_blocks(path: &Path) -> Result<BTreeMap<String, Block>, SomError> {
    let text =
        fs::read_to_string(path).map_err(|e| SomError::new("INVALID_BLOCK", e.to_string()))?;
    let list: Vec<Block> =
        serde_json::from_str(&text).map_err(|e| SomError::new("INVALID_BLOCK", e.to_string()))?;
    let mut out = BTreeMap::new();
    let tag = Regex::new(r"\{\{([A-Za-z_][A-Za-z_0-9]*)\}\}").unwrap();
    let section = Regex::new(r"(?s)\{\{#fields\}\}.*?\{\{/fields\}\}").unwrap();
    let block_id = Regex::new(r"^[a-z][a-z0-9_.]*$").unwrap();
    for block in list {
        let version_allowed = [1, 2, 3].contains(&block.version)
            || [4, 5].contains(&block.version) && block.id.starts_with("compact.");
        if !version_allowed || !block_id.is_match(&block.id) || out.contains_key(&block.id) {
            return Err(SomError::new(
                "INVALID_BLOCK",
                "unsupported version, id, or duplicate block",
            ));
        }
        let removed = section.replace_all(&block.template, "");
        let flow_section = Regex::new(r"(?s)\{\{#flow_inputs\}\}.*?\{\{/flow_inputs\}\}").unwrap();
        let removed = flow_section.replace_all(&removed, "");
        let mut used: HashSet<String> = tag
            .captures_iter(&removed)
            .map(|c| c[1].to_owned())
            .collect();
        for stmt in &block.imports {
            if !(stmt.starts_with("import ") || stmt.starts_with("from ")) {
                return Err(SomError::new("INVALID_BLOCK", "invalid import"));
            }
            used.extend(tag.captures_iter(stmt).map(|c| c[1].to_owned()));
        }
        if section.is_match(&block.template) {
            used.insert("fields".to_owned());
        }
        if flow_section.is_match(&block.template) {
            used.extend(["flow_inputs".into(), "parent_name".into(), "child_name".into()]);
        }
        if used != block.params.keys().cloned().collect()
            || block.params.values().any(|v| {
                ![
                    "identifier",
                    "string",
                    "nullable_string",
                    "type",
                    "integer",
                    "boolean",
                    "fields",
                    "fields_v3",
                    "fields_v4",
                    "fields_v7",
                    "flow_inputs",
                    "flow_inputs_v7",
                    "flow_program",
                    "flow_engine",
                    "routes_v7",
                ]
                .contains(&v.as_str())
            })
        {
            return Err(SomError::new(
                "INVALID_BLOCK",
                format!("{}: template and parameter schema disagree", block.id),
            ));
        }
        if block.params.values().any(|kind| kind == "routes_v7")
            && !block.id.starts_with("compact.")
        {
            return Err(SomError::new("INVALID_BLOCK", "typed route fixtures require compact blocks"));
        }
        out.insert(block.id.clone(), block);
    }
    if out.is_empty() {
        return Err(SomError::new("INVALID_BLOCK", "no blocks"));
    }
    Ok(out)
}

fn literal(value: &Value, kind: &str, where_: &str) -> Result<String, SomError> {
    match kind {
        "routes_v7" => flow_compact::compile_routes(value),
        "flow_program" => flow_compact::compile_program(value),
        "flow_engine" => flow_compact::compile_engine(value),
        "identifier" => ident(value, where_),
        "string" => value
            .as_str()
            .map(|s| serde_json::to_string(s).unwrap())
            .ok_or_else(|| SomError::new("INVALID_VALUE", format!("{where_}: expected string"))),
        "nullable_string" => {
            if value.is_null() {
                Ok("None".into())
            } else {
                literal(value, "string", where_)
            }
        }
        "type" => value
            .as_str()
            .filter(|v| ["str", "int", "bool", "float"].contains(v))
            .map(str::to_owned)
            .ok_or_else(|| SomError::new("INVALID_VALUE", format!("{where_}: unsupported type"))),
        "integer" => value
            .as_i64()
            .map(|v| v.to_string())
            .ok_or_else(|| SomError::new("INVALID_VALUE", format!("{where_}: expected integer"))),
        "boolean" => value
            .as_bool()
            .map(|v| if v { "True" } else { "False" }.to_owned())
            .ok_or_else(|| SomError::new("INVALID_VALUE", format!("{where_}: expected boolean"))),
        _ => Err(SomError::new(
            "INVALID_BLOCK",
            format!("{where_}: unknown parameter type"),
        )),
    }
}

fn fields(value: &Value, where_: &str) -> Result<Vec<BTreeMap<String, String>>, SomError> {
    let list = value
        .as_array()
        .filter(|items| !items.is_empty())
        .ok_or_else(|| {
            SomError::new(
                "INVALID_VALUE",
                format!("{where_}: expected non-empty fields"),
            )
        })?;
    let mut names = HashSet::new();
    let mut out = Vec::new();
    for (i, item) in list.iter().enumerate() {
        let f = object(
            item,
            &["name", "type", "required", "unique"],
            &format!("{where_}[{i}]"),
        )?;
        let name = ident(&f["name"], &format!("{where_}[{i}].name"))?;
        let ty = f["type"].as_str().unwrap_or("");
        let required = f["required"].as_bool();
        let unique = f["unique"].as_bool();
        if ["id", "metadata", "registry"].contains(&name.as_str())
            || !names.insert(name.clone())
            || !["str", "int", "bool", "float"].contains(&ty)
            || required.is_none()
            || unique.is_none()
        {
            return Err(SomError::new(
                "INVALID_VALUE",
                format!("{where_}[{i}]: invalid field"),
            ));
        }
        let required = required.unwrap();
        let unique = unique.unwrap();
        let (sql, mig, sample) = match ty {
            "str" => ("sa.String(255)", "sa.String(length=255)", "\"sample\""),
            "int" => ("sa.Integer", "sa.Integer()", "3"),
            "bool" => ("sa.Boolean", "sa.Boolean()", "True"),
            _ => ("sa.Float", "sa.Float()", "1.5"),
        };
        out.push(BTreeMap::from([
            ("name".into(), name.clone()),
            ("name_literal".into(), serde_json::to_string(&name).unwrap()),
            ("type".into(), ty.into()),
            ("sql_type".into(), sql.into()),
            ("migration_type".into(), mig.into()),
            (
                "nullable".into(),
                (!required)
                    .to_string()
                    .replace("true", "True")
                    .replace("false", "False"),
            ),
            (
                "unique".into(),
                unique
                    .to_string()
                    .replace("true", "True")
                    .replace("false", "False"),
            ),
            (
                "create_type".into(),
                if required {
                    ty.into()
                } else {
                    format!("{ty} | None")
                },
            ),
            (
                "create_default".into(),
                if required {
                    "".into()
                } else {
                    " = None".into()
                },
            ),
            ("patch_type".into(), format!("{ty} | None")),
            ("sample".into(), sample.into()),
        ]));
    }
    Ok(out)
}

fn fields_service(
    value: &Value,
    where_: &str,
    allow_date: bool,
) -> Result<Vec<BTreeMap<String, String>>, SomError> {
    let list = value
        .as_array()
        .filter(|items| !items.is_empty())
        .ok_or_else(|| {
            SomError::new(
                "INVALID_VALUE",
                format!("{where_}: expected non-empty fields"),
            )
        })?;
    let mut names = HashSet::new();
    let mut out = Vec::new();
    for (i, item) in list.iter().enumerate() {
        let required = item.get("required").and_then(Value::as_bool);
        let keys: &[&str] = if required == Some(false) {
            &["name", "type", "required", "unique", "default"]
        } else {
            &["name", "type", "required", "unique"]
        };
        let f = object(item, keys, &format!("{where_}[{i}]"))?;
        let name = ident(&f["name"], &format!("{where_}[{i}].name"))?;
        let ty = f["type"].as_str().unwrap_or("");
        let unique = f["unique"].as_bool();
        if ["id", "metadata", "registry", "created_at", "updated_at"].contains(&name.as_str())
            || !names.insert(name.clone())
            || !if allow_date {
                ["str", "int", "bool", "float", "date"].contains(&ty)
            } else {
                ["str", "int", "bool", "float"].contains(&ty)
            }
            || required.is_none()
            || unique.is_none()
        {
            return Err(SomError::new(
                "INVALID_VALUE",
                format!("{where_}[{i}]: invalid field"),
            ));
        }
        let required = required.unwrap();
        let default = f.get("default");
        if !required
            && !(default == Some(&Value::Null)
                || (ty == "bool" && default == Some(&Value::Bool(false))))
        {
            return Err(SomError::new(
                "INVALID_VALUE",
                format!("{where_}[{i}].default: expected null or false"),
            ));
        }
        let nullable = !required && default == Some(&Value::Null);
        let false_default = default == Some(&Value::Bool(false));
        let (sql, mig, sample) = match ty {
            "str" => ("sa.String(255)", "sa.String(length=255)", "\"sample\""),
            "int" => ("sa.Integer", "sa.Integer()", "3"),
            "bool" => ("sa.Boolean", "sa.Boolean()", "True"),
            "float" => ("sa.Float", "sa.Float()", "1.5"),
            _ => ("sa.Date", "sa.Date()", "\"2030-01-02\""),
        };
        let bool_literal = |v: bool| {
            if v {
                "True".to_owned()
            } else {
                "False".to_owned()
            }
        };
        out.push(BTreeMap::from([
            ("name".into(), name.clone()),
            ("name_literal".into(), serde_json::to_string(&name).unwrap()),
            ("type".into(), ty.into()),
            ("sql_type".into(), sql.into()),
            ("migration_type".into(), mig.into()),
            ("nullable".into(), bool_literal(nullable)),
            ("unique".into(), bool_literal(unique.unwrap())),
            (
                "sql_default".into(),
                if false_default {
                    ", server_default=sa.false()".into()
                } else {
                    "".into()
                },
            ),
            (
                "migration_default".into(),
                if false_default {
                    ", server_default=sa.text('false')".into()
                } else {
                    "".into()
                },
            ),
            (
                "create_type".into(),
                if nullable {
                    format!("{ty} | None")
                } else {
                    ty.into()
                },
            ),
            (
                "create_default".into(),
                if required {
                    "".into()
                } else if false_default {
                    " = False".into()
                } else {
                    " = None".into()
                },
            ),
            (
                "read_type".into(),
                if nullable {
                    format!("{ty} | None")
                } else {
                    ty.into()
                },
            ),
            ("patch_type".into(), format!("{ty} | None")),
            (
                "required_entry".into(),
                if required {
                    format!("            \"{name}\": {sample},\n")
                } else {
                    "".into()
                },
            ),
            ("sample".into(), sample.into()),
        ]));
    }
    Ok(out)
}

fn fields_v3(value: &Value, where_: &str) -> Result<Vec<BTreeMap<String, String>>, SomError> {
    fields_service(value, where_, false)
}

fn fields_v4(value: &Value, where_: &str) -> Result<Vec<BTreeMap<String, String>>, SomError> {
    fields_service(value, where_, true)
}

fn fields_v7(value: &Value, where_: &str) -> Result<Vec<BTreeMap<String, String>>, SomError> {
    let mut fields = fields_v4(value, where_)?;
    for field in &mut fields {
        let unique = if field["unique"] == "True" { ", unique=True" } else { "" };
        field.insert("column_options".into(), format!("{unique}{}", field["sql_default"]));
        field.insert("migration_options".into(), format!(", nullable={}{unique}{}", field["nullable"], field["migration_default"]));
        field.insert("non_null_entry".into(), if field["nullable"] == "False" { format!("{},", field["name_literal"]) } else { String::new() });
        let bson_kind = match field["type"].as_str() { "str" => "string", "int" => "int", "float" => "double", "bool" => "bool", _ => "date" };
        field.insert("unique_index_entry".into(), if unique.is_empty() { String::new() } else { format!("{}: \"{bson_kind}\",", field["name_literal"]) });
    }
    Ok(fields)
}

fn substitute(source: &str, values: &BTreeMap<String, String>) -> Result<String, SomError> {
    let re = Regex::new(r"\{\{([A-Za-z_][A-Za-z_0-9]*)\}\}").unwrap();
    let mut failure = None;
    let output = re.replace_all(source, |captures: &regex::Captures<'_>| {
        match values.get(&captures[1]) {
            Some(v) => v.clone(),
            None => {
                failure = Some(captures[1].to_owned());
                String::new()
            }
        }
    });
    if let Some(name) = failure {
        return Err(SomError::new(
            "INVALID_BLOCK",
            format!("unknown placeholder {name}"),
        ));
    }
    Ok(output.into_owned())
}

fn render(
    block: &Block,
    params: &Map<String, Value>,
    where_: &str,
    format_literals: bool,
) -> Result<(String, Vec<String>), SomError> {
    let wanted: HashSet<&str> = block.params.keys().map(String::as_str).collect();
    if params.keys().map(String::as_str).collect::<HashSet<_>>() != wanted {
        return Err(SomError::new(
            "INVALID_VALUE",
            format!("{where_}: parameter names disagree with block"),
        ));
    }
    let mut literals = BTreeMap::new();
    for (key, kind) in &block.params {
        if !["fields", "fields_v3", "fields_v4", "fields_v7", "flow_inputs", "flow_inputs_v7"].contains(&kind.as_str()) {
            literals.insert(key.clone(), literal(&params[key], kind, where_)?);
        }
    }
    let section = Regex::new(r"(?s)\{\{#fields\}\}(.*?)\{\{/fields\}\}").unwrap();
    let field_values = match block.params.get("fields").map(String::as_str) {
        Some("fields") => fields(&params["fields"], where_)?,
        Some("fields_v3") => fields_v3(&params["fields"], where_)?,
        Some("fields_v4") => fields_v4(&params["fields"], where_)?,
        Some("fields_v7") => fields_v7(&params["fields"], where_)?,
        _ => Vec::new(),
    };
    let mut failure = None;
    let body = section.replace_all(&block.template, |captures: &regex::Captures<'_>| {
        let mut result = String::new();
        for field in &field_values {
            match substitute(&captures[1], field) {
                Ok(s) => result.push_str(&s),
                Err(e) => failure = Some(e),
            }
        }
        result
    });
    if let Some(err) = failure {
        return Err(err);
    }
    let mut source_literals = literals.clone();
    if format_literals {
        for (key, kind) in &block.params {
            if kind == "string" && literals[key].chars().count() > 60 {
                let chars: Vec<char> = params[key].as_str().unwrap().chars().collect();
                let chunks: Vec<String> = chars.chunks(40).map(|chunk| {
                    serde_json::to_string(&chunk.iter().collect::<String>()).unwrap()
                }).collect();
                source_literals.insert(key.clone(), format!("(\n{}\n)", chunks.join("\n")));
            }
        }
    }
    let flow_section = Regex::new(r"(?s)\{\{#flow_inputs\}\}(.*?)\{\{/flow_inputs\}\}").unwrap();
    let mut flow_values = if flow_section.is_match(&body) {
        flow::input_fields(&params["flow_inputs"], &params["parent_name"], &params["child_name"])?
    } else { Vec::new() };
    if block.params.get("flow_inputs").is_some_and(|kind| kind == "flow_inputs_v7") {
        for (index, field) in flow_values.iter_mut().enumerate() {
            field.insert("variable".into(), format!("input_{index}"));
            field.insert("name_literal".into(), serde_json::to_string(&field["name"]).unwrap());
        }
    }
    let mut flow_error = None;
    let body = flow_section.replace_all(&body, |captures: &regex::Captures<'_>| {
        let mut result = String::new();
        for field in &flow_values { match substitute(&captures[1], field) {Ok(text)=>result.push_str(&text),Err(error)=>flow_error=Some(error)} }
        result
    });
    if let Some(error) = flow_error { return Err(error); }
    let source = substitute(&body, &source_literals)?;
    let imports = block
        .imports
        .iter()
        .map(|s| substitute(s, &literals))
        .collect::<Result<Vec<_>, _>>()?;
    Ok((source.trim_end_matches('\n').to_owned(), imports))
}

pub fn assemble(
    dsl: &Value,
    blocks: &BTreeMap<String, Block>,
) -> Result<BTreeMap<String, String>, SomError> {
    assemble_with_byte_limit(dsl, blocks, Some(65536))
}

pub(crate) fn assemble_with_byte_limit(
    dsl: &Value,
    blocks: &BTreeMap<String, Block>,
    byte_limit: Option<usize>,
) -> Result<BTreeMap<String, String>, SomError> {
    let d = object(dsl, &["version", "files"], "dsl")?;
    if d["version"] != DSL_VERSION && d["version"] != DSL_VERSION_2 {
        return Err(SomError::new(
            "UNSUPPORTED_VERSION",
            "dsl.version is not supported",
        ));
    }
    let v2 = d["version"] == DSL_VERSION_2;
    let limit = if v2 { 12 } else { 8 };
    let files = d["files"]
        .as_array()
        .filter(|f| (1..=limit).contains(&f.len()))
        .ok_or_else(|| {
            SomError::new(
                "SCOPE_TOO_LARGE",
                format!("dsl.files: expected 1..{limit} files"),
            )
        })?;
    let mut result = BTreeMap::new();
    let mut count = 0;
    let path_re = Regex::new(if v2 {
        r"^[A-Za-z_0-9/.-]+\.(py|toml|ini|md)$"
    } else {
        r"^[A-Za-z_0-9/.-]+\.py$"
    })
    .unwrap();
    for (fi, file) in files.iter().enumerate() {
        let f = object(file, &["path", "blocks"], &format!("dsl.files[{fi}]"))?;
        let path = f["path"].as_str().unwrap_or("");
        if !path_re.is_match(path)
            || path.starts_with('/')
            || path
                .split('/')
                .any(|p| p.is_empty() || p == "." || p == "..")
        {
            return Err(SomError::new(
                "INVALID_PATH",
                format!("unsafe relative Python path {path}"),
            ));
        }
        if result.contains_key(path) {
            return Err(SomError::new("INVALID_SCHEMA", "duplicate file"));
        }
        let entries = f["blocks"]
            .as_array()
            .filter(|entries| !entries.is_empty())
            .ok_or_else(|| SomError::new("INVALID_SCHEMA", "invalid or empty blocks"))?;
        let mut imports = Vec::new();
        let mut sources = Vec::new();
        let mut seen = HashSet::new();
        for (bi, entry) in entries.iter().enumerate() {
            count += 1;
            if count > 64 {
                return Err(SomError::new("SCOPE_TOO_LARGE", "more than 64 blocks"));
            }
            let e = object(
                entry,
                &["name", "id", "params", "refs"],
                &format!("{path}.blocks[{bi}]"),
            )?;
            let name = ident(&e["name"], &format!("{path}.blocks[{bi}].name"))?;
            let refs = e["refs"]
                .as_array()
                .ok_or_else(|| SomError::new("INVALID_REF", "refs must be a list"))?;
            if seen.contains(&name)
                || refs
                    .iter()
                    .any(|v| v.as_str().is_none_or(|r| !seen.contains(r)))
            {
                return Err(SomError::new(
                    "INVALID_REF",
                    format!("{path}.{name}: duplicate name or unknown reference"),
                ));
            }
            seen.insert(name.clone());
            let id = e["id"]
                .as_str()
                .ok_or_else(|| SomError::new("INVALID_VALUE", "block id must be a string"))?;
            let block = blocks.get(id).ok_or_else(|| {
                SomError::new(
                    "UNSUPPORTED_BLOCK",
                    format!("{path}.{name}: unknown block {id}"),
                )
            })?;
            let params = e["params"]
                .as_object()
                .ok_or_else(|| SomError::new("INVALID_VALUE", "params must be object"))?;
            let (source, block_imports) = render(block, params, &format!("{path}.{name}"),
                                                block.version >= 2 && path.ends_with(".py"))?;
            sources.push(source);
            imports.extend(block_imports);
        }
        let prefix = render_imports(&imports);
        let mut body = String::new();
        for source in sources {
            if !body.is_empty() {
                body.push_str(if source.starts_with("    ") {
                    "\n\n"
                } else {
                    "\n\n\n"
                });
            }
            body.push_str(&source);
        }
        let gap = if body.starts_with("revision = ") {
            "\n\n"
        } else {
            "\n\n\n"
        };
        let output = if prefix.is_empty() {
            body
        } else if body.is_empty() {
            prefix
        } else {
            format!("{prefix}{gap}{body}")
        };
        result.insert(
            path.to_owned(),
            format!("{}\n", output.trim_end_matches('\n')),
        );
    }
    if v2 && byte_limit.is_some_and(|limit| result.values().map(|s: &String| s.len()).sum::<usize>() > limit) {
        return Err(SomError::new(
            "SCOPE_TOO_LARGE",
            "dsl output exceeds 65536 bytes",
        ));
    }
    Ok(result)
}

fn render_imports(statements: &[String]) -> String {
    let mut plain = BTreeSet::new();
    let mut named: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for stmt in statements {
        if let Some(rest) = stmt.strip_prefix("from ") {
            if let Some((module, names)) = rest.split_once(" import ") {
                named
                    .entry(module.to_owned())
                    .or_default()
                    .extend(names.split(',').map(|s| s.trim().to_owned()));
            }
        } else {
            plain.insert(stmt.to_owned());
        }
    }
    let entries = plain
        .into_iter()
        .chain(named.into_iter().map(|(module, names)| {
            format!(
                "from {module} import {}",
                names.into_iter().collect::<Vec<_>>().join(", ")
            )
        }));
    let mut groups = [Vec::new(), Vec::new(), Vec::new()];
    for stmt in entries {
        let module = stmt.split_whitespace().nth(1).unwrap_or("");
        let group = if ["os", "unittest", "collections.abc"].contains(&module) {
            0
        } else if module.starts_with('.') {
            2
        } else {
            1
        };
        groups[group].push(stmt);
    }
    groups
        .into_iter()
        .filter(|g| !g.is_empty())
        .map(|g| g.join("\n"))
        .collect::<Vec<_>>()
        .join("\n\n")
}
