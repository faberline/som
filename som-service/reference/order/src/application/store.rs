use crate::domain::order::Order;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StoreError {
    NotFound,
}

/// The arguments of one page of a list. `after` is the (created_at, id) of the
/// last item of the previous page; the page holds only items strictly after it.
#[derive(Debug, Clone, Copy, Default)]
pub struct ListQuery<'a> {
    pub after: Option<(&'a str, &'a str)>,
    pub limit: usize,
}

/// Storage of orders.
pub trait OrderStore: Send + Sync {
    fn create(&self, order: Order) -> Result<Order, StoreError>;
    fn get(&self, id: &str) -> Result<Order, StoreError>;
    fn list_page(&self, query: &ListQuery<'_>) -> Vec<Order>;
    fn replace(&self, order: Order) -> Result<Order, StoreError>;
    fn delete(&self, id: &str) -> Result<(), StoreError>;
}
