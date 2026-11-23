use std::collections::HashMap;
use std::sync::Arc;

use axum::Router;
use axum::http::{Method, StatusCode};
use serde_json::json;
use service_observability::MetricsProvider;
use som_reference_order::application::idempotency::SystemClock;
use som_reference_order::infrastructure::memory_store::MemoryStore;
use som_reference_order::interfaces::auth::{AuthMode, StaticTokenVerifier};
use som_reference_order::interfaces::metrics::{Operation, OperationMetrics, operation_of};
use som_reference_order::interfaces::routes::{COLLECTION_PATH, ITEM_PATH, router_with_metrics};

use crate::common::{body_json, call, etag_of, open_verifier, send, valid_body};
use crate::fixture::Must;

fn metrics_app(metrics: Arc<OperationMetrics>, verifier: Arc<StaticTokenVerifier>) -> Router {
    router_with_metrics(
        Arc::new(MemoryStore::new()),
        Arc::new(SystemClock),
        verifier,
        metrics,
    )
}

fn check(
    metrics: &OperationMetrics,
    operation: Operation,
    requests: u64,
    errors: u64,
    samples: u64,
) {
    let stats = metrics.stats(operation);
    assert_eq!(
        stats.requests.get(),
        requests,
        "{} requests",
        operation.as_str()
    );
    assert_eq!(stats.errors.get(), errors, "{} errors", operation.as_str());
    assert_eq!(
        stats.latency.count().get(),
        samples,
        "{} latency samples",
        operation.as_str()
    );
}

#[test]
fn operation_of_maps_method_and_path() {
    assert_eq!(
        operation_of(&Method::POST, COLLECTION_PATH),
        Some(Operation::Create)
    );
    assert_eq!(
        operation_of(&Method::GET, COLLECTION_PATH),
        Some(Operation::List)
    );
    assert_eq!(operation_of(&Method::GET, ITEM_PATH), Some(Operation::Read));
    assert_eq!(
        operation_of(&Method::PUT, ITEM_PATH),
        Some(Operation::Replace)
    );
    assert_eq!(
        operation_of(&Method::DELETE, ITEM_PATH),
        Some(Operation::Delete)
    );
    assert_eq!(operation_of(&Method::PATCH, ITEM_PATH), None);
    assert_eq!(operation_of(&Method::GET, "/other"), None);
}

#[tokio::test]
async fn each_operation_has_count_latency_and_error_count() {
    let metrics = Arc::new(OperationMetrics::new());
    let app = metrics_app(metrics.clone(), open_verifier());

    // Create: one 201 and one 422.
    let (status, _, body) = call(&app, "POST", COLLECTION_PATH, Some(valid_body())).await;
    assert_eq!(status, StatusCode::CREATED);
    let id = body_json(&body)["id"]
        .as_str()
        .must("created order has an id")
        .to_owned();
    let (status, _, _) = call(&app, "POST", COLLECTION_PATH, Some(json!({}))).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    check(&metrics, Operation::Create, 2, 1, 2);
    check(&metrics, Operation::Read, 0, 0, 0);
    check(&metrics, Operation::List, 0, 0, 0);
    check(&metrics, Operation::Replace, 0, 0, 0);
    check(&metrics, Operation::Delete, 0, 0, 0);

    // Read: one 200 and one 404.
    let (status, _, _) = call(&app, "GET", &format!("/v1/orders/{id}"), None).await;
    assert_eq!(status, StatusCode::OK);
    let (status, _, _) = call(&app, "GET", "/v1/orders/nope", None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    check(&metrics, Operation::Read, 2, 1, 2);
    check(&metrics, Operation::Create, 2, 1, 2);

    // List: one 200 and one 400.
    let (status, _, _) = call(&app, "GET", COLLECTION_PATH, None).await;
    assert_eq!(status, StatusCode::OK);
    let (status, _, _) = call(&app, "GET", "/v1/orders?limit=0", None).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    check(&metrics, Operation::List, 2, 1, 2);

    // Replace: one 200 on the real id, one 404 on a missing id.
    // etag_of reads with GET, so Read counts it too.
    let etag = etag_of(&app, &id).await;
    let (status, _, _) = send(
        &app,
        "PUT",
        &format!("/v1/orders/{id}"),
        &[("if-match", etag.as_str())],
        Some(valid_body().to_string().into_bytes()),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let (status, _, _) = send(
        &app,
        "PUT",
        "/v1/orders/nope",
        &[],
        Some(valid_body().to_string().into_bytes()),
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    check(&metrics, Operation::Replace, 2, 1, 2);

    // Delete: one 404 on a missing id, one 204 on the real id.
    // The PUT changed the etag, so read it again.
    let etag = etag_of(&app, &id).await;
    let (status, _, _) = send(&app, "DELETE", "/v1/orders/nope", &[], None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (status, _, _) = send(
        &app,
        "DELETE",
        &format!("/v1/orders/{id}"),
        &[("if-match", etag.as_str())],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::NO_CONTENT);
    check(&metrics, Operation::Delete, 2, 1, 2);

    // Read now has 4 calls: two requests of the test, and two etag_of reads.
    check(&metrics, Operation::Read, 4, 1, 4);

    // An unknown path matches no operation and changes no count.
    let (status, _, _) = call(&app, "GET", "/v1/nothing", None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    check(&metrics, Operation::Create, 2, 1, 2);
    check(&metrics, Operation::Read, 4, 1, 4);
    check(&metrics, Operation::List, 2, 1, 2);
    check(&metrics, Operation::Replace, 2, 1, 2);
    check(&metrics, Operation::Delete, 2, 1, 2);

    // The rendered text has the names and labels.
    let text = metrics.render_metrics();
    assert!(text.contains(r#"order_operation_requests_total{operation="create"} 2"#));
    assert!(text.contains(r#"order_operation_errors_total{operation="create"} 1"#));
    assert!(text.contains("# TYPE order_operation_latency_microseconds_count counter"));
    assert!(text.contains(r#"order_operation_latency_microseconds_count{operation="create"} 2"#));
}

#[tokio::test]
async fn a_401_counts_as_an_error_call() {
    let metrics = Arc::new(OperationMetrics::new());
    let required = Arc::new(StaticTokenVerifier::new(AuthMode::Required, HashMap::new()));
    let app = metrics_app(metrics.clone(), required);

    let (status, _, _) = call(&app, "GET", COLLECTION_PATH, None).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    check(&metrics, Operation::List, 1, 1, 1);
}
