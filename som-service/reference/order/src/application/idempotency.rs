use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use crate::application::store::StoreError;
use crate::domain::order::Order;

pub const KEY_TTL_SECS: i64 = 24 * 60 * 60;

pub trait Clock: Send + Sync {
    fn now_secs(&self) -> i64;
}

pub struct SystemClock;

impl Clock for SystemClock {
    fn now_secs(&self) -> i64 {
        time::OffsetDateTime::now_utc().unix_timestamp()
    }
}

struct Entry {
    fingerprint: String,
    order: Order,
    stored_at: i64,
}

// core-gap: 12 (core has no Idempotency-Key store)
pub struct IdempotencyKeys {
    clock: Arc<dyn Clock>,
    entries: Mutex<HashMap<String, Entry>>,
}

impl IdempotencyKeys {
    pub fn new(clock: Arc<dyn Clock>) -> IdempotencyKeys {
        IdempotencyKeys {
            clock,
            entries: Mutex::new(HashMap::new()),
        }
    }

    fn lock(&self) -> MutexGuard<'_, HashMap<String, Entry>> {
        self.entries.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Creates the order once per key. `Ok(None)` means the key was used
    /// before with a different body. The lock is held while `create` runs,
    /// so two requests with the same key cannot both create.
    pub fn create_once(
        &self,
        key: &str,
        fingerprint: &str,
        create: impl FnOnce() -> Result<Order, StoreError>,
    ) -> Result<Option<Order>, StoreError> {
        let now = self.clock.now_secs();
        let mut entries = self.lock();
        entries.retain(|_, entry| now - entry.stored_at < KEY_TTL_SECS);
        if let Some(entry) = entries.get(key) {
            return Ok((entry.fingerprint == fingerprint).then(|| entry.order.clone()));
        }
        let order = create()?;
        entries.insert(
            key.to_owned(),
            Entry {
                fingerprint: fingerprint.to_owned(),
                order: order.clone(),
                stored_at: now,
            },
        );
        Ok(Some(order))
    }
}
