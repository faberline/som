use crate::Error;
use axum::{
    Json, Router,
    body::Bytes,
    extract::{DefaultBodyLimit, rejection::BytesRejection},
    http::StatusCode,
    routing::{get, post},
};
use serde_json::Value;

type Reply = Result<Json<Value>, (StatusCode, Json<Value>)>;
fn rejected(error: Error) -> (StatusCode, Json<Value>) {
    (
        StatusCode::from_u16(error.status).unwrap_or(StatusCode::BAD_REQUEST),
        Json(error.body),
    )
}
fn body(payload: Result<Bytes, BytesRejection>) -> Result<String, (StatusCode, Json<Value>)> {
    let data = payload.map_err(|e| {
        rejected(Error::new(
            if e.status() == StatusCode::PAYLOAD_TOO_LARGE {
                "REQUEST_TOO_LARGE"
            } else {
                "INVALID_JSON"
            },
            "The request body could not be read.",
            "",
        ))
    })?;
    String::from_utf8(data.to_vec())
        .map_err(|_| rejected(Error::new("INVALID_JSON", "Expected UTF-8 JSON.", "")))
}
async fn compile(payload: Result<Bytes, BytesRejection>) -> Reply {
    crate::compile_json(&body(payload)?)
        .map(Json)
        .map_err(rejected)
}
async fn assemble(payload: Result<Bytes, BytesRejection>) -> Reply {
    crate::assemble_json(&body(payload)?)
        .map(Json)
        .map_err(rejected)
}
pub fn router() -> Router {
    Router::new()
        .route("/health", get(|| async { Json(crate::health()) }))
        .route(
            "/compile",
            post(compile).layer(DefaultBodyLimit::max(crate::COMPILE_BODY_LIMIT)),
        )
        .route(
            "/assemble",
            post(assemble).layer(DefaultBodyLimit::max(crate::ASSEMBLE_BODY_LIMIT)),
        )
}
