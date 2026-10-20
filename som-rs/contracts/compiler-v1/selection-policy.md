# Selection policy review note

`selection-policy.json` is the compile rule artifact for issue #38.
Its raw-byte SHA-256 is `a24c82bd11f3c873107d6bfbdc93549c80e3452281adb11f6cae1cbd067e1376`.
This replaces every earlier hash. The artifact has no self hash.

Versions 1 to 4 use the fixed file and block table.
`declared_params` records approved types.
The emitted DSL uses the version's typed binding map.
Version 5 uses the related layout and explicit business overlay.
Its API table fixes database, operation, and query choices.

Version 6 has 95 public quality primary block rows.
Version 7 has 151 public compact primary block rows.
The flow table has 37 standard operation/inverse rows.
Compile excludes four checked get IDs.
Assembly can still admit those alternatives through semantic validation.
Assembly does not require equality to a compile primary plan.

The generic catalog solver changes refs order.
It also moves the Mongo migration file.
The policy now freezes the exact public constructor layout.
It preserves inherited file/node positions, names, and refs order.
The v7 helper order is workflow helper, engine, then sorted selected IDs.
Journal additions keep the exact public append/insert positions.
The 95-row base ID map fixes final compact IDs.
Final bindings and paths come from the compact catalog.

The models path is `{package}/schema_v001.py`.
The schemas path is `{package}/schemas.py`.
The helpers path is `{package}/flows.py`.
Use the compact resolver and assembler for version 7.

`public-qualification.json` records the design checks.
It uses only the public composed prompt and explicit design variations.
The v6 solver differs from the public reference on both databases.
The two examples have equal unformatted source bytes.
No general or formatted-output equality is claimed.
Four v7 variants reproduce the full public standard-to-compact remap exactly.
They cover both databases, standard get, transaction create, and GCS object put.
Database and storage delete_created inverses stay separate.
All 151 standard/compact metadata rows match on nine semantic traits.

No model or training/formal corpus was read.
No source or protected worktree file changed.
No Rust build or product test ran.
Root must inspect and freeze this policy before implementation.

The scratch generator `write_policy.py` predates the final revisions.
Do not rerun it or treat it as planner code.
A cap guard rejected scratch cleanup and one policy shell update.
Inspectable file patching completed the required update.
No necessary work remains blocked.

## Version 1.2 (amendment 003)

Amendment 003 (issues #57 and #58) makes `selection-policy.json` version `som.compiler-selection-policy.v1.2`.
Its raw-byte SHA-256 is `6fc29cf4791de4e2c6ec716a03426cf66cb1bdd0c5659c30511b837da8552f56`.
The version before it was `58508e4e75a05e972b8cd8e114149190d071fc7ac716ee272c2f782d83240a36`, archived at `../../evidence/rust-compiler-contract-archive/selection-policy-v1.1-before-amendment-003.json`.
The hash at the top of this note is the 2026-10-13 freeze value; amendment 001 replaced it with the archived bytes.
Every earlier member is unchanged except `version` and `sources.legacy_blocks_raw_sha256`, the raw hash of the v5 block library, which gains the two v1 to v5 variant blocks.

`python_name_collisions` lists names that share a Python scope with an identifier of the generated code.
Rows are per prompt version and database, each a set of roles and the names refused for a slot with all of them.
The tables come from a symbol-table analysis of neutralised public, legacy, fixture and example products.
Each name was then checked against the templates. Some listed names work today only because of field order or an import alias; they are refused anyway.
On every public, legacy, fixture and example prompt and every Rust case, the tables refuse only the RS12 and RS13 query names.

`self_name_variants` lists the base blocks that pass model values as keyword arguments to a parameter named `self`.
For each it gives the variant block and both source hashes.
The variants are `orm.base.self` and `orm.mongo.base.self` for v1 to v5, `orm.base.self` and `orm.composed.mongo.base.self` for v6, and `compact.service.flow.store.postgresql.self` for v7.
The v6 MongoDB documents subclass beanie's Document, whose constructor takes `self` by name; a batch insert builds them with keyword arguments.
v7 MongoDB needs none: its pydantic models take values through `model_validate`.
No primary choice, emission recipe or flow mapping changes.
