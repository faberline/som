use serde_json::{Value, json};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Pending,
    Paid,
    Shipped,
    Canceled,
}

impl Status {
    pub fn as_str(&self) -> &'static str {
        match self {
            Status::Pending => "pending",
            Status::Paid => "paid",
            Status::Shipped => "shipped",
            Status::Canceled => "canceled",
        }
    }

    pub fn parse(text: &str) -> Option<Status> {
        match text {
            "pending" => Some(Status::Pending),
            "paid" => Some(Status::Paid),
            "shipped" => Some(Status::Shipped),
            "canceled" => Some(Status::Canceled),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderInput {
    pub customer_id: String,
    pub total_cents: i64,
    pub currency: String,
    pub status: Status,
    pub note: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FieldError {
    pub field: String,
    pub message: String,
}

fn field_error(field: &str, message: &str) -> FieldError {
    FieldError {
        field: field.to_owned(),
        message: message.to_owned(),
    }
}

/// Reads a create or replace body. Every bad field gets its own entry, in the
/// order customer_id, total_cents, currency, status, note, then unknown fields.
pub fn parse_input(body: &Value) -> Result<OrderInput, Vec<FieldError>> {
    let Some(object) = body.as_object() else {
        return Err(vec![field_error("body", "must be a JSON object")]);
    };
    let mut errors = Vec::new();

    let customer_id = match object.get("customer_id") {
        None => {
            errors.push(field_error("customer_id", "is required"));
            None
        }
        Some(Value::String(text)) if text.is_empty() => {
            errors.push(field_error("customer_id", "must not be empty"));
            None
        }
        Some(Value::String(text)) => Some(text.clone()),
        Some(_) => {
            errors.push(field_error("customer_id", "must be a string"));
            None
        }
    };

    let total_cents = match object.get("total_cents") {
        None => {
            errors.push(field_error("total_cents", "is required"));
            None
        }
        Some(value) => match value.as_i64() {
            Some(cents) if cents >= 0 => Some(cents),
            Some(_) => {
                errors.push(field_error("total_cents", "must be 0 or more"));
                None
            }
            None => {
                errors.push(field_error("total_cents", "must be an integer"));
                None
            }
        },
    };

    let currency = match object.get("currency") {
        None => {
            errors.push(field_error("currency", "is required"));
            None
        }
        Some(Value::String(text))
            if text.len() == 3 && text.bytes().all(|b| b.is_ascii_uppercase()) =>
        {
            Some(text.clone())
        }
        Some(_) => {
            errors.push(field_error("currency", "must be three upper-case letters"));
            None
        }
    };

    let status = match object.get("status") {
        None => {
            errors.push(field_error("status", "is required"));
            None
        }
        Some(Value::String(text)) => match Status::parse(text) {
            Some(status) => Some(status),
            None => {
                errors.push(field_error(
                    "status",
                    "must be one of pending, paid, shipped, canceled",
                ));
                None
            }
        },
        Some(_) => {
            errors.push(field_error(
                "status",
                "must be one of pending, paid, shipped, canceled",
            ));
            None
        }
    };

    let note = match object.get("note") {
        None | Some(Value::Null) => Some(None),
        Some(Value::String(text)) if text.chars().count() <= 500 => Some(Some(text.clone())),
        Some(Value::String(_)) => {
            errors.push(field_error("note", "must be at most 500 characters"));
            None
        }
        Some(_) => {
            errors.push(field_error("note", "must be a string"));
            None
        }
    };

    for key in object.keys() {
        if !matches!(
            key.as_str(),
            "customer_id" | "total_cents" | "currency" | "status" | "note"
        ) {
            errors.push(field_error(key, "unknown field"));
        }
    }

    match (customer_id, total_cents, currency, status, note) {
        (Some(customer_id), Some(total_cents), Some(currency), Some(status), Some(note))
            if errors.is_empty() =>
        {
            Ok(OrderInput {
                customer_id,
                total_cents,
                currency,
                status,
                note,
            })
        }
        _ => Err(errors),
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Order {
    pub id: String,
    pub created_at: String,
    pub updated_at: String,
    pub customer_id: String,
    pub total_cents: i64,
    pub currency: String,
    pub status: Status,
    pub note: Option<String>,
}

pub fn new_id() -> String {
    uuid::Uuid::now_v7().to_string()
}

pub fn now_rfc3339() -> String {
    time::OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .unwrap_or_else(|_| "1970-01-01T00:00:00Z".to_owned())
}

impl Order {
    pub fn create(input: OrderInput, id: String, now: String) -> Order {
        Order {
            id,
            created_at: now.clone(),
            updated_at: now,
            customer_id: input.customer_id,
            total_cents: input.total_cents,
            currency: input.currency,
            status: input.status,
            note: input.note,
        }
    }

    pub fn replace(&self, input: OrderInput, now: String) -> Order {
        Order {
            id: self.id.clone(),
            created_at: self.created_at.clone(),
            updated_at: now,
            customer_id: input.customer_id,
            total_cents: input.total_cents,
            currency: input.currency,
            status: input.status,
            note: input.note,
        }
    }
}

pub fn to_json(order: &Order) -> Value {
    json!({
        "id": order.id,
        "customer_id": order.customer_id,
        "total_cents": order.total_cents,
        "currency": order.currency,
        "status": order.status.as_str(),
        "note": order.note,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    })
}
