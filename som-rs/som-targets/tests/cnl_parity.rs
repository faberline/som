//! Opt-in comparison against a separately frozen compiler and corpus.
use serde_json::{Value, json};
use som::{
    cnl,
    compiler::{Compiler, digest, package_assets},
    flow::canonical,
    strict_json,
};
use std::{collections::BTreeMap, env, fs, path::PathBuf};

fn required_path(name: &str) -> PathBuf {
    env::var_os(name)
        .unwrap_or_else(|| panic!("set {name}"))
        .into()
}

#[test]
#[ignore = "run scripts/cnl_parity/run.py with a frozen baseline"]
fn write_compiler_parity_report() {
    let corpus: Value =
        serde_json::from_slice(&fs::read(required_path("SOM_PARITY_CORPUS")).unwrap()).unwrap();
    assert_eq!(corpus["version"], "som.cnl-parity-corpus.v1");
    let parse_only = env::var_os("SOM_PARITY_PARSE_ONLY").is_some();
    let compiler = if parse_only {
        None
    } else {
        let assets = required_path("SOM_PARITY_ASSETS");
        let ruff = required_path("SOM_PARITY_RUFF");
        package_assets(&assets, &ruff).unwrap();
        Some(Compiler::load(&assets).unwrap())
    };
    let filter = env::var("SOM_PARITY_CASE_PREFIX").unwrap_or_default();
    let mut reports = BTreeMap::new();
    for case in corpus["cases"].as_array().unwrap() {
        let id = case["id"].as_str().unwrap();
        if !id.starts_with(&filter) {
            continue;
        }
        let source = case["source"].as_str().unwrap();
        assert_eq!(digest(source.as_bytes()), case["source_sha256"], "{id}");
        let negative = case["negative"].as_bool();
        let parsed = cnl::parse(source);
        let unexpected = negative.is_some_and(|negative| parsed.is_ok() == negative);
        let mut syntax = match parsed {
            Ok(parsed) => json!({
                "prompt": canonical(&parsed.prompt),
                "source_map": parsed.source_map,
            }),
            Err(error) => json!({"parse_error": [error.http_status(), error.response()]}),
        };
        if unexpected {
            syntax["expectation_mismatch"] = json!(true);
        }
        assert!(reports.insert(format!("cnl/{id}"), syntax).is_none());
        let Some(compiler) = &compiler else {
            continue;
        };
        for target in case["targets"].as_array().unwrap() {
            let target = target.as_str().unwrap();
            let input = json!({"format": "som.cnl.v1", "source": source, "target": target});
            let (status, response) = som_targets::compile(compiler, &input.to_string());
            let mut record = if status == 200 {
                let bundle = &response["bundle"];
                assert!(bundle["dsl"].is_object(), "{id}/{target} has no DSL");
                let assemble = json!({"bundle": bundle}).to_string();
                let document = strict_json::parse(&assemble).unwrap();
                let (status, response) = if target == "python" {
                    match compiler.assemble_bundle(bundle) {
                        Ok(files) => (200, json!({"files": files})),
                        Err(error) => (
                            som_targets::status(error.code),
                            json!({"code": error.code, "detail": error.detail}),
                        ),
                    }
                } else {
                    som_targets::assemble(compiler, &document)
                };
                if status == 200 {
                    let files = response["files"].as_object().unwrap();
                    assert!(!files.is_empty(), "{id}/{target} produced no files");
                    json!({
                        "prompt": canonical(&bundle["prompt"]),
                        "dsl": canonical(&bundle["dsl"]),
                        "files": files,
                    })
                } else {
                    json!({
                        "prompt": canonical(&bundle["prompt"]),
                        "dsl": canonical(&bundle["dsl"]),
                        "assemble_error": [status, response],
                    })
                }
            } else {
                json!({"compile_error": [status, response]})
            };
            let repository_positive = id.starts_with("repo/") && negative == Some(false);
            if (negative == Some(true) && status == 200)
                || (repository_positive && target == "python" && status != 200)
            {
                record["expectation_mismatch"] = json!(true);
            }
            assert!(reports.insert(format!("{target}/{id}"), record).is_none());
        }
    }
    assert!(!reports.is_empty(), "case prefix selected no inputs");
    fs::write(
        required_path("SOM_PARITY_REPORT"),
        serde_json::to_vec_pretty(&reports).unwrap(),
    )
    .unwrap();
}
