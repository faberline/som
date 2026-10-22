# SOM TypeScript output contract v1 — issue #65

This contract fixes TypeScript output phase 1 for the CNL → DSL → code compiler.
It is frozen before any TypeScript block, catalog entry or backend is written.
Implementation acceptance is separate and follows [acceptance-plan.json](acceptance-plan.json).
The compiler-v1 contract (../compiler-v1) and the rust-output-v1 contract (../rust-output-v1) stay in force. This contract only adds to them.
rust-output-v1 amendment 005 (its CONTRACT.md section 13) admits the `target` value typescript and hands it to this contract.

Status: frozen. The freeze receipt is `som-rs/evidence/typescript-output-contract-freeze-2026-10-21.json` (section 11). Amendment 001 is sealed by `som-rs/evidence/typescript-output-contract-amendment-001.json` (section 7). Amendment 002 is sealed by `som-rs/evidence/typescript-output-contract-amendment-002.json` (section 12).

| File | Content |
|---|---|
| [api.schema.json](api.schema.json) | The typescript target, the compile and assemble check orders, and the 11-key asset manifest |
| [bundle.schema.json](bundle.schema.json) | `som.dsl-bundle.v2` with target typescript (`$id` som.dsl-bundle.v2.typescript) |
| [scope.json](scope.json) | Phase 1 prompt versions, field and column types, identifiers, routes and the six refusals |
| [product-layout.json](product-layout.json) | The generated npm package, its 11 files, execution, naming, request parsing, fixed routes and error bodies |
| [toolchain.json](toolchain.json) | Node, npm, prettier, packages, commands, PostgreSQL and Python pins |
| [reference/package.json](reference/package.json), [reference/tsconfig.json](reference/tsconfig.json) | The exact bytes of those two files in every product |
| [catalog/typescript-catalog.v1.json](catalog/typescript-catalog.v1.json) | Every `ts.` block, its file, its order, its versions and its params |
| [behavior-normalization.json](behavior-normalization.json) | How TypeScript and Python products are compared, and the divergences D1 to D7 |
| [target-examples.json](target-examples.json) | Positive compile requests for target typescript and python, and the name cases |
| [negative-cases.json](negative-cases.json) | 33 refused requests with exact diagnostics and Python controls |
| [case-inventory.json](case-inventory.json) | The 125 acceptance cases and the selection rules |
| [acceptance-plan.json](acceptance-plan.json) | Checks, assemble refusals A1 to A12, product gates, amendments 001 and 002 and issue gates |

The scripts in [som-rs/tests/typescript-output-v1/](../../tests/typescript-output-v1/) (the tools) build the case files and check the contract.
`check_contract.py` needs no compiler and no network; `--receipt` also checks the freeze receipt. `check_python_controls.py` sends each Python control to a compiler's read-only compile endpoint, and `--evidence` records the results.

## 1. Python and Rust

A compile request without `target`, or with `target` python, returns the bytes the current default compiler returns for the same request.
This contract changes no CNL text, typed prompt schema, v1 bundle, Python asset, Python block or Python output file.
The 127 public products and the 544 legacy jobs keep rust-output-v1's `python_unchanged` rule with its amendment 004 wording.

A request with `target` rust gives the same typed prompt, DSL and product files as the baseline.
Its bundle is not byte-identical: amendment 005 changes the Rust `target_contract` descriptor, and the Rust `backend` descriptor hashes shared som-targets sources.
A recorded Rust bundle is refused at assemble with ASSET_HASH_MISMATCH and has to be compiled again. rust-output-v1 section 13 records this as a compatibility break.
[acceptance-plan.json](acceptance-plan.json) `python_unchanged` and `rust_unchanged` replay both gates on the TypeScript candidate.

## 2. Target selection

`target` typescript is the third value of the optional `target` member, for both the CNL request and the typed JSON request.
No CNL sentence selects a target, as in rust-output-v1 section 2.
Every other value keeps the rust-output-v1 result (api.schema.json `x-target-selection`).
The texts that name the targets are rust-output-v1's RT01 to RT06. This contract's TT cases restate them.
Amendment 005 of rust-output-v1 named three targets. Its amendment 006 and this contract's amendment 002 (section 12) name four, with react.

A build that does not implement this contract's phase 1 still refuses target typescript with INVALID_SCHEMA. No candidate is prepared from such a build.

## 3. The TypeScript bundle

A TypeScript compile returns `som.dsl-bundle.v2` with `target` typescript. It has `version`, `target`, the unchanged typed `prompt`, `dsl`, `assets` and `hashes`.
The hashes use the same formulas as Rust: `prompt_sha256` = H(prompt), `dsl_sha256` = H(dsl), and `payload_sha256` = H({version, target, prompt, dsl, assets}).
H is SHA-256 of the som.canonical-json.v1 bytes.

The DSL is `som.dsl.typescript.v1`. It has 1 to 12 files, 1 to 64 blocks per file and at most 64 blocks in total. File paths are the product-layout paths. Every block id starts with `ts.`.
The CNL and JSON forms of the same prompt give byte-identical bundles.

The 11 asset keys follow the Rust manifest (api.schema.json `x-manifest`):

| Asset | Descriptor |
|---|---|
| grammar, compiler_contract, selection_policy, literal_policy, serializer, semantic_contracts | The same descriptors as the Rust manifest of the same build |
| target_contract | `som.typescript-output-contract.v1`, the canonical hash of the sealed receipt's files map |
| catalog | `som.typescript-catalog.v1`, the SHA-256 of the embedded catalog file |
| block_library | `som.typescript-block-library.v1`, the SHA-256 of the TypeScript renderer sources |
| backend | `som.typescript-backend.v1`, the canonical hash of the TypeScript `BACKEND_FILES` map |
| formatter | toolchain.json `formatter` id and sha256 (amendment 001) |

The TypeScript backend lives in `som-targets/src/typescript/` with its own `BACKEND_FILES` (decision 4, section 10).
Shared som-targets files (Cargo.toml, lib.rs, main.rs, profile.rs) appear in both backend maps, so an edit to one changes both descriptors. Such an edit needs an amendment of both contracts and is recorded as a bundle compatibility break for both targets.
The descriptor formulas are in api.schema.json `x-manifest`. They are the Rust formulas with the TypeScript ids.

Assemble dispatches on the bundle version first, then on the target. It then checks the bundle schema in the Rust check_schema order, the hashes, the descriptors, the compiler-v1 prompt validation, the TypeScript profile and the catalog composition (api.schema.json `x-assemble-check-order`).

## 4. Phase 1 scope and refusals

Phase 1 covers prompt versions 1 to 5 with PostgreSQL, the scope of Rust phase 1.
The prompt meaning never depends on the target.
Every compiler-v1 diagnostic keeps its precedence. The TypeScript profile runs after the compiler-v1 semantic validation, and only for target typescript.
Each refusal is UNSUPPORTED_TARGET, HTTP 422. The pointer follows the Rust rules: compile points into the request body, and assemble points to /bundle/prompt plus the scope.json pointer.

| Rule | Refused prompt | Reachable |
|---|---|---|
| prompt_version | Versions 6 and 7 | Always |
| v5_capabilities | A v5 prompt with a capabilities block | v5 |
| path_empty_segment | A resource path with an empty segment | Always |
| path_reserved | The resource path `/healthz` | v1 to v4; compiler-v1 already refuses it in v5 (TP04) |
| table_reserved | The table `_som_ts_migrations` | v1 to v4; the v5 table pattern refuses a leading underscore |
| identifier_reserved | A field or foreign key named `__proto__`, `constructor` or `prototype` | `__proto__` v1 to v4; the other two in every version |

The causes, allowed forms and order are in scope.json. The tools' ts_profile.py is the reference implementation. check_contract.py runs it on every typed JSON TS case, every public example and every coverage case. CNL cases are checked by their byte spans, and the compiler checks them at acceptance (scope_checks).
No TypeScript identifier comes from the prompt. So the names Rust refuses or renames (`self`, `Title`, `a__b`) and JavaScript reserved words are in scope. The 10 name cases in target-examples.json are positive acceptance cases.
A v3 filter named `limit` or `offset` is refused by compiler-v1 amendment 003 for every target. It is not a target refusal.

## 5. Product layout

Node runs the `.ts` files directly with its built-in type stripping. There is no build step and no `dist/` (decision 3). `tsc` is a type-check gate only.
Every v1 to v5 product has 11 files:

```
package.json            reference bytes
package-lock.json       reference bytes, sealed by amendment 001
tsconfig.json           reference bytes
src/main.ts             DATABASE_URL, SOM_APP_ADDR, migrate, serve, exit codes
src/app.ts              Hono app, routes, /healthz, fallbacks, error mapping
src/schemas.ts          request and response types, hand-written validators
src/db.ts               SQL constants and pg functions
src/migrate.ts          migrator with table _som_ts_migrations
migrations/0001_<revision>.sql
test/api.test.ts        node:test, needs TEST_DATABASE_URL
README.md
```

The npm package name is always `som-app`, so package.json, package-lock.json and tsconfig.json are the same bytes in every product.
product-layout.json fixes each file's rule, request parsing, the 404, 405, 409, 422 and 500 bodies, `/healthz`, and the startup exit codes.
Query values follow the Rust product's text rules, not the JSON body rules.
Generated code reads prompt-named members only after `Object.hasOwn` and with bracket access, builds responses as object literals, and uses prompt names in SQL only as double-quoted identifiers. The formatter may print a key that is an IdentifierName (reserved words such as class or delete included) without quotes (amendment 001).

## 6. Toolchain

| Tool | Pin |
|---|---|
| Node | Homebrew 26.5.0, binary, libnode and Homebrew library SHA-256 |
| npm | 11.17.0, tree SHA-256 by the tools' tree_sha.py |
| Packages | hono 4.13.13, @hono/node-server 2.1.5, pg 8.23.1; dev: typescript 5.9.3, prettier 3.9.10, @types/pg 8.23.1 |
| Formatter | prettier 3.9.10 with defaults, `--no-config --no-editorconfig` |
| PostgreSQL | 17.7, binary SHA-256 |
| Python | 3.12.11 for the Python products and the scripts |

A product depends on exactly these packages. Every install runs offline from the vendored cache with `--ignore-scripts`.
The commands are install, typecheck, format_check, test and run (toolchain.json `commands`).

## 7. Amendment 001: the lockfile

The contract freezes without `package-lock.json` (decision 1). No npm access happens before the freeze.
After the freeze, one approved download fills `.local/typescript-output/npm-cache-001`. typescript-output-v1 amendment 001 then seals:

- reference/package-lock.json, lockfileVersion 3;
- toolchain.json `packages.lock_packages`, every lockfile package with its version, resolved URL and integrity;
- toolchain.json `vendored_cache.tree_sha256` and `formatter.sha256`;
- toolchain.json `reference_verification`, a reference product that passes install, typecheck, format_check and test against the cache.

At the freeze, `formatter.sha256`, `packages.lock_packages` and `vendored_cache.tree_sha256` read "pending amendment 001" and `reference_verification.status` read `not_run`.
Amendment 001 (2026-10-21, issue #73) sealed them, and check_contract.py now checks the sealed values (the cache and prettier tree hashes are recomputed when the local directories are present):

- the lockfile has 21 packages; none has an install script, and every resolved URL is on the npm registry;
- the vendored cache tree sha256 is `11ae1ed59d4e17888d3b6e3b864cdfdbc850b11752cae2735d13ccb5f71eb1e9`, and every command sets `npm_config_logs_dir` outside the cache, so npm writes no logs into it;
- the formatter sha256 is `636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8`;
- the reference product, example v1, passed the four commands (evidence `som-rs/evidence/typescript-output-v1/reference-verification-2026-10-21.json`).

The reference verification also showed that prettier's default quoteProps prints identifier field names without quotes, so product-layout.json `naming.fields` now asks for bracket reads and own-property checks, not quoted keys.
A missing pinned version, a package that needs an install script, or any network access after the download blocks amendment 001 and goes back to the user.
No build is a candidate and no product is accepted before amendment 001 is sealed.

## 8. Behavior parity

[behavior-normalization.json](behavior-normalization.json) runs each TypeScript product next to the Python product for the same prompt, on QA ports, never 8765 or 18765.
It uses the Rust schema-equivalence query, which excludes `alembic_version` and `_som_ts_migrations`.
The request scripts use the Rust rule plus four added inputs: an int sent as 1.0, a float sent as an integer, a 255-code-point string with a non-BMP character, and a 256-code-point string (v5).

The divergences are excluded from parity:

| Id | Divergence |
|---|---|
| D1 | Pydantic lax coercion; TypeScript returns 422 |
| D2 | Over-long strings and out-of-range ints in v1 to v4; Python returns 500, TypeScript returns 422 |
| D3 | A path id outside i32; TypeScript returns 404 |
| D4 | A repeated query parameter; TypeScript returns 422 |
| D5 | A trailing slash; Python redirects 307, TypeScript returns 404 |
| D6 | `/healthz` before v5; compared for v5 only |
| D7 | Non-canonical integer text in a path or query; TypeScript returns 422 |

The TypeScript product's own tests check D1 to D5 and D7. A 500 from either product is a parity failure.

## 9. Cases and acceptance

- [target-examples.json](target-examples.json) has CNL and JSON requests with target typescript for the 5 in-scope public examples and the 6 coverage cases, the v1 example with target python, and 10 name cases with their Python controls.
- [negative-cases.json](negative-cases.json) has 33 cases:
  - TT: target selection, including a duplicate member;
  - TS: one or more cases for each refusal and for the refusal order, in CNL and JSON forms;
  - TP: the compiler-v1 diagnostics that keep their precedence.
  
  Each case has an exact diagnostic with a byte span, line and column, plus a Python control body without `target` and its expected result.
- [case-inventory.json](case-inventory.json) has 125 acceptance cases: 5 public examples, 4 version-positive fixtures, 6 coverage cases, 10 name cases and the 100 in-scope legacy jobs.
  It also records which refusal every out-of-scope fixture and legacy job gets.
- [acceptance-plan.json](acceptance-plan.json) has these gates:
  - `python_unchanged` and `rust_unchanged`;
  - the target, scope and bundle checks;
  - the assemble refusals A1 to A12;
  - the product gates that every acceptance case runs: install, typecheck, format check, test, lock unchanged, schema equivalence, parity, strict refusals and startup;
  - the issue gates.

  It samples nothing. A missing, failed or skipped check blocks acceptance.

The Python controls were run read-only against the current default on 8765 on 2026-10-21: 43 passed, 0 failed.

## 10. Decisions (2026-10-21)

1. The contract freezes without `package-lock.json`. Amendment 001 seals the lock, the integrity hashes and the formatter hash after the one approved npm download.
2. `identifier_reserved` keeps `__proto__`, `constructor` and `prototype`.
3. Node type stripping runs the `.ts` files directly. tsc is a gate only. No `dist/`.
4. The backend lives in `som-targets/src/typescript/` with its own `BACKEND_FILES`. An edit to a shared file is recorded as a Rust bundle compatibility break.

The options considered for runtime, framework, database layer, validation, tests, package manager and formatting are in ../../../docs/typescript-output-v1-draft.md section 6.

## 11. Freeze procedure

1. A person approved the candidate on 2026-10-21.
2. The contract files moved from `docs/typescript-output-v1/` to `som-rs/contracts/typescript-output-v1/`, and the tools to `som-rs/tests/typescript-output-v1/`.
3. `python3 som-rs/tests/typescript-output-v1/check_contract.py --files` reports no failure. `check_python_controls.py --evidence` recorded the Python controls on 8765 in `som-rs/evidence/typescript-output-v1/`.
4. An independent reviewer checked the frozen files.
5. The receipt `som-rs/evidence/typescript-output-contract-freeze-2026-10-21.json` seals the files map, the review and the approval. `check_contract.py --receipt` checks it.
6. Split phase 1 into issues: target selection and profile, catalog and renderer, host and parity, and acceptance. Amendment 001 comes first.

After the receipt is sealed, a change to any frozen file needs a numbered amendment with its reason, the old and new hashes, and an independent review.

## 12. Amendment 002: react in the target texts (issue #126)

The react-output-v1 contract (frozen 2026-10-22) adds react as the fourth value of the `target` member.
rust-output-v1 amendment 006 (its CONTRACT.md section 14) admits it, and this amendment follows for the texts this contract restates.

- TT01 to TT06 refuse a target value outside the enum. They change only their cause and allowed form, to the texts of rust-output-v1 api.schema.json `x-target-selection` invalid:
  - cause "Target must be the string python, rust, typescript or react.";
  - allowed form "Omit target, or use target python, rust, typescript or react.".
- TT07 (a duplicate target member) is INVALID_JSON and keeps its texts. Every TS and TP case is unchanged.
- api.schema.json `x-target-selection` `others` now names react, which rust-output-v1 hands to react-output-v1.
- api.schema.json `x-assemble-check-order` target dispatch: a bundle with target react follows react-output-v1, not INVALID_SCHEMA at /bundle/target. Target typescript and rust dispatch as before.
- No scope, refusal, layout, catalog, toolchain, package, case inventory or product rule changes.

A target typescript compile gives the same typed prompt, DSL and product files as before.
Its bundle differs only in bundle.assets.backend, bundle.assets.target_contract and bundle.hashes.payload_sha256:
the TypeScript backend descriptor hashes the shared som-targets lib.rs, where the target texts change, main.rs, where the CLI help names react, and typescript/mod.rs, where this amendment's target_contract hash is set.
The same break covers the later react-output-v1 phase 1 edits to the shared som-targets sources (lib.rs, main.rs, profile.rs and Cargo.toml) that admit and dispatch react. They need no further amendment of this contract, as long as every TypeScript prompt, DSL and product file stays the same.
A TypeScript bundle compiled before this amendment is refused at assemble with ASSET_HASH_MISMATCH. That is a recorded compatibility break; compile the prompt again.
Python gains no exception (section 1).

The som-targets source changes `TARGET_CAUSE`, `TARGET_ALLOWED`, the `som compile --target` help and this contract's `TARGET_CONTRACT_SHA256` in the commit that seals this amendment, because the som-targets tests check the TT cases against the build.
From that commit until react-output-v1 phase 1, a build refuses target react with INVALID_SCHEMA at /target and the four-target texts.
This deviates from react-output-v1 api.schema.json `x-target-selection` before_phase_1, which names the three-target texts for that interval, as rust-output-v1 section 14 records. No candidate is prepared from such a build either way.
[acceptance-plan.json](acceptance-plan.json) `amendment_002` lists the checks, run on the react-output-v1 phase 1 candidate.
The Python controls ran again on the amended negative cases, read-only against the current default on 8765 (`som-rs/evidence/typescript-output-v1/python-controls-2026-10-23.json`). They only read compile responses there; no QA candidate ran on 8765 or 18765.
