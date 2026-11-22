//! The Order field literals of the reference tests live in one file,
//! `reference/order/tests/it/fixture.rs`. No other reference test file may
//! name an Order field, a field value or `Status::`.

const REFERENCE_FILES: [(&str, &str); 16] = [
    (
        "auth.rs",
        include_str!("../../reference/order/tests/it/auth.rs"),
    ),
    (
        "common.rs",
        include_str!("../../reference/order/tests/it/common.rs"),
    ),
    (
        "domain.rs",
        include_str!("../../reference/order/tests/it/domain.rs"),
    ),
    (
        "errors.rs",
        include_str!("../../reference/order/tests/it/errors.rs"),
    ),
    (
        "idempotency.rs",
        include_str!("../../reference/order/tests/it/idempotency.rs"),
    ),
    (
        "layers.rs",
        include_str!("../../reference/order/tests/it/layers.rs"),
    ),
    (
        "limits.rs",
        include_str!("../../reference/order/tests/it/limits.rs"),
    ),
    (
        "metrics.rs",
        include_str!("../../reference/order/tests/it/metrics.rs"),
    ),
    (
        "pagination.rs",
        include_str!("../../reference/order/tests/it/pagination.rs"),
    ),
    (
        "preconditions.rs",
        include_str!("../../reference/order/tests/it/preconditions.rs"),
    ),
    (
        "probes.rs",
        include_str!("../../reference/order/tests/it/probes.rs"),
    ),
    (
        "quality.rs",
        include_str!("../../reference/order/tests/it/quality.rs"),
    ),
    (
        "readme.rs",
        include_str!("../../reference/order/tests/it/readme.rs"),
    ),
    (
        "request_id.rs",
        include_str!("../../reference/order/tests/it/request_id.rs"),
    ),
    (
        "routes.rs",
        include_str!("../../reference/order/tests/it/routes.rs"),
    ),
    (
        "store.rs",
        include_str!("../../reference/order/tests/it/store.rs"),
    ),
];

const FORBIDDEN: [&str; 5] = [
    "\"customer_id\"",
    "\"total_cents\"",
    "\"currency\"",
    "Status::",
    "\"note\"",
];

#[test]
fn reference_tests_name_no_order_field_outside_fixture() {
    let mut offenders = Vec::new();
    for (name, text) in REFERENCE_FILES {
        for word in FORBIDDEN {
            if text.contains(word) {
                offenders.push(format!("{name} contains {word}"));
            }
        }
        for access in [".total_cents", ".note"] {
            if text.contains(access) {
                offenders.push(format!("{name} contains {access}"));
            }
        }
        if text.contains("status=") {
            offenders.push(format!("{name} contains status="));
        }
    }
    assert!(
        offenders.is_empty(),
        "Order field literals outside fixture.rs: {offenders:#?}"
    );
}
