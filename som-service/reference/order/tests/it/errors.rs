use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use serde_json::{Value, json};
use som_reference_order::interfaces::error::{ApiError, ErrorCode};

use crate::fixture::Must;
use crate::fixture::validation_field_errors;

async fn read(response: Response) -> (StatusCode, HeaderMap, Value) {
    let status = response.status();
    let headers = response.headers().clone();
    let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .must("body reads");
    let text = String::from_utf8(bytes.to_vec()).must("body is UTF-8");
    (
        status,
        headers,
        serde_json::from_str(&text).must("body is JSON"),
    )
}

#[tokio::test]
async fn error_body_has_error_message_and_optional_details() {
    let (status, headers, body) =
        read(ApiError::new(ErrorCode::NotFound, "m").into_response()).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(
        headers["content-type"]
            .to_str()
            .must("content type")
            .starts_with("application/json")
    );
    assert_eq!(body, json!({"error": "not_found", "message": "m"}));

    let with = ApiError::new(ErrorCode::NotFound, "m").with_details(vec![json!({"a": 1})]);
    let (_, _, body) = read(with.into_response()).await;
    assert_eq!(body["details"], json!([{"a": 1}]));

    let validation = ApiError::from(validation_field_errors());
    let (status, _, body) = read(validation.into_response()).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"], "validation_failed");
    assert_eq!(body["details"].as_array().map(Vec::len), Some(2));
}

#[tokio::test]
async fn each_error_code_maps_to_its_status() {
    let table = [
        (ErrorCode::InvalidRequest, "invalid_request", 400),
        (ErrorCode::Unauthenticated, "unauthenticated", 401),
        (ErrorCode::Forbidden, "forbidden", 403),
        (ErrorCode::NotFound, "not_found", 404),
        (ErrorCode::Conflict, "conflict", 409),
        (ErrorCode::PreconditionFailed, "precondition_failed", 412),
        (ErrorCode::PayloadTooLarge, "payload_too_large", 413),
        (ErrorCode::ValidationFailed, "validation_failed", 422),
        (
            ErrorCode::PreconditionRequired,
            "precondition_required",
            428,
        ),
        (ErrorCode::RateLimited, "rate_limited", 429),
        (ErrorCode::Internal, "internal", 500),
        (ErrorCode::Unavailable, "unavailable", 503),
    ];
    for (code, text, status) in table {
        assert_eq!(code.as_str(), text);
        assert_eq!(code.status().as_u16(), status);
        let (got, _, _) = read(ApiError::new(code, "m").into_response()).await;
        assert_eq!(got.as_u16(), status, "{text}");
    }
}

#[tokio::test]
async fn internal_error_hides_text_and_shows_request_id() {
    let error = ApiError::internal("db password is hunter2", "req-1");
    let (status, _, body) = read(error.into_response()).await;
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert!(!body.to_string().contains("hunter2"));
    assert_eq!(body["error"], "internal");
    assert_eq!(body["details"][0]["request_id"], "req-1");
}

#[tokio::test]
async fn rate_limited_and_unavailable_have_retry_after() {
    let (_, headers, _) = read(ApiError::new(ErrorCode::RateLimited, "slow").into_response()).await;
    assert_eq!(headers["retry-after"], "1");

    let (_, headers, _) = read(
        ApiError::new(ErrorCode::Unavailable, "later")
            .with_retry_after(7)
            .into_response(),
    )
    .await;
    assert_eq!(headers["retry-after"], "7");

    let (_, headers, _) = read(ApiError::new(ErrorCode::NotFound, "m").into_response()).await;
    assert!(headers.get("retry-after").is_none());
}

#[test]
fn store_not_found_becomes_not_found_error() {
    let error = ApiError::from(som_reference_order::application::store::StoreError::NotFound);
    assert_eq!(
        error,
        ApiError::new(ErrorCode::NotFound, "The order was not found.")
    );
}
