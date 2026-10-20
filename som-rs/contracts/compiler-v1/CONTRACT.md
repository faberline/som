# SOM CNL and DSL bundle contract v1 — issue #38 contract

This contract fixes the first compiler release. It was frozen before parser or planner implementation.
The root review and frozen file hashes are recorded in ../../evidence/rust-compiler-contract-freeze-2026-10-13.json. Implementation acceptance is separate.
The source-index file records the public sources used for this contract.
The [selection policy](selection-policy.json) and [review note](selection-policy.md) fix public constructor emission and primary block choices.
Root supplies the [canonical vectors](canonical-vectors.json), their [Rust qualification](canonical-vector-qualification.json), and the [full acceptance plan](acceptance-plan.json).
No old model formal corpus was read. Existing contract files and their hashes stay unchanged.

## 1. The two stage boundary

Stage one reads a complete CNL or JSON request, the approved catalog, and fixed compiler rules.
It returns a `som.dsl-bundle.v1` with the original typed `prompt`, an existing typed `dsl`, asset identities, and hashes.
The full prompt is the full binding context. The bundle must not replace it with flattened, inferred, or partially bound values.
Stage two verifies that prompt and plan together, binds names, and calls the current Rust renderer and formatter.
Stage two reads the saved bundle and its declared local assets. It does not read the original CNL or a model directory.
No stage inserts a missing business operation, authorization, transaction, inverse, branch, or result.
There is no new semantic IR. There is no model, Python production parser, Python production planner, or Python inference process.
The Python reference code can guide tests. The existing Python checkers remain part of acceptance.
The approved native formatter can run as a native subprocess. This does not make it a Python production process.

## 2. Input text and lexical rules

`grammar.ebnf` defines the sentences and block delimiters. `field-statement-map.json` fixes the mapping.
The first nonblank line is exactly `Use SOM CNL version 1.`.
The second nonblank line explicitly selects prompt version 1 through 7.
A request cannot change its version later.
Each sentence occupies one logical line. A line can contain the several periods in one fixed legacy sentence.
A period inside a quoted JSON scalar is data. It cannot terminate a sentence or block.
A small quote-aware scanner finds the final sentence period and token boundaries. Do not split a line on spaces or periods inside JSON strings.
The parser is line based. A bounded stack handles resource, query, route, action, transaction, and branch blocks.
A matching End step/transaction/branch/flow must repeat the exact opening name.
Sections occur in the order in the grammar. Parent precedes child. This order does not change object semantics.
Leading and trailing ASCII spaces and tabs are ignored. Runs of spaces/tabs between grammar tokens are separators.
Empty lines are ignored. LF and CRLF are accepted. Bare CR, BOM, comments, alternate spellings, case folding, and line continuation are rejected.
CNL keywords are case sensitive. Names and data are preserved exactly.
There is no Unicode normalization, translation, case change, or character substitution.
JSON strings permit valid JSON escapes and Unicode data, including Chinese. Reject invalid UTF-8 and unpaired surrogate escapes.
A scalar literal can be a JSON string, number, true, false, or null. Objects and arrays are not CNL literals.
Record structured inputs are declared by a type and supplied when the generated product runs. They are not embedded JSON request objects in CNL.
Only scalar data can appear after `literal`. Data is never executed as source or SQL.
Source, SQL, arbitrary expressions, loops, parallel nodes, block IDs, templates, filesystem paths for assets, and pronoun-based guesses have no grammar form.
Quoted data that contains source-like text is inert data, not an instruction. Do not invent a substring blacklist that changes legal string values.

Numbers follow JSON syntax. Reject nonfinite numbers and numbers outside the existing validator type range.
Preserve the JSON integer versus binary64 number category. Preserve the sign bit of `-0.0`.
A declared float can contain a JSON integer because the current validator permits it; retain that numeric representation rather than silently rewriting it.
Do not coerce true to 1, a string to a number, or null to an absent field.
A date literal is a valid proleptic Gregorian date from 0001-01-01 through 9999-12-31, written as a JSON string YYYY-MM-DD.
A bytes literal is a JSON string containing the current STANDARD Base64 format. Its decoded content is nonempty and at most 1 MiB.
The transport bound can reject such a large otherwise well-typed request before semantic validation. It is a separate bound.

## 3. Prompt families and exact omission rules

The following table controls which sections exist. A section not listed for the selected version is an error.

| Version | Resources | Queries | Routes | Checks | Capabilities | Flow |
|---|---|---|---|---|---|---|
| 1 | one resource | none | all five v1 legacy sentences, once each | exact v1 check | forbidden | forbidden |
| 2 | one resource | root exact-string filter and limit | all five v2 sentences | exact v2 check | forbidden | forbidden |
| 3 | one resource | root boolean filter and limit | all five v3 sentences | exact v3 check | forbidden | forbidden |
| 4 | one resource | root boolean/date filter and limit | all five v4 sentences | exact v4 check | forbidden | forbidden |
| 5 | parent and child | one query inside each resource | all eleven scoped related sentences | exact related check | explicit block or explicit Use no capabilities | forbidden |
| 6 | parent and child | one query inside each resource | one to eleven selected unique operations | forbidden | all six choices required | forbidden |
| 7 | parent and child | one query inside each resource | one to eleven selected unique operations | forbidden | all six choices required | exactly one named flow |

Preserve all route, field, input, and flow-node array order. Do not sort or repair arrays.
Versions 1–4 map the exact legacy sentences to the existing `logic` strings.
Version 5 maps `For ROLE: SENTENCE` to the existing `{scope,step}` records.
Version 6/7 maps `Allow ACTION on ROLE` to `{scope,action}`. `Allow batch creation` maps to batch/batch_create.
Legacy version 5 always has ten CRUD sentences plus one atomic batch sentence.
Deleting a row is an ordinary selected route. Deletion is not a flow operation.
The check sentences are fixed product check requirements, not proof of a passed check.

Names, resource name, table, path, field type, required flag, unique flag, migration revision, query selectors and limit are explicit.
A missing clause, repeated scalar clause, repeated field, repeated operation, repeated argument, or conflicting declaration fails. The parser cannot apply last-wins semantics.
Required versus optional and unique versus not unique always appear in the field sentence.
Required fields have no default key. A default clause on a required field fails.
Optional v1/v2 fields have no default key. A default clause on those versions fails.
Optional v3–v7 fields have a default key. An absent default clause means exactly JSON null.
The only other supported default is explicit false on an optional bool field.
Date fields are available from v4. In related versions they are optional, not unique, and default to null.
`Use no previous migration revision.` means JSON null. A named previous revision is allowed only in v1/v2.
`Use no exact filter.`, `Use no date filter.`, `Use no workflow value field.`, and `Use no workflow group field.` mean JSON null.
Versions 2/3 require an actual filter field. They cannot use no exact filter. Version 4 requires both actual filter and date fields.
Versions 5–7 allow null query selectors within their existing semantic rules.
Query max_limit is explicitly 20..100. There is no inferred default limit in the compiler request.
The generated product retains its business default limit of 20. This is not a compiler omission rule.
Version 5 `Use no capabilities.` omits the capabilities key. It preserves the existing plain related PostgreSQL path.
Versions 6/7 require explicit database, authentication, storage, workflow, value-field, and group-field clauses.
No compiler clause invents an authentication or storage choice.
No-input, no-export, and no-result markers map to empty arrays/maps. They cannot appear alongside populated clauses.
No other field, node, operation, or result can be omitted. Explicit empty flow steps still fail if authorization is absent.

Versions 6/7 require the exact public contract ID and SHA-256 clause.
Version 6 uses related-business.v1. Version 7 uses related-flow.v1. These bytes bind the existing contracts.
Version 7 also inherits related-business behavior through the existing base validator.
Other versions forbid a contract key because the existing JSON shape does not contain it.
A CNL request has the same acceptance/rejection and typed prompt as its equivalent JSON request.
This includes version-specific omitted keys. Do not insert query/capability/default fields into older formats.

## 4. Existing semantic rules stay binding

`compiler-prompt.schema.json` checks shape. Then run the current Rust version validators.
The schema is not an alternate behavior validator. A valid schema alone does not establish acceptance.
The field map contains every current prompt member, runtime-reference member, and action argument.
The following rules are required even when the JSON schema does not express them.

For v1/v2, fields use str/int/bool/float. Fields are nonempty and unique by name.
For v3, the same types plus the service default rules apply. V4 permits date.
The existing reserved-field and Python-keyword checks stay in force.
Related resources have 1..8 fields each. Package, table and custom-field names use the current lowercase pattern and reserved-name rules.
Resource class names use the current uppercase pattern and generated-name collision checks.
Related names have a maximum 40 characters under the inherited validator. Flow names and inputs are ASCII safe names up to 64 characters.
Do not impose a new 64-character limit on older v1–v4 names that the current validator permits.
Related paths have one to three lowercase route segments and permit hyphens and underscores.
Preserve route collision checks. Parent and child tables and paths must differ.
The child foreign key is managed by the backend and must not collide with custom fields or reserved names.
The relation is restrict deletion. Arbitrary relationships are out of scope.
An exact boolean query points to an optional, nonunique bool with default false. A date query points to the sole optional nonunique date.
A date filter requires the existing boolean filter. V2 uses a nonunique string filter.
A numeric workflow requires an existing numeric child value_field. Decimal summary also requires a required string group_field.
Other workflows must use null value/group fields as the current validator specifies.
Support PostgreSQL and MongoDB; JWT, Firebase, and Google OAuth; none/S3/GCS storage; JSON/gzip/CSV/decimal-summary/statistics workflows.
Secrets, provider credentials, endpoints, and source text are not compiler parameters.
The pinned related-business contract fixes access, error, transaction, provider and recovery behavior. CNL does not let callers override it with arbitrary error sentences.

Flow actions, exact argument names/types, result types and mandatory undo values are in `flow_operations` in the field map.
Argument order is not business order; argument keys form a map. There must be each required key exactly once and no extra key.
Runtime values use exactly three forms: input NAME; step NAME field JSON_STRING; literal TYPE JSON_SCALAR.
The first maps to `{input: NAME}`. The second maps to `{step: NAME, field: STRING}`. Empty field selects the entire step result.
Dotted field paths use the current member traversal. They cannot be expressions or arbitrary object navigation.
The third maps to `{literal: VALUE, type: TYPE}`. Literal null is invalid for the current six scalar literal types.
Inputs are string/integer/float/boolean/date or the five current record/list input types. Bytes are not a declared flow input type.
Results cannot expose bytes. Return metadata, workflow JSON data, or other currently supported values.
Input names, action names, transaction names, branch names, and export names share the current no-collision namespace.
Only declared inputs and visible earlier steps can be read. Forward references and cross-arm reads fail.
Transaction children remain visible after the transaction. The transaction node itself is not a value-producing step.
A branch exposes only its named exports. Both arms must produce the same export type.
Exports may select an input, typed scalar literal, or visible value in the relevant arm.
An is_null test needs a nullable scalar reference. Its else arm refines that same reference to nonnull. That refinement does not escape the arm.
Comparisons require matching scalar types. Bytes cannot be compared. Ordered comparisons reject nullable values and booleans.
Predicates map to eq/ne/lt/le/gt/ge/is_null, without any additional operators.
Authorization is the first action and occurs once, before branches or transactions. A compiler cannot add it when absent.
Database mutations occur inside a named nonnested transaction and declare the exact required inverse.
Provider calls cannot occur inside a database transaction. Object calls require selected storage. CSV import requires CSV.
An object create uses delete_created. Patch uses restore_patch. Reads, authorization and workflow.run use null undo.
The fixed related-flow contract governs journal order, idempotency, owner access, lease/claim safety, reverse execution, provider versions and recovery.
A syntactically valid request cannot weaken those rules.

## 5. Budgets and legacy compatibility

| Bound | Rule |
|---|---|
| Compile and legacy generate request body | 262144 bytes, same existing transport bound |
| New assemble request body | 2097152 bytes; bundle repeats prompt and DSL, so it has its own bounded transport allowance |
| CNL source | Limited by its encoded HTTP envelope; no model token cap; parser functions need no extra arbitrary line cap |
| DSL v1 | 1..8 files and at most 64 blocks |
| DSL v2/v3 | 1..12 files and at most 64 blocks in total, not 64 per file |
| Related custom fields | 1..8 per resource |
| Selected operations | 1..11 unique operation pairs |
| Flow inputs | at most 8 |
| Flow actions | at most 8 in the whole tree, including authorize |
| Branches | at most 2 total and branch depth at most 2 |
| Transactions | at most 2 total, never nested |
| Legacy v2 complete source bytes | Preserve the sealed 65536-byte check at the same backend/quality points |
| Compact v7 source bytes | Observe final complete formatted UTF-8 bytes; no new 65536-byte gate |

V1 has no output byte check in the existing Rust assembler. Do not add a new one by accident.
The model-era 2048 input/output token limits and model codec calls are retired only on the new compiler path.
Do not use codec encoding as a proxy for structural validation. Run the existing semantic validators directly.
Measure complete files, response latency and memory. Size and time observations are not invented acceptance gates.
The current endpoints retain their old request shapes, response shapes, and error codes/status mapping.
Do not edit historical prompt/DSL schema files or contract text. Their hashes bind the retained M2 service.

Public schema drift is explicit: historical v7 schema fixes max_limit=100, but Rust accepts 20..100.
That schema also excludes hyphen route segments that the inherited validator accepts.
The new compiler family schema describes currently accepted Rust semantics and applies existing semantic validation.
It does not change legacy contract IDs or their hashes.

## 6. The bundle and asset binding

`bundle.schema.json` fixes the wire object. It has only version, prompt, dsl, assets, and hashes.
The prompt is unchanged semantic JSON with all typed values. The DSL is the existing versioned plan, including all selected files, blocks, refs, flow nodes and inverses.
The compiler chooses DSL v1 for prompt v1/v2; DSL v2 for v3–v6; DSL v3 for v7. Prompts v1–v5 use the approved legacy v5 block library, which retains earlier blocks. V6 uses the approved composed-quality catalog/block family. V7 uses flow-v7-compact catalog slot paths and bindings directly, including models at `{package}/schema_v001.py`; it does not force the historical noncompact path or IDs.
This choice matches the current reference file families. Assembly additionally accepts any already-supported inner pairing only if its current validator and approved manifest admit it; it never converts versions silently.
A compile result always uses the fixed pairing above, for both CNL and JSON.

Each asset descriptor has an immutable versioned ID and lowercase raw-byte SHA-256. IDs are looked up only in the installed local manifest.
There are descriptors for grammar, compiler_contract, selection_policy, catalog, block_library, backend, formatter, literal_policy, and serializer.
The semantic_contracts array contains sorted descriptors for every inherited public behavior contract used by that request family.
The catalog descriptor binds a manifest that fixes the catalog JSON files and all per-file raw hashes for that family.
The block_library descriptor binds a manifest with every approved block ID/version and source hash, in canonical sorted order.
The backend descriptor hashes the external backend-source-manifest.json with its renderer/validator source and embedded-template hashes. The manifest is not embedded in the sources it hashes. This avoids a self hash cycle. Runtime packages may substitute a separately pinned build manifest with the same explicit source group.
The formatter descriptor binds the native formatter binary, profile, version and dependency policy through its manifest.
For an unformatted legacy family use an explicit versioned render-only manifest; do not fabricate a zero hash or omit the role.
The literal_policy descriptor binds existing Unicode/literal encoding choices. The serializer descriptor binds section 7.
These artifacts are packaging metadata. They do not replace the prompt, DSL, catalog semantics, or existing block bytes.
No asset URI, absolute path, network fetch, model path, or embedded asset body occurs in the bundle.
The compiler selects the only installed policy manifest for the prompt version. The caller cannot supply an alternate catalog or planner policy during compile.
The policy manifest freezes exact legacy block choices and ordered common/operation/flow rules before planner implementation. Hash serializer-policy.json and literal-policy.json as their own external artifacts. Do not put a file’s own hash into that file.
Reuse the current public reference choices for legacy byte parity. Do not take choices from model answers or acceptance expectations.
The frozen selection-policy.json binds standard primary choices and exact ordered emission recipes from the public constructors. Compile chooses standard flow helpers deterministically. Where an existing catalog offers legal alternatives, the v1 policy names one fixed primary per semantic role and capability tuple. No scoring, lookup by case ID, or first filesystem match is permitted.
If more than one primary is declared or none satisfies the request, compilation fails. It does not infer a policy.
The root reviewed and froze selection-policy.json before #40 implementation. It contains source-bound constructor recipes and exact primary flow mappings, not a production Python call. The freeze does not claim that the Rust implementation exists.
Assembly validates the admitted prompt/DSL/catalog semantics, not equality to the compiler primary plan. Both legal standard and checked alternative DSL remain valid assembly inputs. Compile has one standard choice. Preserve each alternative archive as its own assembly/parity scope; one compile result cannot be required to equal both outputs.
The backend cannot replace a referenced catalog with a newer one. Missing assets, altered blocks, unknown IDs, incompatible versions or hashes fail before rendering.
For legacy v1–v5, binding/assembly must also prove all requested supported routes exactly once, query choices, complete required files, and the declared field/migration context. A general block renderer alone does not prove that a supplied plan meets the prompt.
Matching hashes identify bytes, not trusted approval. Assembly must also admit each ID/hash through the active approved manifest.
A rehashed malicious or altered bundle is not valid merely because its own hash checks pass.

## 7. Canonical serialization and hash formulas

Use `som.canonical-json.v1`: the existing Rust `flow::canonical` behavior, pinned to the public source hash in source-index.json.
Objects use key order from an ordered map, recursively. No spaces are emitted. Arrays retain their order.
String encoding uses the current serde_json JSON string encoding, with Unicode preserved and no normalization.
Retain integer versus f64 representation, including f64 -0.0. Floats use the current shortest finite round-trip spelling and the existing negative-exponent adjustment.
In particular, tiny decimal f64 values below 1e-4 use scientific notation and negative exponents have at least two digits, as in the existing canonical routine.
This is not RFC JCS: JCS would collapse representations that this contract must retain.
Do not use a different language's generic JSON serializer without exact vector checks.
Reject duplicate JSON keys in new compile/bundle envelopes rather than allowing a hash over an ambiguous parse.
Preserve existing raw generate JSON parsing behavior for compatibility.

Let C(x) be those canonical UTF-8 bytes without trailing LF. Let H(x)=lowercase SHA-256(C(x)).
`prompt_sha256 = H(bundle.prompt)`.
`dsl_sha256 = H(bundle.dsl)`.
`payload_sha256 = H({version,prompt,dsl,assets})` using the exact four members from the bundle.
The hashes member is outside the payload hash. There is no self-reference.
Source text, source format, source locations, line endings, request time, tool logs and measurement values are not in the bundle.
Equivalent CNL and JSON prompts under the same assets therefore produce identical complete canonical bundles.
Different operation order, field order, type, literal value, null/default or pinned asset hash changes the relevant bundle hash.
Pretty JSON can be used on the wire. Verify hashes from decoded typed values, not pretty wire bytes.
`canonical-vectors.json` fixes representative scalar and ordering behavior. Root independently qualified these vectors against the current Rust canonical routine. The qualification record is copied alongside them. No CNL parser or new compiler run is claimed by this freeze.

## 8. HTTP, CLI and errors

All CLI commands use the local HTTP service. Preserve the current loopback-only HTTP URL rule and disabled redirects.
They do not run a fallback local parser, call a model, write caller project files, or fetch assets.

| Request | Exact request body | HTTP 200 response |
|---|---|---|
| POST /v1/compile CNL | {"format":"som.cnl.v1","source":"CNL text"} | {"status":"ok","bundle":B} |
| POST /v1/compile JSON | {"format":"som.prompt-json.v1","prompt":P} | same compile success shape |
| POST /v1/assemble | {"bundle":B} | {"status":"ok","files":{"relative/path":"complete contents"}} |
| POST /v1/generate | existing raw prompt P, no new envelope | existing status/files success shape |

No content sniffing or guessed format is allowed. The CNL header and the envelope format must agree.
Compile is all-or-error. Assemble returns every complete file only after all checks and formatting pass.
`som compile <cnl-file>` sends the CNL envelope. On success it prints only the bundle JSON plus LF on stdout, so it can become an assemble input file.
`som assemble <bundle-file>` reads the bare B object, sends the assemble envelope and prints the complete response JSON plus LF.
`som generate <prompt-file>` preserves its current request/response behavior and exit codes.
New commands use exit 0 for success, 2 for an HTTP rejection, and 1 for a local I/O/connection/decoding failure, consistent with generate.
On failure compile/assemble print a complete compiler error JSON to stderr and no partial bundle/files to stdout.
CLI parsing errors retain the command parser's existing invocation behavior. Source filenames are not used to infer format.
The source and bundle file are input only. The server never writes a product into the caller's project.

A new compiler error keeps the existing flat status/code/detail fields and adds one diagnostic record:
`{"status":"error","code":"CNL_SYNTAX","detail":"...","diagnostics":[{"code":"CNL_SYNTAX","line":1,"column":1,"end_line":1,"end_column":22,"byte_start":0,"byte_end":21,"prompt_pointer":null,"cause":"Unknown sentence.","allowed_form":"Use SOM CNL version 1."}]}`.
Line/column are one based. Columns count Unicode scalar characters, not UTF-8 bytes or visual tab widths.
End positions are exclusive. Byte offsets are zero based in the original decoded source UTF-8 text.
CRLF counts as one line boundary. Escaped string contents retain the source span of their literal token.
For invalid UTF-8 HTTP JSON the envelope decoder fails; no CNL source span exists.
For JSON and bundle requests report their raw JSON token span and JSON pointer where available.
A new transport/envelope error without a decoded source reports line=column=end_line=end_column=1 and byte_start=byte_end=0, prompt_pointer=null; cause states that no source was decoded.
For a missing clause locate the enclosing End block or EOF; do not blame an unrelated line.
For a semantic failure locate the offending clause via a parser-produced JSON-pointer/source-span map.
This source map is diagnostic metadata in memory. It is not a new semantic IR and is not included in the bundle.
A wrong reference locates that reference token. A missing undo locates End step. A missing first authorization locates the first action or End steps if empty.
The frozen negative cases in ../../tests/compiler-v1/cnl-negative-cases.json define exact spans and error classes before implementation.

Return the first error. Lexical/syntactic errors come before semantic errors. Missing/duplicate clauses follow source traversal. Existing semantic checks use their stable validator traversal.
Do not repair the request or silently drop unknown statements, fields, references, results or limits.
CNL_SYNTAX, CNL_MISSING, CNL_DUPLICATE, CNL_BLOCK_MISMATCH, INVALID_JSON, INVALID_SCHEMA and INVALID_VALUE return 400.
Reuse existing semantic error codes such as INVALID_REF, INVALID_AUTHORIZATION, INVALID_TRANSACTION, INVALID_COMPENSATION, INVALID_COMPOSITION and INVALID_PATH; they retain existing 400 mapping.
UNSUPPORTED_VERSION, UNSUPPORTED_CONTRACT, UNSUPPORTED_LOGIC, UNSUPPORTED_TEST, UNSUPPORTED_BLOCK and SCOPE_TOO_LARGE retain 422 mapping.
BUNDLE_HASH_MISMATCH and ASSET_HASH_MISMATCH return 400. ASSET_UNAVAILABLE returns 503. OUTPUT_QUALITY_FAILED returns 500.
Compile and legacy generate bodies above 262144 bytes return 413. The new assemble envelope has a 2097152-byte bound and returns 413 above it. New compiler transport errors use the explicit compiler error envelope; the old generate path keeps its current transport behavior. These bounds are not model-token limits.
Before compile returns success, compute the serialized canonical UTF-8 byte count of `{bundle:B}`. If it exceeds 2097152, return SCOPE_TOO_LARGE with HTTP 422. No successful compile result may require an over-limit canonical assemble request. CLI uses that canonical bounded envelope for assemble. A caller can still exceed the HTTP wire bound by adding unnecessary whitespace and receives 413.
The old generate errors retain their exact current flat envelope and status mapping. They need not gain new diagnostic fields.
The new compiler's health report declares purpose som.rust-compiler.v1, supported prompt versions and asset identities/hashes.
It cannot claim a loaded model. Legacy M2 health remains unchanged until the tested default switch.

## 9. Freeze and acceptance requirements

Before parser/planner implementation, root must freeze this grammar, field map, schemas, default/refusal rules, selected policy table, canonical vectors, example mappings, case selection rules and independent checks.
All example pairs here are expected mappings. No parser run or product behavior pass is claimed.
The eight initial pairs cover v1–v7 and nullable branch export. They are design examples, not the complete acceptance inventory.
The complete independent inventory must cover every field, capability tuple class, operation and argument, scalar type, comparator, reference shape, empty/nonempty export/result, branch path, transaction, undo and current limit boundary.
Include Unicode, escapes, -0.0, integer/float distinctions, default null/false, null refinement, scoped references and altered asset hashes.
Reject all malformed blocks, versions, duplicate/conflicting clauses, unsupported source/SQL forms, wrong types, unknown/missing refs, cross-arm reads, missing auth, unauthorized/nested transactions, missing/wrong inverses, provider-in-transaction and unsupported capability cases.
Use requirements and independent product/provider/database observations. The generated code or DSL is not its own behavior oracle.
Maintain the retained 100+214 regression inventory and M2 formal114/dev12/supplement12/quality218 scopes exactly as the roadmap says.
Retained receipts remain history. A new compiler run needs its own source/grammar/policy/catalog/tool/bundle hashes and actual outcomes.
Fresh M2 restore and compiler HTTP/CLI canary must pass before a default switch. No paid agent runs or real cloud are required.
Root owns inspection, installation, freeze, acceptance, GitHub and default switching. The contract freeze alone does not close the implementation or rollout issues.

The baseline backend-source-manifest.json records review inputs. A release backend descriptor must bind the actual final compiled source and embedded templates, including the new compiler modules. It cannot claim those baseline hashes identify modified implementation bytes.

## 10. Amendment 003: names of the generated Python code

Amendment 003 (issues #57 and #58) adds two rules. Its receipt is ../../evidence/rust-compiler-contract-amendment-003.json. Sections 1 to 9 stay binding.

A prompt name can share a Python scope with an identifier of the generated code: a module import, class, helper, builtin or query parameter.
Then one of them shadows the other, and the product fails at import or at run time, or works only because of the template layout.
`selection-policy.json` `python_name_collisions` lists those names per prompt version and database, with the name slots, their roles and the traversal order.
Compile refuses the first listed name with INVALID_VALUE, HTTP 400, detail `prompt.<path>: name collides with a generated Python identifier`, and the slot's JSON pointer.
A CNL request reports the same code and detail with the pointer and span of that name's clause. Its allowed_form is `Rename it; this name shares a scope with an identifier of the generated Python code.`
The refusal is a compiler-v1 rule, so it applies to every target, including `target: rust`.
It replaces the Rust query_parameter_collision refusal for v3 `limit` and `offset` filters (RS12 and RS13).
A version and database without rows refuses nothing. Names are compared as written.

The name `self` stays valid for a resource field and for relation.foreign_key.
The generated Python passes model values as keyword arguments to constructors and to `filter_by`, whose first parameter is `self`.
`selection-policy.json` `self_name_variants` lists, per family, a variant block for each such base block.
Its constructor takes the row positionally, or its query matches columns without keyword arguments.
Compile selects the variant exactly when a resource field or relation.foreign_key is `self`; every other prompt keeps its earlier DSL.
Assembly refuses a plan that uses the base block where the variant is required, or a variant where it is not, with INVALID_COMPOSITION.
The variants are new block ids in the approved v5, composed-quality and flow-v7-compact libraries. No existing block changes.

Both rules change the selection policy, the backend and the block libraries, so every bundle's asset descriptors and payload hash change.
For every accepted prompt the DSL and every assembled product file stay byte-identical.
An earlier bundle presented to a backend with this amendment fails ASSET_HASH_MISMATCH. That is a recorded compatibility break; compile the prompt again.
The fixtures are ../../tests/compiler-v1/self-name-fixtures.json, written by self_name_fixtures.py from public examples and CNL fixtures.
They hold 15 `self` cases, 13 refused names and 3 accepted controls, with the expected code, detail, pointers and variant ids.

## 11. Amendment 004: the retained M2 block library

Amendment 004 corrects where amendment 003 put the flow-v7-compact variant. Its receipt is ../../evidence/rust-compiler-contract-amendment-004.json. Sections 1 to 10 stay binding.

`som-code-python/data/blocks/flow-v7-compact/blocks.json` is the block library of the retained M2 service: `blocks/current` points to it, and its hash is the M2 health `blocks_sha256` that a fresh M2 restore must report (section 9).
Amendment 003 appended its variant to that file, so a fresh M2 restore no longer matched the accepted M2 health.
That file again holds the bytes M2 accepted. The flow-v7-compact library that compiler-v1 embeds as `blocks.json` is `som-code-python/data/blocks/flow-v7-compact-self/blocks.json`: those bytes followed by the variant `compact.service.flow.store.postgresql.self`.
It holds exactly the bytes that amendment 003 gave the M2 file, so the embedded `blocks.json` asset is unchanged.
`som-core/scripts/build_self_name_variants.py` reads the base blocks from the M2 file and writes the variant to the copy; it refuses an M2 file that holds a variant.
The v5 and composed-quality libraries do not bind M2 and keep their variants in place.

Only the backend source and this contract text change, so the backend and compiler contract descriptors and the payload hash of every bundle change again.
For every accepted prompt the DSL and every assembled product file stay byte-identical. An earlier bundle fails ASSET_HASH_MISMATCH, as in section 10.
