//! Rust output profiles (som.rust-output-scope.v1).
//! They run on a typed prompt that already passed compiler-v1 validation.
//! `phase1` is the phase 1 record, in the order and with the texts of the frozen scope.json and
//! tests/rust-output-v1/rust_profile.py. `check` is the phase 2 profile of
//! tests/rust-output-v1/rust_profile_phase2.py: only query_parameter_collision stays.
use serde_json::Value;
use std::sync::LazyLock;

pub const SCOPE_TEXT: &str = include_str!("../../contracts/rust-output-v1/scope.json");
static SCOPE: LazyLock<Value> =
    LazyLock::new(|| serde_json::from_str(SCOPE_TEXT).expect("frozen scope.json"));

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Refusal {
    pub rule: &'static str,
    pub code: String,
    pub pointer: String,
    pub cause: String,
    pub allowed_form: String,
}

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

/// rustc non_snake_case, plus names made only of underscores.
pub fn is_rust_snake_case(name: &str) -> bool {
    let core = name.trim_matches('_');
    !core.is_empty() && !core.chars().any(char::is_uppercase) && !core.contains("__")
}

fn identifier(name: &str, pointer: String) -> Option<Refusal> {
    if listed(&SCOPE["identifiers"]["not_raw_identifiers"], name) {
        return Some(refusal("identifier_not_raw", pointer, &[("name", name)]));
    }
    if !is_rust_snake_case(name) {
        return Some(refusal(
            "identifier_not_snake_case",
            pointer,
            &[("name", name)],
        ));
    }
    None
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

/// The v3 filter field named like a fixed list parameter (naming.json W4).
fn query_parameter_collision(prompt: &Value, version: &str) -> Option<Refusal> {
    let query = prompt.get("query").filter(|query| !query.is_null())?;
    let parameters = SCOPE["query_parameters"].get(version)?;
    let filter = text(&query["filter_field"]);
    (parameters["filter_parameter"] == "field_name" && listed(&parameters["fixed"], &filter)).then(
        || {
            refusal(
                "query_parameter_collision",
                "/query/filter_field",
                &[("name", &filter)],
            )
        },
    )
}

/// The phase 2 refusal, or None when Rust output covers the prompt.
pub fn check(prompt: &Value) -> Option<Refusal> {
    query_parameter_collision(prompt, &text(&prompt["version"]))
}

/// The product-layout.json phase2 shape the prompt selects.
pub fn shape(prompt: &Value) -> String {
    let version = text(&prompt["version"]);
    let database = text(&prompt["capabilities"]["database"]);
    match version.as_str() {
        "som.prompt.v7" => format!("flow_{database}"),
        "som.prompt.v6" => format!("capability_{database}"),
        "som.prompt.v5" if prompt.get("capabilities").is_some() => {
            format!("capability_{database}")
        }
        _ => "resource".to_owned(),
    }
}

/// The delivered workflows of the capability_postgresql shape.
pub const WORKFLOWS: [&str; 5] = [
    "json_export",
    "csv",
    "gzip_export",
    "decimal_summary",
    "statistics",
];

/// The delivered auth providers of the capability_postgresql shape.
pub const AUTHS: [&str; 3] = ["jwt", "firebase", "google_oauth"];

/// The delivered object storage of the capability_postgresql shape.
pub const STORAGES: [&str; 3] = ["none", "s3", "gcs"];

/// Whether the owners of the prompt's shape and capabilities have delivered. Until then a
/// construct is NOT_IMPLEMENTED and keeps the phase 1 result (CONTRACT.md section 11). The
/// capability_postgresql shape is delivered for jwt auth and no storage (issue #53 D) with the
/// json_export, csv, gzip_export, decimal_summary and statistics workflows (issue #53 E), for
/// firebase and google_oauth auth too (issue #53 F), and for s3 and gcs storage too (issue #53 G).
/// The capability_mongodb shape is delivered for jwt auth and no storage with the same workflows
/// (issue #53 H1), and for the same auths and storages too (issue #53 H2). The flow_postgresql
/// shape is delivered for the same capabilities when every flow step is a delivered operation,
/// and the flow_mongodb shape is delivered on the same terms.
pub fn delivered(prompt: &Value) -> bool {
    let capabilities = &prompt["capabilities"];
    match shape(prompt).as_str() {
        "resource" => true,
        "capability_postgresql" | "capability_mongodb" => capable(capabilities),
        "flow_postgresql" | "flow_mongodb" => {
            capable(capabilities) && flow_delivered(&prompt["flow"]["steps"])
        }
        _ => false,
    }
}

fn capable(capabilities: &Value) -> bool {
    AUTHS.iter().any(|auth| capabilities["auth"] == *auth)
        && STORAGES
            .iter()
            .any(|storage| capabilities["storage"] == *storage)
        && WORKFLOWS
            .iter()
            .any(|workflow| capabilities["workflow"] == *workflow)
}

/// The flow operations delivered so far: authorize, transaction, the parent and child create,
/// get, list and patch (issue #54 I), batch.create, workflow.run and workflow.import and
/// object.put and object.get (issue #54 J), and the if branch (issue #54 K).
const FLOW_OPS: [&str; 16] = [
    "authorize",
    "transaction",
    "if",
    "parent.create",
    "parent.get",
    "parent.list",
    "parent.patch",
    "child.create",
    "child.get",
    "child.list",
    "child.patch",
    "batch.create",
    "workflow.run",
    "workflow.import",
    "object.put",
    "object.get",
];

pub(crate) fn flow_delivered(steps: &Value) -> bool {
    steps.as_array().is_some_and(|steps| {
        steps.iter().all(|step| {
            FLOW_OPS.contains(&text(&step["op"]).as_str())
                && ["steps", "then", "else"]
                    .iter()
                    .all(|arm| step[*arm].is_null() || flow_delivered(&step[*arm]))
        })
    })
}

/// The refusal a target rust request gets: the phase 2 profile for a delivered shape, else the
/// phase 1 profile.
pub fn active(prompt: &Value) -> Option<Refusal> {
    if delivered(prompt) {
        check(prompt)
    } else {
        phase1(prompt)
    }
}

/// The first phase 1 refusal in the frozen order, or None when the prompt is in phase 1 scope.
/// A prompt it admits keeps its phase 1 files byte for byte.
pub fn phase1(prompt: &Value) -> Option<Refusal> {
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
    if let Some(found) = resource(&prompt["resource"], "/resource", true) {
        return Some(found);
    }
    query_parameter_collision(prompt, &version)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn snake_case_matches_the_reference() {
        for name in ["title", "a1", "_private", "trailing_", "x"] {
            assert!(is_rust_snake_case(name), "{name}");
        }
        for name in ["Title", "a__b", "_", "__", "camelCase"] {
            assert!(!is_rust_snake_case(name), "{name}");
        }
    }

    #[test]
    fn version_refusal_fills_the_frozen_text() {
        let found = phase1(&json!({"version":"som.prompt.v6"})).unwrap();
        assert_eq!(found.rule, "prompt_version");
        assert_eq!(found.code, "UNSUPPORTED_TARGET");
        assert_eq!(found.pointer, "/version");
        assert_eq!(
            found.cause,
            "Rust output phase 1 does not cover prompt version 6: capabilities: authentication, database selection, object storage and workflows."
        );
    }

    #[test]
    fn path_comes_before_table_and_fields() {
        let prompt = json!({"version":"som.prompt.v1","resource":{"path":"/items/","table":"_sqlx_migrations",
            "fields":[{"name":"Title"}]}});
        assert_eq!(phase1(&prompt).unwrap().rule, "path_empty_segment");
        assert_eq!(check(&prompt), None);
    }

    #[test]
    fn phase2_keeps_only_the_query_parameter_collision() {
        let prompt = json!({"version":"som.prompt.v3","resource":{"path":"/items","table":"items",
            "fields":[{"name":"limit"}]},"query":{"filter_field":"limit"}});
        let found = check(&prompt).unwrap();
        assert_eq!(found.rule, "query_parameter_collision");
        assert_eq!(found.pointer, "/query/filter_field");
        assert_eq!(Some(found), phase1(&prompt));
        assert_eq!(check(&json!({"version":"som.prompt.v7"})), None);
    }

    #[test]
    fn only_the_delivered_capabilities_leave_phase_1() {
        let capabilities = json!({"database":"postgresql","auth":"jwt","storage":"none",
            "workflow":"json_export"});
        let v6 = json!({"version":"som.prompt.v6","capabilities":capabilities});
        assert!(delivered(&v6));
        assert_eq!(active(&v6), None);
        let mut v5 = v6.clone();
        v5["version"] = json!("som.prompt.v5");
        assert!(delivered(&v5));
        for workflow in WORKFLOWS {
            let mut prompt = v6.clone();
            prompt["capabilities"]["workflow"] = json!(workflow);
            assert!(delivered(&prompt), "{workflow}");
            assert_eq!(active(&prompt), None, "{workflow}");
        }
        for auth in AUTHS {
            let mut prompt = v6.clone();
            prompt["capabilities"]["auth"] = json!(auth);
            assert!(delivered(&prompt), "{auth}");
            assert_eq!(active(&prompt), None, "{auth}");
        }
        for storage in STORAGES {
            let mut prompt = v6.clone();
            prompt["capabilities"]["storage"] = json!(storage);
            assert!(delivered(&prompt), "{storage}");
            assert_eq!(active(&prompt), None, "{storage}");
        }
        let mut mongo = v6.clone();
        mongo["capabilities"]["database"] = json!("mongodb");
        assert!(delivered(&mongo));
        assert_eq!(active(&mongo), None);
        let mut mongo_v5 = mongo.clone();
        mongo_v5["version"] = json!("som.prompt.v5");
        assert!(delivered(&mongo_v5));
        for workflow in WORKFLOWS {
            let mut prompt = mongo.clone();
            prompt["capabilities"]["workflow"] = json!(workflow);
            assert!(delivered(&prompt), "mongodb {workflow}");
        }
        for auth in AUTHS {
            for storage in STORAGES {
                for workflow in WORKFLOWS {
                    for version in ["som.prompt.v6", "som.prompt.v5"] {
                        let mut prompt = mongo.clone();
                        prompt["version"] = json!(version);
                        prompt["capabilities"]["auth"] = json!(auth);
                        prompt["capabilities"]["storage"] = json!(storage);
                        prompt["capabilities"]["workflow"] = json!(workflow);
                        assert!(
                            delivered(&prompt),
                            "mongodb {version} {auth} {storage} {workflow}"
                        );
                        assert_eq!(active(&prompt), None, "mongodb {auth} {storage} {workflow}");
                    }
                }
            }
        }
        for (name, other) in [
            ("auth", "other"),
            ("storage", "azure"),
            ("workflow", "csv_import"),
        ] {
            let mut prompt = mongo.clone();
            prompt["capabilities"][name] = json!(other);
            assert!(!delivered(&prompt), "mongodb {name}");
        }
        for (name, other) in [
            ("database", "mysql"),
            ("auth", "other"),
            ("storage", "azure"),
            ("workflow", "csv_import"),
        ] {
            let mut prompt = v6.clone();
            prompt["capabilities"][name] = json!(other);
            assert!(!delivered(&prompt), "{name}");
            assert!(active(&prompt).is_some(), "{name}");
        }
        assert!(delivered(&json!({"version":"som.prompt.v5"})));
    }

    #[test]
    fn a_flow_leaves_phase_1_when_every_step_is_delivered() {
        let step = |op: &str| json!({"name": "s", "op": op, "args": {}, "undo": null});
        let flow = |steps: Value| {
            json!({"version":"som.prompt.v7","capabilities":{"database":"postgresql",
                "auth":"jwt","storage":"none","workflow":"json_export"},
                "flow":{"name":"f","inputs":[],"result":{},"steps":steps}})
        };
        let steps = json!([step("authorize"), {"name": "t", "op": "transaction",
            "steps": [step("parent.create"), step("child.patch")]}, step("workflow.run")]);
        assert!(delivered(&flow(steps.clone())));
        assert_eq!(active(&flow(steps.clone())), None);
        let mut csv = flow(steps);
        csv["capabilities"]["workflow"] = json!("csv");
        assert!(delivered(&csv));
        let mut import = flow(json!([{"name": "t", "op": "transaction",
            "steps": [step("batch.create"), step("workflow.import")]}]));
        import["capabilities"]["workflow"] = json!("csv");
        assert!(delivered(&import));
        let mut objects = flow(json!([step("object.put"), step("object.get")]));
        objects["capabilities"]["storage"] = json!("s3");
        assert!(delivered(&objects));
        let branch = |then: Value| {
            json!([step("authorize"), {"name": "b", "op": "if", "condition": {}, "exports": {},
                "then": then, "else": [{"name": "t", "op": "transaction",
                "steps": [step("parent.create")]}]}])
        };
        assert!(delivered(&flow(branch(json!([step("parent.get")])))));
        assert!(!delivered(&flow(branch(json!([step("branch")])))));
        let nested = json!([{"name": "t", "op": "transaction", "steps": [step("branch")]}]);
        assert!(!delivered(&flow(nested)));
        let mut mongo = flow(json!([step("authorize")]));
        mongo["capabilities"]["database"] = json!("mongodb");
        assert!(delivered(&mongo));
        assert_eq!(active(&mongo), None);
        let mut nested = flow(json!([{"name": "t", "op": "transaction",
            "steps": [step("branch")]}]));
        nested["capabilities"]["database"] = json!("mongodb");
        assert!(!delivered(&nested));
    }
}
