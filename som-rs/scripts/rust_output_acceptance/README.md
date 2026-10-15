# Rust output acceptance (#50)

Tools that run every check of `som-rs/contracts/rust-output-v1/acceptance-plan.json`
on one recorded build. Outputs go under `.local/rust-compiler-goal/acceptance/`
and are never overwritten.

1. `prepare_candidate.py --out DIR --port PORT` builds `som` from a clean tree with
   the pinned toolchain, records `source.json`, `source.tar` and `build.json`, and
   starts it on a QA port (never 8765 or 18765).
2. `vendor_crates.py --out DIR` builds the vendored crate set for the frozen
   reference `Cargo.lock` from the local cargo cache, offline. Crates that only
   other platforms need become stubs that fail if compiled.
3. `run_acceptance.py --candidate DIR --vendor DIR --out DIR` runs every step in
   order with the vendored crates and writes a receipt per step and `summary.json`.
   The tools must be committed first.

Steps: `check_contract.py --host`, `check_python_controls.py`, `validate_cases.py`,
`check_targets.py` (the default on 8765 is the read-only reference), `check_cli.py`,
`python_replay.py` (byte replay of the accepted 127 public and 544 legacy runs),
the asset index and `runtime_acceptance.py`, the behavior suite in python, faults
and parity modes, `check_rust_products.py` with the parity report, and
`perf_observe.py` (observations without limits).
