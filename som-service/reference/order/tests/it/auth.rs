use std::collections::HashMap;
use std::sync::Arc;

use axum::Router;
use axum::http::StatusCode;
use som_reference_order::infrastructure::memory_store::MemoryStore;
use som_reference_order::interfaces::auth::{AuthMode, StaticTokenVerifier};
use som_reference_order::interfaces::routes::router;

use crate::common::{body_json, send};
use crate::fixture::Must;

fn verifier(mode: &str) -> Arc<StaticTokenVerifier> {
    let mut tokens = HashMap::new();
    tokens.insert("good-token".to_owned(), "alice".to_owned());
    Arc::new(StaticTokenVerifier::new(
        AuthMode::parse(mode).must("mode parses"),
        tokens,
    ))
}

fn app_with(verifier: Arc<StaticTokenVerifier>) -> Router {
    router(Arc::new(MemoryStore::new()), verifier)
}

#[tokio::test]
async fn auth_required_by_default_and_open_mode_needs_configuration() {
    let app = app_with(verifier(""));

    let (status, _, body) = send(&app, "GET", "/v1/orders", &[], None).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body_json(&body)["error"], "unauthenticated");

    let (status, _, _) = send(
        &app,
        "GET",
        "/v1/orders",
        &[("authorization", "Bearer wrong")],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);

    let (status, _, _) = send(
        &app,
        "GET",
        "/v1/orders",
        &[("authorization", "Bearer good-token")],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    assert_eq!(AuthMode::parse(""), Ok(AuthMode::Required));
    assert_eq!(AuthMode::parse("  Required "), Ok(AuthMode::Required));
    assert_eq!(AuthMode::parse("off"), Ok(AuthMode::Off));
    assert!(AuthMode::parse("open").is_err());

    let open = app_with(verifier("off"));
    let (status, _, _) = send(&open, "GET", "/v1/orders", &[], None).await;
    assert_eq!(status, StatusCode::OK);

    for (method, uri) in [
        ("POST", "/v1/orders"),
        ("GET", "/v1/orders/x"),
        ("GET", "/v1/orders"),
        ("PUT", "/v1/orders/x"),
        ("DELETE", "/v1/orders/x"),
    ] {
        let (status, _, _) = send(&app, method, uri, &[], None).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED, "{method} {uri}");
    }
}

#[tokio::test]
async fn unavailable_auth_backend_gives_503_unavailable_with_retry_after() {
    let app = app_with(Arc::new(StaticTokenVerifier::unavailable()));

    let (status, headers, body) = send(
        &app,
        "GET",
        "/v1/orders",
        &[("authorization", "Bearer good-token")],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body_json(&body)["error"], "unavailable");
    assert!(headers.get("retry-after").is_some());
}

#[tokio::test]
async fn unknown_path_is_404_in_required_mode() {
    let app = app_with(verifier(""));
    let (status, _, _) = send(&app, "GET", "/v1/nothing", &[], None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}
