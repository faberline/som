# Threat model: som-service

## Scope

The `som-service` command line and its local HTTP service (`serve`, which binds
`127.0.0.1` only, `src/main.rs`), and the Rust client and server that it
generates. The frozen `som-rs` compiler and the old `som` service are out of
scope.

## Assets

- The integrity of the generated code: the client and server that a caller
  builds and runs.
- The bearer token that a generated client sends to Defer. It is supplied at
  runtime and is never written into CNL or the generated source.
- The Defer HTTP contract copy, `contract/wire.json`, which is pinned by
  revision and by SHA-256 in `contract/freeze.json`.

## Trust boundaries

- CNL and JSON text sent to `/compile` and `/assemble` is untrusted input.
- The generated code is built and run by the caller, outside som-service.
- The network between a generated client and Defer.

## Threats

| ID | Threat | Control | Evidence |
|---|---|---|---|
| T1 | An oversized request body exhausts memory | Body limits `COMPILE_BODY_LIMIT` (256 KiB) and `ASSEMBLE_BODY_LIMIT` (2 MiB); a larger body gets 413 `REQUEST_TOO_LARGE` | `src/lib.rs` (the two constants and the size checks), `src/server.rs` (`DefaultBodyLimit` and `body`) |
| T2 | Malformed or duplicate JSON silently replaces a value | A strict JSON parser that rejects duplicate keys and bad syntax with a diagnostic (`INVALID_JSON`, `INVALID_SCHEMA`) | `src/strict_json.rs` test `duplicate_keys_do_not_silently_replace_values`; `tests/client_transport.rs` `invalid_successes_keep_exact_bytes_and_reject_known_duplicates` |
| T3 | A hostile name in CNL or JSON injects code into the generated Rust | `identifier` in `src/model.rs` accepts only lower-case ASCII identifiers of 1 to 64 bytes that are not Rust keywords | `src/model.rs` (`identifier`); `tests/compiler.rs` `names_and_exact_wire_bindings_are_checked_without_silent_renaming` |
| T4 | A bearer token or a response body leaks into an error message or log | `Debug` and `Display` of the generated client errors print the status and the message, not the response body or the credential | `templates/lib.rs.txt` (`impl fmt::Debug` and `impl fmt::Display` for `ClientError`, `impl fmt::Debug` for `DeferClient`); `som-service/README.md` |
| T5 | The generated client is sent to another origin by a redirect | The client sets `redirect::Policy::none()`; a redirect is returned as a response and `Location` is not followed | `templates/lib.rs.txt` (client builder); `tests/client_transport.rs` `redirects_are_returned_without_following_location` |
| T6 | A queue or task ID changes the request path (`/`, `.` or `..`) | Each path segment is encoded on its own; empty, `.` and `..` segments are refused before any request is sent | `templates/lib.rs.txt`; `tests/client_transport.rs` `empty_and_dot_segments_never_send_a_request` and `independently_encoded_segments_bearer_and_empty_get_body` |
| T7 | A vulnerable or tampered dependency is built into the service | Every direct dependency is pinned with `=` in `Cargo.toml`; the CNL syntax crate is a path dependency in `cnl/` and reviewed with the repository; `cargo deny --locked check` runs in CI job `test-audit` | `som-service/Cargo.toml`; `.github/workflows/ci.yml` (`test-audit`); `deny.toml` |
| T8 | The Defer contract copy is changed without notice | The copy is pinned by revision and by SHA-256 of `contract/wire.json` in `contract/freeze.json`; the wire fixtures must match | `contract/freeze.json` (`files_sha256`); `tests/client_transport.rs` `all_five_native_states_match_frozen_wire_fixtures` |

## Accepted risks

- No authentication on `/compile` and `/assemble`. The service binds to
  localhost only, and its single local caller is trusted with that access.
- No rate limit. There is one local caller, and the request size limits (T1)
  bound the work of each request.
- No container image, SBOM or signature. som does not ship an image.
