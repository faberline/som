use std::cmp::Reverse;
use std::collections::BTreeMap;
use std::sync::{Mutex, MutexGuard, PoisonError};

use crate::application::store::{OrderStore, StoreError};
use crate::domain::order::Order;

#[derive(Debug, Default)]
pub struct MemoryStore {
    orders: Mutex<BTreeMap<String, Order>>,
}

impl MemoryStore {
    pub fn new() -> MemoryStore {
        MemoryStore::default()
    }

    fn lock(&self) -> MutexGuard<'_, BTreeMap<String, Order>> {
        self.orders.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

impl OrderStore for MemoryStore {
    fn create(&self, order: Order) -> Result<Order, StoreError> {
        self.lock().insert(order.id.clone(), order.clone());
        Ok(order)
    }

    fn get(&self, id: &str) -> Result<Order, StoreError> {
        self.lock().get(id).cloned().ok_or(StoreError::NotFound)
    }

    fn list_page(&self, after: Option<(&str, &str)>, limit: usize) -> Vec<Order> {
        let mut all: Vec<Order> = self.lock().values().cloned().collect();
        all.sort_by(|a, b| (Reverse(&a.created_at), &a.id).cmp(&(Reverse(&b.created_at), &b.id)));
        all.into_iter()
            .skip_while(|order| {
                after.is_some_and(|(created_at, id)| {
                    (Reverse(order.created_at.as_str()), order.id.as_str())
                        <= (Reverse(created_at), id)
                })
            })
            .take(limit)
            .collect()
    }

    fn replace(&self, order: Order) -> Result<Order, StoreError> {
        let mut orders = self.lock();
        match orders.get_mut(&order.id) {
            Some(slot) => {
                *slot = order.clone();
                Ok(order)
            }
            None => Err(StoreError::NotFound),
        }
    }

    fn delete(&self, id: &str) -> Result<(), StoreError> {
        self.lock()
            .remove(id)
            .map(|_| ())
            .ok_or(StoreError::NotFound)
    }
}
