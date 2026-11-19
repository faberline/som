# som standard catalog

The catalog holds the decisions that every som output needs and that a CNL request does not say. Each decision is made once here, with its number and the test that proves it.

## Version

som.std.v1

## Format

Each standard is one file with these parts, in this order:

- Name: the standard name and its CNL word.
- Decisions: one table with the number, the decision and its proof test.
- Facts that CNL must give: the facts a request must state.
- Tests: the tests that each decision needs.
- Usual changes: what an agent most often changes after generation, and the decisions each change touches.
- Version: the catalog version.

`service-baseline.md` has one more part, Core mapping, between Usual changes and Version.

## Standards

| Standard | CNL word | Decisions | File |
|---|---|---|---|
| crud-resource | CRUD | 1 to 12 | [crud-resource.md](crud-resource.md) |
| standard-pagination | standard pagination | 13 to 20 | [standard-pagination.md](standard-pagination.md) |
| standard-errors | none; `crud-resource` and `standard-pagination` bring it | 21 to 24 | [standard-errors.md](standard-errors.md) |
| service-baseline | none; every generated API gets it | 25 to 37 | [service-baseline.md](service-baseline.md) |

## How to change

- A decision change is a new catalog version (`som.std.v2`), decided by the owner.
- A pull request never changes a decision of `som.std.v1`.
- Each standard that is not in v1 is listed below and needs its own standard with its own CNL word.

## Not in v1

- `PATCH`
- soft delete
- sort
- filter
- total count
- a real database
- batch operations
- relations between resources
- events
- multi-tenant
