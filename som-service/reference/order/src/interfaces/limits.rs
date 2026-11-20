use std::time::Duration;

use axum::extract::{Request, State};
use axum::http::{Method, header};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use service_http::{
    AdmissionConfigError, AdmissionController, AdmissionInput, AdmissionMiddleware,
};

use crate::interfaces::error::{ApiError, ErrorCode};

// core-gap: 30 (core has no 30 second request timeout layer)
/// Decision 30: the request timeout of the data routes.
pub const REQUEST_TIMEOUT_SECS: u64 = 30;
/// Decision 30: the grace of a graceful shutdown.
pub const SHUTDOWN_GRACE_SECS: u64 = 10;
// core-gap: 31 (core AdmissionConfig is off when no capacity is set; these are the standard's defaults)
/// Decision 31: the read capacity per key per window.
pub const DEFAULT_READ_CAPACITY: u32 = 600;
/// Decision 31: the write capacity per key per window.
pub const DEFAULT_WRITE_CAPACITY: u32 = 120;
/// Decision 31: the length of the window, in seconds.
pub const DEFAULT_REFILL_SECS: u64 = 60;
/// Decision 31: the number of keys that the controller keeps.
pub const DEFAULT_MAX_KEYS: usize = 1024;
/// Decision 31: the prefix of the `<SVC>_ADMISSION_*` environment names.
pub const ENV_PREFIX: &str = "ORDER";

/// The limits of the data routes: the request timeout and the optional admission controller.
#[derive(Clone)]
pub struct Limits {
    pub timeout: Duration,
    pub admission: Option<AdmissionController>,
}

impl Limits {
    /// The standard's limits, with no environment.
    pub fn standard() -> Limits {
        Self::from_lookup(|_| None)
            .unwrap_or_else(|error| panic!("the default limits are valid: {error}"))
    }

    /// The limits from an injected lookup. An unset name takes the standard's default.
    pub fn from_lookup<F>(lookup: F) -> Result<Limits, AdmissionConfigError>
    where
        F: Fn(&str) -> Option<String>,
    {
        let config = service_http::AdmissionConfig::from_lookup(ENV_PREFIX, |key| {
            lookup(key).or_else(|| default_for(key))
        })?;
        Ok(Limits {
            timeout: Duration::from_secs(REQUEST_TIMEOUT_SECS),
            admission: config.controller("read", "write", "admin"),
        })
    }
}

/// The standard's default for each admission name; the admin class has none, so it stays off.
fn default_for(key: &str) -> Option<String> {
    let read = format!("{ENV_PREFIX}_ADMISSION_READ_CAPACITY");
    let write = format!("{ENV_PREFIX}_ADMISSION_WRITE_CAPACITY");
    let refill = format!("{ENV_PREFIX}_ADMISSION_REFILL_SECS");
    let max_keys = format!("{ENV_PREFIX}_ADMISSION_MAX_KEYS");
    if key == read {
        Some(DEFAULT_READ_CAPACITY.to_string())
    } else if key == write {
        Some(DEFAULT_WRITE_CAPACITY.to_string())
    } else if key == refill {
        Some(DEFAULT_REFILL_SECS.to_string())
    } else if key == max_keys {
        Some(DEFAULT_MAX_KEYS.to_string())
    } else {
        None
    }
}

/// Decision 30: a request that takes longer than `limit` answers 503 unavailable with Retry-After.
pub async fn request_timeout(
    State(limit): State<Duration>,
    request: Request,
    next: Next,
) -> Response {
    match tokio::time::timeout(limit, next.run(request)).await {
        Ok(response) => response,
        Err(_) => ApiError::new(ErrorCode::Unavailable, "The request took too long.")
            .with_retry_after(1)
            .into_response(),
    }
}

/// Decision 31: the admission middleware of the data routes. Reads and writes are separate classes.
pub fn admission_layer(controller: AdmissionController) -> AdmissionMiddleware {
    AdmissionMiddleware::new(controller, classify)
}

fn classify(request: &Request) -> Option<AdmissionInput> {
    let class = match *request.method() {
        Method::GET | Method::HEAD => "read",
        _ => "write",
    };
    let key = request
        .headers()
        .get(header::AUTHORIZATION)
        .map(|value| value.as_bytes())
        .unwrap_or(&[]);
    Some(AdmissionInput::new(class, key))
}
