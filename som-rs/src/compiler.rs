//! Model-free compilation and checked bundle assembly.
use crate::{Block, SomError, composed, flow, flow_compact, planner, python_names, quality};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

pub const PURPOSE: &str = "som.rust-compiler.v1";
pub const BUNDLE_VERSION: &str = "som.dsl-bundle.v1";
pub const COMPILE_BODY_LIMIT: usize = 262144;
pub const ASSEMBLE_BODY_LIMIT: usize = 2097152;
const CURRENT_BYTES: &[u8] =
    include_bytes!("../../som-code-python/data/blocks/flow-v7-compact/blocks.json");
const LEGACY_BYTES: &[u8] = include_bytes!("../../som-code-python/data/blocks/v5/blocks.json");
const QUALITY_BYTES: &[u8] =
    include_bytes!("../../som-code-python/data/blocks/composed-quality/blocks.json");
const GRAMMAR: &str = include_str!("../contracts/compiler-v1/grammar.ebnf");
const CONTRACT: &str = include_str!("../contracts/compiler-v1/CONTRACT.md");
const POLICY: &str = include_str!("../contracts/compiler-v1/selection-policy.json");
const SERIALIZER: &str = include_str!("../contracts/compiler-v1/serializer-policy.json");
const LITERALS: &str = include_str!("../contracts/compiler-v1/literal-policy.json");
const FORMATTER_SHA256: &str = "cb0cdeba051c5c5bdc85c22dc31440ac1ea17651366bf188d4df1f8fe985933a";

fn err(code: &'static str, detail: impl Into<String>) -> SomError {
    SomError {
        code,
        detail: detail.into(),
    }
}
pub fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn canonical_hash(value: &Value) -> String {
    digest(flow::canonical(value).as_bytes())
}
fn descriptor(id: &str, bytes: &[u8]) -> Value {
    json!({"id":id,"sha256":digest(bytes)})
}
fn described(id: &str, value: &Value) -> Value {
    descriptor(id, flow::canonical(value).as_bytes())
}

fn backend_manifest() -> Value {
    // Hash the source text, including this source, rather than a stored self-hash.
    let files = [
        ("src/lib.rs", include_str!("lib.rs")),
        ("src/cnl.rs", include_str!("cnl.rs")),
        ("src/cnl_semantics.rs", include_str!("cnl_semantics.rs")),
        ("src/planner.rs", include_str!("planner.rs")),
        ("src/python_names.rs", include_str!("python_names.rs")),
        ("src/compiler.rs", include_str!("compiler.rs")),
        ("src/strict_json.rs", include_str!("strict_json.rs")),
        (
            "src/json_diagnostics.rs",
            include_str!("json_diagnostics.rs"),
        ),
        ("src/main.rs", include_str!("main.rs")),
        ("src/related.rs", include_str!("related.rs")),
        ("src/business.rs", include_str!("business.rs")),
        ("src/composed.rs", include_str!("composed.rs")),
        ("src/flow.rs", include_str!("flow.rs")),
        ("src/flow_compact.rs", include_str!("flow_compact.rs")),
        ("src/quality.rs", include_str!("quality.rs")),
        ("Cargo.toml", include_str!("../Cargo.toml")),
        ("Cargo.lock", include_str!("../Cargo.lock")),
        ("contracts/related-business.v1.md", composed::CONTRACT_TEXT),
        ("contracts/related-flow.v1.md", flow::CONTRACT_TEXT),
        (
            "contracts/python-literal-encoding.v1.json",
            include_str!("../contracts/python-literal-encoding.v1.json"),
        ),
        (
            "contracts/python-quality-v1/pyproject.toml",
            quality::PROFILE_TEXT,
        ),
        ("block-contracts/composed.json", composed::CATALOG_TEXT),
        (
            "block-contracts/composed-quality.json",
            quality::CATALOG_TEXT,
        ),
        ("block-contracts/flow-v7.json", flow::CATALOG_TEXT),
        (
            "block-contracts/flow-v7-compact.json",
            flow::COMPACT_CATALOG_TEXT,
        ),
        (
            "flow_compact/forward_support.py.tpl",
            flow_compact::SUPPORT_TEXT,
        ),
        ("flow_compact/engine.py.tpl", flow_compact::ENGINE_TEXT),
    ];
    let files: BTreeMap<_, _> = files
        .iter()
        .map(|(name, text)| (*name, digest(text.as_bytes())))
        .collect();
    json!({"version":"som.compiler-backend.v1","files":files})
}

fn formatter_config() -> Value {
    json!({"id":quality::VERSION,"formatter_version":quality::FORMATTER_VERSION,
        "formatter_sha256":FORMATTER_SHA256,"profile_sha256":digest(quality::PROFILE_TEXT.as_bytes())})
}

pub fn asset_manifest() -> Value {
    let config = flow::canonical(&formatter_config()) + "\n";
    json!({"version":"som.compiler-assets.v1","purpose":PURPOSE,"backend":backend_manifest(),
    "files":{
        "blocks.json":digest(CURRENT_BYTES),"legacy-blocks.json":digest(LEGACY_BYTES),"quality-blocks.json":digest(QUALITY_BYTES),
        "quality.json":digest(config.as_bytes()),"ruff":FORMATTER_SHA256,
        "pyproject.toml":digest(quality::PROFILE_TEXT.as_bytes())
    }})
}

/// Package trusted compiler assets. This build command is never part of a request.
pub fn package_assets(destination: &Path, formatter: &Path) -> Result<Value, SomError> {
    let binary = std::fs::read(formatter).map_err(|e| err("ASSET_UNAVAILABLE", e.to_string()))?;
    if digest(&binary) != FORMATTER_SHA256 {
        return Err(err(
            "ASSET_HASH_MISMATCH",
            "native formatter digest differs",
        ));
    }
    if destination.exists() {
        return Err(err("INVALID_VALUE", "asset destination already exists"));
    }
    std::fs::create_dir_all(destination).map_err(|e| err("ASSET_UNAVAILABLE", e.to_string()))?;
    let config = flow::canonical(&formatter_config()) + "\n";
    for (name, bytes) in [
        ("blocks.json", CURRENT_BYTES),
        ("legacy-blocks.json", LEGACY_BYTES),
        ("quality-blocks.json", QUALITY_BYTES),
        ("quality.json", config.as_bytes()),
        ("ruff", binary.as_slice()),
        ("pyproject.toml", quality::PROFILE_TEXT.as_bytes()),
    ] {
        std::fs::write(destination.join(name), bytes)
            .map_err(|e| err("ASSET_UNAVAILABLE", e.to_string()))?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(
            destination.join("ruff"),
            std::fs::Permissions::from_mode(0o755),
        )
        .map_err(|e| err("ASSET_UNAVAILABLE", e.to_string()))?;
    }
    let manifest = asset_manifest();
    std::fs::write(
        destination.join("manifest.json"),
        flow::canonical(&manifest) + "\n",
    )
    .map_err(|e| err("ASSET_UNAVAILABLE", e.to_string()))?;
    // Verify all files and the real native tool before reporting successful packaging.
    let _ = Compiler::load(destination)?;
    Ok(manifest)
}

pub struct Compiler {
    legacy: BTreeMap<String, Block>,
    current: BTreeMap<String, Block>,
    quality_blocks: BTreeMap<String, Block>,
    quality: quality::Quality,
    pub manifest_sha256: String,
}

impl Compiler {
    pub fn load(directory: &Path) -> Result<Self, SomError> {
        let bytes = std::fs::read(directory.join("manifest.json"))
            .map_err(|e| err("ASSET_UNAVAILABLE", e.to_string()))?;
        let text = std::str::from_utf8(&bytes)
            .map_err(|_| err("ASSET_HASH_MISMATCH", "asset manifest is not UTF-8"))?;
        let manifest = crate::strict_json::parse(text)
            .map_err(|_| {
                err(
                    "ASSET_HASH_MISMATCH",
                    "invalid or duplicate asset manifest members",
                )
            })?
            .value;
        let expected = asset_manifest();
        if manifest != expected {
            return Err(err(
                "ASSET_HASH_MISMATCH",
                "asset manifest does not match this compiled release",
            ));
        }
        let mut admitted = BTreeMap::new();
        for (name, hash) in expected["files"].as_object().unwrap() {
            let bytes = std::fs::read(directory.join(name))
                .map_err(|e| err("ASSET_UNAVAILABLE", format!("{name}: {e}")))?;
            if hash != &json!(digest(&bytes)) {
                return Err(err("ASSET_HASH_MISMATCH", format!("{name} digest differs")));
            }
            admitted.insert(name.clone(), bytes);
        }
        let legacy = crate::load_blocks_from_bytes(&admitted["legacy-blocks.json"])?;
        let current = crate::load_blocks_from_bytes(&admitted["blocks.json"])?;
        let quality_blocks = crate::load_blocks_from_bytes(&admitted["quality-blocks.json"])?;
        let quality = quality::Quality::load_pinned(directory, FORMATTER_SHA256)?
            .ok_or_else(|| err("ASSET_UNAVAILABLE", "native quality assets missing"))?;
        composed::verify_blocks_with(&quality_blocks, &quality.catalog)?;
        composed::verify_blocks_with(&current, &flow::COMPACT_CATALOG)?;
        python_names::verify_variants(&legacy, "v1-v5")?;
        python_names::verify_variants(&quality_blocks, "v6")?;
        python_names::verify_variants(&current, "v7")?;
        Ok(Self {
            legacy,
            current,
            quality_blocks,
            quality,
            manifest_sha256: canonical_hash(&manifest),
        })
    }
    fn planner_assets(&self) -> planner::PlannerAssets<'_> {
        planner::PlannerAssets {
            legacy: &self.legacy,
            quality_blocks: &self.quality_blocks,
            current: &self.current,
            quality_catalog: &self.quality.catalog,
            compact_catalog: &flow::COMPACT_CATALOG,
        }
    }
    pub fn descriptors(&self, prompt: &Value) -> Value {
        let version = prompt["version"].as_str().unwrap_or("");
        let flow_request = version == flow::VERSION;
        let formatted = flow_request || version == composed::VERSION;
        let catalog = if flow_request {
            flow::COMPACT_CATALOG_TEXT
        } else if formatted {
            quality::CATALOG_TEXT
        } else {
            POLICY
        };
        let catalog_id = if flow_request {
            "som.compiler-catalog.flow.v1"
        } else if formatted {
            "som.compiler-catalog.quality.v1"
        } else {
            "som.compiler-catalog.legacy.v1"
        };
        let catalog_manifest = json!({"version":catalog_id,"catalog_sha256":digest(catalog.as_bytes()),"selection_policy_sha256":digest(POLICY.as_bytes())});
        let blocks = if flow_request {
            CURRENT_BYTES
        } else if formatted {
            QUALITY_BYTES
        } else {
            LEGACY_BYTES
        };
        let rows: Value = serde_json::from_slice(blocks).expect("compiled approved blocks");
        let rows: BTreeMap<String, Value> = rows
            .as_array()
            .unwrap()
            .iter()
            .map(|block| {
                (
                    block["id"].as_str().unwrap().to_owned(),
                    json!({"version":block["version"],"source_sha256":canonical_hash(block)}),
                )
            })
            .collect();
        let block_manifest = json!({"version":"som.compiler-block-library.v1","file_sha256":digest(blocks),"blocks":rows});
        let formatter = if formatted {
            described("som.compiler-formatter.v1", &formatter_config())
        } else {
            described(
                "som.compiler-render-only.v1",
                &json!({"version":"som.compiler-render-only.v1","formatter":null}),
            )
        };
        let mut semantic = BTreeMap::new();
        if version == "som.prompt.v5" || version == composed::VERSION || flow_request {
            semantic.insert(
                "related-business.v1",
                descriptor("related-business.v1", composed::CONTRACT_TEXT.as_bytes()),
            );
        }
        if flow_request {
            semantic.insert(
                "related-flow.v1",
                descriptor("related-flow.v1", flow::CONTRACT_TEXT.as_bytes()),
            );
        }
        json!({
            "grammar":descriptor("som.cnl.v1",GRAMMAR.as_bytes()),
            "compiler_contract":descriptor("som.compiler-contract.v1",CONTRACT.as_bytes()),
            "selection_policy":descriptor("som.compiler-selection-policy.v1.2",POLICY.as_bytes()),
            "catalog":described(catalog_id,&catalog_manifest),
            "block_library":described("som.compiler-block-library.v1",&block_manifest),
            "backend":described("som.compiler-backend.v1",&backend_manifest()),
            "formatter":formatter,
            "literal_policy":descriptor("som.compiler-literals.v1",LITERALS.as_bytes()),
            "serializer":descriptor("som.canonical-json.v1",SERIALIZER.as_bytes()),
            "semantic_contracts":semantic.into_values().collect::<Vec<_>>()
        })
    }
    /// Read-only expected roles used to locate legacy bundle errors.
    /// This never replaces or repairs the caller's supplied plan.
    pub fn diagnostic_legacy_plan(&self, prompt: &Value) -> Option<Value> {
        if !matches!(
            prompt["version"].as_str(),
            Some(
                "som.prompt.v1"
                    | "som.prompt.v2"
                    | "som.prompt.v3"
                    | "som.prompt.v4"
                    | "som.prompt.v5"
            )
        ) || crate::validate_prompt(prompt).is_err()
        {
            return None;
        }
        planner::plan(prompt, &self.planner_assets()).ok()
    }
    pub fn compile_prompt(&self, prompt: &Value) -> Result<Value, SomError> {
        crate::validate_prompt(prompt)?;
        let dsl = planner::plan(prompt, &self.planner_assets())?;
        self.validate_plan(prompt, &dsl)?;
        let mut bundle = json!({"version":BUNDLE_VERSION,"prompt":prompt,"dsl":dsl,"assets":self.descriptors(prompt)});
        let payload_hash = canonical_hash(&bundle);
        bundle["hashes"] = json!({"prompt_sha256":canonical_hash(prompt),"dsl_sha256":canonical_hash(&bundle["dsl"]),"payload_sha256":payload_hash});
        if flow::canonical(&json!({"bundle":bundle})).len() > ASSEMBLE_BODY_LIMIT {
            return Err(err(
                "SCOPE_TOO_LARGE",
                "complete bundle exceeds the assemble transport limit",
            ));
        }
        Ok(bundle)
    }
    fn validate_plan(&self, prompt: &Value, dsl: &Value) -> Result<(), SomError> {
        match prompt["version"].as_str() {
            Some(flow::VERSION) => flow::validate_plan_with(
                prompt,
                &python_names::base_plan(prompt, dsl)?,
                &flow::COMPACT_CATALOG,
            ),
            Some(composed::VERSION) => composed::validate_plan_with(
                prompt,
                &python_names::base_plan(prompt, dsl)?,
                &self.quality.catalog,
            ),
            _ => {
                // Legacy prompt families have no catalog semantic-plan validator.
                // Check required roles/bindings/dependency edges without replacing the supplied plan.
                let expected = planner::plan(prompt, &self.planner_assets())?;
                validate_legacy_roles(dsl, &expected)
            }
        }
    }
    pub fn assemble_bundle(&self, bundle: &Value) -> Result<BTreeMap<String, String>, SomError> {
        crate::object(
            bundle,
            &["version", "prompt", "dsl", "assets", "hashes"],
            "bundle",
        )?;
        if bundle["version"] != BUNDLE_VERSION {
            return Err(err("UNSUPPORTED_VERSION", "unsupported DSL bundle version"));
        }
        crate::object(
            &bundle["hashes"],
            &["prompt_sha256", "dsl_sha256", "payload_sha256"],
            "bundle hashes",
        )?;
        let payload = json!({"version":bundle["version"],"prompt":bundle["prompt"],"dsl":bundle["dsl"],"assets":bundle["assets"]});
        for (field, expected) in [
            ("prompt_sha256", canonical_hash(&bundle["prompt"])),
            ("dsl_sha256", canonical_hash(&bundle["dsl"])),
            ("payload_sha256", canonical_hash(&payload)),
        ] {
            if bundle["hashes"][field] != expected {
                return Err(err("BUNDLE_HASH_MISMATCH", format!("{field} differs")));
            }
        }
        let prompt = &bundle["prompt"];
        let dsl = &bundle["dsl"];
        crate::validate_prompt(prompt)?;
        if bundle["assets"] != self.descriptors(prompt) {
            return Err(err(
                "ASSET_HASH_MISMATCH",
                "bundle asset identity or digest is not admitted",
            ));
        }
        self.validate_plan(prompt, dsl)?;
        if prompt["version"] == flow::VERSION {
            let resolved = flow_compact::resolve(prompt, dsl)?;
            self.quality.finalize_with_byte_limit(
                flow_compact::render_resolved(&resolved, &self.current)?,
                None,
            )
        } else {
            let resolved = crate::resolve_prompt_refs(prompt, dsl)?;
            let formatted = prompt["version"] == composed::VERSION;
            let files = crate::assemble(
                &resolved,
                if formatted {
                    &self.quality_blocks
                } else {
                    &self.legacy
                },
            )?;
            if formatted {
                self.quality.finalize(files)
            } else {
                Ok(files)
            }
        }
    }
    pub fn generate(&self, prompt: &Value) -> Result<BTreeMap<String, String>, SomError> {
        self.assemble_bundle(&self.compile_prompt(prompt)?)
    }
    pub fn health(&self) -> Value {
        json!({"status":"ok","purpose":PURPOSE,"compiler_version":PURPOSE,"model_loaded":false,
            "blocks_loaded":true,"block_count":self.current.len(),"legacy_block_count":self.legacy.len(),
            "blocks_sha256":digest(CURRENT_BYTES),"quality_blocks_sha256":digest(QUALITY_BYTES),"quality_block_count":self.quality_blocks.len(),"legacy_blocks_sha256":digest(LEGACY_BYTES),
            "asset_manifest_sha256":self.manifest_sha256,"backend_sha256":canonical_hash(&backend_manifest()),
            "grammar_version":"som.cnl.v1","grammar_sha256":digest(GRAMMAR.as_bytes()),
            "selection_policy_version":"som.compiler-selection-policy.v1.2","selection_policy_sha256":digest(POLICY.as_bytes()),
            "bundle_version":BUNDLE_VERSION,"prompt_versions":(1..=7).map(|v|format!("som.prompt.v{v}")).collect::<Vec<_>>(),
            "quality_contract":quality::VERSION,"formatter_version":quality::FORMATTER_VERSION,
            "formatter_sha256":FORMATTER_SHA256,"compact_output_bytes":"observed",
            "request_body_limits":{"compile":COMPILE_BODY_LIMIT,"generate":COMPILE_BODY_LIMIT,"assemble":ASSEMBLE_BODY_LIMIT}})
    }
}

fn validate_legacy_roles(actual: &Value, expected: &Value) -> Result<(), SomError> {
    crate::object(actual, &["version", "files"], "legacy DSL")?;
    if actual["version"] != expected["version"] {
        return Err(err("INVALID_DSL", "prompt and DSL versions do not match"));
    }
    let rows = actual["files"]
        .as_array()
        .ok_or_else(|| err("INVALID_DSL", "files must be an array"))?;
    let max = if actual["version"] == crate::DSL_VERSION {
        8
    } else {
        12
    };
    let count: usize = rows
        .iter()
        .filter_map(|f| f["blocks"].as_array())
        .map(Vec::len)
        .sum();
    if rows.len() > max || count > 64 {
        return Err(err("SCOPE_TOO_LARGE", "plan exceeds file/block limit"));
    }
    let expected_files = expected["files"].as_array().unwrap();
    if rows.len() != expected_files.len() {
        return Err(err(
            "INVALID_COMPOSITION",
            "missing or extra legacy file role",
        ));
    }
    let mut seen = BTreeSet::new();
    for file in rows {
        crate::object(file, &["path", "blocks"], "file")?;
        let path = file["path"]
            .as_str()
            .ok_or_else(|| err("INVALID_PATH", "file path must be a string"))?;
        if !seen.insert(path) {
            return Err(err("INVALID_DSL", "duplicate file"));
        }
        let wanted = expected_files
            .iter()
            .find(|f| f["path"] == path)
            .ok_or_else(|| err("INVALID_PATH", "unknown legacy file role"))?;
        let blocks = file["blocks"]
            .as_array()
            .ok_or_else(|| err("INVALID_DSL", "blocks must be an array"))?;
        let desired = wanted["blocks"].as_array().unwrap();
        if blocks.len() != desired.len() {
            return Err(err(
                "INVALID_COMPOSITION",
                "missing or extra legacy block role",
            ));
        }
        let mut names = BTreeMap::new();
        let mut expected_names = BTreeMap::new();
        for (index, (block, target)) in blocks.iter().zip(desired).enumerate() {
            crate::object(block, &["id", "name", "params", "refs"], "block")?;
            if block["id"] != target["id"] {
                return Err(err(
                    "INVALID_COMPOSITION",
                    "legacy block role or operation order differs",
                ));
            }
            if block["params"] != target["params"] {
                return Err(err("INVALID_REF", "legacy role bindings differ"));
            }
            let name = block["name"]
                .as_str()
                .ok_or_else(|| err("INVALID_REF", "block name must be a string"))?;
            if names.contains_key(name) {
                return Err(err("INVALID_REF", "duplicate block name"));
            }
            let refs = block["refs"]
                .as_array()
                .ok_or_else(|| err("INVALID_REF", "refs must be an array"))?;
            let mut actual_edges = BTreeSet::new();
            for reference in refs {
                let name = reference
                    .as_str()
                    .ok_or_else(|| err("INVALID_REF", "legacy dependency must be a name"))?;
                let target = names
                    .get(name)
                    .ok_or_else(|| err("INVALID_REF", "unknown or forward legacy dependency"))?;
                if !actual_edges.insert(*target) {
                    return Err(err("INVALID_REF", "duplicate legacy dependency"));
                }
            }
            for reference in target["refs"].as_array().unwrap() {
                let edge = expected_names.get(reference.as_str().unwrap()).unwrap();
                if !actual_edges.contains(edge) {
                    return Err(err("INVALID_REF", "missing legacy dependency"));
                }
            }
            names.insert(name, index);
            expected_names.insert(target["name"].as_str().unwrap(), index);
        }
    }
    Ok(())
}
