use std::sync::Arc;

use axum::Json;
use axum::Router;
use axum::body::Bytes;
use axum::extract::rejection::{BytesRejection, QueryRejection};
use axum::extract::{DefaultBodyLimit, Path, Query, State};
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use serde_json::{Value, json};

use crate::application::idempotency::{Clock, IdempotencyKeys, SystemClock};
use crate::application::orders::{
    create_order, delete_order, get_order, list_orders, replace_order,
};
use crate::application::pagination::{DEFAULT_LIMIT, MAX_LIMIT, decode_cursor};
use crate::application::store::OrderStore;
use crate::domain::order::{Order, parse_input, to_json};
use crate::interfaces::auth::{StaticTokenVerifier, require_auth};
use crate::interfaces::error::{ApiError, ErrorCode};
use crate::interfaces::etag::{check_if_match, etag_of};
use crate::interfaces::metrics::{OperationMetrics, record_operation};
use crate::interfaces::probes::probe_router;
use crate::interfaces::request_id::request_id_layer;
use server_lifecycle::LifecycleController;

pub const COLLECTION_PATH: &str = "/v1/orders";
pub const ITEM_PATH: &str = "/v1/orders/{id}";
pub const BODY_LIMIT_BYTES: usize = 1024 * 1024;

#[derive(Clone)]
struct AppState {
    store: Arc<dyn OrderStore>,
    keys: Arc<IdempotencyKeys>,
}

pub fn router(store: Arc<dyn OrderStore>, verifier: Arc<StaticTokenVerifier>) -> Router {
    router_with_clock(store, Arc::new(SystemClock), verifier)
}

pub fn router_with_clock(
    store: Arc<dyn OrderStore>,
    clock: Arc<dyn Clock>,
    verifier: Arc<StaticTokenVerifier>,
) -> Router {
    router_with_metrics(store, clock, verifier, Arc::new(OperationMetrics::new()))
}

pub fn router_with_metrics(
    store: Arc<dyn OrderStore>,
    clock: Arc<dyn Clock>,
    verifier: Arc<StaticTokenVerifier>,
    metrics: Arc<OperationMetrics>,
) -> Router {
    // Outermost: every response, also 404 and 401, gets the id.
    data_router(store, clock, verifier, metrics).layer(axum::middleware::from_fn(request_id_layer))
}

/// The order routes and their layers, without the request id layer.
fn data_router(
    store: Arc<dyn OrderStore>,
    clock: Arc<dyn Clock>,
    verifier: Arc<StaticTokenVerifier>,
    metrics: Arc<OperationMetrics>,
) -> Router {
    let state = AppState {
        store,
        keys: Arc::new(IdempotencyKeys::new(clock)),
    };
    Router::new()
        .route(COLLECTION_PATH, post(create).get(list))
        .route(ITEM_PATH, get(read).put(replace).delete(remove))
        .route_layer(axum::middleware::from_fn_with_state(verifier, require_auth))
        // Outside auth, so a 401 counts as an error call.
        .route_layer(axum::middleware::from_fn_with_state(
            metrics,
            record_operation,
        ))
        .layer(DefaultBodyLimit::max(BODY_LIMIT_BYTES))
        .with_state(state)
}

/// The order routes plus the probes (`/healthz`, `/readyz`, `/openapi.json`). The probes need no token and no metrics count, but they get the request id.
pub fn router_with_probes(
    store: Arc<dyn OrderStore>,
    clock: Arc<dyn Clock>,
    verifier: Arc<StaticTokenVerifier>,
    metrics: Arc<OperationMetrics>,
    lifecycle: LifecycleController,
) -> Router {
    data_router(store, clock, verifier, metrics)
        .merge(probe_router(lifecycle))
        .layer(axum::middleware::from_fn(request_id_layer))
}

fn invalid(message: impl Into<String>) -> ApiError {
    ApiError::new(ErrorCode::InvalidRequest, message)
}

fn idempotency_key(headers: &HeaderMap) -> Result<Option<&str>, ApiError> {
    match headers.get("idempotency-key") {
        None => Ok(None),
        Some(value) => match value.to_str() {
            Ok(text) if !text.is_empty() => Ok(Some(text)),
            _ => Err(invalid("Idempotency-Key must be a non-empty text.")),
        },
    }
}

fn resource_response(status: StatusCode, order: &Order, location: Option<String>) -> Response {
    let etag = etag_of(order);
    match location {
        Some(location) => (
            status,
            [(header::ETAG, etag), (header::LOCATION, location)],
            Json(to_json(order)),
        )
            .into_response(),
        None => (status, [(header::ETAG, etag)], Json(to_json(order))).into_response(),
    }
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
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Result<Bytes, BytesRejection>,
) -> Result<Response, ApiError> {
    let input = parse_input(&read_body(body)?)?;
    // The typed input, not the raw JSON: key order and unknown fields do not change it.
    let fingerprint = format!("{input:?}");
    let order = match idempotency_key(&headers)? {
        None => create_order(state.store.as_ref(), input)?,
        Some(key) => state
            .keys
            .create_once(key, &fingerprint, || {
                create_order(state.store.as_ref(), input)
            })?
            .ok_or_else(|| {
                ApiError::new(
                    ErrorCode::Conflict,
                    "The Idempotency-Key was used with a different body.",
                )
            })?,
    };
    let location = format!("{COLLECTION_PATH}/{}", order.id);
    Ok(resource_response(
        StatusCode::CREATED,
        &order,
        Some(location),
    ))
}

async fn read(
    State(AppState { store, .. }): State<AppState>,
    Path(id): Path<String>,
) -> Result<Response, ApiError> {
    let order = get_order(store.as_ref(), &id)?;
    Ok(resource_response(StatusCode::OK, &order, None))
}

async fn list(
    State(AppState { store, .. }): State<AppState>,
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

/// Decision 11: needs If-Match. The check and the write are two store calls, so a write between them is not caught; a store with a conditional write closes that gap.
async fn replace(
    State(AppState { store, .. }): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    body: Result<Bytes, BytesRejection>,
) -> Result<Response, ApiError> {
    let input = parse_input(&read_body(body)?)?;
    let current = get_order(store.as_ref(), &id)?;
    check_if_match(&headers, &current)?;
    let order = replace_order(store.as_ref(), &id, input)?;
    Ok(resource_response(StatusCode::OK, &order, None))
}

/// Decision 11: needs If-Match. The check and the delete are two store calls, so a write between them is not caught; a store with a conditional write closes that gap.
async fn remove(
    State(AppState { store, .. }): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let current = get_order(store.as_ref(), &id)?;
    check_if_match(&headers, &current)?;
    delete_order(store.as_ref(), &id)?;
    Ok(StatusCode::NO_CONTENT.into_response())
}
