use axum::extract::Request;
use axum::http::{HeaderMap, HeaderValue};
use axum::middleware::Next;
use axum::response::Response;
use service_observability::MAX_REQUEST_ID_BYTES;
use tracing::Instrument;

pub const HEADER: &str = "x-request-id";

/// The request id of one request, as the handlers see it in the request extensions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestId(pub String);

/// The id to use: the header `x-request-id` when it is non-empty text of at most
/// `MAX_REQUEST_ID_BYTES` bytes with no control character; otherwise a new UUIDv7.
pub fn resolve(headers: &HeaderMap) -> String {
    headers
        .get(HEADER)
        .and_then(|value| value.to_str().ok())
        .filter(|text| {
            !text.is_empty()
                && text.len() <= MAX_REQUEST_ID_BYTES
                && !text.chars().any(char::is_control)
        })
        .map(str::to_owned)
        .unwrap_or_else(|| uuid::Uuid::now_v7().to_string())
}

// core-gap: 27 (core has no middleware that reads or makes x-request-id, returns it and puts it in the log span)
pub async fn request_id_layer(mut request: Request, next: Next) -> Response {
    let id = resolve(request.headers());
    request.extensions_mut().insert(RequestId(id.clone()));
    let span = tracing::info_span!(
        "request",
        request_id = %id,
        method = %request.method(),
        path = %request.uri().path(),
    );
    let response = next.run(request).instrument(span.clone()).await;
    let status = response.status().as_u16();
    span.in_scope(|| tracing::info!(status, "request done"));
    let mut response = response;
    if let Ok(value) = HeaderValue::from_str(&id) {
        response.headers_mut().insert(HEADER, value);
    }
    response
}
