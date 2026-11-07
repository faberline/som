# Composable SOM candidate

> Historical research or model-era record. The current path uses fixed algorithms: `CNL → DSL → Code`.
> It needs no model training, model weights or Apple chip.
> The plans, default claims and commands below describe their original period.
> They do not direct current training or replace current compiler acceptance.
> Read [the current architecture](../docs/architecture.md).

This work adds `som.prompt.v6` and the `block_plan` decoder.
It is a development candidate. It does not change `current`.
Claude and real cloud checks remain pending.
See [the complete report](evals/composable-2026-10-01/REPORT.md) for measured status.

Correctness passed. All twenty services in the final Codex round passed.
Total tokens fell by 58.21%. Recipes saved 4.39% and missed the 30% per-app target.
The candidate is not accepted. `current` remains unchanged.

## Input contract

Read [related-business.v1](contracts/related-business.v1.md) with the prompt.
The prompt includes the contract ID and SHA-256 hash.
Both direct-code and SOM agents receive the same contract.
Contract reads count toward their token use.

The [schema](schemas/som.prompt.v6.schema.json) records the JSON shape.
The [example](examples/composed-prompt.json) shows a complete request.
The runtime also checks field types, names, query rules, and capability rules.

Each service has one parent resource and one child resource.
Each resource has 1–8 custom fields.
Choose one value in each capability group:

| Capability | Choices |
|---|---|
| Database | `postgresql`, `mongodb` |
| Identity | `jwt`, `firebase`, `google_oauth` |
| Object storage | `none`, `s3`, `gcs` |
| Workflow | `json_export`, `csv`, `gzip_export`, `decimal_summary`, `statistics` |

The `operations` list has 1–11 unique entries.
Parent and child actions are `create`, `get`, `list`, `patch`, and `delete`.
The batch entry is `{"scope":"batch","action":"batch_create"}`.
The list order sets the order of the operation blocks.
An omitted operation must not appear as a CRUD route.
The selected workflow and identity routes remain part of the service.

The prompt contains no block IDs or source code.
The flagship agent decides which operations and capabilities it needs.

## Model and assembler

The model receives capability values, resource roles, query kinds, and ordered operations.
Empty operation slots use an explicit padding token.
Field names and values stay in the typed validator.
They do not affect block selection.

The model emits the whole plan in one sequence.
Each file uses a named slot.
Each block has an ID, a local name, typed parameter references, and dependency references.
The private token form is:

```text
<file:SLOT><block:ID><name:NAME><bind:PARAM:REFERENCE><deps><name:DEPENDENCY><end></file><eos>
```

Bindings and dependencies can repeat as needed within a block.
The Rust decoder converts each token directly to `som.dsl.v2`.
It retains block choices, order, bindings, and references.
It does not add missing blocks or retry through a template.

The validator checks the [block contracts](../som-code-python/data/block-contracts/composed.json).
It checks provided symbols, required symbols, types, capabilities, routes, identity, and transactions.
Different valid local names and dependency layouts are allowed.
Validation does not compare the plan to one expected plan.
The limit remains 12 files, 64 blocks, and 64 KiB of source.

The service verifies each block's bytes against its contract at startup.
It also verifies model files, tokenizer IDs, and contract hashes.
The candidate bundles the unchanged legacy model and templates.
Prompts v1–v5 use that frozen decoder.
A failed v6 request never enters the legacy decoder.

## Run the candidate

Run from the repository root:

```sh
cargo build --release --bins --locked --manifest-path som-rs/Cargo.toml
som-rs/target/release/som serve --model som-rs/models/composed-candidate --blocks som-code-python/data/blocks/composed/blocks.json --port 8766
som-rs/target/release/som generate som-rs/examples/composed-prompt.json --url http://127.0.0.1:8766
```

The service listens on loopback only.
`POST /v1/generate` and the CLI return complete source files.
They do not write product files.
The evaluation helper is the only component here that places returned files.

## Training and data

The [historical training guide](../som-core/docs/product/training.md) separates
the learning type, task, architecture, target representation, loss and optimizer.
It records the actual input projection and model decision boundary.
It also lists pending planner and structural-action comparisons, with existing
milestone and issue links.

Python trains new tokenizer and model weights from scratch.
The model has two layers, width 64, four attention heads, and seed 17.
The vocabulary has 1,024 tokens. The context has 4,096 tokens.
Input and output each have a limit of 2,048 tokens.
The Rust service performs all production inference and assembly.

The trainer checks every complete development sequence at each 1,000 steps.
It selects the first checkpoint with every development sequence correct.
It never selects a checkpoint using the heldout set.
Every failed training trial stays in the work record.
The combined training limit is one week.

The [data manifest](../som-core/data/composed/manifest.json) records split hashes and counts.
The input, name-free structure, and full composition must be disjoint across splits.
Atomic operations can repeat.
Experiment and document-review app families stay out of new training and development data.
The existing ten apps and their variants are regression cases.

The final training set has 20,000 services. Development has 600 services.
The first 120 composition tests remain as diagnostic regression after a failed candidate.
A separate 80-case set contains fresh full compositions. It does not select checkpoints.
See the [separation audit](evals/composable-2026-10-01/data-audit-3.json).

```sh
som-core/.venv/bin/python -m som_core.train_v1 --mode block_plan --data som-core/data/composed/train.jsonl --dev som-core/data/composed/dev.jsonl --blocks som-code-python/data/blocks/composed/blocks.json --legacy som-rs/models/business-service-candidate --output /absolute/path/to/new-model --steps 1000000 --hours 168
```

The output directory must be new.
Reduce `--hours` by the time already spent on prior trials.

## Acceptance and token accounting

Acceptance uses real local PostgreSQL and MongoDB transactions.
Identity and object storage use isolated local providers.
Each service starts in an empty directory.
The verifier checks install, migration, HTTP behavior, permissions, transactions, races, restart, and recovery.
It keeps source hashes, requirement hashes, process logs, and HTTP evidence.
Timestamp order is checked as time, not as text.

Rust and Python must assemble the actual model plan to identical bytes.
Legacy outputs must also match their frozen bytes.
Wrong or missing model choices count as failures.

The final [Codex kit](evals/composable-2026-10-01/codex-r3/README.md) contains ten direct/SOM pairs.
Each uses `gpt-6-astra` with high effort, a new session, and a new directory.
Each job permits three CLI attempts within a total of 30 minutes.
All attempts count.
Each app and the combined total must save at least 30% of total tokens.

The helper prints the complete response in one tool result when it fits.
That same helper places the files and runs the complete functional check.
Source visibility is audited after the tool result reaches the model.
Each page records its number, byte range, and hashes.
If a tool truncates it, only missing byte ranges need a new read.
The audit checks native model-visible tool records.
A saved file or read receipt alone is insufficient.

Input includes cached input once. Output is added once.
Full returned code, contract reads, checks, and retries all count.
Missing usage remains unknown. Missing cost and CLI rounds remain blank.
Model size, memory, and phase timings are observations. They are not hard gates.

The [app checklist](evals/composable-2026-10-01/APP-CHECKLIST.md) links each service to its requirements and evidence.
The first full comparison and the interrupted second round remain separate.
All fifty measurement jobs are included in the work-cost disclosure. No earlier result replaces a final-round result.

No release, deployment, real cloud test, or default promotion occurs in this round.
