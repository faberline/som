# SOM TypeScript output contract v1 — draft for issue #65

Status: **draft, not frozen.** A person picks the options in section 6 and approves the contract.
Only then is it copied to `som-rs/contracts/typescript-output-v1/`, sealed with a receipt, and split into `agy:todo` issues.
Nothing in this draft changes code, contracts, or outputs.

The structure follows the [Rust output contract](../som-rs/contracts/rust-output-v1/CONTRACT.md).
Section numbers match it where they can.

## 1. Python and Rust stay byte-identical

- A request without `target`, or with target `python`, returns the same bytes as today.
  This covers the 127 public products and the 544 legacy jobs.
- A request with target `rust` returns the same bytes as today.
- The Python backend hash, the asset hash, and every Rust contract hash stay unchanged.
  The only exception is the target diagnostic text in section 2.
- The acceptance plan replays the existing Python and Rust gates against the TypeScript candidate.

## 2. Target selection

- The envelope's optional `target` member admits a third value, `typescript`.
- Every other value still fails the envelope schema.
- No CNL sentence selects a target, as in Rust.

The frozen Rust negative cases RT01–RT07 state the cause "Target must be the string python or rust." and the allowed form "Omit target, or use target python or rust."
Adding `typescript` therefore needs **rust-output-v1 amendment 005**, sealed with its own independent review and receipt. The amendment:

- adds `typescript` to the `target` enum in `api.schema.json`;
- rewrites the RT01–RT07 cause and allowed-form texts to name all three targets;
- changes nothing else.

The Python and Rust products are unaffected. Only those seven error responses change.

## 3. The TypeScript bundle

- Bundle version `som.dsl-bundle.v2`, with `target: "typescript"`.
- DSL `som.dsl.typescript.v1`.
- Asset descriptors are the same as in Rust: backend, block library, selection policy, and `target_contract` (this contract's hash).
- The assemble check order is the Rust order, with a TypeScript profile and a TypeScript catalog in place of the Rust ones.

## 4. Phase 1 scope and refusals

Phase 1 is the Rust phase 1 scope: prompt versions 1 to 5 with PostgreSQL.
The prompt meaning never depends on the target.
Each refusal is UNSUPPORTED_TARGET, HTTP 422, with the same pointer rules as Rust.

| Rule | Refused prompt | Reason |
|---|---|---|
| prompt_version | Versions 6 and 7 | Phase 2 |
| v5_capabilities | A v5 prompt with a capabilities block | Phase 2 |
| path_empty_segment | A resource path with an empty segment | Same as Rust phase 1 |
| path_reserved | The top-level resource path `/healthz` | The product's own health route |
| table_reserved | The table used by the TypeScript migrator (proposed `_som_ts_migrations`) | Bookkeeping table |
| identifier_reserved | A field or foreign key named `__proto__`, `constructor`, or `prototype` | Object-key hazards in JavaScript |
| query_parameter_collision | A v3 filter field named `limit` or `offset` | Same as Rust |

Open question for review: the alternative to `identifier_reserved` is to generate code that never uses wire names as plain object keys (a `Map`, or `Object.create(null)` with quoted keys).
That removes the refusal and costs some readability in the generated code.
The draft proposes the refusal for phase 1.

Check order: the Rust order, with the TypeScript profile in step 5.
Every compiler-v1 diagnostic keeps its precedence over a TypeScript refusal.

## 5. Product layout (proposal)

For v1–v5 with PostgreSQL:

```
package.json
package-lock.json          # or pnpm-lock.yaml, see 6.6
tsconfig.json
README.md
migrations/0001_init.sql
src/main.ts                # serve, migrate upgrade|downgrade
src/settings.ts            # DATABASE_URL, SOM_APP_ADDR
src/db.ts                  # pool, migrator
src/schemas.ts             # request and response types, validation
src/routes.ts              # one handler per route
test/api.test.ts           # generated tests, same coverage as the Rust tests
```

- The exact file set, file count limit, and block order are frozen in `product-layout.json` and `bundle.schema.json`, as in Rust.
- `package.json` pins exact versions with no ranges.
- The lockfile is a frozen reference file, as `reference/Cargo.lock` is for Rust.

## 6. Options to choose

Versions are the registry's current releases on 2026-10-21.
"Closure" counts the transitive npm packages from registry metadata. Optional platform packages are not counted.

### 6.1 Runtime

| Option | State on this machine | Notes |
|---|---|---|
| **A. Node.js 26.5.0 (Homebrew)** — recommended | Installed at `/opt/homebrew/bin/node` | Current line. Expected to become LTS in late October 2026. Built-in type stripping. Pinned by binary SHA-256, like rustc. A Homebrew upgrade can remove it, the same risk Rust has with rustfmt. |
| B. Node.js 22.18.0 (nvm) | Installed under `~/.nvm` | Maintenance LTS until April 2027. Outside Homebrew, so upgrades do not remove it. Older. |
| C. Node.js 24 LTS | Not installed | Needs a download. Active LTS. |
| D. Bun or Deno | Not installed | Need a download. Fewer users run generated backends on them. Not recommended for phase 1. |

### 6.2 HTTP framework

| Option | Version | Closure | Notes |
|---|---|---|---|
| **A. Hono + @hono/node-server** — recommended | 4.13.13 + 2.1.5 | 0 + 0 | Smallest supply chain. Explicit routing. The generated code writes its own validation and error bodies, as the Rust products do with axum. |
| B. Fastify | 5.12.7 | 48 | Built-in JSON Schema validation (ajv). Its error bodies would need mapping to the Python 422 shape anyway. |
| C. Express | 5.3.0 | 67 | Best known. Largest closure, and no typed routing. |
| D. NestJS | 12.2.0 | 6 for core, plus the Express platform | Decorators and dependency injection. Too much framework for generated CRUD. Not recommended. |

### 6.3 Database layer

| Option | Version | Closure | Notes |
|---|---|---|---|
| **A. `pg` (node-postgres) with hand-written parameterized SQL** — recommended | 8.23.1, plus @types/pg 8.23.1 | 13, plus 10 for types | Closest to the Rust products' sqlx runtime API. No code generation. Hand-written migrator, as Rust did after amendment 003. |
| B. postgres.js | 3.4.9 | 0 | Zero dependencies. Tagged-template queries. Less common in production code. |
| C. Kysely on `pg` | 0.29.7 | 0, plus `pg` | Typed query builder. Adds a layer the renderer must emit. Version 0.x. |
| D. Drizzle ORM | 0.45.4 | 0, plus a driver; migrations need drizzle-kit | Schema-first ORM. Version 0.x. Its migration tool is a second toolchain. |
| E. Prisma | 8.0.0-rc.23 | 12, plus a query engine | Code generation and an engine binary. Hard to vendor and run offline. Not recommended. |

### 6.4 Request validation

| Option | Notes |
|---|---|
| **A. Hand-written validators** — recommended | Exact control over the Python 422 bodies the parity suite compares. Zero dependencies. The Rust products do the same. |
| B. zod 4.6.7 | Zero dependencies. Its error format still needs mapping to the Python shape. |
| C. TypeBox 0.34.52 | JSON Schema based. Fits Fastify (6.2 B). |

### 6.5 Test runner

| Option | Notes |
|---|---|
| **A. `node:test` with `node:assert`** — recommended | Built into Node. Zero dependencies. |
| B. vitest 5.0.5 | Closure 22, plus native optional packages. Nicer output. |

### 6.6 Package manager and lockfile

| Option | Notes |
|---|---|
| **A. npm 11.17.0 with `package-lock.json`** — recommended | Ships with Node. `npm ci --offline` from a vendored cache, as `vendor_crates.py` does for Rust. |
| B. pnpm 11.11.0 with `pnpm-lock.yaml` | Installed. Faster, stricter. One more tool to pin. |

### 6.7 Type check and formatting

| Option | Notes |
|---|---|
| **A. typescript 5.9.3 (`tsc --noEmit --strict`) and prettier 3.9.10 (`--check`)** — recommended | Pure JavaScript packages with no dependencies. Easy to vendor and pin by hash. The renderer emits formatted code. The gate checks it, as rustfmt and clippy do for Rust. |
| B. typescript 7.0.2 (native) | Already installed globally under nvm 22.18.0. Fast. Ships as platform binary packages, which are harder to pin. |
| C. No `tsc`, Node type stripping only | No type check at all. Not recommended. |

The product runs on compiled JavaScript or with type stripping. This follows from 6.1 A and is decided at freeze time.

### 6.8 Downloads

Every option except the runtime needs packages from the npm registry.
Your rules approve only crates.io downloads, so **npm downloads need your approval**.
The proposal is a single download into a local vendored cache, pinned by lockfile integrity hashes, after the contract freezes.
Building and testing then runs offline.

## 7. Behavior parity

- Reuse `som-rs/behavior/suite.py` and `behavior-normalization.json` unchanged.
- Add a TypeScript host: `node` runs the product with `DATABASE_URL` and `SOM_APP_ADDR` on a QA port, never 8765 or 18765.
- Each prompt's TypeScript product must match the Python product under the same normalization (D1–D6) and the same schema-equivalence query.
- The migrator's bookkeeping table is excluded from the comparison.

## 8. Cases

- `target-examples.json`: the Rust examples with target `typescript`.
- `negative-cases.json`: TS01 onward, one per refusal in section 4, plus the RT cases from amendment 005.
- `case-inventory.json`: every phase 1 prompt the Rust inventory admits, minus the TypeScript refusals.
- `acceptance-plan.json`:
  - Python byte-identical gates;
  - Rust byte-identical gates;
  - TypeScript product gates: `npm ci --offline`, `tsc --noEmit`, prettier `--check`, `node --test`, and behavior parity.

## 9. Freeze and amendments

1. A person picks the options in section 6 and approves this draft.
2. rust-output-v1 amendment 005 (section 2) is written and independently reviewed. It is sealed first.
3. This contract is copied to `som-rs/contracts/typescript-output-v1/` with its JSON files, independently reviewed, and sealed with a receipt in `som-rs/evidence/`.
4. Phase 1 is split into `agy:todo` issues: target selection, profile, renderer, host, and acceptance.
5. Later changes need numbered amendments, as in Rust.
