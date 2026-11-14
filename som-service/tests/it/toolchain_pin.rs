//! The root rust-toolchain.toml pins the toolchain that CI uses (issue #231).
use std::path::Path;

fn read_root(name: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join(name);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

/// The text between the quotes on the `channel = "..."` line.
fn pinned_channel(toolchain: &str) -> String {
    let line = toolchain
        .lines()
        .map(str::trim)
        .find(|l| l.starts_with("channel = "))
        .unwrap_or_else(|| panic!("rust-toolchain.toml has no channel = line"));
    let start = line
        .find('"')
        .unwrap_or_else(|| panic!("channel line has no quote: {line}"))
        + 1;
    let end = line
        .rfind('"')
        .unwrap_or_else(|| panic!("channel line has no closing quote: {line}"));
    line[start..end].to_string()
}

#[test]
fn root_toolchain_is_pinned_with_components() {
    let text = read_root("rust-toolchain.toml");
    let lines: Vec<&str> = text.lines().map(str::trim).collect();
    assert!(
        lines.contains(&"channel = \"1.96.1\""),
        "rust-toolchain.toml must pin channel = \"1.96.1\""
    );
    assert!(
        lines.contains(&"components = [\"rustfmt\", \"clippy\"]"),
        "rust-toolchain.toml must list components rustfmt and clippy"
    );
}

#[test]
fn root_toolchain_matches_ci() {
    let channel = pinned_channel(&read_root("rust-toolchain.toml"));
    let ci = read_root(".github/workflows/ci.yml");
    let values: Vec<&str> = ci
        .lines()
        .filter_map(|l| l.trim().strip_prefix("toolchain: "))
        .map(str::trim)
        .collect();
    assert!(
        !values.is_empty(),
        "ci.yml has no `toolchain: <version>` line"
    );
    for value in &values {
        assert_eq!(
            *value, channel,
            "ci.yml toolchain {value} differs from the rust-toolchain.toml channel {channel}"
        );
    }
}
