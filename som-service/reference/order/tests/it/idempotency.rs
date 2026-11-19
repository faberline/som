use std::sync::Arc;
use std::sync::atomic::{AtomicI64, Ordering};

use axum::Router;
use axum::http::{HeaderMap, StatusCode};
use serde_json::{Value, json};
use som_reference_order::application::idempotency::KEY_TTL_SECS;

use crate::common::{
    TestClock, app, app_with_clock, body_json, call, etag_of, seed, send, valid_body,
};

fn with_total(total: i64) -> Value {
    let mut body = valid_body();
    body["total_cents"] = json!(total);
    body
}

fn with_note(note: &str) -> Value {
    let mut body = valid_body();
    body["note"] = json!(note);
    body
}

async fn post_keyed(app: &Router, key: &str, body: &Value) -> (StatusCode, HeaderMap, Vec<u8>) {
    send(
        app,
        "POST",
        "/v1/orders",
        &[("idempotency-key", key)],
        Some(body.to_string().into_bytes()),
    )
    .await
}

async fn count(app: &Router) -> usize {
    let (status, _, body) = call(app, "GET", "/v1/orders", None).await;
    assert_eq!(status, StatusCode::OK);
    body_json(&body)["items"]
        .as_array()
        .expect("items is a list")
        .len()
}

#[tokio::test]
async fn idempotency_key_same_body_same_result_different_body_409_and_expires_after_24h() {
    let clock = Arc::new(TestClock(AtomicI64::new(1_000)));
    let (app, _) = app_with_clock(clock.clone());
    let first = valid_body();

    let (status, headers, body) = post_keyed(&app, "k1", &first).await;
    assert_eq!(status, StatusCode::CREATED);
    let id1 = body_json(&body)["id"]
        .as_str()
        .expect("id is text")
        .to_owned();
    let etag1 = headers["etag"].to_str().expect("etag is text").to_owned();
    let location1 = headers["location"]
        .to_str()
        .expect("location is text")
        .to_owned();

    let (status, headers, body) = post_keyed(&app, "k1", &first).await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body_json(&body)["id"], id1.as_str());
    assert_eq!(
        headers["etag"].to_str().expect("etag is text"),
        etag1.as_str()
    );
    assert_eq!(
        headers["location"].to_str().expect("location is text"),
        location1.as_str()
    );
    assert_eq!(count(&app).await, 1);

    let reordered = r#"{"status":"pending","currency":"USD","total_cents":100,"customer_id":"c1"}"#;
    let (status, _, body) = send(
        &app,
        "POST",
        "/v1/orders",
        &[("idempotency-key", "k1")],
        Some(reordered.as_bytes().to_vec()),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body_json(&body)["id"], id1.as_str());

    let (status, _, body) = post_keyed(&app, "k1", &with_total(999)).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body_json(&body)["error"], "conflict");
    assert_eq!(count(&app).await, 1);

    let (status, _, body) = post_keyed(&app, "k2", &first).await;
    assert_eq!(status, StatusCode::CREATED);
    assert_ne!(body_json(&body)["id"], id1.as_str());
    assert_eq!(count(&app).await, 2);

    clock.0.store(1_000 + KEY_TTL_SECS - 1, Ordering::SeqCst);
    let (status, _, body) = post_keyed(&app, "k1", &first).await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body_json(&body)["id"], id1.as_str());
    assert_eq!(count(&app).await, 2);

    clock.0.store(1_000 + KEY_TTL_SECS, Ordering::SeqCst);
    let (status, _, body) = post_keyed(&app, "k1", &first).await;
    assert_eq!(status, StatusCode::CREATED);
    assert_ne!(body_json(&body)["id"], id1.as_str());
    assert_eq!(count(&app).await, 3);

    let (status, _, body) = post_keyed(&app, "k1", &with_total(999)).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body_json(&body)["error"], "conflict");
}

#[tokio::test]
async fn post_without_key_creates_each_time_and_real_clock_replays() {
    let (app, _) = app();

    let (_, _, first) = call(&app, "POST", "/v1/orders", Some(valid_body())).await;
    let (_, _, second) = call(&app, "POST", "/v1/orders", Some(valid_body())).await;
    assert_ne!(body_json(&first)["id"], body_json(&second)["id"]);

    let (status, _, one) = post_keyed(&app, "real", &valid_body()).await;
    assert_eq!(status, StatusCode::CREATED);
    let (status, _, two) = post_keyed(&app, "real", &valid_body()).await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body_json(&one)["id"], body_json(&two)["id"]);
}

#[tokio::test]
async fn empty_idempotency_key_returns_400() {
    let (app, _) = app();

    let (status, _, body) = post_keyed(&app, "", &valid_body()).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body_json(&body)["error"], "invalid_request");

    let (status, _, _) = post_keyed(&app, "k3", &json!({})).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);

    let (status, _, _) = post_keyed(&app, "k3", &valid_body()).await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(count(&app).await, 1);
}

#[tokio::test]
async fn idempotency_key_is_not_used_on_put() {
    let (app, store) = app();
    seed(&store, "a", "2026-01-01T00:00:00Z");

    let etag = etag_of(&app, "a").await;
    let (status, _, _) = send(
        &app,
        "PUT",
        "/v1/orders/a",
        &[("if-match", &etag), ("idempotency-key", "k")],
        Some(with_note("one").to_string().into_bytes()),
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    let etag = etag_of(&app, "a").await;
    let (status, _, body) = send(
        &app,
        "PUT",
        "/v1/orders/a",
        &[("if-match", &etag), ("idempotency-key", "k")],
        Some(with_note("two").to_string().into_bytes()),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body_json(&body)["note"], "two");
}
