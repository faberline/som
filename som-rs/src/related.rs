//! Bounded two-resource prompts. Source text is never a prompt parameter.
use crate::{SomError, fields_v4, ident, object};
use regex::Regex;
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashSet};

pub const VERSION: &str = "som.prompt.v5";
pub const CRUD: [&str; 5] = [
    "Create a row. Return 409 for a unique value conflict.",
    "Get a row by id. Return 404 if it does not exist.",
    "List scoped rows with the configured filters and pagination in id order.",
    "Update supplied fields. Return 404 if the row does not exist.",
    "Delete a row. Reject a parent with children with 409.",
];
pub const BATCH: &str = "Create a parent and 1 to 20 children in one transaction. Roll back all writes on failure.";
pub const TEST: &str = "Check related CRUD, filters, defaults, timestamps, isolation, atomic batch, delete restriction, and migration.";
const OPS: [&str; 5] = ["create", "get", "list", "patch", "delete"];
const RESERVED: &[&str] = &["parent_id", "child_id", "before", "limit", "offset", "db", "date", "datetime", "model_config", "model_fields", "model_dump", "revision_id", "settings", "save", "insert", "delete", "get", "find", "find_one"];

fn invalid(detail: &str) -> SomError { SomError::new("INVALID_VALUE", detail) }

pub fn validate(p: &Value) -> Result<(), SomError> {
    let mut keys = vec!["version", "package", "resources", "relation", "logic", "tests", "migration"];
    if p.get("capabilities").is_some() { keys.push("capabilities"); }
    object(p, &keys, "prompt")?;
    let lower = Regex::new(r"^[a-z][a-z0-9_]{0,39}$").unwrap();
    let upper = Regex::new(r"^[A-Z][A-Za-z0-9]{0,39}$").unwrap();
    let route = Regex::new(r"^(/[a-z][a-z0-9_-]*){1,3}$").unwrap();
    if !lower.is_match(&ident(&p["package"], "package")?) { return Err(invalid("invalid package")); }
    object(&p["resources"], &["parent", "child"], "resources")?;
    object(&p["relation"], &["foreign_key", "on_delete"], "relation")?;
    let fk = ident(&p["relation"]["foreign_key"], "foreign_key")?;
    if !lower.is_match(&fk) || RESERVED.contains(&fk.as_str()) || ["id", "created_at", "updated_at", "metadata", "registry"].contains(&fk.as_str()) { return Err(invalid("invalid foreign key")); }
    if p["relation"]["on_delete"] != "restrict" { return Err(invalid("only restrict deletion is supported")); }
    let mut names = HashSet::new();
    for role in ["parent", "child"] {
        let r = &p["resources"][role];
        object(r, &["name", "table", "path", "fields", "query"], role)?;
        let name = ident(&r["name"], "resource.name")?;
        let table = ident(&r["table"], "resource.table")?;
        if !upper.is_match(&name) || ["Base", "Field", "ConfigDict", "BaseModel", "Mapped", "BatchCreate", "BatchRead"].contains(&name.as_str()) { return Err(invalid("invalid resource class name")); }
        if !lower.is_match(&table) || table == "alembic_version" { return Err(invalid("invalid table")); }
        let path = r["path"].as_str().unwrap_or("");
        if !route.is_match(path) || ["healthz", "docs", "redoc", "openapi.json", "auth", "objects"].contains(&path.split('/').nth(1).unwrap_or("")) { return Err(invalid("invalid resource path")); }
        let fields = r["fields"].as_array().filter(|f| (1..=8).contains(&f.len())).ok_or_else(|| SomError::new("SCOPE_TOO_LARGE", "expected 1..8 fields per resource"))?;
        fields_v4(&r["fields"], "fields")?;
        for field in fields {
            let field_name = field["name"].as_str().unwrap();
            if !lower.is_match(field_name) || RESERVED.contains(&field_name) || field_name == fk || field_name.starts_with("model_") { return Err(invalid("reserved field name")); }
            if field["type"] == "date" && (field["required"] == true || field["unique"] == true || !field["default"].is_null()) { return Err(invalid("dates must be optional with null default")); }
        }
        let q = &r["query"];
        object(q, &["filter_field", "date_field", "max_limit"], "query")?;
        if q["max_limit"].as_u64().is_none_or(|n| !(20..=100).contains(&n)) { return Err(invalid("max_limit must be 20..100")); }
        for (key, ty) in [("filter_field", "bool"), ("date_field", "date")] {
            if !q[key].is_null() {
                let query_name = ident(&q[key], key)?;
                let good = fields.iter().any(|f| f["name"] == query_name && f["type"] == ty && f["unique"] == false && f["required"] == false && (ty != "bool" || f["default"] == false));
                if !good { return Err(invalid("invalid query field")); }
            }
        }
        let dates: Vec<_> = fields.iter().filter(|f| f["type"] == "date").collect();
        if dates.len() > 1 || (!dates.is_empty() && (q["date_field"] != dates[0]["name"] || q["filter_field"].is_null())) || (dates.is_empty() && !q["date_field"].is_null()) { return Err(invalid("date filtering requires a boolean filter and one date")); }
        for suffix in ["", "Create", "Read", "Patch"] {
            if !names.insert(format!("{name}{suffix}")) { return Err(invalid("duplicate generated resource name")); }
        }
    }
    let resources = &p["resources"];
    if resources["parent"]["table"] == resources["child"]["table"] || resources["parent"]["path"] == resources["child"]["path"] { return Err(invalid("resource tables and paths must be distinct")); }
    let steps = p["logic"].as_array().filter(|s| s.len() == 11).ok_or_else(|| SomError::new("UNSUPPORTED_LOGIC", "expected eleven scoped steps"))?;
    let mut pairs = HashSet::new();
    for step in steps {
        object(step, &["scope", "step"], "logic entry")?;
        let scope = step["scope"].as_str().ok_or_else(|| invalid("invalid logic entry"))?;
        let text = step["step"].as_str().ok_or_else(|| invalid("invalid logic entry"))?;
        pairs.insert((scope, text));
    }
    let mut expected: HashSet<_> = ["parent", "child"].into_iter().flat_map(|r| CRUD.into_iter().map(move |s| (r, s))).collect();
    expected.insert(("batch", BATCH));
    if pairs != expected { return Err(SomError::new("UNSUPPORTED_LOGIC", "each supported scoped step must occur once")); }
    if p["tests"] != json!([TEST]) { return Err(SomError::new("UNSUPPORTED_TEST", "unsupported related test contract")); }
    object(&p["migration"], &["revision", "down_revision"], "migration")?;
    ident(&p["migration"]["revision"], "migration.revision")?;
    if !p["migration"]["down_revision"].is_null() { return Err(invalid("down_revision must be null")); }
    if p.get("capabilities").is_some() { crate::business::validate(p)?; }
    Ok(())
}

pub fn query_kind(resource: &Value) -> &'static str {
    if !resource["query"]["date_field"].is_null() { "date" } else if !resource["query"]["filter_field"].is_null() { "boolean" } else { "plain" }
}

pub fn template_key(p: &Value) -> String {
    format!("{VERSION}.{}.{}{}", query_kind(&p["resources"]["parent"]), query_kind(&p["resources"]["child"]), crate::business::key(p))
}

pub fn values(p: &Value) -> BTreeMap<String, Value> {
    let mut values = BTreeMap::new();
    for (key, value) in [("package", &p["package"]), ("migration.revision", &p["migration"]["revision"]), ("migration.down_revision", &p["migration"]["down_revision"]), ("relation.foreign_key", &p["relation"]["foreign_key"])] { values.insert(key.to_owned(), value.clone()); }
    for role in ["parent", "child"] {
        let r = &p["resources"][role];
        for key in ["name", "table", "path", "fields"] { values.insert(format!("{role}.{key}"), r[key].clone()); }
        for key in ["filter_field", "date_field", "max_limit"] { values.insert(format!("{role}.{key}"), r["query"][key].clone()); }
    }
    let base = p["resources"]["parent"]["path"].as_str().unwrap();
    let child = p["resources"]["child"]["path"].as_str().unwrap();
    for (key, value) in [("child.suffix", child.to_owned()), ("parent.item_path", format!("{base}/{{parent_id}}")), ("child.path", format!("{base}/{{parent_id}}{child}")), ("child.item_path", format!("{base}/{{parent_id}}{child}/{{child_id}}")), ("batch.path", format!("{base}/with-children")), ("relation.target", format!("{}.id", p["resources"]["parent"]["table"].as_str().unwrap()))] { values.insert(key.to_owned(), Value::String(value)); }
    if let Some(caps) = p["capabilities"].as_object() {
        for (key, value) in caps { values.insert(format!("capabilities.{key}"), value.clone()); }
    }
    values
}

pub fn operations() -> Vec<String> {
    let mut ids = Vec::new();
    for prefix in ["api.related", "api.mongo"] {
    for role in ["parent", "child"] {
        for op in OPS {
            if op == "list" {
                for kind in ["plain", "boolean", "date"] { ids.push(format!("{prefix}.{role}.list.{kind}")); }
            } else { ids.push(format!("{prefix}.{role}.{op}")); }
        }
    }
    ids.push(format!("{prefix}.batch"));
    }
    ids
}

pub fn selections(p: &Value) -> Vec<(String, String, String)> {
    p["logic"].as_array().unwrap().iter().map(|step| {
        let database = crate::business::database(p);
        let prefix = if database == "mongodb" { "api.mongo" } else { "api.related" };
        let role = step["scope"].as_str().unwrap();
        let text = step["step"].as_str().unwrap();
        let kind = if role == "batch" { "atomic" } else { query_kind(&p["resources"][role]) };
        let (slot, id) = if role == "batch" { ("batch".into(), format!("{prefix}.batch")) } else {
            let op = OPS[CRUD.iter().position(|s| *s == text).unwrap()];
            (format!("{role}_{op}"), format!("{prefix}.{role}.{op}{}", if op == "list" { format!(".{kind}") } else { String::new() }))
        };
        (slot, id, format!("<database>{database}</database><scope>{role}</scope><query>{kind}</query><step>{text}</step><block>"))
    }).collect()
}
