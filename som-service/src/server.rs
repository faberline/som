use crate::Error;
use axum::{
    Json, Router,
    body::Bytes,
    extract::{DefaultBodyLimit, rejection::BytesRejection},
    http::StatusCode,
    routing::{get, post},
};
use serde_json::{Value, json};
use server_lifecycle::DrainController;
use service_http::{ErrorEnvelope, standard_probe_routes_canonical_json};
use std::sync::Arc;

type Reply = Result<Json<Value>, (StatusCode, Json<Value>)>;
fn rejected(error: Error) -> (StatusCode, Json<Value>) {
    let envelope = ErrorEnvelope {
        error: error.body["code"]
            .as_str()
            .unwrap_or("invalid_request")
            .to_ascii_lowercase(),
        message: error.body["detail"]
            .as_str()
            .unwrap_or("The request was rejected.")
            .to_owned(),
    };
    let mut body = error.body;
    body["error"] = Value::String(envelope.error);
    body["message"] = Value::String(envelope.message);
    (
        StatusCode::from_u16(error.status).unwrap_or(StatusCode::BAD_REQUEST),
        Json(body),
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
pub fn openapi_json() -> String {
    json!({"openapi":"3.0.3","info":{"title":"som-service","version":env!("CARGO_PKG_VERSION")},
        "paths":{"/health":{"get":{"responses":{"200":{"description":"Compiler descriptor."}}}},
            "/compile":{"post":{"responses":{"200":{"description":"Compiled bundle."}}}},
            "/assemble":{"post":{"responses":{"200":{"description":"Rendered files."}}}}}})
    .to_string()
}
pub fn router() -> Router {
    standard_probe_routes_canonical_json(Arc::new(DrainController::new()), None, openapi_json)
        .merge(data_routes())
}
fn data_routes() -> Router {
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
