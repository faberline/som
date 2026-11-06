# DFR-07 client acceptance

This check uses the actual generated Rust library. It does not render a test copy.
Expected responses come from the frozen DFR-07 case and native source contract.
The native fixture uses Defer commit `5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0`.
Its Core dependency remains `029e765efaac4080bb9e8283005a9c6792953f54`.

The runner copies the pinned source into a new evidence directory.
It does not change or fetch the Defer repository.
All Cargo commands run offline with at most two build jobs.
Pass local caches that already contain each locked dependency.
The native and generated-client caches can be different.

Run this after the service compiler generates the contract fixture:

```sh
python3 som-service/acceptance/run.py \
  --generated-dir /absolute/path/to/generated/defer_status_client \
  --defer-repo /Users/chrischeng/faberlines/defer \
  --output /private/tmp/dfr07-client-acceptance \
  --cargo /opt/homebrew/Cellar/rust/1.96.1/bin/cargo \
  --native-cargo-home /private/tmp/sift-pr29-cargo.giYPjY \
  --client-cargo-home /private/tmp/faberline-nlp-cargo.bSa7KN
```

Use a new empty output directory for each run.
Optional `--native-target-dir` and `--client-target-dir` can reuse local build caches.
The runner verifies the complete generated source and lockfile before and after the build.
It records the resolved native dependencies, source hashes, binary hashes and commands.

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

A passing `report.json` reports 35 native HTTP cases, 10 native path edge cases,
105 generated-client cases and 48 captured requests.
This report covers the generated client only.
Compiler syntax, DSL, HTTP/CLI and unchanged old-compiler checks have separate reports.
