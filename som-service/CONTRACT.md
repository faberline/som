# Defer task status service contract v1

This slice compiles one Defer GET operation into a Rust library.
The old som-rs compiler, formats, health response, locks and assets stay unchanged.
The service compiler has a separate Cargo root, binary and asset identity.

## Language and typed input

The new formats are `som.cnl.service.v1` and `som.prompt-json.service.v1`.
The typed schema is `som.prompt.service.v1`. The DSL is `som.dsl.service.v1`.
The output profile is `rust-service-client.v1`.
The CNL contract and parser interface are agreed with NLP in coordination.json.

The positive typed input and expected DSL are independent fixtures under contract/.
The first catalog contains only `defer.http.task_status.v1`, pinned to Defer
5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0 and its locked Core dependency.
Unknown provider/type/target/operation names are SOM semantic errors.
`task_cancel` remains unsupported in this slice.

`operation.alias` is the public Rust method name. Inputs map declared Rust
argument names to catalog parameters `queue` and `task_id`. Input declaration
order does not change the canonical route order. Both types are `string`.
Package, alias and input names use `[a-z][a-z0-9_]{0,63}`. Rust keywords and
fixed client method names are reserved. Argument names cannot repeat.
Each catalog parameter must be bound exactly once. No unused input is allowed.

## Generated client

The output is a Rust library crate. It exports `DeferClient`, `ClientError`,
`ErrorEnvelope`, `TaskStatusResponse` and `TaskStatus`.
`DeferClient::new(base_url: &str, bearer: Option<&str>)` takes runtime settings.
The generated method is async. Its two `&str` arguments follow catalog order.
It returns `Result<TaskStatusResponse, ClientError>`.
The package name and method/input aliases come from the validated typed input.

The base URL must be an HTTP or HTTPS origin. A root slash is allowed.
Credentials in the URL, a path prefix, query and fragment are refused.
The client does not follow redirects. It has a 30 second request timeout.
Credentials are runtime bearer headers, never generated source literals.
Sensitive header values must not appear in Debug output or error details.

Encode each queue/task argument as one URL path segment. Slash, percent,
spaces and Unicode must round-trip. Literal `%2e` becomes `%252e`.
Refuse empty, `.` and `..` arguments before any request. This is an explicit
client transport limit. It does not add a Defer admission rule.
The request is GET `/v1/queues/{queue}/tasks/{task_id}` with no body.

Only HTTP 200 is a successful response. Decode the five native TaskStatus
variants. Leased contains a string attempt_id, u64 executor_node and epoch,
and an RFC3339 UTC timestamp. Record fields are required. Extra response record fields are ignored, as in the native DTO decoder.
Malformed JSON, duplicate known fields, invalid enums and numeric overflow
produce InvalidResponse. The raw status and body remain available.

For any non-200 response, preserve the HTTP status and raw body bytes.
Decode `{error,message}` when it is valid; retain bytes when it is not.
Do not call `error_for_status` before reading the body. Network failures,
invalid configuration/input, native HTTP errors and invalid successful
responses are distinct ClientError variants.

## HTTP and compiler expectations

The source-derived wire fixtures cover Scheduled, Leased, Succeeded,
DeadLettered and Canceled. Authentication runs before authorization and lookup.
The error fixtures cover missing/unknown bearer401, forbidden queue403,
authorized missing queue404 and authorized missing task404.
Native Defer permissions and messages are preserved.

The service compiler exposes GET /health and POST /compile and /assemble.
Compile accepts exactly `{format,source}` or `{format,prompt}` as appropriate.
Assemble accepts exactly `{bundle}`. JSON duplicates and unknown keys are refused.
A saved bundle contains the typed prompt, canonical DSL and service asset identity.
Assemble validates the full bundle, rebuilds the expected DSL and checks hashes.
An old or altered asset identity, prompt/DSL mismatch or digest mismatch is refused.
No saved bundle can select raw URLs, methods, templates or credentials.

CLI commands are `serve`, `compile`, `generate` and `assemble`.
They use the same local HTTP entry points. Defaults use a separate loopback port.
Old compiler entry points refuse both new formats and keep their old health values.

## Local acceptance

Freeze contract fixtures before writing the relevant implementation.
Compare CNL and typed input through syntax, typed data, source map, DSL and files.
Build and test the generated crate. Exercise HTTP/CLI compile and saved assemble.
Use a real pinned Defer router/server for native success and error tests.
Use an independent capture server for exact URL segments and malformed responses.
The generated library must execute the calls in these tests.

Run the unchanged old 1930-record comparison,106 SOM tests,9 standalone CNL tests
and29 NLP workspace tests. Keep the original178cases/890records/322products.
Do not replace expected results to accept a failure. Keep separate reports
for source preservation, the new service, generated client and old regressions.
