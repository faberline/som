# som-service stability promise

This document lists the public surface of `som-service`: the command line,
the HTTP routes, the schema ids, the bundle format and the generated client.

## Promise

`som-service` follows semantic versioning for every item below. A change that
removes or renames an item, or changes its meaning, is breaking. A new item may
be added in a minor version. Nothing here breaks before 2.0.0. The crate
`version` in `Cargo.toml` changes only when a person asks for it.

## Command line

`som-service` has five subcommands:

- `serve`: runs the HTTP service. `--port` defaults to 8767.
- `compile`: sends a source file to a running service. `--url` defaults to `http://127.0.0.1:8767`.
- `generate`: sends a prompt file to a running service and gets the generated files. `--url` defaults to `http://127.0.0.1:8767`.
- `assemble`: sends a bundle file to a running service and gets the rendered files. `--url` defaults to `http://127.0.0.1:8767`.
- `identity`: prints the source manifest used for the service asset identity.

## HTTP routes

The data routes are the compiler routes. The probe and documentation routes come
from the core `service-http` crate.

| Method | Path | Smoke request | Smoke status | Other statuses |
|---|---|---|---|---|
| GET | `/health` | none | 200 | |
| GET | `/healthz` | none | 200 | |
| GET | `/readyz` | none | 200 | |
| GET | `/metrics` | none | 200 | |
| GET | `/openapi.json` | none | 200 | |
| GET | `/docs` | none | 200 | |
| POST | `/compile` | body `{` | 400 | 200, 413, 422 |
| POST | `/assemble` | body `{` | 400 | 200, 413, 422 |

A wrong method on `/compile` or `/assemble` gives 405. An unknown path gives 404.
The paths `/health`, `/compile` and `/assemble` are in the OpenAPI document
(`/openapi.json` on the core probe routes is not part of that document).

## Schema ids

- Formats: `som.cnl.service.v1`, `som.prompt-json.service.v1`, `som.prompt-json.service.v2`.
- Prompt versions: `som.prompt.service.v1`, `som.prompt.service.v2`.
- DSL versions: `som.dsl.service.v1`, `som.dsl.service.v2`.
- Target: `rust-service-client.v1`.
- Bundle: `som.bundle.service.v1`.
- Compiler family: `som.service-compiler.v1`.

## Bundle format

A bundle is a JSON object with the fields `version` (`som.bundle.service.v1`),
`assets`, `prompt`, `dsl` and `digest`. The `digest` is the SHA-256 of the other
fields. The service checks each field before it renders files.

## Generated client

The generated client is made by core `openapi-codegen`. Its public items are:

- `Client`, with `Client::new(base_url)` and one method per operation: `task_status`, `task_create`, `queue_put` and `task_cancel`;
- `ClientError`, `ApiError` and the result types `ApiResult` and `ClientResult`;
- `TransportPolicy` and `PrivateTrust`;
- the per-operation error enums (`TaskStatusError`, `TaskCreateError`, `QueuePutError`, `TaskCancelError`);
- the `models` module.

## How the promise is checked

The test `public_surface_matches_contract` in `tests/it/stability.rs` compares
the command names, the routes, the schema ids and the OpenAPI paths with
`contract/public-surface.json`. To change the public surface, change that
contract file and this document in the same pull request.
