//! OPERATIONS.md: every shell command of the document is run by scripts/quickstart.sh or by
//! this test, every flag of the CLI is documented, and the body limits are the ones the code uses (#206).
use serde_json::Value;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_som-service");

/// The runbook commands that this test runs itself: (command, text the output must contain).
const RUNBOOK: &[(&str, &str)] = &[
    (r#"$SOM serve --port "$PORT""#, "Address already in use"),
    (r#"curl -sS -X POST "$URL/compile" -d '{'"#, "INVALID_JSON"),
    (r#"curl -sS -i "$URL/nope""#, "404 Not Found"),
    (r#"$SOM compile "$BAD" --url "$URL""#, "CNL_SYNTAX"),
];

fn read(rel: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(rel);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

/// The commands of the fenced `sh` blocks: trimmed lines, no blanks, no comments.
fn doc_commands(doc: &str) -> Vec<String> {
    let mut commands = Vec::new();
    let mut in_sh = false;
    for line in doc.lines() {
        let line = line.trim();
        if line == "```sh" {
            in_sh = true;
        } else if line.starts_with("```") {
            in_sh = false;
        } else if in_sh && !line.is_empty() && !line.starts_with('#') {
            commands.push(line.to_string());
        }
    }
    commands
}

/// A document command is tested when scripts/quickstart.sh runs it as a whole line,
/// or when it is the first element of a RUNBOOK entry.
fn untested_commands(doc: &str, script: &str) -> Vec<String> {
    let lines: Vec<&str> = script.lines().map(str::trim).collect();
    doc_commands(doc)
        .into_iter()
        .filter(|command| {
            !lines.contains(&command.as_str()) && !RUNBOOK.iter().any(|(run, _)| *run == command)
        })
        .collect()
}

/// The `--flag` tokens of a `--help` text, without `--help`, `=value` or trailing punctuation.
fn flag_tokens(help: &str) -> Vec<String> {
    help.split_whitespace()
        .filter(|token| token.starts_with("--") && *token != "--help")
        .map(|token| {
            let name = token.split('=').next().unwrap_or(token);
            name.trim_end_matches(|c: char| !(c.is_alphanumeric() || c == '-'))
                .to_string()
        })
        .filter(|name| name.len() > 2)
        .collect()
}

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn operations_commands_are_tested() {
    let doc = read("OPERATIONS.md");
    let script = read("scripts/quickstart.sh");
    assert!(
        doc_commands(&doc).len() >= 7,
        "OPERATIONS.md must hold 3 run commands and 4 runbook commands, found {:?}",
        doc_commands(&doc)
    );
    let untested = untested_commands(&doc, &script);
    assert!(
        untested.is_empty(),
        "commands in OPERATIONS.md that are neither in scripts/quickstart.sh nor run by a test: {untested:?}"
    );
}

#[test]
fn a_changed_operations_command_is_reported() {
    let doc = read("OPERATIONS.md");
    let script = read("scripts/quickstart.sh");
    let changed = doc.replacen("--port 8767", "--port 9999", 1);
    assert_ne!(changed, doc, "the document must contain `--port 8767`");
    let untested = untested_commands(&changed, &script);
    assert_eq!(
        untested.len(),
        1,
        "exactly one command must be reported: {untested:?}"
    );
    assert!(
        untested[0].contains("9999"),
        "reported command: {untested:?}"
    );
}

#[test]
fn runbook_commands_run_and_show_the_documented_symptom() {
    let doc = read("OPERATIONS.md");
    let commands = doc_commands(&doc);
    for (command, _) in RUNBOOK {
        assert!(
            commands.iter().any(|c| c == command),
            "OPERATIONS.md must contain the runbook command {command}"
        );
    }

    // A busy port: a free port that is taken again by the server started below.
    let port = std::net::TcpListener::bind(("127.0.0.1", 0))
        .expect("bind a free port")
        .local_addr()
        .expect("local address")
        .port();
    let _server = ChildGuard(
        Command::new(BIN)
            .args(["serve", "--port", &port.to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn som-service serve"),
    );
    let started = Instant::now();
    while std::net::TcpStream::connect(("127.0.0.1", port)).is_err() {
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "the server did not start on port {port}"
        );
        std::thread::sleep(Duration::from_millis(50));
    }

    let bad = std::env::temp_dir().join(format!("som-operations-doc-{}.cnl", std::process::id()));
    std::fs::write(&bad, "Package x.\n").expect("write the bad CNL file");
    let url = format!("http://127.0.0.1:{port}");

    for (index, (command, expected)) in RUNBOOK.iter().enumerate() {
        let output = Command::new("sh")
            .args(["-c", command])
            .env("SOM", BIN)
            .env("PORT", port.to_string())
            .env("URL", &url)
            .env("BAD", &bad)
            .output()
            .unwrap_or_else(|e| panic!("run {command}: {e}"));
        let text = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            text.contains(expected),
            "`{command}` must show `{expected}`, got: {text}"
        );
        // The first command (serve on the busy port) and the last one (compile) must fail.
        if index == 0 || index == RUNBOOK.len() - 1 {
            assert!(
                !output.status.success(),
                "`{command}` must exit non-zero, got {}",
                output.status
            );
        }
    }
    let _ = std::fs::remove_file(&bad);
}

#[test]
fn operations_documents_every_flag() {
    let doc = read("OPERATIONS.md");
    for sub in ["serve", "compile", "generate", "assemble"] {
        let output = Command::new(BIN)
            .args([sub, "--help"])
            .output()
            .unwrap_or_else(|e| panic!("run {sub} --help: {e}"));
        let help = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        for flag in flag_tokens(&help) {
            assert!(
                doc.contains(&flag),
                "OPERATIONS.md must document `{flag}` of `{sub}`"
            );
        }
    }
    assert!(
        doc.contains("There is no environment variable."),
        "OPERATIONS.md must state that there is no environment variable"
    );
}

#[test]
fn limits_in_the_document_match_the_code() {
    let doc = read("OPERATIONS.md");
    assert!(
        doc.contains(&som_service::COMPILE_BODY_LIMIT.to_string()),
        "OPERATIONS.md must state COMPILE_BODY_LIMIT"
    );
    assert!(
        doc.contains(&som_service::ASSEMBLE_BODY_LIMIT.to_string()),
        "OPERATIONS.md must state ASSEMBLE_BODY_LIMIT"
    );
    assert!(doc.contains("413"), "OPERATIONS.md must state status 413");
    assert!(
        doc.contains("REQUEST_TOO_LARGE"),
        "OPERATIONS.md must state the code REQUEST_TOO_LARGE"
    );
}

async fn start() -> String {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("bind");
    let url = format!("http://{}", listener.local_addr().expect("addr"));
    tokio::spawn(async move {
        axum::serve(listener, som_service::server::router())
            .await
            .expect("serve");
    });
    url
}

#[tokio::test]
async fn oversize_body_is_rejected() {
    let url = start().await;
    let client = reqwest::Client::new();

    let response = client
        .post(format!("{url}/compile"))
        .header("content-type", "application/json")
        .body(vec![b' '; som_service::COMPILE_BODY_LIMIT + 1])
        .send()
        .await
        .expect("post oversize compile body");
    assert_eq!(response.status(), reqwest::StatusCode::PAYLOAD_TOO_LARGE);
    let body: Value = response.json().await.expect("json error envelope");
    assert_eq!(body["code"], "REQUEST_TOO_LARGE");
    assert_eq!(body["error"], "request_too_large");

    let response = client
        .post(format!("{url}/assemble"))
        .header("content-type", "application/json")
        .body(vec![b' '; som_service::ASSEMBLE_BODY_LIMIT + 1])
        .send()
        .await
        .expect("post oversize assemble body");
    assert_eq!(response.status(), reqwest::StatusCode::PAYLOAD_TOO_LARGE);
    let body: Value = response.json().await.expect("json error envelope");
    assert_eq!(body["code"], "REQUEST_TOO_LARGE");

    // A body of exactly the limit is not refused for its size: it is invalid JSON, so 400.
    let response = client
        .post(format!("{url}/compile"))
        .header("content-type", "application/json")
        .body(vec![b' '; som_service::COMPILE_BODY_LIMIT])
        .send()
        .await
        .expect("post compile body at the limit");
    assert_eq!(response.status(), reqwest::StatusCode::BAD_REQUEST);
}
