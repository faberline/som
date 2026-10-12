use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, State, rejection::JsonRejection},
    http::StatusCode,
    routing::{get, post},
};
use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use som::{
    DSL_VERSION, DSL_VERSION_2, SomError, assemble, load_blocks, model::Decoder,
    resolve_prompt_refs, validate_prompt,
};
use std::{
    fs,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

#[derive(Parser)]
#[command(name = "som", about = "Local SOM code generation service")]
struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Load the approved blocks and model, then serve on loopback.
    Serve {
        #[arg(long, default_value = "som-rs/models/current")]
        model: PathBuf,
        #[arg(
            long,
            default_value = "som-code-python/data/blocks/current/blocks.json"
        )]
        blocks: PathBuf,
        #[arg(long, default_value_t = 8765)]
        port: u16,
    },
    /// Send one prompt file to the running local service.
    Generate {
        prompt: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8765")]
        url: String,
    },
}

struct AppState {
    model: Mutex<Decoder>,
    blocks: std::collections::BTreeMap<String, som::Block>,
    training_steps: u64,
    model_manifest_sha256: String,
    blocks_sha256: String,
    dsl_version: String,
    quality: Option<som::quality::Quality>,
    legacy_blocks: Option<std::collections::BTreeMap<String, som::Block>>,
    flow_decoder: bool,
    flow_model_purpose: String,
    flow_decode_policy: String,
    flow_input_policy: String,
}

fn sha256(path: &Path) -> Result<String, SomError> {
    let bytes = fs::read(path).map_err(|e| SomError {
        code: "MODEL_UNAVAILABLE",
        detail: e.to_string(),
    })?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

fn verify_manifest(model: &Path, blocks: &Path) -> Result<(u64, String), SomError> {
    let manifest: Value = serde_json::from_slice(&fs::read(model.join("manifest.json")).map_err(
        |e| SomError {
            code: "MODEL_UNAVAILABLE",
            detail: e.to_string(),
        },
    )?)
    .map_err(|e| SomError {
        code: "MODEL_UNAVAILABLE",
        detail: e.to_string(),
    })?;
    if manifest["dsl_version"] != DSL_VERSION
        && manifest["dsl_version"] != DSL_VERSION_2
        && manifest["dsl_version"] != som::flow::DSL_VERSION {
        return Err(SomError {
            code: "MODEL_UNAVAILABLE",
            detail: "manifest DSL version mismatch".into(),
        });
    }
    let config: Value =
        serde_json::from_slice(&fs::read(model.join("config.json")).map_err(|e| SomError {
            code: "MODEL_UNAVAILABLE",
            detail: e.to_string(),
        })?)
        .map_err(|e| SomError {
            code: "MODEL_UNAVAILABLE",
            detail: e.to_string(),
        })?;
    let mut names = vec!["tokenizer.json", "config.json", "model.safetensors"];
    if config["output_mode"] == "flow_plan" {
        let m3 = config["training_profile"] == som::flow_m3_model::PURPOSE;
        if m3 { som::flow_m3_model::verify_config(&config)?; }
        else {
            if !config["training_profile"].is_null() || !config["flow_decode_policy"].is_null() {
                return Err(SomError { code: "MODEL_UNAVAILABLE", detail: "unknown flow training profile".into() });
            }
            som::flow_model::verify_config(&config)?;
        }
        names.extend(["contracts.json", "contract.md", "codec.json", "admission.json",
            "development.jsonl", "quality.json", "ruff", "pyproject.toml",
            "quality-contract.md", "quality-exceptions.json", "output-budget.json"]);
        som::flow_model::verify_input_policy(model, &config)?;
        if !m3 && !config["flow_input_policy"].is_null() {
            names.push("input-policy.json");
        }
        if m3 {
            names.extend(som::flow_m3_model::EXTRA_SIDECARS.iter().copied());
            som::flow_m3_model::verify_sidecars(model, &manifest)?;
        } else { som::flow_model::verify_sidecars(model, &manifest)?; }
        let legacy = model.join("legacy");
        if manifest["legacy_manifest_sha256"] != sha256(&legacy.join("manifest.json"))? {
            return Err(SomError {code: "MODEL_UNAVAILABLE", detail: "legacy manifest hash mismatch".into()});
        }
        verify_manifest(&legacy, &legacy.join("blocks.json"))?;
    } else if manifest["dsl_version"] == som::flow::DSL_VERSION {
        return Err(SomError {code: "MODEL_UNAVAILABLE", detail: "DSL v3 needs the flow_plan decoder".into()});
    }
    if config["output_mode"] == "operation_blocks" {
        names.push("dsl_template.json");
    }
    if config["output_mode"] == "block_plan" {
        names.extend(["contracts.json", "contract.md", "codec.json"]);
        let catalog = if manifest["quality_contract"] == som::quality::VERSION {
            names.extend(["quality.json", "ruff", "pyproject.toml", "quality-contract.md", "quality-exceptions.json"]);
            som::quality::CATALOG_TEXT
        } else { som::composed::CATALOG_TEXT };
        if sha256(&model.join("contracts.json"))? != format!("{:x}", Sha256::digest(catalog.as_bytes()))
            || sha256(&model.join("contract.md"))? != format!("{:x}", Sha256::digest(som::composed::CONTRACT_TEXT.as_bytes())) {
            return Err(SomError { code: "MODEL_UNAVAILABLE", detail: "compiled composition contracts differ from model".into() });
        }
        let legacy = model.join("legacy");
        if manifest["legacy_manifest_sha256"] != sha256(&legacy.join("manifest.json"))? {
            return Err(SomError { code: "MODEL_UNAVAILABLE", detail: "legacy manifest hash mismatch".into() });
        }
        verify_manifest(&legacy, &legacy.join("blocks.json"))?;
    }
    let listed = manifest["files"].as_object().ok_or_else(|| SomError {
        code: "MODEL_UNAVAILABLE",
        detail: "manifest lacks file hashes".into(),
    })?;
    if listed.len() != names.len() {
        return Err(SomError {
            code: "MODEL_UNAVAILABLE",
            detail: "manifest file list mismatch".into(),
        });
    }
    for name in names {
        if manifest["files"][name].as_str() != Some(sha256(&model.join(name))?.as_str()) {
            return Err(SomError {
                code: "MODEL_UNAVAILABLE",
                detail: format!("{name} hash mismatch"),
            });
        }
    }
    if manifest["blocks_sha256"].as_str() != Some(sha256(blocks)?.as_str()) {
        return Err(SomError {
            code: "MODEL_UNAVAILABLE",
            detail: "block library hash mismatch".into(),
        });
    }
    let steps = manifest["training_steps"]
        .as_u64()
        .ok_or_else(|| SomError {
            code: "MODEL_UNAVAILABLE",
            detail: "manifest lacks training_steps".into(),
        })?;
    Ok((steps, manifest["dsl_version"].as_str().unwrap().to_owned()))
}

async fn health(State(state): State<Arc<AppState>>) -> Json<Value> {
    let mut response = json!({"status":"ok","model_loaded":true,"blocks_loaded":true,"block_count":state.blocks.len(),"dsl_version":state.dsl_version,"training_steps":state.training_steps,"model_manifest_sha256":state.model_manifest_sha256,"blocks_sha256":state.blocks_sha256});
    if state.quality.is_some() {
        response["quality_contract"] = json!(som::quality::VERSION);
        response["formatter_version"] = json!(som::quality::FORMATTER_VERSION);
    }
    if state.flow_decoder {
        response["flow_prompt_version"] = json!(som::flow::VERSION);
        response["flow_codec_version"] = json!(som::flow_codec::COMPACT_VERSION);
        response["flow_model_evidence"] = json!(if state.flow_model_purpose == som::flow_m3_model::PURPOSE {
            "disjoint-full-training-candidate"
        } else { "seen-development-fixtures-only" });
        response["flow_model_purpose"] = json!(state.flow_model_purpose);
        response["flow_decode_policy"] = json!(state.flow_decode_policy);
        if !state.flow_input_policy.is_empty() {
            response["flow_input_policy"] = json!(state.flow_input_policy);
        }
        response["flow_output_budget"] = json!("som.m2.output-budget.v2");
    }
    Json(response)
}

async fn generate(
    State(state): State<Arc<AppState>>,
    payload: Result<Json<Value>, JsonRejection>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let started = std::time::Instant::now();
    let Json(prompt) = payload.map_err(|error| {
        response_error(SomError {
            code: "INVALID_JSON",
            detail: error.to_string(),
        })
    })?;
    validate_prompt(&prompt).map_err(response_error)?;
    let flow_request = prompt["version"] == som::flow::VERSION;
    if flow_request && !state.flow_decoder {
        return Err(response_error(SomError {
            code: "MODEL_UNAVAILABLE",
            detail: "the selected model has no admitted flow_plan decoder".into(),
        }));
    }
    let validation_ms = started.elapsed().as_secs_f64() * 1000.0;
    let prompt_text = serde_json::to_string(&prompt).map_err(|e| {
        response_error(SomError {
            code: "INVALID_SCHEMA",
            detail: e.to_string(),
        })
    })?;
    let state_for_worker = Arc::clone(&state);
    let inference_started = std::time::Instant::now();
    let dsl_text = tokio::task::spawn_blocking(move || {
        state_for_worker
            .model
            .lock()
            .map_err(|_| SomError {
                code: "MODEL_FAILED",
                detail: "model lock poisoned".into(),
            })?
            .generate(&prompt_text)
    })
    .await
    .map_err(|e| {
        response_error(SomError {
            code: "MODEL_FAILED",
            detail: e.to_string(),
        })
    })?
    .map_err(response_error)?;
    let inference_ms = inference_started.elapsed().as_secs_f64() * 1000.0;
    let dsl_validation_started = std::time::Instant::now();
    let dsl: Value = serde_json::from_str(&dsl_text).map_err(|e| {
        response_error(SomError {
            code: "INVALID_DSL",
            detail: e.to_string(),
        })
    })?;
    if flow_request {
        som::flow::validate_plan_with(&prompt, &dsl, &som::flow::COMPACT_CATALOG).map_err(response_error)?;
        som::flow_codec::encode_with(&prompt, &dsl, &som::flow::COMPACT_CATALOG).map_err(response_error)?;
    } else if prompt["version"] == som::composed::VERSION {
        if let Some(quality) = &state.quality {
            som::composed::validate_plan_with(&prompt, &dsl, &quality.catalog).map_err(response_error)?;
        } else { som::composed::validate_plan(&prompt, &dsl).map_err(response_error)?; }
    }
    let resolved = if flow_request {
        som::flow_compact::resolve(&prompt, &dsl)
    } else { resolve_prompt_refs(&prompt, &dsl) }.map_err(response_error)?;
    let dsl_validation_ms = dsl_validation_started.elapsed().as_secs_f64()*1000.0;
    let assembly_started = std::time::Instant::now();
    let quality_request = state.quality.is_some() && (flow_request || prompt["version"] == som::composed::VERSION);
    let library = if state.quality.is_some() && !quality_request { state.legacy_blocks.as_ref().unwrap() } else { &state.blocks };
    let files = if flow_request {
        som::flow_compact::render_resolved(&resolved, library)
    } else { assemble(&resolved, library) }.map_err(response_error)?;
    let assembly_ms = assembly_started.elapsed().as_secs_f64()*1000.0;
    let formatting_started = std::time::Instant::now();
    let files = if quality_request {
        let quality = state.quality.clone().unwrap();
        tokio::task::spawn_blocking(move || if flow_request {
            quality.finalize_with_byte_limit(files, None)
        } else { quality.finalize(files) }).await
            .map_err(|e|response_error(SomError {code:"OUTPUT_QUALITY_FAILED", detail:e.to_string()}))?
            .map_err(response_error)?
    } else { files };
    eprintln!("{}", json!({"stage":"request", "formatted_bytes":files.values().map(|s|s.len()).sum::<usize>(), "validation_ms":validation_ms,
        "dsl_validation_ms":dsl_validation_ms,"inference_and_decode_ms":inference_ms,"assembly_ms":assembly_ms,
        "formatting_ms":formatting_started.elapsed().as_secs_f64()*1000.0,
        "total_ms":started.elapsed().as_secs_f64()*1000.0}));
    Ok(Json(json!({"status":"ok", "files":files})))
}

fn response_error(error: SomError) -> (StatusCode, Json<Value>) {
    let status = match error.code {
        "UNSUPPORTED_BLOCK"
        | "UNSUPPORTED_CONTRACT"
        | "UNSUPPORTED_VERSION"
        | "SCOPE_TOO_LARGE"
        | "UNSUPPORTED_LOGIC"
        | "UNSUPPORTED_TEST" => StatusCode::UNPROCESSABLE_ENTITY,
        "MODEL_FAILED" | "MODEL_UNAVAILABLE" | "OUTPUT_QUALITY_FAILED" => StatusCode::INTERNAL_SERVER_ERROR,
        "MODEL_TIMEOUT" => StatusCode::GATEWAY_TIMEOUT,
        _ => StatusCode::BAD_REQUEST,
    };
    (
        status,
        Json(json!({"status":"error","code":error.code,"detail":error.detail})),
    )
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    match Args::parse().command {
        Command::Serve {
            model,
            blocks,
            port,
        } => {
            let (training_steps, dsl_version) = verify_manifest(&model, &blocks)?;
            let model_manifest_sha256 = sha256(&model.join("manifest.json"))?;
            let blocks_sha256 = sha256(&blocks)?;
            let blocks = load_blocks(&blocks)?;
            let quality = som::quality::Quality::load(&model)?;
            let decoder = Decoder::load(&model)?;
            let flow_decoder = decoder.config.output_mode == "flow_plan";
            if flow_decoder && quality.is_none() {
                return Err(SomError {code: "MODEL_UNAVAILABLE", detail: "flow generation requires native output quality".into()}.into());
            }
            let legacy_blocks = if quality.is_some() {
                let path = if flow_decoder { "legacy/legacy/blocks.json" } else { "legacy/blocks.json" };
                Some(load_blocks(&model.join(path))?)
            } else { None };
            if flow_decoder {
                som::composed::verify_blocks_with(&blocks, &som::flow::COMPACT_CATALOG)?;
                som::composed::verify_blocks_with(&blocks, &quality.as_ref().unwrap().catalog)?;
            }
            if decoder.config.output_mode == "block_plan" {
                if let Some(quality) = &quality { som::composed::verify_blocks_with(&blocks, &quality.catalog)?; }
                else { som::composed::verify_blocks(&blocks)?; }
            }
            decoder.warmup()?;
            let flow_model_purpose = if decoder.config.training_profile.is_empty() {
                som::flow_model::PURPOSE.to_owned()
            } else { decoder.config.training_profile.clone() };
            let flow_decode_policy = if decoder.config.flow_decode_policy.is_empty() {
                "unconstrained".to_owned()
            } else { decoder.config.flow_decode_policy.clone() };
            let flow_input_policy = decoder.config.flow_input_policy.clone();
            let state = Arc::new(AppState {
                model: Mutex::new(decoder),
                blocks,
                training_steps,
                model_manifest_sha256,
                blocks_sha256,
                dsl_version,
                quality,
                legacy_blocks,
                flow_decoder,
                flow_model_purpose,
                flow_decode_policy,
                flow_input_policy,
            });
            let router = Router::new()
                .route("/healthz", get(health))
                .route("/v1/generate", post(generate))
                .layer(DefaultBodyLimit::max(256 * 1024))
                .with_state(state);
            let addr = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port);
            let listener = tokio::net::TcpListener::bind(addr).await?;
            eprintln!("SOM listening on {}", listener.local_addr()?);
            axum::serve(listener, router).await?;
        }
        Command::Generate { prompt, url } => {
            let host = reqwest::Url::parse(&url)?;
            if host.scheme() != "http"
                || (host.host_str() != Some("127.0.0.1") && host.host_str() != Some("localhost"))
            {
                return Err("generate only connects to the local service".into());
            }
            let document: Value = serde_json::from_slice(&fs::read(prompt)?)?;
            let client = reqwest::Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .build()?;
            let response = client
                .post(host.join("/v1/generate")?)
                .json(&document)
                .send()
                .await?;
            let status = response.status();
            let result: Value = response.json().await?;
            println!("{}", serde_json::to_string_pretty(&result)?);
            if !status.is_success() {
                std::process::exit(2);
            }
        }
    }
    Ok(())
}
