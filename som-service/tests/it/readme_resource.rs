//! The service README documents the resource request (catalog `som.std.v1`) and keeps the Defer path (issue #307).
use std::path::Path;

fn read(rel: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(rel);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn service_readme_documents_the_resource_request() {
    let text = read("README.md");
    for needle in [
        "## Resource request",
        "som.std.v1",
        "Service resource version 1.",
        "Resource Order.",
        "contract/order.resource.cnl",
        "\"emit\":\"resource\"",
        "compile som-service/contract/order.resource.cnl",
        "src/domain/order.rs",
        "src/application/store.rs",
        "The Defer path is unchanged.",
    ] {
        assert!(
            text.contains(needle),
            "som-service/README.md is missing {needle:?}"
        );
    }
    for needle in ["task_create", "task_cancel"] {
        assert!(
            text.contains(needle),
            "som-service/README.md lost {needle:?}"
        );
    }
    assert!(
        !text.contains("supports one GET operation"),
        "som-service/README.md still says it supports one GET operation"
    );
}
