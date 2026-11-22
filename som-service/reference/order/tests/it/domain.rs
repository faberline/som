use serde_json::json;
use som_reference_order::domain::order::{
    FieldError, Order, OrderInput, new_id, now_rfc3339, parse_input, to_json,
};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};

use crate::fixture::{
    INVALID_FIELDS, REQUIRED_FIELDS, WRONG_TYPE_FIELDS, at_the_max_length_body,
    check_valid_defaults, check_valid_input, float_in_integer_body, float_in_integer_errors,
    invalid_body, null_optional_body, null_optional_is_none, valid_body, wrong_type_body,
};

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

    let mut body = valid_body();
    body["id"] = json!("x");
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
    check_valid_defaults(&json);
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
    let body = invalid_body();
    let errors = parse_input(&body).expect_err("every bad field is refused");
    assert_eq!(field_names(&errors), INVALID_FIELDS);

    let float_in_integer = float_in_integer_body();
    let errors = parse_input(&float_in_integer).expect_err("a float is refused");
    assert_eq!(errors, float_in_integer_errors());

    let null_optional = null_optional_body();
    assert!(
        null_optional_is_none(&null_optional),
        "a null optional field is None"
    );

    let long_note = at_the_max_length_body();
    assert!(parse_input(&long_note).is_ok(), "500 characters is valid");

    let non_string = wrong_type_body();
    let errors = parse_input(&non_string).expect_err("non-string fields are refused");
    assert_eq!(field_names(&errors), WRONG_TYPE_FIELDS);

    let errors = parse_input(&json!([1, 2])).expect_err("an array is not an order");
    assert_eq!(field_names(&errors), ["body"]);

    let errors = parse_input(&json!({})).expect_err("required fields are missing");
    assert_eq!(field_names(&errors), REQUIRED_FIELDS);
    assert!(errors.iter().all(|e| e.message == "is required"));

    check_valid_input(&valid_input());
}
