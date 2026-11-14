//! The threat model and the cargo-deny policy of som-service (issue #205).
use std::path::Path;

fn read(name: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(name);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

const SECTIONS: [&str; 5] = [
    "## Scope",
    "## Assets",
    "## Trust boundaries",
    "## Threats",
    "## Accepted risks",
];

#[test]
fn threat_model_has_required_sections() {
    let text = read("THREAT_MODEL.md");
    let mut last = 0usize;
    for heading in SECTIONS {
        let index = text
            .lines()
            .position(|line| line.trim_end() == heading)
            .unwrap_or_else(|| panic!("THREAT_MODEL.md is missing the line {heading:?}"));
        assert!(index >= last, "{heading:?} is out of order");
        last = index;
    }
}

#[test]
fn every_threat_has_a_control() {
    let text = read("THREAT_MODEL.md");
    let threats = text
        .split("## Threats")
        .nth(1)
        .and_then(|rest| rest.split("## Accepted risks").next())
        .expect("the ## Threats section");
    let rows: Vec<&str> = threats
        .lines()
        .map(str::trim)
        .filter(|line| line.starts_with('|'))
        .collect();
    assert_eq!(
        rows.first().copied(),
        Some("| ID | Threat | Control | Evidence |"),
        "the threat table header is wrong"
    );
    assert!(
        rows.len() >= 2 + 8,
        "need a header, a separator and at least 8 threat rows, found {}",
        rows.len()
    );
    for row in &rows[2..] {
        let cells: Vec<&str> = row.trim_matches('|').split('|').map(str::trim).collect();
        assert_eq!(cells.len(), 4, "a threat row must have 4 cells: {row}");
        assert!(
            cells[0].starts_with('T') && cells[0].len() > 1,
            "a threat ID must start with T: {row}"
        );
        for (i, cell) in cells.iter().enumerate() {
            assert!(!cell.is_empty(), "cell {i} is empty in: {row}");
        }
    }
}

#[test]
fn deny_config_is_strict() {
    let text = read("deny.toml");
    assert!(
        text.contains("unknown-git = \"deny\""),
        "unknown-git must be deny"
    );
    assert!(
        text.contains("unknown-registry = \"deny\""),
        "unknown-registry must be deny"
    );
    let lines: Vec<&str> = text.lines().map(str::trim).collect();
    let ignore_index = lines
        .iter()
        .position(|line| line.starts_with("ignore = "))
        .expect("deny.toml has an ignore = line in [advisories]");
    if lines[ignore_index] != "ignore = []" {
        // Every ignored advisory must have a reason comment on the line above.
        let above = ignore_index
            .checked_sub(1)
            .map(|i| lines[i])
            .unwrap_or_default();
        assert!(
            above.starts_with('#'),
            "the ignore line needs a # reason comment directly above it"
        );
    }
}

#[test]
fn deny_allows_the_reviewed_permissive_license() {
    // webpki-roots 1.0.11 (via reqwest and hyper-rustls) is CDLA-Permissive-2.0.
    let text = read("deny.toml");
    assert!(
        text.contains("\"CDLA-Permissive-2.0\""),
        "deny.toml must allow CDLA-Permissive-2.0"
    );
}

#[test]
fn tokio_is_past_rustsec_2026_0386() {
    // RUSTSEC-2026-0386 is fixed in tokio 1.53.3.
    let text = read("Cargo.toml");
    assert!(
        text.contains("tokio = { version = \"=1.53.3\""),
        "Cargo.toml must pin tokio =1.53.3"
    );
}

#[test]
fn ci_audit_uses_cargo_deny_0_20_2() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../.github/workflows/ci.yml");
    let text =
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
    assert!(
        text.contains("tool: cargo-deny@0.20.2"),
        "test-audit must install cargo-deny@0.20.2"
    );
    assert!(
        !text.contains("cargo-deny@0.18.3"),
        "cargo-deny 0.18.3 cannot read the current RustSec database"
    );
}
