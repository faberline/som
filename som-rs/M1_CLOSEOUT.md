# M1 closeout — 2026-10-02

> Historical model-roadmap document. On 2026-10-13 the human replaced this plan with
> [the Rust roadmap](RUST_ROADMAP.md). Old milestone records were archived and deleted.
> Read [the current handoff](RUST_HANDOFF.md). Preserve the results below; do not resume old M3 training.
> New compiler implementation and acceptance remain pending. The default remains legacy M2.

M1 evidence passed. M2 product acceptance is still open. No model default,
paid-agent baseline, release or cloud environment changed.

## Results

The rule planner reads public prompts and block contracts. It has no corpus,
answer table, expected DSL or model call. Its source hash is
`2035f0bc6e5762d7149dec11017e11ba5df749d04776e99f2332c0f50c6684f0`.

| Regression group | Rules: DSL / requirement / function | Model: DSL / requirement / function | Rejections |
|---|---:|---:|---:|
| 14 complete services | 14 / 14 / 14 | 14 / 14 / 14 | 0 |
| 120 compositions | 120 / 120 / 120 | 120 / 120 / 120 | 0 |
| 80 former blind compositions | 80 / 80 / 80 | 80 / 80 / 80 | 0 |

Both methods used the same prompt, 130-block quality library and fixed
quality profile. Their final files are byte-identical on all 214 cases.
Independent database, HTTP, identity, storage, workflow and quality checks
ran on those exact files. The equal hashes let both methods use the same
behavior evidence. The checks derive outcomes from public requirements.

The old 100 tasks also passed real PostgreSQL checks and byte comparison.
They are compatibility cases. Their saved expected DSL is not a rule-planner
comparison. Another 34 format, contract and quality tests passed.

All these cases are now regression. They are not new blind tests.

## What the model has proved

The v6 model emits a complete file and block plan. The decoder preserves its
block choices, order, references and end marker. Wrong plans fail validation.
The tested model can compose existing capabilities and operation subsets.

The general rule planner can solve this entire measured scope too. This
comparison does not prove that a model is needed or that it beats rules.
Runtime dataflow, conditional branches and durable compensation were not
supported by this baseline. M2 must prove those behaviors separately.

The older decoder selects operation labels. Fixed templates supply its other
blocks. That template work is not counted as model reasoning.

## Decision owners

| Decision | Owner | Check |
|---|---|---|
| Business logic and required behavior | Codex or Claude Code | Public prompt and independent behavior checks |
| Selected implementation blocks and order | Local model | Raw model output, decoded DSL, block contracts |
| Types, capability rules, dependencies and required operations | Validator | Valid-plan and rejection tests |
| Safe names, paths and constants | Versioned binder | Prompt validation and typed parameter checks |
| Code expansion and native formatting | Assembler and fixed Ruff | Final source hashes and quality checks |
| Legacy layout and non-operation blocks | Frozen legacy templates | Archived source and raw label traces |
| Rule comparison layout and symbol providers | Public generic rule planner | Source review, no answer access, equivalent files |

The v6 model chooses named file slots. The binder maps each slot to the fixed
safe path. The model emits dependency references; the validator checks them.
The binder fills approved names and constants, not missing blocks.

## Reproducible evidence

All paths below are relative to this repository.

- Original baseline: `som-rs/evals/m1-closeout-2026-10-02/r1/baseline/`.
  The archive has 353 files and SHA-256
  `52a85734bea8ea86d19db366b54042f032707622ed979102a6c9ef6250e7ae23`.
- Closeout receipt and executable supplement:
  `som-rs/evals/m1-closeout-2026-10-02/r1/closeout-002/`.
  Supplement SHA-256:
  `f0a7bbc7067f9ead2f4a496b33e4ab97943571425cb489112cf9528d4edc3025`.
- Qualifying rule/model comparison: `r1/comparison-002/` under that eval root.
  It stores prompts, raw block plans, DSL, responses and timing logs.
  `closeout-002/supplement/v6-input-token-ids.json` stores exact input IDs.
- Old raw chains: `r1/legacy-raw-chain.jsonl`. These contain 100 prompts and
  500 actual model choices. Their final files match the old HTTP responses.
  The production decoder body still matches the original archived source.
- Full services: `r1/full-services-002/shard-{0,1}/`.
  Both shards passed, with unchanged model, library, checker and tool hashes.
- Old PostgreSQL checks: `r1/legacy100/result.json`.
- Format checks: `r1/compatibility.log`.

The correct baseline binary is
`som-rs/target/composed-quality/release/som`, hash
`c248c3431358c1273b224b7376e1b7eff507d5cfcc0d9a921e952f53fc7f42a1`.
It is saved in the supplement. The original archive also contains an older
`target/release/som`. That binary cannot load the quality candidate. Its
failed startup remains in the evidence. Use the saved correct binary.

The supplement includes both Python environments, exact package versions,
Git status and the original tracked work patch. Original failed attempts
remain alongside the qualifying run. They are not merged into pass counts.

## Observations

| Group | Rules p95 ms | Model HTTP p95 ms |
|---|---:|---:|
| Services 14 | 205.534 | 566.712 |
| Compositions 120 | 208.683 | 526.161 |
| Former blind 80 | 223.349 | 640.820 |

Rule timing includes local validation, assembly and formatting. Model timing
also includes the HTTP round trip and response parsing. These are observations,
not identical transport benchmarks or acceptance limits.

The complete model directory has 29,815,126 bytes. This comparison did not
sample resident memory. The report records it as unavailable, not zero.

## M2 sealed before test weights

`som-core/data/flow-v7-r7/` has 12 development smoke cases and 114 formal
positive cases. The latter contain 90 capability combinations and 24 semantic
cases. Both actual model inputs and name-erased structures have zero split
overlap. Experiment, Observation, Document and Review stay excluded.

`acceptance-seal-003/` freezes the public contract, inputs, rejection cases,
independent evidence requirements and source projection. It has 25 prompt
rejection cases. The archive hash is
`7febfbb75ec99e28b09fd117f6924b2cc43d235611896a962bf05ae0250e71f2`.
Formal tests cannot train test weights or select a checkpoint. Test weights
may use development smoke only. They do not prove accuracy or generalization.

## Later work

M2 remains open until final output budgets, strict quality, Metal decoding,
full runtime behavior and Python/Rust final-byte parity all pass.
M3 holds full training. M5 holds the Claude Experiment comparison gap,
real-cloud checks, token adoption and any default switch. The permanent
direct-agent baselines and all original failures remain intact.
