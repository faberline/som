//! CNL service syntax for one resource (`Service resource version 1.`), lowered
//! to the typed prompt v3. Expected prompts and bundles are the contract files.
use serde_json::{Value, json};
use som_service::{Error, SourceSpan, compile_cnl, compile_json, compile_prompt_v3};

const ORDER_CNL: &str = include_str!("../../contract/order.resource.cnl");
const CUSTOMER_CNL: &str = include_str!("../../contract/customer.resource.cnl");
const ORDER_PROMPT: &str = include_str!("../../contract/order.resource.prompt.json");
const CUSTOMER_PROMPT: &str = include_str!("../../contract/customer.resource.prompt.json");
const TASK_CNL: &str = include_str!("../../contract/task-status.cnl");
const TASK_PROMPT: &str = include_str!("../../contract/prompt.json");

fn value(text: &str) -> Value {
    serde_json::from_str(text).expect("JSON fixture")
}

fn ok<T>(result: Result<T, Error>) -> T {
    match result {
        Ok(value) => value,
        Err(error) => panic!("unexpected error {}: {}", error.status, error.body),
    }
}

fn refused<T>(result: Result<T, Error>, status: u16, code: &str) -> Error {
    let error = match result {
        Ok(_) => panic!("expected {code}, but request was accepted"),
        Err(error) => error,
    };
    assert_eq!(error.status, status, "{}", error.body);
    assert_eq!(error.body["code"], code, "{}", error.body);
    error
}

fn span_text<'a>(source: &'a str, span: &SourceSpan) -> &'a str {
    &source[span.byte_start..span.byte_end]
}

/// Asserts the first diagnostic sits on `line` and selects exactly `text`.
fn at(error: &Error, line: u64, text: &str, source: &str) {
    let diagnostic = &error.body["diagnostics"][0];
    assert_eq!(diagnostic["line"], line, "{}", error.body);
    let start = diagnostic["byte_start"].as_u64().expect("byte_start") as usize;
    let end = diagnostic["byte_end"].as_u64().expect("byte_end") as usize;
    assert_eq!(&source[start..end], text, "{}", error.body);
}

/// The Order source with one replacement; the replacement must change it.
fn bad(replace_from: &str, to: &str) -> String {
    let changed = ORDER_CNL.replace(replace_from, to);
    assert_ne!(changed, ORDER_CNL, "the source did not change");
    changed
}

#[test]
fn cnl_and_typed_json_give_equal_prompts_and_bundles() {
    for (cnl, prompt) in [(ORDER_CNL, ORDER_PROMPT), (CUSTOMER_CNL, CUSTOMER_PROMPT)] {
        let compiled = ok(compile_cnl(cnl));
        assert_eq!(compiled.prompt, value(prompt));
        let bundle = ok(compile_prompt_v3(&value(prompt)));
        assert_eq!(compiled.bundle, bundle);
        assert_eq!(compiled.bundle["catalog"], "som.std.v1");
    }
}

#[test]
fn crlf_and_blank_lines_give_the_same_prompt() {
    let source = ORDER_CNL.replace('\n', "\r\n").replace(
        "Target rust-resource-service.v1.\r\n",
        "Target rust-resource-service.v1.\r\n\r\n",
    );
    assert_ne!(source, ORDER_CNL);
    assert_eq!(ok(compile_cnl(&source)).prompt, value(ORDER_PROMPT));
}

#[test]
fn compile_json_cnl_format_accepts_a_resource_request() {
    let request = json!({"format": "som.cnl.service.v1", "source": ORDER_CNL}).to_string();
    let response = ok(compile_json(&request));
    assert_eq!(response["status"], "ok");
    assert_eq!(response["bundle"]["catalog"], "som.std.v1");
}

#[test]
fn defer_cnl_still_compiles_to_the_frozen_prompt() {
    assert_eq!(ok(compile_cnl(TASK_CNL)).prompt, value(TASK_PROMPT));
}

#[test]
fn source_map_points_at_the_field_sentence_tokens() {
    let map = ok(compile_cnl(ORDER_CNL)).source_map;
    let span = |pointer: &str| {
        map.get(pointer)
            .unwrap_or_else(|| panic!("no span for {pointer}"))
    };

    let field_type = span("/resources/0/fields/2/type");
    assert_eq!(field_type.line, 7);
    assert_eq!(span_text(ORDER_CNL, field_type), "string");

    let standard = span("/resources/0/standards/1");
    assert_eq!(standard.line, 11);
    assert_eq!(span_text(ORDER_CNL, standard), "standard pagination");

    assert_eq!(
        span_text(ORDER_CNL, span("/resources/0/fields/0/min")),
        "min 1"
    );
    assert_eq!(
        span_text(ORDER_CNL, span("/resources/0/fields/3/values")),
        "values pending paid shipped canceled"
    );
    assert_eq!(
        span_text(ORDER_CNL, span("/resources/0")),
        "Resource Order."
    );
}

#[test]
fn unknown_standard_points_at_its_words() {
    let source = bad(
        "Apply standard pagination to Order.",
        "Apply standard sorting to Order.",
    );
    let error = refused(compile_cnl(&source), 422, "UNKNOWN_STANDARD");
    at(&error, 11, "standard sorting", &source);
}

#[test]
fn soft_delete_is_not_in_the_catalog() {
    let source = bad("Apply CRUD to Order.", "Apply soft delete to Order.");
    let error = refused(compile_cnl(&source), 422, "UNKNOWN_STANDARD");
    at(&error, 10, "soft delete", &source);
    assert!(
        error.body["detail"]
            .as_str()
            .unwrap_or("")
            .contains("not in som.std.v1"),
        "{}",
        error.body
    );
}

#[test]
fn missing_pagination_standard_points_at_the_resource() {
    let source = ORDER_CNL.replace("Apply standard pagination to Order.\n", "");
    let error = refused(compile_cnl(&source), 400, "MISSING_STANDARD");
    at(&error, 4, "Resource Order.", &source);
}

#[test]
fn resource_without_fields_points_at_the_resource() {
    let source: String = ORDER_CNL
        .lines()
        .filter(|line| !line.starts_with("Field "))
        .map(|line| format!("{line}\n"))
        .collect();
    let error = refused(compile_cnl(&source), 400, "MISSING_FACT");
    at(&error, 4, "Resource Order.", &source);
}

#[test]
fn reserved_field_name_points_at_the_name() {
    let source = bad(
        "Field customer_id of Order is string required min 1.",
        "Field id of Order is string required.",
    );
    let error = refused(compile_cnl(&source), 400, "INVALID_VALUE");
    at(&error, 5, "id", &source);
}

#[test]
fn optional_field_with_max_but_no_requirement_is_a_syntax_error() {
    let source = bad(
        "Field note of Order is string optional max 500.",
        "Field note of Order is string max 500.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 9, "max", &source);
}

#[test]
fn field_without_required_or_optional_names_the_wrong_word() {
    let source = bad(
        "Field note of Order is string optional max 500.",
        "Field x of Order string.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 9, "string", &source);
    assert_eq!(
        error.body["diagnostics"][0]["allowed_form"],
        "Field NAME of RESOURCE is TYPE required|optional [CONSTRAINTS]."
    );
}

#[test]
fn unsupported_field_type_is_invalid_value() {
    let source = bad(
        "Field note of Order is string optional max 500.",
        "Field x of Order is float required.",
    );
    let error = refused(compile_cnl(&source), 400, "INVALID_VALUE");
    at(&error, 9, "float", &source);
}

#[test]
fn sentence_without_final_period_is_a_syntax_error() {
    let source = bad("Package orders_service.", "Package orders_service");
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 2, "Package orders_service", &source);
    assert_eq!(
        error.body["diagnostics"][0]["allowed_form"],
        "Package NAME."
    );
}

#[test]
fn field_of_another_resource_is_a_syntax_error() {
    let source = bad(
        "Apply CRUD to Order.",
        "Field x of Invoice is string required.\nApply CRUD to Order.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 10, "Invoice", &source);
}

#[test]
fn other_service_version_is_unsupported() {
    let source = bad("Service resource version 1.", "Service resource version 2.");
    let error = refused(compile_cnl(&source), 422, "UNSUPPORTED_VERSION");
    at(&error, 1, "2", &source);
}

#[test]
fn resource_line_without_field_before_it_is_a_syntax_error() {
    let source = ORDER_CNL.replace("Resource Order.\n", "");
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 4, "Field", &source);
}

#[test]
fn resource_line_with_extra_word_is_a_syntax_error() {
    let source = bad("Resource Order.", "Resource Order extra.");
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 4, "extra", &source);
}

#[test]
fn unknown_sentence_start_is_a_syntax_error() {
    let source = bad("Resource Order.", "Rename Order.");
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 4, "Rename", &source);
}

#[test]
fn constraint_out_of_order_is_a_syntax_error() {
    let source = bad(
        "Field customer_id of Order is string required min 1.",
        "Field customer_id of Order is string required max 5 min 1.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 5, "min", &source);
}

#[test]
fn enum_with_max_is_a_syntax_error() {
    let source = bad(
        "Field status of Order is enum required values pending paid shipped canceled.",
        "Field status of Order is enum required max 3.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 8, "max", &source);
}

#[test]
fn constraint_number_must_be_a_number() {
    let source = bad(
        "Field total_cents of Order is integer required min 0.",
        "Field total_cents of Order is integer required min one.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 6, "one", &source);
}

#[test]
fn negative_minimum_on_string_is_a_syntax_error() {
    let source = bad(
        "Field customer_id of Order is string required min 1.",
        "Field customer_id of Order is string required min -1.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 5, "-1", &source);
}

#[test]
fn negative_minimum_on_integer_is_accepted() {
    let source = bad(
        "Field total_cents of Order is integer required min 0.",
        "Field total_cents of Order is integer required min -1.",
    );
    ok(compile_cnl(&source));
}

#[test]
fn upper_letters_cannot_combine_with_min() {
    let source = bad(
        "Field currency of Order is string required upper_letters 3.",
        "Field currency of Order is string required min 1 upper_letters 3.",
    );
    let error = refused(compile_cnl(&source), 400, "INVALID_VALUE");
    at(&error, 7, "upper_letters 3", &source);
}

#[test]
fn enum_with_one_value_points_at_the_values() {
    let source = bad(
        "Field status of Order is enum required values pending paid shipped canceled.",
        "Field status of Order is enum required values pending.",
    );
    let error = refused(compile_cnl(&source), 400, "INVALID_VALUE");
    at(&error, 8, "values pending", &source);
}

#[test]
fn enum_without_values_points_at_the_field() {
    let source = bad(
        "Field status of Order is enum required values pending paid shipped canceled.",
        "Field status of Order is enum required.",
    );
    let error = refused(compile_cnl(&source), 400, "MISSING_FACT");
    at(
        &error,
        8,
        "Field status of Order is enum required.",
        &source,
    );
}

#[test]
fn every_field_missing_is_a_missing_fact_at_the_resource() {
    let source: String = ORDER_CNL
        .lines()
        .filter(|line| !line.starts_with("Field "))
        .map(|line| format!("{line}\n"))
        .collect();
    refused(compile_cnl(&source), 400, "MISSING_FACT");
}

#[test]
fn second_resource_with_the_same_name_is_refused_after_the_first_is_complete() {
    let source = format!(
        "{ORDER_CNL}Resource Order.\nField a of Order is string required.\nApply CRUD to Order.\nApply standard pagination to Order.\n"
    );
    let error = refused(compile_cnl(&source), 400, "INVALID_VALUE");
    at(&error, 12, "Order", &source);
}

#[test]
fn spaces_and_tabs_separate_words_in_any_run() {
    let source = bad("Package orders_service.", "Package \t  orders_service.");
    assert_eq!(ok(compile_cnl(&source)).prompt, value(ORDER_PROMPT));
}

#[test]
fn a_period_after_a_space_ends_the_sentence() {
    let source = bad("Resource Order.", "Resource Order .");
    let compiled = ok(compile_cnl(&source));
    assert_eq!(compiled.prompt["resources"][0]["name"], "Order");
}

#[test]
fn a_line_with_only_a_period_is_an_unknown_sentence() {
    let source = bad("Resource Order.", ".\nResource Order.");
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 4, ".", &source);
}

#[test]
fn package_takes_exactly_one_name() {
    let missing = bad("Package orders_service.", "Package.");
    let error = refused(compile_cnl(&missing), 400, "CNL_SYNTAX");
    at(&error, 2, "Package.", &missing);

    let extra = bad("Package orders_service.", "Package a b.");
    let error = refused(compile_cnl(&extra), 400, "CNL_SYNTAX");
    at(&error, 2, "b", &extra);
}

#[test]
fn target_takes_one_profile_and_needs_its_period() {
    let extra = bad(
        "Target rust-resource-service.v1.",
        "Target rust-resource-service.v1 extra.",
    );
    let error = refused(compile_cnl(&extra), 400, "CNL_SYNTAX");
    at(&error, 3, "extra", &extra);

    let unended = bad(
        "Target rust-resource-service.v1.",
        "Target rust-resource-service.v1",
    );
    let error = refused(compile_cnl(&unended), 400, "CNL_SYNTAX");
    at(&error, 3, "Target rust-resource-service.v1", &unended);
    assert_eq!(
        error.body["diagnostics"][0]["allowed_form"],
        "Target PROFILE."
    );
}

#[test]
fn target_before_package_is_refused() {
    let source = bad(
        "Package orders_service.\nTarget rust-resource-service.v1.",
        "Target rust-resource-service.v1.\nPackage orders_service.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 2, "Target", &source);
    assert_eq!(
        error.body["diagnostics"][0]["allowed_form"],
        "Package NAME."
    );
}

#[test]
fn resource_line_needs_its_period() {
    let source = bad("Resource Order.", "Resource Order");
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 4, "Resource Order", &source);
    assert_eq!(
        error.body["diagnostics"][0]["allowed_form"],
        "Resource NAME."
    );
}

#[test]
fn unknown_start_without_period_names_the_header_form() {
    let source = bad("Resource Order.", "Rename Order");
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 4, "Rename Order", &source);
    assert_eq!(
        error.body["diagnostics"][0]["allowed_form"],
        "Service resource version 1."
    );
}

#[test]
fn field_and_apply_lines_need_their_period() {
    let field = bad(
        "Field note of Order is string optional max 500.",
        "Field note of Order is string optional max 500",
    );
    let error = refused(compile_cnl(&field), 400, "CNL_SYNTAX");
    at(
        &error,
        9,
        "Field note of Order is string optional max 500",
        &field,
    );
    assert_eq!(
        error.body["diagnostics"][0]["allowed_form"],
        "Field NAME of RESOURCE is TYPE required|optional [CONSTRAINTS]."
    );

    let apply = bad("Apply CRUD to Order.", "Apply CRUD to Order");
    let error = refused(compile_cnl(&apply), 400, "CNL_SYNTAX");
    at(&error, 10, "Apply CRUD to Order", &apply);
    assert_eq!(
        error.body["diagnostics"][0]["allowed_form"],
        "Apply STANDARD to RESOURCE."
    );
}

#[test]
fn header_words_are_fixed_and_complete() {
    let wrong = bad("Service resource version 1.", "Service Resource version 1.");
    let error = refused(compile_cnl(&wrong), 400, "CNL_SYNTAX");
    at(&error, 1, "Resource", &wrong);

    let short = bad("Service resource version 1.", "Service resource.");
    let error = refused(compile_cnl(&short), 400, "CNL_SYNTAX");
    at(&error, 1, "Service resource.", &short);

    let extra = bad(
        "Service resource version 1.",
        "Service resource version 1 extra.",
    );
    let error = refused(compile_cnl(&extra), 400, "CNL_SYNTAX");
    at(&error, 1, "extra", &extra);
}

#[test]
fn resource_line_without_a_name_is_a_syntax_error() {
    let source = bad("Resource Order.", "Resource.");
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 4, "Resource.", &source);
}

#[test]
fn field_sentence_must_have_all_its_fixed_words() {
    let no_name = bad("Field note of Order is string optional max 500.", "Field.");
    let error = refused(compile_cnl(&no_name), 400, "CNL_SYNTAX");
    at(&error, 9, "Field.", &no_name);

    let no_of = bad(
        "Field note of Order is string optional max 500.",
        "Field note Order is string optional max 500.",
    );
    let error = refused(compile_cnl(&no_of), 400, "CNL_SYNTAX");
    at(&error, 9, "Order", &no_of);

    let no_type = bad(
        "Field note of Order is string optional max 500.",
        "Field x of Order is.",
    );
    let error = refused(compile_cnl(&no_type), 400, "CNL_SYNTAX");
    at(&error, 9, "Field x of Order is.", &no_type);

    let no_flag = bad(
        "Field note of Order is string optional max 500.",
        "Field x of Order is string.",
    );
    let error = refused(compile_cnl(&no_flag), 400, "CNL_SYNTAX");
    at(&error, 9, "Field x of Order is string.", &no_flag);
}

#[test]
fn constraint_needs_a_number_and_a_whole_one() {
    let no_number = bad(
        "Field note of Order is string optional max 500.",
        "Field note of Order is string optional max.",
    );
    let error = refused(compile_cnl(&no_number), 400, "CNL_SYNTAX");
    at(&error, 9, "max", &no_number);

    let decimal = bad(
        "Field note of Order is string optional max 500.",
        "Field note of Order is string optional max 1.5.",
    );
    let error = refused(compile_cnl(&decimal), 400, "CNL_SYNTAX");
    at(&error, 9, "1.5", &decimal);

    let negative_zero = bad(
        "Field note of Order is string optional max 500.",
        "Field note of Order is string optional max -0.",
    );
    let error = refused(compile_cnl(&negative_zero), 400, "CNL_SYNTAX");
    at(&error, 9, "-0", &negative_zero);

    let too_large = bad(
        "Field note of Order is string optional max 500.",
        "Field note of Order is string optional max 99999999999999999999.",
    );
    let error = refused(compile_cnl(&too_large), 400, "CNL_SYNTAX");
    at(&error, 9, "99999999999999999999", &too_large);
}

#[test]
fn constraint_repeated_in_the_same_sentence_is_refused() {
    let source = bad(
        "Field note of Order is string optional max 500.",
        "Field note of Order is string optional max 5 max 6.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 9, "max", &source);
}

#[test]
fn enum_values_need_at_least_one_word() {
    let source = bad(
        "Field status of Order is enum required values pending paid shipped canceled.",
        "Field status of Order is enum required values.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    at(&error, 8, "values", &source);
}

#[test]
fn apply_needs_a_standard_then_to_then_the_open_resource() {
    let no_standard = bad("Apply CRUD to Order.", "Apply to Order.");
    let error = refused(compile_cnl(&no_standard), 400, "CNL_SYNTAX");
    at(&error, 10, "Apply to Order.", &no_standard);

    let no_to = bad("Apply CRUD to Order.", "Apply CRUD Order.");
    let error = refused(compile_cnl(&no_to), 400, "CNL_SYNTAX");
    at(&error, 10, "Apply CRUD Order.", &no_to);

    let other = bad("Apply CRUD to Order.", "Apply CRUD to Invoice.");
    let error = refused(compile_cnl(&other), 400, "CNL_SYNTAX");
    at(&error, 10, "Invoice", &other);
}
