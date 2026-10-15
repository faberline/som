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
    fs,
    net::{Ipv4Addr, SocketAddr},
    path::PathBuf,
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
        /// Forwarded unchecked as the request target member (python or rust).
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
    Json(compiler.health())
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

/// A som.dsl-bundle.v2 bundle goes to the Rust assembler. Every other bundle keeps the
/// unchanged compiler-v1 path.
async fn assemble_rust(compiler: Arc<Compiler>, doc: strict_json::Document) -> Reply {
    let started = std::time::Instant::now();
    let (code, reply) =
        tokio::task::spawn_blocking(move || som_targets::rust::assemble(&compiler, &doc))
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
        json!({"stage":"assemble","target":"rust","total_ms":started.elapsed().as_secs_f64()*1000.0,"http_status":code})
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

#[tokio::main]
async fn main() {
    let args = Args::parse();
    let structured_errors = matches!(
        &args.command,
        Command::Compile { .. } | Command::Assemble { .. }
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
    }
    Ok(0)
}
