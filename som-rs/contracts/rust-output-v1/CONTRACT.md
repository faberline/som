# SOM Rust output contract v1 — issue #45

This contract fixes Rust output phase 1 for the CNL → DSL → code compiler.
It was frozen before any Rust block, catalog or backend was written.
The frozen file hashes and the independent review are in ../../evidence/rust-output-contract-freeze-2026-10-15.json.
Implementation acceptance is separate and follows [acceptance-plan.json](acceptance-plan.json).
The compiler-v1 contract in ../compiler-v1 stays in force. This contract only adds to it.

| File | Content |
|---|---|
| [api.schema.json](api.schema.json) | The optional `target` member, the new error code and the check order |
| [bundle.schema.json](bundle.schema.json) | `som.dsl-bundle.v2` for target rust |
| [scope.json](scope.json) | Phase 1 prompt versions, field types, columns, routes, identifiers and refusals |
| [product-layout.json](product-layout.json) | The generated Cargo package, file roles, types, fixed routes and error bodies |
| [toolchain.json](toolchain.json) | Rust, rustfmt, clippy, crates, commands, PostgreSQL and Python pins |
| [reference/Cargo.toml](reference/Cargo.toml), [reference/Cargo.lock](reference/Cargo.lock) | The exact manifest and lock of every product |
| [behavior-normalization.json](behavior-normalization.json) | How Rust and Python products are compared |
| [target-examples.json](target-examples.json) | Positive compile requests for every target and their phase 1 coverage |
| [negative-cases.json](negative-cases.json) | Refused requests with exact diagnostics and Python controls |
| [case-inventory.json](case-inventory.json) | The acceptance cases and the selection rules |
| [acceptance-plan.json](acceptance-plan.json) | Checks, assemble refusals and gates for #46 to #51 |
| [phase2/constructs.json](phase2/constructs.json) | Phase 2 (section 10): every compiler-v1 construct, its Rust form and its owner issue |
| [phase2/naming.json](phase2/naming.json) | Phase 2 wire names (W1 to W6) and Rust-internal names (R1 to R3) |
| [phase2/services.json](phase2/services.json) | Phase 2 settings, local overrides, operator commands and health |
| [phase2/behavior.json](phase2/behavior.json) | Phase 2 behavior suite: checkers, adaptations and the Python run |
| [phase2/case-inventory.json](phase2/case-inventory.json) | Phase 2 cases by id, group and prompt hash, with the expected result |
| [phase2/pins.json](phase2/pins.json) | Phase 2 emulator, uv, venv and suite file pins |

The scripts in ../../tests/rust-output-v1 build the case files and check the contract.
`check_contract.py` needs no compiler and no network.

## 1. Python stays byte-identical

A compile request without `target`, or with `target` python, returns the bytes compiler-v1 returns today for the same request.
The CNL grammar, the typed prompt schema, the v1 bundle, every Python asset, every Python block and every Python output file stay unchanged.
No existing case, example, fixture, receipt or snapshot changes.
A som.dsl-bundle.v1 bundle never has a `target` member. The assembler keeps refusing one with INVALID_SCHEMA at /bundle/target.
Python acceptance keeps its scope: 127 public products, 544 historical jobs, and the legacy 100 and 214 case sets.

## 2. Target selection

The caller selects the target with the optional `target` member of the compile request.
It is the same member for the CNL request (`format` som.cnl.v1) and the typed JSON request (`format` som.prompt-json.v1).

| `target` | Result |
|---|---|
| absent | Python, the unchanged som.dsl-bundle.v1 response |
| `"python"` | The same bytes as absent |
| `"rust"` | A som.dsl-bundle.v2 bundle with `target` rust, or one diagnostic |
| any other JSON value | 400 INVALID_SCHEMA at /target, located on the value token |

The refusal cause is "Target must be the string python or rust." The allowed form is "Omit target, or use target python or rust."
A second `target` member is INVALID_JSON at the second key token, like any duplicate member.
`som compile --target python|rust` forwards the string as the member without checking it. A server refusal exits with status 2.

### Deviation from the issue text: no CNL target sentence

Issue #45 asks for target selection "in CNL and in the JSON compile request". The target is not a CNL sentence.
The grammar is the bundle `grammar` asset of every Python bundle. A new sentence would change that asset and so every Python payload hash, which section 1 forbids.
The CNL text describes the product. The target is a fact about the compile request, so it sits next to `format` in both request forms.
The CNL request still selects the target, and [target-examples.json](target-examples.json) holds CNL and JSON requests for every target.

## 3. The Rust bundle

A Rust compile returns `som.dsl-bundle.v2`. It has `version`, `target` rust, the unchanged typed `prompt`, `dsl`, `assets` and `hashes`.
H is SHA-256 of the som.canonical-json.v1 bytes, as in compiler-v1.

- `prompt_sha256` = H(prompt)
- `dsl_sha256` = H(dsl)
- `payload_sha256` = H({version, target, prompt, dsl, assets})

The DSL is `som.dsl.rust.v1`: 1 to 12 files, 1 to 64 blocks per file and at most 64 blocks in total.
File paths are the product-layout paths. Every block id starts with `rust.`.
The CNL and JSON forms of the same prompt give byte-identical bundles.

| Asset | Descriptor |
|---|---|
| grammar, compiler_contract, selection_policy, literal_policy, serializer, semantic_contracts | The descriptors the active compiler-v1 manifest gives a Python bundle for the same prompt |
| target_contract | id `som.rust-output-contract.v1`; sha256 = H(files map of the latest sealed freeze or amendment receipt) |
| catalog, block_library, backend | The Rust descriptors in the active local Rust manifest. Their ids differ from every Python descriptor id. #48 records them |
| formatter | The sha256 of the rustfmt binary in toolchain.json |

The assembler refuses a bundle whose descriptors differ from the active Rust manifest with ASSET_HASH_MISMATCH at /bundle/assets.
It never falls back to Python assets, and a Python bundle never gets Rust assets.

## 4. Phase 1 scope and refusals

Phase 1 admits prompt versions 1 to 5 with PostgreSQL. [scope.json](scope.json) lists the field types, columns, query parameters and routes of each version.
A Rust request admits exactly the prompts compiler-v1 admits, minus the refusals below. The prompt meaning never depends on the target.

Each refusal is UNSUPPORTED_TARGET with HTTP 422. The pointers below are relative to the typed prompt.
A CNL request reports the pointer as is and locates the CNL token. A JSON request reports /prompt plus the pointer and locates the JSON value token.
Every allowed form also says that omitting target generates Python.

| Rule | Refused prompt | Pointer |
|---|---|---|
| prompt_version | Versions 6 and 7: capabilities and flows | /version |
| v5_capabilities | A v5 prompt with a capabilities block ("Use no capabilities." is in scope) | /capabilities/auth, on the authentication value |
| path_empty_segment | A resource path with an empty segment, such as /items/ or /a//b | the resource path |
| path_reserved | The top-level resource path /healthz | the resource path |
| table_reserved | The table _sqlx_migrations, used by the sqlx migrator (reachable in versions 1 to 4) | the resource table |
| identifier_not_raw | A field or foreign key named crate, self or super | the name |
| identifier_not_snake_case | A field or foreign key name made only of underscores, or one that rustc reports as non_snake_case | the name |
| query_parameter_collision | A v3 filter field named limit or offset | /query/filter_field |

The checks run in this order and the first refusal wins:

1. prompt_version;
2. the v5 capabilities;
3. for each resource in order (resource, or parent then child): the path, then the table, then each field name in array order;
4. the v3 and v4 query filter field;
5. the v5 relation foreign key.

The collision rule is reachable only in v3, because compiler-v1 already refuses those names in v4 and v5.
[tests/rust-output-v1/rust_profile.py](../../tests/rust-output-v1/rust_profile.py) is the reference check.

### Check order

A compile request runs these steps:

1. transport limit and JSON parse, including duplicate members;
2. the envelope schema, including target;
3. the CNL parse or the prompt JSON schema;
4. compiler-v1 semantic validation, unchanged;
5. the Rust profile above, only for target rust;
6. planning, with SCOPE_TOO_LARGE when the request is over a limit.

So every compiler-v1 diagnostic keeps its precedence over a Rust refusal (cases RP01 to RP03).

An assemble request first dispatches on the bundle `version`. Every bundle whose version is not the string som.dsl-bundle.v2, including a v1 bundle, keeps the compiler-v1 order and result. A v2 bundle runs these steps:

1. the bundle schema;
2. the three hashes;
3. the assets;
4. compiler-v1 prompt validation;
5. the Rust profile (UNSUPPORTED_TARGET at /bundle/prompt plus the pointer);
6. catalog and composition: the blocks must be the admitted rust blocks in the frozen order for the prompt, with INVALID_COMPOSITION at the first wrong block id;
7. render, rustfmt and output checks.

A bundle with more than 64 blocks in total fails step 6.

## 5. Product layout

Every product is one Cargo package named som_app with 11 files, listed in [product-layout.json](product-layout.json):

- Cargo.toml and Cargo.lock;
- the sources src/main.rs, src/lib.rs, src/models.rs, src/schemas.rs, src/api.rs and src/db.rs;
- the migration migrations/0001_<revision>.sql;
- the tests in tests/api.rs;
- README.md.

Cargo.toml and Cargo.lock are the reference bytes for every product. The prompt package name appears only in README.md.
No Rust item name comes from the prompt. The v5 batch body and response are the wrappers {parent, children}, as in Python, so prompt field names never collide with them. Field names are used verbatim on the wire and in SQL, and as `r#name` in Rust when they are keywords.
SQL identifiers are double-quoted and every value is a bound parameter.

The binary needs DATABASE_URL. SOM_APP_ADDR defaults to 127.0.0.1:8000. Startup runs the embedded migrator.
Every product serves /healthz plus JSON 404 and 405 fallbacks. The product tests need TEST_DATABASE_URL. A missing database fails the tests and never skips them.
Column types follow the Python migration for the same prompt: SERIAL ids, VARCHAR(255), INTEGER, DOUBLE PRECISION, BOOLEAN, DATE, timestamps with a time zone for v3 to v5, and the same indexes.

## 6. Toolchain

[toolchain.json](toolchain.json) pins:

- Rust 1.96.1, rustfmt 1.9.0 and clippy 0.1.96, by binary hash;
- edition 2024;
- crates axum 0.8.9, sqlx 0.8.6, tokio 1.53.3, serde 1.0.229, serde_json 1.0.154 and chrono 0.4.44, with their exact features;
- the dev crates tower 0.5.3 and http-body-util 0.1.5;
- PostgreSQL 17.7 and Python 3.12.11.

The lock has 204 packages. Products use only the sqlx runtime API, so a build needs no database and no offline query data.
Every generated .rs file is already rustfmt output. Acceptance builds, lints and tests offline with --locked, and Cargo.lock must not change.
A change to the toolchain, a crate, a version or a feature is an amendment.

## 7. Behavior parity

[behavior-normalization.json](behavior-normalization.json) fixes how the harness hosts each product, compares the two database schemas, derives the request script from the prompt and normalizes responses.
It lists the six known differences D1 to D6. They are excluded from parity and checked on the Rust product alone where the plan says so.
No other difference is allowed.

## 8. Cases

- [target-examples.json](target-examples.json) has 24 positive requests:
  - the 5 in-scope public examples and the 6 coverage cases, each in CNL and JSON with target rust;
  - example v1 with target python in both forms.
  - Its coverage section shows that every phase 1 field type, field kind and v5 query variant is reached for each version.
  - Every route of a version is generated for every prompt of that version.
- [negative-cases.json](negative-cases.json) has 32 refused requests:
  - 7 target cases;
  - 22 scope cases, at least one for each rule and for the refusal order;
  - 3 cases that show compiler-v1 diagnostics come first.
  - Each case has exact lines, columns, byte offsets, a pointer, a cause and an allowed form.
  - Each case has a Python control request that must keep its current result. All 32 controls passed on the accepted default (evidence/rust-output-v1). For each CNL scope case, rust_profile.py on the prompt the default compiled gives the frozen refusal.
- [case-inventory.json](case-inventory.json) selects every in-scope case with no sampling: 115 acceptance cases (5 public examples, 4 version fixtures, 6 coverage cases and 100 legacy v3/v4 jobs).
  - Out-of-scope examples, fixtures and legacy jobs must give the refusal the inventory records: prompt_version, or v5_capabilities for the v5 capabilities fixture.
  - Legacy jobs are listed by id and prompt hash only.
- [acceptance-plan.json](acceptance-plan.json) adds:
  - the assemble refusal recipes A1 to A9;
  - the per-product gates;
  - the gate for each issue #46 to #51.

A missing result, a failure and a skipped check each block acceptance and are reported as such.

## 9. Freeze and amendments

The freeze receipt records the SHA-256 of every file in this directory, the case scripts and the evidence, with the baseline commit.
Rust block implementation starts only after the receipt is sealed.
A later change to a frozen file is a numbered amendment. It records the reason, the old and new hashes and an independent review.
It also updates the target_contract asset hash.

## 10. Phase 2 scope (amendment 002, issue #52)

Phase 2 admits every prompt compiler-v1 admits, minus one kept refusal.
Sections 1 to 9 stay in force. For a prompt that phase 1 already admits, the Rust output is unchanged.

### Scope and the kept refusal

Phase 2 removes these section 4 refusals:

- prompt_version and v5_capabilities: v5 with capabilities, v6 and v7 become products;
- path_empty_segment and path_reserved (naming.json W2);
- table_reserved (W3);
- identifier_not_raw and identifier_not_snake_case (W1 and R2).

query_parameter_collision stays: a v3 filter field named limit or offset is refused with the section 4 diagnostic.
The query string of such a product is ambiguous: ?limit=5 would be both the filter and the page size.
The Python product for such a prompt does not start (RS12, RS13 in behavior.json), and compiler-v1 already refuses the same names in v4 and v5.
Issue #52 listed this refusal for removal. Keeping it is a recorded deviation from the issue text, decided on 2026-10-16.
[tests/rust-output-v1/rust_profile_phase2.py](../../tests/rust-output-v1/rust_profile_phase2.py) is the reference check. rust_profile.py and scope.json stay the phase 1 record.

### Constructs and owners

[phase2/constructs.json](phase2/constructs.json) lists every prompt pointer and every enum or const value of compiler-v1 as one construct, with its Rust form. None is unassigned.
Each construct names its owner per prompt version:

- phase1: the delivered output of #46 to #51;
- #53: capability products (v5 with capabilities, v6, and the non-flow part of v7), the naming refusal removals and the crate pins;
- #54: v7 flows, transactions, branches and undo.

Until its owner delivers, a construct is NOT_IMPLEMENTED: a target rust request keeps the phase 1 result, the section 4 refusal.
This is the same rule phase 1 followed between its freeze and #51.

### Names

[phase2/naming.json](phase2/naming.json) fixes two kinds of names.
W1 to W6 cover what a client, a database or an operator sees: JSON names, error bodies and status codes, routes, tables and columns, query parameters, and package-derived and internal names. Rust uses the Python strings unchanged.
R1 to R3 cover Rust-internal identifiers. They never change a W string. No Rust item name comes from the prompt.
A field whose name is not a usable Rust identifier gets a fixed Rust name with a serde rename, and SQL always uses the wire name.
Where the Python product fails for such a name, the suite oracle is the Rust reference. behavior.json records the Python defect, which #57 tracks.

### Product layout and services

A phase 2 product is one Cargo package named som_app, as in phase 1.
v1 to v5 products without capabilities keep the phase 1 layout of product-layout.json. Only the W2 and W3 rules change: /healthz, and the migration bookkeeping table.
For capability and flow products, #53 fixes the file list, the crates and the reference Cargo.toml and Cargo.lock in a later numbered amendment, under the rules of section 6.
[phase2/services.json](phase2/services.json) fixes the settings, defaults, refusals and messages. They are the accepted Python product's, so one settings file runs either product.
It also maps each Python operator command to one som_app subcommand (serve, migrate upgrade, migrate downgrade, check-config) and fixes the /healthz answers.

### Behavior

The phase 2 behavior suite is som-rs/behavior/phase2.
It runs the phase 1 HTTP suite and the som-core verifiers on every phase 2 case. The verifiers are adapted copies (adapt.py) whose language-specific steps go through phase2_host.
Together they cover:

- auth accept and reject;
- PostgreSQL and MongoDB persistence and migrations;
- object put and get;
- workflow outputs;
- flow step results, rollback, branches, undo, idempotency, leases and the provider fault matrix.

[phase2/behavior.json](phase2/behavior.json) records:

- the checkers;
- what each adaptation changes and on how many cases;
- the run on the accepted Python products;
- each case that does not pass, with its explanation.

The Rust reference is the checker's oracle. A Rust product passes a case when its checker passes and no assembled file changed.

### Cases and pins

[phase2/case-inventory.json](phase2/case-inventory.json) names every phase 2 case by id, group, position and prompt hash:

- the 444 legacy v6 and v7 jobs of legacy544 run-005;
- the 115 CNL fixtures and the 3 public examples that phase 1 refuses;
- RS01 to RS22.

Each row has its checker and its expected phase 2 result: a product, or query_parameter_collision for RS12 and RS13.
Cases are counted per source, so one prompt can be more than one case: PUBLIC_v6 is the prompt of legacy services14-0000, and the CNL and JSON forms of an RS prompt are one typed prompt. counts.distinct_prompts and shared_prompts record every overlap.
[phase2/pins.json](phase2/pins.json) pins the emulators (PostgreSQL through toolchain.json, mongod, redis-server, moto and the in-repo GCS, Firebase and OIDC stand-ins), uv, the acceptance venv with every package version, and every suite file by hash.
A phase 2 run on any other version or hash is not phase 2 evidence. No cloud service is used.
The builders and their --check modes are tests/rust-output-v1/build_phase2_*.py.
