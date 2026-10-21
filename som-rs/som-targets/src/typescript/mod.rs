//! TypeScript output phase 1 target selection and profile (issues #65 and #74).
//!
//! Until issue #75 lands the TypeScript bundle planner and renderer, an in-scope
//! `target: "typescript"` compile request returns HTTP 501 `NOT_IMPLEMENTED` at `/target`
//! per issue #74 acceptance. No candidate is prepared from this intermediate state
//! (CONTRACT.md section 2).
pub mod profile;

use som::strict_json::Document;

use crate::{Reply, rejected};

pub const TARGET: &str = "typescript";
pub const TARGET_CONTRACT_ID: &str = "som.typescript-output-contract.v1";
/// H(files map) of the latest sealed receipt for contracts/typescript-output-v1: amendment 1.
pub const TARGET_CONTRACT_SHA256: &str =
    "a1ed9978d7ddd0123e22a1e388f1a6e889ca63e43752563cd744733b84c103b4";
pub const NOT_IMPLEMENTED_CAUSE: &str =
    "TypeScript target compilation is not implemented for this prompt construct yet.";
pub const NOT_IMPLEMENTED_ALLOWED: &str =
    "Use a delivered TypeScript phase 1 construct, or omit target to generate Python.";

/// Step 6 of a TypeScript compile: NOT_IMPLEMENTED until issue #75 delivers the bundle planner.
pub fn compile(doc: &Document) -> Reply {
    rejected(doc.error(
        "/target",
        "NOT_IMPLEMENTED",
        NOT_IMPLEMENTED_CAUSE,
        NOT_IMPLEMENTED_ALLOWED,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};
    use som::compiler::{canonical_hash, digest};
    use std::collections::BTreeMap;
    use std::path::{Path, PathBuf};

    fn som_rs() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
    }

    #[test]
    fn target_contract_is_the_latest_receipt() {
        let path = som_rs().join("evidence/typescript-output-contract-amendment-001.json");
        let receipt: Value =
            serde_json::from_str(&std::fs::read_to_string(path).expect("receipt")).expect("JSON");
        assert_eq!(canonical_hash(&receipt["files"]), TARGET_CONTRACT_SHA256);
        assert_eq!(receipt["target_contract"]["sha256"], TARGET_CONTRACT_SHA256);
        assert_eq!(receipt["target_contract"]["id"], TARGET_CONTRACT_ID);
        assert_eq!(receipt["independent_review"]["final_verdict"], "APPROVED");
        for (path, sha) in receipt["files"].as_object().expect("files") {
            let bytes = std::fs::read(som_rs().join("..").join(path)).expect("contract file");
            assert_eq!(digest(&bytes), sha.as_str().expect("sha"), "{path}");
        }
    }

    fn run_compile(source: &str) -> Reply {
        crate::compile_with(
            source,
            |doc, _, _| compile(doc),
            |prompt| {
                som::validate_prompt(prompt)?;
                Ok(json!({"version": som::compiler::BUNDLE_VERSION, "prompt": prompt}))
            },
        )
    }

    fn located(body: &str, form: &str, diagnostic: &Value) -> String {
        let envelope = matches!(diagnostic["prompt_pointer"].as_str(), Some("" | "/target"));
        let parsed: Value;
        let source = if form == "cnl" && !envelope {
            parsed = serde_json::from_str(body).expect("CNL request JSON");
            parsed["source"].as_str().expect("CNL source")
        } else {
            body
        };
        let start = diagnostic["byte_start"].as_u64().expect("byte_start") as usize;
        let end = diagnostic["byte_end"].as_u64().expect("byte_end") as usize;
        source[start..end].to_owned()
    }

    fn assert_one_diagnostic(
        status: u16,
        reply: &Value,
        expected_status: u16,
        expected: &Value,
        id: &str,
    ) {
        assert_eq!(status, expected_status, "{id}: status");
        let object = reply.as_object().expect("error object");
        assert_eq!(object.len(), 4, "{id}: reply keys");
        assert_eq!(reply["status"], "error", "{id}: status field");
        assert_eq!(reply["code"], expected["code"], "{id}: code");
        assert_eq!(reply["detail"], expected["cause"], "{id}: detail");
        let diagnostics = reply["diagnostics"].as_array().expect("diagnostics");
        assert_eq!(diagnostics.len(), 1, "{id}: single diagnostic");
        let diag = &diagnostics[0];
        for (key, value) in expected.as_object().expect("expected diagnostic") {
            assert_eq!(&diag[key], value, "{id}: diagnostic.{key}");
        }
    }

    #[test]
    fn typescript_negative_cases_and_python_controls_match_the_frozen_contract() {
        let path = som_rs().join("contracts/typescript-output-v1/negative-cases.json");
        let doc: Value =
            serde_json::from_str(&std::fs::read_to_string(path).expect("negative-cases.json"))
                .expect("JSON");
        let cases = doc["cases"].as_array().expect("cases");
        assert_eq!(cases.len(), 33);

        for case in cases {
            let id = case["id"].as_str().expect("id");
            let body = case["body"].as_str().expect("body");
            let form = case["form"].as_str().expect("form");
            let expected_status = case["http_status"].as_u64().expect("http_status") as u16;
            let expected = &case["expected"];

            let (status, reply) = run_compile(body);
            assert_one_diagnostic(status, &reply, expected_status, expected, id);
            let span = located(body, form, &reply["diagnostics"][0]);
            assert_eq!(
                span,
                case["span_text"].as_str().expect("span_text"),
                "{id}: span_text"
            );

            let control_body = case["python_control_body"]
                .as_str()
                .expect("python_control_body");
            let control_expected = case["python_control_expected"]
                .as_str()
                .expect("python_control_expected");
            let (py_status, py_reply) = run_compile(control_body);
            if control_expected.starts_with("HTTP 200") {
                assert_eq!(py_status, 200, "{id}: python_control status");
                assert_eq!(py_reply["status"], "ok", "{id}: python_control ok");
                assert_eq!(
                    py_reply["bundle"]["version"],
                    som::compiler::BUNDLE_VERSION,
                    "{id}: python_control bundle version"
                );
            } else {
                assert!(
                    control_expected.starts_with("the same diagnostic"),
                    "{id}: unexpected python_control_expected {control_expected}"
                );
                assert_one_diagnostic(py_status, &py_reply, expected_status, expected, id);
                let py_span = located(control_body, form, &py_reply["diagnostics"][0]);
                assert_eq!(
                    py_span,
                    case["span_text"].as_str().expect("span_text"),
                    "{id}: python_control span_text"
                );
            }
        }
    }

    #[test]
    fn rust_target_selection_negative_cases_match_amendment_005() {
        let path = som_rs().join("contracts/rust-output-v1/negative-cases.json");
        let doc: Value =
            serde_json::from_str(&std::fs::read_to_string(path).expect("negative-cases.json"))
                .expect("JSON");
        let rt_cases: Vec<&Value> = doc["cases"]
            .as_array()
            .expect("cases")
            .iter()
            .filter(|case| case["id"].as_str().is_some_and(|id| id.starts_with("RT")))
            .collect();
        assert_eq!(rt_cases.len(), 7);

        for case in rt_cases {
            let id = case["id"].as_str().expect("id");
            let body = case["body"].as_str().expect("body");
            let form = case["form"].as_str().expect("form");
            let expected_status = case["http_status"].as_u64().expect("http_status") as u16;
            let expected = &case["expected"];

            let (status, reply) = run_compile(body);
            assert_one_diagnostic(status, &reply, expected_status, expected, id);
            let span = located(body, form, &reply["diagnostics"][0]);
            assert_eq!(
                span,
                case["span_text"].as_str().expect("span_text"),
                "{id}: span_text"
            );

            let control_body = case["python_control_body"]
                .as_str()
                .expect("python_control_body");
            let (py_status, py_reply) = run_compile(control_body);
            assert_eq!(py_status, 200, "{id}: python_control status");
            assert_eq!(
                py_reply["bundle"]["version"],
                som::compiler::BUNDLE_VERSION,
                "{id}: python_control bundle version"
            );
        }
    }

    fn parsed_prompt(body: &str) -> Value {
        let value: Value = serde_json::from_str(body).expect("request JSON");
        if value["format"] == "som.cnl.v1" {
            som::cnl::parse(value["source"].as_str().expect("source"))
                .expect("valid CNL")
                .prompt
        } else {
            let prompt = value["prompt"].clone();
            som::validate_prompt(&prompt).expect("valid JSON prompt");
            prompt
        }
    }

    #[test]
    fn target_examples_and_name_cases_pass_the_typescript_profile() {
        let path = som_rs().join("contracts/typescript-output-v1/target-examples.json");
        let doc: Value =
            serde_json::from_str(&std::fs::read_to_string(path).expect("target-examples.json"))
                .expect("JSON");

        for example in doc["examples"].as_array().expect("examples") {
            let id = example["id"].as_str().expect("id");
            let body = example["body"].as_str().expect("body");
            let target = example["target"].as_str().expect("target");

            if target == "python" {
                let same_as = example["same_as_body"].as_str().expect("same_as_body");
                let with_py = run_compile(body);
                let without = run_compile(same_as);
                assert_eq!(with_py.0, 200, "{id}: status");
                assert_eq!(with_py, without, "{id}: byte-identical to omitted target");
            } else {
                assert_eq!(target, "typescript", "{id}: target");
                let prompt = parsed_prompt(body);
                assert_eq!(profile::check(&prompt), None, "{id}: profile must admit");

                let (status, reply) = run_compile(body);
                assert_eq!(status, 501, "{id}: phase 1 before #75 returns 501");
                assert_eq!(reply["code"], "NOT_IMPLEMENTED", "{id}: code");
                let diag = &reply["diagnostics"][0];
                assert_eq!(diag["prompt_pointer"], "/target", "{id}: pointer");
                assert_eq!(diag["cause"], NOT_IMPLEMENTED_CAUSE, "{id}: cause");
                assert_eq!(
                    diag["allowed_form"], NOT_IMPLEMENTED_ALLOWED,
                    "{id}: allowed_form"
                );
                assert_eq!(located(body, "json", diag), "\"typescript\"", "{id}: span");
            }
        }

        let name_cases = doc["name_cases"].as_array().expect("name_cases");
        assert_eq!(name_cases.len(), 10);
        for case in name_cases {
            let id = case["id"].as_str().expect("id");
            let body = case["body"].as_str().expect("body");
            let prompt = parsed_prompt(body);
            assert_eq!(profile::check(&prompt), None, "{id}: TS profile must admit");

            let (status, reply) = run_compile(body);
            assert_eq!(status, 501, "{id}: TS compile before #75 returns 501");
            assert_eq!(reply["code"], "NOT_IMPLEMENTED", "{id}: code");
            assert_eq!(
                reply["diagnostics"][0]["prompt_pointer"], "/target",
                "{id}: pointer"
            );

            if let Some(rust_expected) = case.get("rust_phase1_refusal") {
                let rust_refusal = crate::profile::phase1(&prompt).expect("Rust phase 1 refusal");
                let expected_pointer = if case["form"] == "cnl" {
                    rust_refusal.pointer.clone()
                } else {
                    format!("/prompt{}", rust_refusal.pointer)
                };
                assert_eq!(
                    expected_pointer,
                    rust_expected["pointer"].as_str().expect("pointer"),
                    "{id}: rust_phase1_refusal.pointer"
                );
                assert_eq!(
                    rust_refusal.cause,
                    rust_expected["cause"].as_str().expect("cause"),
                    "{id}: rust_phase1_refusal.cause"
                );
            }

            let control_body = case["python_control_body"]
                .as_str()
                .expect("python_control_body");
            let (py_status, py_reply) = run_compile(control_body);
            assert_eq!(py_status, 200, "{id}: python_control status");
            assert_eq!(
                py_reply["bundle"]["version"],
                som::compiler::BUNDLE_VERSION,
                "{id}: python_control bundle version"
            );
        }
    }

    #[test]
    fn case_inventory_matches_the_typescript_profile() {
        let inventory_path = som_rs().join("contracts/typescript-output-v1/case-inventory.json");
        let inventory: Value = serde_json::from_str(
            &std::fs::read_to_string(inventory_path).expect("case-inventory.json"),
        )
        .expect("JSON");
        let repo = som_rs().join("..");

        for row in inventory["public_examples"]
            .as_array()
            .expect("public_examples")
        {
            let id = row["id"].as_str().expect("id");
            let prompt_path = repo.join(row["prompt"].as_str().expect("prompt"));
            let prompt: Value =
                serde_json::from_str(&std::fs::read_to_string(prompt_path).expect("prompt file"))
                    .expect("prompt JSON");
            let outcome = profile::check(&prompt).map_or("in_scope", |found| found.rule);
            assert_eq!(
                outcome,
                row["typescript"].as_str().expect("typescript"),
                "{id}"
            );
        }

        let fixtures_path = som_rs().join("tests/compiler-v1/cnl-positive-fixtures.json");
        let fixtures: Value = serde_json::from_str(
            &std::fs::read_to_string(fixtures_path).expect("cnl-positive-fixtures.json"),
        )
        .expect("JSON");
        let mut counts: BTreeMap<(String, String), u64> = BTreeMap::new();
        let mut version_positive: BTreeMap<String, String> = BTreeMap::new();
        for case in fixtures["cases"].as_array().expect("cases") {
            let category = case["category"].as_str().expect("category").to_owned();
            let outcome = profile::check(&case["expected_prompt"])
                .map_or("in_scope", |found| found.rule)
                .to_owned();
            if category == "version_positive" {
                version_positive
                    .insert(case["id"].as_str().expect("id").to_owned(), outcome.clone());
            }
            *counts.entry((category, outcome)).or_default() += 1;
        }
        let mut expected_counts: BTreeMap<(String, String), u64> = BTreeMap::new();
        for row in inventory["counts"]["cnl_positive_fixtures"]
            .as_array()
            .expect("cnl_positive_fixtures")
        {
            expected_counts.insert(
                (
                    row["category"].as_str().expect("category").to_owned(),
                    row["typescript"].as_str().expect("typescript").to_owned(),
                ),
                row["count"].as_u64().expect("count"),
            );
        }
        assert_eq!(counts, expected_counts);
        for row in inventory["version_positive_fixtures"]
            .as_array()
            .expect("version_positive_fixtures")
        {
            let id = row["id"].as_str().expect("id");
            assert_eq!(
                version_positive.get(id).map(String::as_str),
                row["typescript"].as_str(),
                "{id}"
            );
        }

        let cov_index_path = som_rs().join("tests/rust-output-v1/cases/index.json");
        let cov_index: Value = serde_json::from_str(
            &std::fs::read_to_string(&cov_index_path).expect("cases/index.json"),
        )
        .expect("JSON");
        let cov_dir = cov_index_path.parent().expect("cases dir");
        for (case, expected_row) in cov_index["cases"].as_array().expect("cases").iter().zip(
            inventory["coverage_cases"]
                .as_array()
                .expect("coverage_cases"),
        ) {
            let id = case["id"].as_str().expect("id");
            assert_eq!(id, expected_row["id"].as_str().expect("id"));
            let prompt: Value = serde_json::from_str(
                &std::fs::read_to_string(cov_dir.join(case["prompt"].as_str().expect("prompt")))
                    .expect("coverage prompt"),
            )
            .expect("JSON");
            let outcome = profile::check(&prompt).map_or("in_scope", |found| found.rule);
            assert_eq!(
                outcome,
                expected_row["typescript"].as_str().expect("typescript"),
                "{id}"
            );
        }
    }
}
