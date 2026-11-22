# som

som turns CNL or typed JSON into the DSL, then into a Rust client and an axum server. The one supported path is `som-service` (Rust). Read `README.md` first.

## Output style

- Write in the user's language. Keep paths, commands and exact output contracts intact.
- Use short sentences, active voice, and one idea in each sentence.
- Report what changed, what passed, what is unknown and the next action.

## Scope

- `som-rs/`, `som-core/` and `som-code-python/` are not in this repository. They are in the archive `faberline/som-archive` (source commit `47e8b3f573882c4726fdb26bca23d2bf138f7820`). They are read only there. Nobody restores them here or edits them, and nobody moves the nlp pin of the old compiler.
- som owns its CNL parser, its syntax, its meaning, lowering and code generation. A CNL syntax change is a som issue, not an nlp issue.
- Until som#217 merges, `som-service` still pins `faberline-nlp-cnl` from the nlp repo in `som-service/Cargo.toml`. Only som#217 moves it. Do not move that revision in any other change.
- This repository is public. No file, issue or pull request holds a secret, a token, a private host name, an e-mail address or a personal path.

## Issue work

- Work on a claimed issue only through the `aw` CLI: `aw claim`, the work, `aw report`. `aw` does the branch, push, pull request, merge and signal comment.
- Edit labels only in the plan step of the `aw-worker` skill.

## Verify

Run from `som-service/` (there is no root `Cargo.toml`):

- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets --locked -- -D warnings`
- `cargo test --locked`
- `cargo deny --locked check`

CI runs the stages of `.github/workflows/ci.yml`; the commands of its jobs are in `.github/ci/jobs.yaml`. The first two lines above are the `fmt-clippy` job there. `cargo test --locked` runs the tests that the `coverage` job runs through `cargo llvm-cov`. `cargo deny --locked check` is the `audit` job. Do not delete or weaken a test to make it pass.
