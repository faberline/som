//! React phase 1 target: bundle emission, catalog composition, assemble checks and rendering (issue #127).
//!
//! The frozen contract is contracts/react-output-v1. A React product is 19 files: the 17 fixed
//! reference files listed in FIXED, then src/schema.ts and README.md, which the blocks in
//! render.rs generate from the prompt. Nothing runs prettier at compile or assemble; the
//! fixed files are formatted reference bytes and the generated files are written in that format.
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
pub const DSL_VERSION: &str = "som.dsl.react.v1";
pub const TARGET: &str = "react";
pub const MAX_FILES: usize = 19;
pub const MAX_BLOCKS: usize = 64;

pub const TARGET_CONTRACT_ID: &str = "som.react-output-contract.v1";
/// H(files map) of the latest sealed receipt for contracts/react-output-v1: amendment 2.
pub const TARGET_CONTRACT_SHA256: &str =
    "d48cbd2a790554bab14a38bd6092e5975e82027c0a6da1ef46d8ff7affdbf638";
pub const CATALOG_ID: &str = "som.react-catalog.v1";
pub const BLOCK_LIBRARY_ID: &str = "som.react-block-library.v1";
pub const BACKEND_ID: &str = "som.react-backend.v1";

pub const CATALOG_TEXT: &str =
    include_str!("../../../contracts/react-output-v1/catalog/react-catalog.v1.json");
const TOOLCHAIN_TEXT: &str = include_str!("../../../contracts/react-output-v1/toolchain.json");

/// The fixed product files in catalog order: path, block id and reference bytes.
pub const FIXED: &[(&str, &str, &str)] = &[
    (
        "index.html",
        "react.index.page",
        include_str!("../../../contracts/react-output-v1/reference/index.html"),
    ),
    (
        "package.json",
        "react.package.manifest",
        include_str!("../../../contracts/react-output-v1/reference/package.json"),
    ),
    (
        "package-lock.json",
        "react.package.lock",
        include_str!("../../../contracts/react-output-v1/reference/package-lock.json"),
    ),
    (
        "tsconfig.json",
        "react.tsconfig.config",
        include_str!("../../../contracts/react-output-v1/reference/tsconfig.json"),
    ),
    (
        "vite.config.ts",
        "react.vite.config",
        include_str!("../../../contracts/react-output-v1/reference/vite.config.ts"),
    ),
    (
        "src/main.tsx",
        "react.main.mount",
        include_str!("../../../contracts/react-output-v1/reference/src/main.tsx"),
    ),
    (
        "src/App.tsx",
        "react.app.routes",
        include_str!("../../../contracts/react-output-v1/reference/src/App.tsx"),
    ),
    (
        "src/api.ts",
        "react.api.client",
        include_str!("../../../contracts/react-output-v1/reference/src/api.ts"),
    ),
    (
        "src/spec.ts",
        "react.spec.types",
        include_str!("../../../contracts/react-output-v1/reference/src/spec.ts"),
    ),
    (
        "src/form.ts",
        "react.form.text",
        include_str!("../../../contracts/react-output-v1/reference/src/form.ts"),
    ),
    (
        "src/styles.css",
        "react.styles.sheet",
        include_str!("../../../contracts/react-output-v1/reference/src/styles.css"),
    ),
    (
        "src/pages/ListPage.tsx",
        "react.pages.list",
        include_str!("../../../contracts/react-output-v1/reference/src/pages/ListPage.tsx"),
    ),
    (
        "src/pages/DetailPage.tsx",
        "react.pages.detail",
        include_str!("../../../contracts/react-output-v1/reference/src/pages/DetailPage.tsx"),
    ),
    (
        "src/pages/FormPage.tsx",
        "react.pages.form",
        include_str!("../../../contracts/react-output-v1/reference/src/pages/FormPage.tsx"),
    ),
    (
        "src/pages/ErrorView.tsx",
        "react.pages.error",
        include_str!("../../../contracts/react-output-v1/reference/src/pages/ErrorView.tsx"),
    ),
    (
        "src/app.test.tsx",
        "react.test.app",
        include_str!("../../../contracts/react-output-v1/reference/src/app.test.tsx"),
    ),
    (
        "src/form.test.ts",
        "react.test.form",
        include_str!("../../../contracts/react-output-v1/reference/src/form.test.ts"),
    ),
];

/// The two generated files, after the fixed files.
const GENERATED: &[&str] = &["src/schema.ts", "README.md"];

/// The renderer source that holds the generated block bodies, then the fixed reference files
/// in FIXED order.
static BLOCK_LIBRARY_TEXT: LazyLock<String> = LazyLock::new(|| {
    let mut text = include_str!("render.rs").to_owned();
    for (_, _, reference) in FIXED {
        text.push_str(reference);
    }
    text
});

const BACKEND_FILES: &[(&str, &str)] = &[
    ("som-targets/Cargo.toml", include_str!("../../Cargo.toml")),
    ("som-targets/src/lib.rs", include_str!("../lib.rs")),
    ("som-targets/src/main.rs", include_str!("../main.rs")),
    ("som-targets/src/profile.rs", include_str!("../profile.rs")),
    ("som-targets/src/react/mod.rs", include_str!("mod.rs")),
    (
        "som-targets/src/react/profile.rs",
        include_str!("profile.rs"),
    ),
    ("som-targets/src/react/render.rs", include_str!("render.rs")),
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

static CATALOG: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(CATALOG_TEXT).expect("frozen react-catalog.v1.json"));

static FORMATTER: LazyLock<Value> = LazyLock::new(|| {
    let toolchain: Value = serde_json::from_str(TOOLCHAIN_TEXT).expect("frozen toolchain.json");
    json!({"id": toolchain["formatter"]["id"], "sha256": toolchain["formatter"]["sha256"]})
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

/// The 11 asset descriptors of a som.dsl-bundle.v2 React bundle.
pub fn manifest(compiler: &Compiler, prompt: &Value) -> Value {
    let mut assets = compiler.descriptors(prompt);
    assets["target_contract"] = json!({"id": TARGET_CONTRACT_ID, "sha256": TARGET_CONTRACT_SHA256});
    assets["catalog"] = raw_described(CATALOG_ID, CATALOG_TEXT);
    assets["block_library"] = raw_described(BLOCK_LIBRARY_ID, &BLOCK_LIBRARY_TEXT);
    assets["backend"] = backend_descriptor();
    assets["formatter"] = FORMATTER.clone();
    assets
}

// ---------------------------------------------------------------- composition

/// Deterministic React DSL composition plan for a phase 1 prompt: every catalog block whose
/// versions include the prompt version, in catalog order. Block params keep their
/// {"$ref": name} form; the assembler resolves them from the prompt.
pub fn plan(prompt: &Value) -> Value {
    let version = prompt["version"].as_str().unwrap_or_default();
    let mut files = Vec::new();
    for file in CATALOG["files"].as_array().expect("catalog files") {
        let blocks: Vec<Value> = file["blocks"]
            .as_array()
            .expect("catalog blocks")
            .iter()
            .filter(|block| {
                block["versions"]
                    .as_array()
                    .is_some_and(|list| list.iter().any(|item| item == version))
            })
            .map(|block| {
                json!({
                    "id": block["id"],
                    "name": block["name"],
                    "params": block["params"],
                    "refs": block["refs"],
                })
            })
            .collect();
        if !blocks.is_empty() {
            files.push(json!({"path": file["path"], "blocks": blocks}));
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

/// Plan and emit a React bundle for a prompt that already passed compiler-v1 validation and
/// the React profile.
pub fn compile(compiler: &Compiler, doc: &Document, prompt: &Value, pointer: &str) -> Reply {
    let assets = manifest(compiler, prompt);
    let dsl = plan(prompt);
    if !within_plan_limits(&dsl) {
        return rejected(doc.error(
            pointer,
            "SCOPE_TOO_LARGE",
            "React DSL plan exceeds the phase 1 file or block limit.",
            "At most 19 files and 64 blocks.",
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
            "Compiled React bundle exceeds the assemble request limit.",
            "A prompt whose canonical assemble request is at most 2097152 bytes.",
        ));
    }
    (200, json!({"status": "ok", "bundle": bundle}))
}

// ---------------------------------------------------------------- assemble

/// Assemble a som.dsl-bundle.v2 React bundle in the frozen x-assemble-check-order.
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
            "Bundle asset descriptors do not match the active React compiler assets.",
            "Asset descriptors from the current compiler and React target.",
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
    if let Some(pointer) = crate::rust::composition_difference(&bundle["dsl"], &plan(prompt)) {
        return rejected(doc.error(
            &pointer,
            "INVALID_COMPOSITION",
            "React DSL composition does not match the deterministic plan for this prompt.",
            "The unmodified DSL emitted by /v1/compile for target react.",
        ));
    }
    let files = render::files(prompt, &bundle["dsl"]).and_then(|files| {
        check_output(&files)?;
        Ok(files)
    });
    match files {
        Ok(files) => (200, json!({"status": "ok", "files": files})),
        Err(detail) => (
            500,
            doc.error(
                "/bundle/dsl/files",
                "OUTPUT_QUALITY_FAILED",
                &detail,
                "The 19 files of contracts/react-output-v1/product-layout.json, the fixed files equal to the reference bytes.",
            ),
        ),
    }
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

fn valid_file_path(path: &str) -> bool {
    FIXED.iter().any(|(fixed, _, _)| *fixed == path) || GENERATED.contains(&path)
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
        "A React bundle",
    )?;
    if bundle["version"] != BUNDLE_VERSION {
        return refuse(
            "/bundle/version",
            "The bundle version must be som.dsl-bundle.v2.",
        );
    }
    if bundle["target"] != TARGET {
        return refuse("/bundle/target", "A React bundle must have target react.");
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
        "The React DSL",
    )?;
    if dsl["version"] != DSL_VERSION {
        return refuse(
            "/bundle/dsl/version",
            "The DSL version must be som.dsl.react.v1.",
        );
    }
    let files = match dsl["files"].as_array() {
        Some(files) if (1..=MAX_FILES).contains(&files.len()) => files,
        _ => return refuse("/bundle/dsl/files", "The DSL must have 1 to 19 files."),
    };
    let mut total_blocks = 0usize;
    for (file_index, file) in files.iter().enumerate() {
        let base = format!("/bundle/dsl/files/{file_index}");
        members(file, &base, &["path", "blocks"], "A DSL file")?;
        if !file["path"].as_str().is_some_and(valid_file_path) {
            return refuse(
                &format!("{base}/path"),
                "The file path is not a React product layout path.",
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
            "A som.dsl-bundle.v2 React bundle that compile returned.",
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

/// The 19 product files: the fixed files equal their reference bytes and the generated
/// files are not empty.
fn check_output(files: &BTreeMap<String, String>) -> Result<(), String> {
    let total = FIXED.len() + GENERATED.len();
    if files.len() != total {
        return Err(format!(
            "expected {total} product files, rendered {}",
            files.len()
        ));
    }
    for (path, _, reference) in FIXED {
        match files.get(*path) {
            Some(content) if content == reference => {}
            Some(_) => return Err(format!("rendered file {path} differs from its reference")),
            None => return Err(format!("missing rendered file {path}")),
        }
    }
    for path in GENERATED {
        match files.get(*path) {
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
    use som::strict_json;
    use std::{
        collections::BTreeSet,
        fs,
        path::{Path, PathBuf},
    };

    const NEGATIVE_CASES: &str =
        include_str!("../../../contracts/react-output-v1/negative-cases.json");
    const TARGET_EXAMPLES: &str =
        include_str!("../../../contracts/react-output-v1/target-examples.json");
    const ACCEPTANCE_PLAN: &str =
        include_str!("../../../contracts/react-output-v1/acceptance-plan.json");
    const SPANS: [&str; 6] = [
        "line",
        "column",
        "end_line",
        "end_column",
        "byte_start",
        "byte_end",
    ];

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
                for git_anc in Path::new(gitdir.trim()).ancestors() {
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

    fn assemble_bundle(comp: &Compiler, bundle: &Value) -> Reply {
        let doc = strict_json::parse(&som::flow::canonical(&json!({"bundle": bundle}))).unwrap();
        let reply = assemble(comp, &doc);
        assert_eq!(
            crate::assemble(comp, &doc),
            reply,
            "the crate dispatches to React"
        );
        reply
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
        assert_eq!(status, expected_status, "{id}: status {reply}");
        let object = reply.as_object().expect("error object");
        assert_eq!(object.len(), 4, "{id}: reply keys");
        assert_eq!(reply["status"], "error", "{id}: status field");
        assert_eq!(reply["code"], expected["code"], "{id}: code");
        assert_eq!(reply["detail"], expected["cause"], "{id}: detail");
        let diagnostics = reply["diagnostics"].as_array().expect("diagnostics");
        assert_eq!(diagnostics.len(), 1, "{id}: single diagnostic");
        for (key, value) in expected.as_object().expect("expected diagnostic") {
            assert_eq!(&diagnostics[0][key], value, "{id}: diagnostic.{key}");
        }
    }

    #[test]
    fn target_contract_is_the_latest_receipt() {
        let path = som_rs().join("evidence/react-output-contract-amendment-002.json");
        let receipt: Value =
            serde_json::from_str(&fs::read_to_string(path).expect("receipt")).expect("JSON");
        assert_eq!(canonical_hash(&receipt["files"]), TARGET_CONTRACT_SHA256);
        assert_eq!(receipt["target_contract"]["sha256"], TARGET_CONTRACT_SHA256);
        assert_eq!(receipt["target_contract"]["id"], TARGET_CONTRACT_ID);
        let rounds = receipt["independent_review"]["rounds"]
            .as_array()
            .expect("review rounds");
        assert_eq!(rounds.last().expect("a round")["verdict"], "APPROVED");
        for (path, sha) in receipt["files"].as_object().expect("files") {
            let bytes = fs::read(som_rs().join("..").join(path)).expect("contract file");
            assert_eq!(digest(&bytes), sha.as_str().expect("sha"), "{path}");
        }
    }

    #[test]
    fn fixed_files_follow_the_catalog_and_the_product_layout() {
        let catalog: Vec<(String, Vec<String>)> = CATALOG["files"]
            .as_array()
            .expect("catalog files")
            .iter()
            .map(|file| {
                let ids = file["blocks"]
                    .as_array()
                    .expect("blocks")
                    .iter()
                    .map(|block| block["id"].as_str().expect("id").to_owned())
                    .collect();
                (file["path"].as_str().expect("path").to_owned(), ids)
            })
            .collect();
        assert_eq!(catalog.len(), FIXED.len() + GENERATED.len());
        for ((path, block, _), (catalog_path, ids)) in FIXED.iter().zip(&catalog) {
            assert_eq!(path, catalog_path);
            assert_eq!(ids, &vec![(*block).to_owned()], "{path}");
        }
        let generated: Vec<&str> = catalog[FIXED.len()..]
            .iter()
            .map(|(path, _)| path.as_str())
            .collect();
        assert_eq!(generated, GENERATED);
        assert_eq!(CATALOG["dsl_version"], DSL_VERSION);

        let layout: Value = serde_json::from_str(
            &fs::read_to_string(som_rs().join("contracts/react-output-v1/product-layout.json"))
                .expect("product-layout.json"),
        )
        .expect("JSON");
        let text = layout.to_string();
        for (path, _, reference) in FIXED {
            assert!(text.contains(&format!("\"{path}\"")), "{path} in layout");
            let on_disk = fs::read_to_string(
                som_rs()
                    .join("contracts/react-output-v1/reference")
                    .join(path),
            )
            .expect("reference file");
            assert_eq!(&on_disk, reference, "{path}");
        }
    }

    #[test]
    fn backend_files_list_every_react_source() {
        let mut listed: BTreeSet<String> = BACKEND_FILES
            .iter()
            .filter_map(|(path, _)| path.strip_prefix("som-targets/src/react/"))
            .map(str::to_owned)
            .collect();
        let mut found = BTreeSet::new();
        let mut pending = vec![PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/react")];
        let root = pending[0].clone();
        while let Some(dir) = pending.pop() {
            for entry in fs::read_dir(dir).expect("react dir") {
                let path = entry.expect("entry").path();
                if path.is_dir() {
                    pending.push(path);
                } else {
                    let rel = path.strip_prefix(&root).expect("relative");
                    found.insert(rel.to_string_lossy().into_owned());
                }
            }
        }
        assert_eq!(listed, found);
        listed.clear();
        let paths: Vec<&str> = BACKEND_FILES.iter().map(|(path, _)| *path).collect();
        let mut sorted = paths.clone();
        sorted.sort_unstable();
        assert_eq!(paths, sorted);
        for (path, source) in BACKEND_FILES {
            let on_disk = fs::read_to_string(som_rs().join(path)).expect("backend source");
            assert_eq!(&on_disk, source, "{path}");
        }
    }

    #[test]
    fn plan_has_19_files_for_each_phase_1_version() {
        for number in 1..=4 {
            let prompt = json!({"version": format!("som.prompt.v{number}")});
            let dsl = plan(&prompt);
            let files = dsl["files"].as_array().expect("files");
            assert_eq!(files.len(), MAX_FILES, "v{number}");
            assert!(within_plan_limits(&dsl), "v{number}");
            let paths: Vec<&str> = files
                .iter()
                .map(|file| file["path"].as_str().expect("path"))
                .collect();
            let expected: Vec<&str> = FIXED
                .iter()
                .map(|(path, _, _)| *path)
                .chain(GENERATED.iter().copied())
                .collect();
            assert_eq!(paths, expected, "v{number}");
        }
        let v5 = plan(&json!({"version": "som.prompt.v5"}));
        assert_eq!(v5["files"], json!([]));
        assert!(!within_plan_limits(&v5));
    }

    #[test]
    fn manifest_shares_the_compiler_descriptors() {
        let comp = compiler();
        for number in 1..=4 {
            let prompt: Value = serde_json::from_str(
                &fs::read_to_string(som_rs().join(format!(
                    "contracts/compiler-v1/examples/v{number}.prompt.json"
                )))
                .expect("prompt"),
            )
            .expect("JSON");
            let assets = manifest(&comp, &prompt);
            let keys: Vec<&str> = assets
                .as_object()
                .expect("assets")
                .keys()
                .map(String::as_str)
                .collect();
            let mut expected = ASSET_KEYS.to_vec();
            expected.sort_unstable();
            assert_eq!(keys, expected);
            let typescript = crate::typescript::manifest(&comp, &prompt);
            for key in &ASSET_KEYS[..6] {
                assert_eq!(assets[*key], typescript[*key], "{key}");
            }
            assert_eq!(
                assets["formatter"]["id"],
                "som.react-formatter.prettier-3.9.10.v1"
            );
            assert_eq!(
                assets["formatter"]["sha256"],
                "636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8"
            );
        }
    }

    #[test]
    fn react_negative_cases_and_python_controls_match_the_frozen_contract() {
        let comp = compiler();
        let data: Value = serde_json::from_str(NEGATIVE_CASES).expect("negative-cases.json");
        let cases = data["cases"].as_array().expect("cases");
        assert_eq!(cases.len(), 30);
        for case in cases {
            let id = case["id"].as_str().expect("id");
            let body = case["body"].as_str().expect("body");
            let form = case["form"].as_str().expect("form");
            let expected_status = case["http_status"].as_u64().expect("http_status") as u16;
            let expected = &case["expected"];
            let (status, reply) = crate::compile(&comp, body);
            assert_one_diagnostic(status, &reply, expected_status, expected, id);
            assert_eq!(
                located(body, form, &reply["diagnostics"][0]),
                case["span_text"].as_str().expect("span_text"),
                "{id}: span_text"
            );

            let Some(control_body) = case["python_control_body"].as_str() else {
                continue;
            };
            let control_expected = case["python_control_expected"]
                .as_str()
                .expect("python_control_expected");
            let (ctrl_status, ctrl_reply) = crate::compile(&comp, control_body);
            if control_expected.starts_with("HTTP 200") {
                assert_eq!(ctrl_status, 200, "{id}: python control {ctrl_reply}");
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
                assert_eq!(
                    located(control_body, form, &ctrl_reply["diagnostics"][0]),
                    case["span_text"].as_str().expect("span_text"),
                    "{id}: python control span_text"
                );
            }
        }
    }

    #[test]
    fn case_inventory_matches_the_react_profile() {
        let inventory: Value = serde_json::from_str(
            &fs::read_to_string(som_rs().join("contracts/react-output-v1/case-inventory.json"))
                .expect("case-inventory.json"),
        )
        .expect("JSON");
        let repo = som_rs().join("..");
        for row in inventory["public_examples"].as_array().expect("examples") {
            let prompt: Value = serde_json::from_str(
                &fs::read_to_string(repo.join(row["prompt"].as_str().expect("prompt")))
                    .expect("prompt file"),
            )
            .expect("JSON");
            let outcome = profile::check(&prompt).map_or("in_scope", |found| found.rule);
            assert_eq!(outcome, row["react"], "{}", row["id"]);
        }

        let fixtures: Value = serde_json::from_str(
            &fs::read_to_string(som_rs().join("tests/compiler-v1/cnl-positive-fixtures.json"))
                .expect("cnl-positive-fixtures.json"),
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
                    row["react"].as_str().expect("react").to_owned(),
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
                row["react"].as_str(),
                "{id}"
            );
        }

        let cov_index_path = som_rs().join("tests/rust-output-v1/cases/index.json");
        let cov_index: Value =
            serde_json::from_str(&fs::read_to_string(&cov_index_path).expect("cases/index.json"))
                .expect("JSON");
        let cov_dir = cov_index_path.parent().expect("cases dir");
        let rows = inventory["coverage_cases"].as_array().expect("coverage");
        let cases = cov_index["cases"].as_array().expect("cases");
        assert_eq!(cases.len(), rows.len());
        for (case, row) in cases.iter().zip(rows) {
            assert_eq!(case["id"], row["id"]);
            let prompt: Value = serde_json::from_str(
                &fs::read_to_string(cov_dir.join(case["prompt"].as_str().expect("prompt")))
                    .expect("coverage prompt"),
            )
            .expect("JSON");
            let outcome = profile::check(&prompt).map_or("in_scope", |found| found.rule);
            assert_eq!(outcome, row["react"], "{}", row["id"]);
        }
    }

    #[test]
    fn target_examples_and_name_cases_compile_and_assemble() {
        let comp = compiler();
        let data: Value = serde_json::from_str(TARGET_EXAMPLES).expect("target-examples.json");
        let mut bundles: BTreeMap<String, Value> = BTreeMap::new();
        for example in data["examples"].as_array().expect("examples") {
            let id = example["id"].as_str().expect("id");
            let body = example["body"].as_str().expect("body");
            if example["target"] == "python" {
                let same = example["same_as_body"].as_str().expect("same_as_body");
                assert_eq!(
                    crate::compile(&comp, body),
                    crate::compile(&comp, same),
                    "{id}"
                );
                continue;
            }
            let (status, reply) = crate::compile(&comp, body);
            assert_eq!(status, 200, "{id}: {reply}");
            let bundle = reply["bundle"].clone();
            assert_eq!(bundle["target"], TARGET);
            assert_eq!(bundle["version"], BUNDLE_VERSION);
            let (status, assembled) = assemble_bundle(&comp, &bundle);
            assert_eq!(status, 200, "{id}: {assembled}");
            assert_eq!(assembled["files"].as_object().expect("files").len(), 19);
            let pair = id.replace("-cnl-", "-").replace("-json-", "-");
            if let Some(other) = bundles.insert(pair, bundle.clone()) {
                assert_eq!(other, bundle, "{id}: CNL and JSON bundles differ");
            }
        }
        assert_eq!(bundles.len(), 8);
        for case in data["name_cases"].as_array().expect("name_cases") {
            let id = case["id"].as_str().expect("id");
            let (status, reply) = crate::compile(&comp, case["body"].as_str().expect("body"));
            assert_eq!(status, 200, "{id}: {reply}");
            let (status, assembled) = assemble_bundle(&comp, &reply["bundle"]);
            assert_eq!(status, 200, "{id}: {assembled}");
            let control = case["python_control_body"].as_str().expect("control");
            let (status, py) = crate::compile(&comp, control);
            assert_eq!(status, 200, "{id}: python control");
            assert_eq!(py["bundle"]["version"], som::compiler::BUNDLE_VERSION);
        }
    }

    #[test]
    fn compile_and_assemble_are_deterministic() {
        let comp = compiler();
        let data: Value = serde_json::from_str(TARGET_EXAMPLES).expect("target-examples.json");
        let body = data["examples"][0]["body"].as_str().expect("body");
        let first = crate::compile(&comp, body);
        assert_eq!(first, crate::compile(&comp, body));
        let bundle = &first.1["bundle"];
        assert_eq!(
            assemble_bundle(&comp, bundle),
            assemble_bundle(&comp, bundle)
        );
    }

    #[test]
    fn assemble_recipes_a1_to_a16() {
        let comp = compiler();
        let data: Value = serde_json::from_str(TARGET_EXAMPLES).expect("target-examples.json");
        let example = |id: &str| {
            data["examples"]
                .as_array()
                .unwrap()
                .iter()
                .find(|item| item["id"] == id)
                .expect("example")["body"]
                .as_str()
                .unwrap()
                .to_owned()
        };
        let v1_body = example("example-v1-json-react");
        let (code, compiled) = crate::compile(&comp, &v1_body);
        assert_eq!(code, 200);
        let base = compiled["bundle"].clone();
        let other_target = |target: &str| {
            let body = v1_body.replace("\"target\":\"react\"", &format!("\"target\":\"{target}\""));
            let (code, reply) = crate::compile(&comp, &body);
            assert_eq!(code, 200, "{target}: {reply}");
            reply["bundle"].clone()
        };
        let typescript_bundle = other_target("typescript");
        let rust_bundle = other_target("rust");
        let v5_prompt: Value = serde_json::from_str(include_str!(
            "../../../contracts/compiler-v1/examples/v5.prompt.json"
        ))
        .expect("v5 prompt");
        let rehash = |b: &mut Value| {
            b["hashes"] = json!({
                "prompt_sha256": canonical_hash(&b["prompt"]),
                "dsl_sha256": canonical_hash(&b["dsl"]),
                "payload_sha256": payload_hash(&b["prompt"], &b["dsl"], &b["assets"]),
            });
        };

        let plan_json: Value = serde_json::from_str(ACCEPTANCE_PLAN).expect("acceptance-plan");
        let recipes = plan_json["assemble_refusals"]["recipes"]
            .as_array()
            .expect("recipes");
        assert_eq!(recipes.len(), 16);
        for recipe in recipes {
            let id = recipe["id"].as_str().unwrap();
            let exp_status = recipe["http_status"].as_u64().unwrap() as u16;
            let exp_code = recipe["code"].as_str().unwrap();
            let exp_ptr = recipe["prompt_pointer"].as_str().unwrap();

            if id == "A3" {
                let mut py_bundle = comp.compile_prompt(&base["prompt"]).expect("python");
                py_bundle["target"] = json!(TARGET);
                let doc = strict_json::parse(&som::flow::canonical(&json!({"bundle": py_bundle})))
                    .unwrap();
                let err = comp.assemble_bundle(&py_bundle).unwrap_err();
                let legacy = comp.diagnostic_legacy_plan(&py_bundle["prompt"]);
                let rep =
                    som::json_diagnostics::bundle_error(&doc, &py_bundle, &err, legacy.as_ref());
                assert_eq!(crate::status(rep["code"].as_str().unwrap()), exp_status);
                assert_eq!(rep["code"], exp_code, "{id}: {rep}");
                assert_eq!(rep["diagnostics"][0]["prompt_pointer"], exp_ptr, "{id}");
                let probe = doc.error(exp_ptr, exp_code, "probe", "probe");
                for key in SPANS {
                    assert_eq!(
                        rep["diagnostics"][0][key], probe["diagnostics"][0][key],
                        "{id}"
                    );
                }
                continue;
            }

            let mut b = match id {
                "A12" => typescript_bundle.clone(),
                "A13" => rust_bundle.clone(),
                _ => base.clone(),
            };
            // A4, A10 and A11 go through the crate dispatch to the Rust and TypeScript
            // assemblers; the others reach the React assembler.
            let mut dispatch = false;
            match id {
                "A1" => b["assets"]["formatter"]["sha256"] = json!("0".repeat(64)),
                "A2" => {
                    let ts = crate::typescript::manifest(&comp, &b["prompt"]);
                    for key in &ASSET_KEYS[6..] {
                        b["assets"][*key] = ts[*key].clone();
                    }
                }
                "A4" | "A10" | "A11" => {
                    b["target"] = json!(match id {
                        "A4" => "python",
                        "A10" => "rust",
                        _ => "typescript",
                    });
                    dispatch = true;
                }
                "A5" => b["dsl"]["files"][0]["blocks"][0]["id"] = json!("orm.base"),
                "A6" => {
                    let shared = comp.descriptors(&v5_prompt);
                    for key in &ASSET_KEYS[..6] {
                        b["assets"][*key] = shared[*key].clone();
                    }
                    b["prompt"] = v5_prompt.clone();
                }
                "A7" => {}
                "A8" => {
                    let old = b["prompt"]["resource"]["fields"][0]["name"].clone();
                    b["prompt"]["resource"]["fields"][0]["name"] = json!("__proto__");
                    let dsl = som::flow::canonical(&b["dsl"]);
                    assert!(
                        !dsl.contains(&old.to_string()),
                        "{id}: no DSL param names it"
                    );
                }
                "A9" => {
                    let files = b["dsl"]["files"].as_array_mut().unwrap();
                    assert_eq!(files.len(), 19);
                    files.push(files.last().unwrap().clone());
                }
                "A12" | "A13" => b["target"] = json!(TARGET),
                "A14" => {
                    b["dsl"]["files"][0]["blocks"][0]["id"] =
                        typescript_bundle["dsl"]["files"][0]["blocks"][0]["id"].clone();
                }
                "A15" => {
                    let files = b["dsl"]["files"].as_array_mut().unwrap();
                    let block = files.last().unwrap()["blocks"]
                        .as_array()
                        .unwrap()
                        .last()
                        .unwrap()
                        .clone();
                    let mut total: usize = files
                        .iter()
                        .map(|f| f["blocks"].as_array().unwrap().len())
                        .sum();
                    let mut index = files.len() - 1;
                    while total < 65 {
                        let blocks = files[index]["blocks"].as_array_mut().unwrap();
                        if blocks.len() == MAX_BLOCKS {
                            index -= 1;
                            continue;
                        }
                        blocks.push(block.clone());
                        total += 1;
                    }
                }
                "A16" => {
                    let first = b["dsl"]["files"][0]["path"].clone();
                    b["dsl"]["files"][0]["path"] = b["dsl"]["files"][1]["path"].clone();
                    b["dsl"]["files"][1]["path"] = first;
                }
                other => panic!("unexpected recipe {other}"),
            }
            if id == "A7" {
                let old = b["hashes"]["payload_sha256"].as_str().unwrap().to_owned();
                let first = if old.starts_with('0') { "1" } else { "0" };
                b["hashes"]["payload_sha256"] = json!(format!("{first}{}", &old[1..]));
            } else {
                rehash(&mut b);
            }
            let doc = strict_json::parse(&som::flow::canonical(&json!({"bundle": b}))).unwrap();
            let (st, rep) = if dispatch {
                crate::assemble(&comp, &doc)
            } else {
                assemble(&comp, &doc)
            };
            assert_eq!(st, exp_status, "{id}: status {rep}");
            assert_eq!(rep["code"], exp_code, "{id}: code {rep}");
            assert_eq!(rep["diagnostics"][0]["prompt_pointer"], exp_ptr, "{id}");
            let probe = doc.error(exp_ptr, exp_code, "probe", "probe");
            for key in SPANS {
                assert_eq!(
                    rep["diagnostics"][0][key], probe["diagnostics"][0][key],
                    "{id}"
                );
            }
        }
    }

    #[test]
    fn a_changed_fixed_block_fails_the_output_check() {
        let mut files: BTreeMap<String, String> = FIXED
            .iter()
            .map(|(path, _, text)| ((*path).to_owned(), (*text).to_owned()))
            .collect();
        for path in GENERATED {
            files.insert((*path).to_owned(), "x\n".to_owned());
        }
        assert_eq!(check_output(&files), Ok(()));
        files.insert("src/api.ts".to_owned(), String::new());
        assert_eq!(
            check_output(&files),
            Err("rendered file src/api.ts differs from its reference".to_owned())
        );
        files.remove("src/api.ts");
        assert!(check_output(&files).is_err());
    }
}
