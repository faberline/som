//! Separate full-training admission. M2's seen-fixture guards remain intact.
use crate::{SomError, flow, flow_codec, flow_constraints, flow_model, quality};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs, path::Path};

pub const PURPOSE: &str = "m3-supervised-composition";
pub const ADMISSION_VERSION: &str = "som.flow.m3-training-admission.v1";
pub const EXTRA_SIDECARS: &[&str] = &["training.jsonl", "dataset.json", "decode-policy.json", "input-policy.json"];
fn unavailable(detail: &str) -> SomError { SomError::new("MODEL_UNAVAILABLE", detail) }
fn bytes(path: &Path) -> Result<Vec<u8>, SomError> { fs::read(path).map_err(|e| unavailable(&e.to_string())) }
fn json_file(path: &Path) -> Result<Value, SomError> { serde_json::from_slice(&bytes(path)?).map_err(|e| unavailable(&e.to_string())) }
fn hash(value: &[u8]) -> String { format!("{:x}", Sha256::digest(value)) }

pub fn verify_config(config: &Value) -> Result<(), SomError> {
    flow_model::verify_config(config)?;
    if config["training_profile"] != PURPOSE || config["flow_decode_policy"] != flow_constraints::VERSION
        || config["flow_input_policy"].as_str().and_then(flow_codec::input_policy_text).is_none() {
        return Err(unavailable("M3 requires its own training profile and constrained decode policy"));
    }
    Ok(())
}

pub fn verify_sidecars(model: &Path, manifest: &Value) -> Result<(), SomError> {
    if manifest["purpose"] != PURPOSE || manifest["training_seed"] != 17
        || manifest["training_examples"] != 2400 || manifest["development_examples"] != 300
        || manifest["formal_training_examples"] != 0 || manifest["unique_inputs"] != 2700
        || manifest["prompt_version"] != flow::VERSION || manifest["dsl_version"] != flow::DSL_VERSION
        || manifest["quality_contract"] != quality::VERSION
        || manifest["decode_policy"] != flow_constraints::VERSION
        || manifest["development_perfect"] != true {
        return Err(unavailable("M3 manifest lacks the complete disjoint full-training scope"));
    }
    let admission = json_file(&model.join("admission.json"))?;
    let config = json_file(&model.join("config.json"))?;
    let input_text = config["flow_input_policy"].as_str()
        .and_then(flow_codec::input_policy_text)
        .ok_or_else(|| unavailable("unknown flow input policy"))?;
    let dataset_bytes = bytes(&model.join("dataset.json"))?;
    let dataset: Value = serde_json::from_slice(&dataset_bytes).map_err(|e| unavailable(&e.to_string()))?;
    let counts = json!({"train":2400,"dev":300,"formal":300});
    if admission["version"] != ADMISSION_VERSION || admission["counts"] != counts
        || admission["quality_behavior_passed"] != true
        || admission["limits"] != json!({"files":12,"blocks":64,"bytes":"observed","input_tokens":2048,"output_tokens":2048})
        || admission["blocks_sha256"] != manifest["blocks_sha256"]
        || admission["formal_training_examples"] != 0
        || admission["decode_policy"] != serde_json::from_str::<Value>(flow_constraints::POLICY_TEXT).unwrap()
        || manifest["blocks_sha256"].as_str().is_none_or(|value|
            value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()))
        || dataset["version"] != "som.flow.m3-dataset.v1" || dataset["counts"] != counts
        || dataset["input_policy_version"] != config["flow_input_policy"]
        || dataset["cross_split_overlap"] != json!({"input":0,"structure":0,"graph":0})
        || admission["dataset_sha256"] != hash(&dataset_bytes)
        || manifest["dataset_sha256"] != hash(&dataset_bytes) {
        return Err(unavailable("M3 artifact admission or data split seal differs"));
    }
    for (name, source, count) in [("training.jsonl", "train.jsonl", 2400), ("development.jsonl", "dev.jsonl", 300)] {
        let corpus = bytes(&model.join(name))?;
        if dataset["files"][source] != hash(&corpus) { return Err(unavailable("M3 split bytes differ from the dataset seal")); }
        let text = std::str::from_utf8(&corpus).map_err(|_| unavailable("M3 split is not UTF-8"))?;
        let rows = text.lines().map(serde_json::from_str::<Value>).collect::<Result<Vec<_>, _>>().map_err(|e| unavailable(&e.to_string()))?;
        let split = if source == "train.jsonl" { "train" } else { "dev" };
        let identities = rows.iter().filter_map(|row| row["id"].as_str()).collect::<std::collections::BTreeSet<_>>();
        let expected = (0..count).map(|index| format!("m3-{split}-{index:04}")).collect::<std::collections::BTreeSet<_>>();
        if rows.len() != count || identities != expected.iter().map(String::as_str).collect()
            || rows.iter().any(|row| ["experiment", "documents"].contains(&row["app"].as_str().unwrap_or(""))) {
            return Err(unavailable("M3 split repeats a held-out App or has the wrong count"));
        }
    }
    if bytes(&model.join("contracts.json"))? != flow::COMPACT_CATALOG_TEXT.as_bytes()
        || bytes(&model.join("contract.md"))? != flow::CONTRACT_TEXT.as_bytes()
        || bytes(&model.join("output-budget.json"))? != flow_model::BUDGET_TEXT.as_bytes()
        || bytes(&model.join("decode-policy.json"))? != flow_constraints::POLICY_TEXT.as_bytes()
        || bytes(&model.join("input-policy.json"))? != input_text.as_bytes()
        || json_file(&model.join("codec.json"))?["version"] != flow_codec::COMPACT_VERSION {
        return Err(unavailable("compiled M3 contracts, codec or decode policy differ"));
    }
    let checks = manifest["development_checks"].as_array().ok_or_else(|| unavailable("M3 lacks complete checkpoint history"))?;
    let last = checks.last().ok_or_else(|| unavailable("M3 checkpoint history is empty"))?;
    let step = manifest["training_steps"].as_u64().unwrap_or(0);
    if step == 0 || !step.is_multiple_of(1000) || last["step"] != step
        || last["correct"] != 300 || last["total"] != 300
        || checks.len() as u64 != step / 1000
        || checks.iter().enumerate().any(|(index, row)| row["step"] != (index as u64 + 1) * 1000
            || row["total"] != 300 || row["correct"].as_u64().is_none_or(|correct| correct > 300))
        || checks[..checks.len()-1].iter().any(|row| row["correct"] == 300)
        || last["results"].as_array().is_none_or(|rows| rows.len() != 300
            || rows.iter().enumerate().any(|(index, row)| row["correct"] != true || row["id"] != format!("m3-dev-{index:04}"))) {
        return Err(unavailable("M3 is not the earliest perfect complete-development checkpoint"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn m3_profile_and_architecture_are_checked_before_weights() {
        let config = json!({"model_type":"gpt2","output_mode":"flow_plan",
            "n_ctx":4096,"n_positions":4096,"n_embd":64,"n_head":4,
            "n_layer":2,"vocab_size":1024,"max_new_tokens":2048,
            "training_profile":PURPOSE,"flow_decode_policy":flow_constraints::VERSION,
            "flow_input_policy":flow_codec::M3_INPUT_VERSION});
        verify_config(&config).unwrap();
        let mut semantic = config.clone();
        semantic["flow_input_policy"] = json!(flow_codec::M3_SEMANTIC_INPUT_VERSION);
        verify_config(&semantic).unwrap();
        for key in ["n_embd", "vocab_size", "training_profile", "flow_decode_policy", "flow_input_policy"] {
            let mut changed = config.clone();
            changed[key] = json!("different");
            assert_eq!(verify_config(&changed).unwrap_err().code, "MODEL_UNAVAILABLE");
        }
        let mut missing = config;
        missing.as_object_mut().unwrap().remove("flow_input_policy");
        assert_eq!(verify_config(&missing).unwrap_err().code, "MODEL_UNAVAILABLE");
    }

    #[test]
    fn m2_or_formal_training_scope_cannot_enter_m3_loader() {
        for manifest in [json!({"purpose":flow_model::PURPOSE}),
                         json!({"purpose":PURPOSE,"formal_training_examples":300})] {
            assert_eq!(verify_sidecars(Path::new("/nonexistent-m3"), &manifest)
                .unwrap_err().code, "MODEL_UNAVAILABLE");
        }
    }
}
