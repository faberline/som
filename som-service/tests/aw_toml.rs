//! The repository aw.toml records the S8 coverage ratchet and the S4 exception (issue #204).
use std::path::Path;

fn aw_toml() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../aw.toml");
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn aw_toml_has_coverage_ratchet() {
    let text = aw_toml();
    let lines: Vec<&str> = text.lines().map(str::trim).collect();
    assert!(
        lines.contains(&"[standards.coverage]"),
        "aw.toml is missing [standards.coverage]"
    );
    assert!(
        lines.contains(&"target = 100"),
        "aw.toml is missing target = 100"
    );
    let raw = lines
        .iter()
        .find_map(|l| l.strip_prefix("line = "))
        .unwrap_or_else(|| panic!("aw.toml is missing a line = ... entry"));
    let value_text = raw.split('#').next().unwrap_or("").trim();
    let value: f64 = value_text
        .parse()
        .unwrap_or_else(|e| panic!("line value {value_text:?} is not a number: {e}"));
    assert!(
        value > 0.0 && value <= 100.0,
        "line = {value} is outside (0, 100]"
    );
}

#[test]
fn aw_toml_core_reuse_exception_has_reason() {
    let text = aw_toml();
    assert!(
        text.contains("[[standards.core_reuse.exception]]"),
        "aw.toml is missing [[standards.core_reuse.exception]]"
    );
    assert!(
        text.contains("check = \"CR5\""),
        "aw.toml is missing check = \"CR5\""
    );
    let reason = text
        .lines()
        .map(str::trim)
        .find_map(|l| l.strip_prefix("reason = \""))
        .unwrap_or_else(|| panic!("aw.toml is missing a reason = \" line"));
    let reason = reason.trim_end_matches('"');
    assert!(
        reason.chars().count() >= 20,
        "the CR5 reason is shorter than 20 characters: {reason:?}"
    );
}
