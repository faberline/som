use som_reference_order::application::store::{ListQuery, OrderStore, StoreError};
use som_reference_order::domain::order::{Order, OrderInput, parse_input};
use som_reference_order::infrastructure::memory_store::MemoryStore;

use crate::fixture::Must;
use crate::fixture::{change, changed_total, valid_body};

fn input() -> OrderInput {
    parse_input(&valid_body()).must("valid body parses")
}

fn order(id: &str, created_at: &str) -> Order {
    Order::create(input(), id.to_owned(), created_at.to_owned())
}

#[test]
fn store_trait_has_in_memory_implementation() {
    let store: Box<dyn OrderStore> = Box::new(MemoryStore::new());
    let created = store
        .create(order("a", "2026-01-01T00:00:00Z"))
        .must("create");
    assert_eq!(store.get("a").must("get"), created);
    assert_eq!(store.get("zzz"), Err(StoreError::NotFound));

    let mut changed = created.clone();
    change(&mut changed);
    let replaced = store.replace(changed.clone()).must("replace");
    assert_eq!(changed_total(&replaced), 250);
    assert_eq!(store.get("a").must("get after replace"), changed);
    let mut unknown = order("nope", "2026-01-01T00:00:00Z");
    unknown.id = "nope".to_owned();
    assert_eq!(store.replace(unknown), Err(StoreError::NotFound));

    store.delete("a").must("delete");
    assert_eq!(store.get("a"), Err(StoreError::NotFound));
    assert_eq!(store.delete("a"), Err(StoreError::NotFound));
}

#[test]
fn list_page_orders_by_created_at_desc_then_id_and_resumes_after_cursor() {
    let store = MemoryStore::new();
    store
        .create(order("c", "2026-01-01T00:00:00Z"))
        .must("create c");
    store
        .create(order("b", "2026-01-03T00:00:00Z"))
        .must("create b");
    store
        .create(order("a", "2026-01-03T00:00:00Z"))
        .must("create a");

    let ids = |orders: Vec<Order>| orders.into_iter().map(|o| o.id).collect::<Vec<_>>();
    let page = |after, limit| store.list_page(&ListQuery { after, limit });
    assert_eq!(ids(page(None, 10)), ["a", "b", "c"]);
    assert_eq!(ids(page(None, 2)), ["a", "b"]);
    assert_eq!(
        ids(page(Some(("2026-01-03T00:00:00Z", "a")), 10)),
        ["b", "c"]
    );
    assert!(page(None, 0).is_empty());
}
