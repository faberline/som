//! Subset selection over the pinned Defer OpenAPI document (issue #171).
use serde_json::Value;
use sha2::{Digest, Sha256};
use som_service::openapi::{
    DEFER_OPENAPI, defer_document, operation_ids, select_operations, unresolved_refs,
};
use std::collections::BTreeSet;
use std::path::Path;

const RECORDED_SHA256: &str = "295e46df0d2379a198b48f19183d62a777a22b2d4c72f725486ef149dd7382c4";
const RECORDED_REVISION: &str = "c408d0b101d20276d5f8c0a81aa787405bd0b58d";
const METHODS: [&str; 8] = [
    "get", "put", "post", "delete", "patch", "head", "options", "trace",
];

const PROMPT_V2: &str = include_str!("fixtures/defer-four.prompt.json");

fn hex_sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn manifest_file(name: &str) -> Vec<u8> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(name);
    std::fs::read(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

/// Every (path, method, operationId) triple in a document's `paths`.
fn operations(document: &Value) -> BTreeSet<(String, String, String)> {
    let mut out = BTreeSet::new();
    for (path, item) in document["paths"].as_object().expect("paths object") {
        for method in METHODS {
            if let Some(op) = item.get(method) {
                out.insert((
                    path.clone(),
                    method.to_owned(),
                    op["operationId"].as_str().expect("operationId").to_owned(),
                ));
            }
        }
    }
    out
}

fn selection() -> Value {
    select_operations(&defer_document(), &["task_status", "task_create"])
        .expect("select task_status and task_create")
}

#[test]
fn subset_keeps_only_selected_operations() {
    let subset = selection();
    let expected: BTreeSet<(String, String, String)> = [
        ("/v1/queues/{queue}/tasks/{task_id}", "get", "task_status"),
        ("/v1/queues/{queue}/tasks", "post", "task_create"),
    ]
    .into_iter()
    .map(|(p, m, id)| (p.to_owned(), m.to_owned(), id.to_owned()))
    .collect();
    assert_eq!(operations(&subset), expected);

    let paths = subset["paths"].as_object().expect("paths object");
    assert_eq!(paths.len(), 2, "exactly two path items: {paths:?}");
    assert!(
        paths["/v1/queues/{queue}/tasks/{task_id}"]
            .get("delete")
            .is_none(),
        "task_cancel must not be kept"
    );
}

#[test]
fn subset_keeps_referenced_schemas() {
    let subset = selection();

    let schema_names: BTreeSet<&str> = subset["components"]["schemas"]
        .as_object()
        .expect("schemas object")
        .keys()
        .map(String::as_str)
        .collect();
    assert_eq!(
        schema_names,
        BTreeSet::from([
            "CreateTask",
            "ErrorEnvelope",
            "Target",
            "TaskStatus",
            "TaskStatusResponse"
        ]),
    );

    assert!(
        unresolved_refs(&subset).is_empty(),
        "subset has unresolved refs: {:?}",
        unresolved_refs(&subset)
    );
    assert!(
        unresolved_refs(&defer_document()).is_empty(),
        "full document has unresolved refs: {:?}",
        unresolved_refs(&defer_document())
    );

    // Cutting the document must not break anything the full document did not already break.
    let full_unresolved = unresolved_refs(&defer_document());
    for r in unresolved_refs(&subset) {
        assert!(full_unresolved.contains(&r), "new unresolved ref {r}");
    }
}

#[test]
fn defer_openapi_refs_all_resolve() {
    fn collect_refs<'a>(value: &'a Value, out: &mut Vec<&'a str>) {
        match value {
            Value::Object(map) => {
                for (key, child) in map {
                    if key == "$ref" {
                        out.push(child.as_str().expect("$ref string"));
                    } else {
                        collect_refs(child, out);
                    }
                }
            }
            Value::Array(items) => {
                for item in items {
                    collect_refs(item, out);
                }
            }
            _ => {}
        }
    }

    let document = defer_document();
    let mut refs = Vec::new();
    collect_refs(&document, &mut refs);
    assert!(!refs.is_empty(), "document has no $ref at all");

    let schemas = document["components"]["schemas"]
        .as_object()
        .expect("components.schemas object");
    for r in &refs {
        let name = r
            .strip_prefix("#/components/schemas/")
            .unwrap_or_else(|| panic!("$ref not under components/schemas: {r}"));
        assert!(!name.contains('/'), "$ref has extra path segments: {r}");
        assert!(schemas.contains_key(name), "dangling $ref: {r}");
    }
}

#[test]
fn unknown_operation_is_an_error() {
    let document = defer_document();
    let err = select_operations(&document, &["task_status", "no_such_op"])
        .expect_err("unknown operationId must fail");
    assert_eq!(err.status, 422);
    assert_eq!(err.body["code"], "UNSUPPORTED_OPERATION");
    assert!(
        err.body["detail"]
            .as_str()
            .expect("detail string")
            .contains("no_such_op"),
        "detail must name the id: {}",
        err.body["detail"]
    );

    let empty = select_operations(&document, &[]).expect_err("empty ids must fail");
    assert_eq!(empty.body["code"], "INVALID_SCHEMA");
}

#[test]
fn contract_hash_matches_recorded_hash() {
    let source: Value =
        serde_json::from_slice(&manifest_file("contract/defer.openapi.source.json"))
            .expect("source record JSON");
    let file_hash = hex_sha256(&manifest_file("contract/defer.openapi.json"));

    assert_eq!(file_hash, RECORDED_SHA256);
    assert_eq!(source["sha256"], RECORDED_SHA256);
    assert_eq!(source["revision"], RECORDED_REVISION);
    assert_eq!(hex_sha256(DEFER_OPENAPI.as_bytes()), RECORDED_SHA256);
}

#[test]
fn operation_ids_follow_the_v2_prompt() {
    let prompt: Value = serde_json::from_str(PROMPT_V2).expect("v2 prompt fixture");
    let ids = operation_ids(&prompt);
    assert_eq!(
        ids,
        ["task_status", "task_create", "queue_put", "task_cancel"]
    );

    let refs: Vec<&str> = ids.iter().map(String::as_str).collect();
    let subset = select_operations(&defer_document(), &refs).expect("four operations");
    assert_eq!(operations(&subset).len(), 4);
}
