# items

Generated from a SOM prompt, package `items`, prompt version 1.

## Routes

- `GET /healthz`
- `GET /items`
- `POST /items`
- `GET /items/{item_id}`
- `PATCH /items/{item_id}`
- `DELETE /items/{item_id}`

## Commands

Install, offline from the vendored npm cache:

```
npm ci --offline --ignore-scripts --no-audit --no-fund
```

Run, with `DATABASE_URL` set and `SOM_APP_ADDR` optional (default `127.0.0.1:8000`):

```
node src/main.ts
```

Test, with `TEST_DATABASE_URL` set:

```
node --test --test-concurrency=1 test/api.test.ts
```

Type-check:

```
node node_modules/typescript/bin/tsc -p tsconfig.json
```

Format check:

```
node node_modules/prettier/bin/prettier.cjs --check --no-config --no-editorconfig src/main.ts src/app.ts src/schemas.ts src/db.ts src/migrate.ts test/api.test.ts
```
