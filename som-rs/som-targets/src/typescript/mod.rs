//! TypeScript phase 1 target: bundle emission, catalog composition, assemble checks and rendering (issues #65, #74 and #75).
pub mod format;
pub mod profile;
pub mod render;

use crate::{Reply, rejected};
use serde_json::{Map, Value, json};
use som::{
    compiler::{ASSEMBLE_BODY_LIMIT, Compiler, canonical_hash, digest},
    strict_json::Document,
};
use std::collections::BTreeMap;
use std::sync::LazyLock;

pub const BUNDLE_VERSION: &str = "som.dsl-bundle.v2";
pub const DSL_VERSION: &str = "som.dsl.typescript.v1";
pub const TARGET: &str = "typescript";
pub const MAX_FILES: usize = 12;
pub const MAX_BLOCKS: usize = 64;

pub const TARGET_CONTRACT_ID: &str = "som.typescript-output-contract.v1";
/// H(files map) of the latest sealed receipt for contracts/typescript-output-v1: amendment 1.
pub const TARGET_CONTRACT_SHA256: &str =
    "a1ed9978d7ddd0123e22a1e388f1a6e889ca63e43752563cd744733b84c103b4";
pub const CATALOG_ID: &str = "som.typescript-catalog.v1";
pub const BLOCK_LIBRARY_ID: &str = "som.typescript-block-library.v1";
pub const BACKEND_ID: &str = "som.typescript-backend.v1";

pub const CATALOG_TEXT: &str =
    include_str!("../../../contracts/typescript-output-v1/catalog/typescript-catalog.v1.json");
const BLOCK_LIBRARY_TEXT: &str =
    concat!(include_str!("render.rs"), include_str!("render/related.rs"),);

const BACKEND_FILES: &[(&str, &str)] = &[
    ("som-targets/Cargo.toml", include_str!("../../Cargo.toml")),
    ("som-targets/src/lib.rs", include_str!("../lib.rs")),
    ("som-targets/src/main.rs", include_str!("../main.rs")),
    ("som-targets/src/profile.rs", include_str!("../profile.rs")),
    (
        "som-targets/src/typescript/format.rs",
        include_str!("format.rs"),
    ),
    ("som-targets/src/typescript/mod.rs", include_str!("mod.rs")),
    (
        "som-targets/src/typescript/profile.rs",
        include_str!("profile.rs"),
    ),
    (
        "som-targets/src/typescript/render.rs",
        include_str!("render.rs"),
    ),
    (
        "som-targets/src/typescript/render/related.rs",
        include_str!("render/related.rs"),
    ),
];

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

const FIXED_FILES: &[&str] = &[
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "src/main.ts",
    "src/app.ts",
    "src/schemas.ts",
    "src/db.ts",
    "src/migrate.ts",
    "test/api.test.ts",
    "README.md",
];

static CATALOG: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(CATALOG_TEXT).expect("frozen typescript-catalog.v1.json")
});

fn raw_described(id: &str, text: &str) -> Value {
    json!({"id": id, "sha256": digest(text.as_bytes())})
}

fn described(id: &str, value: &Value) -> Value {
    json!({"id": id, "sha256": canonical_hash(value)})
}

fn backend_descriptor() -> Value {
    let mut files = Map::new();
    for (path, source) in BACKEND_FILES {
        files.insert((*path).to_owned(), Value::String(digest(source.as_bytes())));
    }
    described(BACKEND_ID, &json!({"version": BACKEND_ID, "files": files}))
}

/// The 11 asset descriptors of a som.dsl-bundle.v2 TypeScript bundle.
pub fn manifest(compiler: &Compiler, prompt: &Value) -> Value {
    let mut assets = compiler.descriptors(prompt);
    assets["target_contract"] = json!({"id": TARGET_CONTRACT_ID, "sha256": TARGET_CONTRACT_SHA256});
    assets["catalog"] = raw_described(CATALOG_ID, CATALOG_TEXT);
    assets["block_library"] = raw_described(BLOCK_LIBRARY_ID, BLOCK_LIBRARY_TEXT);
    assets["backend"] = backend_descriptor();
    assets["formatter"] = format::descriptor();
    assets
}

// ---------------------------------------------------------------- composition

/// Deterministic TypeScript DSL composition plan for a validated phase 1 prompt.
/// Block params keep their {"$ref": name} form; the assembler resolves them from the prompt.
pub fn plan(prompt: &Value) -> Value {
    let version = prompt["version"].as_str().unwrap_or_default();
    let revision = prompt["migration"]["revision"].as_str().unwrap_or_default();
    let mut files = Vec::new();
    for file in CATALOG["files"].as_array().expect("catalog files") {
        let mut blocks = Vec::new();
        for block in file["blocks"].as_array().expect("catalog blocks") {
            let in_version = block["versions"]
                .as_array()
                .is_some_and(|list| list.iter().any(|item| item == version));
            if in_version {
                blocks.push(json!({
                    "id": block["id"],
                    "name": block["name"],
                    "params": block["params"],
                    "refs": block["refs"],
                }));
            }
        }
        if !blocks.is_empty() {
            let path = file["path"]
                .as_str()
                .expect("catalog file path")
                .replace("{migration.revision}", revision);
            files.push(json!({"path": path, "blocks": blocks}));
        }
    }
    json!({"version": DSL_VERSION, "files": files})
}

fn within_plan_limits(dsl: &Value) -> bool {
    let Some(files) = dsl["files"].as_array() else {
        return false;
    };
    if files.is_empty() || files.len() > MAX_FILES {
        return false;
    }
    let mut total_blocks = 0usize;
    for file in files {
        let Some(blocks) = file["blocks"].as_array() else {
            return false;
        };
        if blocks.is_empty() || blocks.len() > MAX_BLOCKS {
            return false;
        }
        total_blocks += blocks.len();
    }
    total_blocks <= MAX_BLOCKS
}

fn payload_hash(prompt: &Value, dsl: &Value, assets: &Value) -> String {
    canonical_hash(&json!({
        "version": BUNDLE_VERSION,
        "target": TARGET,
        "prompt": prompt,
        "dsl": dsl,
        "assets": assets,
    }))
}

pub fn compile(compiler: &Compiler, doc: &Document, prompt: &Value, pointer: &str) -> Reply {
    compile_with_manifest(doc, prompt, pointer, manifest(compiler, prompt))
}

fn compile_with_manifest(doc: &Document, prompt: &Value, pointer: &str, assets: Value) -> Reply {
    let dsl = plan(prompt);
    if !within_plan_limits(&dsl) {
        return rejected(doc.error(
            pointer,
            "SCOPE_TOO_LARGE",
            "TypeScript DSL plan exceeds the phase 1 file or block limit.",
            "At most 12 files and 64 blocks.",
        ));
    }
    let bundle = json!({
        "version": BUNDLE_VERSION,
        "target": TARGET,
        "prompt": prompt,
        "dsl": dsl,
        "assets": assets,
        "hashes": {
            "prompt_sha256": canonical_hash(prompt),
            "dsl_sha256": canonical_hash(&dsl),
            "payload_sha256": payload_hash(prompt, &dsl, &assets),
        }
    });
    if som::flow::canonical(&json!({"bundle": bundle})).len() > ASSEMBLE_BODY_LIMIT {
        return rejected(doc.error(
            pointer,
            "SCOPE_TOO_LARGE",
            "Compiled TypeScript bundle exceeds the assemble request limit.",
            "A prompt whose canonical assemble request is at most 262144 bytes.",
        ));
    }
    (200, json!({"status": "ok", "bundle": bundle}))
}

// ---------------------------------------------------------------- assemble

/// Assemble a som.dsl-bundle.v2 TypeScript bundle in the frozen x-assemble-check-order (steps 1-7).
pub fn assemble(compiler: &Compiler, doc: &Document) -> Reply {
    if let Err(error) = check_schema(doc) {
        return rejected(error);
    }
    let bundle = &doc.value["bundle"];
    if let Err(error) = check_hashes(doc, bundle) {
        return rejected(error);
    }
    let prompt = &bundle["prompt"];
    if bundle["assets"] != manifest(compiler, prompt) {
        return rejected(doc.error(
            "/bundle/assets",
            "ASSET_HASH_MISMATCH",
            "Bundle asset descriptors do not match the active TypeScript compiler assets.",
            "Asset descriptors from the current compiler and TypeScript target.",
        ));
    }
    if let Err(error) = som::validate_prompt(prompt) {
        return rejected(som::json_diagnostics::prompt_error(
            doc,
            prompt,
            &error,
            "/bundle/prompt",
        ));
    }
    if let Some(found) = profile::check(prompt) {
        return rejected(doc.error(
            &format!("/bundle/prompt{}", found.pointer),
            &found.code,
            &found.cause,
            &found.allowed_form,
        ));
    }
    let expected = plan(prompt);
    if let Err(error) = check_composition(doc, &bundle["dsl"], &expected) {
        return rejected(error);
    }
    let mut files = match render::files(prompt, &bundle["dsl"]) {
        Ok(files) => files,
        Err(detail) => {
            return (
                500,
                doc.error(
                    "/bundle/dsl/files",
                    "OUTPUT_QUALITY_FAILED",
                    &detail,
                    "A complete rendered TypeScript product.",
                ),
            );
        }
    };
    if let Err(detail) = format::prettier(&mut files) {
        return (
            503,
            doc.error(
                "/bundle/assets/formatter",
                "ASSET_UNAVAILABLE",
                &detail,
                "The pinned prettier 3.9.10 installation verified through SOM_TS_PRETTIER.",
            ),
        );
    }
    if let Err(detail) = check_output(&files, prompt) {
        return (
            500,
            doc.error(
                "/bundle/dsl/files",
                "OUTPUT_QUALITY_FAILED",
                &detail,
                "The 11 non-empty files of contracts/typescript-output-v1/product-layout.json.",
            ),
        );
    }
    (200, json!({"status": "ok", "files": files}))
}

type Refused = (String, String);

fn escape(key: &str) -> String {
    key.replace('~', "~0").replace('/', "~1")
}

fn refuse<T>(pointer: &str, cause: impl Into<String>) -> Result<T, Refused> {
    Err((pointer.to_owned(), cause.into()))
}

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

fn sha256_str(value: &Value) -> bool {
    value.as_str().is_some_and(|text| {
        text.len() == 64
            && text
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}

fn identifier(text: &str) -> bool {
    let mut bytes = text.bytes();
    bytes
        .next()
        .is_some_and(|first| first.is_ascii_alphabetic() || first == b'_')
        && bytes.all(|b| b.is_ascii_alphanumeric() || b == b'_')
}

fn valid_migration(path: &str) -> bool {
    path.strip_prefix("migrations/0001_")
        .and_then(|rest| rest.strip_suffix(".sql"))
        .is_some_and(identifier)
}

fn valid_file_path(path: &str) -> bool {
    FIXED_FILES.contains(&path) || valid_migration(path)
}

fn check_descriptor(value: &Value, pointer: &str) -> Result<(), Refused> {
    let object = members(value, pointer, &["id", "sha256"], "An asset descriptor")?;
    if object["id"].as_str().is_none_or(str::is_empty) {
        return refuse(
            &format!("{pointer}/id"),
            "A descriptor id must be a non-empty string.",
        );
    }
    if !sha256_str(&object["sha256"]) {
        return refuse(
            &format!("{pointer}/sha256"),
            "A descriptor sha256 must be 64 lowercase hex digits.",
        );
    }
    Ok(())
}

fn check_schema_inner(bundle: &Value) -> Result<(), Refused> {
    members(
        bundle,
        "/bundle",
        &["version", "target", "prompt", "dsl", "assets", "hashes"],
        "A TypeScript bundle",
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
            "A TypeScript bundle must have target typescript.",
        );
    }
    if let Err(pointer) = crate::rust::prompt_schema::check(&bundle["prompt"]) {
        return refuse(
            &format!("/bundle/prompt{pointer}"),
            "The bundle prompt does not match som.compiler-prompt-family.v1.",
        );
    }
    let dsl = members(
        &bundle["dsl"],
        "/bundle/dsl",
        &["version", "files"],
        "The TypeScript DSL",
    )?;
    if dsl["version"] != DSL_VERSION {
        return refuse(
            "/bundle/dsl/version",
            "The DSL version must be som.dsl.typescript.v1.",
        );
    }
    let files = match dsl["files"].as_array() {
        Some(files) if (1..=MAX_FILES).contains(&files.len()) => files,
        _ => return refuse("/bundle/dsl/files", "The DSL must have 1 to 12 files."),
    };
    let mut total_blocks = 0usize;
    for (file_index, file) in files.iter().enumerate() {
        let base = format!("/bundle/dsl/files/{file_index}");
        members(file, &base, &["path", "blocks"], "A DSL file")?;
        if !file["path"].as_str().is_some_and(valid_file_path) {
            return refuse(
                &format!("{base}/path"),
                "The file path is not a TypeScript product layout path.",
            );
        }
        let blocks = match file["blocks"].as_array() {
            Some(blocks) if (1..=MAX_BLOCKS).contains(&blocks.len()) => blocks,
            _ => {
                return refuse(
                    &format!("{base}/blocks"),
                    "A DSL file must have 1 to 64 blocks.",
                );
            }
        };
        for (block_index, block) in blocks.iter().enumerate() {
            let block_ptr = format!("{base}/blocks/{block_index}");
            members(
                block,
                &block_ptr,
                &["id", "name", "params", "refs"],
                "A DSL block",
            )?;
            if block["id"].as_str().is_none_or(str::is_empty) {
                return refuse(
                    &format!("{block_ptr}/id"),
                    "A block id must be a non-empty string.",
                );
            }
            if !block["name"].as_str().is_some_and(identifier) {
                return refuse(
                    &format!("{block_ptr}/name"),
                    "A block name must be an identifier.",
                );
            }
            if !block["params"].is_object() {
                return refuse(
                    &format!("{block_ptr}/params"),
                    "Block params must be an object.",
                );
            }
            match block["refs"].as_array() {
                Some(refs) => {
                    if let Some(position) = refs.iter().position(|item| !item.is_string()) {
                        return refuse(
                            &format!("{block_ptr}/refs/{position}"),
                            "A block ref must be a string.",
                        );
                    }
                }
                None => {
                    return refuse(&format!("{block_ptr}/refs"), "Block refs must be an array.");
                }
            }
        }
        total_blocks += blocks.len();
    }
    let assets = members(
        &bundle["assets"],
        "/bundle/assets",
        ASSET_KEYS,
        "The bundle assets",
    )?;
    for key in ASSET_KEYS {
        let ptr = format!("/bundle/assets/{key}");
        if *key == "semantic_contracts" {
            let Some(rows) = assets[*key].as_array() else {
                return refuse(&ptr, "semantic_contracts must be an array.");
            };
            for (index, row) in rows.iter().enumerate() {
                check_descriptor(row, &format!("{ptr}/{index}"))?;
                if rows[..index].contains(row) {
                    return refuse(
                        &format!("{ptr}/{index}"),
                        "semantic_contracts items must be unique.",
                    );
                }
            }
        } else {
            check_descriptor(&assets[*key], &ptr)?;
        }
    }
    let hashes = members(
        &bundle["hashes"],
        "/bundle/hashes",
        &["prompt_sha256", "dsl_sha256", "payload_sha256"],
        "The bundle hashes",
    )?;
    for key in ["prompt_sha256", "dsl_sha256", "payload_sha256"] {
        if !sha256_str(&hashes[key]) {
            return refuse(
                &format!("/bundle/hashes/{key}"),
                "A hash must be 64 lowercase hex digits.",
            );
        }
    }
    if total_blocks > MAX_BLOCKS {
        return refuse(
            "/bundle/dsl/files",
            "DSL files exceed the maximum total of 64 blocks.",
        );
    }
    Ok(())
}

fn check_schema(doc: &Document) -> Result<(), Value> {
    check_schema_inner(&doc.value["bundle"]).map_err(|(pointer, cause)| {
        doc.error(
            &pointer,
            "INVALID_SCHEMA",
            &cause,
            "A som.dsl-bundle.v2 TypeScript bundle that compile returned.",
        )
    })
}

fn check_hashes(doc: &Document, bundle: &Value) -> Result<(), Value> {
    let prompt = &bundle["prompt"];
    let dsl = &bundle["dsl"];
    let assets = &bundle["assets"];
    for (key, expected) in [
        ("prompt_sha256", canonical_hash(prompt)),
        ("dsl_sha256", canonical_hash(dsl)),
        ("payload_sha256", payload_hash(prompt, dsl, assets)),
    ] {
        if bundle["hashes"][key].as_str() != Some(expected.as_str()) {
            return Err(doc.error(
                &format!("/bundle/hashes/{key}"),
                "BUNDLE_HASH_MISMATCH",
                &format!("Bundle {key} does not match the canonical content."),
                &expected,
            ));
        }
    }
    Ok(())
}

fn check_composition(doc: &Document, actual: &Value, expected: &Value) -> Result<(), Value> {
    if let Some(pointer) = crate::rust::composition_difference(actual, expected) {
        return Err(composition_error(doc, &pointer));
    }
    Ok(())
}

fn composition_error(doc: &Document, pointer: &str) -> Value {
    doc.error(
        pointer,
        "INVALID_COMPOSITION",
        "TypeScript DSL composition does not match the deterministic plan for this prompt.",
        "The unmodified DSL emitted by /v1/compile for target typescript.",
    )
}

fn check_output(files: &BTreeMap<String, String>, prompt: &Value) -> Result<(), String> {
    let revision = prompt["migration"]["revision"].as_str().unwrap_or_default();
    let migration = format!("migrations/0001_{revision}.sql");
    if files.len() != FIXED_FILES.len() + 1 {
        return Err(format!(
            "expected {} product files, rendered {}",
            FIXED_FILES.len() + 1,
            files.len()
        ));
    }
    for path in FIXED_FILES
        .iter()
        .copied()
        .chain(std::iter::once(migration.as_str()))
    {
        match files.get(path) {
            Some(content) if !content.trim().is_empty() => {}
            Some(_) => return Err(format!("rendered file {path} is empty")),
            None => return Err(format!("missing rendered file {path}")),
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Target;
    use som::strict_json;
    use std::{
        collections::BTreeSet,
        fs,
        path::{Path, PathBuf},
    };

    const TS_NEGATIVE_CASES: &str =
        include_str!("../../../contracts/typescript-output-v1/negative-cases.json");
    const TS_TARGET_EXAMPLES: &str =
        include_str!("../../../contracts/typescript-output-v1/target-examples.json");
    const TS_ACCEPTANCE_PLAN: &str =
        include_str!("../../../contracts/typescript-output-v1/acceptance-plan.json");
    const RUST_NEGATIVE_CASES: &str =
        include_str!("../../../contracts/rust-output-v1/negative-cases.json");

    fn som_rs() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
    }

    fn compiler() -> Compiler {
        let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
        for ancestor in manifest_dir.ancestors() {
            let candidate = ancestor.join("som-rs/compiler-assets/current");
            if candidate.join("manifest.json").is_file() {
                return Compiler::load(&candidate).expect("load compiler assets");
            }
            let dot_git = ancestor.join(".git");
            if dot_git.is_file()
                && let Ok(text) = fs::read_to_string(&dot_git)
                && let Some(gitdir) = text.strip_prefix("gitdir:")
            {
                let git_path = Path::new(gitdir.trim());
                for git_anc in git_path.ancestors() {
                    let git_candidate = git_anc.join("som-rs/compiler-assets/current");
                    if git_candidate.join("manifest.json").is_file() {
                        return Compiler::load(&git_candidate).expect("load compiler assets");
                    }
                }
            }
        }
        panic!(
            "som-rs/compiler-assets/current not found in any ancestor of {}",
            manifest_dir.display()
        );
    }

    fn require_prettier_env() -> String {
        let val = std::env::var(format::PRETTIER_ENV).unwrap_or_else(|_| {
            panic!(
                "{} is not set: prettier-dependent TypeScript tests require {} to point to the verified node_modules/prettier directory and never skip",
                format::PRETTIER_ENV,
                format::PRETTIER_ENV
            )
        });
        assert!(
            !val.trim().is_empty(),
            "{} is empty: prettier-dependent TypeScript tests require a non-empty path",
            format::PRETTIER_ENV
        );
        val
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
    fn target_contract_is_the_latest_receipt() {
        let path = som_rs().join("evidence/typescript-output-contract-amendment-001.json");
        let receipt: Value =
            serde_json::from_str(&fs::read_to_string(path).expect("receipt")).expect("JSON");
        assert_eq!(canonical_hash(&receipt["files"]), TARGET_CONTRACT_SHA256);
        assert_eq!(receipt["target_contract"]["sha256"], TARGET_CONTRACT_SHA256);
        assert_eq!(receipt["target_contract"]["id"], TARGET_CONTRACT_ID);
        assert_eq!(receipt["independent_review"]["final_verdict"], "APPROVED");
        for (path, sha) in receipt["files"].as_object().expect("files") {
            let bytes = fs::read(som_rs().join("..").join(path)).expect("contract file");
            assert_eq!(digest(&bytes), sha.as_str().expect("sha"), "{path}");
        }
    }

    #[test]
    fn select_parses_all_supported_targets_and_rejects_bad_values() {
        let omitted = strict_json::parse(r#"{"format":"som.cnl.v1","source":"x"}"#).unwrap();
        assert_eq!(crate::select(&omitted).unwrap(), Target::Python);

        let python =
            strict_json::parse(r#"{"format":"som.cnl.v1","source":"x","target":"python"}"#)
                .unwrap();
        assert_eq!(crate::select(&python).unwrap(), Target::Python);

        let rust =
            strict_json::parse(r#"{"format":"som.cnl.v1","source":"x","target":"rust"}"#).unwrap();
        assert_eq!(crate::select(&rust).unwrap(), Target::Rust);

        let ts =
            strict_json::parse(r#"{"format":"som.cnl.v1","source":"x","target":"typescript"}"#)
                .unwrap();
        assert_eq!(crate::select(&ts).unwrap(), Target::TypeScript);

        let bad =
            strict_json::parse(r#"{"format":"som.cnl.v1","source":"x","target":"ts"}"#).unwrap();
        let err = crate::select(&bad).unwrap_err();
        assert_eq!(err["code"], "INVALID_SCHEMA");
        assert_eq!(err["diagnostics"][0]["prompt_pointer"], "/target");
        assert_eq!(err["diagnostics"][0]["cause"], crate::TARGET_CAUSE);
        assert_eq!(err["diagnostics"][0]["allowed_form"], crate::TARGET_ALLOWED);
    }

    #[test]
    fn typescript_negative_cases_and_python_controls_match_the_frozen_contract() {
        let comp = compiler();
        let data: Value = serde_json::from_str(TS_NEGATIVE_CASES).expect("negative-cases.json");
        let cases = data["cases"].as_array().expect("cases array");
        assert_eq!(cases.len(), 33, "expected 33 negative cases (TT, TS, TP)");

        for case in cases {
            let id = case["id"].as_str().expect("id");
            let body = case["body"].as_str().expect("request body");
            let form = case["form"].as_str().expect("form");
            let expected_status = case["http_status"].as_u64().expect("http_status") as u16;
            let expected = &case["expected"];

            let (status, reply) = crate::compile(&comp, body);
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
            let (ctrl_status, ctrl_reply) = crate::compile(&comp, control_body);
            if control_expected.starts_with("HTTP 200") {
                assert_eq!(
                    ctrl_status, 200,
                    "{id}: python control must succeed: {ctrl_reply}"
                );
                assert_eq!(ctrl_reply["status"], "ok", "{id}: python_control ok");
                assert_eq!(
                    ctrl_reply["bundle"]["version"],
                    som::compiler::BUNDLE_VERSION
                );
            } else {
                assert!(
                    control_expected.starts_with("the same diagnostic"),
                    "{id}: unexpected python_control_expected {control_expected}"
                );
                assert_one_diagnostic(ctrl_status, &ctrl_reply, expected_status, expected, id);
                let py_span = located(control_body, form, &ctrl_reply["diagnostics"][0]);
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
        let comp = compiler();
        let data: Value = serde_json::from_str(RUST_NEGATIVE_CASES).expect("rust negative-cases");
        let cases = data["cases"].as_array().expect("cases array");
        let rt_cases: Vec<&Value> = cases
            .iter()
            .filter(|c| c["id"].as_str().is_some_and(|id| id.starts_with("RT")))
            .collect();
        assert_eq!(rt_cases.len(), 7);
        for case in rt_cases {
            let id = case["id"].as_str().expect("id");
            let body = case["body"].as_str().expect("request body");
            let form = case["form"].as_str().expect("form");
            let expected_status = case["http_status"].as_u64().expect("http_status") as u16;
            let expected = &case["expected"];

            let (status, reply) = crate::compile(&comp, body);
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
            let (py_status, py_reply) = crate::compile(&comp, control_body);
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
        let inventory: Value =
            serde_json::from_str(&fs::read_to_string(inventory_path).expect("case-inventory.json"))
                .expect("JSON");
        let repo = som_rs().join("..");

        for row in inventory["public_examples"]
            .as_array()
            .expect("public_examples")
        {
            let id = row["id"].as_str().expect("id");
            let prompt_path = repo.join(row["prompt"].as_str().expect("prompt"));
            let prompt: Value =
                serde_json::from_str(&fs::read_to_string(prompt_path).expect("prompt file"))
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
            &fs::read_to_string(fixtures_path).expect("cnl-positive-fixtures.json"),
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
        let cov_index: Value =
            serde_json::from_str(&fs::read_to_string(&cov_index_path).expect("cases/index.json"))
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
                &fs::read_to_string(cov_dir.join(case["prompt"].as_str().expect("prompt")))
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

    fn catalog_block_ids() -> BTreeSet<String> {
        CATALOG["files"]
            .as_array()
            .expect("catalog files")
            .iter()
            .flat_map(|f| f["blocks"].as_array().expect("catalog blocks"))
            .map(|b| b["id"].as_str().expect("block id").to_owned())
            .collect()
    }

    fn write_product_dir(root: &Path, case_id: &str, files: &Map<String, Value>) {
        let dir = root.join(case_id);
        fs::create_dir_all(&dir).expect("create product dir");
        for (rel, content) in files {
            let dest = dir.join(rel);
            if let Some(parent) = dest.parent() {
                fs::create_dir_all(parent).expect("create parent dir");
            }
            fs::write(&dest, content.as_str().expect("file string")).expect("write product file");
        }
    }

    #[test]
    fn target_examples_and_name_cases_compile_to_valid_typescript_bundles() {
        let _guard = format::TEST_ENV_LOCK
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let _prettier = require_prettier_env();
        let comp = compiler();
        let catalog_ids = catalog_block_ids();
        let data: Value = serde_json::from_str(TS_TARGET_EXAMPLES).expect("target-examples.json");
        let examples = data["examples"].as_array().expect("examples");
        let name_cases = data["name_cases"].as_array().expect("name_cases");
        let mut by_source: BTreeMap<String, Value> = BTreeMap::new();
        let write_root = std::env::var("SOM_TS_WRITE_PRODUCTS_DIR")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .map(PathBuf::from);
        let mut manifest_entries: Vec<Value> = Vec::new();
        let mut written_ids: BTreeSet<String> = BTreeSet::new();

        for item in examples.iter().chain(name_cases.iter()) {
            let id = item["id"].as_str().expect("example id");
            let target = item["target"].as_str().expect("target");
            let body = item["body"].as_str().expect("body");
            let (status, reply) = crate::compile(&comp, body);
            let (status2, reply2) = crate::compile(&comp, body);
            assert_eq!(status, status2, "{id}: compile status determinism");
            assert_eq!(
                som::flow::canonical(&reply),
                som::flow::canonical(&reply2),
                "{id}: two compiles must return byte-identical responses"
            );

            if target == "python" {
                assert_eq!(status, 200, "{id}: python target example must return 200");
                let same_as = item["same_as_body"].as_str().expect("same_as_body");
                let (same_status, same_reply) = crate::compile(&comp, same_as);
                assert_eq!(same_status, 200);
                assert_eq!(
                    som::flow::canonical(&reply),
                    som::flow::canonical(&same_reply),
                    "{id}: target python must return the exact same bytes as omitted target"
                );
            } else {
                assert_eq!(status, 200, "{id}: expected 200, got {reply}");
                assert_eq!(reply["status"], "ok", "{id}");
                let bundle = &reply["bundle"];
                assert_eq!(bundle["version"], BUNDLE_VERSION, "{id}");
                assert_eq!(bundle["target"], TARGET, "{id}");
                assert_eq!(bundle["dsl"]["version"], DSL_VERSION, "{id}");

                let dsl_files = bundle["dsl"]["files"].as_array().expect("dsl files");
                assert!(
                    !dsl_files.is_empty() && dsl_files.len() <= MAX_FILES,
                    "{id}: dsl file count {}",
                    dsl_files.len()
                );
                let mut total_blocks = 0usize;
                for f in dsl_files {
                    let blocks = f["blocks"].as_array().expect("file blocks");
                    assert!(
                        !blocks.is_empty() && blocks.len() <= MAX_BLOCKS,
                        "{id}: block count in {}",
                        f["path"]
                    );
                    total_blocks += blocks.len();
                    for b in blocks {
                        let bid = b["id"].as_str().expect("block id");
                        assert!(
                            bid.starts_with("ts."),
                            "{id}: block id {bid} must start with ts."
                        );
                        assert!(
                            catalog_ids.contains(bid),
                            "{id}: block id {bid} must be in typescript-catalog.v1.json"
                        );
                    }
                }
                assert!(
                    total_blocks <= MAX_BLOCKS,
                    "{id}: total blocks {total_blocks}"
                );

                let expected_manifest = manifest(&comp, &bundle["prompt"]);
                assert_eq!(
                    bundle["assets"], expected_manifest,
                    "{id}: bundle assets must equal active TypeScript manifest"
                );
                let rust_manifest = crate::rust::descriptors(&comp, &bundle["prompt"]);
                for shared_key in [
                    "grammar",
                    "compiler_contract",
                    "selection_policy",
                    "literal_policy",
                    "serializer",
                    "semantic_contracts",
                ] {
                    assert_eq!(
                        bundle["assets"][shared_key], rust_manifest[shared_key],
                        "{id}: shared descriptor {shared_key} must equal Rust manifest"
                    );
                }

                let assemble_src = som::flow::canonical(&json!({"bundle": bundle}));
                let assemble_doc = strict_json::parse(&assemble_src).expect("parse bundle");
                check_schema(&assemble_doc).unwrap_or_else(|e| panic!("{id}: schema failed: {e}"));
                check_hashes(&assemble_doc, bundle)
                    .unwrap_or_else(|e| panic!("{id}: hashes failed: {e}"));

                let already_assembled_pair = if let Some(source_path) = item["source"].as_str() {
                    let base = source_path
                        .strip_suffix(".cnl")
                        .or_else(|| source_path.strip_suffix(".prompt.json"))
                        .unwrap_or(source_path)
                        .to_owned();
                    if let Some(prev) = by_source.get(&base) {
                        assert_eq!(
                            som::flow::canonical(prev),
                            som::flow::canonical(bundle),
                            "{id}: CNL and JSON forms of {base} must produce byte-identical bundles"
                        );
                        true
                    } else {
                        by_source.insert(base, bundle.clone());
                        false
                    }
                } else {
                    false
                };

                if !already_assembled_pair {
                    let (acode1, areply1) = crate::assemble(&comp, &assemble_doc);
                    let (acode2, areply2) = crate::assemble(&comp, &assemble_doc);
                    assert_eq!(acode1, 200, "{id}: assemble failed: {areply1}");
                    assert_eq!(acode2, 200, "{id}: second assemble failed: {areply2}");
                    assert_eq!(
                        som::flow::canonical(&areply1),
                        som::flow::canonical(&areply2),
                        "{id}: two assembles must return byte-identical responses"
                    );

                    let files_map = areply1["files"].as_object().expect("assembled files map");
                    let revision = bundle["prompt"]["migration"]["revision"]
                        .as_str()
                        .expect("migration revision");
                    let migration_path = format!("migrations/0001_{revision}.sql");
                    let expected_paths: BTreeSet<String> = FIXED_FILES
                        .iter()
                        .copied()
                        .map(str::to_owned)
                        .chain(std::iter::once(migration_path))
                        .collect();
                    let actual_paths: BTreeSet<String> = files_map.keys().cloned().collect();
                    assert_eq!(
                        actual_paths, expected_paths,
                        "{id}: assembled files must be the exact 11 product-layout.json files"
                    );
                    assert_eq!(
                        files_map["package.json"].as_str().unwrap(),
                        render::PACKAGE_JSON,
                        "{id}: package.json must equal reference bytes"
                    );
                    assert_eq!(
                        files_map["package-lock.json"].as_str().unwrap(),
                        render::PACKAGE_LOCK,
                        "{id}: package-lock.json must equal reference bytes"
                    );
                    assert_eq!(
                        files_map["tsconfig.json"].as_str().unwrap(),
                        render::TSCONFIG_JSON,
                        "{id}: tsconfig.json must equal reference bytes"
                    );

                    if let Some(root) = &write_root {
                        let product_id = id
                            .strip_suffix("-cnl-typescript")
                            .or_else(|| id.strip_suffix("-json-typescript"))
                            .unwrap_or(id)
                            .to_owned();
                        if written_ids.insert(product_id.clone()) {
                            write_product_dir(root, &product_id, files_map);
                            manifest_entries.push(json!({
                                "id": product_id,
                                "product": root.join(&product_id).to_string_lossy(),
                                "prompt": bundle["prompt"],
                            }));
                        }
                    }
                }

                if let Some(rust_expected) = item.get("rust_phase1_refusal") {
                    let rust_refusal =
                        crate::profile::phase1(&bundle["prompt"]).expect("Rust phase 1 refusal");
                    let expected_pointer = if item["form"] == "cnl" {
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
            }

            if let Some(control) = item["python_control_body"].as_str() {
                let (ctrl_status, ctrl_reply) = crate::compile(&comp, control);
                assert_eq!(ctrl_status, 200, "{id}: python control must return 200");
                assert_eq!(ctrl_reply["bundle"]["version"], "som.dsl-bundle.v1");
            }
        }

        if let Some(root) = &write_root {
            fs::create_dir_all(root).expect("create write_root");
            let manifest_json = json!({
                "products": manifest_entries,
            });
            fs::write(
                root.join("manifest.json"),
                serde_json::to_string_pretty(&manifest_json).unwrap() + "\n",
            )
            .expect("write manifest.json");
        }
    }

    #[test]
    fn assemble_recipes_a1_to_a12_and_valid_product_succeed() {
        let _guard = format::TEST_ENV_LOCK
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let prettier = require_prettier_env();

        let comp = compiler();
        let data: Value = serde_json::from_str(TS_TARGET_EXAMPLES).expect("target-examples.json");
        let v1_body = data["examples"][0]["body"].as_str().expect("v1 body");
        let (code, compiled) = crate::compile(&comp, v1_body);
        assert_eq!(code, 200);
        let base_bundle = compiled["bundle"].clone();

        let valid_doc =
            strict_json::parse(&som::flow::canonical(&json!({"bundle": base_bundle}))).unwrap();
        let (acode, assembled) = crate::assemble(&comp, &valid_doc);
        assert_eq!(acode, 200, "valid assemble failed: {assembled}");
        assert_eq!(
            assembled["files"].as_object().unwrap().len(),
            11,
            "expected 11 product files"
        );

        let rehash = |b: &mut Value| {
            let p_hash = canonical_hash(&b["prompt"]);
            let d_hash = canonical_hash(&b["dsl"]);
            let pay_hash = payload_hash(&b["prompt"], &b["dsl"], &b["assets"]);
            b["hashes"] = json!({
                "prompt_sha256": p_hash,
                "dsl_sha256": d_hash,
                "payload_sha256": pay_hash,
            });
        };

        let v5_body = data["examples"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["id"] == "example-v5-json-typescript")
            .expect("v5 example")["body"]
            .as_str()
            .unwrap();
        let (v5_code, v5_compiled) = crate::compile(&comp, v5_body);
        assert_eq!(v5_code, 200);
        let v5_bundle = v5_compiled["bundle"].clone();

        let rust_body = v1_body.replace("\"target\":\"typescript\"", "\"target\":\"rust\"");
        let (r_code, r_compiled) = crate::compile(&comp, &rust_body);
        assert_eq!(r_code, 200);
        let rust_bundle = r_compiled["bundle"].clone();

        let v6_prompt: Value = serde_json::from_str(include_str!(
            "../../../contracts/compiler-v1/examples/v6.prompt.json"
        ))
        .expect("v6 prompt");

        let plan_json: Value = serde_json::from_str(TS_ACCEPTANCE_PLAN).expect("acceptance-plan");
        let recipes = plan_json["assemble_refusals"]["recipes"]
            .as_array()
            .expect("recipes");
        assert_eq!(recipes.len(), 12);

        for recipe in recipes {
            let id = recipe["id"].as_str().unwrap();
            let exp_status = recipe["http_status"].as_u64().unwrap() as u16;
            let exp_code = recipe["code"].as_str().unwrap();
            let exp_ptr = recipe["prompt_pointer"].as_str().unwrap();

            if id == "A3" {
                let mut py_bundle = comp.compile_prompt(&base_bundle["prompt"]).expect("py");
                py_bundle["target"] = json!("typescript");
                let doc = strict_json::parse(&som::flow::canonical(&json!({"bundle": py_bundle})))
                    .unwrap();
                let err = comp.assemble_bundle(&py_bundle).unwrap_err();
                let legacy = comp.diagnostic_legacy_plan(&py_bundle["prompt"]);
                let rep =
                    som::json_diagnostics::bundle_error(&doc, &py_bundle, &err, legacy.as_ref());
                assert_eq!(
                    crate::status(rep["code"].as_str().unwrap()),
                    exp_status,
                    "{id}"
                );
                assert_eq!(rep["code"], exp_code, "{id}: {rep}");
                assert_eq!(
                    rep["diagnostics"][0]["prompt_pointer"], exp_ptr,
                    "{id}: {rep}"
                );
                let probe = doc.error(exp_ptr, exp_code, "probe", "probe");
                for key in [
                    "line",
                    "column",
                    "end_line",
                    "end_column",
                    "byte_start",
                    "byte_end",
                ] {
                    assert_eq!(
                        rep["diagnostics"][0][key], probe["diagnostics"][0][key],
                        "{id}: span.{key}"
                    );
                }
                continue;
            }

            let mut b = match id {
                "A6" => v5_bundle.clone(),
                "A11" => rust_bundle.clone(),
                _ => base_bundle.clone(),
            };
            match id {
                "A1" => {
                    b["assets"]["formatter"]["sha256"] = json!("0".repeat(64));
                    rehash(&mut b);
                }
                "A2" => {
                    for key in [
                        "target_contract",
                        "catalog",
                        "block_library",
                        "backend",
                        "formatter",
                    ] {
                        b["assets"][key] = rust_bundle["assets"][key].clone();
                    }
                    rehash(&mut b);
                }
                "A4" => {
                    b["target"] = json!("python");
                    rehash(&mut b);
                }
                "A5" => {
                    b["dsl"]["files"][0]["blocks"][0]["id"] = json!("orm.base");
                    rehash(&mut b);
                }
                "A6" => {
                    let shared = comp.descriptors(&v6_prompt);
                    for key in [
                        "grammar",
                        "compiler_contract",
                        "selection_policy",
                        "literal_policy",
                        "serializer",
                        "semantic_contracts",
                    ] {
                        b["assets"][key] = shared[key].clone();
                    }
                    b["prompt"] = v6_prompt.clone();
                    rehash(&mut b);
                }
                "A7" => {
                    let old = b["hashes"]["payload_sha256"].as_str().unwrap().to_owned();
                    let first = if old.starts_with('0') { "1" } else { "0" };
                    b["hashes"]["payload_sha256"] = json!(format!("{first}{}", &old[1..]));
                }
                "A8" => {
                    b["prompt"]["resource"]["fields"][0]["name"] = json!("__proto__");
                    rehash(&mut b);
                }
                "A9" => {
                    let last = b["dsl"]["files"]
                        .as_array()
                        .unwrap()
                        .last()
                        .unwrap()
                        .clone();
                    let files = b["dsl"]["files"].as_array_mut().unwrap();
                    files.push(last.clone());
                    files.push(last);
                    rehash(&mut b);
                }
                "A10" => {
                    b["target"] = json!("rust");
                    rehash(&mut b);
                }
                "A11" => {
                    b["target"] = json!("typescript");
                    rehash(&mut b);
                }
                "A12" => {
                    b["dsl"]["files"][0]["blocks"][0]["id"] =
                        rust_bundle["dsl"]["files"][0]["blocks"][0]["id"].clone();
                    rehash(&mut b);
                }
                other => panic!("unexpected recipe {other}"),
            }

            let doc = strict_json::parse(&som::flow::canonical(&json!({"bundle": b}))).unwrap();
            let (st, rep) = crate::assemble(&comp, &doc);
            assert_eq!(st, exp_status, "{id}: status mismatch: {rep}");
            assert_eq!(rep["code"], exp_code, "{id}: code mismatch: {rep}");
            assert_eq!(
                rep["diagnostics"][0]["prompt_pointer"], exp_ptr,
                "{id}: pointer mismatch: {rep}"
            );
            let probe = doc.error(exp_ptr, exp_code, "probe", "probe");
            for key in [
                "line",
                "column",
                "end_line",
                "end_column",
                "byte_start",
                "byte_end",
            ] {
                assert_eq!(
                    rep["diagnostics"][0][key], probe["diagnostics"][0][key],
                    "{id}: span.{key}"
                );
            }
        }

        // Additional check: 65 total blocks across files is refused at /bundle/dsl/files.
        let mut over_blocks = base_bundle.clone();
        let blk = over_blocks["dsl"]["files"][0]["blocks"][0].clone();
        let mut count: usize = over_blocks["dsl"]["files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| f["blocks"].as_array().unwrap().len())
            .sum();
        while count < 65 {
            for f in over_blocks["dsl"]["files"].as_array_mut().unwrap() {
                let arr = f["blocks"].as_array_mut().unwrap();
                if arr.len() < 64 && count < 65 {
                    arr.push(blk.clone());
                    count += 1;
                }
            }
        }
        rehash(&mut over_blocks);
        let doc =
            strict_json::parse(&som::flow::canonical(&json!({"bundle": over_blocks}))).unwrap();
        let (st, rep) = crate::assemble(&comp, &doc);
        assert_eq!(st, 400);
        assert_eq!(rep["code"], "INVALID_SCHEMA");
        assert_eq!(rep["diagnostics"][0]["prompt_pointer"], "/bundle/dsl/files");

        // Additional check: tampered prettier directory returns 503 ASSET_UNAVAILABLE.
        let bad_dir =
            std::env::temp_dir().join(format!("som-ts-bad-prettier-{}", std::process::id()));
        let _ = fs::remove_dir_all(&bad_dir);
        fs::create_dir_all(&bad_dir).unwrap();
        fs::write(bad_dir.join("tampered.txt"), "wrong").unwrap();
        // SAFETY: serialized via format::TEST_ENV_LOCK.
        unsafe {
            std::env::set_var(format::PRETTIER_ENV, &bad_dir);
        }
        let doc =
            strict_json::parse(&som::flow::canonical(&json!({"bundle": base_bundle}))).unwrap();
        let (st, rep) = crate::assemble(&comp, &doc);
        // SAFETY: serialized via format::TEST_ENV_LOCK.
        unsafe {
            std::env::set_var(format::PRETTIER_ENV, &prettier);
        }
        let _ = fs::remove_dir_all(&bad_dir);
        assert_eq!(st, 503);
        assert_eq!(rep["code"], "ASSET_UNAVAILABLE");
        assert_eq!(
            rep["diagnostics"][0]["prompt_pointer"],
            "/bundle/assets/formatter"
        );
    }
}
