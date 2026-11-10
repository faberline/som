//! Independent compiler acceptance for the frozen, GET-only service contract.
//! Expected prompts, DSL and diagnostic prose are authored fixtures, not captures.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use som_service::{
    Error, assemble, assemble_json, compile_cnl, compile_json, compile_prompt, compile_prompt_v2,
    health,
};
use std::collections::BTreeSet;

const CNL: &str = include_str!("../contract/task-status.cnl");
const PROMPT: &str = include_str!("../contract/prompt.json");
const DSL: &str = include_str!("../contract/dsl.json");
const PROMPT_V2: &str = include_str!("fixtures/defer-four.prompt.json");
const DSL_V2: &str = include_str!("fixtures/defer-four.dsl.json");

fn value(text: &str) -> Value {
    serde_json::from_str(text).expect("independent JSON fixture")
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

fn pointer(error: &Error, expected: &str) {
    assert_eq!(
        error.body["diagnostics"][0]["prompt_pointer"], expected,
        "{}",
        error.body
    );
}

fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn rehash(bundle: &mut Value) {
    // The contract hashes these four members. Values contain no float literals.
    let payload = json!({
        "version": bundle["version"], "assets": bundle["assets"],
        "prompt": bundle["prompt"], "dsl": bundle["dsl"],
    });
    bundle["digest"] = json!(sha(&serde_json::to_vec(&payload).unwrap()));
}

fn line_span(source: &str, line_number: usize, token: &str) -> Value {
    line_span_at(source, line_number, token, false)
}

fn line_span_at(source: &str, line_number: usize, token: &str, last: bool) -> Value {
    let mut offset = 0;
    for (index, line) in source.split_inclusive('\n').enumerate() {
        if index + 1 == line_number {
            let start = if last {
                line.rfind(token)
            } else {
                line.find(token)
            }
            .expect("token exists on independently selected line");
            return json!({
                "line": line_number, "column": line[..start].chars().count() + 1,
                "end_line": line_number,
                "end_column": line[..start + token.len()].chars().count() + 1,
                "byte_start": offset + start, "byte_end": offset + start + token.len(),
            });
        }
        offset += line.len();
    }
    panic!("fixture line {line_number} does not exist")
}

fn assert_span(actual: &Value, expected: &Value) {
    for key in [
        "line",
        "column",
        "end_line",
        "end_column",
        "byte_start",
        "byte_end",
    ] {
        assert_eq!(actual[key], expected[key], "span {key}: {actual}");
    }
}

fn assert_map_token(source: &str, path: &str, line: usize, token: &str) {
    let parsed = ok(compile_cnl(source));
    let span = parsed
        .source_map
        .get(path)
        .unwrap_or_else(|| panic!("missing SourceMap key {path}"));
    let actual = json!({
        "line": span.line, "column": span.column,
        "end_line": span.end_line, "end_column": span.end_column,
        "byte_start": span.byte_start, "byte_end": span.byte_end,
    });
    assert_span(
        &actual,
        &line_span_at(source, line, token, path.ends_with("/parameter")),
    );
    assert_eq!(&source[span.byte_start..span.byte_end], token);
}

#[test]
fn frozen_contract_files_keep_their_independent_hashes() {
    let freeze = value(include_str!("../contract/freeze.json"));
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    for (path, expected) in freeze["files_sha256"].as_object().unwrap() {
        assert_eq!(
            sha(&std::fs::read(root.join(path)).unwrap()),
            expected.as_str().unwrap(),
            "{path}"
        );
    }
}

#[test]
fn cnl_and_typed_input_match_frozen_prompt_dsl_and_generated_files() {
    let parsed = ok(compile_cnl(CNL));
    let expected_prompt = value(PROMPT);
    let typed = ok(compile_prompt(&expected_prompt));
    assert_eq!(parsed.prompt, expected_prompt);
    assert_eq!(parsed.bundle, typed);
    assert_eq!(typed["version"], "som.bundle.service.v1");
    assert_eq!(typed["prompt"], expected_prompt);
    assert_eq!(typed["dsl"], value(DSL));
    assert_eq!(typed["assets"]["family"], "som.service-compiler.v1");
    let from_cnl = ok(assemble(&parsed.bundle));
    assert_eq!(from_cnl, ok(assemble(&typed)));
    assert!(from_cnl.contains_key("Cargo.toml"));
    assert!(from_cnl.contains_key("src/lib.rs"));
    assert_eq!(parsed.bundle, ok(compile_cnl(CNL)).bundle);
    let mut independently_hashed = typed.clone();
    rehash(&mut independently_hashed);
    assert_eq!(typed["digest"], independently_hashed["digest"]);
}

#[test]
fn aliases_are_preserved_but_dsl_arguments_follow_catalog_order() {
    let source = include_str!("fixtures/renamed.cnl");
    let parsed = ok(compile_cnl(source));
    let expected = value(include_str!("fixtures/renamed.prompt.json"));
    assert_eq!(parsed.prompt, expected);
    assert_eq!(parsed.bundle, ok(compile_prompt(&expected)));
    assert_eq!(
        parsed.bundle["dsl"],
        value(include_str!("fixtures/renamed.dsl.json"))
    );
    assert_map_token(source, "/operation/inputs/0/name", 6, "job_id");
    assert_map_token(source, "/operation/inputs/1/name", 7, "queue_name");
    let mut reordered = expected;
    reordered["operation"]["inputs"]
        .as_array_mut()
        .unwrap()
        .reverse();
    let reordered = ok(compile_prompt(&reordered));
    assert_eq!(reordered["dsl"], parsed.bundle["dsl"]);
    assert_eq!(ok(assemble(&reordered)), ok(assemble(&parsed.bundle)));
}

#[test]
fn source_map_uses_raw_half_open_token_ranges() {
    for (path, line, token) in [
        ("/version", 1, "1"),
        ("/package", 2, "defer_status_client"),
        ("/target", 3, "rust-service-client.v1"),
        ("/service/name", 4, "defer"),
        (
            "/service/contract",
            4,
            "\"defer.http@5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0\"",
        ),
        ("/operation/alias", 5, "read_task_status"),
        ("/operation/name", 5, "task_status"),
        ("/operation/inputs/0/name", 6, "queue"),
        ("/operation/inputs/0/type", 6, "string"),
        ("/operation/inputs/1/name", 7, "task_id"),
        ("/operation/inputs/1/type", 7, "string"),
    ] {
        // task_status also occurs inside the alias. Select the true operation token.
        if path == "/operation/name" {
            let source = CNL.replace("read_task_status", "fetch_status");
            assert_map_token(&source, path, line, token);
        } else {
            assert_map_token(CNL, path, line, token);
        }
    }
    let renamed = include_str!("fixtures/renamed.cnl");
    assert_map_token(renamed, "/operation/inputs/0/parameter", 6, "task_id");
    assert_map_token(renamed, "/operation/inputs/1/parameter", 7, "queue");
}

#[test]
fn crlf_tabs_and_blank_lines_keep_values_and_adjust_source_positions() {
    let source = format!(
        "\r\n{}",
        CNL.lines()
            .map(|line| format!("\t{line}\t\r\n"))
            .collect::<String>()
    );
    assert_eq!(ok(compile_cnl(&source)).bundle, ok(compile_cnl(CNL)).bundle);
    assert_map_token(&source, "/package", 3, "defer_status_client");
    assert_map_token(&source, "/operation/inputs/1/type", 8, "string");
    let tabs = CNL.replace(
        "Accept queue as string for queue.",
        "Accept\tqueue\tas\tstring\tfor\tqueue.",
    );
    assert_eq!(ok(compile_cnl(&tabs)).bundle, ok(compile_cnl(CNL)).bundle);
    assert_map_token(&tabs, "/operation/inputs/0/type", 6, "string");
}

#[test]
fn json_escape_is_decoded_once_but_source_map_keeps_the_raw_token() {
    let source = CNL.replace("\"defer.http@", "\"\\u0064efer.http@");
    assert_eq!(ok(compile_cnl(&source)).bundle, ok(compile_cnl(CNL)).bundle);
    assert_map_token(
        &source,
        "/service/contract",
        4,
        "\"\\u0064efer.http@5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0\"",
    );
}

#[test]
fn semantic_error_goldens_include_exact_text_pointer_and_unicode_span() {
    for case in value(include_str!("fixtures/compiler-errors.json"))
        .as_array()
        .unwrap()
    {
        let change = case["replace"].as_array().unwrap();
        let source = CNL.replacen(change[0].as_str().unwrap(), change[1].as_str().unwrap(), 1);
        let error = refused(
            compile_cnl(&source),
            case["status"].as_u64().unwrap() as u16,
            case["code"].as_str().unwrap(),
        );
        assert_eq!(error.body["detail"], case["detail"], "{}", case["id"]);
        let diagnostics = error.body["diagnostics"].as_array().unwrap();
        assert_eq!(diagnostics.len(), 1);
        let diagnostic = &diagnostics[0];
        assert_eq!(diagnostic["code"], case["code"]);
        assert_eq!(diagnostic["prompt_pointer"], case["pointer"]);
        assert_eq!(diagnostic["cause"], case["detail"]);
        assert_eq!(diagnostic["allowed_form"], case["allowed_form"]);
        assert_span(
            diagnostic,
            &line_span(
                &source,
                case["line"].as_u64().unwrap() as usize,
                case["token"].as_str().unwrap(),
            ),
        );
    }
}

#[test]
fn lexical_failures_precede_unsupported_version_and_semantic_errors() {
    let malformed = CNL.trim_end().trim_end_matches('.');
    for source in [
        malformed.replace("version 1.", "version 2."),
        malformed.replace("to task_status.", "to task_cancel."),
    ] {
        let error = refused(compile_cnl(&source), 400, "CNL_SYNTAX");
        assert_span(
            &error.body["diagnostics"][0],
            &line_span(&source, 7, "Accept task_id as string for task_id"),
        );
    }
    let source = CNL.replace(
        "using contract \"defer.http@",
        "using contract \"\\qdefer.http@",
    );
    refused(compile_cnl(&source), 400, "CNL_SYNTAX");
    refused(compile_cnl(&format!("\u{feff}{CNL}")), 400, "CNL_SYNTAX");
    refused(compile_cnl(&CNL.replacen('\n', "\r", 1)), 400, "CNL_SYNTAX");
}

#[test]
fn missing_duplicate_extra_and_wrong_header_are_structural_refusals() {
    let missing = CNL.lines().take(6).collect::<Vec<_>>().join("\n") + "\n";
    refused(compile_cnl(&missing), 400, "CNL_MISSING");
    refused(
        compile_cnl(&CNL.replace("Accept task_id as", "Accept queue as")),
        400,
        "CNL_DUPLICATE",
    );
    refused(
        compile_cnl(&format!("{CNL}Accept extra as string for extra.\n")),
        400,
        "CNL_SYNTAX",
    );
    refused(
        compile_cnl(&CNL.replace("Service prompt version 1.", "Use SOM CNL version 1.")),
        400,
        "CNL_SYNTAX",
    );
    refused(
        compile_cnl(&CNL.replace("version 1.", "version 3.")),
        422,
        "UNSUPPORTED_VERSION",
    );
}

#[test]
fn typed_contract_rejects_unknown_versions_targets_services_operations_and_types() {
    for (path, replacement, status, code) in [
        ("/version", "som.prompt.v7", 422, "UNSUPPORTED_VERSION"),
        ("/target", "rust", 422, "UNSUPPORTED_TARGET"),
        ("/service/name", "other", 422, "UNSUPPORTED_CONTRACT"),
        (
            "/service/contract",
            "defer.http@unknown",
            422,
            "UNSUPPORTED_CONTRACT",
        ),
        (
            "/operation/name",
            "task_cancel",
            422,
            "UNSUPPORTED_OPERATION",
        ),
        ("/operation/inputs/0/type", "integer", 400, "INVALID_VALUE"),
    ] {
        let mut prompt = value(PROMPT);
        *prompt.pointer_mut(path).unwrap() = json!(replacement);
        let error = refused(compile_prompt(&prompt), status, code);
        pointer(&error, path);
    }
}

#[test]
fn names_and_exact_wire_bindings_are_checked_without_silent_renaming() {
    for (path, names) in [
        ("/package", vec!["Bad", "bad-name", "self", ""]),
        (
            "/operation/alias",
            vec!["fn", "self", "new", "Bad", "", "read-status"],
        ),
        (
            "/operation/inputs/0/name",
            vec!["self", "type", "Bad", "queue-name"],
        ),
    ] {
        for name in names {
            let mut prompt = value(PROMPT);
            *prompt.pointer_mut(path).unwrap() = json!(name);
            let error = refused(compile_prompt(&prompt), 400, "INVALID_VALUE");
            pointer(&error, path);
        }
    }
    let mut prompt = value(PROMPT);
    prompt["operation"]["alias"] = json!("a".repeat(65));
    refused(compile_prompt(&prompt), 400, "INVALID_VALUE");
    for (key, replacement) in [
        ("name", "queue"),
        ("parameter", "queue"),
        ("parameter", "unknown"),
    ] {
        let mut prompt = value(PROMPT);
        prompt["operation"]["inputs"][1][key] = json!(replacement);
        let error = refused(compile_prompt(&prompt), 400, "INVALID_VALUE");
        pointer(&error, &format!("/operation/inputs/1/{key}"));
    }
}

#[test]
fn typed_shapes_reject_missing_unknown_and_mistyped_fields() {
    for path in ["", "/service", "/operation", "/operation/inputs/0"] {
        let mut prompt = value(PROMPT);
        prompt
            .pointer_mut(path)
            .unwrap()
            .as_object_mut()
            .unwrap()
            .insert("extra".into(), json!(true));
        refused(compile_prompt(&prompt), 400, "INVALID_SCHEMA");
    }
    let mut prompt = value(PROMPT);
    prompt.as_object_mut().unwrap().remove("package");
    refused(compile_prompt(&prompt), 400, "INVALID_SCHEMA");
    let mut prompt = value(PROMPT);
    prompt["operation"]["inputs"] = json!({"queue":"string", "task_id":"string"});
    refused(compile_prompt(&prompt), 400, "INVALID_SCHEMA");
    for inputs in [
        json!([]),
        json!([{"name":"queue","type":"string","parameter":"queue"}]),
    ] {
        let mut prompt = value(PROMPT);
        prompt["operation"]["inputs"] = inputs;
        refused(compile_prompt(&prompt), 400, "INVALID_SCHEMA");
    }
}

#[test]
fn http_compile_forms_share_the_same_bundle_and_assemble_files() {
    let cnl = ok(compile_json(
        &json!({"format":"som.cnl.service.v1", "source":CNL}).to_string(),
    ));
    let typed = ok(compile_json(
        &json!({"format":"som.prompt-json.service.v1", "prompt":value(PROMPT)}).to_string(),
    ));
    assert_eq!(cnl["status"], "ok");
    assert_eq!(cnl["bundle"], typed["bundle"]);
    assert_eq!(cnl["bundle"], ok(compile_prompt(&value(PROMPT))));
    let assembled = ok(assemble_json(&json!({"bundle":cnl["bundle"]}).to_string()));
    assert_eq!(assembled["status"], "ok");
    assert_eq!(assembled["files"], json!(ok(assemble(&cnl["bundle"]))));
}

#[test]
fn json_envelopes_reject_duplicate_unknown_keys_and_old_formats() {
    refused(
        compile_json(
            r#"{"format":"som.cnl.service.v1","format":"som.cnl.service.v1","source":""}"#,
        ),
        400,
        "INVALID_SCHEMA",
    );
    let prompt = serde_json::to_string(&value(PROMPT)).unwrap();
    let duplicate = prompt.replacen(
        "\"package\":\"defer_status_client\"",
        "\"package\":\"defer_status_client\",\"package\":\"defer_status_client\"",
        1,
    );
    assert_ne!(duplicate, prompt);
    refused(
        compile_json(&format!(
            r#"{{"format":"som.prompt-json.service.v1","prompt":{duplicate}}}"#
        )),
        400,
        "INVALID_SCHEMA",
    );
    for request in [
        json!({"format":"som.cnl.service.v1", "source":CNL, "extra":true}),
        json!({"format":"som.cnl.service.v1", "source":CNL, "prompt":value(PROMPT)}),
        json!({"format":"som.prompt-json.service.v1", "source":CNL}),
    ] {
        refused(compile_json(&request.to_string()), 400, "INVALID_SCHEMA");
    }
    for format in ["som.cnl.v1", "som.prompt-json.v1", "som.cnl.service.v2"] {
        refused(
            compile_json(&json!({"format":format,"source":CNL}).to_string()),
            422,
            "UNSUPPORTED_VERSION",
        );
    }
    let bundle = ok(compile_prompt(&value(PROMPT)));
    refused(
        assemble_json(&json!({"bundle":bundle,"extra":true}).to_string()),
        400,
        "INVALID_SCHEMA",
    );
    refused(
        assemble_json(&format!(r#"{{"bundle":{bundle},"bundle":{bundle}}}"#)),
        400,
        "INVALID_SCHEMA",
    );
}

#[test]
fn saved_bundle_rejects_assets_and_digest_changes_independently() {
    let original = ok(compile_prompt(&value(PROMPT)));
    let mut digest = original.clone();
    digest["digest"] = json!("0".repeat(64));
    refused(assemble(&digest), 400, "INVALID_BUNDLE");
    for (key, changed) in [
        ("family", "som.compiler-assets.v1".to_owned()),
        ("sha256", "0".repeat(64)),
    ] {
        let mut bundle = original.clone();
        bundle["assets"][key] = json!(changed);
        rehash(&mut bundle);
        refused(assemble(&bundle), 400, "ASSET_HASH_MISMATCH");
    }
}

#[test]
fn rehashed_bundle_cannot_change_prompt_wire_method_path_or_provider() {
    let original = ok(compile_prompt(&value(PROMPT)));
    for (path, replacement) in [
        ("/prompt/operation/alias", "other_status"),
        ("/dsl/operation/method", "DELETE"),
        ("/dsl/operation/path", "https://elsewhere.invalid/secret"),
        ("/dsl/operation/response", "CreateTask"),
        ("/dsl/provider/id", "defer.http.task_cancel.v1"),
    ] {
        let mut bundle = original.clone();
        *bundle.pointer_mut(path).unwrap() = json!(replacement);
        rehash(&mut bundle);
        refused(assemble(&bundle), 400, "INVALID_BUNDLE");
    }
    let mut bundle = original;
    bundle["version"] = json!("som.dsl-bundle.v1");
    rehash(&mut bundle);
    refused(assemble(&bundle), 400, "INVALID_BUNDLE");
}

#[test]
fn health_advertises_only_the_new_compiler_family() {
    let health = health();
    assert_eq!(health["status"], "ok");
    assert_eq!(health["compiler"], "som-service.v1");
    assert_eq!(health["model_loaded"], false);
    for (key, expected) in [
        (
            "formats",
            vec![
                "som.cnl.service.v1",
                "som.prompt-json.service.v1",
                "som.prompt-json.service.v2",
            ],
        ),
        (
            "prompt_versions",
            vec!["som.prompt.service.v1", "som.prompt.service.v2"],
        ),
        (
            "dsl_versions",
            vec!["som.dsl.service.v1", "som.dsl.service.v2"],
        ),
        ("targets", vec!["rust-service-client.v1"]),
    ] {
        let actual: BTreeSet<_> = health[key]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(actual, expected.into_iter().collect(), "health {key}");
        assert_eq!(
            actual.len(),
            health[key].as_array().unwrap().len(),
            "duplicate {key}"
        );
    }
    assert_eq!(health["assets"]["family"], "som.service-compiler.v1");
    assert_eq!(
        health["assets"],
        ok(compile_prompt(&value(PROMPT)))["assets"]
    );
}

#[test]
fn wire_contract_lists_the_four_defer_operations_and_keeps_task_status() {
    let wire = value(include_str!("../contract/wire.json"));
    let ops = wire["operations"].as_object().unwrap();
    let keys: Vec<&str> = ops.keys().map(String::as_str).collect();
    assert_eq!(keys, ["queue_put", "task_cancel", "task_create"]);
    let expect = [
        ("task_create", "POST", "/v1/queues/{queue}/tasks", 201),
        ("queue_put", "PUT", "/v1/queues/{queue}", 200),
        (
            "task_cancel",
            "DELETE",
            "/v1/queues/{queue}/tasks/{task_id}",
            204,
        ),
    ];
    for (name, method, path, status) in expect {
        assert_eq!(ops[name]["method"], method, "{name}");
        assert_eq!(ops[name]["path"], path, "{name}");
        assert_eq!(ops[name]["success"]["status"], status, "{name}");
        assert!(
            !ops[name]["source"]["cites"].as_array().unwrap().is_empty(),
            "{name}"
        );
    }
    // task_status data is unchanged: five success bodies, four errors.
    assert_eq!(wire["success_bodies"].as_array().unwrap().len(), 5);
    assert_eq!(wire["errors"].as_array().unwrap().len(), 4);
}

#[test]
fn prompt_json_v2_lowers_four_operations_to_the_golden_dsl() {
    let bundle = ok(compile_prompt_v2(&value(PROMPT_V2)));
    assert_eq!(bundle["dsl"], value(DSL_V2));
    assert_eq!(bundle["prompt"], value(PROMPT_V2));
    assert_eq!(bundle["version"], "som.bundle.service.v1");
    let mut expected = bundle.clone();
    rehash(&mut expected);
    assert_eq!(bundle["digest"], expected["digest"]);
    let http = ok(compile_json(
        &json!({"format":"som.prompt-json.service.v2","prompt":value(PROMPT_V2)}).to_string(),
    ));
    assert_eq!(http["bundle"], bundle);
}

#[test]
fn v2_dsl_matches_wire_contract_methods_and_paths() {
    let wire = value(include_str!("../contract/wire.json"));
    let dsl = value(DSL_V2);
    let operations = dsl["operations"].as_array().unwrap();
    let find = |name: &str| {
        operations
            .iter()
            .find(|op| op["name"] == name)
            .unwrap_or_else(|| panic!("missing {name}"))
    };
    for name in ["task_create", "queue_put", "task_cancel"] {
        assert_eq!(
            find(name)["method"],
            wire["operations"][name]["method"],
            "{name}"
        );
        assert_eq!(
            find(name)["path"],
            wire["operations"][name]["path"],
            "{name}"
        );
    }
    assert_eq!(find("task_status")["method"], "GET");
    assert_eq!(
        find("task_status")["path"],
        "/v1/queues/{queue}/tasks/{task_id}"
    );
}

#[test]
fn v2_input_order_does_not_change_dsl_inputs() {
    let mut prompt = value(PROMPT_V2);
    let first = prompt["operations"][0]["inputs"].as_array_mut().unwrap();
    first.reverse();
    let last = prompt["operations"][3]["inputs"].as_array_mut().unwrap();
    last.reverse();
    let bundle = ok(compile_prompt_v2(&prompt));
    assert_eq!(bundle["dsl"]["operations"], value(DSL_V2)["operations"]);
    assert_eq!(
        bundle["dsl"]["operations"][1]["request_body"]["name"],
        "task"
    );
}

#[test]
fn v2_refuses_bad_input_with_a_json_path() {
    let cases: Vec<(Value, u16, &str, &str)> = vec![
        (
            json!("task_delete"),
            422,
            "UNSUPPORTED_OPERATION",
            "/operations/0/name",
        ),
        (
            json!("string"),
            400,
            "INVALID_VALUE",
            "/operations/1/inputs/1/type",
        ),
        (
            json!("integer"),
            400,
            "INVALID_VALUE",
            "/operations/0/inputs/0/type",
        ),
        (
            json!("queue"),
            400,
            "INVALID_VALUE",
            "/operations/3/inputs/1/parameter",
        ),
        (
            json!("read_task_status"),
            400,
            "INVALID_VALUE",
            "/operations/2/alias",
        ),
        (
            json!("som.prompt.service.v1"),
            422,
            "UNSUPPORTED_VERSION",
            "/version",
        ),
    ];
    for (replacement, status, code, path) in cases {
        let mut prompt = value(PROMPT_V2);
        let segments: Vec<&str> = path.trim_start_matches('/').split('/').collect();
        let mut target = &mut prompt;
        for segment in &segments[..segments.len() - 1] {
            target = match segment.parse::<usize>() {
                Ok(index) => &mut target[index],
                Err(_) => &mut target[*segment],
            };
        }
        let last = segments[segments.len() - 1];
        match last.parse::<usize>() {
            Ok(index) => target[index] = replacement,
            Err(_) => target[last] = replacement,
        }
        let error = refused(compile_prompt_v2(&prompt), status, code);
        pointer(&error, path);
    }
    let mut prompt = value(PROMPT_V2);
    prompt["operations"] = json!([]);
    pointer(
        &refused(compile_prompt_v2(&prompt), 400, "INVALID_SCHEMA"),
        "/operations",
    );
    let mut prompt = value(PROMPT_V2);
    prompt["operations"][1]["extra"] = json!(true);
    pointer(
        &refused(compile_prompt_v2(&prompt), 400, "INVALID_SCHEMA"),
        "/operations/1",
    );
    let mut prompt = value(PROMPT_V2);
    prompt["operations"][1]["inputs"]
        .as_array_mut()
        .unwrap()
        .remove(1);
    pointer(
        &refused(compile_prompt_v2(&prompt), 400, "INVALID_SCHEMA"),
        "/operations/1/inputs",
    );
    let mut prompt = value(PROMPT_V2);
    prompt["operations"][3]["name"] = json!("task_status");
    prompt["operations"][3]["alias"] = json!("other_status");
    pointer(
        &refused(compile_prompt_v2(&prompt), 400, "INVALID_VALUE"),
        "/operations/3/name",
    );
}

#[test]
fn v1_and_v2_formats_do_not_accept_each_others_prompts() {
    refused(
        compile_json(
            &json!({"format":"som.prompt-json.service.v1","prompt":value(PROMPT_V2)}).to_string(),
        ),
        400,
        "INVALID_SCHEMA",
    );
    refused(
        compile_json(
            &json!({"format":"som.prompt-json.service.v2","prompt":value(PROMPT)}).to_string(),
        ),
        400,
        "INVALID_SCHEMA",
    );
}

#[test]
fn v2_bundle_assembles_four_methods_in_operation_order() {
    let files = ok(assemble(&ok(compile_prompt_v2(&value(PROMPT_V2)))));
    let keys: Vec<&str> = files.keys().map(String::as_str).collect();
    assert_eq!(
        keys,
        ["Cargo.lock", "Cargo.toml", "README.md", "src/lib.rs"]
    );

    let lib = &files["src/lib.rs"];
    assert!(lib.contains(include_str!("fixtures/defer-four.methods.rs")));
    assert!(!lib.contains("{{"));
    let positions: Vec<usize> = [
        "read_task_status",
        "create_task",
        "put_queue",
        "cancel_task",
    ]
    .iter()
    .map(|name| {
        lib.find(&format!("pub async fn {name}("))
            .unwrap_or_else(|| panic!("missing method {name}"))
    })
    .collect();
    assert!(positions.windows(2).all(|pair| pair[0] < pair[1]));
    assert_eq!(
        files["Cargo.toml"],
        include_str!("../templates/Cargo.toml.txt").replace("{{PACKAGE}}", "defer_client")
    );
    assert_eq!(
        files,
        ok(assemble(&ok(compile_prompt_v2(&value(PROMPT_V2)))))
    );
}

#[test]
fn v2_assemble_follows_aliases_and_argument_names() {
    let mut prompt = value(PROMPT_V2);
    prompt["operations"] = json!([{
        "alias": "drop_task", "name": "task_cancel",
        "inputs": [
            {"name": "body", "type": "string", "parameter": "queue"},
            {"name": "status", "type": "string", "parameter": "task_id"}
        ]
    }]);
    let files = ok(assemble(&ok(compile_prompt_v2(&prompt))));
    let lib = &files["src/lib.rs"];
    assert!(lib.contains("pub async fn drop_task("));
    assert!(lib.contains(r#"&["v1", "queues", body, "tasks", status]"#));
    assert!(lib.contains(r#"("queue", body), ("task_id", status)"#));
}

#[test]
fn v2_single_operation_bundle_assembles_only_that_method() {
    let mut prompt = value(PROMPT_V2);
    prompt["operations"] = json!([value(PROMPT_V2)["operations"][2].clone()]);
    let files = ok(assemble(&ok(compile_prompt_v2(&prompt))));
    let lib = &files["src/lib.rs"];
    assert!(lib.contains("pub async fn put_queue("));
    assert!(!lib.contains("pub async fn cancel_task("));
    assert!(!lib.contains("reqwest::Method::DELETE"));
}

#[test]
fn v2_bundle_is_checked_before_it_is_assembled() {
    let bundle = ok(compile_prompt_v2(&value(PROMPT_V2)));

    let mut changed = bundle.clone();
    changed["dsl"]["operations"][0]["method"] = json!("DELETE");
    rehash(&mut changed);
    pointer(
        &refused(assemble(&changed), 400, "INVALID_BUNDLE"),
        "/bundle/dsl",
    );

    let mut zeroed = bundle.clone();
    zeroed["digest"] = json!("0".repeat(64));
    pointer(
        &refused(assemble(&zeroed), 400, "INVALID_BUNDLE"),
        "/bundle/digest",
    );

    let mut reassets = bundle.clone();
    reassets["assets"]["sha256"] = json!("0".repeat(64));
    refused(assemble(&reassets), 400, "ASSET_HASH_MISMATCH");
}

#[test]
fn v1_files_are_unchanged_by_the_v2_renderer() {
    let files = ok(assemble(&ok(compile_prompt(&value(PROMPT)))));
    let expected = include_str!("../templates/lib.rs.txt")
        .replace("{{ALIAS}}", "read_task_status")
        .replace("{{QUEUE_ARGUMENT}}", "queue")
        .replace("{{TASK_ARGUMENT}}", "task_id");
    assert_eq!(files["src/lib.rs"], expected);
    assert!(!files["src/lib.rs"].contains("call_json"));
}

#[test]
fn v1_bundle_bytes_are_unchanged_by_v2() {
    let bundle = ok(compile_prompt(&value(PROMPT)));
    assert_eq!(bundle["dsl"], value(DSL));
    assert_eq!(bundle["prompt"], value(PROMPT));
}
