# DFR-07 local acceptance

The component checks passed on 2026-11-07 with Rust 1.96.1.
This change adds only `som-service/`. Existing worktree changes are preserved.
No SOM commit, push or merge is part of this delivery.

| Check | Result |
|---|---|
| Service compiler | 17 independent compiler tests and 3 JSON tests passed |
| Generated transport | 10 independent tests passed through the real renderer |
| Service HTTP and CLI | CNL/typed DSL parity, compile/assemble, malformed requests and old-format isolation passed |
| Generated client | 105 cases passed; 48 exact requests captured |
| Native Defer | 35 HTTP cases and 10 path edge cases passed |
| New NLP workspace | 36 tests passed: 29 old tests and 7 service parser tests |
| NLP compatibility | All 1,170 frozen sources produced the same API2 and Pest results |
| Old SOM suites | 106 SOM tests and 9 standalone CNL tests passed |
| Old output comparison | 1,930 records; zero differences and zero unexpected accepts/refusals |
| Original corpus | 178 cases, 890 records and 322 products retained |
| Old HTTP/CLI | All four code targets and saved-bundle contracts passed |
| Source guard | All 3,663 existing files and the block symlink remained unchanged |

The original comparison report still hashes to
`ffc59118460ae56f2fbc4131cc46848174bcf7c24f61b6475aad67d942077738`.
The original corpus still hashes to
`260bb1662225144c4044576a4c43e388e4ea6033a0157ffbddd0589a03e9ddf3`.

Evidence is retained under
`/Users/chrischeng/.codex/worktrees/b81a/som/.local/defer-task-status/`:

- `compiler-tests.log` and `service-all-tests.log`.
- `entrypoints-v2/report.json` and `entrypoints-v2/generated/`.
- `native-client-acceptance-v2/report.json` and `verified-proof.json`.
- `nlp/receipt.json`, `nlp/oracle-current.json` and the NLP test logs.
- `legacy-regression.json`, which points to the complete old comparison.
- `preserved-source.json` and the final combined `receipt.json`.

The initial two compiler test failures exposed error classification differences.
The new public JSON entry point now treats duplicate keys as INVALID_SCHEMA.
An altered saved-bundle version is INVALID_BUNDLE. Independent expectations
were retained. The initial native probe needed a smaller Tokio feature set for
offline resolution. Every native and generated-client oracle remained unchanged.

The runtime limits are deliberate. This version supports only GET task status.
It rejects empty, dot and dot-dot path segments, and accepts no path prefix in
the base URL. Redirects are returned as HTTP errors. HTTP200 with an invalid
status body is a decode error. This delivery does not claim automatic task
reclaim, successful target execution, or other Defer operations.

To rerun, use the command in [README.md](README.md). Each run makes a new
evidence directory. A final `receipt.json` is written only if every gate passes.
