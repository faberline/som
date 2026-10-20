# SOM Rust #38 typed JSON oracle inventory

This is a pre-implementation contract aid. It contains hand-authored typed JSON prompts, semantic expectations, and frozen CNL diagnostic cases. It contains no saved answer DSL. Expected diagnostic positions are proposed contract values, not compiler observations. It does not claim that a future compiler passed.

## Coverage

- **v1:** exact five CRUD steps; nonempty route and field list; unique safe field names; no current v1 field-count maximum; migration down_revision may be null or identifier
- **v2:** filter_field references non-unique str field; max_limit integer 20..100; exact string filter
- **v3:** optional fields require default null or false; false default only for bool; filter_field references non-unique bool with default false; down_revision must be null
- **v4:** exactly one optional, non-unique date field with null default; date_field names it; filter_field cannot use reserved query names; date-before is exclusive; down_revision null
- **v5:** parent and child names/tables/routes distinct; 1..8 fields per resource; parent and child CRUD plus atomic batch; restrict parent deletion; field names cannot be reserved or equal FK; max_limit 20..100; at most one optional null-default date per resource; date filtering needs bool filtering; secret inputs prohibited by contracts
- **v6:** contract id/hash pinned to related-business.v1; 1..11 unique scoped operations; preserve request order in planning and require bundle operation order to match; operations parent|child create/get/list/patch/delete and batch_create; same v5 resource/relation/business semantics; capability enums database postgresql|mongodb; auth jwt|firebase|google_oauth; storage none|s3|gcs; workflow json_export|csv|gzip_export|decimal_summary|statistics; numeric workflows require child numeric value_field; decimal_summary also requires required string group_field; unused fields must be null
- **v7:** at most 8 typed inputs; input types boolean|child_input|child_patch|children_input|date|float|integer|parent_input|parent_patch|string; at most 8 actions, 2 if nodes/depth 2, 2 nonnested transactions; all names unique ASCII Python identifiers <=64, no leading underscore/model_; authorization first and once; database writes in transaction; exact undo delete_created/restore_patch; branch arms same export names/types; no forward/cross-branch refs; actions: authorize,parent.create,child.create,batch.create,parent/child.get|list|patch,workflow.run|import,object.put|get; typed input/step/literal refs; literals distinguish type including negative zero; comparisons exact compatible scalar; is_null only nullable; object operations require configured storage and stay outside database transaction; csv import requires csv capability; no delete op; no source code, SQL, loops, parallel nodes, block IDs in prompt; results cannot expose bytes. DSL v1 keeps 1..8 files and at most64 blocks. DSL v2/v3 keep 1..12 files and at most64 blocks total. Flow is at most8 actions,2 branches/depth2, and2 nonnested transactions. Compact v7 formatted bytes are observed; there is no new 65536-byte gate or token limit on the compiler path. Existing noncompact legacy byte limits remain on their old paths.

The fixture pack has 40 cases: one positive case for each prompt version v1–v7, plus typed refusal cases. The machine-readable case records include RFC 6902 patches, expected outcomes and the observed Python validator result. The cases cover null vs typed values, bool vs integer, float `-0.0`, references, ordering, cross-branch visibility, authorization, transaction scope, undo, unsupported input/action, capabilities and result boundaries.

## Output and runtime checks

- **B01_DETERMINISTIC_REPLAY:** same canonical typed prompt and same pinned catalog/contract hashes produce byte-identical complete DSL v3 bundle.
- **B02_APPROVED_BLOCKS_ONLY:** every block id is in the pinned catalog and its exact parameter names/types and source hash match (`UNSUPPORTED_BLOCK or INVALID_BLOCK`).
- **B03_THREE_REFERENCE_KINDS:** generation binding {$ref}, runtime input/step/literal refs, and dependency {symbol} remain distinct typed forms (`INVALID_REF`).
- **B04_NO_IMPLICIT_COMPLETION:** missing requested operation, helper, inverse or output file is rejected; validator does not insert it (`INVALID_COMPOSITION`).
- **B05_ORDER_AND_SCOPE:** preserve ordered fields, operations, flow steps and branch scopes; reject forward/cross-branch edges; enforce DSL v1 limits of 1..8 files and 64 blocks, DSL v2/v3 limits of 1..12 files and 64 blocks total, plus 8 actions, 2 branches/depth2, and 2 nonnested transactions (`INVALID_REF or SCOPE_TOO_LARGE`).
- **B06_RUNTIME_EQUIVALENCE:** CNL-to-typed-prompt semantics and CNL-to-bundle/runtime semantics agree for auth, CRUD, filters, relation, capabilities, flow arguments/results and error outcomes.
- **B07_REFUSAL_NO_REPAIR:** unsupported, missing, conflicting, invalid-typed or out-of-scope requests fail with precise location and code; no model fallback or answer repair (`INVALID_VALUE / UNSUPPORTED_LOGIC / INVALID_REF / INVALID_COMPOSITION`).
- **B08_OPERATION_ORDER_MISMATCH:** bundle operation order must equal prompt operation order (`INVALID_COMPOSITION`); assertion-only until the full catalog plan exists.
- **B09_VALID_ALTERNATE_PLAN:** assembler accepts a semantically valid alternate approved block plan, when all identities/parameters are pinned and requested operations are fully covered; compiler output remains fixed to the standard plan.
- **B10_REQUEST_COVERAGE:** assembler rejects any omitted requested operation and any operation reordering when the prompt contract requires order.
- **B11_ALTERNATIVE_EXPECTATION_PARITY:** verify both alternative-standard and alternative-checked original variants through valid pinned-bundle assembly and archive byte parity; compare compiler output semantic and byte parity to the standard expected variant.
- **B12_CANONICAL_JSON:** use the qualified vectors in `../../contracts/compiler-v1/canonical-vectors.json` and `canonical-vector-qualification.json`. Retain integer versus float, negative zero, Unicode and escape bytes, deterministic map-key order, array order, shortest finite float spelling, and adjusted negative exponents. Qualification was against `som-rs/src/flow.rs` at the pinned source hash; it does not validate a future serializer.
- **B13_SOURCE_AND_ASSET_BINDING:** CNL and typed JSON for one prompt produce the same canonical bundle. Repeat in separate processes. A saved bundle assembles without CNL or model files. Reject altered, missing, extra, unknown, or rehashed malformed assets and payloads. Hashes do not establish approval; local manifests and semantic validation still apply.
- **B14_RUNTIME_BOUNDARY:** new compiler and generator remain Rust-only. They do not call a model or Python production planner. Native formatter identity/profile must match the pinned asset. HTTP stays loopback, refuses redirects, enforces 262144-byte transport, and returns complete error envelopes with no partial outputs.
- **B15_END_TO_END_AND_RELEASE:** execute all 544 original jobs and independent behavior/quality scope, compare complete prior products, and leave failures/missing evidence visible. Before release, bind per-item hashes, run the independent audit and fresh legacy restore/canary, then switch only after all gates pass with automatic restore on failure.

## Suite accounting

- Legacy 100 + 214 = 314 regression jobs.
- M2 formal 114 and development 12 are disjoint subsets of quality 218.
- Supplement 12 is separate.
- Distinct M2 products: quality 218 + supplement 12 = 230.
- Four disjoint accounting groups are legacy100, legacy214, quality218, supplement12; baseline jobs are 100+214+218+12=544.
- Within quality218, each original prompt can have alternative-standard and alternative-checked expected variants. Compile deterministically selects standard; it cannot choose by case ID. Test both variants by valid pinned-bundle assembly and archive byte parity. Compare compile semantics and bytes with standard expected output.
- Existing full behavior scope covers 126 products in quality218 plus the 12 supplements. The other 92 quality products retain quality and parity scope only; do not call them full behavior passes.
- No formal corpus contents or saved answers were read.

## Known gaps and drift

- v5 has no standalone JSON Schema file in this checkout. Its Python validator defines current behavior.
- The v7 schema says `max_limit` is exactly 100 and its route regex excludes hyphens. The Python v5/v6/v7 validators accept 20 through 100 and hyphen routes. The fixtures test those current-validator cases. Keep the schema/validator mismatch visible for contract integration. Do not change old hash-bound schemas as part of #38.
- v6 has a public schema file. v7 schema validation rejects the hand-authored hyphen routes and max_limit 20 even though the Python validator accepts them. The validator result and schema drift are recorded separately.
- CNL syntax and semantic coverage follow Sol's draft `grammar.ebnf` and `field-statement-map.json`. Frozen negative inputs and expected one-diagnostic positions are in `cnl-negative-cases.json`; these expectations were independently reviewed before this freeze. Implementation must satisfy them.
- Flow operation, capability, and runtime-boundary assertions are in `flow-operation-capability-assertions.json`. They enumerate the public field map but do not prove a future compiler or generated bundle.
- The assertion pack covers all 14 public flow operations and all 90 combinations from the public database/authentication/storage/workflow enums. It separately states conditional refusals for numeric fields, CSV import, and object storage.
- `cnl-negative-cases.json` has 15 negative cases. Every span was recalculated from the pinned v7 example and checked against its UTF-8 byte offsets. Sol independently reviewed source positions and pointers before freeze. The two missing-authorization cases preserve error order: a standalone first action expects `INVALID_AUTHORIZATION`, and a transaction before authorization expects `INVALID_TRANSACTION`.
- `serializer.py` creates CNL from hand-authored typed prompts using the saved grammar and field-statement map. `cnl-positive-fixtures.json` freezes 119 pairs: 7 version positives, all 90 capability tuples, all 14 flow actions, six scalar comparators plus nullable `is_null`, and one matching branch-export case. Every typed prompt passed both the existing public Python prompt validator and the qualified Rust typed-prompt validator probe; detailed Rust records are in `rust-validator-results.json`. No future CNL compiler or parser was imported. The serializer takes explicit contract, fixture, and output paths; it rejects unsupported relation delete behavior and changed fixed test clauses instead of normalizing them.
- The independent serializer matches Sol's v1–v6 example CNL byte-for-byte. Its v7 and v7-branch-null output differs only in the order of map-shaped argument/result/export clauses. The manifest records exact example hashes, first differing lines, and a normalization check that permits only map-clause order changes. Compare resulting typed JSON semantics; preserve array order.
- The current `grammar.ebnf`, v6/v7 examples, and `CONTRACT.md` all place parent before child. Earlier task notes reported child-first examples, but that is not true of the files inspected for this audit. Recheck source hashes at final integration.
- The grammar is intentionally broad across versions. `CONTRACT.md` supplies version-specific section rules: query blocks, contract binding, capabilities, checks, routes, and flow. `Use no capabilities.` omits the key and must not substitute for required v6/v7 capability fields.
- `CONTRACT.md` now defines 1-based Unicode-scalar columns, exclusive end positions, and zero-based UTF-8 byte offsets. The 15 CNL cases use that convention and the draft error classes. Sol independently reviewed the source positions and pointers before freeze; the findings and exact corrections are recorded at `the local contract-audit/cnl-negative-review-before-fixes.json record`.
- Bundle invariants name code families, but the full catalog-dependent DSL bundle fixture is still open work for contract integration. Operation-order mismatch is an assertion-only case because a valid full bundle needs the future catalog planner output.
- Budget clarification: `m2-output-budget.v2.json` is compact-M2-candidate metadata. Its 12-file and 64-block limits apply there; its `bytes: observed` field is not a byte ceiling, and its input/output token limits are not compiler limits. Existing noncompact 65536-byte limits remain attached to their existing legacy paths.
- Future compiler acceptance requires fresh independent behavior tests. These fixtures are not acceptance results.
- Root's acceptance plan remains the source for parser, planner, bundle, runtime, baseline, and release gates. The artifacts here add independent oracle details; they do not replace those gates or claim completion.

## Source files

- `som-core/src/som_core/v1.py`
- `som-core/src/som_core/related.py`
- `som-core/src/som_core/business.py`
- `som-core/src/som_core/composed.py`
- `som-core/src/som_core/flow.py`
- `som-core/src/som_core/rule_planner.py`
- `som-rs/schemas/som.prompt.v1.schema.json`
- `som-rs/schemas/som.prompt.v2.schema.json`
- `som-rs/schemas/som.prompt.v3.schema.json`
- `som-rs/schemas/som.prompt.v4.schema.json`
- `som-rs/src/related.rs (v5; no standalone schema file)`
- `som-rs/schemas/som.prompt.v6.schema.json` and `som-rs/src/composed.rs`
- `som-rs/contracts/som.prompt.v7.schema.json`
- `som-rs/contracts/som.dsl.v3.schema.json`
- `som-rs/contracts/related-flow.v1.md`
- `som-rs/contracts/related-business.v1.md`
