//! The resource emitter (#304): the first half of a crate for one resource
//! bundle. The shared layers are the reference files with the names of the
//! resource; the domain file, the store file and the use cases are built from
//! the checked plan.
use serde_json::{Value, json};
use som_service::{Error, assemble_json, assemble_resource, compile_prompt_v2, compile_prompt_v3};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::SystemTime;

pub(crate) const ORDER: &str = include_str!("../../contract/order.resource.prompt.json");
pub(crate) const CUSTOMER: &str = include_str!("../../contract/customer.resource.prompt.json");
const DEFER_FOUR: &str = include_str!("fixtures/defer-four.prompt.json");
const ORDER_CURSOR: &str = include_str!("fixtures/order.cursor.rs");
const LOCK_TEMPLATE: &str = include_str!("../../templates/resource/Cargo.lock.txt");
const REFERENCE_FIXTURE_TEST: &str = include_str!("../../reference/order/tests/it/fixture.rs");
const REFERENCE_DOMAIN_TEST: &str = include_str!("../../reference/order/tests/it/domain.rs");
const REFERENCE_STORE_TEST: &str = include_str!("../../reference/order/tests/it/store.rs");

pub(crate) fn value(text: &str) -> Value {
    serde_json::from_str(text).expect("JSON fixture")
}

fn ok<T>(result: Result<T, Error>) -> T {
    match result {
        Ok(value) => value,
        Err(error) => panic!("unexpected error {}: {}", error.status, error.body),
    }
}

/// Checks the status, the code and the pointer of the one diagnostic of a refusal.
fn refused_at<T>(result: Result<T, Error>, status: u16, code: &str, pointer: &str) {
    let error = match result {
        Ok(_) => panic!("expected {code} at {pointer}, but the request was accepted"),
        Err(error) => error,
    };
    assert_eq!(error.status, status, "{}", error.body);
    assert_eq!(error.body["code"], code, "{}", error.body);
    assert_eq!(
        error.body["diagnostics"][0]["prompt_pointer"], pointer,
        "{}",
        error.body
    );
}

fn bundle_of(prompt: &Value) -> Value {
    ok(compile_prompt_v3(prompt))
}

/// The files that the emitter gives for a prompt v3.
pub(crate) fn emit(prompt: &Value) -> BTreeMap<String, String> {
    ok(assemble_resource(&bundle_of(prompt)))
}

/// The Order request as a JSON object that a test can change in one place.
fn order_with(edit: impl FnOnce(&mut Value)) -> Value {
    let mut prompt = value(ORDER);
    edit(&mut prompt);
    prompt
}

pub(crate) fn resource_prompt(package: &str, name: &str, fields: Value) -> Value {
    json!({
        "version": "som.prompt.service.v3",
        "package": package,
        "target": "rust-resource-service.v1",
        "resources": [{"name": name, "fields": fields, "standards": ["CRUD", "standard pagination"]}]
    })
}

pub(crate) fn title_fields() -> Value {
    json!([{"name": "title", "type": "string", "required": true, "min": 1}])
}

fn zeros() -> String {
    "0".repeat(64)
}

/// The names of the blocks of a lock, in the order of the file (the header is left out).
fn lock_names(lock: &str) -> Vec<String> {
    lock.trim_end()
        .split("\n\n")
        .skip(1)
        .map(|block| {
            let line = block.lines().nth(1).expect("a name line");
            line.trim_start_matches("name = \"")
                .trim_end_matches('"')
                .to_owned()
        })
        .collect()
}

/// The lock without the block of `package`, joined as the template is joined.
fn without_package(lock: &str, package: &str) -> String {
    let name_line = format!("name = \"{package}\"");
    let blocks: Vec<&str> = lock
        .trim_end()
        .split("\n\n")
        .filter(|block| block.lines().nth(1) != Some(name_line.as_str()))
        .collect();
    blocks.join("\n\n")
}

#[test]
fn resource_emit_order_has_forty_files() {
    let files = emit(&value(ORDER));
    let keys: Vec<&str> = files.keys().map(String::as_str).collect();
    assert_eq!(
        keys,
        [
            "Cargo.lock",
            "Cargo.toml",
            "openapi.json",
            "src/application/idempotency.rs",
            "src/application/mod.rs",
            "src/application/orders.rs",
            "src/application/pagination.rs",
            "src/application/store.rs",
            "src/domain/mod.rs",
            "src/domain/order.rs",
            "src/infrastructure/memory_store.rs",
            "src/infrastructure/mod.rs",
            "src/interfaces/auth.rs",
            "src/interfaces/error.rs",
            "src/interfaces/etag.rs",
            "src/interfaces/limits.rs",
            "src/interfaces/metrics.rs",
            "src/interfaces/mod.rs",
            "src/interfaces/probes.rs",
            "src/interfaces/request_id.rs",
            "src/interfaces/routes.rs",
            "src/interfaces/server.rs",
            "src/lib.rs",
            "tests/it/auth.rs",
            "tests/it/common.rs",
            "tests/it/domain.rs",
            "tests/it/errors.rs",
            "tests/it/fixture.rs",
            "tests/it/idempotency.rs",
            "tests/it/layers.rs",
            "tests/it/limits.rs",
            "tests/it/main.rs",
            "tests/it/metrics.rs",
            "tests/it/pagination.rs",
            "tests/it/preconditions.rs",
            "tests/it/probes.rs",
            "tests/it/quality.rs",
            "tests/it/request_id.rs",
            "tests/it/routes.rs",
            "tests/it/store.rs",
        ]
    );
}

#[test]
fn resource_emit_shared_layers_equal_the_reference_for_order() {
    let files = emit(&value(ORDER));
    let shared: [(&str, &str); 9] = [
        (
            "src/lib.rs",
            include_str!("../../reference/order/src/lib.rs"),
        ),
        (
            "src/application/mod.rs",
            include_str!("../../reference/order/src/application/mod.rs"),
        ),
        (
            "src/application/store.rs",
            include_str!("../../reference/order/src/application/store.rs"),
        ),
        (
            "src/application/pagination.rs",
            include_str!("../../reference/order/src/application/pagination.rs"),
        ),
        (
            "src/application/idempotency.rs",
            include_str!("../../reference/order/src/application/idempotency.rs"),
        ),
        (
            "src/application/orders.rs",
            include_str!("../../reference/order/src/application/orders.rs"),
        ),
        (
            "src/domain/mod.rs",
            include_str!("../../reference/order/src/domain/mod.rs"),
        ),
        (
            "src/infrastructure/mod.rs",
            include_str!("../../reference/order/src/infrastructure/mod.rs"),
        ),
        (
            "src/infrastructure/memory_store.rs",
            include_str!("../../reference/order/src/infrastructure/memory_store.rs"),
        ),
    ];
    for (path, text) in shared {
        assert_eq!(files[path], text, "{path} differs from the reference");
    }
}

#[test]
fn resource_emit_customer_uses_its_own_names() {
    let files = emit(&value(CUSTOMER));
    for path in ["src/domain/customer.rs", "src/application/customers.rs"] {
        assert!(files.contains_key(path), "missing {path}");
    }
    assert!(files["src/application/store.rs"].contains("pub trait CustomerStore"));
    let customers = &files["src/application/customers.rs"];
    assert!(customers.contains("pub fn create_customer("));
    assert!(customers.contains("pub fn list_customers("));
    for (path, text) in &files {
        if path.starts_with("src/") {
            assert!(
                !text.to_lowercase().contains("order"),
                "{path} still names the order resource"
            );
        }
    }
    assert!(files["Cargo.toml"].starts_with("[package]\nname = \"customers_service\""));
}

#[test]
fn resource_emit_multiword_and_y_names() {
    let files = emit(&resource_prompt(
        "order_items_service",
        "OrderItem",
        title_fields(),
    ));
    assert!(files.contains_key("src/domain/order_item.rs"));
    assert!(files.contains_key("src/application/order_items.rs"));
    assert!(files["src/application/store.rs"].contains("OrderItemStore"));
    let items = &files["src/application/order_items.rs"];
    assert!(items.contains("create_order_item"));
    assert!(items.contains("list_order_items"));
    assert!(files["src/application/mod.rs"].contains("pub mod order_items;"));

    let files = emit(&resource_prompt(
        "categories_service",
        "Category",
        title_fields(),
    ));
    assert!(files.contains_key("src/application/categories.rs"));
    assert!(files["src/application/categories.rs"].contains("list_categories"));
}

#[test]
fn resource_emit_domain_fields_in_order() {
    let files = emit(&value(ORDER));
    let domain = &files["src/domain/order.rs"];
    let mut positions = Vec::new();
    for needle in [
        "object.get(\"customer_id\")",
        "object.get(\"total_cents\")",
        "object.get(\"currency\")",
        "object.get(\"status\")",
        "object.get(\"note\")",
        "\"unknown field\"",
    ] {
        positions.push(
            domain
                .find(needle)
                .unwrap_or_else(|| panic!("missing {needle}")),
        );
    }
    assert!(
        positions.windows(2).all(|pair| pair[0] < pair[1]),
        "fields out of order: {positions:?}"
    );
    for text in [
        "must be three upper-case letters",
        "must be 0 or more",
        "must be at most 500 characters",
        "must not be empty",
        "must be one of pending, paid, shipped, canceled",
    ] {
        assert!(domain.contains(text), "missing {text}");
    }
}

#[test]
fn resource_emit_customer_domain_constraints() {
    let files = emit(&value(CUSTOMER));
    let domain = &files["src/domain/customer.rs"];
    for text in [
        "if length < 1 {",
        "} else if length > 100 {",
        "must be at least 3 characters",
        "must be at most 254 characters",
        "pub enum Tier {",
        "Gold,",
        "must be one of standard, silver, gold",
        "must be 0 or more",
    ] {
        assert!(domain.contains(text), "missing {text}");
    }
}

#[test]
fn resource_emit_range_and_optional_forms() {
    let fields = json!([
        {"name": "a", "type": "integer", "required": true, "min": 1, "max": 9},
        {"name": "b", "type": "integer", "required": false, "max": 5},
        {"name": "c", "type": "enum", "required": false, "values": ["x_y", "z"]},
    ]);
    let files = emit(&resource_prompt("gauges_service", "Gauge", fields));
    let gauge = &files["src/domain/gauge.rs"];
    for text in [
        "(1..=9).contains(&number)",
        "must be from 1 to 9",
        "number <= 5",
        "must be 5 or less",
        "Some(Some(number))",
        "Some(Some(variant))",
        "XY,",
        ".map(|value| value.as_str())",
    ] {
        assert!(gauge.contains(text), "missing {text}");
    }
}

#[test]
fn resource_emit_has_no_unwrap_no_unsafe() {
    for prompt in [ORDER, CUSTOMER] {
        let files = emit(&value(prompt));
        for (path, text) in &files {
            if path.ends_with(".rs") {
                for banned in [".unwrap()", ".expect(", "unsafe"] {
                    assert!(!text.contains(banned), "{path} contains {banned}");
                }
            }
        }
        assert!(files["Cargo.toml"].contains("unsafe_code = \"forbid\""));
        assert!(files["Cargo.toml"].contains("unwrap_used = \"deny\""));
    }
}

#[test]
fn resource_emit_lock_root_is_sorted() {
    for package in [
        "customers_service",
        "aaa_service",
        "zzz_service",
        "orders_service",
    ] {
        let files = emit(&order_with(|prompt| prompt["package"] = json!(package)));
        let lock = &files["Cargo.lock"];
        let names = lock_names(lock);
        assert!(
            names.windows(2).all(|pair| pair[0] <= pair[1]),
            "{package}: the lock is not sorted"
        );
        assert_eq!(
            lock.matches("[[package]]").count(),
            LOCK_TEMPLATE.matches("[[package]]").count() + 1,
            "{package}"
        );
        assert_eq!(
            names.iter().filter(|name| name.as_str() == package).count(),
            1,
            "{package}: the root block is not present once"
        );
    }
    let files = emit(&value(ORDER));
    assert_eq!(
        without_package(&files["Cargo.lock"], "orders_service"),
        LOCK_TEMPLATE.trim_end()
    );
}

#[test]
fn resource_emit_is_deterministic() {
    assert_eq!(emit(&value(ORDER)), emit(&value(ORDER)));
    assert_eq!(emit(&value(CUSTOMER)), emit(&value(CUSTOMER)));
}

#[test]
fn resource_emit_json_emit_resource() {
    let bundle = bundle_of(&value(ORDER));
    let source = json!({"bundle": bundle, "emit": "resource"}).to_string();
    let out = ok(assemble_json(&source));
    assert_eq!(out["status"], "ok");
    assert!(out["files"]["src/domain/order.rs"].is_string());

    let web = json!({"bundle": bundle, "emit": "web"}).to_string();
    refused_at(assemble_json(&web), 400, "INVALID_SCHEMA", "/emit");

    let plain = json!({"bundle": bundle}).to_string();
    match assemble_json(&plain) {
        Ok(_) => panic!("a resource bundle without emit was accepted"),
        Err(error) => assert_eq!(error.body["code"], "INVALID_BUNDLE", "{}", error.body),
    }
}

#[test]
fn resource_emit_refuses_a_bad_bundle() {
    let bundle = bundle_of(&value(ORDER));

    let mut no_digest = bundle.clone();
    no_digest.as_object_mut().expect("object").remove("digest");
    refused_at(
        assemble_resource(&no_digest),
        400,
        "INVALID_BUNDLE",
        "/bundle",
    );

    let mut version = bundle.clone();
    version["version"] = json!("x");
    refused_at(
        assemble_resource(&version),
        400,
        "INVALID_BUNDLE",
        "/bundle/version",
    );

    let mut assets = bundle.clone();
    assets["assets"]["sha256"] = json!(zeros());
    refused_at(
        assemble_resource(&assets),
        400,
        "ASSET_HASH_MISMATCH",
        "/bundle/assets",
    );

    let mut catalog = bundle.clone();
    catalog["catalog"] = json!("x");
    refused_at(
        assemble_resource(&catalog),
        400,
        "INVALID_BUNDLE",
        "/bundle/catalog",
    );

    let mut prompt = bundle.clone();
    prompt["prompt"]["resources"][0]["fields"][0]["name"] = json!("type");
    refused_at(
        assemble_resource(&prompt),
        400,
        "INVALID_BUNDLE",
        "/bundle/prompt",
    );

    let mut dsl = bundle.clone();
    dsl["dsl"]["resources"][0]["path"] = json!("/v1/x");
    refused_at(
        assemble_resource(&dsl),
        400,
        "INVALID_BUNDLE",
        "/bundle/dsl",
    );

    let mut digest = bundle.clone();
    digest["digest"] = json!(zeros());
    refused_at(
        assemble_resource(&digest),
        400,
        "INVALID_BUNDLE",
        "/bundle/digest",
    );

    let defer = ok(compile_prompt_v2(&value(DEFER_FOUR)));
    refused_at(assemble_resource(&defer), 400, "INVALID_BUNDLE", "/bundle");

    let type_name =
        order_with(|prompt| prompt["resources"][0]["fields"][0]["name"] = json!("type"));
    refused_at(
        compile_prompt_v3(&type_name),
        400,
        "INVALID_VALUE",
        "/resources/0/fields/0/name",
    );
}

#[test]
fn resource_emit_refuses_names_that_break_the_crate() {
    let two_resources = json!({
        "version": "som.prompt.service.v3",
        "package": "orders_service",
        "target": "rust-resource-service.v1",
        "resources": [
            {"name": "Order", "fields": title_fields(), "standards": ["CRUD", "standard pagination"]},
            {"name": "Item", "fields": title_fields(), "standards": ["CRUD", "standard pagination"]},
        ]
    });
    let cases = [
        (
            resource_prompt("orders_service", "Type", title_fields()),
            "/bundle/prompt/resources/0/name",
        ),
        (
            resource_prompt("orders_service", "Page", title_fields()),
            "/bundle/prompt/resources/0/name",
        ),
        (
            resource_prompt("time", "Order", title_fields()),
            "/bundle/prompt/package",
        ),
        (two_resources, "/bundle/prompt/resources"),
        (
            resource_prompt(
                "orders_service",
                "Status",
                json!([{"name": "status", "type": "enum", "required": true, "values": ["a", "b"]}]),
            ),
            "/bundle/prompt/resources/0/fields/0/name",
        ),
        (
            resource_prompt(
                "orders_service",
                "Gauge",
                json!([{"name": "field_error", "type": "enum", "required": true, "values": ["a", "b"]}]),
            ),
            "/bundle/prompt/resources/0/fields/0/name",
        ),
        (
            resource_prompt(
                "orders_service",
                "Gauge",
                json!([
                    {"name": "a_b", "type": "enum", "required": true, "values": ["x", "z"]},
                    {"name": "a__b", "type": "enum", "required": true, "values": ["y", "w"]},
                ]),
            ),
            "/bundle/prompt/resources/0/fields/1/name",
        ),
        (
            resource_prompt(
                "orders_service",
                "Gauge",
                json!([{"name": "state", "type": "enum", "required": true, "values": ["a_b", "a__b"]}]),
            ),
            "/bundle/prompt/resources/0/fields/0/values",
        ),
    ];
    for (prompt, pointer) in cases {
        let bundle = bundle_of(&prompt);
        refused_at(assemble_resource(&bundle), 400, "INVALID_VALUE", pointer);
    }
}

/// A scratch directory of one test run: `SOM_CLIENT_TEST_ROOT` or the temp dir.
pub(crate) fn scratch_root() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .expect("the clock is after 1970")
        .as_nanos();
    let scratch = std::env::var_os("SOM_CLIENT_TEST_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    scratch.join(format!("som-resource-emit-{}-{nonce}", std::process::id()))
}

pub(crate) fn target_dir(root: &Path) -> PathBuf {
    std::env::var_os("SOM_CLIENT_TEST_TARGET")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("target"))
}

pub(crate) fn write_files(root: &Path, files: &BTreeMap<String, String>) {
    for (path, text) in files {
        let target = root.join(path);
        fs::create_dir_all(target.parent().expect("a file has a parent"))
            .expect("create a directory");
        fs::write(&target, text).expect("write a file");
    }
}

/// Runs cargo in `root` and fails with stdout and stderr when it does not succeed.
pub(crate) fn cargo(root: &Path, target: &Path, args: &[&str]) -> Output {
    let output = Command::new(std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into()))
        .current_dir(root)
        .env("CARGO_TARGET_DIR", target)
        .env_remove("RUSTFLAGS")
        .env_remove("CARGO_ENCODED_RUSTFLAGS")
        .env_remove("LLVM_PROFILE_FILE")
        .args(args)
        .output()
        .expect("cargo starts");
    assert!(
        output.status.success(),
        "cargo {} failed in {}\nstdout:\n{}\nstderr:\n{}",
        args.join(" "),
        root.display(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    output
}

#[test]
fn resource_emit_order_builds_clippy_and_passes_reference_tests() {
    let root = scratch_root();
    let order = root.join("order");
    write_files(&order, &emit(&value(ORDER)));
    let mut tests = BTreeMap::new();
    tests.insert(
        "tests/it/main.rs".to_owned(),
        "#[allow(dead_code)]\nmod fixture;\nmod domain;\nmod pagination;\nmod store;\n".to_owned(),
    );
    tests.insert(
        "tests/it/fixture.rs".to_owned(),
        REFERENCE_FIXTURE_TEST.replace("som_reference_order", "orders_service"),
    );
    tests.insert(
        "tests/it/domain.rs".to_owned(),
        REFERENCE_DOMAIN_TEST.replace("som_reference_order", "orders_service"),
    );
    tests.insert(
        "tests/it/store.rs".to_owned(),
        REFERENCE_STORE_TEST.replace("som_reference_order", "orders_service"),
    );
    tests.insert("tests/it/pagination.rs".to_owned(), ORDER_CURSOR.to_owned());
    write_files(&order, &tests);

    let target = target_dir(&root);
    cargo(
        &order,
        &target,
        &[
            "clippy",
            "--locked",
            "--offline",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
    );
    let output = cargo(&order, &target, &["test", "--locked", "--offline"]);
    let stdout = String::from_utf8_lossy(&output.stdout);
    for name in [
        "domain::id_is_server_made_uuidv7_and_client_id_is_refused",
        "domain::time_fields_are_rfc3339_utc_and_set_by_server",
        "domain::unknown_field_is_refused_and_bad_fields_give_422_with_one_entry_each",
        "store::store_trait_has_in_memory_implementation",
        "store::list_page_orders_by_created_at_desc_then_id_and_resumes_after_cursor",
        "pagination::decode_cursor_refuses_odd_length_and_empty_parts",
        "pagination::list_orders_full_last_page_has_no_cursor",
        "pagination::pagination_walk_gives_all_items_once",
    ] {
        let line = format!("test {name} ... ok");
        assert!(
            stdout.lines().any(|found| found == line),
            "missing `{line}` in:\n{stdout}"
        );
    }
}

#[test]
fn resource_emit_customer_builds_and_passes_clippy() {
    let root = scratch_root();
    let customer = root.join("customer");
    write_files(&customer, &emit(&value(CUSTOMER)));
    let target = target_dir(&root);
    cargo(
        &customer,
        &target,
        &[
            "clippy",
            "--locked",
            "--offline",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
    );
    cargo(&customer, &target, &["build", "--locked", "--offline"]);
}

/// The `operationId` values of every operation of an OpenAPI document, sorted.
fn operation_ids(doc: &Value) -> Vec<String> {
    let mut ids = Vec::new();
    for item in doc["paths"].as_object().expect("paths").values() {
        for operation in item.as_object().expect("a path item").values() {
            if let Some(id) = operation["operationId"].as_str() {
                ids.push(id.to_owned());
            }
        }
    }
    ids.sort();
    ids
}

#[test]
fn resource_emit_interfaces_equal_the_reference_for_order() {
    let files = emit(&value(ORDER));
    let shared: [(&str, &str); 10] = [
        (
            "src/interfaces/auth.rs",
            include_str!("../../reference/order/src/interfaces/auth.rs"),
        ),
        (
            "src/interfaces/error.rs",
            include_str!("../../reference/order/src/interfaces/error.rs"),
        ),
        (
            "src/interfaces/etag.rs",
            include_str!("../../reference/order/src/interfaces/etag.rs"),
        ),
        (
            "src/interfaces/limits.rs",
            include_str!("../../reference/order/src/interfaces/limits.rs"),
        ),
        (
            "src/interfaces/metrics.rs",
            include_str!("../../reference/order/src/interfaces/metrics.rs"),
        ),
        (
            "src/interfaces/mod.rs",
            include_str!("../../reference/order/src/interfaces/mod.rs"),
        ),
        (
            "src/interfaces/probes.rs",
            include_str!("../../reference/order/src/interfaces/probes.rs"),
        ),
        (
            "src/interfaces/request_id.rs",
            include_str!("../../reference/order/src/interfaces/request_id.rs"),
        ),
        (
            "src/interfaces/routes.rs",
            include_str!("../../reference/order/src/interfaces/routes.rs"),
        ),
        (
            "src/interfaces/server.rs",
            include_str!("../../reference/order/src/interfaces/server.rs"),
        ),
    ];
    for (path, text) in shared {
        assert_eq!(files[path], text, "{path} differs from the reference");
    }

    let customer = emit(&value(CUSTOMER));
    let limits = &customer["src/interfaces/limits.rs"];
    assert!(limits.contains("CUSTOMER"));
    assert!(!limits.contains("ORDER"));
    let routes = &customer["src/interfaces/routes.rs"];
    assert!(routes.contains("pub const COLLECTION_PATH: &str = \"/v1/customers\";"));
    assert!(routes.contains("CustomerStore"));
    assert!(customer["src/interfaces/metrics.rs"].contains("customer_operation_requests_total"));
}

#[test]
fn resource_emit_openapi_equals_the_reference_for_order() {
    let files = emit(&value(ORDER));
    let text = &files["openapi.json"];
    let reference: Value = value(include_str!("../../reference/order/openapi.json"));
    assert_eq!(value(text), reference);
    assert!(text.ends_with("}\n"));
    assert!(!text.ends_with("}\n\n"));
}

#[test]
fn resource_emit_openapi_for_customer() {
    let files = emit(&value(CUSTOMER));
    let doc = value(&files["openapi.json"]);
    assert_eq!(doc["info"]["title"], "Customer API");
    let paths = doc["paths"].as_object().expect("paths");
    assert!(paths.contains_key("/v1/customers"));
    assert!(paths.contains_key("/v1/customers/{id}"));
    assert_eq!(
        operation_ids(&doc),
        [
            "createCustomer",
            "deleteCustomer",
            "getCustomer",
            "listCustomers",
            "replaceCustomer",
        ]
    );
    let schemas = &doc["components"]["schemas"];
    assert_eq!(
        schemas["Tier"]["enum"],
        json!(["standard", "silver", "gold"])
    );
    for key in ["CustomerInput", "Customer", "CustomerPage", "ErrorBody"] {
        assert!(schemas.get(key).is_some(), "missing schema {key}");
    }
    assert!(schemas.get("Status").is_none());
    let input = &schemas["CustomerInput"];
    assert_eq!(input["properties"]["country"]["pattern"], "^[A-Z]{3}$");
    assert_eq!(
        input["properties"]["name"],
        json!({"type": "string", "minLength": 1, "maxLength": 100})
    );
    assert_eq!(
        input["required"],
        json!(["name", "email", "country", "tier", "loyalty_points"])
    );
    assert_eq!(input["properties"]["note"]["nullable"], true);
    let required = schemas["Customer"]["required"]
        .as_array()
        .expect("required");
    assert_eq!(required.first(), Some(&json!("id")));
    assert_eq!(required.last(), Some(&json!("updated_at")));
}

#[test]
fn resource_emit_openapi_and_routes_use_the_dsl_path() {
    let files = emit(&resource_prompt(
        "order_items_service",
        "OrderItem",
        title_fields(),
    ));
    let routes = &files["src/interfaces/routes.rs"];
    assert!(routes.contains("\"/v1/order-items\""));
    assert!(!routes.contains("\"/v1/order_items\""));
    let doc = value(&files["openapi.json"]);
    assert!(doc["paths"].get("/v1/order-items").is_some());
    assert!(doc["paths"].get("/v1/order_items").is_none());
    assert!(operation_ids(&doc).contains(&"listOrderItems".to_owned()));

    let files = emit(&resource_prompt(
        "categories_service",
        "Category",
        title_fields(),
    ));
    let doc = value(&files["openapi.json"]);
    assert!(doc["paths"].get("/v1/categories").is_some());
    assert!(operation_ids(&doc).contains(&"listCategories".to_owned()));
}

#[test]
fn resource_emit_openapi_optional_enum_uses_all_of() {
    let fields = json!([{"name": "kind", "type": "enum", "required": false, "values": ["x", "y"]}]);
    let files = emit(&resource_prompt("customers_service", "Customer", fields));
    let doc = value(&files["openapi.json"]);
    assert_eq!(
        doc["components"]["schemas"]["CustomerInput"]["properties"]["kind"],
        json!({"allOf": [{"$ref": "#/components/schemas/Kind"}], "nullable": true})
    );
}

#[test]
fn resource_emit_refuses_enum_names_of_the_openapi_file() {
    let cases = [
        (
            resource_prompt(
                "customers_service",
                "Customer",
                json!([{"name": "error_body", "type": "enum", "required": true, "values": ["a", "b"]}]),
            ),
            "/bundle/prompt/resources/0/fields/0/name",
        ),
        (
            resource_prompt(
                "orders_service",
                "Order",
                json!([{"name": "order_page", "type": "enum", "required": true, "values": ["a", "b"]}]),
            ),
            "/bundle/prompt/resources/0/fields/0/name",
        ),
    ];
    for (prompt, pointer) in cases {
        let bundle = bundle_of(&prompt);
        refused_at(assemble_resource(&bundle), 400, "INVALID_VALUE", pointer);
    }
}

#[test]
fn resource_emit_cargo_toml_has_the_reference_dependencies() {
    let files = emit(&value(ORDER));
    let expected = include_str!("../../reference/order/Cargo.toml").replace(
        "name = \"som-reference-order\"",
        "name = \"orders_service\"",
    );
    assert_eq!(files["Cargo.toml"], expected);
}

#[test]
fn resource_emit_order_passes_the_reference_http_tests() {
    let root = scratch_root();
    let order = root.join("order");
    write_files(&order, &emit(&value(ORDER)));
    let reference: [(&str, &str); 14] = [
        (
            "common.rs",
            include_str!("../../reference/order/tests/it/common.rs"),
        ),
        (
            "fixture.rs",
            include_str!("../../reference/order/tests/it/fixture.rs"),
        ),
        (
            "domain.rs",
            include_str!("../../reference/order/tests/it/domain.rs"),
        ),
        (
            "store.rs",
            include_str!("../../reference/order/tests/it/store.rs"),
        ),
        (
            "pagination.rs",
            include_str!("../../reference/order/tests/it/pagination.rs"),
        ),
        (
            "routes.rs",
            include_str!("../../reference/order/tests/it/routes.rs"),
        ),
        (
            "auth.rs",
            include_str!("../../reference/order/tests/it/auth.rs"),
        ),
        (
            "errors.rs",
            include_str!("../../reference/order/tests/it/errors.rs"),
        ),
        (
            "preconditions.rs",
            include_str!("../../reference/order/tests/it/preconditions.rs"),
        ),
        (
            "idempotency.rs",
            include_str!("../../reference/order/tests/it/idempotency.rs"),
        ),
        (
            "limits.rs",
            include_str!("../../reference/order/tests/it/limits.rs"),
        ),
        (
            "metrics.rs",
            include_str!("../../reference/order/tests/it/metrics.rs"),
        ),
        (
            "probes.rs",
            include_str!("../../reference/order/tests/it/probes.rs"),
        ),
        (
            "request_id.rs",
            include_str!("../../reference/order/tests/it/request_id.rs"),
        ),
    ];
    let mut tests = BTreeMap::new();
    let mut main = String::new();
    for (name, text) in reference {
        tests.insert(
            format!("tests/it/{name}"),
            text.replace("som_reference_order", "orders_service"),
        );
        main.push_str(&format!("mod {};\n", name.trim_end_matches(".rs")));
    }
    tests.insert("tests/it/main.rs".to_owned(), main);
    write_files(&order, &tests);

    let target = target_dir(&root);
    cargo(
        &order,
        &target,
        &[
            "clippy",
            "--locked",
            "--offline",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
    );
    let output = cargo(&order, &target, &["test", "--locked", "--offline"]);
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(!stdout.contains("FAILED"), "{stdout}");
    assert!(stdout.contains("0 failed"), "{stdout}");
    for name in [
        "routes::create_returns_201_location_and_full_resource",
        "preconditions::etag_is_returned_and_if_match_mismatch_412_missing_428",
        "routes::body_over_1_mib_returns_413",
        "probes::openapi_json_is_served_and_equals_the_file",
        "idempotency::idempotency_key_same_body_same_result_different_body_409_and_expires_after_24h",
        "pagination::pagination_walk_gives_all_items_once",
    ] {
        let line = format!("test {name} ... ok");
        assert!(
            stdout.lines().any(|found| found == line),
            "missing `{line}` in:\n{stdout}"
        );
    }
}

#[test]
fn resource_emit_customer_builds_with_interfaces() {
    let root = scratch_root();
    let customer = root.join("customer");
    write_files(&customer, &emit(&value(CUSTOMER)));
    let target = target_dir(&root);
    cargo(
        &customer,
        &target,
        &[
            "clippy",
            "--locked",
            "--offline",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
    );
    cargo(&customer, &target, &["build", "--locked", "--offline"]);
    assert!(customer.join("openapi.json").is_file());
}
