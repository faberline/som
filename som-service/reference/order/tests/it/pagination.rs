use std::cmp::Reverse;

use axum::http::StatusCode;
use proptest::prelude::*;
use som_reference_order::application::orders::list_orders;
use som_reference_order::application::pagination::{
    DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, encode_cursor,
};
use som_reference_order::application::store::ListQuery;
use som_reference_order::infrastructure::memory_store::MemoryStore;

use crate::common::{app, body_json, call, seed};
use crate::fixture::Must;
use crate::fixture::{check_list_item, filter_by_field_query};

const DAYS: [&str; 5] = [
    "2026-01-01T00:00:00Z",
    "2026-01-02T00:00:00Z",
    "2026-01-03T00:00:00Z",
    "2026-01-04T00:00:00Z",
    "2026-01-05T00:00:00Z",
];

fn ids_of(body: &serde_json::Value) -> Vec<String> {
    body["items"]
        .as_array()
        .must("items is an array")
        .iter()
        .map(|item| item["id"].as_str().must("id is a string").to_owned())
        .collect()
}

#[tokio::test]
async fn list_returns_200_with_standard_pagination() {
    let (app, store) = app();
    seed(&store, "a", DAYS[0]);
    seed(&store, "b", DAYS[1]);
    seed(&store, "c", DAYS[2]);

    let (status, _, body) = call(&app, "GET", "/v1/orders", None).await;
    assert_eq!(status, StatusCode::OK);
    let value = body_json(&body);
    let items = value["items"].as_array().must("items is an array");
    assert_eq!(items.len(), 3);
    for item in items {
        check_list_item(item);
    }
    assert!(value["next_cursor"].is_null());

    let (empty_app, _) = crate::common::app();
    let (status, _, body) = call(&empty_app, "GET", "/v1/orders", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body_json(&body),
        serde_json::json!({"items": [], "next_cursor": null})
    );
}

#[tokio::test]
async fn list_has_no_offset_parameter() {
    let (app, _store) = app();
    let (status, _, body) = call(&app, "GET", "/v1/orders?offset=1", None).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let value = body_json(&body);
    assert_eq!(value["error"], "invalid_request");
    assert!(value["message"].as_str().must("message").contains("offset"));
}

#[tokio::test]
async fn list_parameters_are_limit_and_cursor_only() {
    let (app, store) = app();
    seed(&store, "a", DAYS[0]);
    seed(&store, "b", DAYS[1]);

    let (status, _, body) = call(&app, "GET", "/v1/orders?limit=1", None).await;
    assert_eq!(status, StatusCode::OK);
    let cursor = body_json(&body)["next_cursor"]
        .as_str()
        .must("a cursor is given")
        .to_owned();

    let (status, _, _) = call(
        &app,
        "GET",
        &format!("/v1/orders?limit=1&cursor={cursor}"),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    let (status, _, _) = call(&app, "GET", "/v1/orders?foo=1", None).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);

    let (status, _, _) = call(&app, "GET", "/v1/orders?limit=1&limit=2", None).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn default_limit_50_max_200_and_larger_limit_returns_400() {
    assert_eq!(DEFAULT_LIMIT, 50);
    assert_eq!(MAX_LIMIT, 200);

    let (app, store) = app();
    for index in 0..51 {
        seed(&store, &format!("o{index:02}"), DAYS[0]);
    }

    let (status, _, body) = call(&app, "GET", "/v1/orders", None).await;
    assert_eq!(status, StatusCode::OK);
    let value = body_json(&body);
    assert_eq!(ids_of(&value).len(), 50);
    assert!(value["next_cursor"].is_string());

    let (status, _, body) = call(&app, "GET", "/v1/orders?limit=200", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(ids_of(&body_json(&body)).len(), 51);

    for query in ["limit=201", "limit=0", "limit=abc", "limit=-1"] {
        let (status, _, _) = call(&app, "GET", &format!("/v1/orders?{query}"), None).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{query} must be refused");
    }
}

#[tokio::test]
async fn list_body_has_items_and_next_cursor_null_on_last_page() {
    let (app, store) = app();
    seed(&store, "a", DAYS[0]);
    seed(&store, "b", DAYS[1]);
    seed(&store, "c", DAYS[2]);

    let (status, _, body) = call(&app, "GET", "/v1/orders?limit=2", None).await;
    assert_eq!(status, StatusCode::OK);
    let first = body_json(&body);
    assert_eq!(ids_of(&first).len(), 2);
    let cursor = first["next_cursor"]
        .as_str()
        .must("a cursor is given")
        .to_owned();

    let (status, _, body) = call(
        &app,
        "GET",
        &format!("/v1/orders?limit=2&cursor={cursor}"),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let last = body_json(&body);
    assert_eq!(ids_of(&last).len(), 1);
    assert!(last["next_cursor"].is_null());

    let mut keys: Vec<&str> = last
        .as_object()
        .must("body is an object")
        .keys()
        .map(String::as_str)
        .collect();
    keys.sort_unstable();
    assert_eq!(keys, ["items", "next_cursor"]);
}

#[tokio::test]
async fn list_order_is_created_at_desc_then_id() {
    let (app, store) = app();
    seed(&store, "b", DAYS[2]);
    seed(&store, "a", DAYS[2]);
    seed(&store, "c", DAYS[0]);
    seed(&store, "d", DAYS[4]);

    let (status, _, body) = call(&app, "GET", "/v1/orders", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(ids_of(&body_json(&body)), ["d", "a", "b", "c"]);
}

#[tokio::test]
async fn bad_cursor_returns_400_and_cursor_is_opaque() {
    let (app, store) = app();
    seed(&store, "a", DAYS[2]);
    seed(&store, "b", DAYS[1]);

    for query in ["cursor=not-a-cursor", "cursor=", "cursor=6162", "cursor=zz"] {
        let (status, _, _) = call(&app, "GET", &format!("/v1/orders?{query}"), None).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{query} must be refused");
    }

    let encoded = encode_cursor(DAYS[2], "a");
    assert!(!encoded.contains("2026"));
    assert!(!encoded.contains("a|"));
    assert_eq!(
        decode_cursor(&encoded),
        Some((DAYS[2].to_owned(), "a".to_owned()))
    );

    let (status, _, body) = call(&app, "GET", "/v1/orders?limit=1", None).await;
    assert_eq!(status, StatusCode::OK);
    let cursor = body_json(&body)["next_cursor"]
        .as_str()
        .must("a cursor is given")
        .to_owned();
    assert!(!cursor.contains("2026"));
}

#[test]
fn decode_cursor_refuses_odd_length_and_empty_parts() {
    assert_eq!(decode_cursor("616"), None);
    assert_eq!(decode_cursor(&encode_cursor("", "a")), None);
    assert_eq!(decode_cursor(&encode_cursor("2026", "")), None);
}

#[tokio::test]
async fn list_body_has_no_total() {
    let (app, store) = app();
    seed(&store, "a", DAYS[0]);
    seed(&store, "b", DAYS[1]);

    for query in ["/v1/orders?limit=2", "/v1/orders?limit=5"] {
        let (status, _, body) = call(&app, "GET", query, None).await;
        assert_eq!(status, StatusCode::OK);
        assert!(
            body_json(&body).get("total").is_none(),
            "{query} has a total"
        );
    }
}

#[tokio::test]
async fn list_has_no_sort_or_filter_parameter() {
    let (app, store) = app();
    seed(&store, "a", DAYS[0]);

    for query in [
        "sort=created_at",
        "sort=-created_at",
        filter_by_field_query(),
        "filter=x",
    ] {
        let (status, _, _) = call(&app, "GET", &format!("/v1/orders?{query}"), None).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{query} must be refused");
    }
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
                    after = Some(decode_cursor(&cursor).must("own cursor decodes"));
                }
            }
        }
        prop_assert_eq!(seen, expected_ids);
    }
}
