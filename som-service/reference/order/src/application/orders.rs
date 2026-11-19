use crate::application::pagination::encode_cursor;
use crate::application::store::{OrderStore, StoreError};
use crate::domain::order::{Order, OrderInput, new_id, now_rfc3339};

pub struct Page {
    pub items: Vec<Order>,
    pub next_cursor: Option<String>,
}

pub fn create_order(store: &dyn OrderStore, input: OrderInput) -> Result<Order, StoreError> {
    store.create(Order::create(input, new_id(), now_rfc3339()))
}

pub fn get_order(store: &dyn OrderStore, id: &str) -> Result<Order, StoreError> {
    store.get(id)
}

pub fn replace_order(
    store: &dyn OrderStore,
    id: &str,
    input: OrderInput,
) -> Result<Order, StoreError> {
    let existing = store.get(id)?;
    store.replace(existing.replace(input, now_rfc3339()))
}

pub fn delete_order(store: &dyn OrderStore, id: &str) -> Result<(), StoreError> {
    store.delete(id)
}

/// Asks for one item more than `limit`: its presence says that another page
/// follows, and the cursor is the last item that is kept.
pub fn list_orders(store: &dyn OrderStore, after: Option<(&str, &str)>, limit: usize) -> Page {
    let mut items = store.list_page(after, limit + 1);
    let next_cursor = if items.len() > limit {
        items.truncate(limit);
        items
            .last()
            .map(|last| encode_cursor(&last.created_at, &last.id))
    } else {
        None
    };
    Page { items, next_cursor }
}
