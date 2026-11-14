use serde_json::Value;
use std::collections::BTreeMap;

/// Expand a checked v2 DSL into a crate: the client made by `openapi-codegen`
/// plus a manifest, a lock file and a README.
pub fn render_v2(package: &str, dsl: &Value) -> BTreeMap<String, String> {
    let operations = dsl["operations"].as_array().expect("validated DSL");
    let readme = readme_head(package, operations)
        + "\nUse `cargo build --locked` for repeat builds.\n"
        + "The client methods are named by the Defer operation id (`task_status`, `task_create`, `queue_put`, `task_cancel`); the aliases of the prompt do not rename them.\n";
    let mut files: BTreeMap<String, String> = [
        (
            "Cargo.toml".to_owned(),
            include_str!("../templates/client_v2_Cargo.toml.txt").replace("{{PACKAGE}}", package),
        ),
        (
            "Cargo.lock".to_owned(),
            include_str!("../templates/client_v2_Cargo.lock.txt").replace("{{PACKAGE}}", package),
        ),
        ("README.md".to_owned(), readme),
    ]
    .into_iter()
    .collect();
    files.extend(crate::codegen::generate_client(dsl).expect("validated DSL"));
    files
}

fn readme_head(package: &str, operations: &[Value]) -> String {
    let mut text = format!(
        "# {package}\n\nThis Rust library calls Defer at commit `{}`.\n\n",
        crate::model::DEFER_REVISION
    );
    for operation in operations {
        text += &format!(
            "- `{}`: {} {}\n",
            operation["alias"].as_str().expect("validated DSL"),
            operation["method"].as_str().expect("validated DSL"),
            operation["path"].as_str().expect("validated DSL"),
        );
    }
    text
}

/// Expand a checked v2 DSL into an axum server: one route, one `Handler` method
/// and one handler function per operation, in DSL order (#161).
pub fn render_server_v2(package: &str, dsl: &Value) -> BTreeMap<String, String> {
    let operations = dsl["operations"].as_array().expect("validated DSL");
    let methods = operations
        .iter()
        .map(server_trait_method)
        .collect::<Vec<_>>()
        .join("\n\n");
    let routes = operations
        .iter()
        .map(server_route)
        .collect::<Vec<_>>()
        .join("\n");
    let handlers = operations
        .iter()
        .map(server_handler)
        .collect::<Vec<_>>()
        .join("\n\n");
    let readme = readme_head(package, operations)
        + "\nRun `cargo generate-lockfile` once, then `cargo build --locked`.\n";
    [
        (
            "Cargo.toml",
            include_str!("../templates/server_Cargo.toml.txt").replace("{{PACKAGE}}", package),
        ),
        (
            "src/lib.rs",
            include_str!("../templates/server_lib.rs.txt")
                .replace("{{TRAIT_METHODS}}", &methods)
                .replace("{{ROUTES}}", &routes)
                .replace("{{HANDLERS}}", &handlers),
        ),
        ("README.md", readme),
    ]
    .into_iter()
    .map(|(path, content)| (path.to_owned(), content))
    .collect()
}

/// The success status of a `none` response, by operation name.
fn server_empty_status(name: &str) -> &'static str {
    match name {
        "task_create" => "CREATED",
        "task_cancel" => "NO_CONTENT",
        _ => "OK",
    }
}

fn server_trait_method(operation: &Value) -> String {
    let text = |key: &str| operation[key].as_str().expect("validated DSL");
    let response = text("response");
    let return_type = if response == "none" { "()" } else { response };
    let mut arguments = vec!["        &self,".to_owned()];
    for input in operation["inputs"].as_array().expect("validated DSL") {
        arguments.push(format!(
            "        {}: String,",
            input["name"].as_str().expect("validated DSL")
        ));
    }
    if let Value::Object(body) = &operation["request_body"] {
        arguments.push(format!(
            "        {}: {},",
            body["name"].as_str().expect("validated DSL"),
            body["type"].as_str().expect("validated DSL")
        ));
    }
    format!(
        "    fn {}(\n{}\n    ) -> impl Future<Output = Result<{return_type}, ServerError>> + Send;",
        text("alias"),
        arguments.join("\n"),
    )
}

fn server_route(operation: &Value) -> String {
    let text = |key: &str| operation[key].as_str().expect("validated DSL");
    format!(
        "        .route(\"{}\", axum::routing::{}(handle_{}::<H>))",
        text("path"),
        text("method").to_ascii_lowercase(),
        text("alias"),
    )
}

fn server_handler(operation: &Value) -> String {
    let text = |key: &str| operation[key].as_str().expect("validated DSL");
    let inputs = operation["inputs"].as_array().expect("validated DSL");
    // Path parameters are extracted in path order, not in DSL input order.
    let path_inputs: Vec<&str> = operation["path_segments"]
        .as_array()
        .expect("validated DSL")
        .iter()
        .filter_map(|segment| segment["param"].as_str())
        .map(|param| {
            inputs
                .iter()
                .find(|input| input["parameter"] == param)
                .expect("validated DSL")["name"]
                .as_str()
                .expect("validated DSL")
        })
        .collect();
    let mut signature =
        vec!["    axum::extract::State(__handler): axum::extract::State<Arc<H>>,".to_owned()];
    let mut lines = Vec::new();
    match path_inputs.as_slice() {
        [one] => {
            signature
                .push("    axum::extract::Path(__path): axum::extract::Path<String>,".to_owned());
            lines.push(format!("    let {one} = __path;"));
        }
        [first, second] => {
            signature.push(
                "    axum::extract::Path(__path): axum::extract::Path<(String, String)>,"
                    .to_owned(),
            );
            lines.push(format!("    let ({first}, {second}) = __path;"));
        }
        _ => panic!("validated DSL"),
    }
    let mut arguments: Vec<&str> = inputs
        .iter()
        .map(|input| input["name"].as_str().expect("validated DSL"))
        .collect();
    if let Value::Object(body) = &operation["request_body"] {
        let name = body["name"].as_str().expect("validated DSL");
        let kind = body["type"].as_str().expect("validated DSL");
        signature.push("    __bytes: axum::body::Bytes,".to_owned());
        lines.push(format!(
            "    let {name}: {kind} = serde_json::from_slice(&__bytes).map_err(|_| ServerError::new(400, \"invalid_request\", \"The request body is not valid for this operation.\"))?;"
        ));
        arguments.push(name);
    }
    let call = format!("__handler.{}({})", text("alias"), arguments.join(", "));
    if text("response") == "none" {
        lines.push(format!("    {call}.await?;"));
        lines.push(format!(
            "    Ok(axum::http::StatusCode::{}.into_response())",
            server_empty_status(text("name"))
        ));
    } else {
        lines.push(format!("    let __reply = {call}.await?;"));
        lines.push("    Ok(axum::Json(__reply).into_response())".to_owned());
    }
    format!(
        "async fn handle_{}<H: Handler>(\n{}\n) -> Result<axum::response::Response, ServerError> {{\n{}\n}}",
        text("alias"),
        signature.join("\n"),
        lines.join("\n"),
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
