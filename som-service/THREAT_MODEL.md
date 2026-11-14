# som-service threat model

## Scope

The `som-service` binary: its `serve` command (a local HTTP service with
`/compile`, `/assemble` and `/health`) and the code it generates: a Rust
client for Defer and an axum server. `som-rs` is frozen and out of scope.

## Assets

- The integrity of the generated Rust code that a caller builds and runs.
- The bearer token that a generated client sends to Defer. It is passed in
  by the caller and is never stored in CNL or in a bundle.
- The copy of the Defer contract in `contract/` (pinned by revision and
  recorded hashes in `contract/freeze.json`).

## Trust boundaries

- Untrusted CNL or JSON text sent to `/compile` and `/assemble`.
- Generated code: the caller builds and runs it, so it runs with the
  caller's privileges.
- The network between a generated client and Defer, which carries the
  bearer token.

## Threats

| ID | Threat | Control | Evidence |
|---|---|---|---|
| T1 | An oversized request body exhausts memory | Body limits: `COMPILE_BODY_LIMIT` (256 KiB) and `ASSEMBLE_BODY_LIMIT` (2 MiB), applied per route; a larger input is refused with `REQUEST_TOO_LARGE`, HTTP 413 | `src/lib.rs` (the two constants and the length checks), `src/server.rs` (`DefaultBodyLimit` on `/compile` and `/assemble`), `src/error.rs` (413 mapping) |
| T2 | Duplicate keys or malformed JSON silently change a request | A strict JSON parser refuses duplicate keys and malformed text with a diagnostic and HTTP 400 | `src/strict_json.rs` (`parse`, `parse_service`); `tests/properties.rs::duplicate_keys_are_always_rejected` |
| T3 | A hostile name in CNL or JSON injects code into generated Rust | `identifier` in `src/model.rs` accepts only lower-case ASCII identifiers of 1 to 64 bytes and refuses Rust keywords | `src/model.rs` (`identifier`) |
| T4 | A token or a server response body leaks through logs or error text | `ClientError` Debug prints no response bytes, and `DeferClient` Debug hides its fields (`finish_non_exhaustive`) | `templates/lib.rs.txt` (the `Debug` impls of `ClientError` and `DeferClient`) |
| T5 | A redirect sends the bearer token to another host | The generated client does not follow redirects; a redirect response is returned as a result | `templates/lib.rs.txt` (`redirect` policy); `tests/client_transport.rs::redirects_are_returned_without_following_location` |
| T6 | A queue or task ID with `/`, `.` or `..` reaches another Defer path | Each path segment is percent-encoded; an empty, `.` or `..` segment is refused before any request is sent | `templates/lib.rs.txt` (segment check near line 165); `tests/client_transport.rs::empty_and_dot_segments_never_send_a_request` |
| T7 | A vulnerable or changed dependency enters the build | Exact `=` pins in `Cargo.toml`, the nlp dependency pinned by `rev`, and `cargo deny --locked check` on every pull request | `som-service/Cargo.toml`, `deny.toml`, the `test-audit` job in `.github/workflows/ci.yml` |
| T8 | The copy of the Defer contract is changed without notice | Hashes of the contract files are recorded in `contract/freeze.json` (`files_sha256`) | `contract/freeze.json` |

## Accepted risks

- `/compile` and `/assemble` have no authentication. The `serve` command
  binds only `127.0.0.1` (`src/main.rs`), so the callers are local.
- No rate limit on `/compile` and `/assemble`. The service has a single
  local caller.
- The hashes in `contract/freeze.json` are not checked by CI. A change to a
  contract file shows up in review, not in a build.
- No container image, SBOM or signature. som does not ship an image.
