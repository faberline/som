use std::{fs, path::Path};

fn service_path(relative: &str) -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join(relative)
}

fn read(relative: &str) -> String {
    fs::read_to_string(service_path(relative))
        .unwrap_or_else(|error| panic!("cannot read {relative}: {error}"))
}

#[test]
fn docs_do_not_name_run_py() {
    assert!(
        !service_path("acceptance/run.py").exists(),
        "acceptance/run.py must be deleted"
    );
    assert!(
        !read("acceptance/README.md").contains("run.py"),
        "acceptance/README.md must not name run.py"
    );
    assert!(
        !read("README.md").contains("run.py"),
        "README.md must not name run.py"
    );
}

#[test]
fn acceptance_readme_keeps_case_counts() {
    assert!(
        read("acceptance/README.md").contains(
            "35 native HTTP cases, 10 native path edge cases, 105 generated-client cases and 48 captured requests"
        ),
        "acceptance/README.md must keep the case-count sentence on one line"
    );
}

#[test]
fn replay_script_is_gone() {
    assert!(
        !service_path("scripts/replay.py").exists(),
        "scripts/replay.py was deleted with the old areas (som#265)"
    );
}
