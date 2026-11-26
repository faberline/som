//! Determinism of the CNL path and the typed JSON path: two runs of the same
//! input give byte-equal bundles and byte-equal file maps (issue #323).
use serde_json::{Value, json};
use som_service::{Error, assemble_resource, compile_cnl, compile_prompt_v3};
use std::collections::BTreeMap;

const ORDER_CNL: &str = include_str!("../../contract/order.resource.cnl");
const CUSTOMER_CNL: &str = include_str!("../../contract/customer.resource.cnl");
const ORDER_PROMPT: &str = include_str!("../../contract/order.resource.prompt.json");
const CUSTOMER_PROMPT: &str = include_str!("../../contract/customer.resource.prompt.json");

type Run = (String, BTreeMap<String, String>);

fn value(text: &str) -> Value {
    serde_json::from_str(text).expect("JSON fixture")
}

fn ok<T>(result: Result<T, Error>) -> T {
    match result {
        Ok(value) => value,
        Err(error) => panic!("unexpected error {}: {}", error.status, error.body),
    }
}

/// One run from CNL: the bundle as text and the file map.
fn run_cnl(source: &str) -> Run {
    let bundle = ok(compile_cnl(source)).bundle;
    let files = ok(assemble_resource(&bundle));
    (bundle.to_string(), files)
}

/// One run from typed JSON: the bundle as text and the file map.
fn run_json(prompt: &Value) -> Run {
    let bundle = ok(compile_prompt_v3(prompt));
    let files = ok(assemble_resource(&bundle));
    (bundle.to_string(), files)
}

fn assert_same(first: &Run, second: &Run, what: &str) {
    assert_eq!(first.0, second.0, "{what}: bundle text differs");
    assert_eq!(first.1.len(), second.1.len(), "{what}: file count differs");
    for (path, text) in &first.1 {
        let other = second
            .1
            .get(path)
            .unwrap_or_else(|| panic!("{what}: {path} missing in the second run"));
        assert_eq!(text.as_bytes(), other.as_bytes(), "{what}: {path} differs");
    }
}

#[test]
fn order_from_cnl_is_byte_equal_in_two_runs() {
    assert_same(&run_cnl(ORDER_CNL), &run_cnl(ORDER_CNL), "Order CNL");
}

#[test]
fn customer_from_cnl_is_byte_equal_in_two_runs() {
    assert_same(
        &run_cnl(CUSTOMER_CNL),
        &run_cnl(CUSTOMER_CNL),
        "Customer CNL",
    );
}

#[test]
fn order_from_typed_json_is_byte_equal_in_two_runs() {
    let prompt = value(ORDER_PROMPT);
    assert_same(&run_json(&prompt), &run_json(&prompt), "Order typed JSON");
}

#[test]
fn customer_from_typed_json_is_byte_equal_in_two_runs() {
    let prompt = value(CUSTOMER_PROMPT);
    assert_same(
        &run_json(&prompt),
        &run_json(&prompt),
        "Customer typed JSON",
    );
}

#[test]
fn cnl_and_typed_json_give_the_same_file_map() {
    assert_same(
        &run_cnl(ORDER_CNL),
        &run_json(&value(ORDER_PROMPT)),
        "Order CNL vs typed JSON",
    );
    assert_same(
        &run_cnl(CUSTOMER_CNL),
        &run_json(&value(CUSTOMER_PROMPT)),
        "Customer CNL vs typed JSON",
    );
}

#[test]
fn a_changed_field_gives_a_different_file_map() {
    let original = run_json(&value(ORDER_PROMPT));
    let mut changed = value(ORDER_PROMPT);
    let field = json!({"name": "determinism_probe", "type": "string", "required": false});
    changed["resources"][0]["fields"]
        .as_array_mut()
        .expect("fields array")
        .push(field);
    let changed = run_json(&changed);
    assert_ne!(
        changed.1, original.1,
        "one changed field must change the output"
    );
}

#[test]
fn the_generated_crates_have_line_counts_for_the_record() {
    for (name, cnl, prompt) in [
        ("Order", ORDER_CNL, ORDER_PROMPT),
        ("Customer", CUSTOMER_CNL, CUSTOMER_PROMPT),
    ] {
        let (_, files) = run_json(&value(prompt));
        let generated: usize = files.values().map(|text| text.lines().count()).sum();
        let cnl_lines = cnl.lines().count();
        println!(
            "{name}: {generated} generated lines in {} files, {cnl_lines} CNL lines",
            files.len()
        );
        assert!(generated > cnl_lines && cnl_lines > 0);
    }
}
