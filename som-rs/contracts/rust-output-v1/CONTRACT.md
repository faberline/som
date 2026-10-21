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
Amendment 004 (section 12) records the one exception, compiler-v1 amendment 003: the Python asset descriptors change, and every Python product file stays byte-identical.

## 2. Target selection

The caller selects the target with the optional `target` member of the compile request.
It is the same member for the CNL request (`format` som.cnl.v1) and the typed JSON request (`format` som.prompt-json.v1).

| `target` | Result |
|---|---|
| absent | Python, the unchanged som.dsl-bundle.v1 response |
| `"python"` | The same bytes as absent |
| `"rust"` | A som.dsl-bundle.v2 bundle with `target` rust, or one diagnostic |
| `"typescript"` | The result of the typescript-output-v1 contract (amendment 005, section 13) |
| any other JSON value | 400 INVALID_SCHEMA at /target, located on the value token |

The refusal cause is "Target must be the string python, rust or typescript." The allowed form is "Omit target, or use target python, rust or typescript."
Amendment 005 (section 13) added typescript to both texts. Before it they named python and rust only.
A second `target` member is INVALID_JSON at the second key token, like any duplicate member.
`som compile --target python|rust|typescript` forwards the string as the member without checking it. A server refusal exits with status 2.

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
Amendment 004 (section 12) moved this refusal to compiler-v1, for every target. The phase 2 profile now refuses nothing.

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

## 11. Phase 2 layout, crates and harness (amendment 003, issue #53)

This amendment fixes what section 10 left to #53: the files, the crates and the Rust mode of the behavior suite.
Sections 1 to 10 stay in force, and the phase 1 layout, crates and plan are unchanged.

### Shapes

[product-layout.json](product-layout.json) phase2 gives five shapes. The prompt alone selects one:

| Shape | Prompts | Files |
|---|---|---|
| resource | v1 to v5 without capabilities | the phase 1 files (11) |
| capability_postgresql | v5 with capabilities, v6; database postgresql | adds src/integrations.rs (12) |
| capability_mongodb | the same; database mongodb | no migration (11) |
| flow_postgresql | v7; database postgresql | models in src/schemas.rs, adds src/integrations.rs and src/flows.rs (12) |
| flow_mongodb | v7; database mongodb | the same, no migration (11) |

A prompt that the phase 1 profile admits gives its phase 1 files byte for byte.
A resource prompt that only phase 2 admits keeps the phase 1 files and crates. Its migrator is the hand-written one below, so a prompt table may be named _sqlx_migrations, and a resource may own /healthz.
Capability and flow products have the som_app subcommands of [phase2/services.json](phase2/services.json). serve does not migrate, as the Python product does not.
PostgreSQL products record their one migration in _som_rust_migrations (naming.json W3). Schema comparison ignores that table.
The bundle limits stay 12 files and 64 blocks per file. bundle.schema.json admits the two new paths.

### Crates

Capability and flow products use [reference-phase2/Cargo.toml](reference-phase2/Cargo.toml) and [Cargo.lock](reference-phase2/Cargo.lock), 335 packages. toolchain.json phase2_crates lists every crate and gives the role of each new one.
They add these crates to the phase 1 set:

- mongodb (with bson);
- flate2 on the system zlib through libz-sys, so gzip bytes equal Python's;
- hyper, hyper-util, hyper-rustls and http-body-util for JWKS, OIDC, S3 and GCS;
- ring, sha2, hmac and base64.

JWT, JWKS, Redis RESP2, CSV, Decimal, statistics and Python-compatible JSON are hand-written.
mongodb, bson and libz-sys and their dependencies were fetched from crates.io once, on 2026-10-17. Every build after that is offline.
scripts/rust_output_acceptance/vendor_crates.py vendors the union of both locks into one directory source. A package that the cache no longer holds may come from an earlier vendor directory (--base) when its files match their checksum list. The receipt records both locks and every such package.

### Harness

Two harness changes let the suite run Rust products. The pins of phase2/pins.json move with them:

- phase2_host.RustProduct starts som_app with SOM_APP_ADDR, the listen setting of services.json;
- check_phase2.py run --language rust --qa URL compiles each typed prompt with target rust on a candidate, assembles it and runs the same checker on the Rust product. RS12 and RS13 pass only with their exact refusal.

The Python baseline was run again with the pinned harness, as behavior.json records. Its result is unchanged: 579 of 584 cases pass, and the five that do not are the explained RS07, RS14, RS16, RS12 and RS13.
Section 12 fixes RS07, RS14 and RS16 in Python, and compiler-v1 now refuses RS12 and RS13.

### Acceptance

[acceptance-plan.json](acceptance-plan.json) phase2 is the plan for #53 to #56. Its main rules:

- every phase 2 case runs every phase 2 gate, and every phase 1 acceptance case runs phase1_unchanged and the phase 1 gates;
- RS12 and RS13 are the only UNSUPPORTED_TARGET results;
- phase 1 files stay byte-identical;
- A6 and A8 change, because phase 2 admits their prompts;
- an independent audit closes #55;
- 8765 changes only with the user's approval (#56).

## 12. Python names: `self` and collisions (amendment 004, issues #57 and #58)

compiler-v1 amendment 003 (../../evidence/rust-compiler-contract-amendment-003.json) adds two rules to the compiler:

- `self_name_variants`: a prompt with a resource field or relation.foreign_key named `self` selects variant blocks. Its Python product then works.
- `python_name_collisions`: a name that shares a Python scope with an identifier of the generated code is refused with INVALID_VALUE, for every target.

This amendment records what those rules change in this contract. Sections 1 to 11 stay in force otherwise.

### Python

Section 1 gets one exception. compiler-v1 amendment 003 changes the backend, the block libraries, the catalogs, the compiler contract and the selection policy, so their asset descriptors change.
For every prompt that compiler-v1 still admits, a Python compile response equals the accepted one except bundle.assets and hashes.payload_sha256.
Only the descriptors backend, block_library, catalog, compiler_contract and selection_policy may differ.
The prompt, the DSL and every assembled product file stay byte-identical.
That holds for the 127 public products and the 544 historical jobs.
A bundle compiled before this amendment is refused at assemble with ASSET_HASH_MISMATCH. That is a recorded compatibility break; compile the prompt again.
No CNL text, typed prompt, existing block or existing Python output changes. The variants are new block ids.

Some prompts change result:

- RS07, RS14 and RS16 have a field or foreign key named `self`. Their Python products now pass the phase 2 checker, and behavior.json issue57 records that.
- RS12 and RS13 have a v3 filter field named limit or offset. They are refused at compile with INVALID_VALUE and HTTP 400, for every target.
  - negative-cases.json records the new diagnostic: the code, detail, pointer and span of compiler-v1, with the CNL allowed form of compiler-v1 amendment 003.
  - Their Python controls give the same diagnostic. The JSON control lacks the target member, so its location is checked in the control body.

### Rust

The phase 2 profile (rust_profile_phase2.py, profile.rs) refuses nothing.
query_parameter_collision stays only in the phase 1 record. naming.json lists it under phase1_refusals_moved_to_compiler_v1.
The Rust bundle carries the same shared compiler-v1 descriptors, so they change as in Python.
Its Rust backend descriptor changes because profile.rs and rust/mod.rs change, and its target_contract descriptor changes as section 4 says for every amendment.
Its DSL and its files change only where the next item says.

#57 also fixes one Rust defect. In a MongoDB capability or flow product, the row reader named a foreign key called `self` apart from the fields, unlike the row struct, so the product did not compile.
The reader now names the foreign key together with the fields, as the row struct does.
Only MongoDB products whose foreign key is not a plain Rust identifier change: `self`, `crate`, `super`, a Rust keyword (written `r#name`) or a name that falls back to `foreign_key`.
Before the fix, each of those products failed to compile, so no accepted product changes. No phase 2 case has such a foreign key, so every phase 2 Rust product stays byte-identical.

### Cases and acceptance

../../tests/compiler-v1/self-name-fixtures.json holds the cases of both rules:

- 15 `self` prompts, with the variant ids they select;
- 13 refused names, with their typed JSON and CNL diagnostics;
- 3 accepted controls.

[acceptance-plan.json](acceptance-plan.json) issue57 is the acceptance plan for this amendment. It runs on one candidate:

- python_replay.py --asset-break: the rule above, with every recorded assemble bundle refused and the same bundle rebased on the new descriptors giving the recorded bytes;
- check_python_controls.py, validate_cases.py (Python files equal to the frozen evidence) and the phase 1 target and CLI checks;
- the phase 2 refusals, with A6 rebuilt on RS13 (below);
- every phase 1 and phase 2 Rust product byte-identical to the accepted #55 run (rust-output-055 run-003);
- check_issue57.py: every positive fixture and RS07, RS14 and RS16 compile from typed JSON and CNL to the same bundle and pass the phase 2 checker in Python and in Rust. Every negative and accepted fixture gives its recorded result in typed JSON, typed JSON with target rust, and CNL.

A6 needs a prompt that compiler-v1 admits and Rust does not. Phase 2 has none left.
A6 now uses the RS13 typed prompt, which compiler-v1 refuses. Assemble validates the prompt first, so the result is INVALID_VALUE 400 at /bundle/prompt/query/filter_field.

## 13. TypeScript target selection (amendment 005, issue #65)

Issue #65 adds a third output target, TypeScript. Its own contract, typescript-output-v1, defines the TypeScript bundle, scope, refusals, product and acceptance.
That contract is separate from this one and is sealed after this amendment.
This amendment changes only what this contract says about the `target` member. Sections 1 to 12 stay in force otherwise.

### What changes

- api.schema.json admits `"typescript"` in the `target` enum. A request with target typescript gets the result typescript-output-v1 defines.
- The refusal texts of section 2 name all three targets:
  - cause "Target must be the string python, rust or typescript.";
  - allowed form "Omit target, or use target python, rust or typescript.".
- negative-cases.json: RT01 to RT06 carry the new texts. Their requests, codes, statuses, pointers and spans are unchanged.
  RT07 (a duplicate target member) is INVALID_JSON and keeps its texts.
- Compile check order (api.schema.json x-check-order): step 5, the Rust profile, still runs only for target rust. A typescript request follows the typescript-output-v1 check order instead.
- Assemble check order (x-assemble-check-order): a som.dsl-bundle.v2 bundle whose `target` member is the string typescript continues in the typescript-output-v1 assemble order.
  Every other target value except rust is still INVALID_SCHEMA at /bundle/target, so A4 (target python in a Rust bundle) keeps its result.
- `som compile --target` documents python, rust and typescript. The CLI still forwards the string without checking it.
- api.schema.json: compile_success and the som.dsl-bundle.v2 bundle schema of this contract cover target python and rust only.
  A typescript response and a som.dsl-bundle.v2 bundle with target typescript follow the typescript-output-v1 schemas; the two contracts share only the envelope version and the `target` member.

### What stays

- Python: section 1 holds with no new exception. A request without target or with target python never reaches the target texts, and the som library sources stay byte-identical.
  Every Python compile and assemble response equals the response of the current default compiler byte for byte.
  The current default is rust-output-candidate-006, the #57 release (binary `52658e13…`, build commit `6116fed5`).
  The recorded bundles of the 127 public products (public-products-003b) and the 544 historical jobs (legacy544 run-005) predate section 12, so they keep its rule: every product file stays byte-identical, and a recorded bundle gives ASSET_HASH_MISMATCH until it is rebased.
- Rust: a target rust compile gives the same typed prompt, DSL and product files as before.
  Its bundle differs only in two descriptors and in payload_sha256:
  - target_contract changes, as section 9 says for every amendment;
  - backend changes, because it hashes the som-targets sources that carry the target_contract hash and, later, the section 13 behavior.
  A Rust bundle compiled before this amendment is refused at assemble with ASSET_HASH_MISMATCH. That is a recorded compatibility break, as in section 12; compile the prompt again.
- No scope, refusal, profile, layout, crate, toolchain, case inventory or phase 2 file changes.

### Implementation and acceptance

The som-targets source carries this amendment's target_contract hash (`TARGET_CONTRACT_SHA256`) from the commit that seals it, because its test binds that hash to the latest receipt.
The behavior change lands with typescript-output-v1 phase 1, in one change: target selection admits typescript, the two texts change, and the assembler dispatches typescript bundles.
Until then a build answers target typescript with the INVALID_SCHEMA refusal and the old texts. Such a build does not meet this amendment, so no Rust or TypeScript candidate is prepared from it.
Rust candidate work therefore waits for typescript-output-v1 phase 1, or for a later amendment that changes this order.

[acceptance-plan.json](acceptance-plan.json) amendment005 is the acceptance plan for this amendment. It runs on the first typescript-output-v1 phase 1 candidate:

- every RT case gives exactly its expected diagnostic, located in the raw request body;
- check_python_controls.py passes for every negative case;
- every Python compile and assemble response equals the current default's byte for byte, and the recorded Python bundles keep the section 12 rule;
- every Rust compile response equals the current default's except the two descriptors and payload_sha256;
- every phase 1 and phase 2 Rust product of the latest accepted run (rust-output-055 run-003) assembles to byte-identical files on the candidate;
- every Rust bundle the current default returns is refused with ASSET_HASH_MISMATCH, and the same bundle rebased on the new descriptors gives the files run-003 recorded;
- the CLI check passes and forwards --target typescript unchanged.

Some of these checks need tools that do not exist yet. typescript-output-v1 phase 1 adds them before the candidate runs; a check without a tool counts as unrun and blocks acceptance.
