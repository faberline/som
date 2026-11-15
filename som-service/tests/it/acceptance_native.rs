use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    fs,
    io::{Read, Write},
    net::TcpStream,
    path::{Path, PathBuf},
    process::{Child, Command, Output, Stdio},
    time::{Duration, Instant, SystemTime},
};

const DEFER_REV: &str = "5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0";
const NATIVE: &str = include_str!("../../acceptance/native-oracle.json");
const WIRE: &str = include_str!("../../contract/wire.json");
const NATIVE_MAIN: &str = include_str!("../../acceptance/native/main.rs");
const NATIVE_MANIFEST: &str = include_str!("../../acceptance/native/Cargo.toml.in");
const PROBE: &str = include_str!("../../acceptance/probe/main.rs");
const CNL: &str = include_str!("../../contract/task-status.cnl");

fn oracle() -> Value {
    serde_json::from_str(NATIVE).unwrap()
}

fn wire() -> Value {
    serde_json::from_str(WIRE).unwrap()
}

fn every_error_is_in_cases(errors: &[Value], cases: &[Value]) -> bool {
    errors.iter().all(|error| {
        cases.iter().any(|case| {
            case["expected_status"] == error["status"] && case["expected_body"] == error["body"]
        })
    })
}

fn success_set(cases: &[Value]) -> BTreeSet<String> {
    cases
        .iter()
        .filter(|case| case["id"].as_str().unwrap_or("").starts_with("native-"))
        .map(|case| case["expected_body"].to_string())
        .collect()
}

#[test]
fn acceptance_native_oracle_counts() {
    let oracle = oracle();
    let wire = wire();
    let cases = oracle["cases"].as_array().unwrap();
    let edges = oracle["native_path_edge_cases"].as_array().unwrap();
    assert_eq!(cases.len(), 35, "native cases");
    assert_eq!(edges.len(), 10, "native path edge cases");
    let path_ids = cases
        .iter()
        .filter(|case| case["id"].as_str().unwrap().starts_with("path-"))
        .count();
    assert_eq!(path_ids, 18, "path- case ids");

    let errors = wire["errors"].as_array().unwrap();
    assert_eq!(errors.len(), 4, "wire errors");
    assert!(
        every_error_is_in_cases(errors, cases),
        "a wire error has no matching native case"
    );

    let success_bodies = wire["success_bodies"].as_array().unwrap();
    assert_eq!(success_bodies.len(), 5, "wire success bodies");
    let wire_success: BTreeSet<String> = success_bodies.iter().map(|v| v.to_string()).collect();
    assert_eq!(success_set(cases), wire_success, "native-* success bodies");

    assert_eq!(DEFER_REV, wire["source"]["revision"].as_str().unwrap());
    let files = wire["source"]["source_files"].as_object().unwrap();
    assert_eq!(files.len(), 20, "source files");
    for (path, hash) in files {
        let hash = hash.as_str().unwrap();
        assert!(
            hash.len() == 64
                && hash
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "{path}: not a lowercase sha256 hex: {hash}"
        );
    }
}

#[test]
fn acceptance_native_error_check_rejects_unlisted_error() {
    let oracle = oracle();
    let wire = wire();
    let cases = oracle["cases"].as_array().unwrap();
    let errors = wire["errors"].as_array().unwrap();
    assert!(
        !every_error_is_in_cases(
            &[json!({"status": 418, "body": {"error": "teapot"}})],
            cases
        ),
        "an error that no case lists must be rejected"
    );
    assert!(
        every_error_is_in_cases(&errors[..1], cases),
        "the first real wire error must be found"
    );
}

#[test]
#[ignore = "needs a local Defer checkout at the pinned revision and offline cargo caches; run: SOM_DEFER_REPO=<path> cargo test --locked -- --ignored acceptance_native"]
fn acceptance_native_http_cases() {
    let oracle = oracle();
    let wire = wire();
    let cases = oracle["cases"].as_array().unwrap();
    let edges = oracle["native_path_edge_cases"].as_array().unwrap();

    let root = scratch_root();
    let target = std::env::var_os("SOM_CLIENT_TEST_TARGET")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("target"));

    let snapshot = defer_snapshot(&root);
    check_source_hashes(&snapshot, &wire);
    let native_binary = build_native(&root, &snapshot, &target);

    let ready = root.join("native-ready.json");
    let (server, info) = start_native(&native_binary, &ready);

    let mut native_bodies: HashMap<String, Vec<u8>> = HashMap::new();
    for row in cases {
        let id = row["id"].as_str().unwrap();
        let (status, body) = get(
            origin(&info, row),
            row["path"].as_str().unwrap(),
            row["token"].as_str(),
        );
        assert_eq!(
            u64::from(status),
            row["expected_status"].as_u64().unwrap(),
            "{id}: status"
        );
        let value: Value =
            serde_json::from_slice(&body).unwrap_or_else(|e| panic!("{id}: body is not JSON: {e}"));
        assert_eq!(value, row["expected_body"], "{id}: body");
        native_bodies.insert(id.to_string(), body);
    }
    for edge in edges {
        let path = edge["path"].as_str().unwrap();
        let (status, body) = get(info["base_url"].as_str().unwrap(), path, Some("reader"));
        assert_eq!(
            u64::from(status),
            edge["status"].as_u64().unwrap(),
            "{path}: status"
        );
        assert_eq!(
            String::from_utf8(body).unwrap(),
            edge["body_utf8"].as_str().unwrap(),
            "{path}: body"
        );
    }

    let generated = root.join("generated");
    let compilation = som_service::compile_cnl(CNL).unwrap();
    let files = som_service::assemble(&compilation.bundle).unwrap();
    for required in ["Cargo.toml", "Cargo.lock", "src/lib.rs"] {
        assert!(
            files.contains_key(required),
            "generated crate has no {required}"
        );
    }
    write_files(&generated, &files);

    let probe = root.join("probe");
    write(&probe.join("src/main.rs"), PROBE);
    fs::copy(generated.join("Cargo.lock"), probe.join("Cargo.lock")).unwrap();
    write(
        &probe.join("Cargo.toml"),
        "[package]\nname = \"som-defer-client-probe\"\nversion = \"0.1.0\"\nedition = \"2021\"\n[workspace]\n[dependencies]\ngenerated-client = { package = \"defer_status_client\", path = \"../generated\" }\nserde_json = \"1.0\"\ntokio = { version = \"1\", features = [\"macros\", \"rt-multi-thread\"] }\n[profile.dev]\ndebug = 0\n",
    );
    run_cargo(
        &probe,
        &target.join("probe"),
        &[
            "metadata",
            "--offline",
            "--format-version",
            "1",
            "--manifest-path",
            "Cargo.toml",
        ],
    );
    run_cargo(
        &probe,
        &target.join("probe"),
        &[
            "build",
            "--locked",
            "--offline",
            "--manifest-path",
            "Cargo.toml",
        ],
    );
    let probe_binary = target.join("probe/debug/som-defer-client-probe");

    let probe_cases: Vec<Value> = cases
        .iter()
        .map(|row| {
            json!({
                "id": row["id"],
                "base_url": origin(&info, row),
                "queue": row["queue"],
                "task_id": row["task_id"],
                "token": row["token"],
            })
        })
        .collect();
    let cases_file = root.join("probe-cases.json");
    let results_file = root.join("probe-results.json");
    write(&cases_file, &serde_json::to_string(&probe_cases).unwrap());
    let status = Command::new(&probe_binary)
        .arg(&cases_file)
        .arg(&results_file)
        .env_remove("HTTP_PROXY")
        .env_remove("HTTPS_PROXY")
        .env_remove("ALL_PROXY")
        .env_remove("http_proxy")
        .env_remove("https_proxy")
        .env_remove("all_proxy")
        .env("NO_PROXY", "127.0.0.1,localhost,::1")
        .env("no_proxy", "127.0.0.1,localhost,::1")
        .status()
        .unwrap();
    assert!(
        status.success(),
        "the generated-client probe failed: {status}"
    );
    let records: Vec<Value> = serde_json::from_slice(&fs::read(&results_file).unwrap()).unwrap();
    assert_eq!(records.len(), cases.len(), "probe record count");

    for (row, record) in cases.iter().zip(&records) {
        let id = row["id"].as_str().unwrap();
        assert_eq!(record["id"], row["id"], "{id}: probe record order");
        if row["expected_status"].as_u64() == Some(200) {
            assert_eq!(record["kind"], "Ok", "{id}: kind");
            assert_eq!(record["body"], row["expected_body"], "{id}: decoded body");
        } else {
            assert_eq!(record["kind"], "Http", "{id}: kind");
            assert_eq!(record["status"], row["expected_status"], "{id}: status");
            assert_eq!(
                record["body"],
                json!(native_bodies[id]),
                "{id}: raw body bytes"
            );
            assert_eq!(record["native"], row["expected_body"], "{id}: native body");
        }
    }

    drop(server);
}

fn scratch_root() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let scratch = std::env::var_os("SOM_CLIENT_TEST_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    let root = scratch.join(format!(
        "som-acceptance-native-{}-{nonce}",
        std::process::id()
    ));
    fs::create_dir_all(&root).unwrap();
    root
}

fn defer_snapshot(root: &Path) -> PathBuf {
    let repo = std::env::var_os("SOM_DEFER_REPO")
        .expect("SOM_DEFER_REPO must name a local Defer checkout at the pinned revision");
    let output = Command::new("git")
        .arg("-C")
        .arg(&repo)
        .args(["archive", DEFER_REV])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git archive {DEFER_REV} failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let tar = root.join("defer.tar");
    fs::write(&tar, &output.stdout).unwrap();
    let snapshot = root.join("defer");
    fs::create_dir_all(&snapshot).unwrap();
    let status = Command::new("tar")
        .arg("-xf")
        .arg(&tar)
        .arg("-C")
        .arg(&snapshot)
        .status()
        .unwrap();
    assert!(status.success(), "tar -xf {tar:?} failed");
    snapshot
}

fn check_source_hashes(snapshot: &Path, wire: &Value) {
    for (path, expected) in wire["source"]["source_files"].as_object().unwrap() {
        let bytes = fs::read(snapshot.join(path)).unwrap_or_else(|e| panic!("{path}: {e}"));
        let actual = format!("{:x}", Sha256::digest(&bytes));
        assert_eq!(actual, expected.as_str().unwrap(), "sha256 of {path}");
    }
}

fn build_native(root: &Path, snapshot: &Path, target: &Path) -> PathBuf {
    let native = root.join("native");
    write(&native.join("src/main.rs"), NATIVE_MAIN);
    write(&native.join("Cargo.toml"), NATIVE_MANIFEST);
    fs::copy(snapshot.join("Cargo.lock"), native.join("Cargo.lock")).unwrap();
    run_cargo(
        &native,
        &target.join("native"),
        &[
            "metadata",
            "--offline",
            "--format-version",
            "1",
            "--manifest-path",
            "Cargo.toml",
        ],
    );
    run_cargo(
        &native,
        &target.join("native"),
        &[
            "build",
            "--locked",
            "--offline",
            "--manifest-path",
            "Cargo.toml",
        ],
    );
    target.join("native/debug/som-defer-dfr07-native")
}

struct Server(Child);

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn start_native(binary: &Path, ready: &Path) -> (Server, Value) {
    let child = Command::new(binary)
        .arg(ready)
        .env_remove("HTTP_PROXY")
        .env_remove("HTTPS_PROXY")
        .env_remove("ALL_PROXY")
        .env_remove("http_proxy")
        .env_remove("https_proxy")
        .env_remove("all_proxy")
        .env("NO_PROXY", "127.0.0.1,localhost,::1")
        .env("no_proxy", "127.0.0.1,localhost,::1")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let mut server = Server(child);
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        assert!(
            server.0.try_wait().unwrap().is_none(),
            "native server stopped before readiness"
        );
        let info = fs::read(ready)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
        if let Some(info) = info {
            return (server, info);
        }
        assert!(
            Instant::now() < deadline,
            "native server readiness timed out"
        );
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn origin<'a>(info: &'a Value, row: &Value) -> &'a str {
    let key = if row["server"].as_str() == Some("open") {
        "open_base_url"
    } else {
        "base_url"
    };
    info[key].as_str().unwrap()
}

fn get(origin: &str, path: &str, token: Option<&str>) -> (u16, Vec<u8>) {
    let address = origin.strip_prefix("http://").expect("an http origin");
    let mut stream = TcpStream::connect(address).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut request = format!("GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n");
    if let Some(token) = token {
        request += &format!("Authorization: Bearer {token}\r\n");
    }
    request += "\r\n";
    stream.write_all(request.as_bytes()).unwrap();
    let mut response = Vec::new();
    stream.read_to_end(&mut response).unwrap();
    let split = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .expect("an HTTP header end");
    let head = String::from_utf8_lossy(&response[..split]).into_owned();
    let status = head
        .lines()
        .next()
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap()
        .parse()
        .unwrap();
    (status, response[split + 4..].to_vec())
}

fn run_cargo(dir: &Path, target: &Path, args: &[&str]) -> Output {
    let output = Command::new(std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into()))
        .current_dir(dir)
        .env("CARGO_TARGET_DIR", target)
        .args(args)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "cargo {} failed in {}\nstdout:\n{}\nstderr:\n{}",
        args.join(" "),
        dir.display(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    output
}

fn write_files(dir: &Path, files: &BTreeMap<String, String>) {
    for (path, content) in files {
        write(&dir.join(path), content);
    }
}

fn write(path: &Path, content: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, content).unwrap();
}
