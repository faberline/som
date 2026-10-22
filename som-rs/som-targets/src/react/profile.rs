//! React output phase 1 profile (som.react-output-scope.v1).
//!
//! It runs on a typed prompt that already passed compiler-v1 validation, in the frozen
//! refusal_order and with the frozen texts of contracts/react-output-v1/scope.json and
//! tests/react-output-v1/react_profile.py.
use serde_json::Value;
use std::sync::LazyLock;

pub use crate::profile::Refusal;

pub const SCOPE_TEXT: &str = include_str!("../../../contracts/react-output-v1/scope.json");
static SCOPE: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(SCOPE_TEXT).expect("frozen React scope.json"));

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

/// The first React phase 1 refusal in the frozen order, or None when the prompt is in scope.
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
    let resource = &prompt["resource"];
    let path = text(&resource["path"]);
    if path.split('/').skip(1).any(str::is_empty) {
        return Some(refusal(
            "path_empty_segment",
            "/resource/path",
            &[("path", &path)],
        ));
    }
    resource["fields"]
        .as_array()
        .into_iter()
        .flatten()
        .enumerate()
        .find_map(|(index, field)| {
            let name = text(&field["name"]);
            listed(&SCOPE["identifiers"]["reserved_names"], &name).then(|| {
                refusal(
                    "identifier_reserved",
                    format!("/resource/fields/{index}/name"),
                    &[("name", &name)],
                )
            })
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn version_refusal_fills_the_frozen_text() {
        let found = check(&json!({"version": "som.prompt.v5"})).unwrap();
        assert_eq!(found.rule, "prompt_version");
        assert_eq!(found.code, "UNSUPPORTED_TARGET");
        assert_eq!(found.pointer, "/version");
        assert_eq!(
            found.cause,
            "React output phase 1 does not cover prompt version 5: related resources: a parent and a child joined by a foreign key."
        );
    }

    #[test]
    fn path_comes_before_fields() {
        let prompt = json!({
            "version": "som.prompt.v1",
            "resource": {"path": "/items/", "fields": [{"name": "__proto__"}]}
        });
        assert_eq!(check(&prompt).unwrap().rule, "path_empty_segment");
    }

    #[test]
    fn reserved_names_in_array_order_and_others_admitted() {
        let prompt = json!({
            "version": "som.prompt.v4",
            "resource": {
                "path": "/healthz",
                "table": "_som_ts_migrations",
                "fields": [{"name": "toString"}, {"name": "prototype"}, {"name": "constructor"}]
            }
        });
        let found = check(&prompt).unwrap();
        assert_eq!(found.pointer, "/resource/fields/1/name");
        assert_eq!(found.cause, "React output cannot use the name prototype.");
        let admitted = json!({
            "version": "som.prompt.v2",
            "resource": {"path": "/a/b", "fields": [{"name": "hasOwnProperty"}, {"name": "self"}]}
        });
        assert_eq!(check(&admitted), None);
    }
}
