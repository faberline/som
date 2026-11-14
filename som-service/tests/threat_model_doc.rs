//! The threat model and the cargo-deny policy of som-service (issue #205).
use std::path::Path;

fn read(rel: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(rel);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

/// The lines of one `## ` section, without the heading line itself.
fn section<'a>(text: &'a str, heading: &str) -> &'a str {
    let start = text
        .find(heading)
        .unwrap_or_else(|| panic!("THREAT_MODEL.md is missing {heading:?}"));
    let after = &text[start + heading.len()..];
    let end = after.find("\n## ").map(|i| i + 1).unwrap_or(after.len());
    &after[..end]
}

#[test]
fn threat_model_has_required_sections() {
    let text = read("THREAT_MODEL.md");
    let headings = [
        "## Scope",
        "## Assets",
        "## Trust boundaries",
        "## Threats",
        "## Accepted risks",
    ];
    let mut last = 0usize;
    for heading in headings {
        let idx = text
            .find(&format!("{heading}\n"))
            .unwrap_or_else(|| panic!("THREAT_MODEL.md is missing the heading {heading:?}"));
        assert!(
            idx >= last,
            "heading {heading:?} is out of order in THREAT_MODEL.md"
        );
        last = idx;
    }
}

#[test]
fn every_threat_has_a_control() {
    let text = read("THREAT_MODEL.md");
    let threats = section(&text, "## Threats");
    let mut rows = 0usize;
    let mut header_seen = false;
    for line in threats.lines() {
        let line = line.trim();
        if !line.starts_with('|') {
            continue;
        }
        let cells: Vec<&str> = line.trim_matches('|').split('|').map(str::trim).collect();
        if !header_seen {
            assert_eq!(
                cells,
                ["ID", "Threat", "Control", "Evidence"],
                "the threat table header must be `ID | Threat | Control | Evidence`"
            );
            header_seen = true;
            continue;
        }
        // The separator row `|---|---|---|---|`.
        if cells
            .iter()
            .all(|c| c.chars().all(|ch| ch == '-' || ch == ':'))
        {
            continue;
        }
        assert_eq!(
            cells.len(),
            4,
            "threat row has {} cells: {line}",
            cells.len()
        );
        for (i, cell) in cells.iter().enumerate() {
            assert!(!cell.is_empty(), "threat row has an empty cell {i}: {line}");
        }
        assert!(
            cells[0].starts_with('T'),
            "threat ID must start with T: {line}"
        );
        rows += 1;
    }
    assert!(header_seen, "THREAT_MODEL.md has no threat table");
    assert!(
        rows >= 8,
        "THREAT_MODEL.md has {rows} threats, at least 8 are required"
    );
}

#[test]
fn deny_config_is_strict() {
    let text = read("deny.toml");
    for needle in ["unknown-git = \"deny\"", "unknown-registry = \"deny\""] {
        assert!(text.contains(needle), "deny.toml is missing {needle:?}");
    }
    let advisories_start = text
        .find("[advisories]")
        .expect("deny.toml is missing the [advisories] table");
    let after = &text[advisories_start + "[advisories]".len()..];
    let advisories_end = after.find("\n[").unwrap_or(after.len());
    let advisories = &after[..advisories_end];
    assert!(
        advisories.lines().any(|l| l.trim() == "ignore = []"),
        "[advisories] must keep `ignore = []`: no advisory is turned off"
    );
}
