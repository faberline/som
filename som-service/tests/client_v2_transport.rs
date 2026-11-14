use std::{fs, path::Path, process::Command, time::SystemTime};

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
        "Generated v2 client and transport evidence: {}",
        root.display()
    );
}

const HARNESS_MANIFEST: &str = r#"[package]
name = "defer_transport_acceptance_v2"
version = "0.1.0"
edition = "2021"
publish = false

[lib]
path = "lib.rs"

[dev-dependencies]
client = { package = "generated_defer_v2", path = "../client" }
serde_json = "=1.0.151"
"#;

// Independent requests, responses and expected bytes for the generated blocking
// v2 client. This harness imports the generated library only.
const TRANSPORT_TESTS: &str = r###"
use client::client::{ApiError, Client, ClientError, QueuePutError, TaskCancelError, TaskCreateError, TaskStatusError};
use client::models::{CreateTask, QueueControlState, QueuePolicy, Target};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
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

fn wire() -> Value { serde_json::from_str(include_str!("wire.json")).unwrap() }

fn sample_task() -> CreateTask {
    CreateTask {
        task_id: "hello".into(),
        target: Target { url: "http://x".into(), method: Some("POST".into()), headers: Some(HashMap::new()) },
        schedule_at: "2026-11-06T00:00:00Z".into(),
        payload: json!({}),
        priority: Some(10),
        max_attempts: Some(2),
        dispatch_deadline_ms: None,
    }
}

fn sent_body(request: &str) -> Value {
    serde_json::from_str(request.split_once("\r\n\r\n").unwrap().1).unwrap()
}

fn request_text(worker: JoinHandle<Vec<u8>>) -> String {
    String::from_utf8(worker.join().unwrap()).unwrap()
}

#[test]
fn post_sends_json_body_and_accepts_201() {
    let (origin, worker) = reply(201, "", b"");
    Client::new(&origin).task_create("jobs".into(), sample_task()).unwrap();
    let request = request_text(worker);
    assert!(request.starts_with("POST /v1/queues/jobs/tasks HTTP/1.1\r\n"), "{request}");
    assert!(request.to_ascii_lowercase().contains("\r\ncontent-type: application/json\r\n"));
    let example: Value = serde_json::from_str(
        wire()["operations"]["task_create"]["source"]["example"].as_str().unwrap(),
    ).unwrap();
    assert_eq!(sent_body(&request), example);
}

#[test]
fn put_sends_policy_and_decodes_the_snapshot() {
    let default_policy = wire()["operations"]["queue_put"]["request_body"]["default_policy"].clone();
    let policy: QueuePolicy = serde_json::from_value(default_policy.clone()).unwrap();
    let body = serde_json::to_vec(&wire()["operations"]["queue_put"]["success"]["body"]).unwrap();
    let (origin, worker) = reply(200, "", &body);
    let snapshot = Client::new(&origin).queue_put("jobs".into(), policy).unwrap();
    let request = request_text(worker);
    assert!(request.starts_with("PUT /v1/queues/jobs HTTP/1.1\r\n"), "{request}");
    assert_eq!(sent_body(&request), default_policy);
    assert_eq!(snapshot.queue, "jobs");
    assert_eq!(snapshot.control_state, QueueControlState::Running);
    assert_eq!(snapshot.task_count, 0);
    assert_eq!(snapshot.policy.max_in_flight, 100);

    let bad_policy: QueuePolicy = serde_json::from_value(default_policy).unwrap();
    let (origin, worker) = reply(400, "", br#"{"error":"bad_request","message":"x"}"#);
    match Client::new(&origin).queue_put("jobs".into(), bad_policy).unwrap_err() {
        ApiError::Api(QueuePutError::Status400(e)) => assert_eq!(e.error, "bad_request"),
        other => panic!("expected queue_put 400: {other:?}"),
    }
    worker.join().unwrap();
}

#[test]
fn delete_sends_no_body_and_accepts_204() {
    let (origin, worker) = reply(204, "", b"");
    Client::new(&origin).task_cancel("jobs".into(), "t".into()).unwrap();
    let request = request_text(worker);
    assert!(request.starts_with("DELETE /v1/queues/jobs/tasks/t HTTP/1.1\r\n"), "{request}");
    assert!(!request.to_ascii_lowercase().contains("content-type:"));
    assert!(request.ends_with("\r\n\r\n"));

    for case in wire()["operations"]["task_cancel"]["errors"].as_array().unwrap() {
        let body = serde_json::to_vec(&case["body"]).unwrap();
        let status = case["status"].as_u64().unwrap() as u16;
        let (origin, worker) = reply(status, "", &body);
        match Client::new(&origin).task_cancel("jobs".into(), "t".into()).unwrap_err() {
            ApiError::Api(TaskCancelError::Status404(e)) => {
                assert_eq!(status, 404);
                assert_eq!(e.error, case["body"]["error"].as_str().unwrap());
                assert_eq!(e.message, case["body"]["message"].as_str().unwrap());
            }
            other => panic!("expected task_cancel 404: {other:?}"),
        }
        worker.join().unwrap();
    }
}

#[test]
fn post_error_bodies_from_the_wire_contract() {
    let example: Value = serde_json::from_str(
        wire()["operations"]["task_create"]["source"]["example"].as_str().unwrap(),
    ).unwrap();
    for case in wire()["operations"]["task_create"]["errors"].as_array().unwrap() {
        let status = case["status"].as_u64().unwrap() as u16;
        let body = serde_json::to_vec(&case["body"]).unwrap();
        let (origin, worker) = reply(status, "", &body);
        let error = Client::new(&origin).task_create("jobs".into(), sample_task()).unwrap_err();
        let (error_code, message) = match error {
            ApiError::Api(TaskCreateError::Status404(e)) if status == 404 => (e.error, e.message),
            ApiError::Api(TaskCreateError::Status409(e)) if status == 409 => (e.error, e.message),
            other => panic!("expected task_create {status}: {other:?}"),
        };
        assert_eq!(error_code, case["body"]["error"].as_str().unwrap());
        assert_eq!(message, case["body"]["message"].as_str().unwrap());
        let request = request_text(worker);
        assert!(request.starts_with("POST /v1/queues/jobs/tasks HTTP/1.1\r\n"), "{request}");
        assert_eq!(sent_body(&request), example);
    }
    let (origin, worker) = reply(400, "", br#"{"error":"bad_request","message":"x"}"#);
    match Client::new(&origin).task_create("jobs".into(), sample_task()).unwrap_err() {
        ApiError::Api(TaskCreateError::Status400(e)) => assert_eq!(e.error, "bad_request"),
        other => panic!("expected task_create 400: {other:?}"),
    }
    let request = request_text(worker);
    assert!(request.starts_with("POST /v1/queues/jobs/tasks HTTP/1.1\r\n"), "{request}");
    assert_eq!(sent_body(&request), example);
}

#[test]
fn read_task_status_still_decodes_status() {
    let (origin, worker) = reply(200, "", br#"{"task_id":"t","status":"Scheduled"}"#);
    let response = Client::new(&origin).task_status("jobs".into(), "t".into()).unwrap();
    let request = request_text(worker);
    assert!(request.starts_with("GET /v1/queues/jobs/tasks/t HTTP/1.1\r\n"), "{request}");
    assert_eq!(response.task_id, "t");
    assert_eq!(response.status, json!("Scheduled"));

    let (origin, worker) = reply(404, "", br#"{"error":"not_found","message":"x"}"#);
    match Client::new(&origin).task_status("jobs".into(), "t".into()).unwrap_err() {
        ApiError::Api(TaskStatusError::Status404(e)) => assert_eq!(e.error, "not_found"),
        other => panic!("expected task_status 404: {other:?}"),
    }
    worker.join().unwrap();
}

#[test]
fn undeclared_status_is_a_client_error() {
    let (origin, worker) = reply(500, "", b"{}");
    match Client::new(&origin).task_create("jobs".into(), sample_task()).unwrap_err() {
        ApiError::Client(_) => {}
        other => panic!("undeclared 500 must be a client error: {other:?}"),
    }
    worker.join().unwrap();

    let (origin, worker) = reply(418, "", b"{}");
    match Client::new(&origin).task_cancel("jobs".into(), "t".into()).unwrap_err() {
        ApiError::Client(_) => {}
        other => panic!("undeclared 418 must be a client error: {other:?}"),
    }
    worker.join().unwrap();
}

#[test]
fn unreachable_origin_is_a_client_error() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    drop(listener);
    match Client::new(&origin).task_status("jobs".into(), "t".into()).unwrap_err() {
        ApiError::Client(ClientError::Http(_)) => {}
        other => panic!("expected a transport client error: {other:?}"),
    }
}

#[test]
fn body_less_success_ignores_the_response_body() {
    let (origin, worker) = reply(204, "", b"");
    Client::new(&origin).task_create("jobs".into(), sample_task()).unwrap();
    worker.join().unwrap();
}
"###;
