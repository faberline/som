//! Server emission from the same checked v2 bundle as the client (#161).
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use som_service::{Error, assemble_json, assemble_server, compile_prompt, compile_prompt_v2};

const PROMPT_V2: &str = include_str!("fixtures/defer-four.prompt.json");
const V1_PROMPT: &str = include_str!("../contract/prompt.json");
const SERVER_CARGO: &str = include_str!("../templates/server_Cargo.toml.txt");
const GOLDEN: &str = include_str!("fixtures/defer-four.server.rs");

fn value(text: &str) -> Value {
    serde_json::from_str(text).expect("independent JSON fixture")
}

fn ok<T>(result: Result<T, Error>) -> T {
    match result {
        Ok(value) => value,
        Err(error) => panic!("unexpected error {}: {}", error.status, error.body),
    }
}

fn refused<T>(result: Result<T, Error>, status: u16, code: &str) -> Error {
    let error = match result {
        Ok(_) => panic!("expected {code}, but request was accepted"),
        Err(error) => error,
    };
    assert_eq!(error.status, status, "{}", error.body);
    assert_eq!(error.body["code"], code, "{}", error.body);
    error
}

fn pointer(error: &Error, expected: &str) {
    assert_eq!(
        error.body["diagnostics"][0]["prompt_pointer"], expected,
        "{}",
        error.body
    );
}

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn rehash(bundle: &mut Value) {
    let payload = json!({
        "version": bundle["version"], "assets": bundle["assets"],
        "prompt": bundle["prompt"], "dsl": bundle["dsl"],
    });
    bundle["digest"] = json!(sha(&serde_json::to_vec(&payload).unwrap()));
}

#[test]
fn server_files_for_four_operations() {
    let files = ok(assemble_server(&ok(compile_prompt_v2(&value(PROMPT_V2)))));
    let names: Vec<&str> = files.keys().map(String::as_str).collect();
    assert_eq!(names, ["Cargo.toml", "README.md", "src/lib.rs"]);

    assert_eq!(
        files["Cargo.toml"],
        SERVER_CARGO.replace("{{PACKAGE}}", "defer_client_server")
    );

    let lib = &files["src/lib.rs"];
    assert!(!lib.contains("{{"), "placeholder left in src/lib.rs");

    // The golden has three parts, split by a `//---` line: trait methods,
    // routes and handlers. Each must appear in src/lib.rs, in that order.
    let parts: Vec<&str> = GOLDEN.split("\n//---\n").collect();
    assert_eq!(parts.len(), 3, "golden must have three parts");
    let mut at = 0;
    for part in parts {
        let part = part.trim_end();
        let found = lib[at..]
            .find(part)
            .unwrap_or_else(|| panic!("golden part missing or out of order:\n{part}"));
        at += found + part.len();
    }
}

#[test]
fn server_refuses_a_v1_bundle() {
    let v1 = ok(compile_prompt(&value(V1_PROMPT)));
    pointer(
        &refused(assemble_server(&v1), 400, "INVALID_BUNDLE"),
        "/bundle/prompt/version",
    );
}

#[test]
fn server_checks_the_bundle_like_the_client() {
    let bundle = ok(compile_prompt_v2(&value(PROMPT_V2)));

    let mut changed = bundle.clone();
    changed["dsl"]["operations"][0]["method"] = json!("DELETE");
    rehash(&mut changed);
    pointer(
        &refused(assemble_server(&changed), 400, "INVALID_BUNDLE"),
        "/bundle/dsl",
    );

    let mut zeroed = bundle.clone();
    zeroed["digest"] = json!("0".repeat(64));
    pointer(
        &refused(assemble_server(&zeroed), 400, "INVALID_BUNDLE"),
        "/bundle/digest",
    );

    let mut reassets = bundle.clone();
    reassets["assets"]["sha256"] = json!("0".repeat(64));
    refused(assemble_server(&reassets), 400, "ASSET_HASH_MISMATCH");
}

#[test]
fn assemble_json_emit() {
    let bundle = ok(compile_prompt_v2(&value(PROMPT_V2)));

    let plain = ok(assemble_json(&json!({"bundle": bundle}).to_string()));
    let client = ok(assemble_json(
        &json!({"bundle": bundle, "emit": "client"}).to_string(),
    ));
    assert_eq!(client, plain);

    let server = ok(assemble_json(
        &json!({"bundle": bundle, "emit": "server"}).to_string(),
    ));
    let names: Vec<&str> = server["files"]
        .as_object()
        .expect("files object")
        .keys()
        .map(String::as_str)
        .collect();
    assert_eq!(names, ["Cargo.toml", "README.md", "src/lib.rs"]);

    let web = refused(
        assemble_json(&json!({"bundle": bundle, "emit": "web"}).to_string()),
        400,
        "INVALID_SCHEMA",
    );
    pointer(&web, "/emit");

    refused(
        assemble_json(&json!({"bundle": bundle, "other": 1}).to_string()),
        400,
        "INVALID_SCHEMA",
    );
}

#[test]
fn server_keeps_aliases_and_argument_names() {
    let mut prompt = value(PROMPT_V2);
    prompt["operations"] = json!([{
        "alias": "drop_task", "name": "task_cancel",
        "inputs": [
            {"name": "body", "type": "string", "parameter": "queue"},
            {"name": "status", "type": "string", "parameter": "task_id"}
        ]
    }]);
    let files = ok(assemble_server(&ok(compile_prompt_v2(&prompt))));
    let lib = &files["src/lib.rs"];
    assert!(lib.contains("fn drop_task("), "{lib}");
    assert!(lib.contains("body: String,"), "{lib}");
    assert!(lib.contains("status: String,"), "{lib}");
    assert!(lib.contains("handle_drop_task::<H>"), "{lib}");
    assert!(lib.contains("let (body, status) = __path;"), "{lib}");
}

#[test]
fn server_uses_core_error_envelope() {
    assert!(SERVER_CARGO.contains(
        "service-http = { git = \"https://github.com/faberline/core\", tag = \"v0.5.0\" }"
    ));
    let files = ok(assemble_server(&ok(compile_prompt_v2(&value(PROMPT_V2)))));
    let lib = &files["src/lib.rs"];
    assert!(
        lib.contains("pub use service_http::ErrorEnvelope;"),
        "{lib}"
    );
    assert!(!lib.contains("pub struct ErrorEnvelope"), "{lib}");
    assert!(!lib.contains("{{"), "placeholder left in src/lib.rs");
}
