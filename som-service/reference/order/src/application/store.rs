use crate::domain::order::Order;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StoreError {
    NotFound,
}

/// Storage of orders. `after` is the (created_at, id) of the last item of the
/// previous page; the page holds only items strictly after it.
pub trait OrderStore: Send + Sync {
    fn create(&self, order: Order) -> Result<Order, StoreError>;
    fn get(&self, id: &str) -> Result<Order, StoreError>;
    fn list_page(&self, after: Option<(&str, &str)>, limit: usize) -> Vec<Order>;
    fn replace(&self, order: Order) -> Result<Order, StoreError>;
    fn delete(&self, id: &str) -> Result<(), StoreError>;
}
