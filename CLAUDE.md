# som

som has one supported path: `som-service` (Rust). Read `README.md` first.

## Frozen paths

- `som-rs/`, `som-core/` and `som-code-python/` are not in this repository. They are in the archive `faberline/som-archive`
  (source commit `47e8b3f573882c4726fdb26bca23d2bf138f7820`). They are read only
  there. Nobody restores them here or edits them, and nobody moves the nlp pin of the old compiler.

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

CI runs the gates of `.github/workflows/ci.yml`. Do not weaken or delete a test to make it pass.
