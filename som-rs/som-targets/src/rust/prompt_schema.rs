//! The structural prompt check of bundle.schema.json: the bundle prompt must match
//! som.compiler-prompt-family.v1 (contracts/compiler-v1/compiler-prompt.schema.json).
//!
//! This is the JSON Schema 2020-12 subset that schema uses. A failure names the first
//! failing instance member in a fixed order: object members in sorted order, then array
//! items by index. A oneOf picks the branch whose const members match the instance, so a
//! prompt is checked against the branch for its version.
use regex::Regex;
use serde_json::{Map, Value};
use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};

pub const PROMPT_SCHEMA_TEXT: &str =
    include_str!("../../../contracts/compiler-v1/compiler-prompt.schema.json");
static SCHEMA: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(PROMPT_SCHEMA_TEXT).expect("prompt schema JSON"));
static PATTERNS: LazyLock<Mutex<HashMap<String, Regex>>> = LazyLock::new(Mutex::default);

/// The pointer, below the prompt, of the first member that does not match the schema.
pub fn check(prompt: &Value) -> Result<(), String> {
    walk(&SCHEMA, prompt, "")
}

fn walk(schema: &Value, value: &Value, at: &str) -> Result<(), String> {
    let Some(schema) = schema.as_object() else {
        return match schema {
            Value::Bool(false) => Err(at.to_owned()),
            _ => Ok(()),
        };
    };
    if let Some(reference) = schema.get("$ref").and_then(Value::as_str) {
        let name = reference.strip_prefix("#/$defs/").expect("local $ref");
        walk(&SCHEMA["$defs"][name], value, at)?;
    }
    for part in schema
        .get("allOf")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        walk(part, value, at)?;
    }
    if let Some(branches) = schema.get("oneOf").and_then(Value::as_array) {
        one_of(branches, value, at)?;
    }
    if let Some(negated) = schema.get("not")
        && walk(negated, value, at).is_ok()
    {
        return Err(at.to_owned());
    }
    if let Some(condition) = schema.get("if") {
        let branch = if walk(condition, value, at).is_ok() {
            schema.get("then")
        } else {
            schema.get("else")
        };
        if let Some(branch) = branch {
            walk(branch, value, at)?;
        }
    }
    if let Some(kind) = schema.get("type")
        && !type_matches(kind, value)
    {
        return Err(at.to_owned());
    }
    if schema
        .get("const")
        .is_some_and(|expected| !same(expected, value))
        || schema
            .get("enum")
            .and_then(Value::as_array)
            .is_some_and(|items| !items.iter().any(|item| same(item, value)))
    {
        return Err(at.to_owned());
    }
    match value {
        Value::Object(object) => check_object(schema, object, at),
        Value::Array(items) => check_array(schema, items, at),
        Value::String(text) => check_string(schema, text, at),
        Value::Number(number) => {
            let number = number.as_f64().unwrap_or(f64::NAN);
            let low = schema.get("minimum").and_then(Value::as_f64);
            let high = schema.get("maximum").and_then(Value::as_f64);
            if low.is_some_and(|low| number < low) || high.is_some_and(|high| number > high) {
                return Err(at.to_owned());
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

/// Exactly one branch must match. The branch whose const members equal the instance
/// members decides the reported pointer; without one, the pointer is the instance.
fn one_of(branches: &[Value], value: &Value, at: &str) -> Result<(), String> {
    let passing = branches
        .iter()
        .filter(|branch| walk(branch, value, at).is_ok())
        .count();
    if passing == 1 {
        return Ok(());
    }
    let selected: Vec<(&Value, Vec<String>)> = branches
        .iter()
        .filter_map(|branch| discriminators(branch, value).map(|keys| (branch, keys)))
        .filter(|(_, keys)| !keys.is_empty())
        .collect();
    if passing == 0
        && let [(branch, _)] = selected.as_slice()
    {
        return walk(branch, value, at);
    }
    if passing == 0
        && let (Some(object), Some(Value::Object(properties))) = (
            value.as_object(),
            branches.first().and_then(|branch| branch.get("properties")),
        )
    {
        // No branch claims the instance: point at the first const member it gets wrong.
        for (key, property) in properties {
            if property.get("const").is_some() && object.contains_key(key) {
                return Err(format!("{at}/{}", escape(key)));
            }
        }
    }
    Err(at.to_owned())
}

/// The const members of a branch, when the instance has every one of them with that value.
fn discriminators(branch: &Value, value: &Value) -> Option<Vec<String>> {
    let object = value.as_object()?;
    let mut keys = Vec::new();
    for (key, property) in branch.get("properties")?.as_object()? {
        if let Some(expected) = property.get("const") {
            if !object.get(key).is_some_and(|actual| same(expected, actual)) {
                return None;
            }
            keys.push(key.clone());
        }
    }
    Some(keys)
}

fn check_object(
    schema: &Map<String, Value>,
    object: &Map<String, Value>,
    at: &str,
) -> Result<(), String> {
    let properties = schema.get("properties").and_then(Value::as_object);
    for (key, member) in object {
        let here = format!("{at}/{}", escape(key));
        if let Some(names) = schema.get("propertyNames") {
            walk(names, &Value::String(key.clone()), &here)?;
        }
        match properties.and_then(|properties| properties.get(key)) {
            Some(property) => walk(property, member, &here)?,
            None => {
                if let Some(extra) = schema.get("additionalProperties") {
                    walk(extra, member, &here)?;
                }
            }
        }
    }
    let required = schema.get("required").and_then(Value::as_array);
    if required
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .any(|key| !object.contains_key(key))
    {
        return Err(at.to_owned());
    }
    Ok(())
}

fn check_array(schema: &Map<String, Value>, items: &[Value], at: &str) -> Result<(), String> {
    let count = |key: &str| schema.get(key).and_then(Value::as_u64);
    let length = items.len() as u64;
    if count("minItems").is_some_and(|low| length < low)
        || count("maxItems").is_some_and(|high| length > high)
    {
        return Err(at.to_owned());
    }
    let prefix = schema
        .get("prefixItems")
        .and_then(Value::as_array)
        .map_or(&[][..], Vec::as_slice);
    for (index, item) in items.iter().enumerate() {
        let here = format!("{at}/{index}");
        match prefix.get(index) {
            Some(part) => walk(part, item, &here)?,
            None => {
                if let Some(rest) = schema.get("items") {
                    walk(rest, item, &here)?;
                }
            }
        }
    }
    if schema.get("uniqueItems") == Some(&Value::Bool(true)) {
        for (index, item) in items.iter().enumerate() {
            if items[..index].iter().any(|earlier| same(earlier, item)) {
                return Err(format!("{at}/{index}"));
            }
        }
    }
    Ok(())
}

fn check_string(schema: &Map<String, Value>, text: &str, at: &str) -> Result<(), String> {
    let length = text.chars().count() as u64;
    let bound = |key: &str| schema.get(key).and_then(Value::as_u64);
    if bound("minLength").is_some_and(|low| length < low)
        || bound("maxLength").is_some_and(|high| length > high)
    {
        return Err(at.to_owned());
    }
    if let Some(pattern) = schema.get("pattern").and_then(Value::as_str) {
        let mut cache = PATTERNS.lock().unwrap_or_else(|poison| poison.into_inner());
        let regex = cache
            .entry(pattern.to_owned())
            .or_insert_with(|| Regex::new(pattern).expect("schema pattern"));
        if !regex.is_match(text) {
            return Err(at.to_owned());
        }
    }
    Ok(())
}

fn type_matches(kind: &Value, value: &Value) -> bool {
    match kind {
        Value::Array(kinds) => kinds.iter().any(|kind| type_matches(kind, value)),
        Value::String(kind) => match kind.as_str() {
            "object" => value.is_object(),
            "array" => value.is_array(),
            "string" => value.is_string(),
            "boolean" => value.is_boolean(),
            "null" => value.is_null(),
            "number" => value.is_number(),
            "integer" => value
                .as_f64()
                .is_some_and(|number| value.is_i64() || value.is_u64() || number.fract() == 0.0),
            _ => false,
        },
        _ => false,
    }
}

/// JSON Schema equality: numbers compare by value.
fn same(left: &Value, right: &Value) -> bool {
    match (left, right) {
        (Value::Number(a), Value::Number(b)) => a.as_f64() == b.as_f64(),
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| same(a, b))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .all(|(key, item)| b.get(key).is_some_and(|other| same(item, other)))
        }
        _ => left == right,
    }
}

fn escape(key: &str) -> String {
    key.replace('~', "~0").replace('/', "~1")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn example(name: &str) -> Value {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../contracts/compiler-v1/examples")
            .join(format!("{name}.prompt.json"));
        serde_json::from_str(&std::fs::read_to_string(path).expect("example")).expect("JSON")
    }

    #[test]
    fn public_examples_match_the_prompt_family() {
        for name in ["v1", "v2", "v3", "v4", "v5", "v6", "v7"] {
            assert_eq!(check(&example(name)), Ok(()), "{name}");
        }
    }

    #[test]
    fn the_first_wrong_member_is_named() {
        let mut prompt = example("v1");
        prompt["resource"]["zz"] = json!(1);
        assert_eq!(check(&prompt), Err("/resource/zz".to_owned()));
        let mut prompt = example("v1");
        prompt["resource"]["fields"][0]["type"] = json!("date");
        assert_eq!(check(&prompt), Err("/resource/fields/0/type".to_owned()));
        let mut prompt = example("v1");
        prompt["version"] = json!("som.prompt.v9");
        assert_eq!(check(&prompt), Err("/version".to_owned()));
        assert_eq!(check(&json!([])), Err(String::new()));
    }
}
