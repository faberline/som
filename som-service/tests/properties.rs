//! Property tests on the public entry points of som-service (issue #204).
use proptest::prelude::*;
use serde_json::Value;

const PROMPT_V2: &str = include_str!("fixtures/defer-four.prompt.json");

fn value(text: &str) -> Value {
    serde_json::from_str(text).expect("JSON fixture")
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 256, failure_persistence: None, ..ProptestConfig::default() })]

    #[test]
    fn duplicate_keys_are_always_rejected(
        key in "[a-z]{1,8}",
        a in 0i64..1000,
        b in 0i64..1000,
    ) {
        let source = format!("{{\"{key}\":{a},\"{key}\":{b}}}");
        let error = som_service::compile_json(&source)
            .expect_err("a duplicate key must be refused");
        prop_assert_eq!(error.status, 400);
        prop_assert_eq!(error.body["code"].as_str(), Some("INVALID_SCHEMA"));
    }

    #[test]
    fn arbitrary_text_never_panics(source in ".{0,2000}") {
        let _ = som_service::compile_json(&source);
        let _ = som_service::assemble_json(&source);
    }
}

#[test]
fn render_is_deterministic() {
    let first_bundle =
        som_service::compile_prompt_v2(&value(PROMPT_V2)).expect("the fixture compiles");
    let second_bundle =
        som_service::compile_prompt_v2(&value(PROMPT_V2)).expect("the fixture compiles again");
    assert_eq!(first_bundle, second_bundle);

    let files_once = som_service::assemble(&first_bundle).expect("assemble");
    let files_twice = som_service::assemble(&first_bundle).expect("assemble again");
    assert_eq!(files_once, files_twice);

    let server_once = som_service::assemble_server(&first_bundle).expect("assemble_server");
    let server_twice = som_service::assemble_server(&first_bundle).expect("assemble_server again");
    assert_eq!(server_once, server_twice);
}
