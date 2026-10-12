use serde_json::Value;
use std::{fs, path::PathBuf};

#[test]
fn related_development_compositions_and_rejections_match_python() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let data = root.join("../som-core/data/related-service");
    let blocks = som::load_blocks(&root.join("../som-code-python/data/blocks/v5/blocks.json")).unwrap();
    let cases = fs::read_to_string(data.join("heldout.jsonl")).unwrap();
    let expected = fs::read_to_string(data.join("heldout.expected.jsonl")).unwrap();
    assert_eq!(cases.lines().count(), expected.lines().count());
    let mut count = 0;
    for (case, expected) in cases.lines().zip(expected.lines()) {
        let case: Value = serde_json::from_str(case).unwrap();
        let expected: Value = serde_json::from_str(expected).unwrap();
        let resolved = som::resolve_prompt_refs(&case["prompt"], &case["dsl"]).unwrap();
        assert_eq!(serde_json::to_value(som::assemble(&resolved, &blocks).unwrap()).unwrap(), expected, "case {count}");
        count += 1;
    }
    assert_eq!(count, 50);
    let mut rejected = 0;
    for line in fs::read_to_string(data.join("rejections.jsonl")).unwrap().lines() {
        let case: Value = serde_json::from_str(line).unwrap();
        assert_eq!(som::validate_prompt(&case["prompt"]).unwrap_err().code, case["code"].as_str().unwrap());
        rejected += 1;
    }
    assert_eq!(rejected, 12);
}

#[test]
fn business_reference_compositions_match_python() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let data = root.join("../som-core/data/business-service");
    let blocks = som::load_blocks(&root.join("../som-code-python/data/blocks/v5/blocks.json")).unwrap();
    let cases = fs::read_to_string(data.join("requirements.jsonl")).unwrap();
    let expected = fs::read_to_string(data.join("reference.jsonl")).unwrap();
    assert_eq!(cases.lines().count(), expected.lines().count());
    for (index, (case, expected)) in cases.lines().zip(expected.lines()).enumerate() {
        let case: Value = serde_json::from_str(case).unwrap();
        let expected: Value = serde_json::from_str(expected).unwrap();
        let resolved = som::resolve_prompt_refs(&case["prompt"], &case["dsl"]).unwrap();
        assert_eq!(serde_json::to_value(som::assemble(&resolved, &blocks).unwrap()).unwrap(), expected, "case {index}");
    }
}

#[test]
#[ignore = "requires local Metal and the zero-step business fixture"]
fn metal_fixture_loads_and_rejects_wrong_operation_selection() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let model = som::model::Decoder::load(&root.join("models/business-metal-fixture")).unwrap();
    model.warmup().unwrap();
    let row: Value = serde_json::from_str(fs::read_to_string(root.join("../som-core/data/business-service/train.jsonl")).unwrap().lines().next().unwrap()).unwrap();
    let error = model.generate(&row["prompt"].to_string()).unwrap_err();
    assert_eq!(error.code, "MODEL_FAILED");
    let blocks = som::load_blocks(&root.join("../som-code-python/data/blocks/v5/blocks.json")).unwrap();
    let output = som::assemble(&som::resolve_prompt_refs(&row["prompt"], &row["dsl"]).unwrap(), &blocks).unwrap();
    assert!(!output.is_empty());
}

#[test]
#[ignore = "requires local Metal and an accepted candidate checkpoint"]
fn candidate_model_generates_all_regression_bytes() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let dir = std::env::var("SOM_TEST_MODEL_DIR").expect("explicit candidate directory");
    let model = som::model::Decoder::load(std::path::Path::new(&dir)).unwrap();
    model.warmup().unwrap();
    let blocks = som::load_blocks(&root.join("../som-code-python/data/blocks/v5/blocks.json")).unwrap();
    let cases = fs::read_to_string(root.join("../som-core/data/business-service/regression.jsonl")).unwrap();
    for (index, line) in cases.lines().enumerate() {
        let row: Value = serde_json::from_str(line).unwrap();
        let result = model.generate(&row["prompt"].to_string()).unwrap_or_else(|error| panic!("case {index}: {error}"));
        let dsl: Value = serde_json::from_str(&result).unwrap();
        let actual = som::assemble(&som::resolve_prompt_refs(&row["prompt"], &dsl).unwrap(), &blocks).unwrap();
        let expected = som::assemble(&som::resolve_prompt_refs(&row["prompt"], &row["dsl"]).unwrap(), &blocks).unwrap();
        assert_eq!(actual, expected, "case {index}");
    }
    assert_eq!(cases.lines().count(), 114);
}
