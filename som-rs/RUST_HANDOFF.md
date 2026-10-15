# Rust compiler handoff — 2026-10-14

The new Rust compiler passed independent acceptance and is the local default.
It was promoted at `2026-10-15T03:08:08.737006+00:00` and serves `127.0.0.1:8765` without loading a model.
The root post-switch check passed 76/76. Main publication and GitHub closeout are pending.
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

Remaining:

1. Publish approved source, documents, and indexes to main.
2. Close #38–#44 and milestones 7 and 8 after evidence review. Read GitHub back.
3. Save the AW handoff. Release the claim after all owned test cleanup is verified.

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
