# SOM React output contract v1 — scope proposal for issue #68

> Historical scope proposal. The [React contract](../som-rs/contracts/react-output-v1/CONTRACT.md)
> and its recorded amendments describe the later implementation.
> The 2026-10-28 direction removes any Apple chip requirement.
> The original host-specific package choices below do not limit the algorithm.
> Read [the current architecture](architecture.md). Run development builds and tests on GitHub runners.

Original proposal status: **proposal, not approved.** The text below preserves the choices considered before the contract freeze.
Nothing in this proposal changes code, contracts, or outputs.

The milestone is [M6](https://github.com/faberline/som/milestone/12), release v0.3.0 ([roadmap](roadmap.md)).
The structure follows the [TypeScript draft](typescript-output-v1-draft.md).

## 1. What the React target is

A React product is a browser client for a generated backend.

- It is generated from the **same prompt** as the backend, so the client and the server agree on paths, fields, filters and errors without any new input.
- The client talks to the backend's HTTP API only. That API is the same for the Python, Rust and TypeScript products, up to the divergences their contracts list, so one React product works with any of them.
- The parity reference is the Python product.

## 2. What happens to Python, Rust and TypeScript

- A request without `target`, or with target `python`, returns the same bytes as the current default.
- The 127 public products and the 544 legacy jobs stay byte-identical for every existing target.
- **Target selection.** The `target` enum gains a value, `react`. The frozen texts that name the allowed targets have to change:
  - Rust RT01–RT06, in **rust-output-v1 amendment 006**;
  - TypeScript TT01–TT06, in **typescript-output-v1 amendment 002**.
  
  Each amendment is sealed with its own independent review and receipt, as amendment 005 was.
- The Rust and TypeScript bundle descriptors change, because the shared `som-targets` sources change. Their product files do not.
- Generated Python products have no CORS middleware, and Python output must not change. The React product therefore never calls the backend cross-origin (section 5).

## 3. Phase 1 scope

Phase 1 covers prompt versions 1 to 4: one resource with str, int, float, bool and date fields.

| Version | API the client uses | Pages |
|---|---|---|
| v1 | list in id order, create, get, patch, delete | list, detail, create, edit, delete |
| v2 | as v1, plus `filter_value` (exact string) and `limit`/`offset` | list adds a text filter and a pager |
| v3 | as v1, plus `<filter_field>=true\|false` and `limit`/`offset` | list adds a three-state boolean filter (any, true, false) and a pager |
| v4 | as v3, plus `before=<date>` on the date field | list adds a date-before filter |

Behaviour of every page:

- **Edit** sends PATCH with only the fields the user changed.
- **Delete** asks for confirmation first.
- **Errors**:
  - 404 and 409 show the backend's `detail` text;
  - 422 shows each field error next to its input;
  - any other status shows one generic message with the status code.
- **Defaults and optional fields** follow the prompt:
  - an optional field with default null can be cleared;
  - a bool field is a checkbox;
  - a date field is a date input.
- **Timestamps** (`created_at`, `updated_at`) are displayed and never edited.

Refused with UNSUPPORTED_TARGET (422), with the same pointer rules as Rust:

| Rule | Refused prompt | Reason |
|---|---|---|
| prompt_version | v5, v6, v7 | Parent/child pages, capabilities and flows come in a later phase |

No new CNL clause and no new DSL section are needed: everything comes from the existing prompt.

## 4. Product layout (sketch)

About 15 files, fixed for each version:

```
package.json
package-lock.json
tsconfig.json
vite.config.ts
index.html
README.md
src/main.tsx
src/api.ts             typed client: one function per route
src/types.ts           row, create and patch types from the prompt fields
src/pages/ListPage.tsx
src/pages/DetailPage.tsx
src/pages/FormPage.tsx create and edit
src/styles.css
src/api.test.ts
src/pages.test.tsx
```

The README covers install, test, dev server, build, and how to point the client at a backend.

## 5. Serving and CORS

- The client calls relative paths under `/api`, for example `/api/items/3`.
- The `vite dev` and `vite preview` servers proxy `/api` to `SOM_API_URL`, which defaults to `http://127.0.0.1:8000`.
- A production build is static files in `dist/`. The README shows one reverse-proxy example that serves `dist/` and forwards `/api`.
- No backend output changes.

## 6. Decisions needed, with the recommendation

| Decision | Recommended | Alternatives | Why |
|---|---|---|---|
| Language | **TypeScript** | JavaScript | Reuses the pinned Node 26.5.0, tsc 5.9.3 and prettier from typescript-output-v1. Typecheck becomes a product gate. |
| Build tool | **Vite** | esbuild alone; no bundler (ES modules) | The common default for React, with a dev server, a proxy and `vite build`. Its native helpers (esbuild, rollup) are platform packages, vendored for darwin-arm64 only, like the TypeScript cache. |
| Router | **React Router, declarative mode** | a hand-written path matcher (about 40 lines) | People who extend the product expect it. The hand-written option saves one dependency. |
| Data fetching | **Generated `fetch` client plus small hooks** | TanStack Query | Five routes need no cache library. Everything stays readable and testable. |
| Styling | **One plain CSS file over semantic HTML** | CSS modules; Tailwind; a component library | No build plugin, nothing to download. Easy to restyle. |
| Unit tests | **Vitest + jsdom + Testing Library** | `node:test` + jsdom | Vitest shares Vite's config, and Testing Library is the usual way to test React pages. |
| Browser end-to-end | **Not in phase 1** | Playwright | Playwright downloads browsers. The integration gate below uses the client against a live backend instead. |

Dependency pins are exact versions chosen from the registry at freeze time, and are recorded in the contract and the lockfile.

## 7. Acceptance sketch

Every v1–v4 acceptance case, with no sampling, runs these gates:

1. `npm ci --offline --ignore-scripts`, from the vendored cache.
2. typecheck (`tsc --noEmit`).
3. format_check (prettier).
4. The product's own tests (Vitest, jsdom). The backend is faked inside the tests.
5. `vite build`. The output must be byte-identical across two builds.
6. lock_unchanged: no command changes the generated files.
7. **API contract**, run with Node and no browser:
   - the product's `src/api.ts` drives a live Python product for the same prompt, on a QA port;
   - it runs each page's request sequence: list with every filter, the pager, create, get, patch, delete, 404, 409 and 422;
   - the expected results come from the existing behavior suite rules.
8. Python, Rust and TypeScript outputs stay byte-identical, except for the Rust and TypeScript bundle descriptors listed in section 2.

## 8. What needs your approval

1. The scope in section 3: v1–v4 only, with v5–v7 refused.
2. The choices in section 6.
3. One **npm download** of the pinned React toolchain into a new vendored cache, `.local/react-output/npm-cache-001`. After that, everything is offline, as it is for TypeScript.
4. Adding `react` to the `target` enum, through rust-output-v1 amendment 006 and typescript-output-v1 amendment 002.

## 9. After approval

1. Write and freeze `som-rs/contracts/react-output-v1/`, with an independent review and a sealed receipt.
2. Split the work into `agy:todo` issues:
   - target selection and profile;
   - bundle, catalog and renderer;
   - host, product gates and the API contract check;
   - a quickstart page.
3. Release v0.3.0 through the same gate as v0.2.0 (#81): candidate, acceptance, rehearsal, your approval to switch 8765, then the tag with your yes.
