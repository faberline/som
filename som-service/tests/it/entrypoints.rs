//! Rust port of the checks in `som-service/scripts/entrypoints.py`.
//!
//! Each test names the `entrypoints.py` check it ports. The legacy-binary
//! block is not ported: it needs the sealed legacy binary and the frozen
//! `som-rs` replay assets, so it stays in `entrypoints.py`.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use som_service::model::{CNL_FORMAT, JSON_FORMAT, JSON_FORMAT_V2};
use std::collections::BTreeSet;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::sync::mpsc;
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_som-service");

/// A running `som-service serve` child on a random port. Dropping it kills the child.
struct Server {
    child: Child,
    port: u16,
}

impl Server {
    fn start() -> Server {
        let mut child = Command::new(BIN)
            .args(["serve", "--port", "0"])
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn som-service serve");
        let stderr = child.stderr.take().expect("piped stderr");
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            // Keep draining stderr after the listening line, so the child never blocks on a full pipe.
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                if let Some(address) = line.strip_prefix("som-service listening on ") {
                    let _ = tx.send(address.to_string());
                }
            }
        });
        let address = match rx.recv_timeout(Duration::from_secs(20)) {
            Ok(address) => address,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                panic!("som-service did not report its listening address: {error}");
            }
        };
        let port = address
            .rsplit(':')
            .next()
            .and_then(|port| port.parse().ok())
            .expect("listening address has a port");
        Server { child, port }
    }

    fn url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Runtime::new().expect("tokio runtime")
}

/// Sends the raw bytes, so duplicate keys and malformed JSON reach the server unchanged.
async fn post(server: &Server, path: &str, body: Vec<u8>) -> (u16, Value) {
    let reply = reqwest::Client::new()
        .post(format!("{}{path}", server.url()))
        .header("content-type", "application/json")
        .body(body)
        .send()
        .await
        .expect("POST reaches the server");
    let status = reply.status().as_u16();
    let bytes = reply.bytes().await.expect("read the response body");
    let value = serde_json::from_slice(&bytes).unwrap_or_else(|error| {
        panic!(
            "response body is JSON ({error}): {}",
            String::from_utf8_lossy(&bytes)
        )
    });
    (status, value)
}

async fn get(server: &Server, path: &str) -> (u16, Value) {
    let reply = reqwest::Client::new()
        .get(format!("{}{path}", server.url()))
        .send()
        .await
        .expect("GET reaches the server");
    let status = reply.status().as_u16();
    let value = reply.json().await.expect("response body is JSON");
    (status, value)
}

fn cli(server: &Server, command: &str, file: &Path) -> Output {
    Command::new(BIN)
        .arg(command)
        .arg(file)
        .arg("--url")
        .arg(server.url())
        .output()
        .expect("run the som-service CLI")
}

fn stdout_json(output: &Output) -> Value {
    assert!(
        output.status.success(),
        "CLI failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("CLI stdout is JSON")
}

fn contract(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("contract")
        .join(name)
}

fn contract_text(name: &str) -> String {
    std::fs::read_to_string(contract(name)).unwrap_or_else(|error| panic!("read {name}: {error}"))
}

fn contract_json(name: &str) -> Value {
    serde_json::from_str(&contract_text(name))
        .unwrap_or_else(|error| panic!("parse {name}: {error}"))
}

fn temp_file(name: &str) -> PathBuf {
    std::env::temp_dir().join(format!("som-entrypoints-{}-{name}", std::process::id()))
}

/// `POST /compile` with the CNL envelope, which must succeed; returns the whole reply.
fn compile_cnl_over_http(rt: &tokio::runtime::Runtime, server: &Server) -> Value {
    let body = json!({"format": CNL_FORMAT, "source": contract_text("task-status.cnl")});
    let (status, reply) = rt.block_on(post(server, "/compile", body.to_string().into_bytes()));
    assert_eq!(status, 200, "compile CNL over HTTP: {reply}");
    reply
}

#[test]
fn service_health_and_version_isolation() {
    // entrypoints.py: "service health and version isolation"
    let server = Server::start();
    let rt = runtime();
    let (status, health) = rt.block_on(get(&server, "/health"));
    assert_eq!(status, 200, "{health}");
    assert_eq!(
        health["formats"],
        json!([CNL_FORMAT, JSON_FORMAT, JSON_FORMAT_V2])
    );
    assert_eq!(health["model_loaded"], json!(false));
}

#[test]
fn http_cnl_and_typed_input_share_frozen_prompt_and_dsl() {
    // entrypoints.py: "HTTP CNL and typed input share frozen prompt and DSL"
    let server = Server::start();
    let rt = runtime();
    let compiled = compile_cnl_over_http(&rt, &server);
    let bundle = &compiled["bundle"];
    assert_eq!(bundle["prompt"], contract_json("prompt.json"));
    assert_eq!(bundle["dsl"], contract_json("dsl.json"));

    let body = json!({"format": JSON_FORMAT, "prompt": contract_json("prompt.json")});
    let (status, typed) = rt.block_on(post(&server, "/compile", body.to_string().into_bytes()));
    assert_eq!(status, 200, "{typed}");
    assert_eq!(typed["bundle"], *bundle);
}

#[test]
fn cli_compile_generate_assemble_equal_http_output() {
    // entrypoints.py: "CLI compile/generate/assemble equal HTTP output"
    let server = Server::start();
    let rt = runtime();
    let bundle = compile_cnl_over_http(&rt, &server)["bundle"].clone();

    let bundle_file = temp_file("bundle.json");
    std::fs::write(
        &bundle_file,
        serde_json::to_string_pretty(&bundle).expect("serialize bundle"),
    )
    .expect("write bundle file");

    assert_eq!(
        stdout_json(&cli(&server, "compile", &contract("task-status.cnl"))),
        bundle
    );
    assert_eq!(
        stdout_json(&cli(&server, "generate", &contract("prompt.json"))),
        bundle
    );

    let (status, assembled) = rt.block_on(post(
        &server,
        "/assemble",
        json!({"bundle": bundle}).to_string().into_bytes(),
    ));
    assert_eq!(status, 200, "{assembled}");
    let files = stdout_json(&cli(&server, "assemble", &bundle_file));
    let _ = std::fs::remove_file(&bundle_file);
    assert_eq!(files, assembled["files"]);

    let keys: BTreeSet<&str> = files
        .as_object()
        .expect("files is an object")
        .keys()
        .map(String::as_str)
        .collect();
    assert_eq!(
        keys,
        BTreeSet::from(["Cargo.toml", "Cargo.lock", "src/lib.rs", "README.md"])
    );
}

#[test]
fn http_refusals_old_formats_malformed_duplicate_oversize_and_changed_bundle() {
    // entrypoints.py: "HTTP old/unknown formats, malformed and duplicate JSON, body limit and bundle refusal"
    let server = Server::start();
    let rt = runtime();
    let cnl = contract_text("task-status.cnl");

    for format in ["som.cnl.v1", "som.prompt-json.v1", "som.cnl.service.v2"] {
        let body = json!({"format": format, "source": cnl});
        let (status, reply) = rt.block_on(post(&server, "/compile", body.to_string().into_bytes()));
        assert_eq!(status, 422, "format {format}: {reply}");
        assert_eq!(reply["code"], "UNSUPPORTED_VERSION", "format {format}");
    }

    let duplicate_keys: Vec<Vec<u8>> = vec![
        br#"{"format":"som.cnl.service.v1","source":"","source":""}"#.to_vec(),
        br#"{"format":"som.prompt-json.service.v1","prompt":{},"extra":true}"#.to_vec(),
    ];
    for raw in duplicate_keys {
        let (status, reply) = rt.block_on(post(&server, "/compile", raw));
        assert_eq!(status, 400, "{reply}");
        assert_eq!(reply["code"], "INVALID_SCHEMA");
    }

    let (status, reply) = rt.block_on(post(&server, "/compile", vec![0xff]));
    assert_eq!(status, 400, "{reply}");
    assert_eq!(reply["code"], "INVALID_JSON");

    let oversize = vec![b' '; som_service::COMPILE_BODY_LIMIT + 1];
    let (status, reply) = rt.block_on(post(&server, "/compile", oversize));
    assert_eq!(status, 413, "{reply}");
    assert_eq!(reply["code"], "REQUEST_TOO_LARGE");

    let mut changed = compile_cnl_over_http(&rt, &server)["bundle"].clone();
    changed["dsl"]["operation"]["method"] = json!("DELETE");
    let (status, reply) = rt.block_on(post(
        &server,
        "/assemble",
        json!({"bundle": changed}).to_string().into_bytes(),
    ));
    assert_eq!(status, 400, "{reply}");
    assert_eq!(reply["code"], "INVALID_BUNDLE");
}

#[test]
fn cli_preserves_duplicate_keys_for_server_refusal() {
    // entrypoints.py: "CLI preserves duplicate keys for server refusal"
    let server = Server::start();
    let file = temp_file("duplicate-prompt.json");
    std::fs::write(
        &file,
        r#"{"version":"som.prompt.service.v1","version":"som.prompt.service.v1"}"#,
    )
    .expect("write duplicate-key prompt");
    let output = cli(&server, "generate", &file);
    let _ = std::fs::remove_file(&file);

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!output.status.success(), "CLI must fail: {stderr}");
    assert!(stderr.contains("INVALID_SCHEMA"), "stderr: {stderr}");
}

#[test]
fn identity_matches_health_bundle_and_source_files() {
    // entrypoints.py: the identity assertions at the end of main()
    let identity_output = Command::new(BIN)
        .arg("identity")
        .output()
        .expect("run som-service identity");
    let identity = stdout_json(&identity_output);

    let server = Server::start();
    let rt = runtime();
    let (status, health) = rt.block_on(get(&server, "/health"));
    assert_eq!(status, 200, "{health}");
    let bundle = compile_cnl_over_http(&rt, &server)["bundle"].clone();

    assert_eq!(identity["assets"], health["assets"]);
    assert_eq!(identity["assets"], bundle["assets"]);
    assert_eq!(identity["assets"], som_service::bundle::identity());

    let manifest = som_service::bundle::source_manifest();
    assert_eq!(identity["manifest"], manifest);
    let canonical = som_service::bundle::canonical(&manifest);
    assert_eq!(
        som_service::bundle::hash(&canonical),
        identity["assets"]["sha256"]
            .as_str()
            .expect("sha256 string")
    );

    let files = identity["manifest"]["files"]
        .as_object()
        .expect("manifest files is an object");
    assert!(!files.is_empty(), "manifest lists no files");
    for (name, digest) in files {
        if name.starts_with("nlp/") {
            continue;
        }
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(name);
        let bytes = std::fs::read(&path).unwrap_or_else(|error| panic!("read {name}: {error}"));
        let mut hasher = Sha256::new();
        hasher.update(&bytes);
        assert_eq!(
            format!("{:x}", hasher.finalize()),
            digest.as_str().expect("digest string"),
            "{name}"
        );
    }
}
