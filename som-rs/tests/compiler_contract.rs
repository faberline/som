//! The expected values and spans were frozen before compiler implementation.
use serde_json::Value;
use som::{cnl, compiler, flow};

#[test]
fn frozen_positive_cnl_preserves_the_complete_typed_prompt() {
    let fixtures: Value =
        serde_json::from_str(include_str!("compiler-v1/cnl-positive-fixtures.json")).unwrap();
    let cases = fixtures["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 119);
    for case in cases {
        let actual = cnl::parse(case["cnl"].as_str().unwrap())
            .unwrap_or_else(|error| panic!("{}: {error}", case["id"]));
        // Canonical text preserves float/integer categories and negative zero.
        assert_eq!(
            flow::canonical(&actual.prompt),
            flow::canonical(&case["expected_prompt"]),
            "{}",
            case["id"]
        );
    }
}

#[test]
fn frozen_negative_cnl_reports_the_exact_first_error_and_source_span() {
    let fixtures: Value =
        serde_json::from_str(include_str!("compiler-v1/cnl-negative-cases.json")).unwrap();
    let base = include_str!("../contracts/compiler-v1/examples/v7.cnl");
    assert_eq!(
        compiler::digest(base.as_bytes()),
        fixtures["base_example"]["sha256"]
    );
    let cases = fixtures["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 15);
    for case in cases {
        let mut lines: Vec<String> = base.lines().map(str::to_owned).collect();
        if let Some(line) = case["replace_line"].as_u64() {
            lines[line as usize - 1] = case["with"].as_str().unwrap().into();
        }
        if let Some(edits) = case["replace_lines"].as_array() {
            for edit in edits {
                lines[edit["line"].as_u64().unwrap() as usize - 1] =
                    edit["with"].as_str().unwrap().into();
            }
        }
        if let Some(insert) = case.get("insert_before_line") {
            lines.insert(
                insert["line"].as_u64().unwrap() as usize - 1,
                insert["text"].as_str().unwrap().into(),
            );
        }
        let source = lines.join("\n") + "\n";
        let error = cnl::parse(&source).expect_err(case["id"].as_str().unwrap());
        assert_eq!(error.code, case["code"]);
        assert_eq!(
            u64::from(error.http_status()),
            case["http_status"].as_u64().unwrap()
        );
        assert_eq!(error.diagnostics.len(), 1);
        let diagnostic = &error.response()["diagnostics"][0];
        for (key, expected) in case["expected"].as_object().unwrap() {
            assert_eq!(&diagnostic[key], expected, "{}: {key}", case["id"]);
        }
        let span = &error.diagnostics[0].span;
        assert_eq!(
            &source[span.byte_start..span.byte_end],
            case["span_text"].as_str().unwrap()
        );
    }
}

#[test]
fn frozen_canonical_vectors_preserve_types_unicode_and_hashes() {
    let fixtures: Value = serde_json::from_str(include_str!(
        "../contracts/compiler-v1/canonical-vectors.json"
    ))
    .unwrap();
    let cases = fixtures["vectors"].as_array().unwrap();
    assert_eq!(cases.len(), 11);
    for case in cases {
        let value: Value = serde_json::from_str(case["input_json"].as_str().unwrap()).unwrap();
        let canonical = flow::canonical(&value);
        assert_eq!(canonical, case["canonical"]);
        assert_eq!(compiler::digest(canonical.as_bytes()), case["sha256"]);
    }
}
