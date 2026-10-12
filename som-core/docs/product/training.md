# Current SOM training

This guide describes the composed development candidate as of 2026-10-01.
The default service still uses the separate v4 model.
Python trains the model and supplies the correctness reference.
Rust runs production inference, validation, assembly and formatting.

A block plan lists files, approved code blocks and typed references.
DSL is the small language that stores these assembly instructions.

Read [COMPOSABLE.md](../../../som-rs/COMPOSABLE.md) for the service contract.
Read [QUALITY.md](../../../som-rs/QUALITY.md) for the Python quality gate.
The former requirements remain in [training-legacy.md](training-legacy.md).
The [2026-09-23 research scan](../reference/layer-sft-research.md) is historical.

## What training means here

Supervised learning uses input and target pairs.
It names the learning type, rather than the architecture or output format.
The current implementation uses these choices.

| Part | Current implementation |
|---|---|
| Learning type | Supervised learning from synthetic, validated pairs |
| Task | Map a validated requirement projection to a complete block plan |
| Architecture | GPT-2 style Transformer, initialized with random weights |
| Software | Python, MLX, `mlx_lm.models.gpt2`, Hugging Face Tokenizers |
| Output | Conditional autoregressive generation of structural tokens |
| Training input | Requirement tokens followed by the correct plan tokens |
| Training procedure | Teacher forcing: supply the correct preceding token |
| Loss | Next-token cross-entropy on the output plan only |
| Optimizer | Adam with learning rate `3e-4` |
| Sampling | Eight examples per update, sampled with replacement |
| Evaluation | Greedy generation of every complete development sequence |

Autoregressive generation emits one token after another.
Each output token depends on the input and previous output tokens.
Teacher forcing uses the correct previous tokens during training.
Development evaluation instead feeds the model's selected tokens back in.

The [trainer](../../src/som_core/train_composed.py) builds `input + target`.
Its loss mask excludes input predictions and padding predictions.
It includes the first output token and the final `<eos>` token.
It divides the summed output loss by the output token count in the batch.
The script uses Adam. It does not use LoRA or preference training.
Cross-entropy penalizes low probability for the correct next token.
The optimizer updates the model weights from that loss.

## Model and recorded checkpoint

A checkpoint is a saved set of model weights at a training step.

The trainer fixes these settings inside `train_composed.py`.
General CLI width and context options do not override block-plan mode.

| Setting | Value |
|---|---:|
| Transformer layers | 2 |
| Hidden width | 64 |
| Attention heads | 4 |
| WordLevel vocabulary | 1,024 |
| Context | 4,096 tokens |
| Input limit | 2,048 tokens |
| Output limit | 2,048 tokens |
| Random seed | 17 |
| Development interval | 1,000 optimizer steps |
| Maximum combined training budget | 168 hours |

The trainer builds the tokenizer from training symbols only.
The WordLevel tokenizer maps each structural symbol to one token ID.
It exports dense token IDs and pads the vocabulary with unused symbols.
Unknown symbols and overlong training examples cause a failure.

The [composed manifest](../../../som-rs/models/composed-candidate/manifest.json)
records 427,776 parameters and 21,000 training steps.
That final trial took 426.13 seconds. This excludes earlier failed trials.
The longest training input had 22 tokens.
The longest training output had 309 tokens.
The configured context is larger than those observed examples.

The [quality manifest](../../../som-rs/models/composed-quality-candidate/manifest.json)
uses the same weights, tokenizer, configuration and codec hashes.
Its block contracts and quality assets have separate hashes.
The quality update did not retrain the model.

## Actual model input and decision boundary

The [projection and codec](../../src/som_core/composed.py) define this boundary.
`features(prompt)` emits:

1. One token each for database, identity, storage and workflow.
2. Parent and child role tokens, each with its query kind.
3. Ordered operation tokens, padded to eleven operation slots.
4. Structural markers for the operation list and plan start.

The model does not read raw Python, a conversation or the full prompt JSON.
It does not receive resource names, field names, values or migration names.
Those values pass through typed references after validation.
The current contract gives those values fixed assembly rules.

| Component | Decisions and duties |
|---|---|
| Flagship agent | Understand the task. Choose supported resources, fields, capabilities and ordered operations. Write the prompt. Place returned files and run checks. |
| Local model | Choose the full sequence of file slots, blocks, local names, parameter references and dependency references. |
| Decoder | Convert structural tokens to DSL. Retain choices and order. Reject malformed or incomplete output. |
| Validator | Check types, capabilities, symbols, routes, access, transactions, operation coverage and references. Bind approved names and constants. |
| Assembler | Expand approved blocks with validated values. Assemble source. Apply the fixed import and format steps for the quality candidate. |

The Python `reference()` planner creates training targets and test fixtures.
The production Rust decoder does not call it.
It does not add missing blocks, replace wrong choices or use a legacy fallback.
Legacy prompts use their separate frozen path.

This boundary limits what the model can decide.
Two requirements that need different plans must not share the same model input.
Such a collision means the input representation is incomplete.
Changing names cannot fix it.
Audit these collisions before expanding the support contract.
Data flow, transaction boundaries and new operation semantics need explicit
input information if they change plan selection.
See [issue #6](https://github.com/faberline/som/issues/6).

## Data separation and selection

The [data manifest](../../data/composed/manifest.json) records dataset hashes.
The [separation audit](../../../som-rs/evals/composable-2026-10-01/data-audit-3.json)
records these counts.
An input means the actual projected model input.
A composition means the canonical plan used by the audit.
The App/package column counts distinct package IDs in that audit.
It does not count business domains.

| Split | Services | App/package IDs | Operations | Unique inputs | Name-free structures | Compositions |
|---|---:|---:|---:|---:|---:|---:|
| Training | 20,000 | 8 | 114,191 | 20,000 | 20,000 | 20,000 |
| Development | 600 | 8 | 3,433 | 600 | 600 | 600 |
| Existing service regression | 14 | 14 | 154 | 12 | 14 | 11 |
| Earlier composition regression | 120 | 10 | 858 | 120 | 120 | 120 |
| Fresh blind compositions | 80 | 10 | 430 | 80 | 80 | 80 |

Training covers all eleven atomic operations and all 95 selectable blocks.
Atomic operations can appear in more than one split.
The recorded audit found zero split overlaps in complete model inputs,
name-free structures and complete compositions.
Experiment and document-review cases stay out of new training and development.
The earlier 120 cases became diagnostic regression after a failed candidate.
The separate 80-case blind set does not select checkpoints.

Every 1,000 steps, the trainer evaluates all development outputs.
It stops at the earliest checkpoint with every full sequence correct.
Formal test results do not select checkpoints.
Failed trials and their elapsed time remain in the work record.

A perfect development result proves success on that development set.
It does not prove that every unseen composition will work.
Blind cases test new complete combinations of supported atoms.
An input audit checks whether the model sees the required facts.
A rule-based planner comparison checks what the model adds on the same tasks,
with the same assembly and functional checks.
That comparison remains pending.
The reference planner is not yet a measured production alternative.
See [issue #2](https://github.com/faberline/som/issues/2) and
[issue #3](https://github.com/faberline/som/issues/3).

## Methods still to compare

Both methods below can use supervised input and target pairs.
They differ in how they represent and emit the target.

| Method | Output representation | Validation | Current status |
|---|---|---|---|
| Sequence generation | File, block, name, binding, dependency and end tokens in one stream | Decode the stream, then check complete DSL contracts | Implemented and measured on recorded candidate cases |
| Structural action generation | Actions that add DSL nodes, select blocks and bind typed references | Check each allowed action, then check complete DSL contracts | Proposed comparison; not implemented or measured here |

Use the same frozen splits, requirements, blocks and independent behavior
checks for the comparison.
Count invalid plans, complete-plan correctness, functional success, tokens,
training time and generation time.
Keep unseen compositions separate from familiar regression cases.
A different output format alone does not prove a win.

Constrained decoding is a generation rule.
It limits which tokens or actions may follow the current state.
It is separate from the learning type, loss and optimizer.
The present Rust block-plan path uses unmasked greedy token selection.
It validates the generated plan after the end token.
Grammar masks, typed reference constraints and failure handling remain work
for [issue #9](https://github.com/faberline/som/issues/9).
Grammar validity alone does not prove correct access or transaction behavior.

Relevant primary sources:

- [Deep Learning, chapter 5](https://www.deeplearningbook.org/contents/ml.html)
  explains supervised learning and learning algorithms.
- [TRANX](https://aclanthology.org/D18-2002/) describes grammar-based structural
  actions for semantic parsing and code generation.
- [PICARD](https://aclanthology.org/2021.emnlp-main.779/) describes incremental
  parsing that rejects invalid tokens during generation.
- [Measuring Compositional Generalization](https://arxiv.org/abs/1912.09713)
  separates familiar atoms from new compound structures in evaluation.

These papers motivate comparisons. Their results are not SOM measurements.

## Run and inspect a new trial

Run from the repository root with a new output directory.
This command starts training. It is not a documentation check.

```sh
som-core/.venv/bin/python -m som_core.train_v1 --mode block_plan --data som-core/data/composed/train.jsonl --dev som-core/data/composed/dev.jsonl --blocks som-code-python/data/blocks/composed/blocks.json --legacy som-rs/models/business-service-candidate --output /absolute/path/to/new-model --steps 1000000 --hours 168
```

Reduce `--hours` by time spent on earlier trials in the same training budget.
Keep data and contract hashes with each trial.
Inspect the exported manifest before acceptance.
The output includes tokenizer, weights, configuration, codec and contracts.
It also includes the frozen legacy bundle.
The manifest records hashes, counts, seed, steps and development results.

## Acceptance and tracking

The [quality correctness receipt](../../../som-rs/evals/composed-quality-2026-10-01/candidate-correctness.json)
records completed local functional and quality checks.
Those checks do not prove the token gate or authorize a default change.
The [direct baseline index](../../../som-rs/evals/direct-baselines/README.md)
records one reusable complete direct job per app and agent.
Codex uses `gpt-6.1-sol`, high effort.
Claude Code uses `claude-opus-5-5`, high effort.
Later SOM measurements count full responses, checks, repairs and retries.
Each agent must save at least 30% for every app and for the combined total.
Missing usage is unknown.
Real cloud acceptance and the default switch remain pending.

The current work uses existing GitHub milestones.
Each stage depends on the preceding stage's acceptance.
Preparation may proceed earlier. Final acceptance needs the required evidence.
The tracker was checked on 2026-10-01. All listed items were open.
Local evidence does not close a tracker item by itself.

| Stage | Milestone | Existing issues | Precondition |
|---|---|---|---|
| Baseline and model decisions | [M1](https://github.com/faberline/som/milestone/1) | [#1](https://github.com/faberline/som/issues/1), [#2](https://github.com/faberline/som/issues/2), [#3](https://github.com/faberline/som/issues/3) | Preserve baseline and decision evidence |
| Semantic contract and composable DSL | [M2](https://github.com/faberline/som/milestone/2) | [#4](https://github.com/faberline/som/issues/4), [#5](https://github.com/faberline/som/issues/5), [#6](https://github.com/faberline/som/issues/6) | M1 accepted |
| Separate data and composed model | [M3](https://github.com/faberline/som/milestone/3) | [#7](https://github.com/faberline/som/issues/7), [#8](https://github.com/faberline/som/issues/8), [#9](https://github.com/faberline/som/issues/9) | M2 accepted |
| Function and Python quality | [M4](https://github.com/faberline/som/milestone/4) | [#10](https://github.com/faberline/som/issues/10), [#11](https://github.com/faberline/som/issues/11), [#12](https://github.com/faberline/som/issues/12) | M3 accepted |
| Token comparison and candidate decision | [M5](https://github.com/faberline/som/milestone/5) | [#13](https://github.com/faberline/som/issues/13), [#14](https://github.com/faberline/som/issues/14), [#15](https://github.com/faberline/som/issues/15) | M4 accepted |
