//! The emitted test files (#326). The reference test files of `reference/order`
//! are emitted with the names of the resource, and the fixture is built from the
//! plan. These checks read the emitted text, build the Order and Customer crates
//! and run the tests of each.
use crate::resource_emit::{
    CUSTOMER, ORDER, cargo, emit, resource_prompt, scratch_root, target_dir, title_fields, value,
    write_files,
};
use std::collections::{BTreeMap, BTreeSet};

/// The reference test files that every emitted crate holds, with their text.
const REFERENCE_TESTS: [(&str, &str); 16] = [
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
        "fixture.rs",
        include_str!("../../reference/order/tests/it/fixture.rs"),
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

/// The text of one emitted file. A missing file fails the test with its path.
fn emitted_file<'a>(files: &'a BTreeMap<String, String>, path: &str) -> &'a str {
    match files.get(path) {
        Some(text) => text,
        None => panic!("the emitted crate has no {path}"),
    }
}

/// The names of the functions in a Rust text: each `fn name(` or `fn name<`.
fn fn_names(text: &str) -> BTreeSet<String> {
    text.match_indices("fn ")
        .filter_map(|(at, _)| {
            let rest = &text[at + 3..];
            let name: String = rest
                .chars()
                .take_while(|c| c.is_alphanumeric() || *c == '_')
                .collect();
            let next = rest[name.len()..].chars().next();
            (!name.is_empty() && matches!(next, Some('(' | '<'))).then_some(name)
        })
        .collect()
}

/// Checks that a run of `cargo test` passed and showed each named test as ok.
fn check_crate_tests(stdout: &str, names: &[&str]) {
    assert!(!stdout.contains("FAILED"), "{stdout}");
    assert!(stdout.contains("0 failed"), "{stdout}");
    for name in names {
        let line = format!("test {name} ... ok");
        assert!(
            stdout.lines().any(|found| found == line),
            "missing `{line}` in:\n{stdout}"
        );
    }
}

#[test]
fn emitted_tests_for_order_have_every_reference_test_name() {
    let files = emit(&value(ORDER));
    for (file, reference) in REFERENCE_TESTS {
        let emitted = emitted_file(&files, &format!("tests/it/{file}"));
        assert_eq!(
            fn_names(emitted),
            fn_names(reference),
            "the functions of tests/it/{file} differ from the reference"
        );
    }
    assert!(fn_names(emitted_file(&files, "tests/it/fixture.rs")).contains("must"));
}

#[test]
fn emitted_order_crate_passes_its_own_tests() {
    let root = scratch_root();
    let order = root.join("order");
    write_files(&order, &emit(&value(ORDER)));
    let target = target_dir(&root);
    cargo(
        &order,
        &target,
        &[
            "clippy",
            "--locked",
            "--offline",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
    );
    let output = cargo(&order, &target, &["test", "--locked", "--offline"]);
    check_crate_tests(
        &String::from_utf8_lossy(&output.stdout),
        &[
            "routes::create_returns_201_location_and_full_resource",
            "layers::four_layers_are_present",
            "quality::each_operation_has_success_and_error_tests_and_pagination_property_test",
            "quality::fmt_clippy_clean_no_unwrap_no_forbidden_code",
        ],
    );
}

#[test]
fn emitted_customer_crate_passes_its_own_tests() {
    let root = scratch_root();
    let customer = root.join("customer");
    write_files(&customer, &emit(&value(CUSTOMER)));
    let target = target_dir(&root);
    cargo(
        &customer,
        &target,
        &[
            "clippy",
            "--locked",
            "--offline",
            "--all-targets",
            "--",
            "-D",
            "warnings",
        ],
    );
    let output = cargo(&customer, &target, &["test", "--locked", "--offline"]);
    check_crate_tests(
        &String::from_utf8_lossy(&output.stdout),
        &[
            "routes::create_returns_201_location_and_full_resource",
            "layers::four_layers_are_present",
            "quality::each_operation_has_success_and_error_tests_and_pagination_property_test",
            "quality::fmt_clippy_clean_no_unwrap_no_forbidden_code",
            "domain::unknown_field_is_refused_and_bad_fields_give_422_with_one_entry_each",
        ],
    );
}

#[test]
fn emitted_tests_have_no_order_words_for_customer() {
    let files = emit(&value(CUSTOMER));
    let order_words = [
        "customer_id",
        "total_cents",
        "OrderStore",
        "Customering",
        "som_reference_order",
    ];
    for (path, text) in &files {
        if path.starts_with("tests/it/") {
            for word in order_words {
                assert!(!text.contains(word), "{path} contains {word}");
            }
        }
    }
    assert!(emitted_file(&files, "tests/it/common.rs").contains("Ordering"));
}

#[test]
fn emitted_tests_keep_reordered_and_the_path() {
    let files = emit(&value(CUSTOMER));
    assert!(emitted_file(&files, "tests/it/idempotency.rs").contains("reordered_valid_json"));
    assert!(emitted_file(&files, "tests/it/fixture.rs").contains("fn reordered_valid_json("));

    let files = emit(&resource_prompt(
        "order_items_service",
        "OrderItem",
        title_fields(),
    ));
    let routes = emitted_file(&files, "tests/it/routes.rs");
    assert!(routes.contains("/v1/order-items"));
    assert!(!routes.contains("/v1/order_items"));
}

#[test]
fn emitted_tests_hold_no_banned_text() {
    let banned = [
        concat!(".exp", "ect("),
        concat!(".unw", "rap()"),
        concat!("uns", "afe"),
    ];
    for prompt in [ORDER, CUSTOMER] {
        let files = emit(&value(prompt));
        for (path, text) in &files {
            if path.ends_with(".rs") {
                for word in banned {
                    assert!(
                        !text.contains(word),
                        "{path} holds `{word}`; the text of tests/it must not hold it"
                    );
                }
            }
        }
    }
}
