//! QUICKSTART.md and scripts/quickstart.sh run the same commands (issue #195).
use std::path::Path;

fn read(rel: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(rel);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

/// The commands of the fenced `sh` blocks: trimmed lines, no blanks, no comments.
fn doc_commands(doc: &str) -> Vec<String> {
    let mut commands = Vec::new();
    let mut in_sh = false;
    for line in doc.lines() {
        let line = line.trim();
        if line == "```sh" {
            in_sh = true;
        } else if line.starts_with("```") {
            in_sh = false;
        } else if in_sh && !line.is_empty() && !line.starts_with('#') {
            commands.push(line.to_string());
        }
    }
    commands
}

/// The doc commands that are not a whole (trimmed) line of the script.
fn missing_commands(doc: &str, script: &str) -> Vec<String> {
    let lines: Vec<&str> = script.lines().map(str::trim).collect();
    doc_commands(doc)
        .into_iter()
        .filter(|command| !lines.contains(&command.as_str()))
        .collect()
}

#[test]
fn quickstart_commands_match_script() {
    let doc = read("QUICKSTART.md");
    let script = read("scripts/quickstart.sh");
    assert!(
        doc_commands(&doc).len() >= 20,
        "QUICKSTART.md has too few commands"
    );
    let missing = missing_commands(&doc, &script);
    assert!(
        missing.is_empty(),
        "commands in QUICKSTART.md that scripts/quickstart.sh does not run: {missing:?}"
    );
}

#[test]
fn a_changed_doc_command_is_reported() {
    let doc = read("QUICKSTART.md");
    let script = read("scripts/quickstart.sh");
    let changed = doc.replacen("cargo build --locked", "cargo build --frozen", 1);
    assert_ne!(
        changed, doc,
        "the doc has no `cargo build --locked` command"
    );
    let missing = missing_commands(&changed, &script);
    assert_eq!(missing.len(), 1, "{missing:?}");
    assert!(missing[0].contains("--frozen"), "{missing:?}");
}

#[test]
fn quickstart_shows_five_requests_with_status_codes() {
    let doc = read("QUICKSTART.md");
    let requests = doc_commands(&doc)
        .into_iter()
        .filter(|command| command.starts_with("curl -sS -w") && command.contains(":8080/v1/"))
        .count();
    assert_eq!(requests, 5, "expected five requests to the app");
    let expected: Vec<&str> = doc
        .lines()
        .filter_map(|line| line.strip_prefix("Expected status: "))
        .collect();
    assert_eq!(expected, ["200", "201", "200", "200", "204"]);
    let script = read("scripts/quickstart.sh");
    assert!(
        script.contains("EXPECTED_STATUS=\"200 201 200 200 204\""),
        "the script must check the same status codes"
    );
}
