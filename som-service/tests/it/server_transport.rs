use std::{fs, path::Path, process::Command, time::SystemTime};

#[test]
fn generated_server_and_client_round_trip() {
    let nonce = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let scratch = std::env::var_os("SOM_CLIENT_TEST_ROOT")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    let root = scratch.join(format!(
        "som-server-transport-{}-{nonce}",
        std::process::id()
    ));
    let generated = root.join("client");
    let server = root.join("server");
    let harness = root.join("harness");
    fs::create_dir_all(harness.join("tests")).unwrap();
    let prompt: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/defer-four.prompt.json")).unwrap();
    let bundle = som_service::compile_prompt_v2(&prompt).unwrap();
    for (base, files) in [
        (&generated, som_service::assemble(&bundle).unwrap()),
        (&server, som_service::assemble_server(&bundle).unwrap()),
    ] {
        for (path, content) in files {
            let path = base.join(path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, content).unwrap();
        }
    }
    fs::write(harness.join("Cargo.toml"), HARNESS_MANIFEST).unwrap();
    fs::write(harness.join("lib.rs"), "").unwrap();
    fs::write(harness.join("tests/round_trip.rs"), ROUND_TRIP_TESTS).unwrap();
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
    run(&harness, &["generate-lockfile", "--offline"]);
    run(
        &harness,
        &["test", "--locked", "--offline", "--", "--test-threads=1"],
    );
    eprintln!(
        "Generated server and client round trip evidence: {}",
        root.display()
    );
}

#[test]
fn all_four_operations_round_trip_is_in_the_harness() {
    for name in [
        "fn all_four_operations_round_trip()",
        "fn native_404_reaches_the_client()",
        "fn bad_body_is_a_native_400()",
    ] {
        assert!(
            ROUND_TRIP_TESTS.contains(name),
            "missing harness test {name}"
        );
    }
}

const HARNESS_MANIFEST: &str = r#"[package]
name = "som_round_trip_harness"
version = "0.1.0"
edition = "2021"
publish = false

[lib]
path = "lib.rs"

[dev-dependencies]
client = { package = "defer_client", path = "../client" }
server = { package = "defer_client_server", path = "../server" }
serde_json = "=1.0.151"
tokio = { version = "=1.53.1", features = ["net", "rt-multi-thread"] }
"#;

const ROUND_TRIP_TESTS: &str = r###"
use client::client::{ApiError, Client, TaskCancelError, TaskStatusError};
use client::models::{CreateTask, QueuePolicy, Target};
use serde_json::json;
use std::{collections::HashMap, io::{Read, Write}, net::TcpStream, sync::Arc};

struct Stub;

impl server::Handler for Stub {
    async fn read_task_status(&self, queue: String, task_id: String) -> Result<server::TaskStatusResponse, server::ServerError> {
        if queue == "missing" || task_id == "missing" {
            return Err(server::ServerError::new(404, "not_found", "no such task"));
        }
        Ok(server::TaskStatusResponse { task_id, status: server::TaskStatus::Scheduled })
    }
    async fn create_task(&self, queue: String, task: server::CreateTask) -> Result<(), server::ServerError> {
        if queue == "missing" {
            return Err(server::ServerError::new(404, "not_found", "no such queue"));
        }
        assert_eq!(task.task_id, "hello");
        assert_eq!(task.priority, Some(10));
        Ok(())
    }
    async fn put_queue(&self, queue: String, policy: server::QueuePolicy) -> Result<server::QueueSnapshot, server::ServerError> {
        Ok(server::QueueSnapshot {
            queue,
            control_state: server::ControlState::Running,
            policy,
            task_count: 0,
            scheduled_count: 0,
            in_flight_count: 0,
            terminal_count: 0,
        })
    }
    async fn cancel_task(&self, queue: String, task_id: String) -> Result<(), server::ServerError> {
        if queue == "missing" || task_id == "missing" {
            return Err(server::ServerError::new(404, "not_found", "no such task"));
        }
        Ok(())
    }
}

/// Runs the generated server on a free local port in its own thread and runtime.
fn start() -> String {
    let (sender, receiver) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap();
        runtime.block_on(async move {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            sender.send(format!("http://{}", listener.local_addr().unwrap())).unwrap();
            server::serve(listener, Arc::new(Stub)).await.unwrap();
        });
    });
    receiver.recv_timeout(std::time::Duration::from_secs(10)).unwrap()
}

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

fn default_policy() -> QueuePolicy {
    QueuePolicy { max_in_flight: 100, max_dispatch_per_tick: 10, max_dispatches_per_second: 50, max_burst_size: 50, lease_ttl_ms: 30000, retry_backoff_ms: 1000 }
}

#[test]
fn all_four_operations_round_trip() {
    let origin = start();
    let client = Client::new(&origin);
    let status = client.task_status("jobs".into(), "t1".into()).unwrap();
    assert_eq!(status.task_id, "t1");
    assert_eq!(status.status, json!("Scheduled"));
    client.task_create("jobs".into(), sample_task()).unwrap();
    let snapshot = client.queue_put("jobs".into(), default_policy()).unwrap();
    assert_eq!(snapshot.queue, "jobs");
    assert_eq!(snapshot.policy.max_in_flight, 100);
    client.task_cancel("jobs".into(), "t1".into()).unwrap();
}

#[test]
fn native_404_reaches_the_client() {
    let origin = start();
    let client = Client::new(&origin);
    match client.task_status("jobs".into(), "missing".into()).unwrap_err() {
        ApiError::Api(TaskStatusError::Status404(e)) => assert_eq!(e.error, "not_found"),
        other => panic!("expected 404: {other:?}"),
    }
    match client.task_cancel("jobs".into(), "missing".into()).unwrap_err() {
        ApiError::Api(TaskCancelError::Status404(e)) => assert_eq!(e.message, "no such task"),
        other => panic!("expected 404: {other:?}"),
    }
}

#[test]
fn bad_body_is_a_native_400() {
    let origin = start();
    let address = origin.trim_start_matches("http://");
    let body = "{\"not\":\"a task\"}";
    let mut socket = TcpStream::connect(address).unwrap();
    write!(socket, "POST /v1/queues/jobs/tasks HTTP/1.1\r\nHost: {address}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
    let mut reply = String::new();
    socket.read_to_string(&mut reply).unwrap();
    assert!(reply.starts_with("HTTP/1.1 400"), "{reply}");
    let payload = reply.split_once("\r\n\r\n").unwrap().1;
    let value: serde_json::Value = serde_json::from_str(payload).unwrap();
    assert_eq!(value["error"], "invalid_request");
    assert_eq!(value["message"], "The request body is not valid for this operation.");
}
"###;
