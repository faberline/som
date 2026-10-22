# TypeScript backend quickstart

This guide walks through verifying the pinned Node.js/npm toolchain and offline npm cache, generating a standalone TypeScript backend project with SOM's local service, and installing, type-checking, format-checking, testing, and running it offline ([CONTRACT.md](../som-rs/contracts/typescript-output-v1/CONTRACT.md)).

## 1. What you get

TypeScript output phase 1 covers prompt versions v1 through v5 (`som.prompt.v1`–`som.prompt.v5`) on PostgreSQL, as defined in [scope.json](../som-rs/contracts/typescript-output-v1/scope.json) (`prompt_versions.in_scope`): single-resource CRUD (v1–v4) and parent/child CRUD with `POST {parent_path}/with-children` atomic batch creation (v5). Prompt versions v6 (`som.prompt.v6`) and v7 (`som.prompt.v7`) are out of scope (`scope.json` `prompt_versions.out_of_scope`) and are refused with `UNSUPPORTED_TARGET` (HTTP `422`). Browse the public CNL and JSON prompt examples (`v1`–`v7` and `v7-branch-null`) in [examples/index.json](../som-rs/contracts/compiler-v1/examples/index.json).

### Fixed package identity and byte-identical manifests

As defined in [product-layout.json](../som-rs/contracts/typescript-output-v1/product-layout.json) (`package`), every generated product is the npm package `som-app`. The prompt's `package` name appears only in `README.md` (`scope.json` `identifiers.prompt_names_not_used_in_typescript`). Because no prompt identifier enters the package manifests or TypeScript configuration, `package.json`, `package-lock.json`, and `tsconfig.json` are byte-for-byte identical across every generated product, matching [reference/package.json](../som-rs/contracts/typescript-output-v1/reference/package.json), [reference/package-lock.json](../som-rs/contracts/typescript-output-v1/reference/package-lock.json), and [reference/tsconfig.json](../som-rs/contracts/typescript-output-v1/reference/tsconfig.json).

### Product files

Every generated v1–v5 product consists of the 11 files defined in [product-layout.json](../som-rs/contracts/typescript-output-v1/product-layout.json) (`files`, `file_count: 11`):

- `package.json` — bytes equal [reference/package.json](../som-rs/contracts/typescript-output-v1/reference/package.json) (`product-layout.json` `files`).
- `package-lock.json` — bytes equal [reference/package-lock.json](../som-rs/contracts/typescript-output-v1/reference/package-lock.json), sealed by `typescript-output-v1` amendment 001 (`product-layout.json` `files`).
- `tsconfig.json` — bytes equal [reference/tsconfig.json](../som-rs/contracts/typescript-output-v1/reference/tsconfig.json) (`product-layout.json` `files`).
- `src/main.ts` — reads `DATABASE_URL` (required) and `SOM_APP_ADDR` (defaults to `127.0.0.1:8000`, `host:port` with port `1`–`65535`), creates a `pg` `Pool`, runs `migrate(pool)` from `src/migrate.ts`, and serves `createApp(pool)` from `src/app.ts` with `@hono/node-server` (`product-layout.json` `files`).
- `src/app.ts` — exports `createApp(pool): Hono` with the resource route handlers, `GET /healthz`, JSON `404` and `405` fallbacks, the v5 `with-children` route, and `422` validation failure mapping (`product-layout.json` `files`).
- `src/schemas.ts` — interfaces for create, update, and read bodies (plus parent, child, and `with-children` batch shapes in v5) and hand-written validators returning either a typed value or `422` items (`product-layout.json` `files`).
- `src/db.ts` — SQL string constants with double-quoted identifiers, bound parameters (`$1`, `$2`, ...), `pg` query functions, the `DATE` (`OID 1082`) text type parser, and `SQLSTATE 23505` / `23503` conflict mapping (`product-layout.json` `files`).
- `src/migrate.ts` — exports `migrate(pool)`, creates `_som_ts_migrations` if absent, takes `pg_advisory_xact_lock` inside one transaction, and applies `migrations/0001_<revision>.sql` verified by SHA-256 hex checksum (`product-layout.json` `files`).
- `migrations/0001_<revision>.sql` — PostgreSQL schema migration where `<revision>` is `migration.revision` verbatim (`product-layout.json` `files`).
- `test/api.test.ts` — product tests using `node:test` and `node:assert/strict` that read `TEST_DATABASE_URL`, drop and recreate `schema public`, run `migrate`, and drive `createApp(pool)` with `app.request()` (`product-layout.json` `files`).
- `README.md` — names the prompt `package`, prompt version, routes, and the `install`, `run`, `test`, `typecheck`, and `format_check` commands from [toolchain.json](../som-rs/contracts/typescript-output-v1/toolchain.json) (`product-layout.json` `files`).

### Direct `.ts` execution (no build step)

As specified in [product-layout.json](../som-rs/contracts/typescript-output-v1/product-layout.json) (`execution`) and [toolchain.json](../som-rs/contracts/typescript-output-v1/toolchain.json) (`node.type_stripping`), Node runs the `.ts` sources directly with its built-in type stripping (`process.features.typescript` is `"strip"` on Node `26.5.0`, with no `--experimental` flag). There is no build step and no `dist/` directory; `tsc -p tsconfig.json` is a type-check gate only (`noEmit: true`, `erasableSyntaxOnly: true`, `verbatimModuleSyntax: true`, `allowImportingTsExtensions: true`).

### Target refusals

Each target refusal in [scope.json](../som-rs/contracts/typescript-output-v1/scope.json) (`refusals`, evaluated in `refusal_order` after `compiler-v1` validation; see also [negative-cases.json](../som-rs/contracts/typescript-output-v1/negative-cases.json)) returns code `UNSUPPORTED_TARGET` with HTTP status `422`:

- `prompt_version` (`UNSUPPORTED_TARGET`, HTTP `422`): applies when the prompt version is `6` (`som.prompt.v6`) or `7` (`som.prompt.v7`), which are out of scope in TypeScript output phase 1 ([scope.json](../som-rs/contracts/typescript-output-v1/scope.json) `refusals.prompt_version`).
- `v5_capabilities` (`UNSUPPORTED_TARGET`, HTTP `422`): applies when a v5 prompt includes a `capabilities` block such as authentication ([scope.json](../som-rs/contracts/typescript-output-v1/scope.json) `refusals.v5_capabilities`).
- `path_empty_segment` (`UNSUPPORTED_TARGET`, HTTP `422`): applies when a resource path has an empty segment, such as a trailing slash (`/items/`) or double slash (`/a//b`) ([scope.json](../som-rs/contracts/typescript-output-v1/scope.json) `refusals.path_empty_segment`).
- `path_reserved` (`UNSUPPORTED_TARGET`, HTTP `422`): applies in prompt versions 1–4 when a resource path is `/healthz`, which is reserved for the health check (`compiler-v1` already refuses a v5 parent or child path whose first segment is `healthz` with `INVALID_VALUE` HTTP `400`; [scope.json](../som-rs/contracts/typescript-output-v1/scope.json) `refusals.path_reserved`).
- `table_reserved` (`UNSUPPORTED_TARGET`, HTTP `422`): applies in prompt versions 1–4 when a resource table name is `_som_ts_migrations`, which is reserved for the migrator (the v5 table pattern already refuses a leading underscore; [scope.json](../som-rs/contracts/typescript-output-v1/scope.json) `refusals.table_reserved`).
- `identifier_reserved` (`UNSUPPORTED_TARGET`, HTTP `422`): applies when a field name or v5 relation foreign key is `constructor` or `prototype` in versions 1–5, or `__proto__` in versions 1–4 ([scope.json](../som-rs/contracts/typescript-output-v1/scope.json) `refusals.identifier_reserved`).

As recorded in [scope.json](../som-rs/contracts/typescript-output-v1/scope.json) (`compiler_v1_refusals.query_parameter_collision`), a v3 filter field named `limit` or `offset` is refused by `compiler-v1` amendment 003 (`INVALID_VALUE` HTTP `400`, [selection-policy.json](../som-rs/contracts/compiler-v1/selection-policy.json) `python_name_collisions`) for every target before target-specific checking.

## 2. Check the toolchain and the npm cache

[toolchain.json](../som-rs/contracts/typescript-output-v1/toolchain.json) pins the exact host runtime, package manager, formatter, dependencies, and offline cache:

- **Node.js (`toolchain.json` `node`):** Homebrew Node `26.5.0` (`v26.5.0`, V8 `14.6.202.34-node.24`) at `/opt/homebrew/Cellar/node/26.5.0/bin/node` (binary SHA-256 `70851490e028b3d699a8d6d4e1de909af2a989359ae807974c92af9c6580a8e8`, `libnode.147.dylib` SHA-256 `0c2dfacde99b9381ec37fbd222891271c7f455cc0524294928e3e34199d57ec3`).
- **npm (`toolchain.json` `npm`):** npm `11.17.0` at `/opt/homebrew/lib/node_modules/npm` (invoked as `/opt/homebrew/Cellar/node/26.5.0/bin/node /opt/homebrew/lib/node_modules/npm/bin/npm-cli.js`, tree SHA-256 `3a9e0a20999d2a5c9727e911c554cd66230dba9a169fe0f0a6a54bd3dc7f9186` across `1957` files and `9` symlinks).
- **Packages and formatter (`toolchain.json` `packages` and `formatter`):** dependencies `@hono/node-server` `2.1.5`, `hono` `4.13.13`, and `pg` `8.23.1`; devDependencies `@types/pg` `8.23.1`, `prettier` `3.9.10` (`som.typescript-formatter.prettier-3.9.10.v1`, SHA-256 `636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8`), and `typescript` `5.9.3` (21 total lockfile packages in [reference/package-lock.json](../som-rs/contracts/typescript-output-v1/reference/package-lock.json)).
- **PostgreSQL and Python (`toolchain.json` `postgresql` and `python`):** PostgreSQL `17.7` at `/opt/homebrew/opt/postgresql@17/bin` and Python `3.12.11`.
- **Vendored npm cache (`toolchain.json` `vendored_cache`):** `.local/typescript-output/npm-cache-001`, read-only with tree SHA-256 `11ae1ed59d4e17888d3b6e3b864cdfdbc850b11752cae2735d13ccb5f71eb1e9` (`86` files, `0` symlinks, computed by [tree_sha.py](../som-rs/tests/typescript-output-v1/tree_sha.py) and checked by [check_contract.py](../som-rs/tests/typescript-output-v1/check_contract.py)).

### Offline npm environment

Before running `npm ci`, export the offline environment required by [toolchain.json](../som-rs/contracts/typescript-output-v1/toolchain.json) (`commands.environment` and `vendored_cache.logs`, which keep debug logs outside the read-only cache so its tree hash never changes):

```sh
export PATH="/opt/homebrew/Cellar/node/26.5.0/bin:/opt/homebrew/opt/postgresql@17/bin:$PATH"
mkdir -p /tmp/som-npm-logs
: > /tmp/som-npm-userconfig

export npm_config_cache="/absolute/path/to/.local/typescript-output/npm-cache-001"
export npm_config_offline=true
export npm_config_audit=false
export npm_config_fund=false
export npm_config_update_notifier=false
export npm_config_logs_dir="/tmp/som-npm-logs"
export npm_config_userconfig="/tmp/som-npm-userconfig"
```

## 3. Generate the project

Run `som write <file.cnl> --target typescript --out <new dir>` to compile a `.cnl` or `.prompt.json` source, assemble the `som.dsl-bundle.v2` TypeScript bundle ([bundle.schema.json](../som-rs/contracts/typescript-output-v1/bundle.schema.json)), and write the 11 product files into a new or empty directory ([api.schema.json](../som-rs/contracts/typescript-output-v1/api.schema.json)):

```sh
som write <file.cnl> --target typescript --out <new dir>
```

`som write --target typescript` needs the #65 default (TypeScript candidate-001, the default on `127.0.0.1:8765` since 2026-10-22) or later. With an older default, point `--url` at a TypeScript candidate on a QA port.

For example, with [v1.cnl](../som-rs/contracts/compiler-v1/examples/v1.cnl):

```sh
som-rs/runtime/current/som write som-rs/contracts/compiler-v1/examples/v1.cnl --target typescript --out ~/work/items_ts
```

On success (`exit 0`), `som write` prints one JSON summary line on `stdout`:

```json
{"files":11,"out":"/Users/you/work/items_ts","status":"ok","target":"typescript"}
```

Exit codes:

- `0` — compiled, assembled, and wrote all files to `--out`.
- `1` — local input, transport, or `--out` path error (`LOCAL_ERROR` JSON printed to `stderr`, no files written).
- `2` — HTTP rejection from `/v1/compile` or `/v1/assemble` (structured diagnostic JSON printed to `stderr`, no files written).

## 4. Install, check, test and run

Every generated product's `README.md` (see [reference-product-001/README.md](../som-rs/evidence/typescript-output-v1/reference-product-001/README.md) and [product-layout.json](../som-rs/contracts/typescript-output-v1/product-layout.json) `files`) lists the prompt package, prompt version, routes, and the five commands from [toolchain.json](../som-rs/contracts/typescript-output-v1/toolchain.json) (`commands`):

```sh
# 1. Install offline from the vendored npm cache (toolchain.json commands.install)
npm ci --offline --ignore-scripts --no-audit --no-fund

# 2. Type-check (toolchain.json commands.typecheck)
node node_modules/typescript/bin/tsc -p tsconfig.json

# 3. Format check (toolchain.json commands.format_check)
node node_modules/prettier/bin/prettier.cjs --check --no-config --no-editorconfig src/main.ts src/app.ts src/schemas.ts src/db.ts src/migrate.ts test/api.test.ts

# 4. Run product tests with TEST_DATABASE_URL set (toolchain.json commands.test)
node --test --test-concurrency=1 test/api.test.ts

# 5. Start the HTTP server with DATABASE_URL set and SOM_APP_ADDR optional (toolchain.json commands.run)
node src/main.ts
```

### Environment variables and migrations

As specified in [product-layout.json](../som-rs/contracts/typescript-output-v1/product-layout.json) (`files`: `src/main.ts`, `src/migrate.ts`, `test/api.test.ts`, and `README.md`) and shown in the generated [README.md](../som-rs/evidence/typescript-output-v1/reference-product-001/README.md):

- `DATABASE_URL` (required by `src/main.ts`): PostgreSQL connection URL. A missing or empty `DATABASE_URL` prints one line to `stderr` (`DATABASE_URL is required`) and exits with status `2` (`product-layout.json` `src/main.ts`).
- `SOM_APP_ADDR` (optional in `src/main.ts`, default `127.0.0.1:8000`): listen address formatted as `host:port` with a port from `1` to `65535`. An invalid `SOM_APP_ADDR` prints one line to `stderr` and exits with status `2` (`product-layout.json` `src/main.ts`).
- `TEST_DATABASE_URL` (required by `test/api.test.ts`): PostgreSQL connection URL read by `node --test --test-concurrency=1 test/api.test.ts`. A missing variable fails the test (never skips); each test drops and recreates `schema public`, runs `migrate(pool)`, and drives `createApp(pool)` via `app.request()` (`product-layout.json` `test/api.test.ts`).
- **How migrations run (`src/migrate.ts`):** the generated `README.md` has no separate migration CLI command because `src/main.ts` calls `migrate(pool)` from `src/migrate.ts` automatically on startup before binding the HTTP server (and `test/api.test.ts` calls `migrate(pool)` in each test). Inside a transaction guarded by `pg_advisory_xact_lock`, `migrate(pool)` creates `_som_ts_migrations` if absent, reads `migrations/0001_<revision>.sql` via `new URL("../migrations/0001_<revision>.sql", import.meta.url)`, and applies it when version `1` is not yet recorded; if version `1` is already recorded with a different revision or SHA-256 hex checksum, startup stops with an error (`product-layout.json` `src/migrate.ts` and `src/main.ts`).

After `install`, `typecheck`, `format_check`, and `test`, `package.json` and `package-lock.json` remain byte-identical to the generated files ([toolchain.json](../som-rs/contracts/typescript-output-v1/toolchain.json) `commands.lock_unchanged`).

## 5. Change it and generate again

Generated TypeScript product files are deterministic for a given prompt and compiler release, and `package.json`, `package-lock.json`, and `tsconfig.json` always match the reference bytes in [reference/](../som-rs/contracts/typescript-output-v1/reference) ([product-layout.json](../som-rs/contracts/typescript-output-v1/product-layout.json) `package` and `files`). When you edit your `.cnl` or `.prompt.json` file and run `som write --target typescript` again, `--out` must be a new or empty directory (`som write` refuses any existing non-empty `--out` directory with exit code `1` so it never overwrites existing files).

If you saved an intermediate `som.dsl-bundle.v2` bundle with `som compile --target typescript`, note that every bundle binds the 11 asset SHA-256 descriptors of the compiler and target contract ([CONTRACT.md](../som-rs/contracts/typescript-output-v1/CONTRACT.md) section 3 and [api.schema.json](../som-rs/contracts/typescript-output-v1/api.schema.json) `x-manifest`). Assembling an old saved bundle after an asset or default change is refused at `/v1/assemble` with `ASSET_HASH_MISMATCH` (HTTP `400`, exit code `2`; [acceptance-plan.json](../som-rs/contracts/typescript-output-v1/acceptance-plan.json) `assemble_refusals` `A1`/`A2`), so re-run `som write --target typescript` (or `som compile --target typescript`) from the `.cnl` or `.prompt.json` source.
