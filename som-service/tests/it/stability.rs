//! The public surface of som-service (CLI, HTTP routes, schema ids, bundle
//! format) is fixed by `contract/public-surface.json` and documented in
//! `STABILITY.md` (#203). A change to one of them must change both files.
use serde_json::{Value, json};
use std::{collections::BTreeSet, process::Command};

const BIN: &str = env!("CARGO_BIN_EXE_som-service");
const CONTRACT: &str = include_str!("../../contract/public-surface.json");
const STABILITY: &str = include_str!("../../STABILITY.md");

fn contract() -> Value {
    serde_json::from_str(CONTRACT).expect("contract JSON")
}

/// The command names printed under `Commands:` by `som-service --help`.
fn cli_names() -> Vec<String> {
    let output = Command::new(BIN)
        .arg("--help")
        .output()
        .expect("run --help");
    let text = String::from_utf8(output.stdout).expect("UTF-8 help");
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
        if let Some(word) = line.split_whitespace().next()
            && word != "help"
        {
            names.push(word.to_owned());
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
                "schema_ids.{key}: contract {} but health() {}",
                ids[key], health[key]
            ));
        }
    }
    if ids["bundle"] != som_service::bundle::VERSION {
        out.push(format!(
            "schema_ids.bundle: contract {} but bundle::VERSION {}",
            ids["bundle"],
            som_service::bundle::VERSION
        ));
    }
    if ids["family"] != som_service::bundle::FAMILY {
        out.push(format!(
            "schema_ids.family: contract {} but bundle::FAMILY {}",
            ids["family"],
            som_service::bundle::FAMILY
        ));
    }
    out
}

fn cli_mismatches(contract: &Value) -> Vec<String> {
    let expected: Vec<String> = contract["cli"]
        .as_array()
        .expect("cli array")
        .iter()
        .map(|name| name.as_str().expect("cli name").to_owned())
        .collect();
    let actual = cli_names();
    if expected == actual {
        Vec::new()
    } else {
        vec![format!("cli: contract {expected:?} but --help {actual:?}")]
    }
}

fn openapi_mismatches(contract: &Value) -> Vec<String> {
    let document: Value =
        serde_json::from_str(&som_service::server::openapi_json()).expect("openapi JSON");
    let documented: BTreeSet<String> = document["paths"]
        .as_object()
        .expect("openapi paths")
        .keys()
        .cloned()
        .collect();
    let declared: BTreeSet<String> = contract["routes"]
        .as_array()
        .expect("routes array")
        .iter()
        .filter(|route| route["openapi"] == true)
        .map(|route| route["path"].as_str().expect("route path").to_owned())
        .collect();
    if documented == declared {
        Vec::new()
    } else {
        vec![format!(
            "openapi paths: contract {declared:?} but openapi_json() {documented:?}"
        )]
    }
}

/// Starts the router on a free local port, as `tests/it/server_http.rs` does.
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

async fn route_mismatches(contract: &Value) -> Vec<String> {
    let url = start().await;
    let client = reqwest::Client::new();
    let mut out = Vec::new();
    for route in contract["routes"].as_array().expect("routes array") {
        let method = route["method"].as_str().expect("method");
        let path = route["path"].as_str().expect("route path");
        let expected = route["smoke_status"].as_u64().expect("smoke_status") as u16;
        let request = match method {
            "GET" => client.get(format!("{url}{path}")),
            "POST" => client
                .post(format!("{url}{path}"))
                .header("content-type", "application/json")
                .body("{"),
            other => {
                out.push(format!("unsupported method in contract: {other}"));
                continue;
            }
        };
        let status = request.send().await.expect("send").status().as_u16();
        if status != expected {
            out.push(format!(
                "{method} {path}: contract {expected} but got {status}"
            ));
        }
    }
    out
}

async fn surface_mismatches(contract: &Value) -> Vec<String> {
    let mut out = cli_mismatches(contract);
    out.extend(schema_mismatches(contract));
    out.extend(openapi_mismatches(contract));
    out.extend(route_mismatches(contract).await);
    out
}

#[tokio::test]
async fn public_surface_matches_contract() {
    let mismatches = surface_mismatches(&contract()).await;
    assert!(mismatches.is_empty(), "{mismatches:#?}");
}

#[tokio::test]
async fn public_surface_fails_when_a_route_changes() {
    let mut changed = contract();
    for route in changed["routes"].as_array_mut().expect("routes array") {
        if route["path"] == "/compile" {
            route["path"] = json!("/compile2");
        }
    }
    let mismatches = surface_mismatches(&changed).await;
    assert!(
        mismatches.iter().any(|text| text.contains("/compile2")),
        "{mismatches:#?}"
    );
}

#[tokio::test]
async fn public_surface_fails_when_a_schema_id_changes() {
    let mut changed = contract();
    changed["schema_ids"]["formats"][0] = json!("som.cnl.service.v9");
    let mismatches = surface_mismatches(&changed).await;
    assert!(
        mismatches.iter().any(|text| text.contains("v9")),
        "{mismatches:#?}"
    );
}

#[test]
fn stability_md_names_every_item() {
    let contract = contract();
    let mut items: Vec<String> = Vec::new();
    for name in contract["cli"].as_array().expect("cli array") {
        items.push(name.as_str().expect("cli name").to_owned());
    }
    for route in contract["routes"].as_array().expect("routes array") {
        items.push(route["path"].as_str().expect("route path").to_owned());
    }
    for value in contract["schema_ids"]
        .as_object()
        .expect("schema_ids")
        .values()
    {
        match value {
            Value::String(text) => items.push(text.clone()),
            Value::Array(list) => {
                for text in list {
                    items.push(text.as_str().expect("schema id").to_owned());
                }
            }
            other => panic!("unexpected schema id value {other}"),
        }
    }
    assert!(!items.is_empty());
    for item in items {
        assert!(
            STABILITY.contains(&format!("`{item}`")),
            "STABILITY.md does not name `{item}`"
        );
    }
}

#[test]
fn stability_md_states_the_promise() {
    assert!(
        STABILITY
            .to_ascii_lowercase()
            .contains("semantic versioning")
    );
    assert!(STABILITY.contains("2.0.0"));
}
