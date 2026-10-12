# Business-service candidate

This candidate extends the unreleased `som.prompt.v5` contract.
The existing `current` model stays in place until all local gates pass.

The latest [scope direction](SCOPE_GROWTH.md) puts full business correctness first.
Performance numbers remain observations. The prior size, memory, and 100 ms
values do not limit capability growth. No automatic request splitting is planned.
The sealed paired comparison continues with its original token rules.

## What the local model does

The model selects eleven operation tokens: five parent operations, five child
operations, and one atomic batch operation. Its input includes the operation
sentence, resource role, database kind, and query kind.

The validator supplies typed names and values. Named slots place the operations.
Approved templates supply authentication, storage, schema, migration, and workflow
code. A wrong model choice is an error. The decoder does not correct the answer.
The service runs the model and assembler in Rust. It returns complete source.
It does not place files in the caller's project.

## Prompt

Each prompt includes `package`, `resources.parent`, `resources.child`, `relation`,
`logic`, `tests`, and `migration`. Each resource has a name, table, path, fields,
and query. Each field has a type, required flag, unique flag, and a default when
optional. There are one to eight custom fields per resource.

The `capabilities` object has these keys:

| Key | Values |
|---|---|
| database | `postgresql`, `mongodb` |
| auth | `jwt`, `firebase`, `google_oauth` |
| storage | `none`, `s3`, `gcs` |
| workflow | `json_export`, `csv`, `gzip_export`, `decimal_summary`, `statistics` |
| value_field | A numeric child field for summaries or statistics; otherwise null |
| group_field | A required string child field for decimal summaries; otherwise null |

The fixed operation sentences are defined in
[`related.py`](../som-core/src/som_core/related.py).
The frozen [application requirements](../som-core/data/business-service/requirements.jsonl)
give complete examples. A caller never supplies source code or block IDs.

## Application behavior

Parent CRUD uses its base path. Child CRUD is nested under the parent's ID.
Cross-parent access and missing parents return 404. A parent with children
cannot be deleted. It returns 409. Batch create commits one transaction.
A late unique conflict rolls back the parent and earlier children.

All business routes require an identity. A reader can read. An editor can write.
Each app has one shared workspace. This contract does not define tenant isolation.
OAuth uses Redis for expiring state and sessions. Writes require a CSRF token.

PostgreSQL uses SQLAlchemy and Alembic. MongoDB uses Beanie and actual replica-set
transactions. MongoDB migrations create the collections and indexes. Its downgrade
command is restricted to explicitly named isolated test databases.

Object routes have a 1 MiB limit. They use conditional creation and SHA-256.
An identical upload can be retried. A different body under the same key returns
409. Objects and application records have separate write operations.

The generated README lists configuration and startup steps for each app.
Test endpoint overrides require test mode and a loopback address.

## Test scope

There are ten standard business apps and four additional boundary applications.
The latter cover eight fields, all-optional fields, reordered operations and paths,
and both databases. They add fourteen generated applications to the prior hundred
regression tasks. Application counts and named behavior checks are separate.

The reference run passed all fourteen applications and 273 named checks.
Its receipt records exact source and verifier hashes. Early failed runs are kept.

| App | Database | Identity | Object store | Workflow |
|---|---|---|---|---|
| Projects and todos | PostgreSQL | JWT | None | JSON export |
| Catalogs and products | PostgreSQL | JWT | S3 mock | CSV import and export |
| Collections and bookmarks | PostgreSQL | Google OAuth mock | None | CSV import and export |
| Recipes and ingredients | PostgreSQL | JWT | None | Decimal totals by unit |
| Customers and contacts | PostgreSQL | Google OAuth mock | None | CSV import and export |
| Devices and inspections | PostgreSQL | Firebase mock | GCS mock | JSON export |
| Surveys and questions | MongoDB | Firebase mock | None | Gzip JSON export |
| Courses and lessons | PostgreSQL | Firebase mock | GCS mock | JSON export |
| Experiments and observations | MongoDB | JWT | None | Numeric statistics |
| Documents and reviews | PostgreSQL | Google OAuth mock | S3 mock | JSON export |

The data contains 600 new training cases and 100 development cases. The split
excludes name-free structural overlap. Experiments and documents are test app
names only. Existing training data remains in the combined corpus.
The model has two layers, width 64, four heads, context 256, vocabulary 256.
Seed 17 first reached all-correct development operation choices at step 4,000.

The [paired kit](evals/business-paired-2026-09-30-r3/README.md) seals 120 real CLI jobs.
It records all attempts, full source reads, tokens, elapsed time and available CLI
costs. Missing usage stays unknown. The old default changes only if all gates pass.
Initial CLI runs exposed defects in the verifier. Those runs are diagnostic.
The corrected run preserves the app requirements, model checkpoint, and limits.
It starts with fresh dialogues and work directories for both methods.

All 60 Codex services passed the frozen checks, transaction checks, and business
flow checks. Their final files and complete source-return records also passed.
Codex used 13,497,433 tokens directly and 14,221,471 with SOM. SOM used 5.36% more.
All ten app medians missed the 30% token saving target. Current stays on v4.
One Codex job exceeded the wall-clock budget while the Mac slept. Its result stays
included. See [the Codex report](evals/business-paired-2026-09-30-r3/codex-results.md).
Claude follow-up work is deferred after 52 completed jobs and eight interrupted
jobs. Its full comparison is unavailable. All attempts remain in the original report.

## Measured Rust performance

The [three-round receipt](evals/related-service/candidate-http-budget-apps.json)
contains five warmups and 114 complete HTTP requests per round.
Timing includes receipt and parsing of the complete response.
The largest group p95 across all rounds was 67.497 ms for the new applications.
The all-task p95 was 53.486 ms. The old groups were 31.276 ms and 33.943 ms.
The model directory was 5,195,771 bytes. Peak process resident memory was
142,180,352 bytes. These results met the three historical size and time references.
Those values are now observations, not current capability limits.
The old database regression also ran during the first timing round.

The receipt lists time, source size, and response size for every application.
These measurements describe this local workload. They do not establish cloud
latency or the best database index for other workloads.

## Cloud checks still pending

Local checks use real isolated PostgreSQL, MongoDB and Redis. S3 uses Moto behind
a local failure proxy. Firebase, Google OAuth and GCS use protocol mocks with
their real client SDKs. These checks do not establish real-cloud correctness.

Real Firebase revocation, Google consent and redirect setup, GCS IAM, and S3 IAM
and network behavior require separate cloud acceptance. This stage has no release
or deployment step.
