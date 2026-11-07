# Rust compiler handoff — 2026-10-22

> Current direction — 2026-10-28: fixed algorithms, `CNL → DSL → Code`.
> No model training, model weights or Apple chip is required.
> Develop locally. Run builds and tests on GitHub Actions runners.
> Homebrew paths and binary hashes below record older accepted tools.
> M12 owns their platform adaptation. Historical results are not a fresh portable acceptance run.
> Read [the current architecture](../docs/architecture.md).

## Current default: TypeScript BE phase 1 (#65)

The default on `127.0.0.1:8765` is TypeScript candidate-001 since `2026-10-22T13:55:00.006599+00:00`. It is released as [v0.5.0](https://github.com/faberline/som/releases/tag/v0.5.0) (tag at `8c4a91fbb5`, notes only).
Binary SHA-256: `2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c`, built from commit `8c4a91fbb5`.
`som-rs/runtime/current` points to `runtime/versions/2bceb3a0…`. `compiler-assets/current` still points to `compiler-assets/versions/d95d93c7…`.
The Python backend stays `581ce38f…`, so Python and Rust product files and bundles are byte-identical to candidate-007.
The [release evidence](evidence/typescript-output-release-2026-10-22-issue65.json) binds TypeScript acceptance run-003 and its independent audit-003 (0 failed, 0 unrun),
the release inputs (inputs-002), rehearsal-003, the approval, the switch journal, the post-switch check and a post-switch TypeScript product test.

What changed for callers:

- `--target typescript` writes a Node.js 26.5 + Hono + `pg` product for prompt versions 1 to 5 with PostgreSQL. See [docs/typescript-be.md](../docs/typescript-be.md).
- Versions 6 and 7, v5 capabilities and the other scope.json refusals answer 422 UNSUPPORTED_TARGET for `target typescript`.

The post-switch check passed 6/6 checks with the Python canaries post-switch-smoke12 and post-switch-legacy100, and the Rust and TypeScript canaries example-v1 and example-v5.

### Restarting 8765 by hand

TypeScript assemble reads the pinned prettier tree from `SOM_TS_PRETTIER`. `runtime/default.json` records the command but not the environment.
The release tools set the variable from the prerequisites `typescript_formatter` (#123). A manual restart of 8765 must set it too, or TypeScript assemble answers 503 ASSET_UNAVAILABLE:

```sh
SOM_TS_PRETTIER=$PWD/.local/typescript-output/prettier-001/node_modules/prettier $PWD/som-rs/runtime/versions/2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c/som serve --assets $PWD/som-rs/compiler-assets/versions/d95d93c764d95373480b580cfa672de91c55f8f06a5446a786bff61395aeccaa --port 8765
```

This is the `command` in `runtime/default.json`, run from the repository root.

### Recovery command

To put candidate-007 (`350369aa…`) back, write a fresh approval with action `restore-default-8765-rust-output`
bound to the current `default.json`, then run from the repository root:

```sh
som-rs/runtime/acceptance-m3-venv/bin/python som-rs/scripts/rust_output_release/switch.py --prerequisites $PWD/.local/typescript-output/release-065/inputs-002/prerequisites.json --root $PWD --port 8765 --restore $PWD/.local/rust-compiler-goal/acceptance/typescript-065/switch-001 --approval FRESH_APPROVAL.json --output FRESH_ABSOLUTE_DIR
```

The #65 rehearsal (rehearsal-003) qualified this restore on a harness port.
Then run `scripts/rust_output_release/check_default.py` with `--expect-binary-sha256 350369aa3f84aeed470d8f076418622fba9a65a9907b81e88ff868620fd1788e`.
After that restore, `target typescript` answers 501 or 422 again; Python and Rust output does not change.

## Previous default: v0.2.0, `som write` (#81)

The default on `127.0.0.1:8765` was rust-output-candidate-007 from `2026-10-21T17:09:02.500328+00:00` until `2026-10-22T13:55:00.006599+00:00`, released as [v0.2.0](https://github.com/faberline/som/releases/tag/v0.2.0).
Binary SHA-256: `350369aa3f84aeed470d8f076418622fba9a65a9907b81e88ff868620fd1788e`, built from commit `b891143d8f`.
`som-rs/runtime/current` points to `runtime/versions/350369aa…`. `compiler-assets/current` still points to `compiler-assets/versions/d95d93c7…`.
The Python backend stays `581ce38f…`, so Python and Rust product files and bundles are byte-identical to candidate-006.
The [release evidence](evidence/rust-output-release-2026-10-22-issue81.json) binds the acceptance run and its independent audit (16 passed, 0 failed, 0 unrun),
the `som write` smoke (16/16 examples), a v3 product tested on a local PostgreSQL, the rehearsal, the approval, the switch journal and the post-switch check.

What changed for callers:

- `som write <file.cnl> --out <dir>` compiles, assembles and writes a product directory in one command (#79). See [docs/python-be.md](../docs/python-be.md) (#84).
- `target typescript` is selectable; it answers 501 NOT_IMPLEMENTED for v1–v5 and 422 UNSUPPORTED_TARGET for v6/v7.

The post-switch check passed 6/6 checks with the Python canaries post-switch-smoke12 and post-switch-legacy100 and the Rust canaries
example-v1, example-v5, example-v6, example-v7 and CAP_MONGODB_FIREBASE_GCS_CSV.

### Recovery command for #81

To put candidate-006 (`52658e13…`) back, write a fresh approval with action `restore-default-8765-rust-output`
bound to the current `default.json`, then run from the repository root:

```sh
som-rs/runtime/acceptance-m3-venv/bin/python som-rs/scripts/rust_output_release/switch.py --prerequisites $PWD/.local/rust-compiler-goal/acceptance/release-020/inputs-001/prerequisites.json --restore $PWD/.local/rust-compiler-goal/acceptance/release-020/switch-002 --approval FRESH_APPROVAL.json --output FRESH_ABSOLUTE_DIR
```

The #81 rehearsal qualified this restore on a harness port.
Then run `scripts/rust_output_release/check_default.py` with `--expect-binary-sha256 52658e13af1ac08659ef1dda7e333634f4ae3893731304e09edec6ca5f9b87c2`.
After that restore, `som write` is gone from the installed CLI; Python and Rust output does not change.

## Previous default: Python self-name fix (#57, #58)

The default on `127.0.0.1:8765` was rust-output-candidate-006 from `2026-10-20T08:39:04.671848+00:00` until `2026-10-21T17:09:02.500328+00:00`.
It serves the Python target and the phase 2 Rust target. Binary SHA-256: `52658e13af1ac08659ef1dda7e333634f4ae3893731304e09edec6ca5f9b87c2`, built from commit `6116fed549`.
`som-rs/runtime/current` points to `runtime/versions/52658e13…`. `compiler-assets/current` points to `compiler-assets/versions/d95d93c7…`.
The Python backend is `581ce38f…`; its compiler embeds `som-code-python/data/blocks/flow-v7-compact-self/blocks.json` (358 blocks).
The M2 library `flow-v7-compact/blocks.json` keeps the accepted M2 bytes (`5a759715…`), so the M2 rollback health still matches (compiler-v1 amendment 004).
The [release evidence](evidence/rust-output-release-2026-10-20-issue57.json) binds the issue57 acceptance and audit, the canaries, the rehearsal, the approval, the switch journal and the post-switch check.

What changed for callers:

- A Python resource field or foreign key named `self` now builds and runs (#57). compiler-v1 selects a `self` variant block only for those prompts.
- compiler-v1 refuses a name that collides with a generated Python identifier with INVALID_VALUE (HTTP 400,
  `prompt.<path>: name collides with a generated Python identifier`); `selection-policy.json` `python_name_collisions` lists the names (#58).
  A v3 filter field named limit or offset is one of them, so the Rust target no longer answers UNSUPPORTED_TARGET.
- Product files of every other prompt are byte-identical. The bundle asset descriptors and payload hash changed
  (rust-output-v1 amendment 004), so a bundle compiled by an earlier default gets ASSET_HASH_MISMATCH: compile it again.

The #57 canaries:

- Python: post-switch-smoke12 and post-switch-legacy100, 7 steps each, and SELF_V5_PARENT_FIELD, SELF_V6_MONGODB_FOREIGN_KEY and SELF_V7_PG_FOREIGN_KEY, 5 steps each;
- Rust: example-v1, example-v5, example-v6, CAP_MONGODB_FIREBASE_GCS_CSV and example-v7, 5 steps each;
- M2: the two Python flow and legacy cases on a fresh legacy M2 clone on a harness port.

### Recovery command

To put the #56 default (`d2de7522…`) back, write a fresh approval with action `restore-default-8765-rust-output`
bound to the current `default.json`, then run from the repository root:

```sh
som-rs/runtime/acceptance-m3-venv/bin/python som-rs/scripts/rust_output_release/switch.py --prerequisites .local/rust-compiler-goal/acceptance/rust-output-057/inputs-002/prerequisites.json --restore $PWD/.local/rust-compiler-goal/acceptance/rust-output-057/switch-001 --approval FRESH_APPROVAL.json --output FRESH_ABSOLUTE_DIR
```

It checks the exact owner before the signal, restores `runtime/current`, starts the saved previous command,
runs the Python canaries and writes a restored receipt. The #57 rehearsal qualified it on a harness port.
Then run `scripts/rust_output_release/check_default.py` with `--expect-binary-sha256 d2de75223496665cd563b51daa51741faa337c9cc2d46d8af8cc596b9f399b9b`.
After that restore, prompts with a `self` field answer 500 again and bundles from candidate 006 get ASSET_HASH_MISMATCH.
Going back further to `05f1bfe6…` after a #57 restore needs its own rehearsal first; it has not been rehearsed.

### Known risks

- Rust assemble runs rustfmt from `/opt/homebrew/Cellar/rust/1.96.1` (see `contracts/rust-output-v1/toolchain.json`).
  Health reports its SHA-256 under `rust_target`. If a Homebrew upgrade removes that version, Rust assemble answers ASSET_UNAVAILABLE.
  Python output is not affected. Pin or reinstall that toolchain before upgrading. Run `python3 som-rs/scripts/rust_toolchain/check_toolchain.py` before and after upgrading Homebrew to verify that every pinned Rust binary is still present and matches its SHA-256.
- Phase 2 products build offline only with vendored crates. The #55 and #57 runs used `.local/rust-output-phase2/vendor-002`.
  A fresh machine needs that directory or network access to crates.io. Run `python3 som-rs/scripts/rust_vendor/check_vendor.py` before running phase 2 builds or acceptance to verify that the vendored crate tree is intact and matches the frozen `Cargo.lock` files.
- The M2 check before the switch is a fresh legacy M2 clone on a harness port. The #44 post-commit M2 restore tool is bound to the default-switch-001 lineage and was not rerun.

### som-core tests

`som-core/.venv` lacks fastapi. Run the som-core tests offline with the M3 acceptance venv instead:
`cd som-core && PYTHONPATH=src ../som-rs/runtime/acceptance-m3-venv/bin/python -m pytest -q tests`.
When the local M3 eval archive `som-rs/evals/m3-flow-2026-10-05/r4/predecessor-source-001/` is not present, `test_flow_m3_input.py::test_all_old_input_bytes_and_output_tokens_are_preserved` skips and the entire `som-core` test suite passes (679 passed, 1 skipped).

## Rust output phase 2 default (#56) — 2026-10-20

From `2026-10-20T03:14:10.975162+00:00` to the #57 switch, the default on `127.0.0.1:8765` was rust-output-candidate-004.
It served the Python target and the phase 2 Rust target. Binary SHA-256: `d2de75223496665cd563b51daa51741faa337c9cc2d46d8af8cc596b9f399b9b`, built from commit `69bb1fe682`.
Its assets were `872eb4dc…` and its Python backend `1c182570…`.
The [release evidence](evidence/rust-output-release-2026-10-20.json) binds the #55 acceptance and audit, the canaries, the rehearsal, the approval, the switch journal and the post-switch check.

The switch tools are in `scripts/rust_output_release/`. They load `scripts/compiler_default_switch.py` by its SHA-256 and do not change it.
The live switch needs an approval file that binds the candidate, the script hashes, the prerequisites and the current `default.json`.
`prepare_inputs.py` takes the Rust canaries by name (`--rust-cases`), builds and runs the phase 2 ones on the local emulators, and can pin the Python canaries from a stand-in of the current default on a harness port (`--stand-in`), so 8765 gets no request before the approval.

The #56 canaries:

- Python: post-switch-smoke12 (flow) and post-switch-legacy100 (legacy), 7 steps each;
- Rust: example-v1, example-v5, example-v6 (v6 capabilities on PostgreSQL), CAP_MONGODB_FIREBASE_GCS_CSV (v6 capabilities on MongoDB) and example-v7 (v7 flow with undo), 5 steps each;
- M2: the same two Python cases on a fresh legacy M2 clone on a harness port.

### Recovery command for #56

To put the phase 1 Rust output default (`05f1bfe6…`, #51) back while `d2de7522…` is the default, write a fresh approval with action `restore-default-8765-rust-output`
bound to the current `default.json`, then run from the repository root:

```sh
som-rs/runtime/acceptance-m3-venv/bin/python som-rs/scripts/rust_output_release/switch.py --prerequisites .local/rust-compiler-goal/acceptance/rust-output-056/inputs-002/prerequisites.json --restore $PWD/.local/rust-compiler-goal/acceptance/rust-output-056/switch-001 --approval FRESH_APPROVAL.json --output FRESH_ABSOLUTE_DIR
```

It checks the exact owner before the signal, restores `runtime/current`, starts the saved previous command,
runs the Python canaries and writes a restored receipt. The #56 rehearsal qualified it on a harness port.
Then run `scripts/rust_output_release/check_default.py` with `--expect-binary-sha256 05f1bfe6eef7045f054b1323a193e8f7bea1b4afe4f287a47cd0d0f9347ae11c`.
The #51 command below was qualified while `c315c279…` was `default.json`. Going back further to `3fad3cb7…`
after a #56 restore needs its own rehearsal first; it has not been rehearsed.
The legacy M2 rollback assets below stay in place. Before #56 the switch proved a fresh legacy M2 clone on a harness port again.

The rustfmt and vendored crate risks of #56 still apply; see [Known risks](#known-risks).
#57 was not done in #56; the section above records its fix.

## Rust output phase 1 default (#51) — 2026-10-16

From `2026-10-16T05:54:52.297481+00:00` to the #56 switch, the default on `127.0.0.1:8765` was rust-output-candidate-003.
It served the Python and phase 1 Rust targets. Binary SHA-256: `05f1bfe6eef7045f054b1323a193e8f7bea1b4afe4f287a47cd0d0f9347ae11c`.
The [release evidence](evidence/rust-output-release-2026-10-16.json) binds the acceptance run, audit, canaries, rehearsal, approval, switch journal and post-switch check.

### Recovery command for #51

To put the compiler default before it (`3fad3cb7…`) back while `05f1bfe6…` is the default, write a fresh approval with action `restore-default-8765-rust-output`
bound to the current `default.json`, then run from the repository root:

```sh
som-rs/runtime/acceptance-m3-venv/bin/python som-rs/scripts/rust_output_release/switch.py --prerequisites .local/rust-compiler-goal/acceptance/rust-output-051/inputs-001/prerequisites.json --restore $PWD/.local/rust-compiler-goal/acceptance/rust-output-051/switch-001 --approval FRESH_APPROVAL.json --output FRESH_ABSOLUTE_DIR
```

It checks the exact owner before the signal, restores `runtime/current`, starts the saved previous command,
runs the Python canaries and writes a restored receipt. The rehearsal qualified it on a harness port.
Then run `scripts/rust_output_release/check_default.py` with `--expect-binary-sha256 3fad3cb7eafb35274f6a37f9a8a371f93807a6b5fedf3e9fe0e074cbf5bfef02`.
The legacy M2 rollback assets below stay in place. Their #44 post-commit restore is bound to the default-switch-001 lineage;
before #51 the switch proved a fresh legacy M2 clone on a harness port instead.

The rustfmt pin risk of #51 still applies; see [Known risks](#known-risks).

## Compiler release (#38–#44) — 2026-10-14

The new Rust compiler passed independent acceptance and is the local default.
It was promoted at `2026-10-15T03:08:08.737006+00:00` and serves `127.0.0.1:8765` without loading a model.
The root post-switch check passed 76/76. Commit `3ced191137` published it to main.
Issues #38–#44 are closed as completed, and milestones 7 and 8 are closed.
The legacy M2 runtime, model and block links remain saved for rollback.

`CNL → Rust parser and fixed rules → complete DSL bundle → existing Rust backend → Python files`

CNL uses fixed English clauses. Data strings can include Chinese.
The caller supplies each business choice. The compiler does not guess missing facts.
The new production path does not load models or start Python.
Python remains the output language and an independent test tool.

Candidate binary SHA-256: `3fad3cb7eafb35274f6a37f9a8a371f93807a6b5fedf3e9fe0e074cbf5bfef02`.
Source receipt SHA-256: `1e80e26f0128e99fe7fa3c4fcf6edae354e51bf75b13f3df70833107906d78bd`.
Canonical asset manifest SHA-256: `872eb4dc162e54c0cf4ada196652417161cffc3cb4a0382d987fc668f94d416e`.
Raw asset manifest file SHA-256: `d215d30b5ab379c0a7ff2b5191393e39106a722403863181c77d2b5ea2a7c020`.
Independent audit SHA-256: `071c5ddc09e6f879ab2079cdcfa6ab6f75a60040efbcb2516568a1431b11b792`.
Independent audit completed at `2026-10-15T02:47:46.475001+00:00`.
The fixed evidence dates identify the cohort, not the acceptance completion date.
The independent audit passed 1215 checks, with zero failures or unrun checks.
Its counts include evidence-integrity checks. They are not all product test cases.
The first final audit run with the original auditor failed because it read newline-terminated raw base64 files strictly.
That run failed the legacy raw-response check, the discharge check derived from it, and the additive check that requires the original audit to pass.
That failed output is preserved. The amended auditor ignores ASCII whitespace only and adds negative tests; its qualification is recorded with the audit execution.

## Release work

Done: the acceptance receipt sealed the audit and all 11 document hashes.
Done: switch prerequisites bound those receipts and fresh process identities.
Done: the qualified switch committed with a new evidence directory.
Done: the root check verified the live default, both current links, asset hashes, and full HTTP/CLI responses.

Done: commit `3ced191137` published approved source, documents, and indexes to main.
Done: #38–#44 were closed as completed and milestones 7 and 8 were closed. GitHub was read back.

After this documentation commit is pushed, root seals the final local closeout receipt.
Root then saves the AW handoff and releases the claim after owned test cleanup is verified.

## Switch and rollback

The qualified controller is `scripts/compiler_default_switch.py`.
Its SHA-256 is `289a8470c5de2a183b01c8a138ecb4bc6cfe80a624f0291b32b779cc854bfb12`.
Use a newly validated prerequisite file and a fresh absolute output directory.
The controller checks the exact process owner before any signal.
It tests a fresh M2 restore clone and a fresh compiler canary before changing the default.
It installs immutable runtime and compiler-asset versions, then changes both current links.
On an admitted failure, it restores the saved M2 process and selectors and checks full replies.
The six isolated recovery scenarios qualify this behavior.

The active default is the compiler. If a later check fails, restore M2 with the post-commit tool.
The separate post-commit restore and read-only checker are qualified by the nine-variant evidence in the acceptance index.
Their exact reviewed source paths and hashes are bound in its `postcommit_m2_restore_qa` graph.
Create a fresh SHA-bound request from the committed compiler receipt and the saved `0001-prerequisites.json` and `0002-legacy-default.json`.
Run the reviewed restore tool with `--request PATH --request-sha256 SHA --output FRESH --execute --approval-label LABEL`.
Run its separate checker with `--request PATH --request-sha256 SHA --restore-output PATH --evidence-sha256 SHA --output FRESH --execute`.
Verify exact owner and selector state before authorizing either action. Preserve actual waits and complete raw output.
Expected refusal and signal cases remain failed/nonzero restore results. The QA pass measures their expected outcomes.

The retained M2 launch command is:

```sh
/Users/chrischeng/faberlines/som/som-rs/runtime/versions/14cf0a67fbcc943639c9e0efb661d6aaf0de7b00289c05fc52819f9e97f239d6/som serve --model /Users/chrischeng/faberlines/som/som-rs/models/current --blocks /Users/chrischeng/faberlines/som/som-code-python/data/blocks/current/blocks.json --port 8765
```

A controller may use this command only after verifying the current compiler owner,
confirming that it stopped, and proving that port 8765 is free.
Do not signal a process from an old PID alone.
There is no `--rollback` flag on the switch tool.
Do not rerun old pre-switch prerequisites after promotion.
Preserve the old default snapshot, canary inputs, model and block links for recovery.

The model link remains `flow-m2-candidate/smoke-r31-001`.
The block link remains `flow-v7-compact`.
The new runtime and compiler-asset links are separate from those rollback assets.

## Evidence and retained work

Read [the guide](RUST_GUIDE.md), [the roadmap](RUST_ROADMAP.md),
[the report](RUST_REPORT.md), and [the handoff](RUST_HANDOFF.md).
The [acceptance index](evidence/rust-compiler-acceptance-2026-10-14.json)
binds the candidate, source, tools, raw local receipts, and independent audit.
The [criterion map](evidence/rust-compiler-criteria-2026-10-14.json)
links all 77 issue requirements to their evidence.
Models, source archives, runtime assets, and raw measurements stay local.

The old M3 heartbeat remains paused. Do not resume model training.
Keep all models, raw records, source archives, and the original seven changes in
`/Users/chrischeng/.codex/worktrees/76af/som`.
The controller still owns its claim while acceptance writes are in progress.
