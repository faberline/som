use serde_json::Value;
use som::{assemble, load_blocks, resolve_prompt_refs, validate_prompt};
use std::fs;
use std::path::PathBuf;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

#[test]
fn python_and_rust_render_the_same_bytes() {
    let dsl: Value = serde_json::from_str(
        &fs::read_to_string(root().join("tests/fixtures/crud.dsl.json")).unwrap(),
    )
    .unwrap();
    let expected: Value = serde_json::from_str(
        &fs::read_to_string(root().join("tests/fixtures/crud.expected.json")).unwrap(),
    )
    .unwrap();
    let blocks =
        load_blocks(&root().join("../som-code-python/data/blocks/v1/blocks.json")).unwrap();
    let actual = assemble(&dsl, &blocks).unwrap();
    for (path, source) in actual {
        assert_eq!(source, expected[&path].as_str().unwrap(), "file {path}");
    }
}

#[test]
fn raw_source_and_unknown_block_are_refused() {
    let mut dsl: Value = serde_json::from_str(
        &fs::read_to_string(root().join("tests/fixtures/crud.dsl.json")).unwrap(),
    )
    .unwrap();
    let blocks =
        load_blocks(&root().join("../som-code-python/data/blocks/v1/blocks.json")).unwrap();
    dsl["files"][0]["blocks"][0]["source"] = Value::String("print('x')".into());
    assert_eq!(assemble(&dsl, &blocks).unwrap_err().code, "INVALID_SCHEMA");
    dsl["files"][0]["blocks"][0]
        .as_object_mut()
        .unwrap()
        .remove("source");
    dsl["files"][0]["blocks"][0]["id"] = Value::String("unknown".into());
    assert_eq!(
        assemble(&dsl, &blocks).unwrap_err().code,
        "UNSUPPORTED_BLOCK"
    );
}

#[test]
fn fifty_frozen_combinations_match_python_bytes() {
    let input = fs::read_to_string(root().join("../som-core/data/v1/heldout.jsonl")).unwrap();
    let output =
        fs::read_to_string(root().join("../som-core/data/v1/heldout.expected.jsonl")).unwrap();
    let blocks =
        load_blocks(&root().join("../som-code-python/data/blocks/v1/blocks.json")).unwrap();
    let mut count = 0;
    for (index, (row, expected)) in input.lines().zip(output.lines()).enumerate() {
        let row: Value = serde_json::from_str(row).unwrap();
        let expected: Value = serde_json::from_str(expected).unwrap();
        let resolved = resolve_prompt_refs(&row["prompt"], &row["dsl"]).unwrap();
        let actual = serde_json::to_value(assemble(&resolved, &blocks).unwrap()).unwrap();
        assert_eq!(actual, expected, "held-out case {index}");
        count += 1;
    }
    assert_eq!(count, 50);
}

#[test]
fn fifty_query_combinations_match_python_bytes() {
    let input = fs::read_to_string(root().join("../som-core/data/query-v2/heldout.jsonl")).unwrap();
    let output =
        fs::read_to_string(root().join("../som-core/data/query-v2/heldout.expected.jsonl"))
            .unwrap();
    let blocks =
        load_blocks(&root().join("../som-code-python/data/blocks/v1/blocks.json")).unwrap();
    let mut count = 0;
    for (index, (row, expected)) in input.lines().zip(output.lines()).enumerate() {
        let row: Value = serde_json::from_str(row).unwrap();
        let expected: Value = serde_json::from_str(expected).unwrap();
        let resolved = resolve_prompt_refs(&row["prompt"], &row["dsl"]).unwrap();
        let actual = serde_json::to_value(assemble(&resolved, &blocks).unwrap()).unwrap();
        assert_eq!(actual, expected, "query case {index}");
        count += 1;
    }
    assert_eq!(count, 50);
}

#[test]
fn fifty_standalone_services_match_python_bytes() {
    let input =
        fs::read_to_string(root().join("../som-core/data/service-v3/heldout.jsonl")).unwrap();
    let output =
        fs::read_to_string(root().join("../som-core/data/service-v3/heldout.expected.jsonl"))
            .unwrap();
    let blocks =
        load_blocks(&root().join("../som-code-python/data/blocks/v3/blocks.json")).unwrap();
    let mut count = 0;
    for (index, (row, expected)) in input.lines().zip(output.lines()).enumerate() {
        let row: Value = serde_json::from_str(row).unwrap();
        let expected: Value = serde_json::from_str(expected).unwrap();
        let resolved = resolve_prompt_refs(&row["prompt"], &row["dsl"]).unwrap();
        let actual = serde_json::to_value(assemble(&resolved, &blocks).unwrap()).unwrap();
        assert_eq!(actual, expected, "service case {index}");
        count += 1;
    }
    assert_eq!(count, 50);
}

#[test]
fn hundred_stress_and_date_services_match_python_bytes() {
    let input =
        fs::read_to_string(root().join("../som-core/data/service-regression-v4/heldout.jsonl"))
            .unwrap();
    let output = fs::read_to_string(
        root().join("../som-core/data/service-regression-v4/heldout.expected.jsonl"),
    )
    .unwrap();
    let blocks =
        load_blocks(&root().join("../som-code-python/data/blocks/v4/blocks.json")).unwrap();
    let mut count = 0;
    for (index, (row, expected)) in input.lines().zip(output.lines()).enumerate() {
        let row: Value = serde_json::from_str(row).unwrap();
        let expected: Value = serde_json::from_str(expected).unwrap();
        let resolved = resolve_prompt_refs(&row["prompt"], &row["dsl"]).unwrap();
        let actual = serde_json::to_value(assemble(&resolved, &blocks).unwrap()).unwrap();
        assert_eq!(actual, expected, "regression service case {index}");
        count += 1;
    }
    assert_eq!(count, 100);
}

#[test]
fn date_service_rejections_match_python_categories() {
    let input =
        fs::read_to_string(root().join("../som-core/data/date-service-v4/rejections.jsonl"))
            .unwrap();
    let blocks =
        load_blocks(&root().join("../som-code-python/data/blocks/v4/blocks.json")).unwrap();
    let mut count = 0;
    for (index, line) in input.lines().enumerate() {
        let row: Value = serde_json::from_str(line).unwrap();
        let code = match resolve_prompt_refs(&row["prompt"], &row["dsl"]) {
            Ok(resolved) => assemble(&resolved, &blocks).unwrap_err().code,
            Err(error) => error.code,
        };
        assert_eq!(
            code,
            row["code"].as_str().unwrap(),
            "date rejection {index}"
        );
        count += 1;
    }
    assert_eq!(count, 10);
}

#[test]
fn standalone_rejections_match_python_categories() {
    let input =
        fs::read_to_string(root().join("../som-core/data/service-v3/rejections.jsonl")).unwrap();
    let blocks =
        load_blocks(&root().join("../som-code-python/data/blocks/v3/blocks.json")).unwrap();
    let mut count = 0;
    for (index, line) in input.lines().enumerate() {
        let row: Value = serde_json::from_str(line).unwrap();
        let code = match resolve_prompt_refs(&row["prompt"], &row["dsl"]) {
            Ok(resolved) => assemble(&resolved, &blocks).unwrap_err().code,
            Err(error) => error.code,
        };
        assert_eq!(
            code,
            row["code"].as_str().unwrap(),
            "service rejection {index}"
        );
        count += 1;
    }
    assert_eq!(count, 12);
}

#[test]
fn query_values_outside_the_contract_are_refused() {
    let line = fs::read_to_string(root().join("../som-core/data/query-v2/heldout.jsonl"))
        .unwrap()
        .lines()
        .next()
        .unwrap()
        .to_owned();
    let row: Value = serde_json::from_str(&line).unwrap();
    let mut prompt = row["prompt"].clone();
    prompt["query"]["filter_field"] = Value::String("missing".into());
    assert_eq!(validate_prompt(&prompt).unwrap_err().code, "INVALID_VALUE");
    prompt = row["prompt"].clone();
    prompt["query"]["max_limit"] = Value::from(101);
    assert_eq!(validate_prompt(&prompt).unwrap_err().code, "INVALID_VALUE");
    prompt = row["prompt"].clone();
    prompt["query"]["max_limit"] = Value::Bool(true);
    assert_eq!(validate_prompt(&prompt).unwrap_err().code, "INVALID_VALUE");
}

#[test]
fn frozen_rejection_categories_match_python() {
    let input = fs::read_to_string(root().join("../som-core/data/v1/rejections.jsonl")).unwrap();
    let blocks =
        load_blocks(&root().join("../som-code-python/data/blocks/v1/blocks.json")).unwrap();
    let mut count = 0;
    for (index, line) in input.lines().enumerate() {
        let mut row: Value = serde_json::from_str(line).unwrap();
        if row["dsl"]["version"] == "som.dsl.v2" {
            row["dsl"]["version"] = Value::String("som.dsl.v9".into());
        }
        let code = match resolve_prompt_refs(&row["prompt"], &row["dsl"]) {
            Ok(resolved) => assemble(&resolved, &blocks).unwrap_err().code,
            Err(error) => error.code,
        };
        assert_eq!(code, row["code"].as_str().unwrap(), "rejection {index}");
        count += 1;
    }
    assert_eq!(count, 12);
}

#[test]
fn unsupported_prompt_wording_is_rejected() {
    let mut prompt: Value =
        serde_json::from_str(&fs::read_to_string(root().join("examples/prompt.json")).unwrap())
            .unwrap();
    prompt["logic"][0] = Value::String("Create an item.".into());
    assert_eq!(
        validate_prompt(&prompt).unwrap_err().code,
        "UNSUPPORTED_LOGIC"
    );
    let original: Value =
        serde_json::from_str(&fs::read_to_string(root().join("examples/prompt.json")).unwrap())
            .unwrap();
    prompt["logic"] = original["logic"].clone();
    prompt["tests"][0] = Value::String("Check the API.".into());
    assert_eq!(
        validate_prompt(&prompt).unwrap_err().code,
        "UNSUPPORTED_TEST"
    );
}
