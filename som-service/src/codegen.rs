use crate::{Error, openapi};
use openapi_codegen::{
    GenOptions, Lang, OperationSelection, RustTarget, TargetProfile, generate_for_target,
};
use serde_json::Value;
use std::collections::BTreeMap;

/// Generate the Rust client of a checked v2 DSL with core `openapi-codegen`.
/// Keys: `src/models.rs`, `src/client.rs`, `src/lib.rs` (the generated `mod.rs`)
/// and `.openapi-codegen.json`.
pub fn generate_client(dsl: &Value) -> Result<BTreeMap<String, String>, Error> {
    let ids = openapi::operation_ids(dsl);
    let ids_ref: Vec<&str> = ids.iter().map(String::as_str).collect();
    let document = openapi::select_operations(&openapi::defer_document(), &ids_ref)?;
    let selection = OperationSelection::new(ids)
        .map_err(|e| Error::new("INVALID_SCHEMA", e.to_string(), "/operations"))?;
    let options = GenOptions::new(Lang::Rust, "defer.openapi.json", "client", "Client")
        .with_operations(selection);
    let output = generate_for_target(
        &document.to_string(),
        &options,
        TargetProfile::Rust(RustTarget::Rust2024),
    )
    .map_err(|e| Error::new("INVALID_SCHEMA", e.to_string(), "/operations"))?;
    let mut map = BTreeMap::new();
    for file in &output.files {
        let key = match file.rel_path.as_str() {
            "mod.rs" => "src/lib.rs".to_owned(),
            other => format!("src/{other}"),
        };
        map.insert(key, file.contents.clone());
    }
    let manifest = serde_json::to_string_pretty(&output.manifest().expect("manifest"))
        .expect("manifest serializes")
        + "\n";
    map.insert(".openapi-codegen.json".to_owned(), manifest);
    Ok(map)
}
