//! The generated-client acceptance checks (issue #239).
//!
//! 70 cases run against the real generated client of `contract/task-status.cnl`,
//! through the probe in `acceptance/probe/main.rs`, with a capture server built
//! on `std::net::TcpListener`. The native Defer cases are in acceptance_native.rs (issue #240).
use std::{
    collections::HashMap,
    fmt::Write as _,
    fs,
    io::{Read, Write},
    net::{Shutdown, TcpListener, TcpStream},
    path::PathBuf,
    process::Command,
    sync::{Arc, Mutex, OnceLock},
    thread,
    time::{Duration, SystemTime},
};

use serde_json::{Value, json};

const SECRET: &str = "secret-fixture-value";
const NATIVE: &str = include_str!("../../acceptance/native-oracle.json");
const CAPTURE: &str = include_str!("../../acceptance/capture-oracle.json");
const PROBE: &str = include_str!("../../acceptance/probe/main.rs");
const CNL: &str = include_str!("../../contract/task-status.cnl");

const PROBE_MANIFEST: &str = r#"[package]
name = "som-defer-client-probe"
version = "0.1.0"
edition = "2021"

[workspace]

[dependencies]
generated-client = { package = "defer_status_client", path = "../generated" }
serde_json = "1.0"
tokio = { version = "1", features = ["macros", "rt-multi-thread"] }

[profile.dev]
debug = 0
"#;

fn hex(bytes: &[u8]) -> String {
    let mut out = String::new();
    for byte in bytes {
        write!(out, "{byte:02x}").unwrap();
    }
    out
}

fn unhex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
        .collect()
}

/// Same as Python `quote(text, safe="")`.
fn percent_encode(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            out.push(byte as char);
        } else {
            write!(out, "%{byte:02X}").unwrap();
        }
    }
    out
}

/// The cases in oracle order, with the scripted response of each request case.
struct Plan {
    cases: Vec<Value>,
    responses: HashMap<String, Value>,
    expected: Vec<Value>,
}

impl Plan {
    /// A case that goes through the capture server: it scripts the response
    /// for the path the client must call, and records the request it expects.
    fn request(&mut self, base: &str, mut case: Value, scripted: Value) {
        let path = format!(
            "/v1/queues/{}/tasks/{}",
            percent_encode(case["queue"].as_str().unwrap()),
            percent_encode(case["task_id"].as_str().unwrap()),
        );
        self.responses.insert(path.clone(), scripted);
        self.expected.push(json!({
            "method": "GET",
            "path": path,
            "authorization": format!("Bearer {SECRET}"),
            "body": [],
            "content_length": 0,
            "transfer_encoding": null,
        }));
        case["base_url"] = json!(base);
        case["token"] = json!(SECRET);
        self.cases.push(case);
    }
}

/// Port of `client_checks` without the native-server cases. Pure: no I/O.
fn build_cases(base: &str, refused: &str) -> Plan {
    let native: Value = serde_json::from_str(NATIVE).unwrap();
    let capture: Value = serde_json::from_str(CAPTURE).unwrap();
    let mut plan = Plan {
        cases: Vec::new(),
        responses: HashMap::new(),
        expected: Vec::new(),
    };
    let native_cases = native["cases"].as_array().unwrap();

    for row in native_cases {
        let id = row["id"].as_str().unwrap();
        if !id.starts_with("path-") {
            continue;
        }
        let body = row["expected_body"].clone();
        let scripted = json!({
            "response_status": 200,
            "response_body_hex": hex(body.to_string().as_bytes()),
        });
        let case = json!({
            "id": format!("capture-{id}"),
            "queue": row["queue"],
            "task_id": row["task_id"],
            "expected_kind": "Ok",
            "expected_decoded": body,
        });
        plan.request(base, case, scripted);
    }

    for row in capture["responses"].as_array().unwrap() {
        let id = row["id"].as_str().unwrap();
        let kind = row["expected_kind"].as_str().unwrap();
        let mut case = json!({
            "id": id,
            "queue": format!("response-{id}"),
            "task_id": "t",
            "expected_kind": kind,
        });
        match kind {
            "Ok" => case["expected_decoded"] = row["expected_decoded"].clone(),
            "Network" => {
                case["expected_status"] = row["response_status"].clone();
                case["expected_body"] = json!(unhex(row["expected_body_hex"].as_str().unwrap()));
                case["expected_timeout"] = json!(false);
            }
            _ => {
                case["expected_status"] = row["response_status"].clone();
                case["expected_body"] = json!(unhex(row["expected_body_hex"].as_str().unwrap()));
                if kind == "Http" {
                    case["expected_native"] = row["expected_native"].clone();
                }
            }
        }
        plan.request(base, case, row.clone());
    }

    for (index, edge) in native["client_transport_refusals"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        let parameter = edge["parameter"].as_str().unwrap();
        let mut case = json!({
            "id": format!("invalid-path-{index}"),
            "base_url": base,
            "token": SECRET,
            "queue": "valid",
            "task_id": "valid",
            "expected_kind": "InvalidInput",
            "expected_parameter": parameter,
        });
        case[parameter] = edge["value"].clone();
        plan.cases.push(case);
    }

    for (index, origin) in capture["invalid_origins"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        plan.cases.push(json!({
            "id": format!("invalid-origin-{index}"),
            "base_url": origin,
            "token": SECRET,
            "configure_only": true,
            "expected_kind": "InvalidConfiguration",
        }));
    }

    plan.cases.push(json!({
        "id": "invalid-bearer",
        "base_url": base,
        "token": format!("{SECRET}\r\nInjected: x"),
        "configure_only": true,
        "expected_kind": "InvalidConfiguration",
    }));

    for origin in [
        base.to_string(),
        format!("{base}/"),
        "https://example.invalid".to_string(),
    ] {
        plan.cases.push(json!({
            "id": format!("valid-origin-{origin}"),
            "base_url": origin,
            "token": SECRET,
            "configure_only": true,
            "expected_kind": "Configured",
        }));
    }

    plan.cases.push(json!({
        "id": "network-connection-refused",
        "base_url": refused,
        "token": SECRET,
        "queue": "jobs",
        "task_id": "t",
        "expected_kind": "Network",
        "expected_timeout": false,
    }));

    plan.request(
        base,
        json!({
            "id": "request-timeout",
            "queue": "timeout",
            "task_id": "t",
            "expected_kind": "Network",
            "expected_timeout": true,
        }),
        json!({
            "timeout": true,
            "response_status": 200,
            "response_body_hex": hex(b"{}"),
        }),
    );
    plan
}

fn read_more(stream: &mut TcpStream, buf: &mut Vec<u8>) -> bool {
    let mut chunk = [0u8; 4096];
    match stream.read(&mut chunk) {
        Ok(0) | Err(_) => false,
        Ok(n) => {
            buf.extend_from_slice(&chunk[..n]);
            true
        }
    }
}

/// Port of `CaptureHandler`: HTTP/1.1 keep-alive, one thread per connection.
fn serve_connection(
    mut stream: TcpStream,
    records: &Mutex<Vec<Value>>,
    responses: &HashMap<String, Value>,
) {
    let mut buf = Vec::new();
    loop {
        let end = loop {
            if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                break pos;
            }
            if !read_more(&mut stream, &mut buf) {
                return;
            }
        };
        let head = String::from_utf8_lossy(&buf[..end]).into_owned();
        buf.drain(..end + 4);

        let mut lines = head.split("\r\n");
        let mut request = lines.next().unwrap_or_default().split(' ');
        let method = request.next().unwrap_or_default().to_string();
        let path = request.next().unwrap_or_default().to_string();
        let headers: Vec<(String, String)> = lines
            .filter_map(|line| line.split_once(':'))
            .map(|(name, value)| (name.trim().to_ascii_lowercase(), value.trim().to_string()))
            .collect();
        let header = |name: &str| {
            headers
                .iter()
                .find(|(key, _)| key == name)
                .map(|(_, value)| value.clone())
        };
        let length: usize = header("content-length")
            .and_then(|value| value.parse().ok())
            .unwrap_or(0);
        while buf.len() < length {
            if !read_more(&mut stream, &mut buf) {
                return;
            }
        }
        let body: Vec<u8> = buf.drain(..length).collect();

        records.lock().unwrap().push(json!({
            "method": method,
            "path": path,
            "authorization": header("authorization"),
            "body": body,
            "content_length": length,
            "transfer_encoding": header("transfer-encoding"),
        }));

        let scripted = responses.get(&path).cloned().unwrap_or_else(
            || json!({"response_status": 500, "response_body_hex": hex(b"unexpected request")}),
        );
        if scripted["timeout"].as_bool() == Some(true) {
            thread::sleep(Duration::from_secs(31));
        }
        let raw = unhex(scripted["response_body_hex"].as_str().unwrap());
        let status = scripted["response_status"].as_u64().unwrap();
        let extra = scripted["declared_extra_bytes"].as_u64().unwrap_or(0);
        let mut head = format!(
            "HTTP/1.1 {status} OK\r\nContent-Length: {}\r\nContent-Type: application/json\r\n",
            raw.len() as u64 + extra,
        );
        if let Some(extra_headers) = scripted["response_headers"].as_object() {
            for (name, value) in extra_headers {
                write!(head, "{name}: {}\r\n", value.as_str().unwrap_or_default()).unwrap();
            }
        }
        head.push_str("\r\n");
        let mut out = head.into_bytes();
        out.extend_from_slice(&raw);
        let _ = stream.write_all(&out);
        let _ = stream.flush();
        if scripted["close_after_body"].as_bool() == Some(true) {
            let _ = stream.shutdown(Shutdown::Both);
            return;
        }
    }
}

/// Binds the capture server first, so its base URL can go into the cases.
fn bind_capture() -> (TcpListener, String) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    (listener, format!("http://127.0.0.1:{port}"))
}

fn serve_capture(
    listener: TcpListener,
    responses: HashMap<String, Value>,
) -> Arc<Mutex<Vec<Value>>> {
    let records = Arc::new(Mutex::new(Vec::new()));
    let responses = Arc::new(responses);
    let shared = Arc::clone(&records);
    thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(stream) = stream else { continue };
            let records = Arc::clone(&shared);
            let responses = Arc::clone(&responses);
            thread::spawn(move || serve_connection(stream, &records, &responses));
        }
    });
    records
}

/// A local port that nothing listens on: bind, read the port, drop the listener.
fn refused_origin() -> String {
    let port = TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port();
    format!("http://127.0.0.1:{port}")
}

/// Port of the per-case body of `check_probe`.
fn check_record(actual: &Value, case: &Value) {
    let id = case["id"].as_str().unwrap();
    assert_eq!(actual["id"], case["id"], "{id}: record id");
    assert_eq!(
        actual["kind"], case["expected_kind"],
        "{id}: kind, record {actual}"
    );
    match actual["kind"].as_str().unwrap() {
        "Ok" => assert_eq!(actual["body"], case["expected_decoded"], "{id}: body"),
        kind @ ("Http" | "InvalidResponse") => {
            assert_eq!(actual["status"], case["expected_status"], "{id}: status");
            assert_eq!(actual["body"], case["expected_body"], "{id}: body");
            if kind == "Http" {
                assert_eq!(actual["native"], case["expected_native"], "{id}: native");
            }
        }
        "InvalidInput" => assert_eq!(
            actual["parameter"], case["expected_parameter"],
            "{id}: parameter"
        ),
        "Network" => {
            assert_eq!(actual["timeout"], case["expected_timeout"], "{id}: timeout");
            let status = case.get("expected_status").cloned().unwrap_or(Value::Null);
            assert_eq!(actual["status"], status, "{id}: status");
            let body = case.get("expected_body").cloned().unwrap_or(json!([]));
            assert_eq!(actual["body"], body, "{id}: body");
        }
        _ => {}
    }
    for field in ["debug", "display", "client_debug"] {
        if let Some(text) = actual[field].as_str() {
            assert!(
                !text.contains(SECRET),
                "{id}: secret leaked in {field}: {text}"
            );
        }
    }
}

struct Run {
    cases: Vec<Value>,
    records: Vec<Value>,
    captured: Vec<Value>,
    expected: Vec<Value>,
}

/// Runs the probe once for every test in this file (about 31 s: the timeout case).
fn run() -> &'static Run {
    static RUN: OnceLock<Run> = OnceLock::new();
    RUN.get_or_init(execute)
}

fn execute() -> Run {
    let nonce = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let scratch = std::env::var_os("SOM_CLIENT_TEST_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    let root = scratch.join(format!(
        "som-acceptance-client-{}-{nonce}",
        std::process::id()
    ));
    let generated = root.join("generated");
    let probe = root.join("probe");
    let target = std::env::var_os("SOM_CLIENT_TEST_TARGET")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("target"));

    let compiled = som_service::compile_cnl(CNL).unwrap();
    let files = som_service::assemble(&compiled.bundle).unwrap();
    for (path, content) in &files {
        let path = generated.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content).unwrap();
    }
    fs::create_dir_all(probe.join("src")).unwrap();
    fs::write(probe.join("src/main.rs"), PROBE).unwrap();
    fs::copy(generated.join("Cargo.lock"), probe.join("Cargo.lock")).unwrap();
    fs::write(probe.join("Cargo.toml"), PROBE_MANIFEST).unwrap();

    let cargo = std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
    let cargo_run = |args: &[&str]| {
        let output = Command::new(&cargo)
            .current_dir(&probe)
            .env("CARGO_TARGET_DIR", &target)
            .args(args)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "cargo {args:?} failed in {}\nstdout:\n{}\nstderr:\n{}",
            probe.display(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        );
    };
    // The metadata call completes the lock; a direct --locked build fails without it.
    cargo_run(&["metadata", "--offline", "--format-version", "1"]);
    cargo_run(&["build", "--locked", "--offline"]);
    let binary = target.join("debug").join("som-defer-client-probe");

    let (listener, base) = bind_capture();
    let plan = build_cases(&base, &refused_origin());
    let captured = serve_capture(listener, plan.responses);

    let cases_path = root.join("probe-cases.json");
    let results_path = root.join("probe-results.json");
    fs::write(&cases_path, serde_json::to_vec_pretty(&plan.cases).unwrap()).unwrap();
    let output = Command::new(&binary)
        .arg(&cases_path)
        .arg(&results_path)
        .env_remove("HTTP_PROXY")
        .env_remove("HTTPS_PROXY")
        .env_remove("ALL_PROXY")
        .env_remove("http_proxy")
        .env_remove("https_proxy")
        .env_remove("all_proxy")
        .env("NO_PROXY", "127.0.0.1,localhost,::1")
        .env("no_proxy", "127.0.0.1,localhost,::1")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "probe failed in {}\nstderr:\n{}",
        root.display(),
        String::from_utf8_lossy(&output.stderr),
    );
    let records: Vec<Value> = serde_json::from_slice(&fs::read(&results_path).unwrap()).unwrap();
    let captured = captured.lock().unwrap().clone();
    Run {
        cases: plan.cases,
        records,
        captured,
        expected: plan.expected,
    }
}

#[test]
fn acceptance_client_case_counts() {
    let native: Value = serde_json::from_str(NATIVE).unwrap();
    let capture: Value = serde_json::from_str(CAPTURE).unwrap();
    assert_eq!(capture["responses"].as_array().unwrap().len(), 29);
    assert_eq!(capture["invalid_origins"].as_array().unwrap().len(), 11);
    assert_eq!(
        native["client_transport_refusals"]
            .as_array()
            .unwrap()
            .len(),
        6
    );
    let path_rows = native["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|row| row["id"].as_str().unwrap().starts_with("path-"))
        .count();
    assert_eq!(path_rows, 18);

    let plan = build_cases("http://127.0.0.1:1", "http://127.0.0.1:2");
    let ids: Vec<&str> = plan
        .cases
        .iter()
        .map(|case| case["id"].as_str().unwrap())
        .collect();
    assert_eq!(ids.len(), 70, "the generated-client cases");
    let count = |prefix: &str| ids.iter().filter(|id| id.starts_with(prefix)).count();
    assert_eq!(count("capture-path-"), 18);
    assert_eq!(count("invalid-path-"), 6);
    assert_eq!(count("invalid-origin-"), 11);
    assert_eq!(count("invalid-bearer"), 1);
    assert_eq!(count("valid-origin-"), 3);
    assert_eq!(count("network-connection-refused"), 1);
    assert_eq!(count("request-timeout"), 1);
    // The remaining 29 cases are the response rows of the capture oracle.
    let responses: Vec<&str> = capture["responses"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| row["id"].as_str().unwrap())
        .collect();
    let named = [
        "invalid-bearer",
        "network-connection-refused",
        "request-timeout",
    ];
    let prefixes = [
        "capture-path-",
        "invalid-path-",
        "invalid-origin-",
        "valid-origin-",
    ];
    let rest: Vec<&str> = ids
        .iter()
        .copied()
        .filter(|id| !named.contains(id) && !prefixes.iter().any(|p| id.starts_with(p)))
        .collect();
    assert_eq!(rest, responses);
    assert_eq!(
        plan.expected.len(),
        48,
        "requests that reach the capture server"
    );
    // 70 here + the 35 native cases of acceptance_native.rs = 105.
}

#[test]
fn acceptance_client_captured_requests() {
    let r = run();
    assert_eq!(r.captured.len(), 48);
    assert_eq!(r.captured, r.expected);
}

#[test]
fn acceptance_client_probe_matches_oracle() {
    let r = run();
    assert_eq!(r.records.len(), r.cases.len());
    for (record, case) in r.records.iter().zip(&r.cases) {
        check_record(record, case);
    }
}

#[test]
fn acceptance_client_never_leaks_secret() {
    let r = run();
    for record in &r.records {
        for field in ["debug", "display", "client_debug"] {
            if let Some(text) = record[field].as_str() {
                assert!(
                    !text.contains(SECRET),
                    "{}: secret in {field}",
                    record["id"]
                );
            }
        }
    }
    assert!(
        r.records
            .iter()
            .any(|record| record["client_debug"].is_string()),
        "no record carries client_debug, so the check saw no real data"
    );
}

#[test]
fn acceptance_client_request_timeout() {
    let r = run();
    let record = r
        .records
        .iter()
        .find(|record| record["id"] == "request-timeout")
        .expect("request-timeout record");
    assert_eq!(record["kind"], "Network");
    assert_eq!(record["timeout"], true);
    assert!(record["status"].is_null(), "status: {record}");
    assert_eq!(record["body"], json!([]));
    assert!(
        r.captured
            .iter()
            .any(|request| request["path"] == "/v1/queues/timeout/tasks/t"),
        "the timeout request did not reach the capture server"
    );
}

#[test]
#[should_panic(expected = "kind")]
fn acceptance_client_check_record_rejects_a_wrong_kind() {
    check_record(
        &json!({"id": "x", "kind": "Ok", "body": {}}),
        &json!({"id": "x", "expected_kind": "Http"}),
    );
}
