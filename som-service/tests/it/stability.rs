//! The public surface of som-service matches STABILITY.md and contract/public-surface.json (issue #203).
use serde_json::{Value, json};
use std::collections::BTreeSet;
use std::process::Command;

const CONTRACT: &str = include_str!("../../contract/public-surface.json");
const STABILITY: &str = include_str!("../../STABILITY.md");
const BIN: &str = env!("CARGO_BIN_EXE_som-service");

fn contract() -> Value {
    serde_json::from_str(CONTRACT).expect("contract json")
}

/// The command names that `som-service --help` lists under `Commands:`, without `help`.
fn cli_names() -> Vec<String> {
    let out = Command::new(BIN)
        .arg("--help")
        .output()
        .expect("run --help");
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    let mut names = Vec::new();
    let mut in_commands = false;
    for line in text.lines() {
        if line.trim() == "Commands:" {
            in_commands = true;
            continue;
        }
        if !in_commands {
            continue;
        }
        if line.trim().is_empty() {
            break;
        }
        if let Some(name) = line
            .split_whitespace()
            .next()
            .filter(|name| *name != "help")
        {
            names.push(name.to_string());
        }
    }
    names
}

fn schema_mismatches(contract: &Value) -> Vec<String> {
    let mut out = Vec::new();
    let health = som_service::health();
    let ids = &contract["schema_ids"];
    for key in ["formats", "prompt_versions", "dsl_versions", "targets"] {
        if health[key] != ids[key] {
            out.push(format!(
                "schema_ids.{key}: health() has {} and the contract has {}",
                health[key], ids[key]
            ));
        }
    }
    if ids["bundle"] != som_service::bundle::VERSION {
        out.push(format!(
            "schema_ids.bundle: bundle::VERSION is {} and the contract has {}",
            som_service::bundle::VERSION,
            ids["bundle"]
        ));
    }
    if ids["family"] != som_service::bundle::FAMILY {
        out.push(format!(
            "schema_ids.family: bundle::FAMILY is {} and the contract has {}",
            som_service::bundle::FAMILY,
            ids["family"]
        ));
    }
    out
}

fn openapi_mismatches(contract: &Value) -> Vec<String> {
    let doc: Value =
        serde_json::from_str(&som_service::server::openapi_json()).expect("openapi json");
    let served: BTreeSet<String> = doc["paths"]
        .as_object()
        .expect("paths")
        .keys()
        .cloned()
        .collect();
    let listed: BTreeSet<String> = contract["routes"]
        .as_array()
        .expect("routes")
        .iter()
        .filter(|route| route["openapi"] == true)
        .map(|route| route["path"].as_str().expect("path").to_string())
        .collect();
    if served == listed {
        Vec::new()
    } else {
        vec![format!(
            "openapi paths {served:?} differ from the contract's {listed:?}"
        )]
    }
}

/// Starts the router in process and compares the status of each contract route.
async fn route_mismatches(contract: &Value) -> Vec<String> {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("bind");
    let base = format!("http://{}", listener.local_addr().expect("addr"));
    tokio::spawn(async move {
        axum::serve(listener, som_service::server::router())
            .await
            .expect("serve");
    });
    let client = reqwest::Client::new();
    let mut out = Vec::new();
    for route in contract["routes"].as_array().expect("routes") {
        let method = route["method"].as_str().expect("method");
        let path = route["path"].as_str().expect("path");
        let want = route["smoke_status"].as_u64().expect("smoke_status");
        let request = if method == "POST" {
            client
                .post(format!("{base}{path}"))
                .header("content-type", "application/json")
                .body("{")
        } else {
            client.get(format!("{base}{path}"))
        };
        let got = request.send().await.expect("send").status().as_u16() as u64;
        if got != want {
            out.push(format!(
                "{method} {path}: status {got}, the contract says {want}"
            ));
        }
    }
    out
}

async fn surface_mismatches(contract: &Value) -> Vec<String> {
    let mut out = Vec::new();
    let cli = cli_names();
    let want: Vec<String> = contract["cli"]
        .as_array()
        .expect("cli")
        .iter()
        .map(|name| name.as_str().expect("name").to_string())
        .collect();
    if cli != want {
        out.push(format!("cli {cli:?} differs from the contract's {want:?}"));
    }
    out.extend(schema_mismatches(contract));
    out.extend(openapi_mismatches(contract));
    out.extend(route_mismatches(contract).await);
    out
}

fn strings_in(value: &Value, out: &mut Vec<String>) {
    match value {
        Value::String(text) => out.push(text.clone()),
        Value::Array(items) => items.iter().for_each(|item| strings_in(item, out)),
        Value::Object(fields) => fields.values().for_each(|field| strings_in(field, out)),
        _ => {}
    }
}

#[tokio::test]
async fn public_surface_matches_contract() {
    let mismatches = surface_mismatches(&contract()).await;
    assert!(mismatches.is_empty(), "{mismatches:#?}");
}

#[tokio::test]
async fn public_surface_fails_when_a_route_changes() {
    let mut changed = contract();
    for route in changed["routes"].as_array_mut().expect("routes") {
        if route["path"] == "/compile" {
            route["path"] = json!("/compile2");
        }
    }
    let mismatches = surface_mismatches(&changed).await;
    assert!(
        mismatches.iter().any(|m| m.contains("/compile2")),
        "{mismatches:#?}"
    );
}

#[tokio::test]
async fn public_surface_fails_when_a_schema_id_changes() {
    let mut changed = contract();
    changed["schema_ids"]["formats"][0] = json!("som.cnl.service.v9");
    let mismatches = surface_mismatches(&changed).await;
    assert!(
        !mismatches.is_empty(),
        "a changed schema id must be reported"
    );
}

#[test]
fn stability_md_names_every_item() {
    let contract = contract();
    let mut items = Vec::new();
    for name in contract["cli"].as_array().expect("cli") {
        items.push(name.as_str().expect("name").to_string());
    }
    for route in contract["routes"].as_array().expect("routes") {
        items.push(route["path"].as_str().expect("path").to_string());
    }
    strings_in(&contract["schema_ids"], &mut items);
    let missing: Vec<String> = items
        .into_iter()
        .filter(|item| !STABILITY.contains(&format!("`{item}`")))
        .collect();
    assert!(
        missing.is_empty(),
        "not named in STABILITY.md: {missing:#?}"
    );
}

#[test]
fn stability_md_states_the_promise() {
    assert!(STABILITY.to_lowercase().contains("semantic versioning"));
    assert!(STABILITY.contains("2.0.0"));
}
