# Grow SOM's task scope through verified business flows

## Current priority

The 2026-09-30 direction makes correctness the first acceptance condition.
Test complete business behavior, permissions, errors, rollback, recovery, and
existing regressions. Valid DSL or successful startup alone is not acceptance.
A refusal within the supported scope is a failure.

Continue expanding the verified multi-app, identity, object storage, database,
and workflow capabilities. Measure resources and time before choosing limits.
The previous 100 ms, 50 MB, and 1 GB values do not block capability expansion.
A sub-second call is an exploratory value, not a new one-second hard limit.
The user separately reconfirmed the existing 30% paired-token gate for changing
the default model. It still applies per agent and per app. Resource observations
and this token switch rule are separate requirements.

## Performance measurements

The current Mac tools retain these previous values for comparison:

| Measure | Historical reference | Method |
|---|---:|---|
| Complete model directory, including weights, tokenizer, configuration, template, and manifest | 50 MB | Sum regular file sizes. One MB is 1,000,000 bytes. |
| Loaded service resident memory | 1 GB | Peak macOS resident bytes during the request run. One GB is 1,000,000,000 bytes. |
| Warm request latency | p95 <= 100 ms | One request at a time. Time from sending a supported SOM prompt to reading and parsing the full HTTP response. Five warmups precede the measured cases. |

Measure the loaded Rust service:

```sh
som-code-python/.venv/bin/python som-core/scripts/bench_service_budget.py --model som-rs/models/current --cases som-core/data/service-regression-v4/heldout.jsonl --output /private/tmp/som-current-budget.json
```

The tool checks that `/healthz` names the exact model manifest being measured.
Its default mode records comparisons without failing on these reference values.
`--enforce-limits` reproduces an explicitly requested threshold experiment.
The business evaluator also defaults to measurement only. Its
`--enforce-historical-budgets` switch reproduces the previous comparison gate.
An HTTP error, refusal, parity failure, or product failure still fails evaluation.
The same case corpus must pass independent correctness checks. Warm timing excludes
startup, multiple concurrent requests, flagship reasoning, and product tests.
Measure those separately when they become part of the service requirement.
Keep previous receipts intact. They describe the rules in force for those runs.

## Prompt and DSL

The flagship prompt should express the full requirement with few repeated words.
Reduce repeated operation sentences and settings through explicit, named contracts.
The flagship still decides business rules or explicitly selects their contract.

The local DSL has a different purpose. It must fully and precisely describe the
approved blocks, typed values, references, relationships, and order.
Its size does not add flagship tokens because the service consumes it locally.
The model can select operations and relationships. Rust can supply deterministic
facts and validate the complete DSL. Do not remove DSL detail to shorten the prompt.
The current HTTP response already returns complete source without returning DSL.
All returned source still counts in the flagship token comparison.

Do not automatically ask the flagship to split a request at this stage.
First record a clear bottleneck. Any later split must preserve common data models,
interfaces, and transaction rules, with an end-to-end business-flow check.

## Current boundary

The current default model weighs about 0.5 MB. The Rust decoder selects five ordered
API blocks from the supported CRUD families. A versioned DSL template supplies
the other files and blocks. The v1 through v4 formats require five exact logic
sentences and a fixed test sentence. Adding an approved block to the library
alone does not let the model use it. The model and block library hashes are
coupled in the manifest.
The business candidate selects eleven parent, child, and batch operations.
Its approved infrastructure templates supply identity, storage, and workflows.
See [the business contract](BUSINESS.md) for its measured state.

The v1 DSL accepts at most eight files and 64 block instances. The v2 DSL
accepts at most 12 files, 64 block instances, and 65,536 output bytes. A
latency number from a small CRUD response cannot be applied to an unbounded
package.

## What an approved block means

An approved block has a typed interface, a version, and passing correctness
tests for its supported use. Approval does not mean that one block is fastest
for every workload. Performance depends on data size, indexes, database
configuration, and the API contract.

The query block applies exact filtering, ordering, limit, and offset in SQL.
This bounds the returned page. The v3 migration adds a composite index on
the boolean filter and ID. A large offset can still make a database scan
costly. Treat it as a correct first query block, not a generally optimal
database plan. Before
calling a block performance qualified, compare it with a named baseline on
realistic PostgreSQL data, record query plans and p95 latency, and keep that
evidence with the block version. A faster variant may be a new block instead
of a silent change to an approved one.

## Expansion path

1. Define one new task family with a versioned prompt shape, typed slots,
   approved blocks, and an independent behavior test. The flagship specifies
   desired behavior. It does not select block IDs.
2. Train the model to choose the required operations and relationships. Keep source
   code in the approved block library. Record model size and context requirements.
3. Let Rust validate the predicted DSL, resolve typed prompt values, and
   assemble complete files. Reject unsupported combinations clearly.
4. Freeze new held-out tasks. Check the old and new task families together.
   Require valid Rust/Python parity and independent full business-flow checks.
   Record model size, idle/peak memory, complete response time, and flagship tokens.
5. Rerun paired Codex and Claude Code token comparisons for the new family.
   Larger returned code can erase token savings even when SOM runs quickly.

As the library grows toward thousands of blocks, keep candidate selection
bounded. Load or index only the blocks needed for a chosen family. This is an
architecture direction, not a current capability. Each new family needs its
own implementation and acceptance evidence.
