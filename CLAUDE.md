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

som owns its CNL parser, its syntax, its meaning, lowering and code generation.
A CNL syntax change is a som issue, not an nlp issue.
Until som#217 merges, `som-service` still pins `faberline-nlp-cnl` from the nlp
repo in `som-service/Cargo.toml`. Only som#217 moves it. Do not move that
revision in any other change.

## Verify

Run from `som-service/` (there is no root `Cargo.toml`):

- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets --locked -- -D warnings`
- `cargo test --locked`

CI runs the `som-rs` gates. Do not weaken or delete a test to make it pass.
