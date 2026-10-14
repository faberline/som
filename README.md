# som

SOM has a new Rust compiler candidate. It has two stages:

`fixed English CNL → typed DSL bundle → existing Rust backend → Python files`

CNL is controlled natural language. It uses exact English clauses and block
delimiters. The calling agent supplies the business choices. Rust parses those
choices and selects approved blocks by fixed rules. The backend returns all
generated files. It does not write them into the caller's project.

The new path needs no model weights or Python production process. Existing
JSON prompt versions v1–v7 remain supported. Python is still the output language.
The existing Python quality and behavior checks remain required.

Candidate 003 is implemented. Its full independent acceptance is pending.
The saved runtime and gap checks pass. The fresh public-products run passes 127/127.
The full 544-job baseline is still running. Recovery run002 ended with a failure.
Seven recovery scenarios met their expected outcomes; one failed and one is unrun.
Read the [dated progress index](som-rs/evidence/rust-compiler-progress-2026-10-15.json).
This source landing does not claim release acceptance.
The accepted legacy M2 model service remains the default on port 8765.
No compiler default switch or new milestone closure is claimed.

Read [the current status](som-rs/RUST_STATUS.md),
[the compiler guide](som-rs/RUST_GUIDE.md),
[the roadmap](som-rs/RUST_ROADMAP.md), and
[the handoff](som-rs/RUST_HANDOFF.md).

## Historical model roadmap and retained legacy service

The following reports describe the previous model roadmap and retained M2
service. They do not instruct new M3 training.
The old M3 automation remains paused. Old failures and local assets stay saved.
The model milestone statements below are historical snapshots from before the
2026-10-13 roadmap reset. All six old milestone records were archived and deleted.
The [M1 closeout](som-rs/M1_CLOSEOUT.md) passed on 2026-10-02.
The rules and model paths produced the same files on 214 regression cases.
All 214 services and the old 100 PostgreSQL cases passed.
The [M2 closeout](som-rs/M2_CLOSEOUT.md) passed on 2026-10-05.
Issues #4–#6 and milestone 2 are closed on GitHub.
The development flow contract is `som.prompt.v7` / `som.dsl.v3`.
All 218 normal products pass strict quality and Python/Rust byte parity.
All 114 formal flow cases and 12 capability supplements pass full behavior.
All old 100 cases and 214 service regressions pass.
All 314 fresh native legacy outputs match the sealed archive.
All 12 seen-fixture model chains pass Rust Metal, HTTP and CLI checks.
These model checks prove the seen-input path. Full model training is M3 work.
Final output bytes are observations. File, block and token limits remain fixed.
See [the M3 handoff](som-rs/M3_HANDOFF.md).
The local development default is now the accepted M2 flow candidate.
The [switch receipt](som-rs/evals/m3-flow-2026-10-05/r1/default-switch-001/result.json)
passes 12 native flow chains and 314 exact legacy outputs.
M3 remains open. The r6 round uses the r10 dataset and v3 input bindings.
It has 2,400 training, 300 development and 300 formal cases.
All 2,700 training and development reference products passed admission.
Training stopped after an interruption. The last complete checkpoint is
step 74,000. Its raw development check passed 3/300 complete sequences.
The formal model test has not started. The M2 default pointers stay fixed.
See [the current M3 status](som-rs/M3_STATUS.md) for evidence and next tasks.

SOM is a local code generation service. Codex or Claude Code decides the
program logic and sends a versioned JSON prompt. The default accepts v1–v7.
M2 v7 model evidence covers twelve seen fixtures. New combinations need M3.
A local model turns that request into a versioned DSL. The Rust assembler expands approved code
blocks and returns complete files. The agent places and tests those files.

The service and CLI use Rust. Python trains the model and checks assembler
correctness. A production request does not start Python or write project files.

| Project | What it is |
|---------|------------|
| [som-rs](som-rs/README.md) | Rust HTTP service, model inference, CLI, DSL validation, and assembler. |
| [som-core](som-core/README.md) | Python training and reference assembler. Older experiments remain as research code. |
| [som-code-python](som-code-python/README.md) | Approved code blocks and the older Python corpus. |

The first target is a FastAPI CRUD package with SQLAlchemy, PostgreSQL,
unittest, and an Alembic migration. The local pilot model passed 50 frozen
PostgreSQL cases through the Rust service. Four paired tasks across all four
field shapes cut total flagship tokens by 59.5% for Codex and 54.6% for
Claude Code. Both exceeded the 30% target in this measured v1 scope. See
[the Rust service guide](som-rs/README.md).

The v2 task family adds exact string filtering and limit/offset pagination.
Its model kept the same small architecture and served the first two prompt
versions. It matched 100 frozen outputs byte for byte. All 50 new query tasks
passed independent PostgreSQL checks. The model is 0.532 MB, and the loaded
service's warm sequential p95 was 26.173 ms. See
[the query contract](som-rs/QUERY_V2.md) and
[the budget receipt](som-rs/evals/service-budget-query-v2-2026-09-29.json).

Four paired query tasks then passed independent PostgreSQL checks for both
agents and both methods. Codex used 47.9% fewer total tokens with SOM.
Claude Code used 57.5% fewer. This is a four-task sample, so savings can vary
by task. See [the paired receipt](som-rs/evals/flagship-query-paired-2026-09-29.json).

The earlier standalone service model generated FastAPI services with PostgreSQL,
Alembic, unittest, app startup, and instructions. The [Todo example](som-rs/examples/service-prompt.json)
has a default page size of 20 and a maximum of 100. All 50 new services passed
isolated PostgreSQL checks. All 150 old and new outputs matched the Python
reference byte for byte. The model is 0.538 MB. Warm request p95 was 48.209 ms
under the measured run. Four paired service tasks passed for both agents.
Codex saved 64.9% of total tokens; Claude Code saved 83.1%. See the
[v3 service contract](som-rs/SERVICE_V3.md) and
[paired receipt](som-rs/evals/flagship-service-v3-paired-2026-09-29.json).

An additional 50 new field combinations passed without retraining or changing
the blocks. The [stress evaluation](som-rs/SERVICE_V3.md#untrained-structural-stress-set)
measured 48.17 ms warm p95 and kept the same 0.538 MB model.

The [current development service](som-rs/CURRENT.md) adds an optional date field and an
exclusive `before` list filter. It matched 200 old and new outputs byte for
byte. All 50 new services passed isolated PostgreSQL checks. Its model
directory is 0.541 MB, and warm request p95 was 34.247 ms in that run.
The same v4 service also passed a [combined 100-case regression](som-rs/SERVICE_V4.md#previous-and-new-tasks-together):
the prior 50 structural stress tasks and the new 50 date tasks all passed
isolated PostgreSQL checks. `som serve` now loads this model and its 34-block
library by default. The completed date-task comparison measured 55.6% fewer
tokens for Codex and 75.7% fewer for Claude Code across four paired tasks each.
See [the date-task results](som-rs/evals/date-service-paired-2026-09-30/results.md).

The [business candidate](som-rs/BUSINESS.md) adds related resources, authentication,
object storage, MongoDB, and business workflows. Its fourteen standard and boundary
applications passed 273 local behavior checks through both Python and Rust.
The [120-job token comparison](som-rs/evals/business-paired-2026-09-30-r3/README.md)
used fresh actual Codex and Claude Code sessions. All 60 Codex services passed.
Codex used 13,497,433 tokens directly and 14,221,471 with SOM, an increase of 5.36%.
No app met the required 30% median saving. The previous model remains the default.
See [the Codex results](som-rs/evals/business-paired-2026-09-30-r3/codex-results.md)
and [the candidate work record](som-rs/evals/related-service/WORK.md).
Claude completed 52 services before interruption. Its follow-up work is deferred
by user request. All completed work and failed attempts are retained.
The latest [scope direction](som-rs/SCOPE_GROWTH.md) prioritizes complete business
correctness. Resource use and response time are measured before new limits are
chosen. Prompt wording should be concise; the local DSL remains complete.

The new [composable candidate](som-rs/COMPOSABLE.md) adds `som.prompt.v6`.
Its model emits the complete block plan with typed references and dependencies.
Its evaluation helper delivers full source in one tool result when it fits.
Its correctness checks passed. The latest twenty-job Codex round saved 58.21%
of total tokens and 88.51% of elapsed time. Nine apps met the token target.
Recipes saved 4.39%, so candidate acceptance did not pass. The current default
stays unchanged. See the [complete report](som-rs/evals/composable-2026-10-01/REPORT.md).

## License

MIT — see [LICENSE](LICENSE).

### M2 output size policy, 2026-10-04

The human changed compact M2 output bytes to an observation.
The file, block, token, quality and function gates remain fixed.
Legacy output behavior and `current` remain unchanged.
See [the acceptance ledger](som-rs/evals/m2-flow-2026-10-04/r31/ACCEPTANCE.md).
M2 has passed. See [the closeout](som-rs/M2_CLOSEOUT.md).
