//! The CNL syntax crate is owned by this repository (issue #217).
use std::path::Path;

fn read(relative: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(relative);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

#[test]
fn cnl_is_a_local_path_dependency() {
    let manifest = read("Cargo.toml");
    assert!(
        manifest.contains("faberline-nlp-cnl = { path = \"cnl\" }"),
        "Cargo.toml must depend on faberline-nlp-cnl by local path"
    );
    assert!(
        !manifest.contains("faberline/nlp"),
        "Cargo.toml must not reference faberline/nlp"
    );
    let lock = read("Cargo.lock");
    assert!(
        !lock.contains("github.com/faberline/nlp"),
        "Cargo.lock must not reference github.com/faberline/nlp"
    );
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("cnl");
    assert!(root.join("Cargo.toml").is_file(), "cnl/Cargo.toml exists");
    assert!(
        root.join("src/som_cnl.pest").is_file(),
        "cnl/src/som_cnl.pest exists"
    );
}

#[test]
fn deny_toml_has_no_nlp_git_source() {
    let deny = read("deny.toml");
    assert!(
        !deny.contains("faberline/nlp"),
        "deny.toml must not allow the faberline/nlp git source"
    );
}

#[test]
fn moved_crate_keeps_its_api_versions_and_sources() {
    assert_eq!(faberline_nlp_cnl::SYNTAX_API_VERSION, 2);
    assert_eq!(faberline_nlp_cnl::SERVICE_SYNTAX_API_VERSION, 1);
    assert_eq!(faberline_nlp_cnl::SOURCE_FILES.len(), 12);
    let names: Vec<&str> = faberline_nlp_cnl::SOURCE_FILES
        .iter()
        .map(|(name, _)| *name)
        .collect();
    assert!(
        names.contains(&"Cargo.toml"),
        "SOURCE_FILES lists Cargo.toml"
    );
    assert!(
        names.contains(&"src/som_cnl.pest"),
        "SOURCE_FILES lists src/som_cnl.pest"
    );
}
