use std::sync::Arc;

use axum::Router;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use serde_json::{Value, json};
use som_reference_order::application::store::OrderStore;
use som_reference_order::domain::order::{Order, parse_input};
use som_reference_order::infrastructure::memory_store::MemoryStore;
use som_reference_order::interfaces::routes::router;
use tower::ServiceExt;

pub fn app() -> (Router, Arc<MemoryStore>) {
    let store = Arc::new(MemoryStore::new());
    (router(store.clone()), store)
}

pub async fn call(
    app: &Router,
    method: &str,
    uri: &str,
    body: Option<Value>,
) -> (StatusCode, HeaderMap, Vec<u8>) {
    call_raw(
        app,
        method,
        uri,
        body.map(|value| value.to_string().into_bytes()),
    )
    .await
}

pub async fn call_raw(
    app: &Router,
    method: &str,
    uri: &str,
    body: Option<Vec<u8>>,
) -> (StatusCode, HeaderMap, Vec<u8>) {
    let builder = Request::builder().method(method).uri(uri);
    let request = match body {
        Some(bytes) => builder
            .header("content-type", "application/json")
            .body(Body::from(bytes)),
        None => builder.body(Body::empty()),
    }
    .expect("request builds");
    let response = app.clone().oneshot(request).await.expect("router answers");
    let status = response.status();
    let headers = response.headers().clone();
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("body reads")
        .to_vec();
    (status, headers, bytes)
}

pub fn body_json(bytes: &[u8]) -> Value {
    serde_json::from_slice(bytes).expect("body is JSON")
}

pub fn valid_body() -> Value {
    json!({"customer_id": "c1", "total_cents": 100, "currency": "USD", "status": "pending"})
}

pub fn seed(store: &MemoryStore, id: &str, created_at: &str) {
    let input = parse_input(&valid_body()).expect("valid body parses");
    store
        .create(Order::create(input, id.to_owned(), created_at.to_owned()))
        .expect("seed stores the order");
}
