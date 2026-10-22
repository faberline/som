//! QA driver for the React renderer (issue #127): compile and assemble request bodies with
//! target react and write each product to a new directory.
//!
//! Usage: react_render <compiler assets dir> <cases.json> <out dir (new)>
//!
//! cases.json is {"cases": [{"id": ..., "bodies": [request body, ...]}]}. Each body is a
//! compile request with target react, run in the frozen x-check-order up to the React profile
//! (the same steps as the crate compile entry since issue #128). For each case the
//! driver writes <out>/<id>/prompt.json (the canonical bundle prompt) and <out>/<id>/files/,
//! and <out>/results.json lists per case the status and bundle hash of each body, the
//! prompt hash and the hash of each product file.
use serde_json::{Map, Value, json};
use som::compiler::{Compiler, canonical_hash, digest};
use som::strict_json;
use som_targets::react;
use std::{env, fs, path::Path, process::ExitCode};

fn compile(compiler: &Compiler, body: &str) -> Result<Value, String> {
    let doc = strict_json::parse(body).map_err(|error| format!("parse: {error}"))?;
    if doc.value["target"] != react::TARGET {
        return Err("the request has no target react".to_owned());
    }
    let (prompt, pointer) = match doc.value["format"].as_str() {
        Some("som.cnl.v1") => {
            let source = doc.value["source"].as_str().ok_or("no CNL source")?;
            let parsed =
                som::cnl::parse(source).map_err(|error| format!("cnl: {}", error.response()))?;
            (parsed.prompt, "/source")
        }
        Some("som.prompt-json.v1") => {
            let prompt = doc.value["prompt"].clone();
            som::validate_prompt(&prompt).map_err(|error| format!("validate: {error:?}"))?;
            (prompt, "/prompt")
        }
        other => return Err(format!("unexpected format {other:?}")),
    };
    if let Some(found) = react::profile::check(&prompt) {
        return Err(format!("refused: {} at {}", found.rule, found.pointer));
    }
    let (status, reply) = react::compile(compiler, &doc, &prompt, pointer);
    if status != 200 {
        return Err(format!("compile {status}: {reply}"));
    }
    Ok(reply["bundle"].clone())
}

fn assemble(compiler: &Compiler, bundle: &Value) -> Result<Map<String, Value>, String> {
    let body = som::flow::canonical(&json!({"bundle": bundle}));
    let doc = strict_json::parse(&body).map_err(|error| format!("parse: {error}"))?;
    let (status, reply) = react::assemble(compiler, &doc);
    if status != 200 {
        return Err(format!("assemble {status}: {reply}"));
    }
    reply["files"]
        .as_object()
        .cloned()
        .ok_or_else(|| "assemble returned no files".to_owned())
}

fn run_case(compiler: &Compiler, case: &Value, out: &Path) -> Result<Value, String> {
    let id = case["id"].as_str().ok_or("case id")?;
    let mut bundles = Vec::new();
    for body in case["bodies"].as_array().ok_or("case bodies")? {
        bundles.push(compile(compiler, body.as_str().ok_or("body string")?)?);
    }
    let first = bundles.first().ok_or("a case needs a body")?;
    let bundle_hashes: Vec<String> = bundles.iter().map(canonical_hash).collect();
    let files = assemble(compiler, first)?;
    let dir = out.join(id);
    for (path, content) in &files {
        let target = dir.join("files").join(path);
        fs::create_dir_all(target.parent().expect("file parent")).map_err(|e| e.to_string())?;
        fs::write(&target, content.as_str().ok_or("file text")?).map_err(|e| e.to_string())?;
    }
    fs::write(
        dir.join("prompt.json"),
        som::flow::canonical(&first["prompt"]),
    )
    .map_err(|e| e.to_string())?;
    let hashes: Map<String, Value> = files
        .iter()
        .map(|(path, content)| {
            let text = content.as_str().unwrap_or_default();
            (path.clone(), Value::String(digest(text.as_bytes())))
        })
        .collect();
    Ok(json!({
        "id": id,
        "status": "ok",
        "bundle_sha256": bundle_hashes,
        "prompt_sha256": canonical_hash(&first["prompt"]),
        "files": hashes,
    }))
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 {
        eprintln!("usage: react_render <compiler assets dir> <cases.json> <out dir (new)>");
        return ExitCode::from(2);
    }
    let compiler = Compiler::load(Path::new(&args[1])).expect("load compiler assets");
    let cases: Value =
        serde_json::from_str(&fs::read_to_string(&args[2]).expect("cases.json")).expect("JSON");
    let out = Path::new(&args[3]);
    fs::create_dir(out).expect("a new out dir");
    let mut results = Vec::new();
    let mut failed = 0;
    for case in cases["cases"].as_array().expect("cases") {
        match run_case(&compiler, case, out) {
            Ok(result) => results.push(result),
            Err(error) => {
                failed += 1;
                results.push(json!({"id": case["id"], "status": "error", "error": error}));
            }
        }
    }
    let text = serde_json::to_string_pretty(&json!({"cases": results})).expect("JSON");
    fs::write(out.join("results.json"), text + "\n").expect("results.json");
    println!("{} cases, {failed} failed", results.len());
    if failed == 0 {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}
