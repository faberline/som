# SOM React output contract v1 — issue #68

This contract fixes React front end output phase 1 for the CNL → DSL → code compiler.
It is frozen before any React block, catalog entry or Rust backend is written in som-targets. The reference renderer in the tools is Python and is not part of a build.
Implementation acceptance is separate and follows [acceptance-plan.json](acceptance-plan.json).
The compiler-v1 contract (../compiler-v1), the rust-output-v1 contract (../rust-output-v1) and the typescript-output-v1 contract (../typescript-output-v1) stay in force. This contract only adds to them.
rust-output-v1 amendment 006 and typescript-output-v1 amendment 002 will admit the `target` value react and hand it to this contract (section 1).

Status: not frozen yet. The freeze receipt will be `som-rs/evidence/react-output-contract-freeze-2026-10-22.json` (section 11).

| File | Content |
|---|---|
| [api.schema.json](api.schema.json) | The react target, the compile and assemble check orders, and the 11-key asset manifest |
| [bundle.schema.json](bundle.schema.json) | `som.dsl-bundle.v2` with target react (`$id` som.dsl-bundle.v2.react) |
| [scope.json](scope.json) | Phase 1 prompt versions, field types, form inputs, identifiers, routes, API calls and the three refusals |
| [product-layout.json](product-layout.json) | The generated Vite package, its 19 files, the service it calls, execution, naming and limits |
| [toolchain.json](toolchain.json) | Node, npm, prettier, packages, the sealed lockfile packages, the vendored cache, commands, PostgreSQL and Python pins, and the reference verification |
| [reference/](reference/) | The exact bytes of the 17 fixed files of every product, package-lock.json included |
| [catalog/react-catalog.v1.json](catalog/react-catalog.v1.json) | Every `react.` block, its file, its order, its versions and its params |
| [api-contract.json](api-contract.json) | The 20 steps the product's own src/api.ts runs against the Python product of the same prompt |
| [target-examples.json](target-examples.json) | Positive compile requests for target react and python, and the 9 name cases |
| [negative-cases.json](negative-cases.json) | 30 refused requests with exact diagnostics and Python controls |
| [case-inventory.json](case-inventory.json) | The 129 acceptance cases and the selection rules |
| [acceptance-plan.json](acceptance-plan.json) | Checks, assemble refusals A1 to A16, product gates, the target amendments and issue gates |

The scripts in [som-rs/tests/react-output-v1/](../../tests/react-output-v1/) (the tools) build the case files, render reference products, run the gates and check the contract.
`check_contract.py` needs no compiler and no network; `--receipt` also checks the freeze receipt. `check_python_controls.py` sends each Python control to a compiler's read-only compile endpoint, and `--evidence` records the results.

## 1. Python, Rust and TypeScript

A compile request without `target`, or with `target` python, returns the bytes the current default compiler returns for the same request.
This contract changes no CNL text, typed prompt schema, v1 bundle, Python asset, Python block or Python output file.
The 127 public products and the 544 legacy jobs keep rust-output-v1's `python_unchanged` rule with its amendment 004 wording.

The target enum is shared. Admitting react changes the shared som-targets files and the target-selection texts of the two existing target contracts, so two amendments come first ([acceptance-plan.json](acceptance-plan.json) `target_amendments`):

- rust-output-v1 amendment 006: the RT cases that refuse a target value outside the enum change only their cause and allowed form to the four-target texts;
- typescript-output-v1 amendment 002: the TT cases that refuse a target value outside the enum change in the same way.

A request with `target` rust or typescript then gives the same typed prompt, DSL and product files as the baseline (TypeScript candidate-001).
Only the `backend` and `target_contract` descriptors and `payload_sha256` may differ. A recorded Rust or TypeScript bundle is refused at assemble with ASSET_HASH_MISMATCH and has to be compiled again; each amendment records this as a compatibility break.
Neither amendment is sealed by this freeze. No candidate serves target react before both are sealed, each with an independent review.

## 2. Target selection

`target` react is the fourth value of the optional `target` member, for both the CNL request and the typed JSON request.
No CNL sentence selects a target.
A target value outside the enum is INVALID_SCHEMA, HTTP 400, pointer /target, with the four-target texts of api.schema.json `x-target-selection` `invalid`:

- cause: "Target must be the string python, rust, typescript or react."
- allowed form: "Omit target, or use target python, rust, typescript or react."

The RR cases of negative-cases.json restate them. RR08 is a duplicate `target` member, which keeps the compiler-v1 INVALID_JSON diagnostic.
A build that does not implement this contract's phase 1 still refuses target react with the three-target INVALID_SCHEMA texts. No candidate is prepared from such a build.

## 3. The React bundle

A React compile returns `som.dsl-bundle.v2` with `target` react. It has `version`, `target`, the unchanged typed `prompt`, `dsl`, `assets` and `hashes`.
The hashes use the Rust formulas: `prompt_sha256` = H(prompt), `dsl_sha256` = H(dsl), and `payload_sha256` = H({version, target, prompt, dsl, assets}).
H is SHA-256 of the som.canonical-json.v1 bytes.

The DSL is `som.dsl.react.v1`. It has 1 to 19 files and 1 to 64 blocks per file. File paths are the product-layout paths, with no migration path. Every block id starts with `react.`.
The bundle schema cannot count blocks across files; the assembler refuses more than 64 blocks in total.
The CNL and JSON forms of the same prompt give byte-identical bundles.

The 11 asset keys follow the Rust manifest (api.schema.json `x-manifest`):

| Asset | Descriptor |
|---|---|
| grammar, compiler_contract, selection_policy, literal_policy, serializer, semantic_contracts | The same descriptors as the Rust manifest of the same build |
| target_contract | `som.react-output-contract.v1`, the canonical hash of the sealed receipt's files map |
| catalog | `som.react-catalog.v1`, the SHA-256 of the embedded catalog file |
| block_library | `som.react-block-library.v1`, the SHA-256 of the React renderer sources |
| backend | `som.react-backend.v1`, the canonical hash of the React `BACKEND_FILES` map |
| formatter | toolchain.json `formatter` id and sha256 |

The React backend lives in `som-targets/src/react/` with its own `BACKEND_FILES`. Shared som-targets files appear in every backend map, so an edit to one changes every descriptor and needs an amendment of each target contract.
Assemble dispatches on the bundle version first, then on the target. It then checks the bundle schema, the hashes, the descriptors, the compiler-v1 prompt validation, the React profile and the catalog composition (api.schema.json `x-assemble-check-order`). acceptance-plan.json `assemble_refusals` has a recipe for each refusal, A1 to A16.

## 4. Phase 1 scope and refusals

Phase 1 covers prompt versions 1 to 4 (decision 1). The React product is a front end for the Python service that the same prompt generates without a target. It has no server, database or migration of its own.
The prompt meaning never depends on the target.
Every compiler-v1 diagnostic keeps its precedence; the RP cases restate four of them. The React profile runs after the compiler-v1 semantic validation, and only for target react.
Each refusal is UNSUPPORTED_TARGET, HTTP 422. Compile points into the request body, and assemble points to /bundle/prompt plus the scope.json pointer.

| Rule | Refused prompt |
|---|---|
| prompt_version | Versions 5, 6 and 7 |
| path_empty_segment | A resource path with an empty segment |
| identifier_reserved | A field named `__proto__`, `constructor` or `prototype` |

The refusal order is the prompt version, then the resource path, then each field name in array order. The causes and allowed forms are in scope.json.
The tools' react_profile.py is the reference implementation. check_contract.py runs it on every typed JSON RS case, every public example and every coverage case. CNL cases are checked by their byte spans, and the compiler checks them at acceptance (scope_checks).
No TypeScript identifier comes from the prompt: field names appear only as type-literal members, string literals and bracket reads after `Object.hasOwn`. So JavaScript reserved words, uppercase letters, repeated underscores and Object.prototype names such as `valueOf` are in scope. The 9 name cases in target-examples.json are positive acceptance cases.
A v3 filter named `limit` or `offset` is refused by compiler-v1 amendment 003 for every target (RS17, RS18). It is not a target refusal.

## 5. Product layout

Vite builds and serves the sources, vitest runs the tests in jsdom, and `tsc` is a type-check gate only. Every v1 to v4 product has 19 files:

```
index.html                  reference bytes
package.json                reference bytes
package-lock.json           reference bytes
tsconfig.json               reference bytes
vite.config.ts              reference bytes: the /api proxy to SOM_API_URL, the jsdom test environment
src/main.tsx                reference bytes: BrowserRouter and StrictMode on #root
src/App.tsx                 reference bytes: the fixed routes
src/api.ts                  reference bytes: the fetch client
src/spec.ts                 reference bytes: the types of the schema constants
src/form.ts                 reference bytes: form text to JSON values and request bodies
src/styles.css              reference bytes
src/pages/ListPage.tsx      reference bytes
src/pages/DetailPage.tsx    reference bytes
src/pages/FormPage.tsx      reference bytes
src/pages/ErrorView.tsx     reference bytes
src/app.test.tsx            reference bytes: page tests against a fake fetch
src/form.test.ts            reference bytes
src/schema.ts               generated: Row, CreateBody, PatchBody, ListQuery, RESOURCE, FIELDS, QUERY
README.md                   generated: resource name and path, run and check commands
```

The npm package name is always `som-app`, so 17 files are the same bytes in every product. Only src/schema.ts and README.md depend on the prompt; render_reference.py fixes their bytes.
The front end routes are fixed: `/`, `/new`, `/:id`, `/:id/edit` and a not-found page. The resource path appears only in API calls to `/api<path>`, which the Vite dev and preview proxy sends to `SOM_API_URL<path>` (default http://127.0.0.1:8000).
The API calls per prompt version, the form inputs per field type, and the error mapping are in scope.json and product-layout.json `service`.

## 6. Toolchain

| Tool | Pin |
|---|---|
| Node | Homebrew 26.5.0, binary, libnode and Homebrew library SHA-256 |
| npm | 11.17.0, tree SHA-256 by the tools' tree_sha.py |
| Packages | react 19.3.1, react-dom 19.3.1, react-router 8.5.0 |
| Dev packages | typescript 5.9.3, vite 8.3.5, vitest 5.0.5, jsdom 30.1.3, @testing-library/react 16.3.3, @testing-library/dom 10.4.2, @types/react 19.3.0, @types/react-dom 19.3.0, @types/node 26.6.5, prettier 3.9.10 |
| Formatter | prettier 3.9.10 with defaults, `--no-config --no-editorconfig`; sha256 `636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8` |
| PostgreSQL | 17.7, for the Python service in the API contract |
| Python | 3.12.11, for the Python service and the scripts |

Unlike typescript-output-v1, the lockfile is sealed at the freeze. The one approved npm download (decision 2) filled `.local/react-output/npm-cache-001` before the freeze:

- reference/package-lock.json is lockfileVersion 3 with 123 packages. 99 are in the vendored cache; the other 24 are optional platform packages for other hosts.
- Every resolved URL is on the npm registry with sha512 integrity. Only fsevents declares an install script, and every install runs with `--ignore-scripts`.
- The vendored cache tree sha256 is `8560dba81d69e7599ec4a050946a1b7c2240b5ac7cd8155647750b4df59796b6` (508 files). Every command sets `npm_config_logs_dir` outside the cache.

The commands are install, typecheck, format_check, test and build (toolchain.json `commands`). The build runs twice into new directories outside the product and the two dist trees must be identical. After the commands, package.json, package-lock.json and every other product file outside node_modules keep their bytes, and no product file outside node_modules is added or removed. The offline npm settings keep npm off the network after the download; the other commands are not sandboxed, and none of them is configured to use the network.

## 7. Pre-freeze reference verification

Before the freeze the tools rendered and gated real products against the vendored cache:

- the eight public v1 to v4 products (000, 119, 001, 120, 002, 121, 003, 122) pass install, typecheck, format_check, test and two identical builds with the lockfile, the other product files and the cache unchanged, and pass the API contract against the hosted Python product of the same prompt;
- public product 003 (som.prompt.v4) is the recorded reference product;
- the nine name cases pass the same product gates.

RN07 (a v3 filter field named `valueOf`) first failed typecheck. `listRows` had the default parameter `ListQuery = {}`, and `{}` is not assignable to a ListQuery whose `valueOf` member is a boolean. reference/src/api.ts now takes the query without a default, and every recorded run used the fixed file.
The evidence is in `som-rs/evidence/react-output-v1/` (toolchain.json `reference_verification`). check_contract.py checks that every fixed file of the recorded reference product equals its reference bytes, that each public product and name case records the reference fixed-file hash and gates that exit 0, and, when the `.local` prompts are present, that its generated files equal render_reference.py for its prompt. Without them it prints a NOTE; `--require-prompts` makes an absent prompt a failure, and the M6 acceptance runs check_contract.py with it.

## 8. API contract

[api-contract.json](api-contract.json) drives the product's own src/api.ts, with the constants of its src/schema.ts, against the Python product of the same prompt. It checks that the client and the service agree on paths, bodies, status codes, error details and list queries.
The Python product is hosted by run_api_contract.py on QA ports with a fresh PostgreSQL cluster, never 8765 or 18765. Every process it starts is checked with lsof before it is signalled, and stopped.
It has 20 steps, and the driver records all 20 in order on every run. Of these, 7 steps have a `when` condition. When it is false for the prompt, the step is recorded with ok null and its `skip_reasons` reason; that is the only allowed skip (api-contract.json `skip_rule`). A step that needs a row an earlier step did not create fails. check_contract.py checks that each recorded run skipped exactly the steps whose condition is false.
Before the freeze it ran for the eight public products. Name cases have no public Python product, so their API contract runs in the M6 acceptance, against the Python product the candidate assembles for the same request without target.

## 9. Cases and acceptance

- [target-examples.json](target-examples.json) has CNL and JSON requests with target react for the 4 in-scope public examples and the 4 in-scope coverage cases, the v1 example with target python, and 9 name cases with their Python controls.
- [negative-cases.json](negative-cases.json) has 30 cases:
  - RR01 to RR08: target selection, including a duplicate member;
  - RS01 to RS18: each refusal and the refusal order in CNL and JSON forms, plus the two compiler-v1 limit and offset refusals;
  - RP01 to RP04: compiler-v1 diagnostics that keep their precedence.

  Each case has an exact diagnostic with a byte span, line and column, plus a Python control body without `target` and its expected result.
- [case-inventory.json](case-inventory.json) has 129 acceptance cases: 4 public examples, 4 version-positive fixtures, 4 coverage cases, 9 name cases, 8 public products and the 100 in-scope legacy jobs.
  It also records which refusal every out-of-scope public product, fixture and legacy job gets.
- [acceptance-plan.json](acceptance-plan.json) has these gates:
  - `python_unchanged`, `rust_unchanged` and `typescript_unchanged`;
  - the target, scope and bundle checks;
  - the assemble refusals A1 to A16;
  - the product gates that every acceptance case runs: install, typecheck, format check, test, two identical builds, lock, product files and cache unchanged, and the API contract;
  - the issue gates, with the target amendments before the renderer.

  It samples nothing. A missing, failed or skipped check blocks acceptance.

The Python controls of the 30 negative cases and the 9 name cases were run read-only against the current default on 8765 on 2026-10-22: 39 passed, 0 failed. In M6, `--expect` with that evidence also needs each HTTP 200 control to give the same payload_sha256.

## 10. Decisions (2026-10-22, issue #68)

1. Phase 1 covers prompt versions 1 to 4. Versions 5 to 7 are refused with prompt_version.
2. TypeScript, Vite, React Router in declarative mode, a fetch client with hooks, one plain CSS file, and Vitest with jsdom and Testing Library. No Playwright. One npm download fills the vendored cache, and the lockfile is sealed at the freeze.
3. `react` joins the target enum through rust-output-v1 amendment 006 and typescript-output-v1 amendment 002.
4. `identifier_reserved` keeps `__proto__`, `constructor` and `prototype`, the same as the TypeScript target.

## 11. Freeze procedure

1. A person approved the scope and stack on 2026-10-22 (issue #68).
2. `python3 som-rs/tests/react-output-v1/check_contract.py --files` reports no failure. `check_python_controls.py --evidence` recorded the Python controls on 8765 in `som-rs/evidence/react-output-v1/`.
3. An independent reviewer checks the frozen files.
4. The receipt `som-rs/evidence/react-output-contract-freeze-2026-10-22.json` seals the files map, the review and the approval. `check_contract.py --receipt` checks it.
5. Phase 1 is split into issues: the target amendments, catalog and renderer, target and host, products, acceptance and release (acceptance-plan.json `issue_gates`).

After the receipt is sealed, a change to any frozen file needs a numbered amendment with its reason, the old and new hashes, and an independent review.

## 12. Amendment 001: list test assertion and changed API sample (2026-10-23, issue #132)

The user approved amendment 001 on 2026-10-23 after the first full 129-case product-gate run (#129) found two defects in the frozen test tools:

1. In `reference/src/app.test.tsx`, the list test "lists rows with the first page query" counted `screen.getAllByText(String(sample(first)))` across the whole page and expected `2`. When another column rendered the same text (for example, several `bool` fields all rendering `true`), the count was 3 to 7 instead of 2 (60 legacy100 products). The test now checks that the table has `2` data rows (`tbody tr`) and that in each data row the `FIELDS[0]` cell (the column right after `id`) has the text `String(sample(FIELDS[0]))`.
2. In `tests/react-output-v1/api_contract.mjs`, `sample(field, "changed")` returned the same value as the full row for `int` (`7`), `float` (`1.5`) and `date` (`2026-01-05`). When the changed field was `unique`, the step "patch changed field" failed with `409 conflict` against the full row (legacy100-0012 and legacy100-0030). The `changed` sample values are now `19` for `int`, `4.5` for `float` and `"2026-04-01"` for `date`; `str` stays `"changed <field name>"` and `bool` is unchanged.

Changed paths:

- `som-rs/contracts/react-output-v1/CONTRACT.md`: adds this section;
- `som-rs/contracts/react-output-v1/api-contract.json`: `samples` names the `changed` values `19`, `4.5` and `2026-04-01`; the 20 steps, their names and their `when` conditions are unchanged;
- `som-rs/contracts/react-output-v1/reference/src/app.test.tsx`: the list test checks the 2 `tbody tr` data rows and the `FIELDS[0]` cell text in each row;
- `som-rs/contracts/react-output-v1/toolchain.json`: `reference_verification` points to the 2026-10-23 reference verification evidence;
- `som-rs/tests/react-output-v1/api_contract.mjs`: `sample(field, "changed")` returns `19` for `int`, `4.5` for `float` and `"2026-04-01"` for `date`.

Added evidence (`som-rs/evidence/react-output-v1/`, keeping the 2026-10-22 files in the files map):

- `som-rs/evidence/react-output-v1/name-cases-2026-10-23.json`;
- `som-rs/evidence/react-output-v1/public-products-2026-10-23.json`;
- `som-rs/evidence/react-output-v1/reference-verification-2026-10-23.json`.

No other rule changes. The receipt is `som-rs/evidence/react-output-contract-amendment-001.json`.

## 13. Amendment 002: create test wait for detail GET (2026-10-23, issue #134)

In the #129 rerun on 2026-10-23 (`candidate-002` after amendment 001), 145/145 bundles and 127/129 products passed, and the 2 failures (`legacy100-0046` and `legacy100-0077`) came from a timing race in the frozen reference test "create > sends the required fields and opens the new row" (`reference/src/app.test.tsx`): after Save, the detail page heading `<Name> 5` renders as soon as the page mounts, while the detail GET starts later in a passive `useEffect`. `findByRole("heading")` could therefore resolve before the GET was recorded in `calls[1]`, producing `expected undefined to be '/api<path>/5'`.

In `reference/src/app.test.tsx`, the assertion on `calls[1]` now waits for the detail GET using `waitFor` from `@testing-library/react`:

```ts
await waitFor(() => expect(calls[1]?.url).toBe(`/api${RESOURCE.path}/5`));
```

The test keeps every assertion (the heading, the POST body in `calls[0]`, and the GET url in `calls[1]`), and no other test changes behavior.

Changed paths:

- `som-rs/contracts/react-output-v1/CONTRACT.md`: adds this section;
- `som-rs/contracts/react-output-v1/reference/src/app.test.tsx`: the create test waits for `calls[1]?.url` with `waitFor`;
- `som-rs/contracts/react-output-v1/toolchain.json`: `reference_verification` points to the `2026-10-23-a002` reference verification evidence.

Added evidence (`som-rs/evidence/react-output-v1/`, keeping all earlier evidence files in the files map):

- `som-rs/evidence/react-output-v1/name-cases-2026-10-23-a002.json`;
- `som-rs/evidence/react-output-v1/public-products-2026-10-23-a002.json`;
- `som-rs/evidence/react-output-v1/reference-verification-2026-10-23-a002.json`.

No other rule changes. The receipt is `som-rs/evidence/react-output-contract-amendment-002.json`.
