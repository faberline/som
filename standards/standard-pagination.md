# standard-pagination

## Name

standard pagination. The CNL word is `standard pagination`.

## Decisions

| # | Decision | Proof test |
|---|---|---|
| 13 | Cursor pagination. No offset. | `list_has_no_offset_parameter` |
| 14 | Parameters: `limit` and `cursor` only. | `list_parameters_are_limit_and_cursor_only` |
| 15 | Default 50 items, maximum 200. A larger `limit` gives `400`; it is not reduced silently. | `default_limit_50_max_200_and_larger_limit_returns_400` |
| 16 | Response: `{"items": [...], "next_cursor": "<string>" or null}`. `null` means no next page. | `list_body_has_items_and_next_cursor_null_on_last_page` |
| 17 | Order: `created_at` newest first, then `id`. The order is fixed. | `list_order_is_created_at_desc_then_id` |
| 18 | The cursor is an opaque string. A bad cursor gives `400`. | `bad_cursor_returns_400_and_cursor_is_opaque` |
| 19 | No total count. | `list_body_has_no_total` |
| 20 | Sort and filter are not part of this standard. Each is a later standard that CNL must name. | `list_has_no_sort_or_filter_parameter` |

## Facts that CNL must give

- The words `standard pagination` on the list of the resource.

Nothing else. The standard does not guess a fact.

## Tests

Every row of the Decisions table names its proof test. The crate has one success test for each list operation and one test for each error code of this standard (`400`). The property test `pagination_walk_gives_all_items_once` walks all pages and checks that all items come once, with no repeat and no gap.

## Usual changes

| Change | Decisions touched |
|---|---|
| Add a filter to the list | 14, 20 |
| Add a sort to the list | 17, 20 |

## Version

som.std.v1
