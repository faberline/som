//! Typed JSON prompt version 3: resources with fields and standards of the
//! catalog `som.std.v1`. The Defer path (prompt versions 1 and 2) does not use it.
//! Every refusal names the pointer of the value that is wrong; nothing is guessed.
use crate::error::Error;
use crate::model;
use serde_json::{Map, Value, json};
use std::collections::BTreeSet;

pub const PROMPT_VERSION_V3: &str = "som.prompt.service.v3";
pub const JSON_FORMAT_V3: &str = "som.prompt-json.service.v3";
pub const DSL_VERSION_V3: &str = "som.dsl.service.v3";
pub const BUNDLE_VERSION_RESOURCE: &str = "som.bundle.resource.v1";
pub const TARGET_RESOURCE: &str = "rust-resource-service.v1";
pub const CATALOG: &str = "som.std.v1";

const MAX_RESOURCES: usize = 8;
const MAX_FIELDS: usize = 32;
const MAX_LENGTH: i64 = 10000;
const MAX_UPPER_LETTERS: i64 = 64;
const MAX_INTEGER: i64 = 9007199254740991;
const RESERVED_FIELDS: [&str; 3] = ["id", "created_at", "updated_at"];
const STANDARDS: [&str; 2] = ["CRUD", "standard pagination"];
/// Standards that `som.std.v1` does not hold, named by the standards pages for a later version.
const NOT_IN_V1: [&str; 10] = [
    "patch",
    "soft delete",
    "sort",
    "filter",
    "total count",
    "real database",
    "batch",
    "relations",
    "events",
    "multi-tenant",
];

/// A typed prompt v3 that has passed `validate_v3`.
#[derive(Clone, Debug, PartialEq)]
pub struct PromptV3(pub Value);

fn text<'a>(value: &'a Value, pointer: &str) -> Result<&'a str, Error> {
    value
        .as_str()
        .ok_or_else(|| Error::new("INVALID_SCHEMA", "Expected a string.", pointer))
}

/// The value of an optional integer constraint, checked against `low..=high`.
fn number(
    object: &Map<String, Value>,
    key: &str,
    at: &str,
    low: i64,
    high: i64,
) -> Result<Option<i64>, Error> {
    let pointer = format!("{at}/{key}");
    object
        .get(key)
        .map(|value| {
            value
                .as_i64()
                .filter(|n| (low..=high).contains(n))
                .ok_or_else(|| {
                    Error::new(
                        "INVALID_VALUE",
                        format!("Expected an integer from {low} to {high}."),
                        &pointer,
                    )
                })
        })
        .transpose()
}

fn valid_resource_name(name: &str) -> bool {
    name.len() <= 64
        && name.as_bytes().first().is_some_and(u8::is_ascii_uppercase)
        && name.bytes().all(|b| b.is_ascii_alphanumeric())
}

/// Checks a typed prompt v3 and keeps it unchanged. Checks run in a fixed order
/// (header, resources, fields, standards) so that each pointer is stable.
pub fn validate_v3(value: &Value) -> Result<PromptV3, Error> {
    let p = model::exact(value, &["version", "package", "target", "resources"], "")?;
    if text(&p["version"], "/version")? != PROMPT_VERSION_V3 {
        return Err(Error::new(
            "UNSUPPORTED_VERSION",
            "Unsupported service prompt version.",
            "/version",
        ));
    }
    model::identifier(text(&p["package"], "/package")?, "/package")?;
    if text(&p["target"], "/target")? != TARGET_RESOURCE {
        return Err(Error::new(
            "UNSUPPORTED_TARGET",
            "Only rust-resource-service.v1 is supported.",
            "/target",
        )
        .with_allowed_form("Target rust-resource-service.v1."));
    }
    let resources = p["resources"].as_array().ok_or_else(|| {
        Error::new(
            "INVALID_SCHEMA",
            "Expected an array of resources.",
            "/resources",
        )
    })?;
    if resources.is_empty() || resources.len() > MAX_RESOURCES {
        return Err(Error::new(
            "INVALID_SCHEMA",
            "Expected 1 to 8 resources.",
            "/resources",
        ));
    }
    let mut names = BTreeSet::new();
    for (i, resource) in resources.iter().enumerate() {
        validate_resource(resource, &format!("/resources/{i}"), &mut names)?;
    }
    Ok(PromptV3(value.clone()))
}

fn validate_resource(value: &Value, at: &str, names: &mut BTreeSet<String>) -> Result<(), Error> {
    let r = model::exact(value, &["name", "fields", "standards"], at)?;
    let name = text(&r["name"], &format!("{at}/name"))?;
    if !valid_resource_name(name) || !names.insert(name.to_owned()) {
        return Err(Error::new(
            "INVALID_VALUE",
            "Expected a unique Pascal resource name of 1 to 64 bytes.",
            &format!("{at}/name"),
        ));
    }

    let fields_at = format!("{at}/fields");
    let fields = r["fields"]
        .as_array()
        .ok_or_else(|| Error::new("INVALID_SCHEMA", "Expected an array of fields.", &fields_at))?;
    if fields.is_empty() {
        return Err(Error::new(
            "MISSING_FACT",
            "A resource needs at least one field.",
            &fields_at,
        ));
    }
    if fields.len() > MAX_FIELDS {
        return Err(Error::new(
            "INVALID_SCHEMA",
            "A resource has at most 32 fields.",
            &fields_at,
        ));
    }
    let mut field_names = BTreeSet::new();
    for (j, field) in fields.iter().enumerate() {
        validate_field(field, &format!("{fields_at}/{j}"), &mut field_names)?;
    }

    validate_standards(&r["standards"], at, name)
}

fn validate_field(value: &Value, at: &str, seen: &mut BTreeSet<String>) -> Result<(), Error> {
    let object = value
        .as_object()
        .ok_or_else(|| Error::new("INVALID_SCHEMA", "Expected a field object.", at))?;
    if !object.contains_key("required") {
        return Err(Error::new(
            "MISSING_FACT",
            "A field must state required or optional.",
            &format!("{at}/required"),
        ));
    }
    if !object.contains_key("name") || !object.contains_key("type") {
        return Err(Error::new(
            "INVALID_SCHEMA",
            "A field needs a name and a type.",
            at,
        ));
    }

    let name = text(&object["name"], &format!("{at}/name"))?;
    model::identifier(name, &format!("{at}/name"))?;
    if RESERVED_FIELDS.contains(&name) || !seen.insert(name.to_owned()) {
        return Err(Error::new(
            "INVALID_VALUE",
            "A field name must be unique and not id, created_at or updated_at.",
            &format!("{at}/name"),
        ));
    }

    let kind = text(&object["type"], &format!("{at}/type"))?;
    let allowed: &[&str] = match kind {
        "string" => &["min", "max", "upper_letters"],
        "integer" => &["min", "max"],
        "enum" => &["values"],
        _ => {
            return Err(Error::new(
                "INVALID_VALUE",
                "Expected the type string, integer or enum.",
                &format!("{at}/type"),
            ));
        }
    };
    if !object["required"].is_boolean() {
        return Err(Error::new(
            "INVALID_VALUE",
            "Expected true or false.",
            &format!("{at}/required"),
        ));
    }
    for key in object.keys() {
        let known = ["name", "type", "required"].contains(&key.as_str());
        if !known && !allowed.contains(&key.as_str()) {
            return Err(Error::new(
                "INVALID_SCHEMA",
                format!("The key {key} is not allowed for a {kind} field."),
                &format!("{at}/{key}"),
            ));
        }
    }

    match kind {
        "string" => validate_string(object, at),
        "integer" => validate_integer(object, at),
        _ => validate_enum(object, at),
    }
}

fn validate_string(object: &Map<String, Value>, at: &str) -> Result<(), Error> {
    let min = number(object, "min", at, 0, MAX_LENGTH)?;
    let max = number(object, "max", at, 0, MAX_LENGTH)?;
    if let (Some(low), Some(high)) = (min, max)
        && low > high
    {
        return Err(Error::new(
            "INVALID_VALUE",
            "min must not be greater than max.",
            &format!("{at}/min"),
        ));
    }
    let upper = number(object, "upper_letters", at, 1, MAX_UPPER_LETTERS)?;
    if upper.is_some() && (min.is_some() || max.is_some()) {
        return Err(Error::new(
            "INVALID_VALUE",
            "upper_letters cannot be combined with min or max.",
            &format!("{at}/upper_letters"),
        ));
    }
    Ok(())
}

fn validate_integer(object: &Map<String, Value>, at: &str) -> Result<(), Error> {
    let min = number(object, "min", at, -MAX_INTEGER, MAX_INTEGER)?;
    let max = number(object, "max", at, -MAX_INTEGER, MAX_INTEGER)?;
    if let (Some(low), Some(high)) = (min, max)
        && low > high
    {
        return Err(Error::new(
            "INVALID_VALUE",
            "min must not be greater than max.",
            &format!("{at}/min"),
        ));
    }
    Ok(())
}

fn validate_enum(object: &Map<String, Value>, at: &str) -> Result<(), Error> {
    let values_at = format!("{at}/values");
    let values = object.get("values").ok_or_else(|| {
        Error::new(
            "MISSING_FACT",
            "An enum field must list its values.",
            &values_at,
        )
    })?;
    let list = values
        .as_array()
        .filter(|list| (2..=16).contains(&list.len()))
        .ok_or_else(|| Error::new("INVALID_VALUE", "Expected 2 to 16 values.", &values_at))?;
    let mut distinct = BTreeSet::new();
    for value in list {
        let word = value
            .as_str()
            .filter(|word| word.len() <= 32)
            .ok_or_else(|| {
                Error::new(
                    "INVALID_VALUE",
                    "Each value is a lower-case identifier of at most 32 bytes.",
                    &values_at,
                )
            })?;
        model::identifier(word, &values_at)?;
        if !distinct.insert(word) {
            return Err(Error::new(
                "INVALID_VALUE",
                "The values must be distinct.",
                &values_at,
            ));
        }
    }
    Ok(())
}

fn validate_standards(value: &Value, at: &str, name: &str) -> Result<(), Error> {
    let at = format!("{at}/standards");
    let standards = value
        .as_array()
        .ok_or_else(|| Error::new("INVALID_SCHEMA", "Expected an array of standards.", &at))?;
    let mut listed = BTreeSet::new();
    for (k, standard) in standards.iter().enumerate() {
        let pointer = format!("{at}/{k}");
        let standard_name = text(standard, &pointer)?;
        if !STANDARDS.contains(&standard_name) {
            let mut detail = format!("Standard \"{standard_name}\" is not in som.std.v1.");
            if NOT_IN_V1.contains(&standard_name.to_lowercase().as_str()) {
                detail.push_str(" The catalog lists it as not in v1.");
            }
            return Err(Error::new("UNKNOWN_STANDARD", detail, &pointer));
        }
        if !listed.insert(standard_name) {
            return Err(Error::new(
                "INVALID_VALUE",
                "A standard is listed only once.",
                &pointer,
            ));
        }
    }
    if !(listed.contains("CRUD") && listed.contains("standard pagination")) {
        return Err(Error::new(
            "MISSING_STANDARD",
            "Each resource must list CRUD and standard pagination.",
            &at,
        )
        .with_allowed_form(&format!(
            "Apply CRUD to {name}. Apply standard pagination to {name}."
        )));
    }
    Ok(())
}

/// `OrderItem` gives `order_item`: an underscore before each upper-case letter but the first.
pub fn snake_case(pascal: &str) -> String {
    let mut out = String::new();
    for (i, c) in pascal.chars().enumerate() {
        if c.is_ascii_uppercase() {
            if i > 0 {
                out.push('_');
            }
            out.push(c.to_ascii_lowercase());
        } else {
            out.push(c);
        }
    }
    out
}

/// The plural of a snake name and its path. `Category` gives `categories`
/// and `/v1/categories`; `OrderItem` gives `order_items` and `/v1/order-items`.
pub fn plural_path(snake: &str) -> (String, String) {
    let plural = pluralize(snake);
    let path = format!("/v1/{}", plural.replace('_', "-"));
    (plural, path)
}

fn pluralize(snake: &str) -> String {
    if let Some(stem) = snake.strip_suffix('y')
        && stem
            .bytes()
            .last()
            .is_some_and(|b| !matches!(b, b'a' | b'e' | b'i' | b'o' | b'u'))
    {
        return format!("{stem}ies");
    }
    if snake.ends_with(['s', 'x', 'z']) || snake.ends_with("ch") || snake.ends_with("sh") {
        return format!("{snake}es");
    }
    format!("{snake}s")
}

/// The plan of a checked prompt v3 (DSL version 3).
pub fn plan_v3(prompt: &PromptV3) -> Value {
    let p = &prompt.0;
    let resources: Vec<Value> = p["resources"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|r| {
            let name = r["name"].as_str().unwrap_or("");
            let snake = snake_case(name);
            let (plural, path) = plural_path(&snake);
            json!({
                "name": name,
                "snake": snake,
                "plural": plural,
                "path": path,
                "fields": r["fields"],
                "standards": r["standards"],
            })
        })
        .collect();
    json!({
        "version": DSL_VERSION_V3,
        "catalog": CATALOG,
        "package": p["package"],
        "target": TARGET_RESOURCE,
        "resources": resources,
    })
}
