# Operations

This document covers running, configuring and diagnosing `som-service`. The
commands in it are tested: `tests/it/operations_doc.rs` checks each one.

## Run

Build the service, then start it. `SOM` and `WORK` are set as in step 1 of
`QUICKSTART.md`: `SOM` is the built `som-service` binary and `WORK` is a
temporary directory.

```sh
$SOM serve --port 8767 &
curl -fsS http://127.0.0.1:8767/health
$SOM compile som-service/tests/it/fixtures/defer-seven.cnl > "$WORK/bundle.json"
```

## Configuration

| Command | Flag | Type | Default | Meaning |
|---|---|---|---|---|
| `serve` | `--port` | u16 | 8767 | Port to listen on. The service binds 127.0.0.1 only. |
| `compile` | `--url` | URL | `http://127.0.0.1:8767` | Service that the command calls. |
| `generate` | `--url` | URL | `http://127.0.0.1:8767` | Service that the command calls. |
| `assemble` | `--url` | URL | `http://127.0.0.1:8767` | Service that the command calls. |

There is no environment variable.

## Limits

| Route | Limit in bytes | Constant |
|---|---|---|
| `POST /compile` | 262144 | `COMPILE_BODY_LIMIT` |
| `POST /assemble` | 2097152 | `ASSEMBLE_BODY_LIMIT` |

A body over the limit gets HTTP 413 and the error code `REQUEST_TOO_LARGE`
in the error envelope.

## Runbook

### Port in use

Symptom: the service does not start, and stderr shows
`Error: Os { code: 98, kind: AddrInUse, message: "Address already in use" }`.
The process exits with status 1.

Cause: another process already listens on the port.

Fix: stop that process, or start the service on another port with `--port`.

Here `PORT` is a busy port, for example the port of a running `serve`:

```sh
$SOM serve --port "$PORT"
```

### Invalid request (400)

Symptom: the response status is 400, and the body is JSON with
`"code":"INVALID_JSON"` and `"error":"invalid_json"`, a `message` and a
`diagnostics` array.

Cause: the request body is not valid JSON.

Fix: send a body that is valid JSON with the `content-type: application/json`
header.

Here `URL` is `http://127.0.0.1:$PORT`, the address of a running service:

```sh
curl -sS -X POST "$URL/compile" -d '{'
```

The output is a `text` block:

```text
{"code":"INVALID_JSON","error":"invalid_json","message":"...","diagnostics":[]}
```

### Unknown route (404)

Symptom: the response status is 404 `Not Found` with an empty body.

Cause: the path is not a route of the service. A known path with another
method gets 405 and the header `allow`.

Fix: call a route that the service serves, such as `GET /health`.

Here `URL` is `http://127.0.0.1:$PORT`, the address of a running service:

```sh
curl -sS -i "$URL/nope"
```

The first line of the output is a `text` block:

```text
HTTP/1.1 404 Not Found
```

### Compile diagnostics

Symptom: `compile` exits with status 1. Stderr shows the JSON error with
`"code": "CNL_SYNTAX"` and a `diagnostics` entry with `line`, `column`,
`byte_start`, `byte_end` and `allowed_form`. Then it prints
`Error: "Service returned HTTP 400 Bad Request."`.

Cause: the source file has a sentence that the CNL grammar does not accept.

Fix: write the sentence at `line` and `column` in a form that the grammar
accepts. `allowed_form` gives the accepted form.

Here `BAD` is a CNL file with a mistake, for example one line `Package x.`,
and `URL` is `http://127.0.0.1:$PORT`, the address of a running service:

```sh
$SOM compile "$BAD" --url "$URL"
```
