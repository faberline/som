pub mod bundle;
pub mod error;
mod lower;
pub mod model;
pub mod openapi;
pub mod render;
pub mod server;
mod strict_json;

pub use error::{Error, SourceSpan};
use serde_json::{Value, json};
use std::collections::BTreeMap;

pub const COMPILE_BODY_LIMIT: usize = 262144;
pub const ASSEMBLE_BODY_LIMIT: usize = 2097152;

#[derive(Debug)]
pub struct Compilation {
    pub prompt: Value,
    pub source_map: BTreeMap<String, SourceSpan>,
    pub bundle: Value,
}
pub fn compile_cnl(source: &str) -> Result<Compilation, Error> {
    if source.len() > COMPILE_BODY_LIMIT {
        return Err(Error::new(
            "REQUEST_TOO_LARGE",
            "CNL source exceeds the request limit.",
            "/source",
        ));
    }
    let (prompt, source_map) = lower::parse(source)?;
    let bundle = compile_prompt(&prompt).map_err(|e| e.with_map(&source_map))?;
    Ok(Compilation {
        prompt,
        source_map,
        bundle,
    })
}
pub fn compile_prompt(prompt: &Value) -> Result<Value, Error> {
    bundle::compile(prompt)
}
pub fn compile_prompt_v2(prompt: &Value) -> Result<Value, Error> {
    bundle::compile_v2(prompt)
}
pub fn assemble(bundle: &Value) -> Result<BTreeMap<String, String>, Error> {
    bundle::assemble(bundle)
}
pub fn assemble_server(bundle: &Value) -> Result<BTreeMap<String, String>, Error> {
    bundle::assemble_server(bundle)
}
pub fn health() -> Value {
    json!({"status":"ok","compiler":"som-service.v1",
        "formats":[model::CNL_FORMAT,model::JSON_FORMAT,model::JSON_FORMAT_V2],
        "prompt_versions":[model::PROMPT_VERSION,model::PROMPT_VERSION_V2],
        "dsl_versions":[model::DSL_VERSION,model::DSL_VERSION_V2],"targets":[model::TARGET],
        "model_loaded":false,"assets":bundle::identity()})
}
fn json_error(doc: &strict_json::Document, error: Error, prefix: &str) -> Error {
    let pointer = error.body["diagnostics"][0]["prompt_pointer"]
        .as_str()
        .unwrap_or("");
    let location = format!("{prefix}{pointer}");
    Error::from_body(
        doc.error(
            &location,
            error.body["code"].as_str().unwrap_or("INVALID_SCHEMA"),
            error.body["detail"].as_str().unwrap_or("Invalid request."),
            error.body["diagnostics"][0]["allowed_form"]
                .as_str()
                .unwrap_or("A complete service request."),
        ),
    )
}
pub fn compile_json(source: &str) -> Result<Value, Error> {
    if source.len() > COMPILE_BODY_LIMIT {
        return Err(Error::new(
            "REQUEST_TOO_LARGE",
            "Compile request exceeds the body limit.",
            "",
        ));
    }
    let doc = strict_json::parse_service(source).map_err(Error::from_body)?;
    match doc.value["format"].as_str() {
        Some(model::CNL_FORMAT) => {
            model::exact(&doc.value, &["format", "source"], "")
                .map_err(|e| json_error(&doc, e, ""))?;
            let text = doc.value["source"].as_str().ok_or_else(|| {
                json_error(
                    &doc,
                    Error::new("INVALID_SCHEMA", "Expected a source string.", "/source"),
                    "",
                )
            })?;
            let result = compile_cnl(text)?;
            Ok(
                json!({"status":"ok","prompt":result.prompt,"source_map":result.source_map,"bundle":result.bundle}),
            )
        }
        Some(model::JSON_FORMAT) => {
            model::exact(&doc.value, &["format", "prompt"], "")
                .map_err(|e| json_error(&doc, e, ""))?;
            let result =
                compile_prompt(&doc.value["prompt"]).map_err(|e| json_error(&doc, e, "/prompt"))?;
            Ok(json!({"status":"ok","bundle":result}))
        }
        Some(model::JSON_FORMAT_V2) => {
            model::exact(&doc.value, &["format", "prompt"], "")
                .map_err(|e| json_error(&doc, e, ""))?;
            let result = compile_prompt_v2(&doc.value["prompt"])
                .map_err(|e| json_error(&doc, e, "/prompt"))?;
            Ok(json!({"status":"ok","bundle":result}))
        }
        _ => Err(json_error(
            &doc,
            Error::new(
                "UNSUPPORTED_VERSION",
                "An explicit service compile format is required.",
                "/format",
            ),
            "",
        )),
    }
}
pub fn assemble_json(source: &str) -> Result<Value, Error> {
    if source.len() > ASSEMBLE_BODY_LIMIT {
        return Err(Error::new(
            "REQUEST_TOO_LARGE",
            "Assemble request exceeds the body limit.",
            "",
        ));
    }
    let doc = strict_json::parse_service(source).map_err(Error::from_body)?;
    let emit = if doc.value.get("emit").is_some() {
        model::exact(&doc.value, &["bundle", "emit"], "").map_err(|e| json_error(&doc, e, ""))?;
        doc.value["emit"].as_str().unwrap_or("")
    } else {
        model::exact(&doc.value, &["bundle"], "").map_err(|e| json_error(&doc, e, ""))?;
        "client"
    };
    let files = match emit {
        "client" => assemble(&doc.value["bundle"]),
        "server" => assemble_server(&doc.value["bundle"]),
        _ => Err(Error::new(
            "INVALID_SCHEMA",
            "Expected emit to be client or server.",
            "/emit",
        )),
    }
    .map_err(|e| json_error(&doc, e, ""))?;
    Ok(json!({"status":"ok","files":files}))
}
