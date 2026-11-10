//! CNL service syntax version 2 (several operations, optional body statement).
//! Expected prompts, DSL and diagnostics are fixtures or literal values.
use serde_json::{Value, json};
use som_service::{Error, assemble, compile_cnl, compile_json, compile_prompt, compile_prompt_v2};

const CNL_V2: &str = include_str!("fixtures/defer-four.cnl");
const PROMPT_V2: &str = include_str!("fixtures/defer-four.prompt.json");
const DSL_V2: &str = include_str!("fixtures/defer-four.dsl.json");
const CNL_V1: &str = include_str!("../contract/task-status.cnl");
const PROMPT_V1: &str = include_str!("../contract/prompt.json");

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

fn span_text<'a>(source: &'a str, span: &som_service::SourceSpan) -> &'a str {
    &source[span.byte_start..span.byte_end]
}

#[test]
fn cnl_v2_golden_equals_prompt_json_v2() {
    let compiled = ok(compile_cnl(CNL_V2));
    assert_eq!(compiled.prompt, value(PROMPT_V2));
    let expected = ok(compile_prompt_v2(&value(PROMPT_V2)));
    assert_eq!(compiled.bundle, expected);
    assert_eq!(compiled.bundle["dsl"], value(DSL_V2));
    assert_eq!(ok(assemble(&compiled.bundle)), ok(assemble(&expected)),);
}

#[test]
fn cnl_v1_still_equals_prompt_json_v1() {
    let compiled = ok(compile_cnl(CNL_V1));
    assert_eq!(compiled.prompt, value(PROMPT_V1));
    assert_eq!(compiled.bundle, ok(compile_prompt(&value(PROMPT_V1))));
}

#[test]
fn compile_json_cnl_format_accepts_a_v2_document() {
    let request = json!({"format": "som.cnl.service.v1", "source": CNL_V2}).to_string();
    let response = ok(compile_json(&request));
    assert_eq!(response["status"], "ok");
    assert_eq!(response["bundle"], ok(compile_prompt_v2(&value(PROMPT_V2))));
}

#[test]
fn v2_source_map_points_at_operation_tokens() {
    let compiled = ok(compile_cnl(CNL_V2));
    let map = &compiled.source_map;

    let alias = &map["/operations/1/alias"];
    assert_eq!(alias.line, 8);
    assert_eq!(span_text(CNL_V2, alias), "create_task");

    let name = &map["/operations/1/inputs/1/name"];
    assert_eq!(name.line, 10);
    assert_eq!(span_text(CNL_V2, name), "task");

    let ty = &map["/operations/1/inputs/1/type"];
    assert_eq!(span_text(CNL_V2, ty), "CreateTask");

    let parameter = &map["/operations/3/inputs/1/parameter"];
    assert_eq!(parameter.line, 16);
    assert_eq!(span_text(CNL_V2, parameter), "task_id");
}

#[test]
fn v2_wrong_body_type_points_at_the_type_token() {
    let source = CNL_V2.replace(
        "Send body task as CreateTask.",
        "Send body task as QueuePolicy.",
    );
    let error = refused(compile_cnl(&source), 400, "INVALID_VALUE");
    let diagnostic = &error.body["diagnostics"][0];
    assert_eq!(diagnostic["prompt_pointer"], "/operations/1/inputs/1/type");
    assert_eq!(diagnostic["line"], 10);
}

#[test]
fn v2_duplicate_alias_is_a_duplicate_error() {
    // Without the v2 lowering this fails earlier: the v2 header is UNSUPPORTED_VERSION.
    let source = CNL_V2.replace(
        "Bind operation put_queue to queue_put.",
        "Bind operation create_task to queue_put.",
    );
    let error = refused(compile_cnl(&source), 400, "CNL_DUPLICATE");
    assert_eq!(error.body["diagnostics"][0]["line"], 11);
}

#[test]
fn v2_input_before_operation_is_a_syntax_error() {
    let source = CNL_V2.replace("Bind operation read_task_status to task_status.\n", "");
    let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    let diagnostic = &error.body["diagnostics"][0];
    assert_eq!(diagnostic["line"], 5);
    assert_eq!(diagnostic["prompt_pointer"], "/operations");
}

#[test]
fn v2_second_body_is_a_duplicate_error() {
    let source = CNL_V2.replace(
        "Send body task as CreateTask.\n",
        "Send body task as CreateTask.\nSend body again as CreateTask.\n",
    );
    refused(compile_cnl(&source), 400, "CNL_DUPLICATE");
}

#[test]
fn v2_without_operations_is_missing() {
    let source: String = CNL_V2
        .lines()
        .take(4)
        .map(|line| format!("{line}\n"))
        .collect();
    refused(compile_cnl(&source), 400, "CNL_MISSING");
}

#[test]
fn v2_unknown_operation_is_unsupported() {
    let source = CNL_V2.replace("to queue_put.", "to nope.");
    let error = refused(compile_cnl(&source), 422, "UNSUPPORTED_OPERATION");
    assert_eq!(error.body["diagnostics"][0]["line"], 11);
}

#[test]
fn v1_document_rejects_a_body_statement() {
    let source = format!("{CNL_V1}Send body x as CreateTask.\n");
    refused(compile_cnl(&source), 400, "CNL_SYNTAX");
}

#[test]
fn v3_header_is_unsupported() {
    let source = CNL_V2.replace("version 2.", "version 3.");
    refused(compile_cnl(&source), 422, "UNSUPPORTED_VERSION");
}
