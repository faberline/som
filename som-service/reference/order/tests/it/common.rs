use std::sync::Arc;
use std::sync::atomic::{AtomicI64, Ordering};

use axum::Router;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use serde_json::Value;
use som_reference_order::application::idempotency::Clock;
use som_reference_order::application::store::OrderStore;
use som_reference_order::domain::order::{Order, parse_input};
use som_reference_order::infrastructure::memory_store::MemoryStore;
use som_reference_order::interfaces::auth::{AuthMode, StaticTokenVerifier};
use som_reference_order::interfaces::routes::{router, router_with_clock};
use tower::ServiceExt;

/// Open mode, set explicitly: the existing tests do not send a token.
pub fn open_verifier() -> Arc<StaticTokenVerifier> {
    Arc::new(StaticTokenVerifier::new(
        AuthMode::Off,
        std::collections::HashMap::new(),
    ))
}

pub fn app() -> (Router, Arc<MemoryStore>) {
    let store = Arc::new(MemoryStore::new());
    (router(store.clone(), open_verifier()), store)
}

/// A clock that the test moves by hand.
pub struct TestClock(pub AtomicI64);

impl Clock for TestClock {
    fn now_secs(&self) -> i64 {
        self.0.load(Ordering::SeqCst)
    }
}

pub fn app_with_clock(clock: Arc<TestClock>) -> (Router, Arc<MemoryStore>) {
    let store = Arc::new(MemoryStore::new());
    (
        router_with_clock(store.clone(), clock, open_verifier()),
        store,
    )
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
    send(app, method, uri, &[], body).await
}

pub async fn send(
    app: &Router,
    method: &str,
    uri: &str,
    headers: &[(&str, &str)],
    body: Option<Vec<u8>>,
) -> (StatusCode, HeaderMap, Vec<u8>) {
    let mut builder = Request::builder().method(method).uri(uri);
    for (name, value) in headers {
        builder = builder.header(*name, *value);
    }
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

/// The ETag of an order, read with a GET.
pub async fn etag_of(app: &Router, id: &str) -> String {
    let (status, headers, _) = call(app, "GET", &format!("/v1/orders/{id}"), None).await;
    assert_eq!(status, StatusCode::OK);
    headers
        .get("etag")
        .expect("GET carries an etag")
        .to_str()
        .expect("etag is text")
        .to_owned()
}

pub fn body_json(bytes: &[u8]) -> Value {
    serde_json::from_slice(bytes).expect("body is JSON")
}

pub use crate::fixture::valid_body;

pub fn seed(store: &MemoryStore, id: &str, created_at: &str) {
    let input = parse_input(&valid_body()).expect("valid body parses");
    store
        .create(Order::create(input, id.to_owned(), created_at.to_owned()))
        .expect("seed stores the order");
}
