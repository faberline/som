//! The root README states the R1 Rust API mainline and the milestone order (issue #185).
use std::path::Path;

fn readme() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../README.md");
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn readme_names_r1_mainline() {
    let text = readme();
    for needle in [
        "R1 — Rust API",
        "som-service",
        "openapi-codegen",
        "service-http",
    ] {
        assert!(text.contains(needle), "README is missing {needle:?}");
    }
}

#[test]
fn readme_lists_milestones_in_order() {
    let text = readme();
    let milestones = [
        "R1 — Rust API 主線 (",
        "M6 — React 前端 (",
        "M9 — SRE (",
        "M10 — AI (",
        "M12 — Rust CI (",
    ];
    let mut last: Option<usize> = None;
    for milestone in milestones {
        let idx = text
            .find(milestone)
            .unwrap_or_else(|| panic!("README is missing milestone {milestone:?}"));
        if let Some(prev) = last {
            assert!(
                idx > prev,
                "milestone {milestone:?} is not after the previous milestone"
            );
        }
        last = Some(idx);
    }
    for version in [
        "som@0.6.0",
        "som@0.7.0",
        "som@0.8.0",
        "som@0.9.0",
        "som@0.10.0",
    ] {
        assert!(text.contains(version), "README is missing {version:?}");
    }
    for needle in ["frozen", "som-rs"] {
        assert!(text.contains(needle), "README is missing {needle:?}");
    }
}

#[test]
fn readme_drops_old_goal_table() {
    let text = readme();
    for old in [
        "SOM's end goal is code for four scenarios",
        "| Scenario | Python | Rust | TypeScript | React |",
        "FE starts with React only",
    ] {
        assert!(
            !text.contains(old),
            "README still contains the old goal text {old:?}"
        );
    }
}
