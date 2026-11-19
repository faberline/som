# standard-errors

## Name

No CNL word. `crud-resource` and `standard-pagination` bring it.

## Decisions

| # | Decision | Proof test |
|---|---|---|
| 21 | Body: `{"error": "<code>", "message": "<text for a person>", "details": [...]}`. `details` is optional. This keeps the two fields of core `service-auth`. | `error_body_has_error_message_and_optional_details` |
| 22 | Codes: `invalid_request` (400), `unauthenticated` (401), `forbidden` (403), `not_found` (404), `conflict` (409), `precondition_failed` (412), `payload_too_large` (413), `validation_failed` (422), `precondition_required` (428), `rate_limited` (429), `internal` (500), `unavailable` (503). | `each_error_code_maps_to_its_status` |
| 23 | A `500` does not show internal error text. It gives the code and the request id. The detail goes to the log. | `internal_error_hides_text_and_shows_request_id` |
| 24 | `429` and `503` have `Retry-After`. | `rate_limited_and_unavailable_have_retry_after` |

## Facts that CNL must give

None.

The standard does not guess a fact.

## Tests

Every row of the Decisions table names its proof test. The crate has one test for each error code in decision 22 that checks its status, and one test for each error body shape.

## Usual changes

| Change | Decisions touched |
|---|---|
| Add an error code | 22 |
| Add a `details` entry to an error | 21 |

## Version

som.std.v1
