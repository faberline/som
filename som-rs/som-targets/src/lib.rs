//! Target-aware compile requests around the unchanged compiler-v1 library (issues #47 and #48).
//!
//! The som library sources stay byte-identical. Its backend descriptor hashes those
//! sources, so every Python bundle keeps the bytes the accepted compiler-v1 returns.
//! The check order is the frozen contracts/rust-output-v1/api.schema.json x-check-order.
pub mod profile;
pub mod rust;
pub mod typescript;

use serde_json::{Value, json};
use som::{
    cnl::{CompilerError, Diagnostic, SourceMap, SourceSpan},
    compiler::Compiler,
    strict_json::{self, Document},
};

pub const TARGET_CAUSE: &str = "Target must be the string python, rust, typescript or react.";
pub const TARGET_ALLOWED: &str = "Omit target, or use target python, rust, typescript or react.";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Target {
    Python,
    Rust,
    TypeScript,
}

/// A complete HTTP reply: status code and JSON body.
pub type Reply = (u16, Value);

/// The compiler-v1 status mapping, plus the codes this contract adds.
pub fn status(code: &str) -> u16 {
    match code {
        "UNSUPPORTED_BLOCK"
        | "UNSUPPORTED_CONTRACT"
        | "UNSUPPORTED_VERSION"
        | "SCOPE_TOO_LARGE"
        | "UNSUPPORTED_LOGIC"
        | "UNSUPPORTED_TEST"
        | "UNSUPPORTED_TARGET" => 422,
        "ASSET_UNAVAILABLE" => 503,
        "MODEL_FAILED" | "MODEL_UNAVAILABLE" | "OUTPUT_QUALITY_FAILED" => 500,
        "NOT_IMPLEMENTED" => 501,
        "MODEL_TIMEOUT" => 504,
        _ => 400,
    }
}

pub(crate) fn rejected(error: Value) -> Reply {
    (
        status(error["code"].as_str().unwrap_or("INVALID_SCHEMA")),
        error,
    )
}

/// Exactly the required members, plus an optional target member.
fn envelope(value: &Value, keys: &[&str]) -> bool {
    value.as_object().is_some_and(|members| {
        let optional = usize::from(members.contains_key("target"));
        members.len() == keys.len() + optional && keys.iter().all(|key| members.contains_key(*key))
    })
}

/// Read the optional target member, located on its raw value token when refused.
pub fn select(doc: &Document) -> Result<Target, Value> {
    match doc.value.get("target") {
        None => Ok(Target::Python),
        Some(Value::String(target)) if target == "python" => Ok(Target::Python),
        Some(Value::String(target)) if target == "rust" => Ok(Target::Rust),
        Some(Value::String(target)) if target == "typescript" => Ok(Target::TypeScript),
        Some(_) => Err(doc.error("/target", "INVALID_SCHEMA", TARGET_CAUSE, TARGET_ALLOWED)),
    }
}

/// Locate a CNL refusal on the source token of its prompt pointer.
/// Every profile pointer is mapped by the CNL parser today. The parent fallback is a guard:
/// it keeps the exact pointer and widens only the span, unlike `Document::error`.
fn cnl_diagnostic(map: &SourceMap, pointer: &str, code: &str, cause: &str, allowed: &str) -> Value {
    let mut selected = pointer.to_owned();
    let span = loop {
        if let Some(span) = map.get(&selected) {
            break span.clone();
        }
        if selected.is_empty() {
            break SourceSpan {
                line: 1,
                column: 1,
                end_line: 1,
                end_column: 1,
                byte_start: 0,
                byte_end: 0,
            };
        }
        selected.truncate(selected.rfind('/').unwrap_or(0));
    };
    CompilerError {
        status: "error".into(),
        code: code.into(),
        detail: cause.into(),
        diagnostics: vec![Diagnostic {
            code: code.into(),
            span,
            prompt_pointer: Some(pointer.into()),
            cause: cause.into(),
            allowed_form: allowed.into(),
        }],
    }
    .response()
}

/// Where a prompt came from, so a diagnostic can locate its source token.
enum Origin<'a> {
    Cnl(&'a SourceMap),
    Json,
}

fn profile_refusal(doc: &Document, origin: Origin, found: &profile::Refusal) -> Reply {
    let body = match origin {
        Origin::Cnl(map) => cnl_diagnostic(
            map,
            &found.pointer,
            &found.code,
            &found.cause,
            &found.allowed_form,
        ),
        Origin::Json => doc.error(
            &format!("/prompt{}", found.pointer),
            &found.code,
            &found.cause,
            &found.allowed_form,
        ),
    };
    rejected(body)
}

fn compile_rust(
    compiler: &Compiler,
    doc: &Document,
    prompt: &Value,
    origin: Origin,
    pointer: &str,
) -> Reply {
    // Step 4: compiler-v1 semantic validation, unchanged. CNL parsing already ran it.
    if let Origin::Json = origin
        && let Err(error) = som::validate_prompt(prompt)
    {
        return rejected(som::json_diagnostics::prompt_error(
            doc, prompt, &error, "/prompt",
        ));
    }
    // Step 5: the Rust profile.
    if let Some(found) = profile::active(prompt) {
        return profile_refusal(doc, origin, &found);
    }
    // Step 6: planning.
    rust::compile(compiler, doc, prompt, pointer)
}

fn compile_typescript(
    compiler: &Compiler,
    doc: &Document,
    prompt: &Value,
    origin: Origin,
    pointer: &str,
) -> Reply {
    // Step 4: compiler-v1 semantic validation, unchanged. CNL parsing already ran it.
    if let Origin::Json = origin
        && let Err(error) = som::validate_prompt(prompt)
    {
        return rejected(som::json_diagnostics::prompt_error(
            doc, prompt, &error, "/prompt",
        ));
    }
    // Step 5: the TypeScript phase 1 profile.
    if let Some(found) = typescript::profile::check(prompt) {
        return profile_refusal(doc, origin, &found);
    }
    // Step 6: planning.
    typescript::compile(compiler, doc, prompt, pointer)
}

/// Assemble a som.dsl-bundle.v2 bundle. A bundle whose target member is "typescript"
/// runs the TypeScript v2 assembler; every other v2 bundle runs the Rust v2 assembler
/// (which checks members, version and target in the frozen check_schema order).
pub fn assemble(compiler: &Compiler, doc: &Document) -> Reply {
    if doc.value["bundle"]["target"] == typescript::TARGET {
        typescript::assemble(compiler, doc)
    } else {
        rust::assemble(compiler, doc)
    }
}

/// One compile request body, in the frozen check order.
/// Without target, or with target python, the reply is the compiler-v1 reply byte for byte.
pub fn compile(compiler: &Compiler, source: &str) -> Reply {
    let doc = match strict_json::parse(source) {
        Ok(doc) => doc,
        Err(error) => return rejected(error),
    };
    let (target, prompt, map, semantic_pointer) = match doc.value["format"].as_str() {
        Some("som.cnl.v1") => {
            if !envelope(&doc.value, &["format", "source"]) || !doc.value["source"].is_string() {
                return rejected(doc.error(
                    "",
                    "INVALID_SCHEMA",
                    "CNL compile requires only format and a source string.",
                    "{format: som.cnl.v1, source: CNL text}",
                ));
            }
            let target = match select(&doc) {
                Ok(target) => target,
                Err(error) => return rejected(error),
            };
            match som::cnl::parse(doc.value["source"].as_str().unwrap()) {
                Ok(parsed) => (target, parsed.prompt, Some(parsed.source_map), "/source"),
                Err(error) => return (error.http_status(), error.response()),
            }
        }
        Some("som.prompt-json.v1") => {
            if !envelope(&doc.value, &["format", "prompt"]) {
                return rejected(doc.error(
                    "",
                    "INVALID_SCHEMA",
                    "JSON compile requires only format and prompt.",
                    "{format: som.prompt-json.v1, prompt: typed prompt}",
                ));
            }
            let target = match select(&doc) {
                Ok(target) => target,
                Err(error) => return rejected(error),
            };
            (target, doc.value["prompt"].clone(), None, "/prompt")
        }
        _ => {
            return rejected(doc.error(
                "/format",
                "UNSUPPORTED_VERSION",
                "An explicit supported compile format is required.",
                "som.cnl.v1 or som.prompt-json.v1",
            ));
        }
    };
    let origin = map.as_ref().map_or(Origin::Json, Origin::Cnl);
    match target {
        Target::Rust => {
            return compile_rust(compiler, &doc, &prompt, origin, semantic_pointer);
        }
        Target::TypeScript => {
            return compile_typescript(compiler, &doc, &prompt, origin, semantic_pointer);
        }
        Target::Python => {}
    }
    match compiler.compile_prompt(&prompt) {
        Ok(bundle) => (200, json!({"status":"ok","bundle":bundle})),
        Err(error) if semantic_pointer == "/prompt" => rejected(
            som::json_diagnostics::prompt_error(&doc, &prompt, &error, "/prompt"),
        ),
        Err(error) => rejected(doc.error(
            semantic_pointer,
            error.code,
            &error.detail,
            "A prompt that meets the pinned compiler contract.",
        )),
    }
}
