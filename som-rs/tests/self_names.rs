//! Issues #57 and #58: the frozen self-name fixtures (compiler-v1 amendment 003).
//! Variant selection on a full plan needs the packaged assets; the QA probe and the #57
//! acceptance cover it. This test covers the refusals, pointers, CNL equality and trigger.
use serde_json::{json, Value};
use som::{cnl, flow, json_diagnostics, python_names, validate_prompt};

fn fixtures() -> Value {
    serde_json::from_str(include_str!("compiler-v1/self-name-fixtures.json")).unwrap()
}

fn cnl_prompt(case: &Value) -> Value {
    cnl::parse(case["cnl"].as_str().unwrap())
        .unwrap_or_else(|error| panic!("{}: {}", case["id"], error.detail))
        .prompt
}

#[test]
fn self_names_are_accepted_and_select_their_variants() {
    let fixtures = fixtures();
    let cases = fixtures["positive"].as_array().unwrap();
    assert_eq!(cases.len(), 15);
    for case in cases {
        let id = &case["id"];
        let prompt = &case["prompt"];
        validate_prompt(prompt).unwrap_or_else(|error| panic!("{id}: {}", error.detail));
        assert_eq!(flow::canonical(&cnl_prompt(case)), flow::canonical(prompt), "{id}");
        assert!(python_names::uses_self(prompt), "{id}");
        let version = prompt["version"].as_str().unwrap();
        let family = python_names::family(version).unwrap_or_else(|| panic!("{id}: {version}"));
        // Each expected variant is the variant the family selects for one of its base blocks.
        for variant in case["python_variants"].as_array().unwrap() {
            let base = variant.as_str().unwrap().strip_suffix(".self").unwrap();
            let plan = json!({"files": [{"blocks": [{"id": base}]}]});
            let selected = python_names::select_variants(prompt, plan);
            assert_eq!(selected["files"][0]["blocks"][0]["id"], *variant, "{id}: {family}");
        }
    }
}

#[test]
fn colliding_names_are_refused_for_json_and_cnl() {
    let fixtures = fixtures();
    let cases = fixtures["negative"].as_array().unwrap();
    assert_eq!(cases.len(), 13);
    for case in cases {
        let id = &case["id"];
        let expected = &case["expected"];
        let error = validate_prompt(&case["prompt"]).expect_err(id.as_str().unwrap());
        assert_eq!(error.code, expected["code"], "{id}");
        assert_eq!(error.detail, expected["detail"], "{id}");
        assert!(python_names::is_collision(&error.detail), "{id}");
        let pointer = json_diagnostics::prompt_pointer(&case["prompt"], &error).unwrap();
        assert_eq!(format!("/prompt{pointer}"), expected["json_prompt_pointer"], "{id}");

        let error = cnl::parse(case["cnl"].as_str().unwrap()).expect_err(id.as_str().unwrap());
        assert_eq!(error.code, expected["code"], "{id}");
        assert_eq!(u64::from(error.http_status()), expected["status"].as_u64().unwrap(), "{id}");
        assert_eq!(error.diagnostics.len(), 1, "{id}");
        let diagnostic = &error.diagnostics[0];
        assert_eq!(
            diagnostic.prompt_pointer.as_deref(),
            expected["cnl_prompt_pointer"].as_str(),
            "{id}"
        );
        assert_eq!(diagnostic.allowed_form, expected["cnl_allowed_form"], "{id}");
    }
}

#[test]
fn names_without_a_policy_row_are_accepted() {
    let fixtures = fixtures();
    let cases = fixtures["accepted"].as_array().unwrap();
    assert_eq!(cases.len(), 3);
    for case in cases {
        let id = &case["id"];
        validate_prompt(&case["prompt"]).unwrap_or_else(|error| panic!("{id}: {}", error.detail));
        assert_eq!(flow::canonical(&cnl_prompt(case)), flow::canonical(&case["prompt"]), "{id}");
        assert!(!python_names::uses_self(&case["prompt"]), "{id}");
    }
}
