use std::collections::HashMap;
use std::sync::Arc;

use axum::Router;
use axum::http::StatusCode;
use serde_json::Value;
use server_lifecycle::LifecycleController;
use som_reference_order::application::idempotency::SystemClock;
use som_reference_order::infrastructure::memory_store::MemoryStore;
use som_reference_order::interfaces::auth::{AuthMode, StaticTokenVerifier};
use som_reference_order::interfaces::error::ErrorCode;
use som_reference_order::interfaces::metrics::OperationMetrics;
use som_reference_order::interfaces::routes::router_with_probes;

use crate::common::{body_json, call, send};

/// The router with the probes; the data routes still need a token.
fn probes_app(lifecycle: LifecycleController) -> Router {
    let verifier = Arc::new(StaticTokenVerifier::new(AuthMode::Required, HashMap::new()));
    router_with_probes(
        Arc::new(MemoryStore::new()),
        Arc::new(SystemClock),
        verifier,
        Arc::new(OperationMetrics::new()),
        lifecycle,
    )
}

fn header_text(headers: &axum::http::HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .map(|value| value.to_str().expect("header is text").to_owned())
}

/// Every string under a `$ref` key, found by a walk of the whole document.
fn collect_refs(value: &Value, refs: &mut Vec<String>) {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                if key == "$ref" {
                    refs.push(child.as_str().expect("$ref is a string").to_owned());
                } else {
                    collect_refs(child, refs);
                }
            }
        }
        Value::Array(items) => items.iter().for_each(|item| collect_refs(item, refs)),
        _ => {}
    }
}

fn document() -> Value {
    body_json(
        &std::fs::read(concat!(env!("CARGO_MANIFEST_DIR"), "/openapi.json")).expect("file reads"),
    )
}

#[tokio::test]
async fn healthz_and_readyz_respond() {
    // Ready: the probes need no token, the data routes still do.
    let app = probes_app(LifecycleController::serving());
    let (status, _, _) = call(&app, "GET", "/healthz", None).await;
    assert_eq!(status, StatusCode::OK);
    let (status, _, _) = call(&app, "GET", "/readyz", None).await;
    assert_eq!(status, StatusCode::OK);
    let (status, _, _) = call(&app, "GET", "/v1/orders", None).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);

    // Not ready: 503 with a Retry-After of 5 seconds.
    let app = probes_app(LifecycleController::new());
    let (status, headers, _) = call(&app, "GET", "/readyz", None).await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(header_text(&headers, "retry-after").as_deref(), Some("5"));
}

#[tokio::test]
async fn openapi_json_is_served_and_equals_the_file() {
    let app = probes_app(LifecycleController::serving());
    let (status, headers, body) = call(&app, "GET", "/openapi.json", None).await;
    assert_eq!(status, StatusCode::OK);
    let file =
        std::fs::read(concat!(env!("CARGO_MANIFEST_DIR"), "/openapi.json")).expect("file reads");
    assert_eq!(body, file, "the served bytes equal the file");
    let content_type = header_text(&headers, "content-type").expect("content-type is set");
    assert!(
        content_type.starts_with("application/json"),
        "{content_type}"
    );

    let parsed = body_json(&body);
    let paths = parsed["paths"].as_object().expect("paths is an object");
    let mut keys: Vec<&str> = paths.keys().map(String::as_str).collect();
    keys.sort_unstable();
    assert_eq!(keys, vec!["/v1/orders", "/v1/orders/{id}"]);

    let collection = paths["/v1/orders"].as_object().expect("collection item");
    assert!(collection.contains_key("get") && collection.contains_key("post"));
    let item = paths["/v1/orders/{id}"].as_object().expect("item");
    assert!(item.contains_key("get") && item.contains_key("put") && item.contains_key("delete"));

    let mut ids: Vec<String> = Vec::new();
    for path in paths.values() {
        for (method, operation) in path.as_object().expect("path item") {
            if method == "parameters" {
                continue;
            }
            ids.push(
                operation["operationId"]
                    .as_str()
                    .expect("operationId")
                    .to_owned(),
            );
        }
    }
    ids.sort_unstable();
    assert_eq!(
        ids,
        vec![
            "createOrder",
            "deleteOrder",
            "getOrder",
            "listOrders",
            "replaceOrder"
        ]
    );
}

#[test]
fn openapi_refs_all_resolve() {
    let parsed = document();
    let mut refs = Vec::new();
    collect_refs(&parsed, &mut refs);
    assert!(refs.len() >= 8, "the walk found {} refs", refs.len());
    let schemas = parsed["components"]["schemas"]
        .as_object()
        .expect("components.schemas is an object");
    for reference in &refs {
        let name = reference
            .strip_prefix("#/components/schemas/")
            .unwrap_or_else(|| panic!("{reference} does not point to a schema"));
        assert!(schemas.contains_key(name), "{reference} does not resolve");
    }
}

#[tokio::test]
async fn probe_responses_carry_the_request_id() {
    let app = probes_app(LifecycleController::serving());
    let (status, headers, _) = send(
        &app,
        "GET",
        "/healthz",
        &[("x-request-id", "probe-1")],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        header_text(&headers, "x-request-id").as_deref(),
        Some("probe-1")
    );

    let (status, headers, _) = call(&app, "GET", "/healthz", None).await;
    assert_eq!(status, StatusCode::OK);
    let generated = header_text(&headers, "x-request-id").expect("a request id is set");
    assert!(!generated.is_empty());
}

#[test]
fn openapi_documents_the_error_body_and_pagination() {
    let parsed = document();
    let schemas = &parsed["components"]["schemas"];

    let error_required = schemas["ErrorBody"]["required"]
        .as_array()
        .expect("ErrorBody.required");
    assert!(error_required.iter().any(|name| name == "error"));
    assert!(error_required.iter().any(|name| name == "message"));

    let codes: Vec<&str> = schemas["ErrorBody"]["properties"]["error"]["enum"]
        .as_array()
        .expect("error enum")
        .iter()
        .map(|code| code.as_str().expect("code is text"))
        .collect();
    let all = [
        ErrorCode::InvalidRequest,
        ErrorCode::Unauthenticated,
        ErrorCode::Forbidden,
        ErrorCode::NotFound,
        ErrorCode::Conflict,
        ErrorCode::PreconditionFailed,
        ErrorCode::PayloadTooLarge,
        ErrorCode::ValidationFailed,
        ErrorCode::PreconditionRequired,
        ErrorCode::RateLimited,
        ErrorCode::Internal,
        ErrorCode::Unavailable,
    ];
    assert_eq!(codes.len(), 12);
    for code in &all {
        assert!(
            codes.contains(&code.as_str()),
            "{} is missing",
            code.as_str()
        );
    }

    let page_required = schemas["OrderPage"]["required"]
        .as_array()
        .expect("OrderPage.required");
    assert!(page_required.iter().any(|name| name == "items"));
    assert!(page_required.iter().any(|name| name == "next_cursor"));

    let parameters = parsed["paths"]["/v1/orders"]["get"]["parameters"]
        .as_array()
        .expect("list parameters");
    let limit = parameters
        .iter()
        .find(|parameter| parameter["name"] == "limit")
        .expect("limit parameter");
    assert_eq!(limit["schema"]["minimum"], 1);
    assert_eq!(limit["schema"]["maximum"], 200);
}
