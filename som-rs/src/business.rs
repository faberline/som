//! Enumerated integration settings. Secrets are never prompt parameters.
use crate::{SomError, object};
use serde_json::Value;

fn invalid(detail: &str) -> SomError { SomError::new("INVALID_VALUE", detail) }

pub fn validate(p: &Value) -> Result<(), SomError> {
    let caps = &p["capabilities"];
    object(caps, &["database", "auth", "storage", "workflow", "value_field", "group_field"], "capabilities")?;
    for (key, allowed) in [
        ("database", vec!["postgresql", "mongodb"]),
        ("auth", vec!["jwt", "firebase", "google_oauth"]),
        ("storage", vec!["none", "s3", "gcs"]),
        ("workflow", vec!["json_export", "csv", "gzip_export", "decimal_summary", "statistics"]),
    ] {
        if !caps[key].as_str().is_some_and(|v| allowed.contains(&v)) { return Err(invalid(&format!("unsupported capability: {key}"))); }
    }
    let fields = p["resources"]["child"]["fields"].as_array().unwrap();
    if p["resources"]["child"]["path"] == "/export" || p["resources"]["child"]["path"] == "/import" { return Err(invalid("child path conflicts with a workflow route")); }
    let numeric = caps["workflow"] == "decimal_summary" || caps["workflow"] == "statistics";
    if numeric {
        if !caps["value_field"].is_string() || !fields.iter().any(|f| f["name"] == caps["value_field"] && (f["type"] == "int" || f["type"] == "float")) { return Err(invalid("numeric workflow requires a numeric value_field")); }
    } else if !caps["value_field"].is_null() { return Err(invalid("this workflow has no value_field")); }
    if caps["workflow"] == "decimal_summary" {
        if !caps["group_field"].is_string() || !fields.iter().any(|f| f["name"] == caps["group_field"] && f["type"] == "str" && f["required"] == true) { return Err(invalid("decimal summary requires a required string group_field")); }
    } else if !caps["group_field"].is_null() { return Err(invalid("this workflow has no group_field")); }
    Ok(())
}

pub fn database(p: &Value) -> &str { p["capabilities"]["database"].as_str().unwrap_or("postgresql") }

pub fn key(p: &Value) -> String {
    if p.get("capabilities").is_none() { return String::new(); }
    format!(".{}", ["database", "auth", "storage", "workflow"].into_iter().map(|key| p["capabilities"][key].as_str().unwrap()).collect::<Vec<_>>().join("."))
}
