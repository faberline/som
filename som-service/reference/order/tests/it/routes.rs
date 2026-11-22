use axum::http::StatusCode;
use serde_json::json;
use som_reference_order::interfaces::routes::{BODY_LIMIT_BYTES, COLLECTION_PATH, ITEM_PATH};

use crate::common::{app, body_json, call, call_raw, etag_of, seed, send, valid_body};
use crate::fixture::{
    RESOURCE_KEYS, body_with_big_text, check_created, check_replaced, one_bad_field_body,
    replacement_body,
};

#[tokio::test]
async fn crud_paths_are_plural_kebab_lower() {
    assert_eq!(COLLECTION_PATH, "/v1/orders");
    assert_eq!(ITEM_PATH, "/v1/orders/{id}");

    let (app, _store) = app();
    let (status, _, body) = call(&app, "POST", COLLECTION_PATH, Some(valid_body())).await;
    assert_eq!(status, StatusCode::CREATED);
    let id = body_json(&body)["id"]
        .as_str()
        .expect("id is a string")
        .to_owned();

    let (status, _, _) = call(&app, "GET", &format!("/v1/orders/{id}"), None).await;
    assert_eq!(status, StatusCode::OK);

    let (status, _, _) = call(&app, "GET", "/v1/order", None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let (status, _, _) = call(&app, "GET", "/v1/Orders", None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn create_returns_201_location_and_full_resource() {
    let (app, _store) = app();
    let (status, headers, body) = call(&app, "POST", COLLECTION_PATH, Some(valid_body())).await;
    assert_eq!(status, StatusCode::CREATED);

    let value = body_json(&body);
    let id = value["id"].as_str().expect("id is a string").to_owned();
    let location = headers
        .get("location")
        .expect("Location header is set")
        .to_str()
        .expect("Location is text");
    assert_eq!(location, format!("/v1/orders/{id}"));

    let mut keys: Vec<&str> = value
        .as_object()
        .expect("body is an object")
        .keys()
        .map(String::as_str)
        .collect();
    keys.sort_unstable();
    let mut expected = RESOURCE_KEYS.to_vec();
    expected.sort_unstable();
    assert_eq!(keys, expected);
    check_created(&value);

    let (status, _, body) = call(&app, "POST", COLLECTION_PATH, Some(one_bad_field_body())).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body_json(&body)["error"], "validation_failed");

    let (status, _, body) =
        call_raw(&app, "POST", COLLECTION_PATH, Some(b"not json".to_vec())).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body_json(&body)["error"], "invalid_request");
}

#[tokio::test]
async fn read_returns_200_and_unknown_id_returns_404() {
    let (app, store) = app();
    seed(&store, "a", "2026-01-01T00:00:00Z");

    let (status, _, body) = call(&app, "GET", "/v1/orders/a", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body_json(&body)["id"], "a");

    let (status, _, body) = call(&app, "GET", "/v1/orders/zzz", None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body_json(&body)["error"], "not_found");
}

#[tokio::test]
async fn replace_returns_200_with_full_resource_and_no_patch_route() {
    let (app, store) = app();
    seed(&store, "a", "2026-01-01T00:00:00Z");

    let replacement = replacement_body();
    let etag = etag_of(&app, "a").await;
    let (status, _, body) = send(
        &app,
        "PUT",
        "/v1/orders/a",
        &[("if-match", &etag)],
        Some(replacement.to_string().into_bytes()),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let value = body_json(&body);
    check_replaced(&value);
    assert_eq!(value["id"], "a");
    assert_eq!(value["created_at"], "2026-01-01T00:00:00Z");
    assert_ne!(value["updated_at"], "2026-01-01T00:00:00Z");

    let (status, _, get_body) = call(&app, "GET", "/v1/orders/a", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body_json(&get_body), value);

    let (status, _, _) = call(&app, "PUT", "/v1/orders/zzz", Some(valid_body())).await;
    assert_eq!(status, StatusCode::NOT_FOUND);

    let (status, _, _) = call(&app, "PUT", "/v1/orders/a", Some(json!({}))).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);

    let (status, _, _) = call(&app, "PATCH", "/v1/orders/a", Some(valid_body())).await;
    assert_eq!(status, StatusCode::METHOD_NOT_ALLOWED);
}

#[tokio::test]
async fn delete_returns_204_and_second_delete_returns_404() {
    let (app, store) = app();
    seed(&store, "a", "2026-01-01T00:00:00Z");

    let etag = etag_of(&app, "a").await;
    let (status, _, body) =
        send(&app, "DELETE", "/v1/orders/a", &[("if-match", &etag)], None).await;
    assert_eq!(status, StatusCode::NO_CONTENT);
    assert!(body.is_empty());

    let (status, _, _) = call(&app, "GET", "/v1/orders/a", None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);

    let (status, _, _) = call(&app, "DELETE", "/v1/orders/a", None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn body_over_1_mib_returns_413() {
    let (app, store) = app();
    seed(&store, "a", "2026-01-01T00:00:00Z");

    let big = body_with_big_text(BODY_LIMIT_BYTES);

    let (status, _, body) = call(&app, "POST", COLLECTION_PATH, Some(big.clone())).await;
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
    assert_eq!(body_json(&body)["error"], "payload_too_large");

    let (status, _, body) = call(&app, "PUT", "/v1/orders/a", Some(big)).await;
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
    assert_eq!(body_json(&body)["error"], "payload_too_large");

    let small_but_long = body_with_big_text(3000);
    let (status, _, _) = call(&app, "POST", COLLECTION_PATH, Some(small_but_long)).await;
    assert_ne!(status, StatusCode::PAYLOAD_TOO_LARGE);
}
