//! Rust output phase 1 for one resource (issue #48): planning, som.dsl-bundle.v2 bundles and
//! the v2 assembler in the frozen x-assemble-check-order of contracts/rust-output-v1.
//!
//! The plan is a pure function of the prompt and the Rust catalog. The assembler renders
//! only the plan the catalog gives for the prompt, so a bundle cannot select other code.
pub mod format;
pub mod prompt_schema;
pub mod render;

use serde_json::{Map, Value, json};
use som::{
    compiler::{ASSEMBLE_BODY_LIMIT, Compiler, canonical_hash, digest},
    strict_json::Document,
};
use std::collections::BTreeMap;
use std::sync::LazyLock;

use crate::{Reply, profile, rejected};

pub const BUNDLE_VERSION: &str = "som.dsl-bundle.v2";
pub const DSL_VERSION: &str = "som.dsl.rust.v1";
pub const TARGET: &str = "rust";
pub const MAX_FILES: usize = 12;
pub const MAX_BLOCKS: usize = 64;

pub const CATALOG_ID: &str = "som.rust-catalog.v1";
pub const BLOCK_LIBRARY_ID: &str = "som.rust-block-library.v1";
pub const BACKEND_ID: &str = "som.rust-backend.v1";
pub const TARGET_CONTRACT_ID: &str = "som.rust-output-contract.v1";
/// H(files map) of the latest sealed receipt for contracts/rust-output-v1: amendment 2.
pub const TARGET_CONTRACT_SHA256: &str =
    "00edf1f7cba7847ba142b68a97c4956e2ed62a1ed4e821a712122a187992b838";

pub const CATALOG_TEXT: &str = include_str!("../../catalog/rust-catalog.v1.json");
const BLOCK_LIBRARY_TEXT: &str =
    concat!(include_str!("render.rs"), include_str!("render/related.rs"));
static CATALOG: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(CATALOG_TEXT).expect("Rust catalog JSON"));

/// Every source that decides the Rust bundle and its files, hashed as text.
const BACKEND_FILES: &[(&str, &str)] = &[
    ("som-targets/Cargo.toml", include_str!("../../Cargo.toml")),
    ("som-targets/src/lib.rs", include_str!("../lib.rs")),
    ("som-targets/src/main.rs", include_str!("../main.rs")),
    ("som-targets/src/profile.rs", include_str!("../profile.rs")),
    ("som-targets/src/rust/mod.rs", include_str!("mod.rs")),
    ("som-targets/src/rust/format.rs", include_str!("format.rs")),
    (
        "som-targets/src/rust/prompt_schema.rs",
        include_str!("prompt_schema.rs"),
    ),
    (
        "contracts/compiler-v1/compiler-prompt.schema.json",
        prompt_schema::PROMPT_SCHEMA_TEXT,
    ),
    (
        "contracts/rust-output-v1/scope.json",
        include_str!("../../../contracts/rust-output-v1/scope.json"),
    ),
    (
        "contracts/rust-output-v1/reference/Cargo.toml",
        include_str!("../../../contracts/rust-output-v1/reference/Cargo.toml"),
    ),
    (
        "contracts/rust-output-v1/reference/Cargo.lock",
        include_str!("../../../contracts/rust-output-v1/reference/Cargo.lock"),
    ),
];

/// The product files every phase 1 plan produces, besides its one migration.
const LAYOUT: &[&str] = &[
    "Cargo.toml",
    "Cargo.lock",
    "src/main.rs",
    "src/lib.rs",
    "src/models.rs",
    "src/schemas.rs",
    "src/api.rs",
    "src/db.rs",
    "tests/api.rs",
    "README.md",
];

fn descriptor(id: &str, bytes: &[u8]) -> Value {
    json!({"id": id, "sha256": digest(bytes)})
}

fn described(id: &str, value: &Value) -> Value {
    descriptor(id, som::flow::canonical(value).as_bytes())
}

/// The active local Rust manifest for a prompt. The shared descriptors are the ones compiler-v1
/// gives a Python bundle for the same prompt. The Rust ones replace catalog, block library,
/// backend and formatter, and target_contract is added.
pub fn descriptors(compiler: &Compiler, prompt: &Value) -> Value {
    let mut assets = compiler.descriptors(prompt);
    for (name, value) in rust_descriptors() {
        assets[name] = value;
    }
    assets
}

/// The Rust descriptors that do not depend on the prompt.
fn rust_descriptors() -> [(&'static str, Value); 5] {
    let files: Map<String, Value> = BACKEND_FILES
        .iter()
        .map(|(path, text)| ((*path).to_owned(), json!(digest(text.as_bytes()))))
        .collect();
    [
        ("catalog", descriptor(CATALOG_ID, CATALOG_TEXT.as_bytes())),
        (
            "block_library",
            descriptor(BLOCK_LIBRARY_ID, BLOCK_LIBRARY_TEXT.as_bytes()),
        ),
        (
            "backend",
            described(BACKEND_ID, &json!({"version": BACKEND_ID, "files": files})),
        ),
        (
            "formatter",
            json!({"id": format::FORMATTER_ID, "sha256": format::RUSTFMT_SHA256}),
        ),
        (
            "target_contract",
            json!({"id": TARGET_CONTRACT_ID, "sha256": TARGET_CONTRACT_SHA256}),
        ),
    ]
}

/// The Rust target section of the service health: versions and the fixed asset descriptors,
/// including the pinned rustfmt hash. It reads no file, so health stays the same while the
/// service runs.
pub fn health() -> Value {
    let mut value = json!({"bundle_version": BUNDLE_VERSION, "dsl_version": DSL_VERSION,
        "formatter_version": format::FORMATTER_ID, "formatter_sha256": format::RUSTFMT_SHA256});
    value["assets"] = Value::Object(
        rust_descriptors()
            .into_iter()
            .map(|(name, item)| (name.to_owned(), item))
            .collect(),
    );
    value
}

/// The prompt value a dotted catalog name points at.
fn lookup<'a>(prompt: &'a Value, dotted: &str) -> Option<&'a Value> {
    prompt.pointer(&format!("/{}", dotted.replace('.', "/")))
}

/// The frozen Rust DSL for a prompt that passed validation and the profile.
/// Block params keep their {"$ref": name} form. The assembler resolves them from the prompt.
pub fn plan(prompt: &Value) -> Result<Value, String> {
    let version = prompt["version"].as_str().unwrap_or_default();
    let mut files = Vec::new();
    for file in CATALOG["files"].as_array().expect("catalog files") {
        let blocks: Vec<Value> = file["blocks"]
            .as_array()
            .expect("catalog blocks")
            .iter()
            .filter(|block| block["versions"].as_array().is_some_and(|rows| rows.iter().any(|row| row == version)))
            .map(|block| {
                json!({"id": block["id"], "name": block["name"], "params": block["params"], "refs": block["refs"]})
            })
            .collect();
        if blocks.is_empty() {
            continue;
        }
        let template = file["path"].as_str().expect("catalog path");
        let path = match (template.find('{'), template.find('}')) {
            (Some(open), Some(close)) => {
                let name = &template[open + 1..close];
                let value = lookup(prompt, name)
                    .and_then(Value::as_str)
                    .ok_or_else(|| format!("the prompt has no {name} for {template}"))?;
                format!("{}{value}{}", &template[..open], &template[close + 1..])
            }
            _ => template.to_owned(),
        };
        files.push(json!({"path": path, "blocks": blocks}));
    }
    if files.is_empty() {
        return Err(format!("the Rust catalog has no plan for {version}"));
    }
    Ok(json!({"version": DSL_VERSION, "files": files}))
}

/// Block params with every {"$ref": name} replaced by the prompt value. A missing value is null.
fn resolve(params: &Value, prompt: &Value) -> Value {
    let Some(members) = params.as_object() else {
        return params.clone();
    };
    let resolved: Map<String, Value> = members
        .iter()
        .map(|(key, value)| {
            let found = match value.get("$ref").and_then(Value::as_str) {
                Some(name) if value.as_object().is_some_and(|object| object.len() == 1) => {
                    lookup(prompt, name).cloned().unwrap_or(Value::Null)
                }
                _ => value.clone(),
            };
            (key.clone(), found)
        })
        .collect();
    Value::Object(resolved)
}

fn block_count(dsl: &Value) -> usize {
    dsl["files"].as_array().map_or(0, |files| {
        files
            .iter()
            .map(|file| file["blocks"].as_array().map_or(0, Vec::len))
            .sum()
    })
}

/// Step 6 of a Rust compile: the bundle for a prompt that passed validation and the profile.
pub fn compile(compiler: &Compiler, doc: &Document, prompt: &Value, pointer: &str) -> Reply {
    let dsl = match plan(prompt) {
        Ok(dsl) => dsl,
        Err(cause) => {
            return rejected(doc.error(
                pointer,
                "OUTPUT_QUALITY_FAILED",
                &cause,
                "A Rust phase 1 prompt.",
            ));
        }
    };
    let too_large = |cause: &str| {
        rejected(doc.error(
            pointer,
            "SCOPE_TOO_LARGE",
            cause,
            "A Rust plan with at most 12 files and 64 blocks whose bundle fits the assemble limit.",
        ))
    };
    if dsl["files"].as_array().map_or(0, Vec::len) > MAX_FILES {
        return too_large("The Rust plan has more than 12 files.");
    }
    if block_count(&dsl) > MAX_BLOCKS {
        return too_large("The Rust plan has more than 64 blocks.");
    }
    let mut bundle = json!({
        "version": BUNDLE_VERSION,
        "target": TARGET,
        "prompt": prompt,
        "dsl": dsl,
        "assets": descriptors(compiler, prompt),
    });
    let payload = canonical_hash(&bundle);
    bundle["hashes"] = json!({
        "prompt_sha256": canonical_hash(prompt),
        "dsl_sha256": canonical_hash(&bundle["dsl"]),
        "payload_sha256": payload,
    });
    if som::flow::canonical(&json!({"bundle": bundle})).len() > ASSEMBLE_BODY_LIMIT {
        return too_large("The complete Rust bundle exceeds the assemble transport limit.");
    }
    (200, json!({"status": "ok", "bundle": bundle}))
}

// ---------------------------------------------------------------- bundle schema

/// A schema refusal: the pointer under /bundle and the cause.
type Refused = (String, String);

fn escape(key: &str) -> String {
    key.replace('~', "~0").replace('/', "~1")
}

fn refuse<T>(pointer: &str, cause: impl Into<String>) -> Result<T, Refused> {
    Err((pointer.to_owned(), cause.into()))
}

/// Exactly the listed members: an unknown member first, then a missing one.
fn members<'a>(
    value: &'a Value,
    pointer: &str,
    keys: &[&str],
    what: &str,
) -> Result<&'a Map<String, Value>, Refused> {
    let Some(object) = value.as_object() else {
        return refuse(pointer, format!("{what} must be an object."));
    };
    if let Some(extra) = object.keys().find(|key| !keys.contains(&key.as_str())) {
        return refuse(
            &format!("{pointer}/{}", escape(extra)),
            format!("{what} does not allow this member."),
        );
    }
    if let Some(missing) = keys.iter().find(|key| !object.contains_key(**key)) {
        return refuse(pointer, format!("{what} requires {missing}."));
    }
    Ok(object)
}

fn sha256(value: &Value) -> bool {
    value.as_str().is_some_and(|text| {
        text.len() == 64
            && text
                .bytes()
                .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    })
}

fn identifier(text: &str) -> bool {
    let mut bytes = text.bytes();
    bytes
        .next()
        .is_some_and(|first| first.is_ascii_alphabetic() || first == b'_')
        && bytes.all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

fn layout_path(path: &str) -> bool {
    LAYOUT.contains(&path)
        || path
            .strip_prefix("migrations/0001_")
            .and_then(|rest| rest.strip_suffix(".sql"))
            .is_some_and(identifier)
}

fn check_descriptor(value: &Value, pointer: &str) -> Result<(), Refused> {
    let object = members(value, pointer, &["id", "sha256"], "An asset descriptor")?;
    if object["id"].as_str().is_none_or(str::is_empty) {
        return refuse(
            &format!("{pointer}/id"),
            "A descriptor id must be a non-empty string.",
        );
    }
    if !sha256(&object["sha256"]) {
        return refuse(
            &format!("{pointer}/sha256"),
            "A descriptor sha256 must be 64 lowercase hex digits.",
        );
    }
    Ok(())
}

const ASSET_KEYS: &[&str] = &[
    "grammar",
    "compiler_contract",
    "selection_policy",
    "literal_policy",
    "serializer",
    "semantic_contracts",
    "target_contract",
    "catalog",
    "block_library",
    "backend",
    "formatter",
];

/// The som.dsl-bundle.v2 structural schema (contracts/rust-output-v1/bundle.schema.json).
pub fn check_schema(bundle: &Value) -> Result<(), Refused> {
    let root = "/bundle";
    members(
        bundle,
        root,
        &["version", "target", "prompt", "dsl", "assets", "hashes"],
        "A Rust bundle",
    )?;
    if bundle["version"] != BUNDLE_VERSION {
        return refuse(
            "/bundle/version",
            "The bundle version must be som.dsl-bundle.v2.",
        );
    }
    if bundle["target"] != TARGET {
        return refuse(
            "/bundle/target",
            "A som.dsl-bundle.v2 bundle must have target rust.",
        );
    }
    if let Err(pointer) = prompt_schema::check(&bundle["prompt"]) {
        return refuse(
            &format!("/bundle/prompt{pointer}"),
            "The bundle prompt does not match som.compiler-prompt-family.v1.",
        );
    }
    let dsl = members(
        &bundle["dsl"],
        "/bundle/dsl",
        &["version", "files"],
        "The Rust DSL",
    )?;
    if dsl["version"] != DSL_VERSION {
        return refuse(
            "/bundle/dsl/version",
            "The DSL version must be som.dsl.rust.v1.",
        );
    }
    let files = match dsl["files"].as_array() {
        Some(files) if (1..=MAX_FILES).contains(&files.len()) => files,
        _ => return refuse("/bundle/dsl/files", "The DSL must have 1 to 12 files."),
    };
    for (index, file) in files.iter().enumerate() {
        let at = format!("/bundle/dsl/files/{index}");
        members(file, &at, &["path", "blocks"], "A DSL file")?;
        if !file["path"].as_str().is_some_and(layout_path) {
            return refuse(
                &format!("{at}/path"),
                "The file path is not a Rust product layout path.",
            );
        }
        let blocks = match file["blocks"].as_array() {
            Some(blocks) if (1..=MAX_BLOCKS).contains(&blocks.len()) => blocks,
            _ => {
                return refuse(
                    &format!("{at}/blocks"),
                    "A DSL file must have 1 to 64 blocks.",
                );
            }
        };
        for (number, block) in blocks.iter().enumerate() {
            let at = format!("{at}/blocks/{number}");
            members(block, &at, &["id", "name", "params", "refs"], "A DSL block")?;
            if block["id"].as_str().is_none_or(str::is_empty) {
                return refuse(
                    &format!("{at}/id"),
                    "A block id must be a non-empty string.",
                );
            }
            if !block["name"].as_str().is_some_and(identifier) {
                return refuse(&format!("{at}/name"), "A block name must be an identifier.");
            }
            if !block["params"].is_object() {
                return refuse(&format!("{at}/params"), "Block params must be an object.");
            }
            match block["refs"].as_array() {
                Some(refs) => {
                    if let Some(position) = refs.iter().position(|item| !item.is_string()) {
                        return refuse(
                            &format!("{at}/refs/{position}"),
                            "A block ref must be a string.",
                        );
                    }
                }
                None => return refuse(&format!("{at}/refs"), "Block refs must be an array."),
            }
        }
    }
    let assets = members(
        &bundle["assets"],
        "/bundle/assets",
        ASSET_KEYS,
        "The bundle assets",
    )?;
    for key in ASSET_KEYS {
        let at = format!("/bundle/assets/{key}");
        if *key == "semantic_contracts" {
            let Some(rows) = assets[*key].as_array() else {
                return refuse(&at, "semantic_contracts must be an array.");
            };
            for (index, row) in rows.iter().enumerate() {
                check_descriptor(row, &format!("{at}/{index}"))?;
                if rows[..index].contains(row) {
                    return refuse(
                        &format!("{at}/{index}"),
                        "semantic_contracts items must be unique.",
                    );
                }
            }
        } else {
            check_descriptor(&assets[*key], &at)?;
        }
    }
    let hashes = members(
        &bundle["hashes"],
        "/bundle/hashes",
        &["prompt_sha256", "dsl_sha256", "payload_sha256"],
        "The bundle hashes",
    )?;
    for key in ["prompt_sha256", "dsl_sha256", "payload_sha256"] {
        if !sha256(&hashes[key]) {
            return refuse(
                &format!("/bundle/hashes/{key}"),
                "A hash must be 64 lowercase hex digits.",
            );
        }
    }
    Ok(())
}

// ---------------------------------------------------------------- composition

/// Where a supplied DSL first differs from the frozen plan. Files and blocks are compared
/// in order: a different path is at the file path, a different or extra block is at its
/// block id, a missing block is at the file's block list, an extra file is at its path and
/// a missing file is at the file list.
pub fn composition_difference(supplied: &Value, expected: &Value) -> Option<String> {
    let empty = Vec::new();
    let list = |value: &Value| value.as_array().unwrap_or(&empty).clone();
    let (given, planned) = (list(&supplied["files"]), list(&expected["files"]));
    for index in 0..given.len().max(planned.len()) {
        let at = format!("/bundle/dsl/files/{index}");
        let (file, plan) = match (given.get(index), planned.get(index)) {
            (Some(file), Some(plan)) => (file, plan),
            (Some(_), None) => return Some(format!("{at}/path")),
            _ => return Some("/bundle/dsl/files".to_owned()),
        };
        if file["path"] != plan["path"] {
            return Some(format!("{at}/path"));
        }
        let (blocks, wanted) = (list(&file["blocks"]), list(&plan["blocks"]));
        for slot in 0..blocks.len().max(wanted.len()) {
            match (blocks.get(slot), wanted.get(slot)) {
                (Some(block), Some(want)) if block == want => {}
                (Some(_), _) => return Some(format!("{at}/blocks/{slot}/id")),
                (None, _) => return Some(format!("{at}/blocks")),
            }
        }
    }
    (supplied != expected).then(|| "/bundle/dsl".to_owned())
}

// ---------------------------------------------------------------- assemble

const COMPOSITION_CAUSE: &str = "The Rust DSL differs from the frozen Rust plan for this prompt.";
const COMPOSITION_ALLOWED: &str =
    "The som.dsl.rust.v1 plan that compile returns for this prompt with target rust.";

/// Render every planned file. Rust files are formatted by the pinned rustfmt.
fn render_files(
    prompt: &Value,
    dsl: &Value,
) -> Result<BTreeMap<String, String>, (&'static str, String)> {
    let mut files = BTreeMap::new();
    for file in dsl["files"].as_array().into_iter().flatten() {
        let path = file["path"].as_str().unwrap_or_default().to_owned();
        let mut parts = Vec::new();
        for block in file["blocks"].as_array().into_iter().flatten() {
            let id = block["id"].as_str().unwrap_or_default();
            let params = resolve(&block["params"], prompt);
            parts.push(
                render::render(id, &params).map_err(|cause| ("OUTPUT_QUALITY_FAILED", cause))?,
            );
        }
        let text = if path.ends_with(".rs") {
            format::rustfmt(&parts.join("\n")).map_err(|cause| ("ASSET_UNAVAILABLE", cause))?
        } else {
            parts.concat()
        };
        files.insert(path, text);
    }
    Ok(files)
}

/// Output checks: exactly the 11 layout files, Rust files formatted and non-empty.
fn check_output(files: &BTreeMap<String, String>) -> Result<(), String> {
    let migrations = files
        .keys()
        .filter(|path| path.starts_with("migrations/"))
        .count();
    if files.len() != LAYOUT.len() + 1
        || migrations != 1
        || LAYOUT.iter().any(|path| !files.contains_key(*path))
    {
        return Err("The rendered file set is not the Rust product layout.".to_owned());
    }
    if let Some((path, _)) = files.iter().find(|(_, text)| text.trim().is_empty()) {
        return Err(format!("The rendered file {path} is empty."));
    }
    Ok(())
}

/// Steps 3 to 9 of x-assemble-check-order for a som.dsl-bundle.v2 bundle.
pub fn assemble(compiler: &Compiler, doc: &Document) -> Reply {
    let bundle = &doc.value["bundle"];
    // Step 3: the bundle schema.
    if let Err((pointer, cause)) = check_schema(bundle) {
        return rejected(doc.error(
            &pointer,
            "INVALID_SCHEMA",
            &cause,
            "A som.dsl-bundle.v2 bundle that compile returned.",
        ));
    }
    let prompt = &bundle["prompt"];
    // Step 4: the hashes, in order.
    let payload = json!({
        "version": bundle["version"],
        "target": bundle["target"],
        "prompt": prompt,
        "dsl": bundle["dsl"],
        "assets": bundle["assets"],
    });
    for (key, value) in [
        ("prompt_sha256", prompt),
        ("dsl_sha256", &bundle["dsl"]),
        ("payload_sha256", &payload),
    ] {
        if bundle["hashes"][key] != canonical_hash(value) {
            return rejected(doc.error(
                &format!("/bundle/hashes/{key}"),
                "BUNDLE_HASH_MISMATCH",
                "The bundle hash does not match its canonical content.",
                "Hashes computed over som.canonical-json.v1 bytes.",
            ));
        }
    }
    // Step 5: the assets equal the active Rust manifest.
    if bundle["assets"] != descriptors(compiler, prompt) {
        return rejected(doc.error(
            "/bundle/assets",
            "ASSET_HASH_MISMATCH",
            "The bundle assets differ from the active local Rust manifest.",
            "The asset descriptors that compile returns with target rust.",
        ));
    }
    // Step 6: compiler-v1 prompt validation, unchanged.
    if let Err(error) = som::validate_prompt(prompt) {
        return rejected(som::json_diagnostics::prompt_error(
            doc,
            prompt,
            &error,
            "/bundle/prompt",
        ));
    }
    // Step 7: the Rust profile.
    if let Some(found) = profile::check(prompt) {
        return rejected(doc.error(
            &format!("/bundle/prompt{}", found.pointer),
            &found.code,
            &found.cause,
            &found.allowed_form,
        ));
    }
    // Step 8: catalog and composition against the frozen plan.
    let expected = match plan(prompt) {
        Ok(expected) => expected,
        Err(cause) => {
            return rejected(doc.error(
                "/bundle/prompt",
                "OUTPUT_QUALITY_FAILED",
                &cause,
                "A Rust phase 1 prompt.",
            ));
        }
    };
    if let Some(pointer) = composition_difference(&bundle["dsl"], &expected) {
        return rejected(doc.error(
            &pointer,
            "INVALID_COMPOSITION",
            COMPOSITION_CAUSE,
            COMPOSITION_ALLOWED,
        ));
    }
    // Step 9: render, rustfmt and output checks.
    let files = match render_files(prompt, &expected) {
        Ok(files) => files,
        Err(("ASSET_UNAVAILABLE", cause)) => {
            return rejected(doc.error(
                "/bundle/assets/formatter",
                "ASSET_UNAVAILABLE",
                &cause,
                "The pinned rustfmt 1.9.0 binary from toolchain.json.",
            ));
        }
        Err((code, cause)) => {
            return rejected(doc.error("/bundle/dsl", code, &cause, "A Rust phase 1 plan."));
        }
    };
    if let Err(cause) = check_output(&files) {
        return rejected(doc.error(
            "/bundle/dsl",
            "OUTPUT_QUALITY_FAILED",
            &cause,
            "A Rust phase 1 plan.",
        ));
    }
    (200, json!({"status": "ok", "files": files}))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::{Path, PathBuf};

    fn som_rs() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
    }

    fn example(name: &str) -> Value {
        let path = som_rs().join(format!("contracts/compiler-v1/examples/{name}.prompt.json"));
        serde_json::from_str(&std::fs::read_to_string(path).expect("example")).expect("JSON")
    }

    /// The latest receipt: the highest numbered amendment, else the freeze receipt.
    fn latest_receipt() -> Value {
        let evidence = som_rs().join("evidence");
        let mut amendments: Vec<PathBuf> = std::fs::read_dir(&evidence)
            .expect("evidence")
            .map(|entry| entry.expect("entry").path())
            .filter(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with("rust-output-contract-amendment-"))
            })
            .collect();
        amendments.sort();
        let path = amendments
            .pop()
            .unwrap_or_else(|| evidence.join("rust-output-contract-freeze-2026-10-15.json"));
        serde_json::from_str(&std::fs::read_to_string(path).expect("receipt")).expect("JSON")
    }

    #[test]
    fn target_contract_is_the_latest_receipt() {
        let receipt = latest_receipt();
        assert_eq!(canonical_hash(&receipt["files"]), TARGET_CONTRACT_SHA256);
        assert_eq!(receipt["target_contract"]["sha256"], TARGET_CONTRACT_SHA256);
        assert_eq!(receipt["target_contract"]["id"], TARGET_CONTRACT_ID);
        if receipt.get("amendment").is_some() {
            assert_eq!(receipt["independent_review"]["final_verdict"], "APPROVED");
        }
        for (path, sha) in receipt["files"].as_object().expect("files") {
            let bytes = std::fs::read(som_rs().join("..").join(path)).expect("contract file");
            assert_eq!(digest(&bytes), sha.as_str().expect("sha"), "{path}");
        }
    }

    #[test]
    fn health_reports_the_bundle_descriptors() {
        let value = health();
        assert_eq!(value["formatter_sha256"], format::RUSTFMT_SHA256);
        assert_eq!(
            value["assets"]["formatter"]["sha256"],
            format::RUSTFMT_SHA256
        );
        let assets = value["assets"].as_object().expect("assets");
        assert_eq!(assets.len(), 5);
        for (name, item) in rust_descriptors() {
            assert_eq!(assets[name], item, "{name}");
        }
    }

    #[test]
    fn plans_are_deterministic_and_bounded() {
        for name in ["v1", "v2", "v3", "v4", "v5"] {
            let prompt = example(name);
            let dsl = plan(&prompt).expect("plan");
            assert_eq!(plan(&prompt).expect("plan"), dsl, "{name}");
            assert_eq!(dsl["version"], DSL_VERSION, "{name}");
            let files = dsl["files"].as_array().expect("files");
            assert_eq!(files.len(), LAYOUT.len() + 1, "{name}");
            assert!(
                files.len() <= MAX_FILES && block_count(&dsl) <= MAX_BLOCKS,
                "{name}"
            );
            for block in files
                .iter()
                .flat_map(|file| file["blocks"].as_array().expect("blocks"))
            {
                assert!(
                    block["id"].as_str().expect("id").starts_with("rust."),
                    "{name}"
                );
            }
        }
    }

    #[test]
    fn composition_names_the_first_wrong_block_id() {
        let prompt = example("v1");
        let expected = plan(&prompt).expect("plan");
        assert_eq!(composition_difference(&expected, &expected), None);
        let at = |change: &dyn Fn(&mut Value)| {
            let mut dsl = expected.clone();
            change(&mut dsl);
            composition_difference(&dsl, &expected)
        };
        let blocks = |dsl: &mut Value, file: usize| -> Vec<Value> {
            dsl["files"][file]["blocks"]
                .as_array()
                .expect("blocks")
                .clone()
        };
        assert_eq!(
            at(&|dsl| dsl["files"][0]["blocks"][0]["id"] = json!("orm.base")).as_deref(),
            Some("/bundle/dsl/files/0/blocks/0/id")
        );
        assert_eq!(
            at(&|dsl| {
                let mut list = blocks(dsl, 0);
                list.push(list[0].clone());
                dsl["files"][0]["blocks"] = json!(list);
            })
            .as_deref(),
            Some(
                format!(
                    "/bundle/dsl/files/0/blocks/{}/id",
                    blocks(&mut expected.clone(), 0).len()
                )
                .as_str()
            )
        );
        let last = expected["files"].as_array().expect("files").len() - 1;
        assert_eq!(
            at(&|dsl| {
                let mut list = blocks(dsl, last);
                list.pop();
                dsl["files"][last]["blocks"] = json!(list);
            })
            .as_deref(),
            Some(format!("/bundle/dsl/files/{last}/blocks").as_str())
        );
        assert_eq!(
            at(&|dsl| dsl["files"][4]["blocks"][0]["params"] = json!({})).as_deref(),
            Some("/bundle/dsl/files/4/blocks/0/id")
        );
        assert_eq!(
            at(&|dsl| dsl["files"][1]["path"] = json!("README.md")).as_deref(),
            Some("/bundle/dsl/files/1/path")
        );
        assert_eq!(
            at(&|dsl| {
                dsl["files"].as_array_mut().expect("files").pop();
            })
            .as_deref(),
            Some("/bundle/dsl/files")
        );
    }

    #[test]
    fn schema_refuses_a_bundle_without_members() {
        assert!(check_schema(&json!({})).is_err());
        assert!(check_schema(&json!([])).is_err());
    }

    #[test]
    fn rendering_is_byte_identical_and_complete() {
        for name in ["v1", "v2", "v3", "v4", "v5"] {
            let prompt = example(name);
            let dsl = plan(&prompt).expect("plan");
            let first = render_files(&prompt, &dsl).expect("render");
            assert_eq!(
                render_files(&prompt, &dsl).expect("render"),
                first,
                "{name}"
            );
            check_output(&first).expect("layout");
            let (_, migration) = first
                .iter()
                .find(|(path, _)| path.starts_with("migrations/"))
                .expect("migration");
            // Amendment 1: v3 and v4 timestamps default to CURRENT_TIMESTAMP, as in Python.
            assert_eq!(
                migration.contains("DEFAULT CURRENT_TIMESTAMP"),
                matches!(name, "v3" | "v4"),
                "{name}"
            );
            // The v5 Python migration uses now(), so the v5 tables do too.
            assert_eq!(migration.contains("DEFAULT now()"), name == "v5", "{name}");
        }
    }

    #[test]
    fn output_check_refuses_a_missing_or_empty_file() {
        let prompt = example("v1");
        let mut files = render_files(&prompt, &plan(&prompt).expect("plan")).expect("render");
        files.insert("README.md".to_owned(), " \n".to_owned());
        assert!(check_output(&files).is_err());
        files.remove("README.md");
        assert!(check_output(&files).is_err());
    }
}
