//! The root README states CNL ownership (issues #184, #217).
use std::path::Path;

fn readme() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../README.md");
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn readme_states_cnl_ownership() {
    let text = readme();
    for needle in [
        "## CNL ownership",
        "som owns CNL syntax and CNL meaning",
        "som-service/cnl/",
        "A CNL syntax change is a som issue",
        "compiler-v1/grammar.ebnf",
        "frozen",
        "faberline-nlp-cnl",
    ] {
        assert!(text.contains(needle), "README lacks: {needle}");
    }
}

#[test]
fn readme_no_longer_routes_syntax_to_nlp() {
    let text = readme();
    for old in [
        "A syntax change starts as an nlp issue",
        "## CNL split with nlp",
        "pin different nlp revisions",
    ] {
        assert!(
            !text.contains(old),
            "README still routes CNL to nlp: {old:?}"
        );
    }
}

#[test]
fn readme_ownership_section_is_one_section_before_history() {
    let text = readme();
    let heading = "## CNL ownership";
    assert_eq!(
        text.matches(heading).count(),
        1,
        "README must contain {heading:?} exactly once"
    );
    let ownership = text.find(heading).expect("heading present");
    let current = text
        .find("## Current state")
        .expect("README has ## Current state");
    let history = text
        .find("## Historical model roadmap and retained legacy service")
        .expect("README has the historical roadmap heading");
    assert!(
        current < ownership,
        "CNL ownership section must come after ## Current state"
    );
    assert!(
        ownership < history,
        "CNL ownership section must come before the historical roadmap"
    );
}
