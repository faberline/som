//! Offline candidate proof. This is not a service generation route.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::{self, BufRead};
use std::path::Path;

fn evaluate(row: &Value, blocks: &BTreeMap<String, som::Block>) -> Result<Value, som::SomError> {
    let prompt = &row["prompt"];
    let dsl = if let Some(text) = row["encoded"].as_str() {
        som::flow_codec::decode_with(prompt, text, &som::flow::COMPACT_CATALOG)?
    } else {
        row["dsl"].clone()
    };
    let resolved = som::flow_compact::resolve(prompt, &dsl)?;
    if row["action"] == "resolve" {
        return Ok(json!({"status": "ok", "resolved": resolved}));
    }
    if row["action"] == "program" {
        let program =
            som::flow_compact::compile_program(&json!({"prompt": prompt, "plan": dsl["flow"]}))?;
        return Ok(json!({"status": "ok", "program": program}));
    }
    let quality_path = row["formatter_model"]
        .as_str()
        .ok_or_else(|| som::SomError {
            code: "OUTPUT_QUALITY_FAILED",
            detail: "formatter fixture is required".into(),
        })?;
    let quality =
        som::quality::Quality::load(Path::new(quality_path))?.ok_or_else(|| som::SomError {
            code: "OUTPUT_QUALITY_FAILED",
            detail: "formatter fixture is not pinned".into(),
        })?;
    if row["action"] == "diagnostic_files" {
        som::flow_codec::features_with(prompt, &som::flow::COMPACT_CATALOG)?;
        som::flow_codec::encode_with(prompt, &dsl, &som::flow::COMPACT_CATALOG)?;
        let mut files = BTreeMap::new();
        for file in resolved["files"].as_array().unwrap() {
            for (path, source) in
                som::assemble(&json!({"version": "som.dsl.v2", "files": [file]}), blocks)?
            {
                let source = if path.ends_with(".py") {
                    quality.format_source(
                        &path,
                        &som::quality::assemble_imports(&source),
                        prompt["package"].as_str().unwrap(),
                    )?
                } else {
                    source
                };
                files.insert(path, source);
            }
        }
        return Ok(json!({"status": "diagnostic", "product_acceptance": false, "files": files}));
    }
    Ok(
        json!({"status": "ok", "files": som::flow_compact::assemble(prompt, &dsl, blocks, &quality)?}),
    )
}

fn main() {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    if arguments == ["--identity"] {
        println!(
            "{}",
            json!({
                "catalog_sha256": format!("{:x}", Sha256::digest(som::flow::COMPACT_CATALOG_TEXT.as_bytes())),
                "contract_sha256": som::flow::contract_hash(),
                "support_sha256": format!("{:x}", Sha256::digest(som::flow_compact::SUPPORT_TEXT.as_bytes())),
                "codec_version": som::flow_codec::COMPACT_VERSION,
            })
        );
        return;
    }
    if arguments.len() != 1 || arguments[0].starts_with('-') {
        eprintln!("usage: som-flow-compact-verify <approved-blocks.json> | --identity");
        std::process::exit(2);
    }
    let blocks = som::load_blocks(Path::new(&arguments[0]))
        .and_then(|blocks| {
            som::composed::verify_blocks_with(&blocks, &som::flow::COMPACT_CATALOG)?;
            Ok(blocks)
        })
        .unwrap_or_else(|error| {
            eprintln!("{error}");
            std::process::exit(1);
        });
    for line in io::stdin().lock().lines() {
        let line = line.unwrap_or_else(|error| {
            eprintln!("{error}");
            std::process::exit(1);
        });
        let result = match serde_json::from_str::<Value>(&line) {
            Ok(row) => match evaluate(&row, &blocks) {
                Ok(value) => value,
                Err(error) => {
                    json!({"status": "error", "code": error.code, "detail": error.detail})
                }
            },
            Err(_) => json!({"status": "error", "code": "INVALID_JSON"}),
        };
        println!("{result}");
    }
}
