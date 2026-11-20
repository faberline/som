use axum::http::HeaderMap;
use axum::http::header::IF_MATCH;

use crate::domain::order::{Order, to_json};
use crate::interfaces::error::{ApiError, ErrorCode};

// core-gap: 11 (core has no ETag helper)
pub fn etag_of(order: &Order) -> String {
    format!(
        "\"{:016x}\"",
        fnv1a64(to_json(order).to_string().as_bytes())
    )
}

fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0100_0000_01b3);
    }
    hash
}

pub fn check_if_match(headers: &HeaderMap, current: &Order) -> Result<(), ApiError> {
    let Some(value) = headers.get(IF_MATCH) else {
        return Err(ApiError::new(
            ErrorCode::PreconditionRequired,
            "If-Match is required.",
        ));
    };
    if value.as_bytes() == etag_of(current).as_bytes() {
        Ok(())
    } else {
        Err(ApiError::new(
            ErrorCode::PreconditionFailed,
            "If-Match does not match the current ETag.",
        ))
    }
}
