use std::{fs, path::Path, process::Command, time::SystemTime};

#[test]
fn generated_client_transport() {
    let nonce = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let scratch = std::env::var_os("SOM_CLIENT_TEST_ROOT")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    let root = scratch.join(format!(
        "som-client-transport-{}-{nonce}",
        std::process::id()
    ));
    let generated = root.join("client");
    let harness = root.join("harness");
    fs::create_dir_all(harness.join("tests")).unwrap();
    // These argument names deliberately collide with generated local variables.
    for (path, content) in som_service::render::render(
        "generated_defer_transport",
        "lookup_status",
        "parameters",
        "endpoint",
    ) {
        let path = generated.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content).unwrap();
    }
    fs::write(harness.join("Cargo.toml"), HARNESS_MANIFEST).unwrap();
    fs::write(harness.join("lib.rs"), "").unwrap();
    fs::write(harness.join("tests/transport.rs"), TRANSPORT_TESTS).unwrap();
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("contract/wire.json"),
        harness.join("tests/wire.json"),
    )
    .unwrap();
    let target = std::env::var_os("SOM_CLIENT_TEST_TARGET")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| root.join("target"));
    let run = |directory: &Path, args: &[&str]| {
        let output = Command::new(std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into()))
            .current_dir(directory)
            .env("CARGO_TARGET_DIR", &target)
            .args(args)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "cargo {args:?} failed in {}\nstdout:\n{}\nstderr:\n{}",
            directory.display(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        );
        eprintln!("{}", String::from_utf8_lossy(&output.stdout));
    };
    run(&generated, &["build", "--locked", "--offline"]);
    run(&harness, &["generate-lockfile", "--offline"]);
    run(
        &harness,
        &["test", "--locked", "--offline", "--", "--test-threads=1"],
    );
    eprintln!(
        "Generated client and transport evidence: {}",
        root.display()
    );
}

const HARNESS_MANIFEST: &str = r#"[package]
name = "defer_transport_acceptance"
version = "0.1.0"
edition = "2021"
publish = false

[lib]
path = "lib.rs"

[dev-dependencies]
client = { package = "generated_defer_transport", path = "../client" }
serde_json = "=1.0.151"
tokio = { version = "=1.54.1", features = ["rt", "macros"] }
"#;

// Independent requests, responses and expected bytes. This harness imports the
// generated library. It does not include or inspect the renderer implementation.
const TRANSPORT_TESTS: &str = r###"
use client::{ClientError, DeferClient, TaskStatus, TaskStatusResponse};
use std::{
    io::{Read, Write},
    net::TcpListener,
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

fn reply(status: u16, headers: &str, body: &[u8]) -> (String, JoinHandle<Vec<u8>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    listener.set_nonblocking(true).unwrap();
    let response = format!(
        "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n{headers}\r\n",
        body.len()
    ).into_bytes();
    let body = body.to_vec();
    let worker = thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut socket = loop {
            match listener.accept() {
                Ok((socket, _)) => break socket,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    assert!(Instant::now() < deadline, "client did not connect");
                    thread::sleep(Duration::from_millis(2));
                }
                Err(error) => panic!("accept: {error}"),
            }
        };
        socket.set_nonblocking(false).unwrap();
        socket.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        let mut request = Vec::new();
        let mut buffer = [0; 4096];
        while !request.windows(4).any(|part| part == b"\r\n\r\n") {
            let count = socket.read(&mut buffer).unwrap();
            assert!(count > 0, "incomplete request headers");
            request.extend_from_slice(&buffer[..count]);
        }
        socket.write_all(&response).unwrap();
        socket.write_all(&body).unwrap();
        request
    });
    (origin, worker)
}

async fn request(status: u16, body: &[u8]) -> Result<TaskStatusResponse, ClientError> {
    let (origin, worker) = reply(status, "", body);
    let result = DeferClient::new(&origin, None).unwrap().lookup_status("jobs", "t").await;
    let sent = worker.join().unwrap();
    assert!(!String::from_utf8_lossy(&sent).to_ascii_lowercase().contains("authorization:"));
    result
}

fn assert_http(error: ClientError, expected_status: u16, expected_body: &[u8]) {
    match error {
        ClientError::Http { status, body, .. } => {
            assert_eq!(status, expected_status);
            assert_eq!(body, expected_body);
        }
        other => panic!("expected HTTP error: {other:?}"),
    }
}

#[tokio::test(flavor = "current_thread")]
async fn independently_encoded_segments_bearer_and_empty_get_body() {
    let body = br#"{"task_id":"t","status":"Scheduled"}"#;
    let (origin, worker) = reply(200, "", body);
    let secret = "transport-test-only-token";
    let client = DeferClient::new(&format!("{origin}/"), Some(secret)).unwrap();
    assert!(!format!("{client:?}").contains(secret));
    client.lookup_status("a/b% 雪?#", "%2e").await.unwrap();
    let request = String::from_utf8(worker.join().unwrap()).unwrap();
    assert_eq!(
        request.lines().next().unwrap(),
        "GET /v1/queues/a%2Fb%25%20%E9%9B%AA%3F%23/tasks/%252e HTTP/1.1"
    );
    let headers = request.to_ascii_lowercase();
    assert!(headers.contains("\r\nauthorization: bearer transport-test-only-token\r\n"));
    assert!(!headers.contains("transfer-encoding:"));
    assert!(!headers.contains("content-length:") || headers.contains("content-length: 0\r\n"));
    assert!(request.ends_with("\r\n\r\n"));
    let (origin, worker) = reply(200, "", body);
    DeferClient::new(&origin, None).unwrap()
        .lookup_status("%2E", "tasks/a% 文?# +").await.unwrap();
    let request = String::from_utf8(worker.join().unwrap()).unwrap();
    assert_eq!(request.lines().next().unwrap(),
        "GET /v1/queues/%252E/tasks/tasks%2Fa%25%20%E6%96%87%3F%23%20+ HTTP/1.1");
}

#[test]
fn invalid_base_origins_and_bearer_headers_are_refused() {
    for origin in [
        "", "relative", "ftp://127.0.0.1", "http://", "http:///127.0.0.1",
        "http://user:password@127.0.0.1", "http://@127.0.0.1", "http://127.0.0.1/a",
        "http://127.0.0.1//", "http://127.0.0.1/./", "http://127.0.0.1/a/..",
        "http://127.0.0.1/%2e", "http://127.0.0.1?", "http://127.0.0.1/#",
        "http://127.0.0.1?q=x", "http://127.0.0.1#x", "http://127.0.0.1\\",
        " http://127.0.0.1", "http://127.0.0.1\n", "http://127.0.0.1:\n80",
        "http://127.0.0.1\0",
    ] {
        assert!(matches!(DeferClient::new(origin, None), Err(ClientError::InvalidConfiguration { .. })), "accepted {origin:?}");
    }
    for origin in ["http://127.0.0.1", "https://example.invalid/", "http://[::1]:1234/"] {
        assert!(DeferClient::new(origin, None).is_ok());
    }
    let secret = "secret\r\nInjected: bad";
    let error = DeferClient::new("http://127.0.0.1", Some(secret)).unwrap_err();
    assert!(matches!(error, ClientError::InvalidConfiguration { .. }));
    assert!(!format!("{error} {error:?}").contains("secret"));
}

#[tokio::test(flavor = "current_thread")]
async fn empty_and_dot_segments_never_send_a_request() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let client = DeferClient::new(&format!("http://{}", listener.local_addr().unwrap()), None).unwrap();
    for segment in ["", ".", ".."] {
        assert!(matches!(client.lookup_status(segment, "task").await, Err(ClientError::InvalidInput { parameter: "queue", .. })));
        assert!(matches!(client.lookup_status("jobs", segment).await, Err(ClientError::InvalidInput { parameter: "task_id", .. })));
    }
    assert_eq!(listener.accept().unwrap_err().kind(), std::io::ErrorKind::WouldBlock);
}

#[tokio::test(flavor = "current_thread")]
async fn redirects_are_returned_without_following_location() {
    let target = TcpListener::bind("127.0.0.1:0").unwrap();
    target.set_nonblocking(true).unwrap();
    let headers = format!("Location: http://{}/redirected\r\n", target.local_addr().unwrap());
    let body = b"redirect body";
    let (origin, worker) = reply(302, &headers, body);
    let error = DeferClient::new(&origin, Some("redirect-test-token")).unwrap()
        .lookup_status("jobs", "t").await.unwrap_err();
    assert_http(error, 302, body);
    worker.join().unwrap();
    assert_eq!(target.accept().unwrap_err().kind(), std::io::ErrorKind::WouldBlock);
}

#[tokio::test(flavor = "current_thread")]
async fn all_five_native_states_match_frozen_wire_fixtures() {
    let wire: serde_json::Value = serde_json::from_str(include_str!("wire.json")).unwrap();
    let cases = wire["success_bodies"].as_array().unwrap();
    assert_eq!(cases.len(), 5);
    for expected in cases {
        let bytes = serde_json::to_vec(expected).unwrap();
        let actual = request(200, &bytes).await.unwrap();
        assert_eq!(serde_json::to_value(actual).unwrap(), *expected);
    }
}

#[tokio::test(flavor = "current_thread")]
async fn record_extras_are_allowed_and_leases_keep_u64_and_utc_time() {
    let body = br#"{"task_id":"t","extra":true,"extra":false,"status":{"Leased":{"attempt_id":"a","executor_node":18446744073709551615,"epoch":18446744073709551615,"expires_at":"2026-11-06T08:00:30+08:00","extra":1,"extra":2}}}"#;
    match request(200, body).await.unwrap().status {
        TaskStatus::Leased { attempt_id, executor_node, epoch, expires_at } => {
            assert_eq!(attempt_id, "a");
            assert_eq!(executor_node, u64::MAX);
            assert_eq!(epoch, u64::MAX);
            assert_eq!(expires_at.to_rfc3339(), "2026-11-06T00:00:30+00:00");
        }
        other => panic!("expected Leased: {other:?}"),
    }
}

#[tokio::test(flavor = "current_thread")]
async fn native_http_errors_keep_status_raw_body_and_optional_envelope() {
    let wire: serde_json::Value = serde_json::from_str(include_str!("wire.json")).unwrap();
    for case in wire["errors"].as_array().unwrap() {
        let expected_status = case["status"].as_u64().unwrap() as u16;
        let bytes = serde_json::to_vec(&case["body"]).unwrap();
        match request(expected_status, &bytes).await.unwrap_err() {
            ClientError::Http { status, body, native: Some(native) } => {
                assert_eq!(status, expected_status);
                assert_eq!(body, bytes);
                assert_eq!(native.error, case["body"]["error"].as_str().unwrap());
                assert_eq!(native.message, case["body"]["message"].as_str().unwrap());
            }
            other => panic!("missing native envelope: {other:?}"),
        }
    }
    for (status, body) in [
        (201, br#"{"task_id":"t","status":"Scheduled"}"#.as_slice()),
        (204, b"".as_slice()),
        (500, b"\xffnot JSON\0".as_slice()),
        (401, br#"{"error":"a","error":"b","message":"c"}"#.as_slice()),
        (403, br#"{"error":"a","message":"b","message":"c"}"#.as_slice()),
        (404, br#"{"error":"a"}"#.as_slice()),
        (503, br#"{"error":2,"message":"b"}"#.as_slice()),
    ] {
        match request(status, body).await.unwrap_err() {
            ClientError::Http { status: actual, body: raw, native } => {
                assert_eq!(actual, status);
                assert_eq!(raw, body);
                assert!(native.is_none());
            }
            other => panic!("expected HTTP error: {other:?}"),
        }
    }
    let raw = br#"{"error":"forbidden","message":"echoed-secret-token","extra":1}"#;
    let error = request(403, raw).await.unwrap_err();
    assert!(!format!("{error} {error:?}").contains("echoed-secret-token"));
    match error {
        ClientError::Http { native: Some(native), .. } => {
            assert_eq!(native.message, "echoed-secret-token");
            assert!(!format!("{native:?}").contains("echoed-secret-token"));
        }
        other => panic!("expected envelope: {other:?}"),
    }
}

#[tokio::test(flavor = "current_thread")]
async fn invalid_successes_keep_exact_bytes_and_reject_known_duplicates() {
    let mut invalid: Vec<Vec<u8>> = [
        "not JSON", "{}", "null", "[]", "{\"task_id\":\"t\"}",
        r#"{"status":"Scheduled"}"#,
        r#"{"task_id":"t","task_id":"t","status":"Scheduled"}"#,
        r#"{"task_id":"t","status":"Scheduled","status":"Scheduled"}"#,
        r#"{"task_id":1,"status":"Scheduled"}"#,
        r#"{"task_id":"t","status":"Unknown"}"#,
        r#"{"task_id":"t","status":{"Unknown":{}}}"#,
        r#"{"task_id":"t","status":{"Leased":{}}}"#,
        r#"{"task_id":"t","status":"Scheduled"} trailing"#,
        r#"{"task_id":"echoed-secret-token","status":"Invalid"}"#,
    ].iter().map(|value| value.as_bytes().to_vec()).collect();
    invalid.push(vec![0xff, 0]);
    let fields = [
        ("attempt_id", "\"a\""), ("executor_node", "1"), ("epoch", "1"),
        ("expires_at", "\"2026-11-06T00:00:30Z\""),
    ];
    for (name, value) in fields {
        let complete = fields.iter().map(|(key, value)| format!("\"{key}\":{value}")).collect::<Vec<_>>().join(",");
        invalid.push(format!("{{\"task_id\":\"t\",\"status\":{{\"Leased\":{{{complete},\"{name}\":{value}}}}}}}").into_bytes());
        let missing = fields.iter().filter(|(key, _)| *key != name).map(|(key, value)| format!("\"{key}\":{value}")).collect::<Vec<_>>().join(",");
        invalid.push(format!("{{\"task_id\":\"t\",\"status\":{{\"Leased\":{{{missing}}}}}}}").into_bytes());
    }
    for (field, bad) in [
        ("executor_node", "18446744073709551616"), ("epoch", "18446744073709551616"),
        ("executor_node", "-1"), ("epoch", "1.5"), ("attempt_id", "null"),
        ("expires_at", "\"not-a-date\""),
    ] {
        let inner = fields.iter().map(|(name, value)| format!("\"{name}\":{}", if *name == field { bad } else { value })).collect::<Vec<_>>().join(",");
        invalid.push(format!("{{\"task_id\":\"t\",\"status\":{{\"Leased\":{{{inner}}}}}}}").into_bytes());
    }
    for expected in invalid {
        let error = request(200, &expected).await.unwrap_err();
        assert!(!format!("{error} {error:?}").contains("echoed-secret-token"));
        match error {
            ClientError::InvalidResponse { status, body } => {
                assert_eq!(status, 200);
                assert_eq!(body, expected);
            }
            other => panic!("expected InvalidResponse: {other:?}"),
        }
    }
}

#[tokio::test(flavor = "current_thread")]
async fn network_failures_have_a_distinct_error() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    drop(listener);
    let error = DeferClient::new(&origin, Some("network-secret-token")).unwrap()
        .lookup_status("jobs", "t").await.unwrap_err();
    assert!(matches!(error, ClientError::Network { status: None, ref body, .. } if body.is_empty()));
    assert!(!format!("{error} {error:?}").contains("network-secret-token"));
}

#[tokio::test(flavor = "current_thread")]
async fn interrupted_bodies_keep_the_received_status_and_prefix() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let worker = thread::spawn(move || {
        let (mut socket, _) = listener.accept().unwrap();
        socket.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        let mut request = Vec::new();
        let mut buffer = [0; 4096];
        while !request.windows(4).any(|part| part == b"\r\n\r\n") {
            let count = socket.read(&mut buffer).unwrap();
            assert!(count > 0);
            request.extend_from_slice(&buffer[..count]);
        }
        socket.write_all(b"HTTP/1.1 500 Test\r\nContent-Length: 100\r\nConnection: close\r\n\r\nprefix-secret").unwrap();
        socket.flush().unwrap();
        // Let the first body frame arrive before the connection ends.
        thread::sleep(Duration::from_millis(20));
    });
    let error = DeferClient::new(&origin, None).unwrap()
        .lookup_status("jobs", "t").await.unwrap_err();
    worker.join().unwrap();
    assert!(!format!("{error} {error:?}").contains("prefix-secret"));
    match error {
        ClientError::Network { status: Some(500), body, timeout: false } => assert_eq!(body, b"prefix-secret"),
        other => panic!("expected partial network error: {other:?}"),
    }
}
"###;

#[test]
fn generated_v2_client_transport() {
    let nonce = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let scratch = std::env::var_os("SOM_CLIENT_TEST_ROOT")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    let root = scratch.join(format!(
        "som-client-transport-v2-{}-{nonce}",
        std::process::id()
    ));
    let generated = root.join("client");
    let harness = root.join("harness");
    fs::create_dir_all(harness.join("tests")).unwrap();
    let mut prompt: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/defer-four.prompt.json")).unwrap();
    prompt["package"] = serde_json::json!("generated_defer_v2");
    let files = som_service::assemble(&som_service::compile_prompt_v2(&prompt).unwrap()).unwrap();
    for (path, content) in files {
        let path = generated.join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, content).unwrap();
    }
    fs::write(harness.join("Cargo.toml"), HARNESS_MANIFEST_V2).unwrap();
    fs::write(harness.join("lib.rs"), "").unwrap();
    fs::write(harness.join("tests/transport.rs"), TRANSPORT_TESTS_V2).unwrap();
    fs::copy(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("contract/wire.json"),
        harness.join("tests/wire.json"),
    )
    .unwrap();
    let target = std::env::var_os("SOM_CLIENT_TEST_TARGET")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| root.join("target"));
    let run = |directory: &Path, args: &[&str]| {
        let output = Command::new(std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into()))
            .current_dir(directory)
            .env("CARGO_TARGET_DIR", &target)
            .args(args)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "cargo {args:?} failed in {}\nstdout:\n{}\nstderr:\n{}",
            directory.display(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        );
        eprintln!("{}", String::from_utf8_lossy(&output.stdout));
    };
    run(&generated, &["build", "--locked", "--offline"]);
    run(&harness, &["generate-lockfile", "--offline"]);
    run(
        &harness,
        &["test", "--locked", "--offline", "--", "--test-threads=1"],
    );
    eprintln!(
        "Generated v2 client and transport evidence: {}",
        root.display()
    );
}

const HARNESS_MANIFEST_V2: &str = r#"[package]
name = "defer_transport_acceptance_v2"
version = "0.1.0"
edition = "2021"
publish = false

[lib]
path = "lib.rs"

[dev-dependencies]
client = { package = "generated_defer_v2", path = "../client" }
serde_json = "=1.0.151"
tokio = { version = "=1.54.1", features = ["rt", "macros"] }
"#;

// Independent v2 requests and responses. The generated crate is the only client under test.
const TRANSPORT_TESTS_V2: &str = r###"
use client::{ClientError, ControlState, CreateTask, DeferClient, QueuePolicy, TaskStatus, TaskTarget};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    net::TcpListener,
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

fn reply(status: u16, headers: &str, body: &[u8]) -> (String, JoinHandle<Vec<u8>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    listener.set_nonblocking(true).unwrap();
    let response = format!(
        "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n{headers}\r\n",
        body.len()
    ).into_bytes();
    let body = body.to_vec();
    let worker = thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut socket = loop {
            match listener.accept() {
                Ok((socket, _)) => break socket,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    assert!(Instant::now() < deadline, "client did not connect");
                    thread::sleep(Duration::from_millis(2));
                }
                Err(error) => panic!("accept: {error}"),
            }
        };
        socket.set_nonblocking(false).unwrap();
        socket.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        let mut request = Vec::new();
        let mut buffer = [0; 4096];
        while !request.windows(4).any(|part| part == b"\r\n\r\n") {
            let count = socket.read(&mut buffer).unwrap();
            assert!(count > 0, "incomplete request headers");
            request.extend_from_slice(&buffer[..count]);
        }
        let head_end = request.windows(4).position(|part| part == b"\r\n\r\n").unwrap() + 4;
        let head = String::from_utf8_lossy(&request[..head_end]).to_ascii_lowercase();
        let length = head
            .lines()
            .find_map(|line| line.strip_prefix("content-length:"))
            .map(|value| value.trim().parse::<usize>().unwrap())
            .unwrap_or(0);
        while request.len() < head_end + length {
            let count = socket.read(&mut buffer).unwrap();
            assert!(count > 0, "incomplete request body");
            request.extend_from_slice(&buffer[..count]);
        }
        socket.write_all(&response).unwrap();
        socket.write_all(&body).unwrap();
        request
    });
    (origin, worker)
}

fn wire() -> Value {
    serde_json::from_str(include_str!("wire.json")).unwrap()
}

fn sample_task() -> CreateTask {
    CreateTask {
        task_id: "hello".into(),
        target: TaskTarget {
            url: "http://x".into(),
            method: Some("POST".into()),
            headers: Some(BTreeMap::new()),
        },
        schedule_at: "2026-11-06T00:00:00Z".parse().unwrap(),
        payload: Some(json!({})),
        priority: Some(10),
        max_attempts: Some(2),
    }
}

fn sent_body(request: &str) -> Value {
    serde_json::from_str(request.split_once("\r\n\r\n").unwrap().1).unwrap()
}

#[tokio::test(flavor = "current_thread")]
async fn post_sends_json_body_and_accepts_201() {
    let (origin, worker) = reply(201, "", b"");
    DeferClient::new(&origin, None).unwrap()
        .create_task("a/b", &sample_task())
        .await
        .unwrap();
    let request = String::from_utf8(worker.join().unwrap()).unwrap();
    assert_eq!(request.lines().next().unwrap(), "POST /v1/queues/a%2Fb/tasks HTTP/1.1");
    assert!(request.to_ascii_lowercase().contains("\r\ncontent-type: application/json\r\n"));
    let example = wire()["operations"]["task_create"]["source"]["example"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(sent_body(&request), serde_json::from_str::<Value>(&example).unwrap());
}

#[tokio::test(flavor = "current_thread")]
async fn put_sends_policy_and_decodes_the_snapshot() {
    let wire = wire();
    let default_policy = wire["operations"]["queue_put"]["request_body"]["default_policy"].clone();
    let policy: QueuePolicy = serde_json::from_value(default_policy.clone()).unwrap();
    let answer = serde_json::to_vec(&wire["operations"]["queue_put"]["success"]["body"]).unwrap();
    let (origin, worker) = reply(200, "", &answer);
    let snapshot = DeferClient::new(&origin, None).unwrap()
        .put_queue("jobs", &policy)
        .await
        .unwrap();
    let request = String::from_utf8(worker.join().unwrap()).unwrap();
    assert_eq!(request.lines().next().unwrap(), "PUT /v1/queues/jobs HTTP/1.1");
    assert_eq!(sent_body(&request), default_policy);
    assert_eq!(snapshot.control_state, ControlState::Running);
    assert_eq!(snapshot.policy, policy);
    assert_eq!(snapshot.task_count, 0);

    let bad = br#"{"error":"bad_request","message":"x"}"#;
    let (origin, worker) = reply(400, "", bad);
    let error = DeferClient::new(&origin, None).unwrap()
        .put_queue("jobs", &policy)
        .await
        .unwrap_err();
    worker.join().unwrap();
    match error {
        ClientError::Http { status: 400, body, native } => {
            assert_eq!(body, bad);
            assert!(native.is_some());
        }
        other => panic!("expected 400 HTTP error: {other:?}"),
    }
}

#[tokio::test(flavor = "current_thread")]
async fn delete_sends_no_body_and_accepts_204() {
    let (origin, worker) = reply(204, "", b"");
    DeferClient::new(&origin, None).unwrap()
        .cancel_task("jobs", "t")
        .await
        .unwrap();
    let request = String::from_utf8(worker.join().unwrap()).unwrap();
    assert_eq!(request.lines().next().unwrap(), "DELETE /v1/queues/jobs/tasks/t HTTP/1.1");
    assert!(!request.to_ascii_lowercase().contains("content-type:"));
    assert!(request.ends_with("\r\n\r\n"));

    for case in wire()["operations"]["task_cancel"]["errors"].as_array().unwrap() {
        let status = case["status"].as_u64().unwrap() as u16;
        let bytes = serde_json::to_vec(&case["body"]).unwrap();
        let (origin, worker) = reply(status, "", &bytes);
        let error = DeferClient::new(&origin, None).unwrap()
            .cancel_task("jobs", "t")
            .await
            .unwrap_err();
        worker.join().unwrap();
        match error {
            ClientError::Http { status: actual, body, native: Some(native) } => {
                assert_eq!(actual, status);
                assert_eq!(body, bytes);
                assert_eq!(native.error, case["body"]["error"].as_str().unwrap());
                assert_eq!(native.message, case["body"]["message"].as_str().unwrap());
            }
            other => panic!("expected native HTTP error: {other:?}"),
        }
    }
}

#[tokio::test(flavor = "current_thread")]
async fn post_error_bodies_from_the_wire_contract() {
    for case in wire()["operations"]["task_create"]["errors"].as_array().unwrap() {
        let status = case["status"].as_u64().unwrap() as u16;
        let bytes = serde_json::to_vec(&case["body"]).unwrap();
        let (origin, worker) = reply(status, "", &bytes);
        let error = DeferClient::new(&origin, None).unwrap()
            .create_task("jobs", &sample_task())
            .await
            .unwrap_err();
        worker.join().unwrap();
        match error {
            ClientError::Http { status: actual, body, native: Some(native) } => {
                assert_eq!(actual, status);
                assert_eq!(body, bytes);
                assert_eq!(native.error, case["body"]["error"].as_str().unwrap());
                assert_eq!(native.message, case["body"]["message"].as_str().unwrap());
            }
            other => panic!("expected native HTTP error: {other:?}"),
        }
    }
}

#[tokio::test(flavor = "current_thread")]
async fn wrong_success_status_is_an_http_error() {
    let (origin, worker) = reply(200, "", b"");
    let error = DeferClient::new(&origin, None).unwrap()
        .create_task("jobs", &sample_task())
        .await
        .unwrap_err();
    worker.join().unwrap();
    assert!(matches!(error, ClientError::Http { status: 200, .. }));

    let (origin, worker) = reply(200, "", b"");
    let error = DeferClient::new(&origin, None).unwrap()
        .cancel_task("jobs", "t")
        .await
        .unwrap_err();
    worker.join().unwrap();
    assert!(matches!(error, ClientError::Http { status: 200, .. }));
}

#[tokio::test(flavor = "current_thread")]
async fn empty_and_dot_segments_never_send_a_request() {
    let client = DeferClient::new("http://127.0.0.1:9", None).unwrap();
    assert!(matches!(
        client.cancel_task("", "t").await,
        Err(ClientError::InvalidInput { parameter: "queue", .. })
    ));
    assert!(matches!(
        client.cancel_task("jobs", "..").await,
        Err(ClientError::InvalidInput { parameter: "task_id", .. })
    ));
}

#[tokio::test(flavor = "current_thread")]
async fn read_task_status_still_decodes_status() {
    let (origin, worker) = reply(200, "", br#"{"task_id":"t","status":"Scheduled"}"#);
    let response = DeferClient::new(&origin, None).unwrap()
        .read_task_status("jobs", "t")
        .await
        .unwrap();
    worker.join().unwrap();
    assert_eq!(response.status, TaskStatus::Scheduled);
}
"###;
