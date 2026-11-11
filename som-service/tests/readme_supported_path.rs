//! The root README and the service README name `som-service` (Rust) as the one supported path (issue #196).
use std::path::Path;

fn read(rel: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(rel);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn readme_names_one_supported_path() {
    let text = read("../README.md");
    assert_eq!(
        text.matches("## Supported path").count(),
        1,
        "README must have exactly one `## Supported path` heading"
    );
    let start = text.find("## Supported path").unwrap();
    let after = &text[start + "## Supported path".len()..];
    let end = after.find("\n## ").map(|i| i + 1).unwrap_or(after.len());
    let section = &after[..end];
    for needle in ["som-service", "som-service/README.md", "frozen"] {
        assert!(
            section.contains(needle),
            "`## Supported path` section is missing {needle:?}"
        );
    }
    let current = text
        .find("## Current state")
        .expect("README must keep the `## Current state` heading");
    assert!(
        start < current,
        "`## Supported path` must come before `## Current state`"
    );
}

#[test]
fn service_readme_has_no_one_get_claim() {
    let text = read("README.md");
    assert!(
        !text.contains("supports one GET operation"),
        "som-service/README.md still says it supports one GET operation"
    );
    for needle in ["task_create", "task_cancel"] {
        assert!(
            text.contains(needle),
            "som-service/README.md is missing {needle:?}"
        );
    }
}
