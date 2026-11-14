//! The root README links resolve, and its history section is a short pointer (issue #233).
use std::path::Path;

fn readme() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../README.md");
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn readme_relative_links_exist() {
    let text = readme();
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    let mut checked = 0;
    let mut rest = text.as_str();
    while let Some(start) = rest.find("](") {
        let after = &rest[start + 2..];
        let Some(end) = after.find(')') else {
            break;
        };
        let raw = &after[..end];
        rest = &after[end + 1..];
        if raw.starts_with("http://")
            || raw.starts_with("https://")
            || raw.starts_with("mailto:")
            || raw.starts_with('#')
        {
            continue;
        }
        let target = raw.split('#').next().unwrap_or("");
        checked += 1;
        assert!(
            root.join(target).exists(),
            "README links to a missing path: {target}"
        );
    }
    assert!(
        checked >= 5,
        "README has {checked} relative links, expected at least 5"
    );
}

#[test]
fn readme_history_section_is_short() {
    let text = readme();
    let heading = "## Historical model roadmap and retained legacy service";
    let start = text
        .find(heading)
        .expect("README keeps the history heading");
    let body = &text[start + heading.len()..];
    let end = body.find("\n## ").map(|i| i + 1).unwrap_or(body.len());
    let section = &body[..end];
    let lines = section.lines().filter(|l| !l.trim().is_empty()).count();
    assert!(
        (3..=6).contains(&lines),
        "history section has {lines} non-empty lines, expected 3 to 6"
    );
    for needle in ["som-rs/README.md", "som-service/README.md", "historical"] {
        assert!(
            section.contains(needle),
            "history section is missing {needle:?}"
        );
    }
}
