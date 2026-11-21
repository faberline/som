//! Every literal that names an Order field, a field value or the typed
//! struct of the reference tests. The other test files call these functions.

use serde_json::{Value, json};
use som_reference_order::domain::order::{FieldError, Order, OrderInput, Status};

/// The fields of the body, in the order of the plan (no id, no time fields).
pub const FIELDS: [&str; 5] = ["customer_id", "total_cents", "currency", "status", "note"];

/// The keys of a resource in a response.
pub const RESOURCE_KEYS: [&str; 8] = [
    "id",
    "customer_id",
    "total_cents",
    "currency",
    "status",
    "note",
    "created_at",
    "updated_at",
];

/// The fields that `invalid_body` gets wrong, in order.
pub const INVALID_FIELDS: [&str; 6] = [
    "customer_id",
    "total_cents",
    "currency",
    "status",
    "note",
    "extra",
];

/// The fields that `wrong_type_body` gives a value of the wrong JSON type, in order.
pub const WRONG_TYPE_FIELDS: [&str; 4] = ["customer_id", "currency", "status", "note"];

/// The fields that an empty body lacks, in order.
pub const REQUIRED_FIELDS: [&str; 4] = ["customer_id", "total_cents", "currency", "status"];

pub fn valid_body() -> Value {
    json!({"customer_id": "c1", "total_cents": 100, "currency": "USD", "status": "pending"})
}

/// A valid body that differs in every field (for `PUT`).
pub fn replacement_body() -> Value {
    json!({
        "customer_id": "c2",
        "total_cents": 250,
        "currency": "EUR",
        "status": "paid",
        "note": "hi"
    })
}

/// A body with one bad value for every constrained field, and an unknown field.
pub fn invalid_body() -> Value {
    json!({
        "customer_id": "",
        "total_cents": -1,
        "currency": "usd",
        "status": "lost",
        "note": "x".repeat(501),
        "extra": 1
    })
}

/// A valid body with a value of the wrong JSON type for each string field.
pub fn wrong_type_body() -> Value {
    let mut body = valid_body();
    body["customer_id"] = json!(5);
    body["currency"] = json!(1);
    body["status"] = json!(true);
    body["note"] = json!(7);
    body
}

/// A valid body whose `total_cents` is a float.
pub fn float_total_body() -> Value {
    let mut body = valid_body();
    body["total_cents"] = json!(1.5);
    body
}

/// A valid body whose optional `note` is null.
pub fn null_note_body() -> Value {
    let mut body = valid_body();
    body["note"] = Value::Null;
    body
}

/// A valid body whose `note` has the largest allowed length.
pub fn at_the_max_length_body() -> Value {
    body_with_text("n".repeat(500))
}

/// A valid body with an empty `customer_id`.
pub fn one_bad_field_body() -> Value {
    let mut body = valid_body();
    body["customer_id"] = json!("");
    body
}

/// A valid body whose optional string `note` has `len` characters.
pub fn body_with_big_text(len: usize) -> Value {
    body_with_text("a".repeat(len))
}

/// A valid body whose `note` is `text`.
pub fn with_text(text: &str) -> Value {
    body_with_text(text.to_owned())
}

/// A valid body whose `total_cents` is `n`.
pub fn with_number(n: i64) -> Value {
    let mut body = valid_body();
    body["total_cents"] = json!(n);
    body
}

/// The `note` of a body or of a resource.
pub fn text_of(value: &Value) -> &Value {
    &value["note"]
}

/// The valid body with its keys in another order.
pub fn reordered_valid_json() -> &'static str {
    r#"{"status":"pending","currency":"USD","total_cents":100,"customer_id":"c1"}"#
}

/// The asserts for one item of a list response.
pub fn check_list_item(item: &Value) {
    assert!(item["id"].is_string());
    assert!(item["customer_id"].is_string());
    assert!(item["total_cents"].is_i64());
    assert!(item["currency"].is_string());
    assert!(item["status"].is_string());
    assert!(item["created_at"].is_string());
    assert!(item["updated_at"].is_string());
}

/// The asserts for a resource that a create returned without a note.
pub fn check_created(value: &Value) {
    for field in FIELDS {
        assert!(value.get(field).is_some(), "{field} is in the resource");
    }
    assert!(value["note"].is_null());
    assert_eq!(value["customer_id"], "c1");
}

/// The asserts for a resource that a replace returned.
pub fn check_replaced(value: &Value) {
    assert_eq!(value["customer_id"], "c2");
    assert_eq!(value["total_cents"], 250);
    assert_eq!(value["note"], "hi");
}

/// The default status and note of a valid body.
pub fn check_valid_defaults(json: &Value) {
    assert_eq!(json["status"], "pending");
    assert_eq!(json["note"], Value::Null);
}

/// The status of a parsed valid body.
pub fn check_valid_input(input: &OrderInput) {
    assert_eq!(input.status, Status::Pending);
}

/// The change that a store test makes to a stored order.
pub fn change(order: &mut Order) {
    order.total_cents = 250;
    order.status = Status::Paid;
}

/// The total of an order that `change` was applied to.
pub fn changed_total(order: &Order) -> i64 {
    order.total_cents
}

/// The one field error of `float_total_body`.
pub fn float_total_errors() -> Vec<FieldError> {
    vec![FieldError {
        field: "total_cents".to_owned(),
        message: "must be an integer".to_owned(),
    }]
}

/// The two field errors that the error body test uses.
pub fn validation_field_errors() -> Vec<FieldError> {
    vec![
        FieldError {
            field: "customer_id".to_owned(),
            message: "is required".to_owned(),
        },
        FieldError {
            field: "currency".to_owned(),
            message: "must be three upper-case letters".to_owned(),
        },
    ]
}

fn body_with_text(note: String) -> Value {
    let mut body = valid_body();
    body["note"] = json!(note);
    body
}
