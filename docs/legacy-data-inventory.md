# Legacy data inventory

This lists only. It deletes, moves and edits nothing; the owner decides per group. Measured at `main` commit `67450ad`.

## Method

```text
du -sk som-core/data/* som-code-python/data/* | sort -rn
find som-core som-code-python -type f -size +1M -printf '%s %p\n' | sort -rn
# named by, per entry NAME (path form; the folder itself is excluded)
grep -rInE --exclude-dir=.git --exclude-dir=target 'som-core/data/NAME([^A-Za-z0-9_-]|$)' .
grep -rInE --exclude-dir=.git --exclude-dir=target 'som-code-python/data/NAME([^A-Za-z0-9_-]|$)' .
# embedded: include macros in the Rust sources
grep -rn 'include_\(str\|bytes\|dir\)!' som-rs/src som-service
# ci-test: a test that test-som-rs runs (not #[ignore]) must read the data
grep -n '#\[ignore\|cfg(' som-rs/tests/*.rs som-rs/src/*.rs
# frozen record: any hit in a frozen som-rs/ file (evidence, contracts, docs, scripts)
grep -rInE --exclude-dir=target 'som-core/data/NAME([^A-Za-z0-9_-]|$)' som-rs
grep -rInE --exclude-dir=target 'som-code-python/data/NAME([^A-Za-z0-9_-]|$)' som-rs
```

Classes, tested in this order: `embedded` (an include macro in `som-rs/src/` or `som-service/`), `ci-test` (a non-ignored test in `som-rs/tests/` or a `cfg(test)` module in `som-rs/src/`, both run by job `test-som-rs`; job `test-scripts` reads no legacy data), `qa-only` (named only by `som-core/scripts/`, `som-core/tests/` (not run by `ci.yml`), `som-rs/scripts/`, a `som-core/src` module, a doc or an evidence file), `unnamed` (no name outside the folder). MB = `du` KiB / 1024.

- The folder name alone is too generic (`v1`, `composed`), so the grep uses the path form.
- `som-rs/tests/related.rs:53` is inside an `#[ignore]` test behind `cfg(feature = "legacy-model")`. It is not a CI reader, so `business-service/train.jsonl` is `qa-only`, not `ci-test`.
- Line numbers are as measured at `67450ad`. The spec measured `073c73d`.

## Inventory

| Path | Size | Named by | Class | Frozen record? |
|---|---:|---|---|---|
| `som-code-python/data/README.md` | 0.0 MB | `som-code-python/README.md:81` (relative `data/README.md`, a doc) | qa-only | no |
| `som-code-python/data/block-contracts` | 0.3 MB | embedded: `som-rs/src/quality.rs:15`, `som-rs/src/flow.rs:12`, `som-rs/src/composed.rs:10` (include_str!) | embedded | n/a |
| `som-code-python/data/blocks` | 1.7 MB | embedded: `som-rs/src/compiler.rs:13`, `:14`, `:16` (include_bytes!) | embedded | n/a |
| `som-code-python/data/curated` | 20.0 MB | `som-core/src/som_core/dataset.py:311` | qa-only | no |
| `som-code-python/data/snippets` | 0.1 MB | `som-core/README.md:99` | qa-only | no |
| `som-code-python/data/user` | 0.0 MB | `som-code-python/data/README.md:152` | qa-only | no |
| `som-core/data/business-service` | 10.1 MB | CI test: `som-rs/tests/related.rs:33` (not ignored; reads requirements.jsonl and reference.jsonl); also named by evidence and scripts | ci-test | n/a |
| ↳ `som-core/data/business-service/train.jsonl` | 4.7 MB | only `som-rs/tests/related.rs:53`, inside an `#[ignore]` test behind `cfg(feature = "legacy-model")`: not run by CI; plus evidence | qa-only | yes, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:502` |
| ↳ `som-core/data/business-service/templates.json` | 4.4 MB | `som-core/scripts/build_composed_contracts.py:54`, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:497` | qa-only | yes, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:497` |
| `som-core/data/composed` | 9.3 MB | `som-rs/scripts/hosted_readiness/inputs/public-golden-hashes-v1.json:43` (217 hits), `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:123` (7 hits), `som-rs/scripts/hosted_readiness/evidence/public-inputs-2f8708ddce-independent-review.json:112` (3 hits) and 26 more | qa-only | yes, `som-rs/scripts/hosted_readiness/evidence/public-inputs-a7d37b-independent-review.json:40` |
| ↳ `som-core/data/composed/manifest.json` | 7.9 MB | `som-rs/COMPOSABLE.md:112`, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:517` | qa-only | yes, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:517` |
| `som-core/data/crud-query-service-v3` | 4.1 MB | `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:527` (4 hits), `som-core/scripts/build_service_stress_v3.py:109` (2 hits), `som-core/scripts/build_date_corpus_v4.py:24` and 3 more | qa-only | yes, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:527` |
| ↳ `som-core/data/crud-query-service-v3/train.jsonl` | 2.5 MB | `som-core/scripts/build_service_stress_v3.py:109`, `som-rs/README.md:146`, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:542` | qa-only | yes, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:542` |
| ↳ `som-core/data/crud-query-service-v3/heldout.expected.jsonl` | 1.1 MB | `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:527` | qa-only | yes, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:527` |
| `som-core/data/crud-query-v2` | 0.3 MB | `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:172` (4 hits), `som-core/scripts/eval_exact.py:23` (2 hits), `som-core/scripts/bench_service_budget.py:74` and 2 more | qa-only | yes, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:172` |
| `som-core/data/date-service-v4` | 2.0 MB | CI test: `som-rs/tests/parity.rs:135` (not ignored); also named by evidence and scripts | ci-test | n/a |
| ↳ `som-core/data/date-service-v4/train.jsonl` | 1.1 MB | `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:577` | qa-only | yes, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:577` |
| `som-core/data/flow-m3-r10` | 3.9 MB | `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:214` (4 hits), `som-core/tests/test_flow_m3_actual_trace.py:19` (2 hits), `som-core/tests/test_flow_m3_target.py:20` (2 hits) and 2 more | qa-only | yes, `som-rs/evidence/m3-2026-10-11-progress.json:7` |
| ↳ `som-core/data/flow-m3-r10/dev.jsonl` | 3.9 MB | `som-core/tests/test_flow_m3_actual_trace.py:19` (2 hits), `som-core/tests/test_flow_m3_target.py:20` (2 hits), `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:582` | qa-only | yes, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:582` |
| `som-core/data/flow-m3-r9` | 31.3 MB | CI test: `som-rs/src/flow_codec.rs:359` (cfg(test), not ignored); also named by evidence and a QA test | ci-test | n/a |
| ↳ `som-core/data/flow-m3-r9/train.jsonl` | 31.3 MB | CI test: `som-rs/src/flow_codec.rs:359` (test at line 356, not ignored) | ci-test | n/a |
| `som-core/data/flow-v7` | 0.4 MB | `som-core/scripts/freeze_flow_cases.py:13` | qa-only | no |
| `som-core/data/flow-v7-r10` | 0.9 MB | embedded: `som-rs/src/flow_model.rs:105`, `:212` (include_bytes!), `som-rs/src/flow_codec.rs:328` (include_str!, test module) | embedded | n/a |
| `som-core/data/flow-v7-r2` | 0.4 MB | nothing outside the folder | unnamed | n/a |
| `som-core/data/flow-v7-r3` | 0.4 MB | nothing outside the folder | unnamed | n/a |
| `som-core/data/flow-v7-r4` | 0.0 MB | nothing outside the folder | unnamed | n/a |
| `som-core/data/flow-v7-r5` | 0.4 MB | nothing outside the folder | unnamed | n/a |
| `som-core/data/flow-v7-r6` | 0.4 MB | nothing outside the folder | unnamed | n/a |
| `som-core/data/flow-v7-r7` | 0.7 MB | `som-core/scripts/finalize_m1.py:80`, `som-rs/M1_CLOSEOUT.md:115` | qa-only | yes, `som-rs/M1_CLOSEOUT.md:115` (path) |
| `som-core/data/flow-v7-r8` | 0.4 MB | nothing outside the folder | unnamed | n/a |
| `som-core/data/flow-v7-r9` | 1.5 MB | `som-core/scripts/finalize_flow_draft.py:96` | qa-only | no |
| `som-core/data/flow-v7-unsealed-001` | 0.0 MB | nothing outside the folder | unnamed | n/a |
| `som-core/data/query-v2` | 0.5 MB | CI test: `som-rs/tests/parity.rs:70`, `:72`, `:181` (not ignored); also named by evidence and scripts | ci-test | n/a |
| `som-core/data/related-service` | 5.8 MB | CI test: `som-rs/tests/related.rs:7` (not ignored); also named by evidence and scripts | ci-test | n/a |
| ↳ `som-core/data/related-service/train.jsonl` | 4.4 MB | `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:622` | qa-only | yes, `som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json:622` |
| `som-core/data/service-regression-v4` | 1.6 MB | CI test: `som-rs/tests/parity.rs:112`, `:115` (not ignored); also named by hosted_readiness scripts (not CI) | ci-test | n/a |
| ↳ `som-core/data/service-regression-v4/heldout.expected.jsonl` | 1.2 MB | CI test: `som-rs/tests/parity.rs:115` (test at line 110, not ignored) | ci-test | n/a |
| `som-core/data/service-stress-v3` | 0.8 MB | `som-rs/SERVICE_V3.md:63` (link to `manifest.json`), `som-core/scripts/build_date_corpus_v4.py:26`, `som-core/scripts/build_service_regression_v4.py:12`, `som-core/scripts/build_service_stress_v3.py:21` and 3 more | qa-only | yes, `som-rs/SERVICE_V3.md:63` (path) |
| `som-core/data/service-v3` | 0.8 MB | CI test: `som-rs/tests/parity.rs:91`, `:93`, `:159` (not ignored); also named by evidence and scripts | ci-test | n/a |
| `som-core/data/v1` | 0.4 MB | CI test: `som-rs/tests/parity.rs:51`, `:53` (not ignored); also named by evidence and scripts | ci-test | n/a |

## Summary

| Class | MB | Rows |
|---|---:|---:|
| embedded | 3.0 | 3 |
| ci-test | 52.5 | 8 |
| qa-only | 41.1 | 12 |
| unnamed | 2.2 | 7 |
| **Total** | **98.8** | **30** |

Total check: `som-core/data` 76.7 MB plus `som-code-python/data` 22.2 MB. The exact total is 101180 KiB, which is 98.8 MB. The rounded parts add to 98.9 because of rounding. The `↳` file rows are not counted again.

## Not in this list

Code, scripts, tests, locks and docs in `som-core/` and `som-code-python/` (about 5 MB) are out of scope.
