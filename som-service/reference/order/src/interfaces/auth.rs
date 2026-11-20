use std::collections::HashMap;
use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::HeaderMap;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use service_auth::{AuthError, Verifier, bearer_token};

use crate::interfaces::error::{ApiError, ErrorCode};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthMode {
    Required,
    Off,
}

impl AuthMode {
    pub fn parse(text: &str) -> Result<AuthMode, String> {
        match text.trim().to_ascii_lowercase().as_str() {
            // core-gap: 25 (core AuthConfig::resolve treats an empty mode as off)
            "" | "required" => Ok(AuthMode::Required),
            "off" => Ok(AuthMode::Off),
            other => Err(format!(
                "auth mode must be `off` or `required`; got `{other}`"
            )),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Caller {
    pub subject: String,
}

pub struct StaticTokenVerifier {
    mode: AuthMode,
    /// token -> subject
    tokens: HashMap<String, String>,
    available: bool,
}

impl StaticTokenVerifier {
    pub fn new(mode: AuthMode, tokens: HashMap<String, String>) -> StaticTokenVerifier {
        StaticTokenVerifier {
            mode,
            tokens,
            available: true,
        }
    }

    /// Stands for a failed auth backend: every request gets `Unavailable`.
    pub fn unavailable() -> StaticTokenVerifier {
        StaticTokenVerifier {
            mode: AuthMode::Required,
            tokens: HashMap::new(),
            available: false,
        }
    }
}

impl Verifier for StaticTokenVerifier {
    type Principal = Caller;

    fn authenticate(&self, headers: &HeaderMap) -> Result<Caller, AuthError> {
        if !self.available {
            return Err(AuthError::Unavailable(
                "The auth backend does not answer.".into(),
            ));
        }
        if self.mode == AuthMode::Off {
            return Ok(Caller {
                subject: "anonymous".into(),
            });
        }
        match bearer_token(headers) {
            None => Err(AuthError::Unauthenticated),
            Some(token) => match self.tokens.get(token) {
                Some(subject) => Ok(Caller {
                    subject: subject.clone(),
                }),
                None => Err(AuthError::Unauthenticated),
            },
        }
    }

    fn required(&self) -> bool {
        self.mode == AuthMode::Required
    }
}

pub async fn require_auth(
    State(verifier): State<Arc<StaticTokenVerifier>>,
    mut request: Request,
    next: Next,
) -> Response {
    match verifier.authenticate(request.headers()) {
        Ok(caller) => {
            request.extensions_mut().insert(caller);
            next.run(request).await
        }
        Err(error) => {
            // core-gap: 25 (core renders the code auth_unavailable and has no Retry-After; the standard needs unavailable and Retry-After)
            let api_error = match error {
                AuthError::Unauthenticated => ApiError::new(
                    ErrorCode::Unauthenticated,
                    "A valid bearer token is required.",
                ),
                AuthError::Unavailable(message) => {
                    ApiError::new(ErrorCode::Unavailable, message).with_retry_after(5)
                }
                AuthError::Forbidden(message) => ApiError::new(ErrorCode::Forbidden, message),
            };
            api_error.into_response()
        }
    }
}
