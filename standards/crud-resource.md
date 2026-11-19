# crud-resource

## Name

CRUD. The CNL word is `CRUD`.

## Decisions

| # | Decision | Proof test |
|---|---|---|
| 1 | Paths: `/v1/orders`, `/v1/orders/{id}`. Plural, lower case, kebab-case. | `crud_paths_are_plural_kebab_lower` |
| 2 | Create: `POST /v1/orders` gives `201`, a `Location` header and the full resource. | `create_returns_201_location_and_full_resource` |
| 3 | Read: `GET /v1/orders/{id}` gives `200`; not found gives `404`. | `read_returns_200_and_unknown_id_returns_404` |
| 4 | List: `GET /v1/orders` gives `200` and uses `standard-pagination`. | `list_returns_200_with_standard_pagination` |
| 5 | Update: `PUT` replaces the full resource and gives `200`. No `PATCH` in v1. | `replace_returns_200_with_full_resource_and_no_patch_route` |
| 6 | Delete: hard delete, `204`. A second delete gives `404`. No soft delete in v1. | `delete_returns_204_and_second_delete_returns_404` |
| 7 | Id: the server makes it, a UUIDv7 string. The client cannot set it. | `id_is_server_made_uuidv7_and_client_id_is_refused` |
| 8 | Time fields: `created_at` and `updated_at` are added, RFC 3339, UTC, set by the server. | `time_fields_are_rfc3339_utc_and_set_by_server` |
| 9 | Validation: unknown fields are refused. A type or constraint error gives `422` with one entry for each bad field. | `unknown_field_is_refused_and_bad_fields_give_422_with_one_entry_each` |
| 10 | Request size: body limit 1 MiB; larger gives `413`. | `body_over_1_mib_returns_413` |
| 11 | Concurrent change: the resource has an `ETag`. `PUT` and `DELETE` need `If-Match`; a mismatch gives `412`; a missing header gives `428`. | `etag_is_returned_and_if_match_mismatch_412_missing_428` |
| 12 | Repeated create: `POST` accepts `Idempotency-Key`. Same key and same body give the same result; same key and a different body give `409`. Keys are kept for 24 hours. | `idempotency_key_same_body_same_result_different_body_409_and_expires_after_24h` |

## Facts that CNL must give

- The name of the resource.
- Each field with its type and constraints.

The standard does not guess a fact.

## Tests

Every row of the Decisions table names its proof test. The crate has one success test for each operation (create, read, list, replace, delete) and one test for each error code that the Decisions table names for that operation.

## Usual changes

| Change | Decisions touched |
|---|---|
| Add a field with a constraint | 7, 8, 9 |
| Change hard delete to soft delete | 6 |
| Add an operation that is not CRUD, for example `POST /v1/orders/{id}/cancel` | 1, 11 |
| Replace the in-memory store | none; the store trait is decision 26 |

## Version

som.std.v1
