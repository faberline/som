# DFR-07 client acceptance

This check uses the actual generated Rust library. It does not render a test copy.
Expected responses come from the frozen DFR-07 case and native source contract.
The native fixture uses Defer commit `5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0`.
Its Core dependency remains `029e765efaac4080bb9e8283005a9c6792953f54`.

The checks are Rust tests in `som-service/tests/it/`:
`acceptance_native.rs` (native Defer router, 35 cases and 10 path edge cases) and
`acceptance_client.rs` (the generated client against a capture server, 70 cases of its own
plus the 35 native cases).

Run the offline generated-client tests with `cargo test --locked` from `som-service/`.

Run the native Defer test (it is `#[ignore]`) with this block, exactly:

```sh
SOM_DEFER_REPO=<defer-repo> cargo test --locked -- --ignored acceptance_native
```

The native Defer test reads the pinned Defer revision with `git archive`, never changes or fetches the Defer repository, and builds offline; `CARGO_HOME` must hold the locked native dependencies.

The native server uses Defer's real router and committed Raft commands.
A fixed clock creates Scheduled, Leased, Succeeded, DeadLettered and Canceled states.
There is no background dispatch loop. The fixture never sends target requests.
Both required-auth and open-auth routers use the same state.
The client calls real HTTP routes for all five states and native 401, 403 and 404 errors.

The capture server checks the exact GET path, bearer header and empty body.
It supplies malformed responses and verifies raw error-body retention.
It also checks redirect refusal, invalid origins, invalid bearer headers,
connection failure, a truncated HTTP body, the 30 second request timeout
and safe Debug/Display output.
Empty, dot and dot-dot path values must fail before a request is sent.
This is the client transport limit stated in the contract.
The separate raw-HTTP checks preserve the native router's different behavior.

The Rust tests check 35 native HTTP cases, 10 native path edge cases, 105 generated-client cases and 48 captured requests.
This check covers the generated client only.
Compiler syntax, DSL, HTTP/CLI and unchanged old-compiler checks have separate reports.
