//! The root README states the CNL split with nlp (issue #184).
use std::path::Path;

fn readme() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../README.md");
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn readme_states_cnl_split() {
    let text = readme();
    for needle in [
        "## CNL split with nlp",
        "som owns CNL meaning",
        "faberline-nlp-cnl",
        "A syntax change starts as an nlp issue",
        "compiler-v1/grammar.ebnf",
        "frozen",
    ] {
        assert!(text.contains(needle), "README lacks: {needle}");
    }
}

#[test]
fn readme_split_section_is_one_section_before_history() {
    let text = readme();
    let heading = "## CNL split with nlp";
    assert_eq!(
        text.matches(heading).count(),
        1,
        "README must contain {heading:?} exactly once"
    );
    let split = text.find(heading).expect("heading present");
    let current = text
        .find("## Current state")
        .expect("README has ## Current state");
    let history = text
        .find("## Historical model roadmap and retained legacy service")
        .expect("README has the historical roadmap heading");
    assert!(
        current < split,
        "CNL split section must come after ## Current state"
    );
    assert!(
        split < history,
        "CNL split section must come before the historical roadmap"
    );
}
