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
