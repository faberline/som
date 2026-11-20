use axum::Router;
use axum::extract::Request;
use axum::http::{HeaderValue, StatusCode, header};
use axum::middleware::Next;
use axum::response::Response;
use server_lifecycle::LifecycleController;

/// Decision 32: the bytes that `/openapi.json` serves. The client comes from core openapi-codegen (decision 32); this crate does not run it.
pub const OPENAPI_JSON: &str = include_str!("../../openapi.json");

pub fn openapi_json() -> String {
    OPENAPI_JSON.to_owned()
}

/// Core answers 503 on `/readyz` with no Retry-After header; decision 29 needs it.
async fn readyz_retry_after(request: Request, next: Next) -> Response {
    let is_readyz = request.uri().path() == "/readyz";
    let mut response = next.run(request).await;
    // core-gap: 29 (core readyz answers 503 with no Retry-After; the standard needs it)
    if is_readyz && response.status() == StatusCode::SERVICE_UNAVAILABLE {
        response
            .headers_mut()
            .insert(header::RETRY_AFTER, HeaderValue::from(5u64));
    }
    response
}

/// `/healthz`, `/readyz`, `/metrics` and `/openapi.json` (decision 29 and 32). They need no token.
pub fn probe_router(lifecycle: LifecycleController) -> Router {
    service_http::lifecycle_probe_routes_canonical_json(lifecycle, None, openapi_json)
        .layer(axum::middleware::from_fn(readyz_retry_after))
}
