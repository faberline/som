use std::sync::Arc;

use axum::Json;
use axum::Router;
use axum::body::Bytes;
use axum::extract::rejection::{BytesRejection, QueryRejection};
use axum::extract::{DefaultBodyLimit, Path, Query, State};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use serde_json::{Value, json};

use crate::application::orders::{
    create_order, delete_order, get_order, list_orders, replace_order,
};
use crate::application::pagination::{DEFAULT_LIMIT, MAX_LIMIT, decode_cursor};
use crate::application::store::OrderStore;
use crate::domain::order::{parse_input, to_json};
use crate::interfaces::error::{ApiError, ErrorCode};

pub const COLLECTION_PATH: &str = "/v1/orders";
pub const ITEM_PATH: &str = "/v1/orders/{id}";
pub const BODY_LIMIT_BYTES: usize = 1024 * 1024;

pub fn router(store: Arc<dyn OrderStore>) -> Router {
    Router::new()
        .route(COLLECTION_PATH, post(create).get(list))
        .route(ITEM_PATH, get(read).put(replace).delete(remove))
        .layer(DefaultBodyLimit::max(BODY_LIMIT_BYTES))
        .with_state(store)
}

fn invalid(message: impl Into<String>) -> ApiError {
    ApiError::new(ErrorCode::InvalidRequest, message)
}

fn read_body(body: Result<Bytes, BytesRejection>) -> Result<Value, ApiError> {
    let bytes = body.map_err(|rejection| {
        if rejection.status() == StatusCode::PAYLOAD_TOO_LARGE {
            ApiError::new(ErrorCode::PayloadTooLarge, "The body is larger than 1 MiB.")
        } else {
            invalid("The body could not be read.")
        }
    })?;
    serde_json::from_slice(&bytes).map_err(|_| invalid("The body is not valid JSON."))
}

async fn create(
    State(store): State<Arc<dyn OrderStore>>,
    body: Result<Bytes, BytesRejection>,
) -> Result<Response, ApiError> {
    let input = parse_input(&read_body(body)?)?;
    let order = create_order(store.as_ref(), input)?;
    let location = format!("{COLLECTION_PATH}/{}", order.id);
    Ok((
        StatusCode::CREATED,
        [(header::LOCATION, location)],
        Json(to_json(&order)),
    )
        .into_response())
}

async fn read(
    State(store): State<Arc<dyn OrderStore>>,
    Path(id): Path<String>,
) -> Result<Response, ApiError> {
    let order = get_order(store.as_ref(), &id)?;
    Ok((StatusCode::OK, Json(to_json(&order))).into_response())
}

async fn list(
    State(store): State<Arc<dyn OrderStore>>,
    query: Result<Query<Vec<(String, String)>>, QueryRejection>,
) -> Result<Response, ApiError> {
    let Query(pairs) = query.map_err(|_| invalid("The query string is not valid."))?;

    let mut limit: Option<&str> = None;
    let mut cursor: Option<&str> = None;
    for (name, value) in &pairs {
        let slot = match name.as_str() {
            "limit" => &mut limit,
            "cursor" => &mut cursor,
            _ => return Err(invalid(format!("Unknown query parameter: {name}."))),
        };
        if slot.is_some() {
            return Err(invalid(format!("Query parameter {name} is given twice.")));
        }
        *slot = Some(value.as_str());
    }

    let limit = match limit {
        None => DEFAULT_LIMIT,
        Some(text) => match text.parse::<usize>() {
            Ok(number) if (1..=MAX_LIMIT).contains(&number) => number,
            _ => return Err(invalid("limit must be an integer from 1 to 200.")),
        },
    };
    let after = match cursor {
        None => None,
        Some(text) => Some(decode_cursor(text).ok_or_else(|| invalid("The cursor is not valid."))?),
    };

    let page = list_orders(
        store.as_ref(),
        after
            .as_ref()
            .map(|(created_at, id)| (created_at.as_str(), id.as_str())),
        limit,
    );
    let body = json!({
        "items": page.items.iter().map(to_json).collect::<Vec<Value>>(),
        "next_cursor": page.next_cursor,
    });
    Ok((StatusCode::OK, Json(body)).into_response())
}

/// No If-Match yet: reference/order 3 (#283) adds it (decision 11).
async fn replace(
    State(store): State<Arc<dyn OrderStore>>,
    Path(id): Path<String>,
    body: Result<Bytes, BytesRejection>,
) -> Result<Response, ApiError> {
    let input = parse_input(&read_body(body)?)?;
    let order = replace_order(store.as_ref(), &id, input)?;
    Ok((StatusCode::OK, Json(to_json(&order))).into_response())
}

/// No If-Match yet: reference/order 3 (#283) adds it (decision 11).
async fn remove(
    State(store): State<Arc<dyn OrderStore>>,
    Path(id): Path<String>,
) -> Result<Response, ApiError> {
    delete_order(store.as_ref(), &id)?;
    Ok(StatusCode::NO_CONTENT.into_response())
}
