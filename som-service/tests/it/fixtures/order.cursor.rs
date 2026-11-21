use orders_service::application::orders::list_orders;
use orders_service::application::pagination::{decode_cursor, encode_cursor};
use orders_service::application::store::{ListQuery, OrderStore};
use orders_service::domain::order::{Order, OrderInput, parse_input};
use orders_service::infrastructure::memory_store::MemoryStore;
use proptest::prelude::*;
use serde_json::json;
use std::cmp::Reverse;

const DAYS: [&str; 5] = [
    "2026-01-01T00:00:00Z",
    "2026-01-02T00:00:00Z",
    "2026-01-03T00:00:00Z",
    "2026-01-04T00:00:00Z",
    "2026-01-05T00:00:00Z",
];

fn seed(store: &MemoryStore, id: &str, created_at: &str) {
    let input: OrderInput = parse_input(
        &json!({"customer_id":"c","total_cents":1,"currency":"USD","status":"pending"}),
    )
    .expect("valid");
    store
        .create(Order::create(input, id.to_owned(), created_at.to_owned()))
        .expect("create");
}

#[test]
fn decode_cursor_refuses_odd_length_and_empty_parts() {
    assert_eq!(decode_cursor("616"), None);
    assert_eq!(decode_cursor(&encode_cursor("", "a")), None);
    assert_eq!(decode_cursor(&encode_cursor("2026", "")), None);
}

#[test]
fn list_orders_full_last_page_has_no_cursor() {
    let store = MemoryStore::new();
    seed(&store, "a", DAYS[0]);
    seed(&store, "b", DAYS[1]);
    seed(&store, "c", DAYS[2]);

    let query = |limit| ListQuery { after: None, limit };
    assert!(list_orders(&store, &query(3)).next_cursor.is_none());
    assert!(list_orders(&store, &query(2)).next_cursor.is_some());
}

proptest! {
    #[test]
    fn pagination_walk_gives_all_items_once(
        days in proptest::collection::vec(0usize..5, 0..=40),
        limit in 1usize..=7,
    ) {
        let store = MemoryStore::new();
        let mut expected: Vec<(String, String)> = Vec::new();
        for (index, day) in days.iter().enumerate() {
            let id = format!("o{index:02}");
            seed(&store, &id, DAYS[*day]);
            expected.push((DAYS[*day].to_owned(), id));
        }
        expected.sort_by(|a, b| (Reverse(&a.0), &a.1).cmp(&(Reverse(&b.0), &b.1)));
        let expected_ids: Vec<String> = expected.into_iter().map(|(_, id)| id).collect();

        let mut seen: Vec<String> = Vec::new();
        let mut after: Option<(String, String)> = None;
        let mut pages = 0;
        loop {
            pages += 1;
            assert!(pages <= 100, "the walk does not end");
            let page = list_orders(
                &store,
                &ListQuery {
                    after: after.as_ref().map(|(created_at, id)| (created_at.as_str(), id.as_str())),
                    limit,
                },
            );
            if page.next_cursor.is_some() {
                assert_eq!(page.items.len(), limit, "a full page has limit items");
            }
            seen.extend(page.items.iter().map(|order| order.id.clone()));
            match page.next_cursor {
                None => break,
                Some(cursor) => {
                    after = Some(decode_cursor(&cursor).expect("own cursor decodes"));
                }
            }
        }
        prop_assert_eq!(seen, expected_ids);
    }
}
