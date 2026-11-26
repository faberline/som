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
        let mut expected = fn_names(reference);
        if file == "quality.rs" {
            expected.remove("core_is_pinned_to_a_tag_and_cargo_lock_is_present");
        }
        assert_eq!(
            fn_names(emitted),
            expected,
            "the functions of tests/it/{file} differ from the reference"
        );
    }
    assert!(fn_names(emitted_file(&files, "tests/it/fixture.rs")).contains("must"));
    let quality = emitted_file(&files, "tests/it/quality.rs");
    assert!(!quality.contains("core_is_pinned_to_a_tag_and_cargo_lock_is_present"));
    assert!(quality.contains("fmt_clippy_clean_no_unwrap_no_forbidden_code"));
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
            "readme::readme_has_run_steps_and_one_curl_per_operation",
            "readme::readme_table_has_37_rows_and_each_row_names_an_existing_test",
            "readme::readme_rows_equal_the_catalog",
            "readme::readme_how_to_change_has_the_five_changes",
            "readme::core_is_pinned_to_a_tag_and_cargo_lock_is_present",
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
            "readme::readme_has_run_steps_and_one_curl_per_operation",
            "readme::readme_table_has_37_rows_and_each_row_names_an_existing_test",
            "readme::readme_rows_equal_the_catalog",
            "readme::readme_how_to_change_has_the_five_changes",
            "readme::core_is_pinned_to_a_tag_and_cargo_lock_is_present",
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

/// The lines of a text whose first cell is a decision number (a row of the decisions table).
fn decision_row_count(text: &str) -> usize {
    text.lines()
        .filter(|line| {
            line.starts_with("| ")
                && line
                    .trim_matches('|')
                    .split('|')
                    .next()
                    .is_some_and(|cell| cell.trim().parse::<u32>().is_ok())
        })
        .count()
}

/// The lines that start with `curl` after trim (one per operation in the README).
fn curl_lines(text: &str) -> Vec<&str> {
    text.lines()
        .map(str::trim)
        .filter(|line| line.starts_with("curl "))
        .collect()
}

#[test]
fn emitted_readme_has_the_four_sections_and_names_of_the_resource() {
    let files = emit(&value(CUSTOMER));
    let readme = emitted_file(&files, "README.md");
    for heading in ["## Run", "## Examples", "## Decisions", "## How to change"] {
        assert!(
            readme.lines().any(|line| line == heading),
            "README has no `{heading}` heading"
        );
    }
    for word in [
        "/v1/customers",
        "CustomerStore",
        "src/domain/customer.rs",
        "CUSTOMER_ADMISSION_READ_CAPACITY",
        "-p customers_service",
        "/v1/customers/{id}/cancel",
    ] {
        assert!(readme.contains(word), "README lacks `{word}`");
    }
    for word in [
        "Order",
        "/v1/orders",
        "som-reference-order",
        "som_reference_order",
        "ORDER_",
    ] {
        assert!(!readme.contains(word), "README holds `{word}`");
    }
    assert_eq!(decision_row_count(readme), 37, "the decisions table");
    let curls = curl_lines(readme);
    assert_eq!(curls.len(), 5, "one curl line per operation: {curls:#?}");
    let create = curls
        .iter()
        .find(|line| line.starts_with("curl -X POST"))
        .copied()
        .unwrap_or_default();
    assert!(create.contains("\"tier\":\"standard\""), "{create}");
    let replace = curls
        .iter()
        .find(|line| line.starts_with("curl -X PUT"))
        .copied()
        .unwrap_or_default();
    assert!(replace.contains("\"tier\":\"silver\""), "{replace}");
    assert!(
        readme.contains("sets `tier` to `gold` and `updated_at`"),
        "the cancel change names the last value of the enum"
    );
}

#[test]
fn emitted_readme_keeps_the_reference_text_for_order() {
    let prompt = value(ORDER);
    let package = prompt["package"].as_str().unwrap_or_default();
    let files = emit(&prompt);
    let readme = emitted_file(&files, "README.md");
    for word in [
        "Sorting: `created_at` newest first",
        "The order is fixed.",
        "sets `status` to `canceled` and `updated_at`",
        "ORDER_ADMISSION_READ_CAPACITY",
        &format!("-p {package}"),
    ] {
        assert!(readme.contains(word), "README lacks `{word}`");
    }
    assert_eq!(decision_row_count(readme), 37, "the decisions table");
    assert_eq!(curl_lines(readme).len(), 5, "one curl line per operation");
}

#[test]
fn emitted_readme_cancel_names_no_status_without_an_enum_field() {
    let files = emit(&resource_prompt(
        "order_items_service",
        "OrderItem",
        title_fields(),
    ));
    let readme = emitted_file(&files, "README.md");
    assert!(
        readme
            .lines()
            .any(|line| line == "### Add POST /v1/order-items/{id}/cancel"),
        "the cancel heading names the path of the plan"
    );
    assert!(
        readme.contains("sets `updated_at`"),
        "the cancel change sets updated_at"
    );
    assert!(
        !readme.contains("canceled"),
        "a plan with no enum names no status"
    );
    assert!(readme.contains("/v1/order-items"));
}

#[test]
fn emitted_readme_test_has_a_constant_catalog_and_no_standards_path() {
    let files = emit(&value(CUSTOMER));
    let test = emitted_file(&files, "tests/it/readme.rs");
    for text in [
        "const CATALOG: [(u32, &str, &str); 37]",
        "fn core_is_pinned_to_a_tag_and_cargo_lock_is_present(",
        "fn readme_rows_equal_the_catalog(",
    ] {
        assert!(test.contains(text), "tests/it/readme.rs lacks `{text}`");
    }
    for text in [
        "../../../standards",
        "fn decision_rows(",
        "Order",
        "customer_id",
    ] {
        assert!(!test.contains(text), "tests/it/readme.rs holds `{text}`");
    }
    let tuples = test
        .lines()
        .filter(|line| line.starts_with("    (") && line.ends_with("),"))
        .count();
    assert_eq!(tuples, 37, "the catalog holds 37 tuples");
    assert!(emitted_file(&files, "tests/it/main.rs").contains("mod readme;\n"));
}
