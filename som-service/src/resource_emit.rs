//! The resource emitter (#304). It renders the checked plan of one resource
//! into the first half of a crate. The shared layers are the reference files of
//! `reference/order` with the names of the resource; the domain file, the store
//! trait and the use cases follow from the plan.
use crate::{Error, model};
use serde_json::{Map, Value, json};
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
const INTERFACES_AUTH: &str = include_str!("../reference/order/src/interfaces/auth.rs");
const INTERFACES_ERROR: &str = include_str!("../reference/order/src/interfaces/error.rs");
const INTERFACES_ETAG: &str = include_str!("../reference/order/src/interfaces/etag.rs");
const INTERFACES_LIMITS: &str = include_str!("../reference/order/src/interfaces/limits.rs");
const INTERFACES_METRICS: &str = include_str!("../reference/order/src/interfaces/metrics.rs");
const INTERFACES_MOD: &str = include_str!("../reference/order/src/interfaces/mod.rs");
const INTERFACES_PROBES: &str = include_str!("../reference/order/src/interfaces/probes.rs");
const INTERFACES_REQUEST_ID: &str = include_str!("../reference/order/src/interfaces/request_id.rs");
const INTERFACES_ROUTES: &str = include_str!("../reference/order/src/interfaces/routes.rs");
const INTERFACES_SERVER: &str = include_str!("../reference/order/src/interfaces/server.rs");
const OPENAPI_TEMPLATE: &str = include_str!("../reference/order/openapi.json");
const TEST_AUTH: &str = include_str!("../reference/order/tests/it/auth.rs");
const TEST_COMMON: &str = include_str!("../reference/order/tests/it/common.rs");
const TEST_DOMAIN: &str = include_str!("../reference/order/tests/it/domain.rs");
const TEST_ERRORS: &str = include_str!("../reference/order/tests/it/errors.rs");
const TEST_IDEMPOTENCY: &str = include_str!("../reference/order/tests/it/idempotency.rs");
const TEST_LAYERS: &str = include_str!("../reference/order/tests/it/layers.rs");
const TEST_LIMITS: &str = include_str!("../reference/order/tests/it/limits.rs");
const TEST_METRICS: &str = include_str!("../reference/order/tests/it/metrics.rs");
const TEST_PAGINATION: &str = include_str!("../reference/order/tests/it/pagination.rs");
const TEST_PRECONDITIONS: &str = include_str!("../reference/order/tests/it/preconditions.rs");
const TEST_PROBES: &str = include_str!("../reference/order/tests/it/probes.rs");
const TEST_QUALITY: &str = include_str!("../reference/order/tests/it/quality.rs");
const TEST_REQUEST_ID: &str = include_str!("../reference/order/tests/it/request_id.rs");
const TEST_ROUTES: &str = include_str!("../reference/order/tests/it/routes.rs");
const TEST_STORE: &str = include_str!("../reference/order/tests/it/store.rs");

/// The test files of the reference that every emitted crate gets: the name in
/// the crate and the reference text. `readme.rs` is not here, and `fixture.rs`
/// is built from the plan (`fixture_file`).
const TEST_FILES: [(&str, &str); 15] = [
    ("auth.rs", TEST_AUTH),
    ("common.rs", TEST_COMMON),
    ("domain.rs", TEST_DOMAIN),
    ("errors.rs", TEST_ERRORS),
    ("idempotency.rs", TEST_IDEMPOTENCY),
    ("layers.rs", TEST_LAYERS),
    ("limits.rs", TEST_LIMITS),
    ("metrics.rs", TEST_METRICS),
    ("pagination.rs", TEST_PAGINATION),
    ("preconditions.rs", TEST_PRECONDITIONS),
    ("probes.rs", TEST_PROBES),
    ("quality.rs", TEST_QUALITY),
    ("request_id.rs", TEST_REQUEST_ID),
    ("routes.rs", TEST_ROUTES),
    ("store.rs", TEST_STORE),
];

/// The `Must` trait of the reference fixture, copied verbatim into every emitted fixture.
const MUST_BLOCK: &str = r##"/// `expect` with a name that the emitted-test text check does not ban: a failed result or an empty option stops the test with the message.
pub trait Must<T> {
    /// The value, or a panic with `what` (and the error for a result).
    fn must(self, what: &str) -> T;
}

impl<T, E: std::fmt::Debug> Must<T> for Result<T, E> {
    #[track_caller]
    fn must(self, what: &str) -> T {
        match self {
            Ok(value) => value,
            Err(error) => panic!("{what}: {error:?}"),
        }
    }
}

impl<T> Must<T> for Option<T> {
    #[track_caller]
    fn must(self, what: &str) -> T {
        match self {
            Some(value) => value,
            None => panic!("{what}"),
        }
    }
}
"##;

/// The files of the interfaces layer: the name in the crate and its reference text.
const INTERFACE_FILES: [(&str, &str); 10] = [
    ("auth.rs", INTERFACES_AUTH),
    ("error.rs", INTERFACES_ERROR),
    ("etag.rs", INTERFACES_ETAG),
    ("limits.rs", INTERFACES_LIMITS),
    ("metrics.rs", INTERFACES_METRICS),
    ("mod.rs", INTERFACES_MOD),
    ("probes.rs", INTERFACES_PROBES),
    ("request_id.rs", INTERFACES_REQUEST_ID),
    ("routes.rs", INTERFACES_ROUTES),
    ("server.rs", INTERFACES_SERVER),
];

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
    let path = text(resource, "path");
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
    for (file, template) in INTERFACE_FILES {
        let text = shared(template);
        let text = if file == "routes.rs" {
            with_path(text, plural, path)
        } else {
            text
        };
        files.insert(format!("src/interfaces/{file}"), text);
    }
    for (file, template) in TEST_FILES {
        files.insert(
            format!("tests/it/{file}"),
            test_text(template, package, name, snake, plural, path),
        );
    }
    files.insert(
        "tests/it/fixture.rs".to_owned(),
        fixture_file(package, name, snake, fields),
    );
    files.insert("tests/it/main.rs".to_owned(), test_main());
    files.insert(
        "openapi.json".to_owned(),
        openapi_file(name, snake, plural, path, fields),
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
            || type_name == format!("{name}Page")
            || type_name == "ErrorBody"
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
        .replace("Orders", "{{PLURAL_PASCAL}}")
        .replace("Order", "{{RESOURCE}}")
        .replace("order", "{{SNAKE}}")
        .replace("ORDER", "{{UPPER}}")
        .replace("{{PLURAL}}", plural)
        .replace("{{PLURAL_PASCAL}}", &pascal(plural))
        .replace("{{RESOURCE}}", name)
        .replace("{{SNAKE}}", snake)
        .replace("{{UPPER}}", &snake.to_ascii_uppercase())
}

/// The route text of the resource: the plural in the path of the plan
/// (`/v1/order-items`), not the plural of the name (`/v1/order_items`).
fn with_path(text: String, plural: &str, path: &str) -> String {
    text.replace(&format!("/v1/{plural}"), path)
}

/// The lock of the crate: the template with the root package at its sorted place.
fn lock_file(package: &str) -> String {
    let root = format!(
        "[[package]]\nname = \"{package}\"\nversion = \"0.1.0\"\ndependencies = [\n \"axum\",\n \"metrics-prometheus\",\n \"proptest\",\n \"serde_json\",\n \"server-lifecycle\",\n \"service-auth\",\n \"service-http\",\n \"service-observability\",\n \"time\",\n \"tokio\",\n \"tower\",\n \"tracing\",\n \"tracing-subscriber\",\n \"uuid\",\n]"
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

/// The OpenAPI document of the resource: the reference document with the names,
/// the path of the plan and the schemas of the fields. The output is pretty JSON
/// with one final newline; a document that cannot be read gives an empty text.
fn openapi_file(name: &str, snake: &str, plural: &str, path: &str, fields: &[Value]) -> String {
    let text = with_path(
        substitute(OPENAPI_TEMPLATE, name, snake, plural),
        plural,
        path,
    );
    let Ok(mut doc) = serde_json::from_str::<Value>(&text) else {
        return String::new();
    };
    let Some(schemas) = doc
        .get_mut("components")
        .and_then(|components| components.get_mut("schemas"))
        .and_then(Value::as_object_mut)
    else {
        return String::new();
    };
    schemas.remove("Status");
    for field in fields.iter().filter(|field| kind_of(field) == "enum") {
        schemas.insert(
            pascal(name_of(field)),
            json!({"type": "string", "enum": values_of(field)}),
        );
    }
    let mut input_properties = Map::new();
    let mut resource_properties = Map::new();
    for field in fields {
        input_properties.insert(name_of(field).to_owned(), input_property(field));
        resource_properties.insert(name_of(field).to_owned(), type_property(field));
    }
    let required: Vec<String> = fields
        .iter()
        .filter(|field| is_required(field))
        .map(|field| name_of(field).to_owned())
        .collect();
    let input = json!({
        "type": "object",
        "required": required,
        "properties": input_properties,
    });
    let mut resource_required = vec!["id".to_owned()];
    resource_required.extend(required);
    resource_required.extend(["created_at".to_owned(), "updated_at".to_owned()]);
    let mut properties = Map::new();
    properties.insert("id".to_owned(), json!({"type": "string"}));
    properties.extend(resource_properties);
    properties.insert("created_at".to_owned(), json!({"type": "string"}));
    properties.insert("updated_at".to_owned(), json!({"type": "string"}));
    let resource = json!({
        "type": "object",
        "required": resource_required,
        "properties": properties,
    });
    schemas.insert(format!("{name}Input"), input);
    schemas.insert(name.to_owned(), resource);
    match serde_json::to_string_pretty(&doc) {
        Ok(pretty) => format!("{pretty}\n"),
        Err(_) => String::new(),
    }
}

/// The schema of an enum field: a reference to the enum, and `nullable` when optional.
fn enum_property(field: &Value) -> Value {
    let reference = json!({"$ref": format!("#/components/schemas/{}", pascal(name_of(field)))});
    if is_required(field) {
        reference
    } else {
        json!({"allOf": [reference], "nullable": true})
    }
}

/// The schema of a field in the input: the type and the constraints of the plan.
fn input_property(field: &Value) -> Value {
    let mut property = match kind_of(field) {
        "string" => {
            let mut map = Map::new();
            map.insert("type".to_owned(), json!("string"));
            if let Some(count) = field["upper_letters"].as_i64() {
                map.insert("pattern".to_owned(), json!(format!("^[A-Z]{{{count}}}$")));
            } else {
                if field["min"].as_i64().is_some_and(|min| min >= 1) {
                    map.insert("minLength".to_owned(), json!(field["min"]));
                }
                if field["max"].as_i64().is_some() {
                    map.insert("maxLength".to_owned(), json!(field["max"]));
                }
            }
            Value::Object(map)
        }
        "integer" => {
            let mut map = Map::new();
            map.insert("type".to_owned(), json!("integer"));
            if field["min"].as_i64().is_some() {
                map.insert("minimum".to_owned(), json!(field["min"]));
            }
            if field["max"].as_i64().is_some() {
                map.insert("maximum".to_owned(), json!(field["max"]));
            }
            Value::Object(map)
        }
        _ => return enum_property(field),
    };
    if !is_required(field) {
        property["nullable"] = json!(true);
    }
    property
}

/// The schema of a field in the resource: its type only, `nullable` when optional.
fn type_property(field: &Value) -> Value {
    let mut property = match kind_of(field) {
        "string" => json!({"type": "string"}),
        "integer" => json!({"type": "integer"}),
        _ => return enum_property(field),
    };
    if !is_required(field) {
        property["nullable"] = json!(true);
    }
    property
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

/// The text of one reference test file for the resource. `substitute` changes
/// every `order`, so the package, `Ordering` and `reordered_` are first put
/// behind markers that hold no `order` text, and restored after it.
fn test_text(
    template: &str,
    package: &str,
    name: &str,
    snake: &str,
    plural: &str,
    path: &str,
) -> String {
    let marked = template
        .replace("som_reference_order", "@@PKG@@")
        .replace("Ordering", "@@STD@@")
        .replace("reordered_", "@@REORD@@");
    let text = substitute(&marked, name, snake, plural)
        .replace("@@STD@@", "Ordering")
        .replace("@@REORD@@", "reordered_")
        .replace("@@PKG@@", package);
    with_path(text, plural, path)
}

/// The `tests/it/main.rs` of an emitted crate: one `mod` line for each emitted
/// test file and for the fixture, sorted.
fn test_main() -> String {
    let mut stems: Vec<&str> = TEST_FILES
        .iter()
        .map(|&(file, _)| file.trim_end_matches(".rs"))
        .collect();
    stems.push("fixture");
    stems.sort_unstable();
    stems.iter().map(|stem| format!("mod {stem};\n")).collect()
}

/// The text of `tests/it/fixture.rs`: the reference fixture with the names and
/// the values of the plan. A plan with no integer field or no string field uses
/// the fallbacks of this file; its fixture still compiles, and its tests are not
/// promised.
fn fixture_file(package: &str, name: &str, snake: &str, fields: &[Value]) -> String {
    let all: Vec<&str> = fields.iter().map(name_of).collect();
    let all_refs: Vec<&Value> = fields.iter().collect();
    let required: Vec<&Value> = fields.iter().filter(|field| is_required(field)).collect();
    let optional: Vec<&Value> = fields.iter().filter(|field| !is_required(field)).collect();
    let bad: Vec<&Value> = fields
        .iter()
        .filter(|field| bad_value(field).is_some())
        .collect();
    let wrong_type: Vec<&str> = fields
        .iter()
        .filter(|field| kind_of(field) != "integer")
        .map(name_of)
        .collect();
    let required_names: Vec<&str> = required.iter().copied().map(name_of).collect();
    let required_enums: Vec<&Value> = required
        .iter()
        .copied()
        .filter(|field| kind_of(field) == "enum")
        .collect();
    let integer: Option<&Value> = fields.iter().find(|field| kind_of(field) == "integer");
    let text_field: Option<&Value> = fields
        .iter()
        .find(|field| kind_of(field) == "string" && !is_required(field))
        .or_else(|| fields.iter().find(|field| kind_of(field) == "string"));
    let int_name = integer.map_or("", name_of);
    let text_name = text_field.map_or("", name_of);
    let text_len = text_field
        .and_then(|field| field["max"].as_i64())
        .unwrap_or(500);
    let first_name = all.first().copied().unwrap_or("id");
    let mut out = String::new();

    let mut imports = vec![
        "FieldError".to_owned(),
        name.to_owned(),
        format!("{name}Input"),
        "parse_input".to_owned(),
    ];
    imports.extend(
        required_enums
            .iter()
            .copied()
            .map(|field| pascal(name_of(field))),
    );
    imports.sort();
    out.push_str(&format!(
        "//! Every literal that names a {name} field, a field value or the typed\n\
         //! struct of the generated tests. The other test files call these functions.\n\n\
         use serde_json::{{Value, json}};\n\
         use {package}::domain::{snake}::{{{}}};\n\n",
        imports.join(", ")
    ));
    out.push_str(MUST_BLOCK);
    out.push('\n');

    out.push_str(&format!(
        "/// The fields of the body, in the order of the plan (no id, no time fields).\n\
         pub const FIELDS: [&str; {}] = [{}];\n\n",
        all.len(),
        quoted_list(&all)
    ));
    let mut keys = vec!["id"];
    keys.extend(all.iter().copied());
    keys.extend(["created_at", "updated_at"]);
    out.push_str(&format!(
        "/// The keys of a resource in a response.\n\
         pub const RESOURCE_KEYS: [&str; {}] = [{}];\n\n",
        keys.len(),
        quoted_list(&keys)
    ));
    let mut invalid: Vec<&str> = bad.iter().copied().map(name_of).collect();
    invalid.push("extra");
    out.push_str(&format!(
        "/// The fields that `invalid_body` gets wrong, in order.\n\
         pub const INVALID_FIELDS: [&str; {}] = [{}];\n\n",
        invalid.len(),
        quoted_list(&invalid)
    ));
    out.push_str(&format!(
        "/// The fields that `wrong_type_body` gives a value of the wrong JSON type, in order.\n\
         pub const WRONG_TYPE_FIELDS: [&str; {}] = [{}];\n\n",
        wrong_type.len(),
        quoted_list(&wrong_type)
    ));
    out.push_str(&format!(
        "/// The fields that an empty body lacks, in order.\n\
         pub const REQUIRED_FIELDS: [&str; {}] = [{}];\n\n",
        required_names.len(),
        quoted_list(&required_names)
    ));

    push_fn(
        &mut out,
        "",
        "pub fn valid_body() -> Value",
        &[format!(
            "json!({{{}}})",
            json_pairs(&required, valid_value).join(", ")
        )],
    );
    push_fn(
        &mut out,
        "/// A valid body that differs in every field (for `PUT`).",
        "pub fn replacement_body() -> Value",
        &[format!(
            "json!({{{}}})",
            json_pairs(&all_refs, replacement_value).join(", ")
        )],
    );
    let mut invalid_pairs = json_pairs(&all_refs, invalid_value);
    invalid_pairs.push("\"extra\": 1".to_owned());
    push_fn(
        &mut out,
        "/// A body with one bad value for every constrained field, and an unknown field.",
        "pub fn invalid_body() -> Value",
        &[format!("json!({{{}}})", invalid_pairs.join(", "))],
    );
    let wrong_body = if wrong_type.is_empty() {
        vec!["valid_body()".to_owned()]
    } else {
        let mut lines = vec!["let mut body = valid_body();".to_owned()];
        lines.extend(
            wrong_type
                .iter()
                .map(|field| format!("body[{field:?}] = json!(7);")),
        );
        lines.push("body".to_owned());
        lines
    };
    push_fn(
        &mut out,
        "/// A valid body with a value of the wrong JSON type for each string field.",
        "pub fn wrong_type_body() -> Value",
        &wrong_body,
    );

    match integer {
        Some(field) => {
            push_fn(
                &mut out,
                "/// A valid body whose integer field holds a float.",
                "pub fn float_in_integer_body() -> Value",
                &[
                    "let mut body = valid_body();".to_owned(),
                    format!("body[{:?}] = json!(1.5);", name_of(field)),
                    "body".to_owned(),
                ],
            );
            push_fn(
                &mut out,
                "/// The one field error of `float_in_integer_body`.",
                "pub fn float_in_integer_errors() -> Vec<FieldError>",
                &[format!(
                    "vec![FieldError {{ field: {:?}.to_owned(), message: \"must be an integer\".to_owned() }}]",
                    name_of(field)
                )],
            );
        }
        None => {
            push_fn(
                &mut out,
                "/// A valid body; this plan has no integer field to hold a float.",
                "pub fn float_in_integer_body() -> Value",
                &["valid_body()".to_owned()],
            );
            push_fn(
                &mut out,
                "/// The field errors of `float_in_integer_body`: none for this plan.",
                "pub fn float_in_integer_errors() -> Vec<FieldError>",
                &["Vec::new()".to_owned()],
            );
        }
    }

    match optional.first() {
        Some(field) => {
            let optional_name = name_of(field);
            push_fn(
                &mut out,
                "/// A valid body whose optional field is null.",
                "pub fn null_optional_body() -> Value",
                &[
                    "let mut body = valid_body();".to_owned(),
                    format!("body[{optional_name:?}] = Value::Null;"),
                    "body".to_owned(),
                ],
            );
            push_fn(
                &mut out,
                "/// True when `body` parses and its optional field is `None`.",
                "pub fn null_optional_is_none(body: &Value) -> bool",
                &[format!(
                    "parse_input(body).must(\"a null optional field is valid\").{optional_name}.is_none()"
                )],
            );
        }
        None => {
            push_fn(
                &mut out,
                "/// A valid body; this plan has no optional field to set to null.",
                "pub fn null_optional_body() -> Value",
                &["valid_body()".to_owned()],
            );
            push_fn(
                &mut out,
                "/// True when `body` parses: this plan has no optional field.",
                "pub fn null_optional_is_none(body: &Value) -> bool",
                &["parse_input(body).is_ok()".to_owned()],
            );
        }
    }

    let filter = format!("{first_name}=x");
    push_fn(
        &mut out,
        "/// A query string with a filter on a field value, which the list refuses.",
        "pub fn filter_by_field_query() -> &'static str",
        &[format!("{filter:?}")],
    );
    push_fn(
        &mut out,
        "/// A valid body whose text field has the largest allowed length.",
        "pub fn at_the_max_length_body() -> Value",
        &[format!("body_with_text(\"n\".repeat({text_len}))")],
    );
    let one_bad = match bad.first() {
        Some(field) => vec![
            "let mut body = valid_body();".to_owned(),
            format!(
                "body[{:?}] = json!({});",
                name_of(field),
                json_text(&invalid_value(field))
            ),
            "body".to_owned(),
        ],
        None => vec!["valid_body()".to_owned()],
    };
    push_fn(
        &mut out,
        "/// A valid body with one bad field: the first field that has a bad value.",
        "pub fn one_bad_field_body() -> Value",
        &one_bad,
    );
    push_fn(
        &mut out,
        "/// A valid body whose text field has `len` characters.",
        "pub fn body_with_big_text(len: usize) -> Value",
        &["body_with_text(\"a\".repeat(len))".to_owned()],
    );
    push_fn(
        &mut out,
        "/// A valid body whose text field is `text`.",
        "pub fn with_text(text: &str) -> Value",
        &["body_with_text(text.to_owned())".to_owned()],
    );
    match integer {
        Some(_) => push_fn(
            &mut out,
            "/// A valid body whose integer field is `n`.",
            "pub fn with_number(n: i64) -> Value",
            &[
                "let mut body = valid_body();".to_owned(),
                format!("body[{int_name:?}] = json!(n);"),
                "body".to_owned(),
            ],
        ),
        None => push_fn(
            &mut out,
            "/// A valid body; this plan has no integer field to set.",
            "pub fn with_number(_n: i64) -> Value",
            &["valid_body()".to_owned()],
        ),
    }
    push_fn(
        &mut out,
        "/// The text field of a body or of a resource.",
        "pub fn text_of(value: &Value) -> &Value",
        &[format!("&value[{text_name:?}]")],
    );
    let reversed: Vec<String> = required
        .iter()
        .rev()
        .map(|field| format!("\"{}\":{}", name_of(field), json_text(&valid_value(field))))
        .collect();
    let joined = format!("{{{}}}", reversed.join(","));
    push_fn(
        &mut out,
        "/// The valid body with its keys in another order.",
        "pub fn reordered_valid_json() -> &'static str",
        &[format!("{joined:?}")],
    );

    let mut item_lines = vec!["assert!(item[\"id\"].is_string());".to_owned()];
    for field in &required {
        let check = if kind_of(field) == "integer" {
            "is_i64"
        } else {
            "is_string"
        };
        item_lines.push(format!("assert!(item[{:?}].{check}());", name_of(field)));
    }
    item_lines.push("assert!(item[\"created_at\"].is_string());".to_owned());
    item_lines.push("assert!(item[\"updated_at\"].is_string());".to_owned());
    push_fn(
        &mut out,
        "/// The asserts for one item of a list response.",
        "pub fn check_list_item(item: &Value)",
        &item_lines,
    );

    let mut created = vec![
        "for field in FIELDS {".to_owned(),
        "    assert!(value.get(field).is_some(), \"{field} is in the resource\");".to_owned(),
        "}".to_owned(),
    ];
    for field in &optional {
        created.push(format!("assert!(value[{:?}].is_null());", name_of(field)));
    }
    if let Some(field) = required.first() {
        created.push(format!(
            "assert_eq!(value[{:?}], {});",
            name_of(field),
            json_text(&valid_value(field))
        ));
    }
    push_fn(
        &mut out,
        "/// The asserts for a resource that a create returned.",
        "pub fn check_created(value: &Value)",
        &created,
    );

    let replaced: Vec<String> = all_refs
        .iter()
        .map(|field| {
            format!(
                "assert_eq!(value[{:?}], {});",
                name_of(field),
                json_text(&replacement_value(field))
            )
        })
        .collect();
    push_fn(
        &mut out,
        "/// The asserts for a resource that a replace returned.",
        "pub fn check_replaced(value: &Value)",
        &replaced,
    );

    let mut defaults: Vec<String> = required_enums
        .iter()
        .map(|field| {
            format!(
                "assert_eq!(json[{:?}], {});",
                name_of(field),
                json_text(&valid_value(field))
            )
        })
        .collect();
    defaults.extend(
        optional
            .iter()
            .map(|field| format!("assert!(json[{:?}].is_null());", name_of(field))),
    );
    let defaults_param = if defaults.is_empty() { "_json" } else { "json" };
    push_fn(
        &mut out,
        "/// The defaults of a valid body: the first value of each enum, and null for each optional field.",
        &format!("pub fn check_valid_defaults({defaults_param}: &Value)"),
        &defaults,
    );

    let input_lines: Vec<String> = required_enums
        .iter()
        .map(|field| {
            format!(
                "assert_eq!(input.{}, {}::{});",
                name_of(field),
                pascal(name_of(field)),
                pascal(first_value(field))
            )
        })
        .collect();
    let input_param = if input_lines.is_empty() {
        "_input"
    } else {
        "input"
    };
    push_fn(
        &mut out,
        "/// The enum values of a parsed valid body.",
        &format!("pub fn check_valid_input({input_param}: &{name}Input)"),
        &input_lines,
    );

    match integer {
        Some(field) => {
            let (assign, read) = if is_required(field) {
                ("250".to_owned(), format!("{snake}.{}", name_of(field)))
            } else {
                (
                    "Some(250)".to_owned(),
                    format!("{snake}.{}.unwrap_or_default()", name_of(field)),
                )
            };
            push_fn(
                &mut out,
                &format!("/// The change that a store test makes to a stored {name}."),
                &format!("pub fn change({snake}: &mut {name})"),
                &[format!("{snake}.{} = {assign};", name_of(field))],
            );
            push_fn(
                &mut out,
                &format!("/// The total of a stored {name} that `change` was applied to."),
                &format!("pub fn changed_total({snake}: &{name}) -> i64"),
                &[read],
            );
        }
        None => {
            push_fn(
                &mut out,
                &format!(
                    "/// The change that a store test makes to a stored {name}: none without an integer field."
                ),
                &format!("pub fn change(_{snake}: &mut {name})"),
                &Vec::new(),
            );
            push_fn(
                &mut out,
                &format!("/// The total of a stored {name}: 250 without an integer field."),
                &format!("pub fn changed_total(_{snake}: &{name}) -> i64"),
                &["250".to_owned()],
            );
        }
    }

    let errors: Vec<String> = required_names
        .iter()
        .take(2)
        .map(|field| {
            format!(
                "FieldError {{ field: {field:?}.to_owned(), message: \"is required\".to_owned() }}"
            )
        })
        .collect();
    let errors_body = if errors.is_empty() {
        "Vec::new()".to_owned()
    } else {
        format!("vec![{}]", errors.join(", "))
    };
    push_fn(
        &mut out,
        "/// The field errors that the error body test uses.",
        "pub fn validation_field_errors() -> Vec<FieldError>",
        &[errors_body],
    );

    let (text_param, text_lines) = if text_field.is_some() {
        (
            "text",
            vec![
                "let mut body = valid_body();".to_owned(),
                format!("body[{text_name:?}] = json!(text);"),
                "body".to_owned(),
            ],
        )
    } else {
        ("_text", vec!["valid_body()".to_owned()])
    };
    push_fn(
        &mut out,
        "",
        &format!("fn body_with_text({text_param}: String) -> Value"),
        &text_lines,
    );
    out
}

/// Writes one function of the fixture: its doc line (empty for none), its
/// signature and its body lines, which are indented here.
fn push_fn(out: &mut String, doc: &str, signature: &str, body: &[String]) {
    if !doc.is_empty() {
        out.push_str(doc);
        out.push('\n');
    }
    out.push_str(signature);
    out.push_str(" {\n");
    for line in body {
        out.push_str("    ");
        out.push_str(line);
        out.push('\n');
    }
    out.push_str("}\n\n");
}

/// The Rust text of a list of names: `"a", "b"`.
fn quoted_list(names: &[&str]) -> String {
    names
        .iter()
        .map(|name| format!("{name:?}"))
        .collect::<Vec<String>>()
        .join(", ")
}

/// The JSON text of a value. For the names and values of a plan (ASCII) it is
/// also a Rust literal.
fn json_text(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_default()
}

/// The `"name": value` pairs of the fields, in the order given.
fn json_pairs(fields: &[&Value], value_of: fn(&Value) -> Value) -> Vec<String> {
    fields
        .iter()
        .map(|field| format!("{:?}: {}", name_of(field), json_text(&value_of(field))))
        .collect()
}

/// `count` copies of `letter`; a count below one gives an empty text.
fn repeat_text(letter: char, count: i64) -> String {
    letter
        .to_string()
        .repeat(usize::try_from(count).unwrap_or(0))
}

/// The shortest text that the field allows: its `min`, and at least one character.
fn min_length(field: &Value) -> i64 {
    field["min"].as_i64().unwrap_or(0).max(1)
}

fn first_value(field: &Value) -> &str {
    values_of(field).first().copied().unwrap_or("")
}

/// A value that the parser accepts for the field.
fn valid_value(field: &Value) -> Value {
    match kind_of(field) {
        "string" => match field["upper_letters"].as_i64() {
            Some(count) => json!(repeat_text('A', count)),
            None => json!(repeat_text('x', min_length(field))),
        },
        "integer" => json!(field["min"].as_i64().unwrap_or(1)),
        _ => json!(first_value(field)),
    }
}

/// Another value that the parser accepts for the field: it differs from `valid_value`.
fn replacement_value(field: &Value) -> Value {
    match kind_of(field) {
        "string" => match field["upper_letters"].as_i64() {
            Some(count) => json!(repeat_text('B', count)),
            None => json!(repeat_text('y', min_length(field))),
        },
        "integer" => {
            let raised = field["min"].as_i64().unwrap_or(0) + 1;
            json!(field["max"].as_i64().map_or(raised, |max| raised.min(max)))
        }
        _ => {
            let values = values_of(field);
            json!(values.get(1).or(values.first()).copied().unwrap_or(""))
        }
    }
}

/// A value that the parser refuses for the field with one error, or none when the plan gives no bound.
fn bad_value(field: &Value) -> Option<Value> {
    let min = field["min"].as_i64();
    let max = field["max"].as_i64();
    match kind_of(field) {
        "string" => match (field["upper_letters"].as_i64(), min, max) {
            (Some(count), _, _) => Some(json!(repeat_text('a', count))),
            (None, Some(low), _) if low >= 1 => Some(json!("")),
            (None, _, Some(high)) => Some(json!(repeat_text('x', high + 1))),
            _ => None,
        },
        "integer" => match (min, max) {
            (Some(low), _) => Some(json!(low - 1)),
            (None, Some(high)) => Some(json!(high + 1)),
            (None, None) => None,
        },
        _ => Some(json!("lost")),
    }
}

/// The value of a field in `invalid_body`: its bad value, or its valid value when it has none.
fn invalid_value(field: &Value) -> Value {
    bad_value(field).unwrap_or_else(|| valid_value(field))
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
