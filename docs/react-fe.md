# React frontend quickstart

Generate a React client and a Python service from the same prompt.
The [React contract](../som-rs/contracts/react-output-v1/CONTRACT.md) defines this output.
Run all checks, tests, and builds below on a GitHub runner.
See the [M6 release plan](m6-release.md) for the hosted switch and rollback rehearsal.
The production switch and release approvals are still pending.

## 1. What you get

React phase 1 covers prompt versions `som.prompt.v1` through `som.prompt.v4`.
It provides pages to list, create, view, edit, and delete one resource.
The client calls the Python service made from the same prompt with no target.
The React product has no database or migration of its own.
See [scope.json](../som-rs/contracts/react-output-v1/scope.json), `prompt_versions`, `semantics`, and `routes`.

SOM uses fixed rules for `CNL → DSL → Code`.
CNL is the fixed English input format.
DSL is the complete plan for the output files.
The current Rust compiler needs no model, model training, or Apple model runtime.
See the [Rust compiler roadmap](../som-rs/RUST_ROADMAP.md), its opening rule and `M1 — CNL to DSL`.
The host limits in section 2 describe the pinned tools, not a need for Apple hardware in this algorithm.

### Package and files

Every product is the npm package `som-app`.
The prompt package name appears only in the `src/schema.ts` header comment.
There are 19 files: 17 fixed files and 2 files made from the prompt.
The fixed files have the same bytes in every product.
See [product-layout.json](../som-rs/contracts/react-output-v1/product-layout.json), `package` and `files`.

| File | Purpose |
|---|---|
| `index.html` | HTML page with the root element. |
| `package.json` | Fixed package name, scripts, and package versions. |
| `package-lock.json` | Fixed package download list and hashes. |
| `tsconfig.json` | TypeScript check settings. |
| `vite.config.ts` | API proxy and test settings. |
| `src/main.tsx` | Starts React with the browser router. |
| `src/App.tsx` | Selects the page for each URL. |
| `src/api.ts` | Sends requests and reads API errors. |
| `src/spec.ts` | Defines the types used by the resource settings. |
| `src/form.ts` | Turns form text into create and edit request bodies. |
| `src/styles.css` | Page styles. |
| `src/pages/ListPage.tsx` | List, filter, and page controls. |
| `src/pages/DetailPage.tsx` | One row, with edit and delete controls. |
| `src/pages/FormPage.tsx` | Create and edit forms. |
| `src/pages/ErrorView.tsx` | Error display. |
| `src/app.test.tsx` | Page tests with a fake API request function. |
| `src/form.test.ts` | Form value tests. |
| `src/schema.ts` | Prompt-based types and `RESOURCE`, `FIELDS`, and `QUERY` values. |
| `README.md` | Prompt-based resource, fields, pages, and commands. |

Vite builds and serves the TypeScript files.
Vitest runs the tests in jsdom, which supplies browser objects for tests.
`tsc -p tsconfig.json` only checks types.
It writes no output files because `noEmit` is set.
See [product-layout.json](../som-rs/contracts/react-output-v1/product-layout.json), `execution`, and [reference/tsconfig.json](../som-rs/contracts/react-output-v1/reference/tsconfig.json).

### Target refusals

The compiler first checks the prompt with the existing `compiler-v1` rules.
It then applies each React refusal in [scope.json](../som-rs/contracts/react-output-v1/scope.json), `refusals` and `refusal_order`.
Each refusal below returns `UNSUPPORTED_TARGET` with HTTP status `422`.

| Refusal | Code | HTTP status | When it applies |
|---|---|---|---|
| `prompt_version` | `UNSUPPORTED_TARGET` | `422` | The prompt is v5, v6, or v7. Use v1–v4 for React. |
| `path_empty_segment` | `UNSUPPORTED_TARGET` | `422` | The resource path has a trailing slash or a double slash, such as `/items/` or `/a//b`. |
| `identifier_reserved` | `UNSUPPORTED_TARGET` | `422` | A field name is `__proto__`, `constructor`, or `prototype`. |

The refusal order is prompt version, resource path, then fields in array order.
Existing compiler rules can refuse a request first.
For example, a v3 filter field named `limit` or `offset` returns `INVALID_VALUE`, HTTP `400`, for every target.
The existing v4 rule also reserves `before`, `limit`, and `offset` as filter or date field names.
See [scope.json](../som-rs/contracts/react-output-v1/scope.json), `compiler_v1_refusals`.

## 2. Check the toolchain and the npm cache

The exact pins are in [toolchain.json](../som-rs/contracts/react-output-v1/toolchain.json).
Do not replace them with newer package versions.

| Tool or asset | Exact pin |
|---|---|
| Node.js | `26.5.0`; version output `v26.5.0`; binary `/opt/homebrew/Cellar/node/26.5.0/bin/node`. |
| npm | `11.17.0`; root `/opt/homebrew/lib/node_modules/npm`. |
| npm invocation | `/opt/homebrew/Cellar/node/26.5.0/bin/node /opt/homebrew/lib/node_modules/npm/bin/npm-cli.js`. |
| React and React DOM | `19.3.1`. |
| React Router | `8.5.0`. |
| TypeScript | `5.9.3`. |
| Vite | `8.3.5`. |
| Vitest | `5.0.5`. |
| Prettier | `3.9.10`. |
| npm cache | `.local/react-output/npm-cache-001` under the SOM repository. |
| Cache tree SHA-256 | `8560dba81d69e7599ec4a050946a1b7c2240b5ac7cd8155647750b4df59796b6`. |
| Cache tree counts | `508` files; `0` symbolic links. |

SHA-256 is a hash used to check exact bytes.
The contract also pins Node's libraries and the npm tree.
Its npm tree hash is `3a9e0a20999d2a5c9727e911c554cd66230dba9a169fe0f0a6a54bd3dc7f9186`, with `1957` files and `9` symbolic links.
See [toolchain.json](../som-rs/contracts/react-output-v1/toolchain.json), `node`, `npm`, `packages`, `formatter`, and `vendored_cache`.

The frozen toolchain records an `aarch64-apple-darwin` host and Homebrew paths.
Its npm cache contains the platform packages fetched for that host only.
These are limits of the historical implementation.
Portable Rust provisioning is pending [M12](https://github.com/faberline/som/milestone/18).
This guide does not claim that an unprepared GitHub runner can use those paths or that this cache works on Linux.
Use a GitHub runner with the exact pinned tools and cache already provisioned.
See [toolchain.json](../som-rs/contracts/react-output-v1/toolchain.json), `host` and `packages.platform_packages`.

In a GitHub runner shell, set the contract environment before `npm ci`.
`GITHUB_WORKSPACE` must be the SOM checkout.
Keep the logs outside the read-only cache.
Keep these settings in the same runner step as the commands in section 4.
See [toolchain.json](../som-rs/contracts/react-output-v1/toolchain.json), `commands.environment` and `vendored_cache.logs`.

```sh
export PATH="/opt/homebrew/Cellar/node/26.5.0/bin:/usr/bin:/bin"
export SOM_REPO="$GITHUB_WORKSPACE"
mkdir -p /tmp/som-react-npm-logs
: > /tmp/som-react-npm-userconfig

export npm_config_cache="$SOM_REPO/.local/react-output/npm-cache-001"
export npm_config_offline=true
export npm_config_audit=false
export npm_config_fund=false
export npm_config_update_notifier=false
export npm_config_logs_dir="/tmp/som-react-npm-logs"
export npm_config_userconfig="/tmp/som-react-npm-userconfig"

npm() {
  /opt/homebrew/Cellar/node/26.5.0/bin/node /opt/homebrew/lib/node_modules/npm/bin/npm-cli.js "$@"
}

node --version
npm --version
python3 "$SOM_REPO/som-rs/tests/typescript-output-v1/tree_sha.py" "$npm_config_cache"
```

The last command must report the cache hash and counts above.
The tree hash method is defined in [tree_sha.py](../som-rs/tests/typescript-output-v1/tree_sha.py).
The cache is an input prepared outside the generated product.
Generation does not include it in the 19 files.

## 3. Generate Python and React

Use one `.cnl` or `.prompt.json` source for both products.
Omit `--target` to generate Python.
Use `--target react` to generate its React client.
Use a new or empty directory for each product.
See [product-layout.json](../som-rs/contracts/react-output-v1/product-layout.json), `service`, and the [write CLI source](../som-rs/som-targets/src/main.rs).

```sh
som write <file.cnl> --out <new-python-dir>
som write <file.cnl> --target react --out <new-react-dir>
```

React generation needs the v0.3.0 default or a later default that supports React.
Until v0.3.0 becomes the default, use `--url` with a prepared React QA candidate.
The M6 release version is listed in the [scenario roadmap](roadmap.md), `Milestones and releases`.
Set `SOM_QA_URL` to that candidate's URL before these commands.
Use the same candidate for both products.

```sh
som write som-rs/contracts/compiler-v1/examples/v1.cnl --url "$SOM_QA_URL" --out "$RUNNER_TEMP/items_python"
som write som-rs/contracts/compiler-v1/examples/v1.cnl --target react --url "$SOM_QA_URL" --out "$RUNNER_TEMP/items_react"
```

The input above is the public [v1.cnl](../som-rs/contracts/compiler-v1/examples/v1.cnl) example.
On success, the React command prints one JSON line to `stdout`:

```json
{"files":19,"out":"/path/to/items_react","status":"ok","target":"react"}
```

The [write CLI source](../som-rs/som-targets/src/main.rs) defines these exit codes:

- `0`: The command compiled, assembled, and wrote all files to `--out`.
- `1`: Input, transport, or output path error. The command prints `LOCAL_ERROR` JSON to `stderr` and writes no files.
- `2`: HTTP rejection from `/v1/compile` or `/v1/assemble`. The command prints diagnostic JSON to `stderr` and writes no files.

## 4. Install, check, test, and build

Run these as GitHub runner steps in the generated React directory.
Use the environment from section 2.
The commands below preserve [toolchain.json](../som-rs/contracts/react-output-v1/toolchain.json), `commands.install`, `typecheck`, `format_check`, `test`, and `build`.
Replace `<dist dir outside the product>` with a new directory outside the product.

```sh
cd "$RUNNER_TEMP/items_react"

# Install from the pinned offline cache.
npm ci --offline --ignore-scripts --no-audit --no-fund

# Check types.
node node_modules/typescript/bin/tsc -p tsconfig.json

# Check formatting.
node node_modules/prettier/bin/prettier.cjs --check --no-config --no-editorconfig .

# Run product tests.
node node_modules/vitest/vitest.mjs run

# Build static files outside the product.
node node_modules/vite/bin/vite.js build --outDir <dist dir outside the product> --emptyOutDir
```

Run the build twice into two new directories on the GitHub runner.
The two output trees must have the same bytes.
The commands must leave `package.json`, `package-lock.json`, the cache, and every product file outside `node_modules` unchanged.
They must not add or remove product files outside `node_modules`.
See [toolchain.json](../som-rs/contracts/react-output-v1/toolchain.json), `commands.build_reproducible`, `lock_unchanged`, `product_unchanged`, and `vendored_cache`.

### Serve the client and connect the Python service

`SOM_API_URL` defaults to `http://127.0.0.1:8000`.
Set it to the address of the generated Python service when that address differs.
The Vite development server and preview server forward `/api/*` to this address.
They remove the `/api` prefix before forwarding the request.
See [product-layout.json](../som-rs/contracts/react-output-v1/product-layout.json), `service.proxy`, and [reference/vite.config.ts](../som-rs/contracts/react-output-v1/reference/vite.config.ts).

For a GitHub runner service check, start the Python product from the same prompt first.
Use the [Python backend guide](python-be.md) for its required service settings.
Then run the React development server in the prepared runner:

```sh
SOM_API_URL=http://127.0.0.1:8000 npm run dev
```

To preview a build made outside the product, point Vite at that build directory:

```sh
SOM_API_URL=http://127.0.0.1:8000 node node_modules/vite/bin/vite.js preview --outDir <dist dir outside the product>
```

The dev and preview commands come from [reference/package.json](../som-rs/contracts/react-output-v1/reference/package.json) and [reference/vite.config.ts](../som-rs/contracts/react-output-v1/reference/vite.config.ts).
The build output is static files.
A production server and its `/api` reverse proxy are outside phase 1.
See [product-layout.json](../som-rs/contracts/react-output-v1/product-layout.json), `service.proxy`.

## 5. Change it and generate again

For a fixed prompt and compiler release, generation gives the same bytes.
The 17 fixed files always match their reference files in [product-layout.json](../som-rs/contracts/react-output-v1/product-layout.json), `files`.
The other 2 files follow the prompt rules in [product-layout.json](../som-rs/contracts/react-output-v1/product-layout.json), `renderer`.

Edit the `.cnl` or `.prompt.json` source.
Generate both products again into new or empty directories.
`som write` refuses a non-empty `--out` directory with exit code `1`.
See the [write CLI source](../som-rs/som-targets/src/main.rs), `write_files`.

A saved `som.dsl-bundle.v2` React bundle binds 11 asset hashes.
The active compiler must have the same asset descriptors when it assembles the bundle.
See [api.schema.json](../som-rs/contracts/react-output-v1/api.schema.json), `x-manifest` and `x-assemble-check-order`.

After an asset or default change, an old bundle can fail with `ASSET_HASH_MISMATCH`.
This is HTTP `400` from `/v1/assemble` and CLI exit code `2`.
The refusal is defined in [acceptance-plan.json](../som-rs/contracts/react-output-v1/acceptance-plan.json), `assemble_refusals.recipes` A1 and A2.
Run `som write --target react` again from the source with a new output directory.
If you need a saved bundle, run `som compile <file.cnl> --target react` again from the CNL source.
