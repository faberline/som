# Python quality candidate

> Historical research or model-era record. The current path uses fixed algorithms: `CNL → DSL → Code`.
> It needs no model training, model weights or Apple chip.
> The plans, default claims and commands below describe their original period.
> They do not direct current training or replace current compiler acceptance.
> Read [the current architecture](../docs/architecture.md).

This is a separate development candidate. `current` stays unchanged.
The original composed model, blocks, outputs and failed runs stay archived.
See [the evidence folder](evals/composed-quality-2026-10-01).
Quality and functional acceptance passed for all 214 new service cases.
The old 100 PostgreSQL cases also passed, with unchanged final source.
Rust and Python matched on 20,814 valid outputs and 25 rejection cases.
The latest SOM-only round passed all ten apps with each agent.
It uses fixed direct records. No direct job ran again.
Codex saved 94.95% across ten apps. Each app passed the 30% token gate.
Claude saved 96.98% across nine apps with qualified direct records.
Claude Experiment passed SOM checks, but its direct comparison stays unknown.
The complete ten-app Claude token gate remains unproven.
See [the latest report](evals/fixed-baseline-som-2026-10-01/r1/REPORT.md).
The [earlier stopped round](evals/composed-quality-2026-10-01/REPORT.md)
and its three complete pairs remain archived.
See [the correctness receipt](evals/composed-quality-2026-10-01/candidate-correctness.json).

## Fixed contract

The contract is `som.python-quality.v1`. The generated `pyproject.toml`
contains its rules, tool versions, dependency hash and exception registry.
The [contract](contracts/python-quality-v1/contract.md) describes the scope.

| Tool | Version | Required result |
|---|---|---|
| Ruff | 0.16.6 | Format, imports and all selected rules pass |
| mypy | 2.3.1 | Strict checks pass; explicit and unresolved Any are restricted |
| Pylint | 4.0.9 | Default rules pass, with documented symbol exceptions |
| isort | 9.0.2 | Import order passes |

All app code, tests and migrations use Python 3.12 and explicit types.
Python lines use at most 79 characters. Indentation uses four spaces.
SDK data crosses a validating boundary before business code uses it.
The [exception registry](contracts/python-quality-v1/exceptions.json)
names each allowed symbol, rule, reason and verification case.
Whole-file skips and uncoded ignores are rejected.

## Rust request path

1. Decode the model's complete block plan.
2. Check its contracts, bindings and dependencies.
3. Assemble version 2 quality blocks and their import metadata.
4. Sort imports with the pinned native Ruff executable.
5. Format with Ruff.
6. Check all fixed Ruff rules without further fixes.
7. Check the final file count and UTF-8 byte limit.
8. Return the complete final source.

The request path does not start Python. It feeds source to native Ruff
through standard input. Only import sorting and formatting may fix source.
Other errors require a block correction.

Startup checks the formatter, configuration and model file hashes.
It also checks the formatter version. Missing or mismatched assets stop
startup. A failed or timed-out formatter returns `OUTPUT_QUALITY_FAILED`.
No partial product files are returned on this error.

Limits stay at 12 files, 64 blocks and 65,536 final source bytes.
The public prompt, DSL and successful response shape stay unchanged.
The quality candidate's health response also reports `quality_contract`
and `formatter_version`.

Old prompts use the archived decoder and blocks. The original v6
candidate keeps its original output. The quality candidate reuses the
same model weights, token encoding, block IDs and parameter interfaces.

## Build and run the candidate

Run from the repository root:

```sh
cargo build --release --manifest-path som-rs/Cargo.toml --target-dir som-rs/target/composed-quality
som-rs/target/composed-quality/release/som serve --model som-rs/models/composed-quality-candidate --blocks som-code-python/data/blocks/composed-quality/blocks.json --port 8767
som-rs/target/composed-quality/release/som generate prompt.json --url http://127.0.0.1:8767
```

The server and CLI return files. They do not place them in an app directory.
The evaluation helper places files only for the isolated evaluation.

## Required checks

The full gate covers 14 services, 120 held-out compositions and 80 blind
compositions. It also covers the old 100 PostgreSQL tasks, format
compatibility, rejection categories and Rust/Python final-byte parity.
All accepted final source must stay unchanged on a second format pass.

Each service receives the same complete-product quality checks and
independent database, provider, transaction, concurrency and workflow
checks. Testers create isolated databases. They use local cloud mocks.
They do not patch generated product files.

Recipe checks also cover signed integer limits, zero, finite float limits,
NaN and infinity rejection, Decimal arithmetic, unit groups, partial
updates and nullable notes. Deliberate broken types, missing dependencies,
missing authorization and split transactions must fail their checks.

Raw tool output and model-visible output can differ. The token evaluator
applies the installed Codex CLI's actual history limits before it checks
response completeness. It reads only missing byte ranges when needed.
Every page carries a response hash, page number and byte range.

## Token comparison

The user selected `gpt-6.1-sol`, with high effort, for Codex.
Claude Code uses `claude-opus-5-5`, with high effort.
Its SOM checks passed for all ten apps. Nine token comparisons are known.

### Reuse direct baselines

The user chose one complete direct implementation per app.
These records are fixed historical baselines. Later SOM candidates use them
without another direct measurement for existing apps. Only a new app needs
a direct measurement. Record changes to shared tasks, checks, dependencies,
models or settings beside each comparison. Keep the original evidence.

Codex has ten qualified direct baselines. Claude Code has nine.
Claude Experiment failed the quality gate and used its 30-minute job budget.
Its repair usage is incomplete, so that comparison remains unknown.
See [the baseline policy and index](evals/direct-baselines/README.md) and
[the complete collection report](evals/direct-baselines/collect-2026-10-01-r2/REPORT.md).
This collection ran direct jobs only. It did not run SOM jobs or switch defaults.

Repair and check retries within one job count toward its total tokens.
They are separate from repeated independent measurements.
Repeated measurements are not required for each candidate.
One measurement does not establish variation or an agent's success rate.

Label a later result as a fixed-baseline comparison. Keep its SOM records
in a new candidate round. Do not select old results by token savings.
The per-app and aggregate 30% gate stays in place.

### Archived quality round

Each method uses a fresh conversation, a fresh directory, the same lean
configuration, the same safety hooks and the same locked dependencies.
The first instruction contains the complete shared requirements.

Run Recipe first. If its complete pair misses 30% or lacks valid evidence,
stop the sealed round. Otherwise run the other nine apps in that round.
Each job has at most three CLI attempts and a total 30 minute limit.
All attempts, source reads, checks and waits count toward token use.
Cached input counts once. Missing metrics stay unknown.

The first quality round stopped after the global Codex config hash changed.
Its raw records stay separate. The new round uses fresh work directories.
It pins `gpt-6.1-sol` and high on every CLI call. It checks canonical global
settings, excluding only those two explicitly overridden defaults.
All other settings and the exact safety hook bytes stay frozen.
Actual session records confirm the model, effort, usage and source visibility.

Every app and the aggregate must save at least 30% of total tokens.
Model size, memory and stage times are observations, without hard limits.
Claude, real cloud checks and a default switch remain pending.
