use axum::{
    Json, Router,
    body::Bytes,
    extract::{
        DefaultBodyLimit, State,
        rejection::{BytesRejection, JsonRejection},
    },
    http::StatusCode,
    routing::{get, post},
};
use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use som::{
    SomError,
    compiler::{ASSEMBLE_BODY_LIMIT, COMPILE_BODY_LIMIT, Compiler},
    strict_json,
};
use std::{
    collections::BTreeMap,
    fs,
    io::Write as _,
    net::{Ipv4Addr, SocketAddr},
    path::{Component, Path, PathBuf},
    sync::Arc,
};

#[derive(Parser)]
#[command(
    name = "som",
    about = "Local requirements compiler and code generator with Python and Rust targets"
)]
struct Args {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    /// Load approved compiler assets and serve on loopback.
    Serve {
        #[arg(long, default_value = "som-rs/compiler-assets/current")]
        assets: PathBuf,
        #[arg(long, default_value_t = 8765)]
        port: u16,
    },
    /// Compile fixed English CNL through the local service. Print a DSL bundle.
    Compile {
        source: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8765")]
        url: String,
        /// Forwarded unchecked as the request target member (python, rust or typescript).
        #[arg(long)]
        target: Option<String>,
    },
    /// Assemble a saved DSL bundle through the local service. Print all files.
    Assemble {
        bundle: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8765")]
        url: String,
    },
    /// Send one existing JSON prompt file to the local service.
    Generate {
        prompt: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8765")]
        url: String,
    },
    /// Compile a CNL file or JSON prompt and write the assembled product directory.
    Write {
        source: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8765")]
        url: String,
        /// Forwarded unchecked as the request target member (python, rust or typescript).
        #[arg(long)]
        target: Option<String>,
    },
}

type Reply = Result<Json<Value>, (StatusCode, Json<Value>)>;
fn status(code: &str) -> StatusCode {
    StatusCode::from_u16(som_targets::status(code)).unwrap_or(StatusCode::BAD_REQUEST)
}
fn rejected(error: Value) -> (StatusCode, Json<Value>) {
    (
        status(error["code"].as_str().unwrap_or("INVALID_SCHEMA")),
        Json(error),
    )
}
fn native_error(error: SomError) -> (StatusCode, Json<Value>) {
    (
        status(error.code),
        Json(json!({"status":"error","code":error.code,"detail":error.detail})),
    )
}
fn body(payload: Result<Bytes, BytesRejection>) -> Result<String, (StatusCode, Json<Value>)> {
    let bytes = payload.map_err(|e| {
        let code = if e.status() == StatusCode::PAYLOAD_TOO_LARGE {
            "REQUEST_TOO_LARGE"
        } else {
            "INVALID_JSON"
        };
        (
            e.status(),
            Json(strict_json::envelope_error(
                code,
                &e.body_text(),
                "A complete JSON request within this endpoint's body limit; no source was decoded.",
            )),
        )
    })?;
    String::from_utf8(bytes.to_vec()).map_err(|_| {
        rejected(strict_json::envelope_error(
            "INVALID_JSON",
            "Request is not UTF-8; no source was decoded.",
            "UTF-8 JSON.",
        ))
    })
}
fn exact(value: &Value, keys: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|m| m.len() == keys.len() && keys.iter().all(|key| m.contains_key(*key)))
}

async fn health(State(compiler): State<Arc<Compiler>>) -> Json<Value> {
    let mut value = compiler.health();
    value["targets"] = json!(["python", som_targets::rust::TARGET]);
    value["rust_target"] = som_targets::rust::health();
    Json(value)
}

async fn compile(
    State(compiler): State<Arc<Compiler>>,
    payload: Result<Bytes, BytesRejection>,
) -> Reply {
    let source = body(payload)?;
    let started = std::time::Instant::now();
    let (code, reply) =
        tokio::task::spawn_blocking(move || som_targets::compile(&compiler, &source))
            .await
            .map_err(|e| {
                rejected(strict_json::envelope_error(
                    "OUTPUT_QUALITY_FAILED",
                    &format!("Compiler worker failed: {e}"),
                    "A complete supported request.",
                ))
            })?;
    eprintln!(
        "{}",
        json!({"stage":"compile","total_ms":started.elapsed().as_secs_f64()*1000.0,"http_status":code,"bundle_bytes":reply.get("bundle").map(|b| som::flow::canonical(b).len())})
    );
    if code == 200 {
        return Ok(Json(reply));
    }
    Err((
        StatusCode::from_u16(code).unwrap_or(StatusCode::BAD_REQUEST),
        Json(reply),
    ))
}

async fn assemble(
    State(compiler): State<Arc<Compiler>>,
    payload: Result<Bytes, BytesRejection>,
) -> Reply {
    let source = body(payload)?;
    let doc = strict_json::parse(&source).map_err(rejected)?;
    if !exact(&doc.value, &["bundle"]) {
        return Err(rejected(doc.error(
            "",
            "INVALID_SCHEMA",
            "Assemble requires only a bundle member.",
            "{bundle: complete DSL bundle}",
        )));
    }
    if doc.value["bundle"]["version"] == som_targets::rust::BUNDLE_VERSION {
        return assemble_rust(compiler, doc).await;
    }
    let bundle = doc.value["bundle"].clone();
    let diagnostic_bundle = bundle.clone();
    let expected_legacy = compiler.diagnostic_legacy_plan(&bundle["prompt"]);
    let started = std::time::Instant::now();
    let files = tokio::task::spawn_blocking(move || compiler.assemble_bundle(&bundle))
        .await
        .map_err(|e| {
            rejected(strict_json::envelope_error(
                "OUTPUT_QUALITY_FAILED",
                &format!("Assembler worker failed: {e}"),
                "An admitted complete bundle.",
            ))
        })?
        .map_err(|e| {
            rejected(som::json_diagnostics::bundle_error(
                &doc,
                &diagnostic_bundle,
                &e,
                expected_legacy.as_ref(),
            ))
        })?;
    eprintln!(
        "{}",
        json!({"stage":"assemble","total_ms":started.elapsed().as_secs_f64()*1000.0,"formatted_bytes":files.values().map(String::len).sum::<usize>()})
    );
    Ok(Json(json!({"status":"ok","files":files})))
}

/// A som.dsl-bundle.v2 bundle goes to the v2 assembler (Rust or TypeScript). Every other bundle
/// keeps the unchanged compiler-v1 path.
async fn assemble_rust(compiler: Arc<Compiler>, doc: strict_json::Document) -> Reply {
    let started = std::time::Instant::now();
    let target = doc.value["bundle"]["target"]
        .as_str()
        .unwrap_or("rust")
        .to_owned();
    let (code, reply) = tokio::task::spawn_blocking(move || som_targets::assemble(&compiler, &doc))
        .await
        .map_err(|e| {
            rejected(strict_json::envelope_error(
                "OUTPUT_QUALITY_FAILED",
                &format!("Assembler worker failed: {e}"),
                "An admitted complete bundle.",
            ))
        })?;
    eprintln!(
        "{}",
        json!({"stage":"assemble","target":target,"total_ms":started.elapsed().as_secs_f64()*1000.0,"http_status":code})
    );
    if code == 200 {
        return Ok(Json(reply));
    }
    Err((
        StatusCode::from_u16(code).unwrap_or(StatusCode::BAD_REQUEST),
        Json(reply),
    ))
}

async fn generate(
    State(compiler): State<Arc<Compiler>>,
    payload: Result<Json<Value>, JsonRejection>,
) -> Reply {
    let Json(prompt) = payload.map_err(|error| {
        let mut response = native_error(SomError {
            code: "INVALID_JSON",
            detail: error.to_string(),
        });
        if error.status() == StatusCode::PAYLOAD_TOO_LARGE {
            response.0 = StatusCode::PAYLOAD_TOO_LARGE;
        }
        response
    })?;
    let started = std::time::Instant::now();
    let files = tokio::task::spawn_blocking(move || compiler.generate(&prompt))
        .await
        .map_err(|e| {
            native_error(SomError {
                code: "OUTPUT_QUALITY_FAILED",
                detail: format!("Generator worker failed: {e}"),
            })
        })?
        .map_err(native_error)?;
    eprintln!(
        "{}",
        json!({"stage":"generate","total_ms":started.elapsed().as_secs_f64()*1000.0,"formatted_bytes":files.values().map(String::len).sum::<usize>()})
    );
    Ok(Json(json!({"status":"ok","files":files})))
}

async fn request(
    url: &str,
    endpoint: &str,
    body: String,
) -> Result<(reqwest::StatusCode, Value), Box<dyn std::error::Error>> {
    let host = reqwest::Url::parse(url)?;
    if host.scheme() != "http" || !matches!(host.host_str(), Some("127.0.0.1" | "localhost")) {
        return Err("SOM only connects to the local HTTP service".into());
    }
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .build()?;
    let response = client
        .post(host.join(endpoint)?)
        .header("content-type", "application/json")
        .body(body)
        .send()
        .await?;
    let status = response.status();
    let result = response.json().await?;
    Ok((status, result))
}

fn compile_request_body(
    source: &Path,
    raw: &str,
    target: Option<&str>,
) -> Result<Value, Box<dyn std::error::Error>> {
    let mut body = if source.to_string_lossy().ends_with(".cnl") {
        json!({"format": "som.cnl.v1", "source": raw})
    } else {
        let prompt: Value = serde_json::from_str(raw)?;
        strict_json::parse(raw)
            .map_err(|e| e["detail"].as_str().unwrap_or("Invalid JSON").to_owned())?;
        json!({"format": "som.prompt-json.v1", "prompt": prompt})
    };
    if let Some(target) = target {
        body["target"] = json!(target);
    }
    Ok(body)
}

fn validate_out_dir(out: &Path) -> Result<(), String> {
    if out.as_os_str().is_empty() {
        return Err("Refused --out: path is empty".into());
    }
    match fs::symlink_metadata(out) {
        Ok(meta) => {
            if meta.file_type().is_symlink() {
                return Err(format!(
                    "Refused --out {}: symlinks are not allowed",
                    out.display()
                ));
            }
            if !meta.is_dir() {
                return Err(format!(
                    "Refused --out {}: path exists and is not a directory",
                    out.display()
                ));
            }
            let mut entries =
                fs::read_dir(out).map_err(|e| format!("Refused --out {}: {e}", out.display()))?;
            if entries
                .next()
                .transpose()
                .map_err(|e| e.to_string())?
                .is_some()
            {
                return Err(format!(
                    "Refused --out {}: directory is not empty",
                    out.display()
                ));
            }
            Ok(())
        }
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            for ancestor in out.ancestors().skip(1) {
                if ancestor.as_os_str().is_empty() {
                    break;
                }
                match fs::symlink_metadata(ancestor) {
                    Ok(meta) => {
                        if meta.file_type().is_symlink() {
                            let target_meta = fs::metadata(ancestor)
                                .map_err(|e| format!("Refused --out {}: {e}", out.display()))?;
                            if !target_meta.is_dir() {
                                return Err(format!(
                                    "Refused --out {}: parent {} is not a directory",
                                    out.display(),
                                    ancestor.display()
                                ));
                            }
                        } else if !meta.is_dir() {
                            return Err(format!(
                                "Refused --out {}: parent {} is not a directory",
                                out.display(),
                                ancestor.display()
                            ));
                        }
                        break;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
                    Err(e) => return Err(format!("Refused --out {}: {e}", out.display())),
                }
            }
            Ok(())
        }
        Err(err) => Err(format!("Refused --out {}: {err}", out.display())),
    }
}

fn validate_relative_path(path: &str) -> Result<&Path, String> {
    if path.is_empty() {
        return Err("Refused empty file path in assemble output".into());
    }
    if path.starts_with('/') || path.contains('\\') || path.contains('\0') {
        return Err(format!(
            "Refused unsafe file path in assemble output: {path:?}"
        ));
    }
    for segment in path.split('/') {
        if segment.is_empty() || segment == "." || segment == ".." {
            return Err(format!(
                "Refused unsafe file path in assemble output: {path:?}"
            ));
        }
    }
    let rel = Path::new(path);
    if rel.is_absolute() || !rel.components().all(|c| matches!(c, Component::Normal(_))) {
        return Err(format!(
            "Refused unsafe file path in assemble output: {path:?}"
        ));
    }
    Ok(rel)
}

fn write_files(
    out: &Path,
    files: &BTreeMap<String, String>,
) -> Result<(), Box<dyn std::error::Error>> {
    validate_out_dir(out)?;
    let mut validated = Vec::with_capacity(files.len());
    for (path, content) in files {
        let rel = validate_relative_path(path)?;
        validated.push((rel, content.as_bytes()));
    }
    for (i, (left, _)) in validated.iter().enumerate() {
        for (right, _) in validated.iter().skip(i + 1) {
            if right.starts_with(left) || left.starts_with(right) {
                return Err(format!(
                    "Conflicting file paths in assemble output: {} and {}",
                    left.display(),
                    right.display()
                )
                .into());
            }
        }
    }
    fs::create_dir_all(out)?;
    let out_meta = fs::symlink_metadata(out)?;
    if out_meta.file_type().is_symlink() || !out_meta.is_dir() {
        return Err(format!("Refused --out {}: not a real directory", out.display()).into());
    }
    for (rel, bytes) in validated {
        let mut current = out.to_path_buf();
        if let Some(parent) = rel.parent() {
            for component in parent.components() {
                current.push(component);
                match fs::symlink_metadata(&current) {
                    Ok(meta) => {
                        if meta.file_type().is_symlink() || !meta.is_dir() {
                            return Err(format!(
                                "Refused path inside --out {}: {} is not a real directory",
                                out.display(),
                                current.display()
                            )
                            .into());
                        }
                    }
                    Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                        fs::create_dir(&current)?;
                    }
                    Err(err) => return Err(err.into()),
                }
            }
        }
        let dest = out.join(rel);
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&dest)?;
        file.write_all(bytes)?;
    }
    Ok(())
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    let structured_errors = matches!(
        &args.command,
        Command::Compile { .. } | Command::Assemble { .. } | Command::Write { .. }
    );
    match run(args).await {
        Ok(code) => std::process::exit(code),
        Err(error) => {
            if structured_errors {
                let body = strict_json::envelope_error(
                    "LOCAL_ERROR",
                    &format!("{error}. No source was decoded for this diagnostic."),
                    "Use a readable UTF-8 input and a reachable local HTTP service.",
                );
                eprintln!("{}", serde_json::to_string_pretty(&body).unwrap());
            } else {
                eprintln!("{error}");
            }
            std::process::exit(1);
        }
    }
}

async fn run(args: Args) -> Result<i32, Box<dyn std::error::Error>> {
    match args.command {
        Command::Serve { assets, port } => {
            let compiler = Arc::new(Compiler::load(&assets)?);
            let router = Router::new()
                .route("/healthz", get(health))
                .route(
                    "/v1/compile",
                    post(compile).layer(DefaultBodyLimit::max(COMPILE_BODY_LIMIT)),
                )
                .route(
                    "/v1/assemble",
                    post(assemble).layer(DefaultBodyLimit::max(ASSEMBLE_BODY_LIMIT)),
                )
                .route(
                    "/v1/generate",
                    post(generate).layer(DefaultBodyLimit::max(COMPILE_BODY_LIMIT)),
                )
                .with_state(compiler);
            let listener =
                tokio::net::TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, port)))
                    .await?;
            eprintln!(
                "{}",
                json!({"stage":"listening","address":listener.local_addr()?.to_string(),"purpose":som::compiler::PURPOSE})
            );
            axum::serve(listener, router).await?;
        }
        Command::Compile {
            source,
            url,
            target,
        } => {
            let mut body = json!({"format":"som.cnl.v1","source":fs::read_to_string(source)?});
            if let Some(target) = target {
                body["target"] = json!(target);
            }
            let (status, result) =
                request(&url, "/v1/compile", serde_json::to_string(&body)?).await?;
            if !status.is_success() {
                eprintln!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(2);
            }
            let bundle = result
                .get("bundle")
                .filter(|_| result["status"] == "ok")
                .ok_or("Invalid compile response")?;
            println!("{}", serde_json::to_string_pretty(bundle)?);
        }
        Command::Assemble { bundle, url } => {
            let source = fs::read_to_string(bundle)?;
            let bundle = match strict_json::parse(&source) {
                Ok(document) => document.value,
                Err(error) => {
                    eprintln!("{}", serde_json::to_string_pretty(&error)?);
                    return Ok(1);
                }
            };
            // The server remains the semantic authority. Canonical framing keeps
            // every successful compile result within its promised wire bound.
            let body = som::flow::canonical(&json!({"bundle": bundle}));
            let (status, result) = request(&url, "/v1/assemble", body).await?;
            if !status.is_success() {
                eprintln!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(2);
            }
            println!("{}", serde_json::to_string_pretty(&result)?);
        }
        Command::Generate { prompt, url } => {
            let document: Value = serde_json::from_slice(&fs::read(prompt)?)?;
            let (status, result) =
                request(&url, "/v1/generate", serde_json::to_string(&document)?).await?;
            println!("{}", serde_json::to_string_pretty(&result)?);
            if !status.is_success() {
                return Ok(2);
            }
        }
        Command::Write {
            source,
            out,
            url,
            target,
        } => {
            validate_out_dir(&out)?;
            let raw = fs::read_to_string(&source)?;
            let compile_body = compile_request_body(&source, &raw, target.as_deref())?;
            let (status, compile_result) =
                request(&url, "/v1/compile", serde_json::to_string(&compile_body)?).await?;
            if !status.is_success() {
                eprintln!("{}", serde_json::to_string_pretty(&compile_result)?);
                return Ok(2);
            }
            let bundle = compile_result
                .get("bundle")
                .filter(|_| compile_result["status"] == "ok")
                .ok_or("Invalid compile response")?;
            let assemble_body = som::flow::canonical(&json!({"bundle": bundle}));
            let (status, assemble_result) = request(&url, "/v1/assemble", assemble_body).await?;
            if !status.is_success() {
                eprintln!("{}", serde_json::to_string_pretty(&assemble_result)?);
                return Ok(2);
            }
            let files_obj = assemble_result
                .get("files")
                .and_then(Value::as_object)
                .filter(|_| assemble_result["status"] == "ok")
                .ok_or("Invalid assemble response")?;
            let mut files = BTreeMap::new();
            for (path, content) in files_obj {
                let text = content.as_str().ok_or("Invalid assemble file content")?;
                files.insert(path.clone(), text.to_owned());
            }
            write_files(&out, &files)?;
            let resolved_target = bundle
                .get("target")
                .and_then(Value::as_str)
                .unwrap_or("python");
            println!(
                "{}",
                serde_json::to_string(&json!({
                    "status": "ok",
                    "out": out.to_string_lossy(),
                    "files": files.len(),
                    "target": resolved_target,
                }))?
            );
        }
    }
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static NEXT_TEST_ID: AtomicUsize = AtomicUsize::new(1);

    struct TempScope {
        dir: PathBuf,
    }

    impl TempScope {
        fn new(name: &str) -> Self {
            let id = NEXT_TEST_ID.fetch_add(1, Ordering::Relaxed);
            let dir = std::env::temp_dir().join(format!(
                "som-write-test-{}-{}-{name}",
                std::process::id(),
                id
            ));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).expect("create temp test scope");
            Self { dir }
        }
    }

    impl Drop for TempScope {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.dir);
        }
    }

    #[test]
    fn chooses_request_body_from_source_name_and_optional_target() {
        let cnl_body = compile_request_body(
            Path::new("examples/v1.cnl"),
            "Use prompt version 1.\n",
            None,
        )
        .expect("cnl body");
        assert_eq!(
            cnl_body,
            json!({
                "format": "som.cnl.v1",
                "source": "Use prompt version 1.\n"
            })
        );

        let cnl_rust =
            compile_request_body(Path::new("v1.cnl"), "Use prompt version 1.\n", Some("rust"))
                .expect("cnl rust body");
        assert_eq!(
            cnl_rust,
            json!({
                "format": "som.cnl.v1",
                "source": "Use prompt version 1.\n",
                "target": "rust"
            })
        );

        let json_body = compile_request_body(
            Path::new("examples/v1.prompt.json"),
            r#"{"version":"som.prompt.v1","package":"app"}"#,
            None,
        )
        .expect("json body");
        assert_eq!(
            json_body,
            json!({
                "format": "som.prompt-json.v1",
                "prompt": {"version": "som.prompt.v1", "package": "app"}
            })
        );

        let json_target = compile_request_body(
            Path::new("prompt.txt"),
            r#"{"version":"som.prompt.v1"}"#,
            Some("typescript"),
        )
        .expect("json target body");
        assert_eq!(
            json_target,
            json!({
                "format": "som.prompt-json.v1",
                "prompt": {"version": "som.prompt.v1"},
                "target": "typescript"
            })
        );

        assert!(
            compile_request_body(Path::new("bad.json"), "{not-json", None).is_err(),
            "invalid JSON must fail locally"
        );
    }

    #[test]
    fn out_directory_validation_and_creation_rules() {
        let scope = TempScope::new("out-dir");

        // 1. An empty directory is accepted.
        let empty_dir = scope.dir.join("empty");
        fs::create_dir(&empty_dir).expect("mkdir empty");
        assert!(validate_out_dir(&empty_dir).is_ok());

        // 2. A missing directory (and missing parents) is accepted and created by write_files.
        let missing_dir = scope.dir.join("missing").join("nested_out");
        assert!(validate_out_dir(&missing_dir).is_ok());
        let mut files = BTreeMap::new();
        files.insert("README.md".to_owned(), "# app\n".to_owned());
        write_files(&missing_dir, &files).expect("write to missing dir");
        assert!(missing_dir.is_dir());
        assert_eq!(
            fs::read_to_string(missing_dir.join("README.md")).expect("read README"),
            "# app\n"
        );

        // 3. A non-empty directory is refused.
        assert!(validate_out_dir(&missing_dir).is_err());
        assert!(write_files(&missing_dir, &files).is_err());

        // 4. An existing file given as --out is refused.
        let existing_file = scope.dir.join("some_file.txt");
        fs::write(&existing_file, "hello").expect("write file");
        assert!(validate_out_dir(&existing_file).is_err());
        assert!(write_files(&existing_file, &files).is_err());
    }

    #[test]
    fn relative_path_validation_refuses_unsafe_paths() {
        for bad in [
            "../x", "/x", "a/./b", "a\\b", "", "a/../b", ".", "..", "a//b", "a/", "a\0b",
        ] {
            assert!(
                validate_relative_path(bad).is_err(),
                "path {bad:?} must be refused"
            );
        }
        for good in ["README.md", "src/main.rs", "a/b/c/file.py"] {
            assert!(
                validate_relative_path(good).is_ok(),
                "path {good:?} must be accepted"
            );
        }
    }

    #[test]
    fn write_files_creates_nested_directories_and_writes_nothing_when_any_path_is_refused() {
        let scope = TempScope::new("write-files");

        // 1. Nested paths create their directories in sorted order.
        let ok_out = scope.dir.join("ok_product");
        let mut good_map = BTreeMap::new();
        good_map.insert("src/api/routes.py".to_owned(), "routes = []\n".to_owned());
        good_map.insert("README.md".to_owned(), "# title\n".to_owned());
        good_map.insert(
            "migrations/versions/0001_init.py".to_owned(),
            "rev = 1\n".to_owned(),
        );
        write_files(&ok_out, &good_map).expect("write nested files");
        assert_eq!(
            fs::read_to_string(ok_out.join("README.md")).expect("README"),
            "# title\n"
        );
        assert_eq!(
            fs::read_to_string(ok_out.join("src/api/routes.py")).expect("routes"),
            "routes = []\n"
        );
        assert_eq!(
            fs::read_to_string(ok_out.join("migrations/versions/0001_init.py")).expect("migration"),
            "rev = 1\n"
        );

        // 2. A refused path in the middle of the map writes nothing (neither earlier files nor out dir).
        for bad in ["../x", "/x", "a/./b", "a\\b", ""] {
            let refused_out = scope.dir.join(format!("refused_{}", bad.len()));
            let _ = fs::remove_dir_all(&refused_out);
            let mut bad_map = BTreeMap::new();
            bad_map.insert("00_first.txt".to_owned(), "first".to_owned());
            bad_map.insert(bad.to_owned(), "bad".to_owned());
            bad_map.insert("zz_last.txt".to_owned(), "last".to_owned());
            assert!(
                write_files(&refused_out, &bad_map).is_err(),
                "map with {bad:?} must fail"
            );
            assert!(
                !refused_out.exists(),
                "nothing may be created when {bad:?} is refused"
            );

            // Also when --out already existed as an empty directory, it stays empty:
            fs::create_dir(&refused_out).expect("create empty refused_out");
            assert!(write_files(&refused_out, &bad_map).is_err());
            assert!(
                fs::read_dir(&refused_out)
                    .expect("read_dir")
                    .next()
                    .is_none(),
                "existing empty --out must remain empty when {bad:?} is refused"
            );
        }
    }

    #[tokio::test]
    async fn write_command_round_trips_over_ephemeral_loopback_server() {
        let scope = TempScope::new("http-integration");

        let router = Router::new()
            .route(
                "/v1/compile",
                post(|payload: Bytes| async move {
                    let body: Value = serde_json::from_slice(&payload).expect("compile json");
                    let format = body["format"].as_str().unwrap_or("");
                    if format == "som.cnl.v1" && body["source"].as_str() == Some("REJECT\n") {
                        return (
                            StatusCode::UNPROCESSABLE_ENTITY,
                            Json(json!({"status": "error", "code": "INVALID_VALUE", "detail": "rejected"})),
                        );
                    }
                    let mut bundle = json!({
                        "version": "som.dsl-bundle.v1",
                        "received_format": format,
                    });
                    if let Some(target) = body.get("target") {
                        bundle["target"] = target.clone();
                    }
                    (StatusCode::OK, Json(json!({"status": "ok", "bundle": bundle})))
                }),
            )
            .route(
                "/v1/assemble",
                post(|payload: Bytes| async move {
                    let text = std::str::from_utf8(&payload).expect("utf8");
                    let doc = strict_json::parse(text).expect("strict json");
                    assert!(super::exact(&doc.value, &["bundle"]));
                    let target = doc.value["bundle"]
                        .get("target")
                        .and_then(Value::as_str)
                        .unwrap_or("python");
                    let mut files = serde_json::Map::new();
                    files.insert("README.md".into(), json!(format!("# target={target}\n")));
                    files.insert("src/app.txt".into(), json!("ok\n"));
                    (StatusCode::OK, Json(json!({"status": "ok", "files": files})))
                }),
            );

        let listener = tokio::net::TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, 0)))
            .await
            .expect("bind ephemeral loopback");
        let addr = listener.local_addr().expect("local_addr");
        let url = format!("http://{addr}");
        let server = tokio::spawn(async move {
            let _ = axum::serve(listener, router).await;
        });

        // 1. CNL source without --target -> writes Python product and exits 0.
        let cnl_path = scope.dir.join("app.cnl");
        fs::write(&cnl_path, "Use prompt version 1.\n").expect("write cnl");
        let cnl_out = scope.dir.join("out_cnl");
        let code = run(Args {
            command: Command::Write {
                source: cnl_path,
                out: cnl_out.clone(),
                url: url.clone(),
                target: None,
            },
        })
        .await
        .expect("run cnl write");
        assert_eq!(code, 0);
        assert_eq!(
            fs::read_to_string(cnl_out.join("README.md")).expect("README"),
            "# target=python\n"
        );
        assert_eq!(
            fs::read_to_string(cnl_out.join("src/app.txt")).expect("src/app.txt"),
            "ok\n"
        );

        // 2. JSON prompt source with --target rust -> forwards target and writes files.
        let json_path = scope.dir.join("app.prompt.json");
        fs::write(&json_path, r#"{"version":"som.prompt.v1"}"#).expect("write json");
        let json_out = scope.dir.join("out_rust");
        let code = run(Args {
            command: Command::Write {
                source: json_path,
                out: json_out.clone(),
                url: url.clone(),
                target: Some("rust".to_owned()),
            },
        })
        .await
        .expect("run json write");
        assert_eq!(code, 0);
        assert_eq!(
            fs::read_to_string(json_out.join("README.md")).expect("README"),
            "# target=rust\n"
        );

        // 3. HTTP rejection exits 2 and writes nothing.
        let reject_path = scope.dir.join("reject.cnl");
        fs::write(&reject_path, "REJECT\n").expect("write reject cnl");
        let reject_out = scope.dir.join("out_reject");
        let code = run(Args {
            command: Command::Write {
                source: reject_path,
                out: reject_out.clone(),
                url,
                target: None,
            },
        })
        .await
        .expect("run reject write");
        assert_eq!(code, 2);
        assert!(!reject_out.exists());

        server.abort();
    }
}
