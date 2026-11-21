//! The resource emitter (#304). It renders the checked plan of one resource
//! into the first half of a crate. The shared layers are the reference files of
//! `reference/order` with the names of the resource; the domain file, the store
//! trait and the use cases follow from the plan.
use crate::{Error, model};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

const CARGO_TOML: &str = include_str!("../templates/resource/Cargo.toml.txt");
const LOCK_TEMPLATE: &str = include_str!("../templates/resource/Cargo.lock.txt");
const LIB_RS: &str = include_str!("../reference/order/src/lib.rs");
const APPLICATION_MOD: &str = include_str!("../reference/order/src/application/mod.rs");
const STORE: &str = include_str!("../reference/order/src/application/store.rs");
const PAGINATION: &str = include_str!("../reference/order/src/application/pagination.rs");
const IDEMPOTENCY: &str = include_str!("../reference/order/src/application/idempotency.rs");
const ORDERS: &str = include_str!("../reference/order/src/application/orders.rs");
const DOMAIN_MOD: &str = include_str!("../reference/order/src/domain/mod.rs");
const INFRASTRUCTURE_MOD: &str = include_str!("../reference/order/src/infrastructure/mod.rs");
const MEMORY_STORE: &str = include_str!("../reference/order/src/infrastructure/memory_store.rs");
const INTERFACES_STUB: &str = "// The interfaces layer is added by the next step of the emitter.\n";

/// Names that the generated files use as types. A resource or an enum cannot take them.
const RESERVED_TYPES: [&str; 21] = [
    "Option",
    "Result",
    "Some",
    "None",
    "Ok",
    "Err",
    "String",
    "Vec",
    "Value",
    "Box",
    "Self",
    "Page",
    "ListQuery",
    "StoreError",
    "Clock",
    "SystemClock",
    "Entry",
    "IdempotencyKeys",
    "MemoryStore",
    "FieldError",
    "Default",
];

/// The files of one resource crate, keyed by their path in the crate.
pub(crate) fn render(dsl: &Value) -> Result<BTreeMap<String, String>, Error> {
    check(dsl)?;
    let package = text(dsl, "package");
    let resource = &dsl["resources"][0];
    let name = text(resource, "name");
    let snake = text(resource, "snake");
    let plural = text(resource, "plural");
    let fields = fields_of(resource);
    let shared = |template: &str| substitute(template, name, snake, plural);

    let mut files = BTreeMap::new();
    files.insert(
        "Cargo.toml".to_owned(),
        CARGO_TOML.replace("{{PACKAGE}}", package),
    );
    files.insert("Cargo.lock".to_owned(), lock_file(package));
    files.insert("src/lib.rs".to_owned(), shared(LIB_RS));
    files.insert("src/application/mod.rs".to_owned(), shared(APPLICATION_MOD));
    files.insert("src/application/store.rs".to_owned(), shared(STORE));
    files.insert(
        "src/application/pagination.rs".to_owned(),
        shared(PAGINATION),
    );
    files.insert(
        "src/application/idempotency.rs".to_owned(),
        shared(IDEMPOTENCY),
    );
    files.insert(format!("src/application/{plural}.rs"), shared(ORDERS));
    files.insert("src/domain/mod.rs".to_owned(), shared(DOMAIN_MOD));
    files.insert(
        format!("src/domain/{snake}.rs"),
        domain_file(name, snake, fields),
    );
    files.insert(
        "src/infrastructure/mod.rs".to_owned(),
        shared(INFRASTRUCTURE_MOD),
    );
    files.insert(
        "src/infrastructure/memory_store.rs".to_owned(),
        shared(MEMORY_STORE),
    );
    files.insert(
        "src/interfaces/mod.rs".to_owned(),
        INTERFACES_STUB.to_owned(),
    );
    Ok(files)
}

fn text<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or("")
}

fn fields_of(resource: &Value) -> &[Value] {
    match resource["fields"].as_array() {
        Some(fields) => fields,
        None => &[],
    }
}

fn name_of(field: &Value) -> &str {
    text(field, "name")
}

fn kind_of(field: &Value) -> &str {
    text(field, "type")
}

fn is_required(field: &Value) -> bool {
    field["required"].as_bool() == Some(true)
}

fn values_of(field: &Value) -> Vec<&str> {
    field["values"].as_array().map_or_else(Vec::new, |values| {
        values
            .iter()
            .map(|value| value.as_str().unwrap_or(""))
            .collect()
    })
}

/// The package names that the lock template already holds (its `name = "..."` lines).
fn locked_names() -> impl Iterator<Item = &'static str> {
    LOCK_TEMPLATE.lines().filter_map(|line| {
        line.strip_prefix("name = \"")
            .and_then(|rest| rest.strip_suffix('"'))
    })
}

/// The checks of the resource plan. Each refusal names the pointer of the prompt.
fn check(dsl: &Value) -> Result<(), Error> {
    if dsl["resources"].as_array().map_or(0, Vec::len) != 1 {
        return Err(Error::new(
            "INVALID_VALUE",
            "A resource crate holds one resource in this version.",
            "/bundle/prompt/resources",
        ));
    }
    let package = text(dsl, "package");
    if locked_names().any(|locked| locked == package) {
        return Err(Error::new(
            "INVALID_VALUE",
            "The package name is the name of a dependency of the generated crate.",
            "/bundle/prompt/package",
        ));
    }
    const NAME: &str = "/bundle/prompt/resources/0/name";
    let resource = &dsl["resources"][0];
    let name = text(resource, "name");
    model::identifier(text(resource, "snake"), NAME)?;
    model::identifier(text(resource, "plural"), NAME)?;
    if RESERVED_TYPES.contains(&name) {
        return Err(Error::new(
            "INVALID_VALUE",
            "The resource name is a type of the generated crate.",
            NAME,
        ));
    }
    let mut enum_names = BTreeSet::new();
    for (index, field) in fields_of(resource).iter().enumerate() {
        if kind_of(field) != "enum" {
            continue;
        }
        let pointer = format!("/bundle/prompt/resources/0/fields/{index}/name");
        let type_name = pascal(name_of(field));
        let clashes = RESERVED_TYPES.contains(&type_name.as_str())
            || type_name == name
            || type_name == format!("{name}Input")
            || !enum_names.insert(type_name);
        if clashes {
            return Err(Error::new(
                "INVALID_VALUE",
                "The enum name is a type of the generated crate.",
                &pointer,
            ));
        }
        let mut variants = BTreeSet::new();
        for value in values_of(field) {
            if !variants.insert(pascal(value)) {
                return Err(Error::new(
                    "INVALID_VALUE",
                    "Two values of the enum have the same Rust name.",
                    &format!("/bundle/prompt/resources/0/fields/{index}/values"),
                ));
            }
        }
    }
    Ok(())
}

/// `pascal("loyalty_tier")` is `LoyaltyTier`: split on `_`, drop empty parts,
/// upper-case the first character of each part and join.
fn pascal(text: &str) -> String {
    text.split('_')
        .filter(|part| !part.is_empty())
        .map(capitalize)
        .collect()
}

fn capitalize(part: &str) -> String {
    let mut chars = part.chars();
    chars
        .next()
        .map(|first| first.to_ascii_uppercase().to_string() + chars.as_str())
        .unwrap_or_default()
}

/// Turns a reference text into the text of another resource. It is one pass:
/// the names become markers first, so that a plural such as `order_items`
/// is not changed twice.
fn substitute(template: &str, name: &str, snake: &str, plural: &str) -> String {
    template
        .replace("orders", "{{PLURAL}}")
        .replace("Order", "{{RESOURCE}}")
        .replace("order", "{{SNAKE}}")
        .replace("{{PLURAL}}", plural)
        .replace("{{RESOURCE}}", name)
        .replace("{{SNAKE}}", snake)
}

/// The lock of the crate: the template with the root package at its sorted place.
fn lock_file(package: &str) -> String {
    let root = format!(
        "[[package]]\nname = \"{package}\"\nversion = \"0.1.0\"\ndependencies = [\n \"proptest\",\n \"serde_json\",\n \"time\",\n \"uuid\",\n]"
    );
    let mut blocks = LOCK_TEMPLATE.trim_end().split("\n\n");
    let header = blocks.next().unwrap_or("");
    let mut out = vec![header.to_owned()];
    let mut placed = false;
    for block in blocks {
        if !placed && block_name(block) > package {
            out.push(root.clone());
            placed = true;
        }
        out.push(block.to_owned());
    }
    if !placed {
        out.push(root);
    }
    format!("{}\n", out.join("\n\n"))
}

fn block_name(block: &str) -> &str {
    block
        .lines()
        .nth(1)
        .unwrap_or("")
        .trim_start_matches("name = \"")
        .trim_end_matches('"')
}

fn count_word(n: i64) -> String {
    const WORDS: [&str; 10] = [
        "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    ];
    match usize::try_from(n) {
        Ok(count) if (1..=10).contains(&count) => WORDS[count - 1].to_owned(),
        _ => n.to_string(),
    }
}

/// The Rust type of a field in the input and the resource struct.
fn rust_type(field: &Value) -> String {
    let base = match kind_of(field) {
        "string" => "String".to_owned(),
        "integer" => "i64".to_owned(),
        _ => pascal(name_of(field)),
    };
    if is_required(field) {
        base
    } else {
        format!("Option<{base}>")
    }
}

/// The text of the domain file of the resource: the enums, the input, the reader
/// of a body, the struct, the id and the time, the create and replace functions,
/// and the JSON of the resource. Parts are joined by one empty line.
fn domain_file(name: &str, snake: &str, fields: &[Value]) -> String {
    let mut parts = vec!["use serde_json::{Value, json};\n".to_owned()];
    for field in fields.iter().filter(|field| kind_of(field) == "enum") {
        let type_name = pascal(name_of(field));
        parts.push(enum_part(&type_name, &values_of(field)));
        parts.push(enum_impl_part(&type_name, &values_of(field)));
    }
    parts.push(input_part(name, fields));
    parts.push(FIELD_ERROR_PART.to_owned());
    parts.push(parse_input_part(name, fields));
    parts.push(resource_part(name, fields));
    parts.push(FIXED_ID_AND_TIME_PART.to_owned());
    parts.push(impl_part(name, fields));
    parts.push(to_json_part(name, snake, fields));
    parts.join("\n")
}

fn enum_part(type_name: &str, values: &[&str]) -> String {
    let variants: String = values
        .iter()
        .map(|value| format!("    {},\n", pascal(value)))
        .collect();
    format!("#[derive(Debug, Clone, Copy, PartialEq, Eq)]\npub enum {type_name} {{\n{variants}}}\n")
}

fn enum_impl_part(type_name: &str, values: &[&str]) -> String {
    let as_str: String = values
        .iter()
        .map(|value| {
            format!(
                "            {type_name}::{} => \"{value}\",\n",
                pascal(value)
            )
        })
        .collect();
    let parse: String = values
        .iter()
        .map(|value| {
            format!(
                "            \"{value}\" => Some({type_name}::{}),\n",
                pascal(value)
            )
        })
        .collect();
    format!(
        "impl {type_name} {{\n    pub fn as_str(&self) -> &'static str {{\n        match self {{\n{as_str}        }}\n    }}\n\n    pub fn parse(text: &str) -> Option<{type_name}> {{\n        match text {{\n{parse}            _ => None,\n        }}\n    }}\n}}\n"
    )
}

fn input_part(name: &str, fields: &[Value]) -> String {
    let declared: String = fields
        .iter()
        .map(|field| format!("    pub {}: {},\n", name_of(field), rust_type(field)))
        .collect();
    format!("#[derive(Debug, Clone, PartialEq)]\npub struct {name}Input {{\n{declared}}}\n")
}

const FIELD_ERROR_PART: &str = "#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FieldError {
    pub field: String,
    pub message: String,
}

fn field_error(field: &str, message: &str) -> FieldError {
    FieldError {
        field: field.to_owned(),
        message: message.to_owned(),
    }
}
";

/// One arm of a body reader that records a bad field and gives no value.
fn fail(field: &str, message: &str) -> String {
    format!("{{ errors.push(field_error(\"{field}\", \"{message}\")); None }}")
}

/// The block of one field. `Some(x)` in the arms gives `Some(Some(x))` for an
/// optional field, so that the value of the block is an `Option` of the field.
fn parse_block(field: &Value) -> String {
    let name = name_of(field);
    let required = is_required(field);
    let some = |value: &str| {
        if required {
            format!("Some({value})")
        } else {
            format!("Some(Some({value}))")
        }
    };
    let mut arms = String::new();
    if required {
        arms.push_str(&format!("        None => {},\n", fail(name, "is required")));
    } else {
        arms.push_str("        None | Some(Value::Null) => Some(None),\n");
    }
    match kind_of(field) {
        "string" => arms.push_str(&string_arms(field, &some)),
        "integer" => arms.push_str(&integer_arms(field, &some)),
        _ => arms.push_str(&enum_arms(field, &some)),
    }
    format!("    let parsed_{name} = match object.get(\"{name}\") {{\n{arms}    }};\n\n")
}

fn string_arms(field: &Value, some: &dyn Fn(&str) -> String) -> String {
    let name = name_of(field);
    let mut arms = String::new();
    if let Some(count) = field["upper_letters"].as_i64() {
        let noun = if count == 1 { "letter" } else { "letters" };
        arms.push_str(&format!(
            "        Some(Value::String(text)) if text.len() == {count} && text.bytes().all(|b| b.is_ascii_uppercase()) => {},\n",
            some("text.clone()")
        ));
        arms.push_str(&format!(
            "        Some(_) => {},\n",
            fail(
                name,
                &format!("must be {} upper-case {noun}", count_word(count))
            )
        ));
        return arms;
    }
    let min = field["min"].as_i64().unwrap_or(0);
    let max = field["max"].as_i64();
    if min == 0 && max.is_none() {
        arms.push_str(&format!(
            "        Some(Value::String(text)) => {},\n",
            some("text.clone()")
        ));
    } else {
        let mut branches = String::new();
        let mut keyword = "if";
        if min >= 1 {
            let message = if min == 1 {
                "must not be empty".to_owned()
            } else {
                format!("must be at least {min} characters")
            };
            branches.push_str(&format!(
                "{keyword} length < {min} {} ",
                fail(name, &message)
            ));
            keyword = "else if";
        }
        if let Some(max) = max {
            branches.push_str(&format!(
                "{keyword} length > {max} {} ",
                fail(name, &format!("must be at most {max} characters"))
            ));
        }
        arms.push_str(&format!(
            "        Some(Value::String(text)) => {{\n            let length = text.chars().count();\n            {branches}else {{ {} }}\n        }}\n",
            some("text.clone()")
        ));
    }
    arms.push_str(&format!(
        "        Some(_) => {},\n",
        fail(name, "must be a string")
    ));
    arms
}

fn integer_arms(field: &Value, some: &dyn Fn(&str) -> String) -> String {
    let name = name_of(field);
    let mut arms = String::new();
    let bounds = match (field["min"].as_i64(), field["max"].as_i64()) {
        (Some(low), Some(high)) => Some((
            format!("({low}..={high}).contains(&number)"),
            format!("must be from {low} to {high}"),
        )),
        (Some(low), None) => Some((format!("number >= {low}"), format!("must be {low} or more"))),
        (None, Some(high)) => Some((
            format!("number <= {high}"),
            format!("must be {high} or less"),
        )),
        (None, None) => None,
    };
    match bounds {
        Some((guard, message)) => {
            arms.push_str(&format!(
                "        Some(value) => match value.as_i64() {{\n            Some(number) if {guard} => {},\n            Some(_) => {},\n            None => {},\n        }},\n",
                some("number"),
                fail(name, &message),
                fail(name, "must be an integer")
            ));
        }
        None => {
            arms.push_str(&format!(
                "        Some(value) => match value.as_i64() {{\n            Some(number) => {},\n            None => {},\n        }},\n",
                some("number"),
                fail(name, "must be an integer")
            ));
        }
    }
    arms
}

fn enum_arms(field: &Value, some: &dyn Fn(&str) -> String) -> String {
    let name = name_of(field);
    let type_name = pascal(name);
    let values = values_of(field);
    let message = format!("must be one of {}", values.join(", "));
    format!(
        "        Some(Value::String(text)) => match {type_name}::parse(text) {{\n            Some(variant) => {},\n            None => {},\n        }},\n        Some(_) => {},\n",
        some("variant"),
        fail(name, &message),
        fail(name, &message)
    )
}

fn parse_input_part(name: &str, fields: &[Value]) -> String {
    let blocks: String = fields.iter().map(parse_block).collect();
    let keys: Vec<String> = fields
        .iter()
        .map(|field| format!("\"{}\"", name_of(field)))
        .collect();
    let unwrapped: String = fields
        .iter()
        .map(|field| {
            let field_name = name_of(field);
            format!(
                "    let Some(value_{field_name}) = parsed_{field_name} else {{\n        return Err(errors);\n    }};\n"
            )
        })
        .collect();
    let assigned: String = fields
        .iter()
        .map(|field| {
            let field_name = name_of(field);
            format!("        {field_name}: value_{field_name},\n")
        })
        .collect();
    format!(
        "/// Reads a create or replace body. Every bad field gets its own entry, in declared sequence, then unknown fields.
pub fn parse_input(body: &Value) -> Result<{name}Input, Vec<FieldError>> {{
    let Some(object) = body.as_object() else {{
        return Err(vec![field_error(\"body\", \"must be a JSON object\")]);
    }};
    let mut errors = Vec::new();

{blocks}    for key in object.keys() {{
        if !matches!(key.as_str(), {}) {{
            errors.push(field_error(key, \"unknown field\"));
        }}
    }}

    if !errors.is_empty() {{
        return Err(errors);
    }}
{unwrapped}    Ok({name}Input {{
{assigned}    }})
}}
",
        keys.join(" | ")
    )
}

fn resource_part(name: &str, fields: &[Value]) -> String {
    let declared: String = fields
        .iter()
        .map(|field| format!("    pub {}: {},\n", name_of(field), rust_type(field)))
        .collect();
    format!(
        "#[derive(Debug, Clone, PartialEq)]\npub struct {name} {{\n    pub id: String,\n    pub created_at: String,\n    pub updated_at: String,\n{declared}}}\n"
    )
}

const FIXED_ID_AND_TIME_PART: &str = "pub fn new_id() -> String {
    uuid::Uuid::now_v7().to_string()
}

pub fn now_rfc3339() -> String {
    time::OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .unwrap_or_else(|_| \"1970-01-01T00:00:00Z\".to_owned())
}
";

fn impl_part(name: &str, fields: &[Value]) -> String {
    let copied: String = fields
        .iter()
        .map(|field| {
            let field_name = name_of(field);
            format!("            {field_name}: input.{field_name},\n")
        })
        .collect();
    format!(
        "impl {name} {{
    pub fn create(input: {name}Input, id: String, now: String) -> {name} {{
        {name} {{
            id,
            created_at: now.clone(),
            updated_at: now,
{copied}        }}
    }}

    pub fn replace(&self, input: {name}Input, now: String) -> {name} {{
        {name} {{
            id: self.id.clone(),
            created_at: self.created_at.clone(),
            updated_at: now,
{copied}        }}
    }}
}}
"
    )
}

fn to_json_part(name: &str, snake: &str, fields: &[Value]) -> String {
    let lines: String = fields
        .iter()
        .map(|field| {
            let field_name = name_of(field);
            let value = match (kind_of(field), is_required(field)) {
                ("enum", true) => format!("{snake}.{field_name}.as_str()"),
                ("enum", false) => format!("{snake}.{field_name}.map(|value| value.as_str())"),
                _ => format!("{snake}.{field_name}"),
            };
            format!("        \"{field_name}\": {value},\n")
        })
        .collect();
    format!(
        "pub fn to_json({snake}: &{name}) -> Value {{
    json!({{
        \"id\": {snake}.id,
{lines}        \"created_at\": {snake}.created_at,
        \"updated_at\": {snake}.updated_at,
    }})
}}
"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pascal_joins_the_parts_of_a_name() {
        assert_eq!(pascal("loyalty_tier"), "LoyaltyTier");
        assert_eq!(pascal("a__b"), "AB");
        assert_eq!(pascal("x_y"), "XY");
    }

    #[test]
    fn count_word_spells_small_numbers() {
        assert_eq!(count_word(3), "three");
        assert_eq!(count_word(11), "11");
    }
}
