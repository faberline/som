# Rust compiler status — candidate 003

Status: implemented candidate; final acceptance pending.
Snapshot: 2026-10-15T00:54:10+08:00.
The human requested this source and progress landing before release acceptance.
See the [progress index](evidence/rust-compiler-progress-2026-10-15.json).
The accepted legacy M2 service remains the default. No switch is claimed.

## Saved candidate identity

Candidate directory: `.local/rust-compiler-goal/candidate-003`.
Binary SHA256: `3fad3cb7eafb35274f6a37f9a8a371f93807a6b5fedf3e9fe0e074cbf5bfef02`.
Source-receipt SHA256: `1e80e26f0128e99fe7fa3c4fcf6edae354e51bf75b13f3df70833107906d78bd`.
Canonical asset-manifest SHA256: `872eb4dc162e54c0cf4ada196652417161cffc3cb4a0382d987fc668f94d416e`.

The saved runtime receipt names port 18765 and reports model_loaded=false.
This document does not prove that PID is still live. Operator actions require
a fresh owner check. Final accepted install paths and default PID are pending.

## What is implemented

Rust parses the fixed English grammar and explicit JSON prompt v1–v7.
It selects approved blocks by fixed rules. It emits full typed bundles.
Standalone assemble validates saved bundles and their declared local assets.
The existing Rust renderer and pinned native formatter return all Python files.
The default build excludes model dependencies. Production does not start Python.

The compiler preserves typed scalar values and exact float-zero signs.
Pure literal predicates can produce Boolean constants with the same meaning.
Input and step references remain live expressions. Complete generated products
still require strict quality and independent behavior tests.

## Saved checks

Candidate 003 build commands exited 0. The source receipt stayed unchanged.
The saved runtime boundary gate passes 37 required checks with no unrun cases.
The gap gate passes 31 required checks with no unrun cases.
The frozen suite passes 119 CNL/JSON pairs, 40 typed cases, 15 CNL refusals,
11 canonical vectors, and 14 bundle cases. Nine cases remain unrun in that suite.
Separate runtime/gap receipts must prove their exact coverage before acceptance.

Candidate 003 passes all 101 diagnostic checks.
The fresh complete public-products run passes 127/127 after a reviewed checker
correction. The original 125/127 receipt and its two failures remain unchanged.
The full baseline run005 is active: 176/176 completed jobs pass, out of 544 required.
This snapshot has 0 failed jobs and 368 jobs still pending. Partial counts are not acceptance.
Run004 remains failed: 325 passed, one failed, one interrupted and 217 unrun.
Its original transport failure has no proven root cause. No rows are reused.
All 11 renderer compile pairs, strict-quality checks, and focused behavior checks
passed. The unchanged full-M2 outcomes remain separate below.
The performance observer completed 84 requests and full-response comparisons.
Latency and service-only memory are observations, not threshold gates.
Six isolated switch rehearsals passed. Recovery run002 ended with actual exit 2.
Seven scenarios met their expected outcomes. The SIGHUP scenario did not reach
the required post-stop barrier. The final already-stopped scenario is unrun.
All 88 recorded test PIDs were absent in the later root check. The original
cleanup uncertainty remains recorded. Independent failure review is pending.
Recovery run001 remains failed: two expected passes, one observer failure,
one interrupted case and five unrun cases. The corrected observer starts a new cohort.
Fresh final acceptance, live rollback/canary, default switch, final paths,
release publication and tracker closeout remain pending.
Implementation publication is a separate step and does not close these gates.
Candidate 002 partial and failed receipts remain history. They are not substitutes.

## Scope and known limits

The baseline inventory contains 544 assembly jobs and 540 distinct prompts.
Three duplicate groups retain different plans or job identities.
Formal 114 and development 12 are within quality 218. Supplement 12 is separate.
Original full behavior covers 126 quality products and 12 supplements.
The remaining 92 quality products retain quality/parity checks only.

The 11 renderer cases supplement the historical scope.
Fresh compile pairs, strict quality, and focused behavior each passed 11 cases.
The unchanged full-M2 checker passed only `REF_NULLABLE_OPTIONAL_FIELD`.
The other 10 cases reproduced their exact saved checker failures.
Independent receipt review confirmed this scoped result.
It does not claim 11 unchanged full-M2 passes.

The saved branch-null and empty-branch failures use a malformed conflict
request. The old checker inserts `parent.title` into a parent model that
accepts `name`. The service rejects the extra field before the expected 409.
Empty-field-segments fails with `KeyError` for an empty member name.
The branch-export case and six comparison cases fail with unresolved probes.
These exact failures stay visible. Focused checks do not turn them into passes.
The empty literal branch also has no reachable false arm.

PostgreSQL JSONB can normalize durable -0.0 to 0.0. Compiler and generated-literal
bit checks stay exact. Persisted runtime observations use the existing typed
numeric meaning for finite float zeros only. Every lost sign is recorded as a
representation limit. This is not a runtime bit-preservation pass.

The verified Drive archive permitted removal of 55 unused local files.
The 37 files required by acceptance or recovery stay local. About 481 MiB was reclaimed.
Read the [cleanup receipt](evidence/rust-training-data-archive-cleanup-2026-10-14.json).

Retained legacy model and block pointers stay unchanged. Old M3 automation is
paused. Old reports, raw evidence, models, and the protected worktree are preserved.
