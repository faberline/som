use axum::http::StatusCode;
use serde_json::json;

use crate::common::{app, body_json, call, etag_of, seed, send, valid_body};
use crate::fixture::{text_of, with_text};

#[tokio::test]
async fn etag_is_returned_and_if_match_mismatch_412_missing_428() {
    let (app, store) = app();
    seed(&store, "a", "2026-01-01T00:00:00Z");

    let (status, headers, _) = call(&app, "GET", "/v1/orders/a", None).await;
    assert_eq!(status, StatusCode::OK);
    let old = headers["etag"].to_str().expect("etag is text").to_owned();
    assert!(old.starts_with('"') && old.ends_with('"'));
    assert_eq!(old.len(), 18);
    assert_eq!(etag_of(&app, "a").await, old);

    let (status, headers, body) = call(&app, "POST", "/v1/orders", Some(valid_body())).await;
    assert_eq!(status, StatusCode::CREATED);
    let new_id = body_json(&body)["id"]
        .as_str()
        .expect("id is text")
        .to_owned();
    let created_etag = headers["etag"].to_str().expect("etag is text").to_owned();
    assert_eq!(etag_of(&app, &new_id).await, created_etag);

    let (status, _, body) = call(&app, "PUT", "/v1/orders/a", Some(valid_body())).await;
    assert_eq!(status, StatusCode::PRECONDITION_REQUIRED);
    assert_eq!(body_json(&body)["error"], "precondition_required");

    let (status, _, body) = send(
        &app,
        "PUT",
        "/v1/orders/a",
        &[("if-match", "\"bad\"")],
        Some(valid_body().to_string().into_bytes()),
    )
    .await;
    assert_eq!(status, StatusCode::PRECONDITION_FAILED);
    assert_eq!(body_json(&body)["error"], "precondition_failed");

    let (status, headers, _) = send(
        &app,
        "PUT",
        "/v1/orders/a",
        &[("if-match", &old)],
        Some(with_text("x").to_string().into_bytes()),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let new = headers["etag"].to_str().expect("etag is text").to_owned();
    assert_ne!(new, old);
    assert_eq!(etag_of(&app, "a").await, new);

    let (status, _, _) = send(
        &app,
        "PUT",
        "/v1/orders/a",
        &[("if-match", &old)],
        Some(with_text("y").to_string().into_bytes()),
    )
    .await;
    assert_eq!(status, StatusCode::PRECONDITION_FAILED);
    let (_, _, body) = call(&app, "GET", "/v1/orders/a", None).await;
    assert_eq!(text_of(&body_json(&body)), "x");

    let (status, _, body) = call(&app, "DELETE", "/v1/orders/a", None).await;
    assert_eq!(status, StatusCode::PRECONDITION_REQUIRED);
    assert_eq!(body_json(&body)["error"], "precondition_required");
    let (status, _, _) = call(&app, "GET", "/v1/orders/a", None).await;
    assert_eq!(status, StatusCode::OK);

    let (status, _, body) = send(
        &app,
        "DELETE",
        "/v1/orders/a",
        &[("if-match", "\"bad\"")],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::PRECONDITION_FAILED);
    assert_eq!(body_json(&body)["error"], "precondition_failed");
    let (status, _, _) = call(&app, "GET", "/v1/orders/a", None).await;
    assert_eq!(status, StatusCode::OK);

    let (status, _, body) = send(&app, "DELETE", "/v1/orders/a", &[("if-match", &new)], None).await;
    assert_eq!(status, StatusCode::NO_CONTENT);
    assert!(body.is_empty());
    let (status, _, _) = call(&app, "GET", "/v1/orders/a", None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);

    let (status, _, _) = call(&app, "PUT", "/v1/orders/zzz", Some(valid_body())).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (status, _, _) = call(&app, "DELETE", "/v1/orders/zzz", None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn if_match_is_checked_after_the_body_is_validated() {
    let (app, store) = app();
    seed(&store, "a", "2026-01-01T00:00:00Z");

    let (status, _, _) = send(
        &app,
        "PUT",
        "/v1/orders/a",
        &[("if-match", "\"bad\"")],
        Some(json!({}).to_string().into_bytes()),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
}
