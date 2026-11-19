use axum::Json;
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use serde_json::{Value, json};

use crate::application::store::StoreError;
use crate::domain::order::FieldError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
    InvalidRequest,
    Unauthenticated,
    Forbidden,
    NotFound,
    Conflict,
    PreconditionFailed,
    PayloadTooLarge,
    ValidationFailed,
    PreconditionRequired,
    RateLimited,
    Internal,
    Unavailable,
}

impl ErrorCode {
    pub fn as_str(&self) -> &'static str {
        match self {
            ErrorCode::InvalidRequest => "invalid_request",
            ErrorCode::Unauthenticated => "unauthenticated",
            ErrorCode::Forbidden => "forbidden",
            ErrorCode::NotFound => "not_found",
            ErrorCode::Conflict => "conflict",
            ErrorCode::PreconditionFailed => "precondition_failed",
            ErrorCode::PayloadTooLarge => "payload_too_large",
            ErrorCode::ValidationFailed => "validation_failed",
            ErrorCode::PreconditionRequired => "precondition_required",
            ErrorCode::RateLimited => "rate_limited",
            ErrorCode::Internal => "internal",
            ErrorCode::Unavailable => "unavailable",
        }
    }

    pub fn status(&self) -> StatusCode {
        match self {
            ErrorCode::InvalidRequest => StatusCode::BAD_REQUEST,
            ErrorCode::Unauthenticated => StatusCode::UNAUTHORIZED,
            ErrorCode::Forbidden => StatusCode::FORBIDDEN,
            ErrorCode::NotFound => StatusCode::NOT_FOUND,
            ErrorCode::Conflict => StatusCode::CONFLICT,
            ErrorCode::PreconditionFailed => StatusCode::PRECONDITION_FAILED,
            ErrorCode::PayloadTooLarge => StatusCode::PAYLOAD_TOO_LARGE,
            ErrorCode::ValidationFailed => StatusCode::UNPROCESSABLE_ENTITY,
            ErrorCode::PreconditionRequired => StatusCode::PRECONDITION_REQUIRED,
            ErrorCode::RateLimited => StatusCode::TOO_MANY_REQUESTS,
            ErrorCode::Internal => StatusCode::INTERNAL_SERVER_ERROR,
            ErrorCode::Unavailable => StatusCode::SERVICE_UNAVAILABLE,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ApiError {
    pub code: ErrorCode,
    pub message: String,
    pub details: Option<Vec<Value>>,
    pub retry_after_secs: Option<u64>,
}

impl ApiError {
    pub fn new(code: ErrorCode, message: impl Into<String>) -> ApiError {
        ApiError {
            code,
            message: message.into(),
            details: None,
            retry_after_secs: None,
        }
    }

    pub fn with_details(mut self, details: Vec<Value>) -> ApiError {
        self.details = Some(details);
        self
    }

    pub fn with_retry_after(mut self, secs: u64) -> ApiError {
        self.retry_after_secs = Some(secs);
        self
    }

    /// The detail goes to the log only; the body shows the request id.
    pub fn internal(detail: &str, request_id: &str) -> ApiError {
        tracing::error!(request_id, detail, "internal error");
        ApiError::new(ErrorCode::Internal, "Internal error.")
            .with_details(vec![json!({"request_id": request_id})])
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let status = self.code.status();
        let mut body = json!({"error": self.code.as_str(), "message": self.message});
        if let Some(details) = self.details {
            body["details"] = Value::Array(details);
        }
        let mut response = (status, Json(body)).into_response();
        if matches!(self.code, ErrorCode::RateLimited | ErrorCode::Unavailable) {
            let secs = self.retry_after_secs.unwrap_or(1);
            response
                .headers_mut()
                .insert(header::RETRY_AFTER, HeaderValue::from(secs));
        }
        response
    }
}

impl From<Vec<FieldError>> for ApiError {
    fn from(errors: Vec<FieldError>) -> ApiError {
        let details = errors
            .into_iter()
            .map(|e| json!({"field": e.field, "message": e.message}))
            .collect();
        ApiError::new(
            ErrorCode::ValidationFailed,
            "The request has invalid fields.",
        )
        .with_details(details)
    }
}

impl From<StoreError> for ApiError {
    fn from(error: StoreError) -> ApiError {
        match error {
            StoreError::NotFound => ApiError::new(ErrorCode::NotFound, "The order was not found."),
        }
    }
}
