# som

som has one supported path: `som-service` (Rust). Read `README.md` first.

## Frozen paths

- `som-rs/` is frozen. Do not edit it, and do not add syntax to
  `som-rs/contracts/compiler-v1/` (including `grammar.ebnf`).
- `som-rs` keeps its sealed nlp pin (`faberline-nlp-cnl` in `som-rs/Cargo.toml`)
  until the owner approves a re-pin in issue #180. Do not move that pin.
- `som-rs/evidence/` is recorded evidence. Do not regenerate or edit it to make a
  check pass.

## CNL syntax

CNL syntax belongs to `faberline/nlp`. A syntax change starts as an nlp issue.
After it merges, move the `faberline-nlp-cnl` revision in
`som-service/Cargo.toml`. som owns only CNL meaning, lowering and code generation.

## Verify

Run from `som-service/` (there is no root `Cargo.toml`):

- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets --locked -- -D warnings`
- `cargo test --locked`

CI runs the `som-rs` gates. Do not weaken or delete a test to make it pass.
