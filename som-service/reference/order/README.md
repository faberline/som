# som-reference-order

## Run

Run these from `som-service/`:

```bash
cargo test --locked -p som-reference-order
```

This crate has no binary: `src/lib.rs` only. The service is wired in code.
The plain router is `som_reference_order::interfaces::routes::router(store, verifier)`,
with `store: Arc<dyn OrderStore>` (for example `Arc::new(MemoryStore::new())` from
`infrastructure::memory_store`) and `verifier: Arc<StaticTokenVerifier>`.
Auth is required by default (`AuthMode::Required`); `AuthMode::Off` gives open mode and
must be set in the configuration. For a real service use:

- `routes::router_with_probes(store, clock, verifier, metrics, lifecycle)`, which adds
  `/healthz`, `/readyz` and `/openapi.json`;
- `interfaces::server::serve(listener, app, lifecycle)`, with
  `interfaces::server::shutdown_trigger(lifecycle)` for graceful shutdown;
- `routes::router_with_limits(store, clock, verifier, metrics, limits)`, where `limits`
  is `interfaces::limits::Limits::from_lookup(...)` and reads these environment names
  (`ORDER` is the prefix):
  - `ORDER_ADMISSION_READ_CAPACITY` (default 600) and `ORDER_ADMISSION_WRITE_CAPACITY` (120),
    per key in each window;
  - `ORDER_ADMISSION_REFILL_SECS` (60) and `ORDER_ADMISSION_MAX_KEYS` (1024).

The request timeout is 30 seconds (`REQUEST_TIMEOUT_SECS`) and the shutdown grace is
10 seconds (`SHUTDOWN_GRACE_SECS`). The request id header is `x-request-id`.

## Examples

The examples assume a service wired as in Run, listening on `localhost:8080`.
Each call sends a bearer token in place of `<token>`.

```bash
curl -X POST http://localhost:8080/v1/orders -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -H 'Idempotency-Key: <key>' -d '{"customer_id":"c-1","total_cents":1500,"currency":"USD","status":"pending","note":"first order"}'
curl http://localhost:8080/v1/orders/<id> -H 'Authorization: Bearer <token>'
curl 'http://localhost:8080/v1/orders?limit=2' -H 'Authorization: Bearer <token>'
curl -X PUT http://localhost:8080/v1/orders/<id> -H 'Authorization: Bearer <token>' -H 'If-Match: <etag>' -H 'Content-Type: application/json' -d '{"customer_id":"c-1","total_cents":1600,"currency":"USD","status":"paid","note":"first order"}'
curl -X DELETE http://localhost:8080/v1/orders/<id> -H 'Authorization: Bearer <token>' -H 'If-Match: <etag>'
```

## Decisions

The 37 decisions of the standard catalog (`standards/`), each with the test that proves it.

| # | Decision | File | Test |
|---|---|---|---|
| 1 | Paths: `/v1/orders`, `/v1/orders/{id}`. Plural, lower case, kebab-case. | tests/it/routes.rs | `crud_paths_are_plural_kebab_lower` |
| 2 | Create: `POST /v1/orders` gives `201`, a `Location` header and the full resource. | tests/it/routes.rs | `create_returns_201_location_and_full_resource` |
| 3 | Read: `GET /v1/orders/{id}` gives `200`; not found gives `404`. | tests/it/routes.rs | `read_returns_200_and_unknown_id_returns_404` |
| 4 | List: `GET /v1/orders` gives `200` and uses `standard-pagination`. | tests/it/pagination.rs | `list_returns_200_with_standard_pagination` |
| 5 | Update: `PUT` replaces the full resource and gives `200`. No `PATCH` in v1. | tests/it/routes.rs | `replace_returns_200_with_full_resource_and_no_patch_route` |
| 6 | Delete: hard delete, `204`. A second delete gives `404`. No soft delete in v1. | tests/it/routes.rs | `delete_returns_204_and_second_delete_returns_404` |
| 7 | Id: the server makes it, a UUIDv7 string. The client cannot set it. | tests/it/domain.rs | `id_is_server_made_uuidv7_and_client_id_is_refused` |
| 8 | Time fields: `created_at` and `updated_at` are added, RFC 3339, UTC, set by the server. | tests/it/domain.rs | `time_fields_are_rfc3339_utc_and_set_by_server` |
| 9 | Validation: unknown fields are refused. A type or constraint error gives `422` with one entry for each bad field. | tests/it/domain.rs | `unknown_field_is_refused_and_bad_fields_give_422_with_one_entry_each` |
| 10 | Request size: body limit 1 MiB; larger gives `413`. | tests/it/routes.rs | `body_over_1_mib_returns_413` |
| 11 | Concurrent change: the resource has an `ETag`. `PUT` and `DELETE` need `If-Match`; a mismatch gives `412`; a missing header gives `428`. | tests/it/preconditions.rs | `etag_is_returned_and_if_match_mismatch_412_missing_428` |
| 12 | Repeated create: `POST` accepts `Idempotency-Key`. Same key and same body give the same result; same key and a different body give `409`. Keys are kept for 24 hours. | tests/it/idempotency.rs | `idempotency_key_same_body_same_result_different_body_409_and_expires_after_24h` |
| 13 | Cursor pagination. No offset. | tests/it/pagination.rs | `list_has_no_offset_parameter` |
| 14 | Parameters: `limit` and `cursor` only. | tests/it/pagination.rs | `list_parameters_are_limit_and_cursor_only` |
| 15 | Default 50 items, maximum 200. A larger `limit` gives `400`; it is not reduced silently. | tests/it/pagination.rs | `default_limit_50_max_200_and_larger_limit_returns_400` |
| 16 | Response: `{"items": [...], "next_cursor": "<string>" or null}`. `null` means no next page. | tests/it/pagination.rs | `list_body_has_items_and_next_cursor_null_on_last_page` |
| 17 | Order: `created_at` newest first, then `id`. The order is fixed. | tests/it/pagination.rs | `list_order_is_created_at_desc_then_id` |
| 18 | The cursor is an opaque string. A bad cursor gives `400`. | tests/it/pagination.rs | `bad_cursor_returns_400_and_cursor_is_opaque` |
| 19 | No total count. | tests/it/pagination.rs | `list_body_has_no_total` |
| 20 | Sort and filter are not part of this standard. Each is a later standard that CNL must name. | tests/it/pagination.rs | `list_has_no_sort_or_filter_parameter` |
| 21 | Body: `{"error": "<code>", "message": "<text for a person>", "details": [...]}`. `details` is optional. This keeps the two fields of core `service-auth`. | tests/it/errors.rs | `error_body_has_error_message_and_optional_details` |
| 22 | Codes: `invalid_request` (400), `unauthenticated` (401), `forbidden` (403), `not_found` (404), `conflict` (409), `precondition_failed` (412), `payload_too_large` (413), `validation_failed` (422), `precondition_required` (428), `rate_limited` (429), `internal` (500), `unavailable` (503). | tests/it/errors.rs | `each_error_code_maps_to_its_status` |
| 23 | A `500` does not show internal error text. It gives the code and the request id. The detail goes to the log. | tests/it/errors.rs | `internal_error_hides_text_and_shows_request_id` |
| 24 | `429` and `503` have `Retry-After`. | tests/it/errors.rs | `rate_limited_and_unavailable_have_retry_after` |
| 25 | Authentication: core `service-auth`, bearer token. Each operation needs a signed-in caller by default. Open mode must be set in the configuration. | tests/it/auth.rs | `auth_required_by_default_and_open_mode_needs_configuration` |
| 26 | Storage: a store trait (for example `OrderStore`) and an in-memory implementation. A real database is a later standard that CNL must name. | tests/it/store.rs | `store_trait_has_in_memory_implementation` |
| 27 | Request id: read or make `x-request-id`, return it, and put it in each log line. | tests/it/request_id.rs | `request_id_is_read_or_made_returned_and_logged` |
| 28 | Logs and metrics: core `service-observability`. Each operation has a count, a latency and an error count. | tests/it/metrics.rs | `each_operation_has_count_latency_and_error_count` |
| 29 | Health: `/healthz` and `/readyz`. | tests/it/probes.rs | `healthz_and_readyz_respond` |
| 30 | Timeout 30 seconds for each request. Graceful shutdown with core `server-lifecycle`. Default: shutdown grace 10 seconds. | tests/it/limits.rs | `request_times_out_after_30_seconds_and_shutdown_is_graceful` |
| 31 | Admission limits: core `service-http`. The default values are written in the standard. Default: body limit 1 MiB (decision 10); read capacity 600 and write capacity 120 for each key in each 60 seconds (`<SVC>_ADMISSION_READ_CAPACITY`, `<SVC>_ADMISSION_WRITE_CAPACITY`, `<SVC>_ADMISSION_REFILL_SECS` = 60, `<SVC>_ADMISSION_MAX_KEYS` = 1024). | tests/it/limits.rs | `admission_defaults_apply` |
| 32 | OpenAPI: the output has `openapi.json`, and the service serves it. The client comes from core `openapi-codegen`. | tests/it/probes.rs | `openapi_json_is_served_and_equals_the_file` |
| 33 | Layout: the four layers of the aw standard `rust-layout.md`. | tests/it/layers.rs | `four_layers_are_present` |
| 34 | Dependencies: core is pinned to a tag. `Cargo.lock` is present. | tests/it/quality.rs | `core_is_pinned_to_a_tag_and_cargo_lock_is_present` |
| 35 | Quality: `fmt`, `clippy` with no warning, no `unwrap`, no `unsafe`. | tests/it/quality.rs | `fmt_clippy_clean_no_unwrap_no_forbidden_code` |
| 36 | Tests: for each operation one success test and one test for each error. Pagination has a property test: a walk of all pages gives all items, with no repeat and no gap. | tests/it/quality.rs | `each_operation_has_success_and_error_tests_and_pagination_property_test` |
| 37 | Docs: a README that says how to run it, with one `curl` example for each operation. | tests/it/readme.rs | `readme_has_run_steps_and_one_curl_per_operation` |

## How to change

### Add a field with a constraint
Add the field to `Order` and to `OrderInput`, read and check it in `parse_input`, copy it in `create` and `replace`, and return it in `to_json`.
Files: `src/domain/order.rs`
Decisions: 7, 8, 9

### Add a filter to the list
Add the key to `ListQuery`, read it in `list` with `400` for an unknown value, and keep only the matching items in `list_page`.
Files: `src/application/store.rs`, `src/infrastructure/memory_store.rs`, `src/interfaces/routes.rs`
Decisions: 14, 20

### Replace the in-memory store
Write a second type that implements `OrderStore`, and build the router with it; the handlers do not change.
Files: `src/infrastructure/vec_store.rs`, `src/infrastructure/mod.rs` (new: vec_store.rs)
Decisions: none

### Add POST /v1/orders/{id}/cancel
Add the route and a handler that checks `If-Match` as `replace` does, then sets the existing `canceled` status and `updated_at`.
Files: `src/application/orders.rs`, `src/domain/order.rs`, `src/interfaces/routes.rs`
Decisions: 1, 11

### Change hard delete to soft delete
Set `deleted_at` on the item in `delete` and keep it in the store; `get`, `replace` and `list_page` then treat the item as absent.
Files: `src/domain/order.rs`, `src/infrastructure/memory_store.rs`
Decisions: 6
