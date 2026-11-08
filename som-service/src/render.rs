use serde_json::Value;
use std::collections::BTreeMap;

/// Expand a checked v2 DSL into one method per operation, in DSL order.
pub fn render_v2(package: &str, dsl: &Value) -> BTreeMap<String, String> {
    let operations = dsl["operations"].as_array().expect("validated DSL");
    let methods = operations
        .iter()
        .map(v2_method)
        .collect::<Vec<_>>()
        .join("\n\n");
    let mut readme = format!(
        "# {package}\n\nThis Rust library calls Defer at commit `{}`.\n\n",
        crate::model::DEFER_REVISION
    );
    for operation in operations {
        readme += &format!(
            "- `{}`: {} {}\n",
            operation["alias"].as_str().expect("validated DSL"),
            operation["method"].as_str().expect("validated DSL"),
            operation["path"].as_str().expect("validated DSL"),
        );
    }
    readme += "\nUse `cargo build --locked` for repeat builds.\n";
    [
        (
            "Cargo.toml",
            include_str!("../templates/Cargo.toml.txt").replace("{{PACKAGE}}", package),
        ),
        (
            "Cargo.lock",
            include_str!("../templates/Cargo.lock.txt").replace("{{PACKAGE}}", package),
        ),
        (
            "src/lib.rs",
            include_str!("../templates/lib_v2.rs.txt").replace("{{METHODS}}", &methods),
        ),
        ("README.md", readme),
    ]
    .into_iter()
    .map(|(path, content)| (path.to_owned(), content))
    .collect()
}

fn v2_method(operation: &Value) -> String {
    let text = |key: &str| operation[key].as_str().expect("validated DSL");
    let alias = text("alias");
    let method = text("method");
    let name = text("name");
    let inputs = operation["inputs"].as_array().expect("validated DSL");
    let (helper, status) = match name {
        "task_status" => ("call_json", 200),
        "task_create" => ("call_empty", 201),
        "queue_put" => ("call_json", 200),
        "task_cancel" => ("call_empty", 204),
        _ => panic!("validated DSL"),
    };
    let response = text("response");
    let (return_type, helper) = if response == "none" {
        ("()".to_owned(), "call_empty")
    } else {
        (response.to_owned(), helper)
    };
    let mut arguments = vec!["        &self,".to_owned()];
    for input in inputs {
        arguments.push(format!(
            "        {}: &str,",
            input["name"].as_str().expect("validated DSL")
        ));
    }
    let body = match &operation["request_body"] {
        Value::Object(body) => {
            let body_name = body["name"].as_str().expect("validated DSL");
            arguments.push(format!(
                "        {body_name}: &{},",
                body["type"].as_str().expect("validated DSL")
            ));
            format!(
                "Some(serde_json::to_vec({body_name}).map_err(|_| ClientError::InvalidInput {{ parameter: \"{body_name}\", reason: \"The body cannot be serialized.\" }})?)"
            )
        }
        _ => "None".to_owned(),
    };
    let parameters = inputs
        .iter()
        .map(|input| {
            format!(
                "(\"{}\", {})",
                input["parameter"].as_str().expect("validated DSL"),
                input["name"].as_str().expect("validated DSL")
            )
        })
        .collect::<Vec<_>>()
        .join(", ");
    let segments = operation["path_segments"]
        .as_array()
        .expect("validated DSL")
        .iter()
        .map(
            |segment| match (segment["literal"].as_str(), segment["param"].as_str()) {
                (Some(literal), None) => format!("\"{literal}\""),
                (None, Some(param)) => inputs
                    .iter()
                    .find(|input| input["parameter"] == param)
                    .expect("validated DSL")["name"]
                    .as_str()
                    .expect("validated DSL")
                    .to_owned(),
                _ => panic!("validated DSL"),
            },
        )
        .collect::<Vec<_>>()
        .join(", ");
    format!(
        "    pub async fn {alias}(\n{}\n    ) -> Result<{return_type}, ClientError> {{\n        self.{helper}(\n            reqwest::Method::{method},\n            {status},\n            &[{parameters}],\n            &[{segments}],\n            {body},\n        )\n        .await\n    }}",
        arguments.join("\n"),
    )
}

/// Expand the fixed client profile. All four names are validated by SOM first.
pub fn render(
    package: &str,
    alias: &str,
    queue_argument: &str,
    task_argument: &str,
) -> BTreeMap<String, String> {
    [
        ("Cargo.toml", include_str!("../templates/Cargo.toml.txt")),
        ("Cargo.lock", include_str!("../templates/Cargo.lock.txt")),
        ("src/lib.rs", include_str!("../templates/lib.rs.txt")),
        ("README.md", include_str!("../templates/README.md.txt")),
    ]
    .into_iter()
    .map(|(path, template)| {
        (
            path.to_owned(),
            template
                .replace("{{PACKAGE}}", package)
                .replace("{{ALIAS}}", alias)
                .replace("{{QUEUE_ARGUMENT}}", queue_argument)
                .replace("{{TASK_ARGUMENT}}", task_argument),
        )
    })
    .collect()
}
