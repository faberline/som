use crate::{Error, model, render};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub const VERSION: &str = "som.bundle.service.v1";
pub const FAMILY: &str = "som.service-compiler.v1";
pub fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn canonical(value: &Value) -> Vec<u8> {
    serde_json::to_vec(value).expect("JSON serialization")
}

pub fn source_manifest() -> Value {
    let sources: &[(&str, &[u8])] = &[
        ("Cargo.toml", include_bytes!("../Cargo.toml")),
        ("Cargo.lock", include_bytes!("../Cargo.lock")),
        ("CONTRACT.md", include_bytes!("../CONTRACT.md")),
        (
            "contract/prompt.json",
            include_bytes!("../contract/prompt.json"),
        ),
        ("contract/dsl.json", include_bytes!("../contract/dsl.json")),
        (
            "contract/wire.json",
            include_bytes!("../contract/wire.json"),
        ),
        (
            "contract/task-status.cnl",
            include_bytes!("../contract/task-status.cnl"),
        ),
        ("src/lib.rs", include_bytes!("lib.rs")),
        ("src/model.rs", include_bytes!("model.rs")),
        ("src/lower.rs", include_bytes!("lower.rs")),
        ("src/error.rs", include_bytes!("error.rs")),
        ("src/strict_json.rs", include_bytes!("strict_json.rs")),
        ("src/bundle.rs", include_bytes!("bundle.rs")),
        ("src/render.rs", include_bytes!("render.rs")),
        ("src/server.rs", include_bytes!("server.rs")),
        ("src/main.rs", include_bytes!("main.rs")),
        (
            "templates/Cargo.toml.txt",
            include_bytes!("../templates/Cargo.toml.txt"),
        ),
        (
            "templates/Cargo.lock.txt",
            include_bytes!("../templates/Cargo.lock.txt"),
        ),
        (
            "templates/lib.rs.txt",
            include_bytes!("../templates/lib.rs.txt"),
        ),
        (
            "templates/README.md.txt",
            include_bytes!("../templates/README.md.txt"),
        ),
    ];
    let mut files: BTreeMap<String, String> = sources
        .iter()
        .map(|(name, bytes)| (name.to_string(), hash(bytes)))
        .collect();
    for (name, bytes) in faberline_nlp_cnl::SOURCE_FILES {
        files.insert(format!("nlp/{name}"), hash(bytes));
    }
    json!({"family":FAMILY,"files":files})
}
pub fn identity() -> Value {
    json!({"family":FAMILY,"sha256":hash(&canonical(&source_manifest()))})
}
pub fn compile(value: &Value) -> Result<Value, Error> {
    let prompt = model::validate(value)?;
    let mut bundle =
        json!({"version":VERSION,"assets":identity(),"prompt":prompt,"dsl":model::plan(&prompt)});
    let digest = hash(&canonical(&bundle));
    bundle["digest"] = json!(digest);
    Ok(bundle)
}
pub fn compile_v2(value: &Value) -> Result<Value, Error> {
    let prompt = model::validate_v2(value)?;
    let mut bundle = json!({"version":VERSION,"assets":identity(),"prompt":prompt,"dsl":model::plan_v2(&prompt)});
    let digest = hash(&canonical(&bundle));
    bundle["digest"] = json!(digest);
    Ok(bundle)
}
/// A v2 bundle is checked like v1, but it is not rendered yet (#163).
fn assemble_v2(bundle: &Value) -> Result<BTreeMap<String, String>, Error> {
    let prompt = model::validate_v2(&bundle["prompt"]).map_err(|_| {
        Error::new(
            "INVALID_BUNDLE",
            "The bundle prompt is not admitted.",
            "/bundle/prompt",
        )
    })?;
    if bundle["dsl"] != model::plan_v2(&prompt) {
        return Err(Error::new(
            "INVALID_BUNDLE",
            "The bundle DSL does not match its typed prompt.",
            "/bundle/dsl",
        ));
    }
    let content = json!({"version":VERSION,"assets":bundle["assets"],"prompt":bundle["prompt"],"dsl":bundle["dsl"]});
    if bundle["digest"] != hash(&canonical(&content)) {
        return Err(Error::new(
            "INVALID_BUNDLE",
            "The bundle digest is invalid.",
            "/bundle/digest",
        ));
    }
    Err(Error::new(
        "INVALID_BUNDLE",
        "Assembling a service prompt v2 bundle is not available yet.",
        "/bundle/prompt/version",
    ))
}
pub fn assemble(bundle: &Value) -> Result<BTreeMap<String, String>, Error> {
    model::exact(
        bundle,
        &["version", "assets", "prompt", "dsl", "digest"],
        "",
    )
    .map_err(|_| {
        Error::new(
            "INVALID_BUNDLE",
            "Expected a complete service bundle.",
            "/bundle",
        )
    })?;
    if bundle["version"] != VERSION {
        return Err(Error::new(
            "INVALID_BUNDLE",
            "Unsupported service bundle version.",
            "/bundle/version",
        ));
    }
    if bundle["assets"] != identity() {
        return Err(Error::new(
            "ASSET_HASH_MISMATCH",
            "Bundle service assets do not match this compiler.",
            "/bundle/assets",
        ));
    }
    if bundle["prompt"]["version"] == model::PROMPT_VERSION_V2 {
        return assemble_v2(bundle);
    }
    let prompt = model::validate(&bundle["prompt"]).map_err(|_| {
        Error::new(
            "INVALID_BUNDLE",
            "The bundle prompt is not admitted.",
            "/bundle/prompt",
        )
    })?;
    let expected = model::plan(&prompt);
    if bundle["dsl"] != expected {
        return Err(Error::new(
            "INVALID_BUNDLE",
            "The bundle DSL does not match its typed prompt.",
            "/bundle/dsl",
        ));
    }
    let content = json!({"version":VERSION,"assets":bundle["assets"],"prompt":bundle["prompt"],"dsl":bundle["dsl"]});
    if bundle["digest"] != hash(&canonical(&content)) {
        return Err(Error::new(
            "INVALID_BUNDLE",
            "The bundle digest is invalid.",
            "/bundle/digest",
        ));
    }
    let inputs = expected["operation"]["inputs"]
        .as_array()
        .expect("planned inputs");
    Ok(render::render(
        &prompt.package,
        &prompt.operation.alias,
        inputs[0]["name"].as_str().unwrap(),
        inputs[1]["name"].as_str().unwrap(),
    ))
}
