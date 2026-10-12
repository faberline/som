//! Admission metadata for the seen-input M2 flow path.
//! This module cannot create weights or claim model generalization.

use crate::{SomError, flow, flow_codec, quality};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{fs, path::Path};

pub const PURPOSE: &str = "m2-development-fixture-smoke";
pub const ADMISSION_VERSION: &str = "som.flow.m2-training-admission.v2";
pub const BUDGET_TEXT: &str = include_str!("../contracts/m2-output-budget.v2.json");

fn unavailable(detail: &str) -> SomError {
    SomError::new("MODEL_UNAVAILABLE", detail)
}

fn read(path: &Path) -> Result<Vec<u8>, SomError> {
    fs::read(path).map_err(|error| unavailable(&error.to_string()))
}

fn json_file(path: &Path) -> Result<Value, SomError> {
    serde_json::from_slice(&read(path)?).map_err(|error| unavailable(&error.to_string()))
}

/// Check the fixed smoke architecture before any tensor allocation.
pub fn verify_config(config: &Value) -> Result<(), SomError> {
    for (key, expected) in [
        ("n_ctx", 4096),
        ("n_positions", 4096),
        ("n_embd", 64),
        ("n_head", 4),
        ("n_layer", 2),
        ("vocab_size", 1024),
        ("max_new_tokens", 2048),
    ] {
        if config[key].as_u64() != Some(expected) {
            return Err(unavailable("M2 flow architecture differs from the sealed scope"));
        }
    }
    if config["model_type"] != "gpt2" || config["output_mode"] != "flow_plan" {
        return Err(unavailable("M2 smoke weights require the flow_plan decoder"));
    }
    if !config["flow_input_policy"].is_null()
        && config["flow_input_policy"].as_str().and_then(flow_codec::input_policy_text).is_none() {
        return Err(unavailable("unknown flow input policy"));
    }
    Ok(())
}

/// Optional for frozen M2 models, mandatory when a new input policy is selected.
pub fn verify_input_policy(model: &Path, config: &Value) -> Result<(), SomError> {
    if config["flow_input_policy"].is_null() {
        return Ok(());
    }
    let expected = config["flow_input_policy"].as_str()
        .and_then(flow_codec::input_policy_text)
        .ok_or_else(|| unavailable("unknown flow input policy"))?;
    let data = read(&model.join("input-policy.json"))?;
    let manifest = json_file(&model.join("manifest.json"))?;
    if data != expected.as_bytes()
        || manifest["files"]["input-policy.json"] != format!("{:x}", Sha256::digest(&data)) {
        return Err(unavailable("flow input policy or its hash differs"));
    }
    Ok(())
}

/// Verify all sidecars; the outer manifest checks their file hashes.
pub fn verify_sidecars(model: &Path, manifest: &Value) -> Result<(), SomError> {
    if manifest["purpose"] != PURPOSE
        || manifest["training_seed"] != 17
        || manifest["training_examples"] != 12
        || manifest["prompt_version"] != flow::VERSION
        || manifest["dsl_version"] != flow::DSL_VERSION
        || manifest["quality_contract"] != quality::VERSION
    {
        return Err(unavailable("M2 flow manifest does not declare the seen-only fixture scope"));
    }
    let admission = json_file(&model.join("admission.json"))?;
    if admission["version"] != ADMISSION_VERSION
        || admission["size_quality_passed"] != true
        || admission["formal_cases"] != 114
        || admission["development_cases"] != 12
        || admission["limits"] != serde_json::json!({
            "files":12,"blocks":64,"bytes":"observed",
            "input_tokens":2048,"output_tokens":2048
        })
        || admission["blocks_sha256"] != manifest["blocks_sha256"]
        || manifest["blocks_sha256"].as_str().is_none_or(|hash|
            hash.len() != 64 || !hash.bytes().all(|byte| byte.is_ascii_hexdigit()))
    {
        return Err(unavailable("M2 weights lack a matching size and quality admission"));
    }
    if read(&model.join("contracts.json"))? != flow::COMPACT_CATALOG_TEXT.as_bytes()
        || read(&model.join("contract.md"))? != flow::CONTRACT_TEXT.as_bytes()
        || read(&model.join("output-budget.json"))? != BUDGET_TEXT.as_bytes()
        || admission["output_budget_sha256"] != format!("{:x}", Sha256::digest(BUDGET_TEXT.as_bytes()))
    {
        return Err(unavailable("compiled compact flow contracts differ from the model"));
    }
    let codec = json_file(&model.join("codec.json"))?;
    if codec["version"] != flow_codec::COMPACT_VERSION {
        return Err(unavailable("M2 requires the compact lossless flow codec"));
    }
    let corpus = read(&model.join("development.jsonl"))?;
    if corpus != include_bytes!("../../som-core/data/flow-v7-r10/dev.jsonl") {
        return Err(unavailable("M2 smoke fixtures differ from the sealed development corpus"));
    }
    let digest = format!("{:x}", Sha256::digest(&corpus));
    if admission["development_sha256"] != digest {
        return Err(unavailable("development fixture hash differs from admission"));
    }
    let rows = std::str::from_utf8(&corpus)
        .map_err(|_| unavailable("development fixture is not UTF-8"))?
        .lines()
        .map(serde_json::from_str::<Value>)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| unavailable("development fixture contains invalid JSON"))?;
    if rows.len() != 12 {
        return Err(unavailable("M2 test weights must use exactly twelve development fixtures"));
    }
    let mut inputs = std::collections::BTreeSet::new();
    for row in rows {
        let encoded = flow_codec::features_with(&row["prompt"], &flow::COMPACT_CATALOG)?;
        if !inputs.insert(encoded) {
            return Err(unavailable("development fixtures repeat an actual model input"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn config() -> Value {
        json!({"model_type":"gpt2","output_mode":"flow_plan",
            "n_ctx":4096,"n_positions":4096,"n_embd":64,
            "n_head":4,"n_layer":2,"vocab_size":1024,
            "max_new_tokens":2048})
    }

    #[test]
    fn smoke_architecture_is_fixed() {
        verify_config(&config()).unwrap();
        for key in ["n_ctx", "n_positions", "n_embd", "n_head", "n_layer",
                    "vocab_size", "max_new_tokens"] {
            let mut changed = config();
            changed[key] = json!(1);
            assert_eq!(verify_config(&changed).unwrap_err().code, "MODEL_UNAVAILABLE");
        }
    }

    #[test]
    fn wrong_purpose_fails_before_reading_weights() {
        let manifest = json!({"purpose":"formal-test-training"});
        let error = verify_sidecars(Path::new("/nonexistent"), &manifest).unwrap_err();
        assert_eq!(error.code, "MODEL_UNAVAILABLE");
        assert!(error.detail.contains("seen-only"));
    }

    #[test]
    fn input_policy_is_optional_only_for_archived_inputs() {
        verify_input_policy(Path::new("/nonexistent"), &config()).unwrap();
        let mut changed = config();
        changed["flow_input_policy"] = json!("unknown");
        assert_eq!(verify_config(&changed).unwrap_err().code, "MODEL_UNAVAILABLE");
        assert_eq!(verify_input_policy(Path::new("/nonexistent"), &changed)
            .unwrap_err().code, "MODEL_UNAVAILABLE");
    }

    #[test]
    fn input_policy_requires_exact_content_and_manifest_hash() {
        let directory = std::env::temp_dir().join(format!("som-input-policy-{}-{}",
            std::process::id(), std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir(&directory).unwrap();
        let mut selected = config();
        selected["flow_input_policy"] = json!(flow_codec::M3_INPUT_VERSION);
        fs::write(directory.join("input-policy.json"), flow_codec::M3_INPUT_TEXT).unwrap();
        let manifest = json!({"files":{"input-policy.json":format!("{:x}",
            Sha256::digest(flow_codec::M3_INPUT_TEXT.as_bytes()))}});
        fs::write(directory.join("manifest.json"), manifest.to_string()).unwrap();
        verify_input_policy(&directory, &selected).unwrap();
        fs::write(directory.join("input-policy.json"), "{}").unwrap();
        assert_eq!(verify_input_policy(&directory, &selected).unwrap_err().code, "MODEL_UNAVAILABLE");
        fs::write(directory.join("input-policy.json"), flow_codec::M3_INPUT_TEXT).unwrap();
        fs::write(directory.join("manifest.json"), json!({"files":{}}).to_string()).unwrap();
        assert_eq!(verify_input_policy(&directory, &selected).unwrap_err().code, "MODEL_UNAVAILABLE");
        selected["flow_input_policy"] = json!(flow_codec::M3_SEMANTIC_INPUT_VERSION);
        fs::write(directory.join("input-policy.json"), flow_codec::M3_SEMANTIC_INPUT_TEXT).unwrap();
        fs::write(directory.join("manifest.json"), json!({"files":{
            "input-policy.json":format!("{:x}", Sha256::digest(flow_codec::M3_SEMANTIC_INPUT_TEXT.as_bytes()))}}).to_string()).unwrap();
        verify_input_policy(&directory, &selected).unwrap();
        selected["flow_input_policy"] = json!(flow_codec::M3_INPUT_VERSION);
        assert_eq!(verify_input_policy(&directory, &selected).unwrap_err().code, "MODEL_UNAVAILABLE");
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn sidecars_bind_admission_contracts_and_development_bytes() {
        struct Temporary(std::path::PathBuf);
        impl Drop for Temporary {
            fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
        }
        let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
            .unwrap().as_nanos();
        let temporary = Temporary(std::env::temp_dir().join(format!(
            "som-flow-metadata-{}-{nonce}", std::process::id())));
        fs::create_dir(&temporary.0).unwrap();
        let model = &temporary.0;
        let corpus = include_bytes!("../../som-core/data/flow-v7-r10/dev.jsonl");
        let blocks_hash = "a".repeat(64);
        let manifest = json!({"purpose":PURPOSE,"training_seed":17,
            "training_examples":12,"prompt_version":flow::VERSION,
            "dsl_version":flow::DSL_VERSION,"quality_contract":quality::VERSION,
            "blocks_sha256":blocks_hash});
        let admission = json!({"version":ADMISSION_VERSION,"size_quality_passed":true,
            "formal_cases":114,"development_cases":12,
            "limits":{"files":12,"blocks":64,"bytes":"observed",
                      "input_tokens":2048,"output_tokens":2048},
            "blocks_sha256":blocks_hash,
            "output_budget_sha256":format!("{:x}",Sha256::digest(BUDGET_TEXT.as_bytes())),
            "development_sha256":format!("{:x}",Sha256::digest(corpus))});
        fs::write(model.join("contracts.json"), flow::COMPACT_CATALOG_TEXT).unwrap();
        fs::write(model.join("output-budget.json"), BUDGET_TEXT).unwrap();
        fs::write(model.join("contract.md"), flow::CONTRACT_TEXT).unwrap();
        fs::write(model.join("codec.json"), json!({"version":flow_codec::COMPACT_VERSION}).to_string()).unwrap();
        fs::write(model.join("development.jsonl"), corpus).unwrap();
        fs::write(model.join("admission.json"), admission.to_string()).unwrap();
        verify_sidecars(model, &manifest).unwrap();
        assert!(!model.join("model.safetensors").exists());

        for (key, replacement) in [
            ("size_quality_passed", json!(false)),
            ("development_cases", json!(114)),
            ("blocks_sha256", json!("b".repeat(64))),
            ("development_sha256", json!("b".repeat(64))),
            ("limits", json!({"bytes":131072})),
        ] {
            let mut changed = admission.clone();
            changed[key] = replacement;
            fs::write(model.join("admission.json"), changed.to_string()).unwrap();
            assert_eq!(verify_sidecars(model, &manifest).unwrap_err().code,
                       "MODEL_UNAVAILABLE");
        }
        fs::write(model.join("admission.json"), admission.to_string()).unwrap();
        for (name, original) in [
            ("contracts.json", flow::COMPACT_CATALOG_TEXT.as_bytes()),
            ("contract.md", flow::CONTRACT_TEXT.as_bytes()),
            ("development.jsonl", corpus.as_slice()),
        ] {
            fs::write(model.join(name), b"changed").unwrap();
            assert_eq!(verify_sidecars(model, &manifest).unwrap_err().code,
                       "MODEL_UNAVAILABLE");
            fs::write(model.join(name), original).unwrap();
        }
        fs::write(model.join("codec.json"), json!({"version":flow_codec::VERSION}).to_string()).unwrap();
        assert_eq!(verify_sidecars(model, &manifest).unwrap_err().code,
                   "MODEL_UNAVAILABLE");
    }
}
