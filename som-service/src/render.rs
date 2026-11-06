use std::collections::BTreeMap;

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
