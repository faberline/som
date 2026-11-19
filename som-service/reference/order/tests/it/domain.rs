use serde_json::{Value, json};
use som_reference_order::domain::order::{
    FieldError, Order, OrderInput, Status, new_id, now_rfc3339, parse_input, to_json,
};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};

fn valid_body() -> Value {
    json!({"customer_id": "c1", "total_cents": 100, "currency": "USD", "status": "pending"})
}

fn valid_input() -> OrderInput {
    parse_input(&valid_body()).expect("valid body parses")
}

fn field_names(errors: &[FieldError]) -> Vec<&str> {
    errors.iter().map(|e| e.field.as_str()).collect()
}

#[test]
fn id_is_server_made_uuidv7_and_client_id_is_refused() {
    let id = new_id();
    let parsed = uuid::Uuid::parse_str(&id).expect("id parses as a uuid");
    assert_eq!(parsed.get_version_num(), 7);
    assert_ne!(new_id(), id);

    let body = json!({"id": "x", "customer_id": "c", "total_cents": 1, "currency": "USD", "status": "pending"});
    let errors = parse_input(&body).expect_err("a client id is refused");
    assert_eq!(
        errors,
        vec![FieldError {
            field: "id".to_owned(),
            message: "unknown field".to_owned(),
        }]
    );
}

#[test]
fn time_fields_are_rfc3339_utc_and_set_by_server() {
    let now = now_rfc3339();
    OffsetDateTime::parse(&now, &Rfc3339).expect("now is RFC 3339");
    assert!(now.ends_with('Z'), "UTC: {now}");

    let order = Order::create(
        valid_input(),
        "id-1".to_owned(),
        "2026-01-01T00:00:00Z".to_owned(),
    );
    assert_eq!(order.created_at, "2026-01-01T00:00:00Z");
    assert_eq!(order.updated_at, "2026-01-01T00:00:00Z");

    let replaced = order.replace(valid_input(), "2026-01-02T00:00:00Z".to_owned());
    assert_eq!(replaced.id, "id-1");
    assert_eq!(replaced.created_at, "2026-01-01T00:00:00Z");
    assert_eq!(replaced.updated_at, "2026-01-02T00:00:00Z");
    let json = to_json(&replaced);
    assert_eq!(json["id"], "id-1");
    assert_eq!(json["status"], "pending");
    assert_eq!(json["note"], Value::Null);
    assert_eq!(json["updated_at"], "2026-01-02T00:00:00Z");

    let mut body = valid_body();
    body["created_at"] = json!("2020-01-01T00:00:00Z");
    let errors = parse_input(&body).expect_err("a client time is refused");
    assert_eq!(
        errors,
        vec![FieldError {
            field: "created_at".to_owned(),
            message: "unknown field".to_owned(),
        }]
    );
}

#[test]
fn unknown_field_is_refused_and_bad_fields_give_422_with_one_entry_each() {
    let body = json!({
        "customer_id": "",
        "total_cents": -1,
        "currency": "usd",
        "status": "lost",
        "note": "x".repeat(501),
        "extra": 1
    });
    let errors = parse_input(&body).expect_err("every bad field is refused");
    assert_eq!(
        field_names(&errors),
        [
            "customer_id",
            "total_cents",
            "currency",
            "status",
            "note",
            "extra"
        ]
    );

    let mut float_total = valid_body();
    float_total["total_cents"] = json!(1.5);
    let errors = parse_input(&float_total).expect_err("a float is refused");
    assert_eq!(
        errors,
        vec![FieldError {
            field: "total_cents".to_owned(),
            message: "must be an integer".to_owned(),
        }]
    );

    let mut null_note = valid_body();
    null_note["note"] = Value::Null;
    let input = parse_input(&null_note).expect("null note is valid");
    assert_eq!(input.note, None);

    let mut long_note = valid_body();
    long_note["note"] = json!("n".repeat(500));
    assert!(parse_input(&long_note).is_ok(), "500 characters is valid");

    let mut non_string = valid_body();
    non_string["customer_id"] = json!(5);
    non_string["currency"] = json!(1);
    non_string["status"] = json!(true);
    non_string["note"] = json!(7);
    let errors = parse_input(&non_string).expect_err("non-string fields are refused");
    assert_eq!(
        field_names(&errors),
        ["customer_id", "currency", "status", "note"]
    );

    let errors = parse_input(&json!([1, 2])).expect_err("an array is not an order");
    assert_eq!(field_names(&errors), ["body"]);

    let errors = parse_input(&json!({})).expect_err("required fields are missing");
    assert_eq!(
        field_names(&errors),
        ["customer_id", "total_cents", "currency", "status"]
    );
    assert!(errors.iter().all(|e| e.message == "is required"));

    let status: Status = valid_input().status;
    assert_eq!(status, Status::Pending);
}
