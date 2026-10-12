# Query and pagination task family

`som.prompt.v2` adds a `query` object to the CRUD prompt. It keeps the same
five files and the `som.dsl.v1` output. The model selects the ordered API
blocks. Rust validates the typed query values and assembles source from
approved blocks. The flagship never names a block ID.

The new prompt fields are:

```json
"query": {"filter_field": "title", "max_limit": 100}
```

`filter_field` must name a declared, non-unique string field. `max_limit` is
an integer from 20 through 100. The query list step and test sentence have
exact wording in [the schema](schemas/som.prompt.v2.schema.json). Other task
families still return a clear rejection.

The generated `GET /resource` route accepts an optional `filter_value` for
exact equality. It accepts `limit` from 1 through `max_limit`, with a default
of 20. It accepts a nonnegative `offset`, with a default of zero. It orders
rows by ID. SQL applies the filter, order, limit, and offset before rows reach
Python. The existing create, get, patch, delete, unittest, and Alembic files
remain part of the generated package. The added unittest checks filtering,
page bounds, and invalid query values.

Use [query-prompt.json](examples/query-prompt.json) as one valid request.
The model in `models/crud-query-v2` was trained from scratch on both the old
CRUD family and this query family. The frozen data holds 280 training and 50
held-out tasks for each family. The previous model and its exact 14-block
library remain available in `models/crud-v1` and
`../som-code-python/data/blocks/crud-v1/blocks.json` for historical checks.

The query block improves the first CRUD list block by doing filter and page
work in SQL. It has no filter-column index and uses offset pagination. It is
not certified as the fastest choice for large tables or large offsets. See
[scope growth](SCOPE_GROWTH.md) for the performance qualification rule.

The current model matched all 100 frozen old and new tasks byte for byte.
The 50 new tasks passed real PostgreSQL behavior and migration checks.
All 250 generated Python files passed Ruff E/F lint and formatter checks.
See the [exact-output receipt](evals/crud-query-v2-exact-2026-09-29.json),
[PostgreSQL receipt](evals/query-v2-2026-09-29.json), and
[resource receipt](evals/service-budget-query-v2-2026-09-29.json).
