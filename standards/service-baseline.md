# service-baseline

## Name

No CNL word. CNL does not name it; every generated API gets it.

## Decisions

| # | Decision | Proof test |
|---|---|---|
| 25 | Authentication: core `service-auth`, bearer token. Each operation needs a signed-in caller by default. Open mode must be set in the configuration. | `auth_required_by_default_and_open_mode_needs_configuration` |
| 26 | Storage: a store trait (for example `OrderStore`) and an in-memory implementation. A real database is a later standard that CNL must name. | `store_trait_has_in_memory_implementation` |
| 27 | Request id: read or make `x-request-id`, return it, and put it in each log line. | `request_id_is_read_or_made_returned_and_logged` |
| 28 | Logs and metrics: core `service-observability`. Each operation has a count, a latency and an error count. | `each_operation_has_count_latency_and_error_count` |
| 29 | Health: `/healthz` and `/readyz`. | `healthz_and_readyz_respond` |
| 30 | Timeout 30 seconds for each request. Graceful shutdown with core `server-lifecycle`. Default: shutdown grace 10 seconds. | `request_times_out_after_30_seconds_and_shutdown_is_graceful` |
| 31 | Admission limits: core `service-http`. The default values are written in the standard. Default: body limit 1 MiB (decision 10); read capacity 600 and write capacity 120 for each key in each 60 seconds (`<SVC>_ADMISSION_READ_CAPACITY`, `<SVC>_ADMISSION_WRITE_CAPACITY`, `<SVC>_ADMISSION_REFILL_SECS` = 60, `<SVC>_ADMISSION_MAX_KEYS` = 1024). | `admission_defaults_apply` |
| 32 | OpenAPI: the output has `openapi.json`, and the service serves it. The client comes from core `openapi-codegen`. | `openapi_json_is_served_and_equals_the_file` |
| 33 | Layout: the four layers of the aw standard `rust-layout.md`. | `four_layers_are_present` |
| 34 | Dependencies: core is pinned to a tag. `Cargo.lock` is present. | `core_is_pinned_to_a_tag_and_cargo_lock_is_present` |
| 35 | Quality: `fmt`, `clippy` with no warning, no `unwrap`, no `unsafe`. | `fmt_clippy_clean_no_unwrap_no_unsafe` |
| 36 | Tests: for each operation one success test and one test for each error. Pagination has a property test: a walk of all pages gives all items, with no repeat and no gap. | `each_operation_has_success_and_error_tests_and_pagination_property_test` |
| 37 | Docs: a README that says how to run it, with one `curl` example for each operation. | `readme_has_run_steps_and_one_curl_per_operation` |

## Facts that CNL must give

None.

The standard does not guess a fact.

## Tests

Every row of the Decisions table names its proof test. Each generated API has one success test for each operation and one test for each error, and the pagination property test of decision 36.

## Usual changes

| Change | Decisions touched |
|---|---|
| Replace the in-memory store with a real database | 26 |
| Change the admission limits | 31 |

## Core mapping

Checked at core tag v0.5.1 (commit d31cb479bf58080c59a9d772e9dab25176da3e90), the tag that som-service pins.

| Decision | Core crate and item | File at tag v0.5.1 | Status |
|---|---|---|---|
| D25 | `service-auth`: `auth_middleware`, `Verifier`, `bearer_token`; `AuthConfig::resolve` with mode `off` or `required` | `crates/service-auth/src/application/http/middleware.rs`, `crates/service-auth/src/application/http/verifier.rs`, `crates/service-auth/src/app/static_token_auth.rs` | partial |
| D26 | none in core; the store trait is local code | none | gap |
| D27 | `service-observability`: log field `request_id` (`MAX_REQUEST_ID_BYTES` = 128, `ServiceJsonFormatter`); no middleware | `crates/service-observability/src/jsonl.rs` | gap |
| D28 | `service-observability`: `MetricsProvider`, `init_tracing`; `metrics-prometheus`: `Counter`, `Latency`; no per-operation middleware | `crates/service-observability/src/metrics.rs`, `crates/metrics-prometheus/src/instrument.rs` | partial |
| D29 | `service-http`: `lifecycle_probe_routes`, `standard_probe_routes` (`/healthz`, `/readyz`, `/metrics`, `/openapi.json`, `/docs`) | `crates/service-http/src/interfaces/probes.rs` | present |
| D30 | `server-lifecycle`: `LifecycleController`; `service-http`: `serve_with_lifecycle`, `LifecycleShutdownTrigger`, `run_signal_bridge`; no per-request timeout layer | `crates/server-lifecycle/src/lifecycle/controller.rs`, `crates/service-http/src/interfaces/transport/serve.rs`, `crates/service-http/src/interfaces/signal.rs` | partial |
| D31 | `service-http`: `AdmissionConfig::from_env`, `admission_middleware`, `body_limit_layer` | `crates/service-http/src/application/admission_config.rs`, `crates/service-http/src/interfaces/admission_middleware.rs`, `crates/service-http/src/interfaces/body_limit.rs` | partial |
| D32 | `service-http`: `/openapi.json` route in `standard_probe_routes`; `openapi-codegen`: `generate` for the client | `crates/service-http/src/interfaces/probes.rs`, `crates/openapi-codegen/src/application/generate.rs` | present |

### Core gaps

1. D25: `AuthConfig::resolve` treats an empty mode as `off`. The standard needs `required` as the default. The generated config must default the mode to `required` until core offers a default. Also `AuthError::Unavailable` renders the code `auth_unavailable`; decision 22 names `unavailable`.
2. D26: core has no store trait. The reference and the generator write it locally (this is not a core request; it is listed because decision 26 names no core crate).
3. D27: core has no middleware that reads or makes `x-request-id`, returns it, and puts it in the log span.
4. D28: core has no middleware that counts each operation, measures its latency and counts its errors.
5. D30: core has no 30 second request timeout layer.
6. D31: `AdmissionConfig` is off when no capacity is set; core has no default capacity. The values of decision 31 are the standard's defaults.
7. Outside 25 to 32, found while reading: core `service-http` `ApiErr` and `ErrorEnvelope` have no `details` list (decision 21); core has no `ETag` or `If-Match` helper (11), no `Idempotency-Key` store (12), no cursor helper (18) and no UUIDv7 maker (7).

## Version

som.std.v1
