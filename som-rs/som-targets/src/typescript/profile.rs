//! TypeScript output phase 1 profile (som.typescript-output-scope.v1).
//!
//! It runs on a typed prompt that already passed compiler-v1 validation, in the frozen order and
//! with the frozen texts of contracts/typescript-output-v1/scope.json and
//! tests/typescript-output-v1/ts_profile.py.
use serde_json::Value;
use std::sync::LazyLock;

pub use crate::profile::Refusal;

pub const SCOPE_TEXT: &str = include_str!("../../../contracts/typescript-output-v1/scope.json");
static SCOPE: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(SCOPE_TEXT).expect("frozen TypeScript scope.json"));

fn refusal(rule: &'static str, pointer: impl Into<String>, fill: &[(&str, &str)]) -> Refusal {
    let entry = &SCOPE["refusals"][rule];
    let mut cause = entry["cause"].as_str().expect("frozen cause").to_owned();
    for (key, value) in fill {
        cause = cause.replace(&format!("{{{key}}}"), value);
    }
    Refusal {
        rule,
        code: entry["code"].as_str().expect("frozen code").to_owned(),
        pointer: pointer.into(),
        cause,
        allowed_form: entry["allowed_form"]
            .as_str()
            .expect("frozen allowed form")
            .to_owned(),
    }
}

fn listed(list: &Value, item: &str) -> bool {
    list.as_array()
        .is_some_and(|rows| rows.iter().any(|row| row == item))
}

fn text(value: &Value) -> String {
    value
        .as_str()
        .map_or_else(|| som::flow::canonical(value), str::to_owned)
}

fn identifier(name: &str, pointer: String) -> Option<Refusal> {
    listed(&SCOPE["identifiers"]["reserved_names"], name)
        .then(|| refusal("identifier_reserved", pointer, &[("name", name)]))
}

fn path(path: &str, pointer: String, top_level: bool) -> Option<Refusal> {
    if path.split('/').skip(1).any(str::is_empty) {
        return Some(refusal("path_empty_segment", pointer, &[("path", path)]));
    }
    if top_level && listed(&SCOPE["routes"]["fixed_top_level_paths"], path) {
        return Some(refusal("path_reserved", pointer, &[("path", path)]));
    }
    None
}

fn table(table: &str, pointer: String) -> Option<Refusal> {
    listed(&SCOPE["identifiers"]["reserved_tables"], table)
        .then(|| refusal("table_reserved", pointer, &[("table", table)]))
}

fn resource(resource: &Value, base: &str, top_level: bool) -> Option<Refusal> {
    path(&text(&resource["path"]), format!("{base}/path"), top_level)
        .or_else(|| table(&text(&resource["table"]), format!("{base}/table")))
        .or_else(|| {
            resource["fields"]
                .as_array()
                .into_iter()
                .flatten()
                .enumerate()
                .find_map(|(index, field)| {
                    identifier(&text(&field["name"]), format!("{base}/fields/{index}/name"))
                })
        })
}

/// The first TypeScript phase 1 refusal in the frozen order, or None when the prompt is in scope.
pub fn check(prompt: &Value) -> Option<Refusal> {
    let version = text(&prompt["version"]);
    if !listed(&SCOPE["prompt_versions"]["in_scope"], &version) {
        let family = text(&SCOPE["prompt_versions"]["out_of_scope"][&version]);
        let number = version.strip_prefix("som.prompt.v").unwrap_or(&version);
        return Some(refusal(
            "prompt_version",
            "/version",
            &[("version", number), ("family", &family)],
        ));
    }
    if version == "som.prompt.v5" {
        if let Some(capabilities) = prompt.get("capabilities") {
            return Some(refusal(
                "v5_capabilities",
                "/capabilities/auth",
                &[("auth", &text(&capabilities["auth"]))],
            ));
        }
        return resource(&prompt["resources"]["parent"], "/resources/parent", true)
            .or_else(|| resource(&prompt["resources"]["child"], "/resources/child", false))
            .or_else(|| {
                identifier(
                    &text(&prompt["relation"]["foreign_key"]),
                    "/relation/foreign_key".into(),
                )
            });
    }
    resource(&prompt["resource"], "/resource", true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn version_refusal_fills_the_frozen_text() {
        let found = check(&json!({"version": "som.prompt.v6"})).unwrap();
        assert_eq!(found.rule, "prompt_version");
        assert_eq!(found.code, "UNSUPPORTED_TARGET");
        assert_eq!(found.pointer, "/version");
        assert_eq!(
            found.cause,
            "TypeScript output phase 1 does not cover prompt version 6: capabilities: authentication, database selection, object storage and workflows."
        );
    }

    #[test]
    fn path_comes_before_table_and_fields() {
        let prompt = json!({
            "version": "som.prompt.v1",
            "resource": {
                "path": "/items/",
                "table": "_som_ts_migrations",
                "fields": [{"name": "__proto__"}]
            }
        });
        assert_eq!(check(&prompt).unwrap().rule, "path_empty_segment");
    }

    #[test]
    fn child_healthz_and_non_reserved_names_are_admitted() {
        let prompt = json!({
            "version": "som.prompt.v5",
            "resources": {
                "parent": {
                    "path": "/projects",
                    "table": "_sqlx_migrations",
                    "fields": [
                        {"name": "Title"},
                        {"name": "toString"},
                        {"name": "hasOwnProperty"},
                        {"name": "valueOf"}
                    ]
                },
                "child": {
                    "path": "/healthz",
                    "table": "todos",
                    "fields": [
                        {"name": "a__b"},
                        {"name": "self"},
                        {"name": "crate"}
                    ]
                }
            },
            "relation": {
                "foreign_key": "self"
            }
        });
        assert_eq!(check(&prompt), None);
    }
}
