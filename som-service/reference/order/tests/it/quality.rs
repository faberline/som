//! Decisions 34 to 36 (issue #295): core is pinned to a tag and `Cargo.lock` is
//! present; the crate forbids unchecked code blocks and `unwrap`; each operation has its
//! success and error tests and pagination has a property test. Static checks only.
use crate::fixture::Must;
use std::path::{Path, PathBuf};

const CRATE: &str = env!("CARGO_MANIFEST_DIR");
// Written in two parts so that this file holds none of the banned text.
const UNWRAP_CALL: &str = concat!(".unw", "rap()");
const UNCHECKED_KEYWORD: &str = concat!("uns", "afe");
const FORBID_LINE: &str = concat!("uns", "afe_code = \"forbid\"");

fn read(path: &Path) -> String {
    std::fs::read_to_string(path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

/// True when `word` appears in `line` as a whole word.
fn has_word(line: &str, word: &str) -> bool {
    line.split(|c: char| !(c.is_alphanumeric() || c == '_'))
        .any(|w| w == word)
}

/// Every `.rs` file under `dir`, recursively.
fn collect_rs(dir: &Path, out: &mut Vec<PathBuf>) {
    let entries =
        std::fs::read_dir(dir).unwrap_or_else(|e| panic!("read dir {}: {e}", dir.display()));
    for entry in entries {
        let path = entry.must("read dir entry").path();
        if path.is_dir() {
            collect_rs(&path, out);
        } else if path.extension().is_some_and(|e| e == "rs") {
            out.push(path);
        }
    }
}

#[test]
fn core_is_pinned_to_a_tag_and_cargo_lock_is_present() {
    let root = Path::new(CRATE).join("../..");
    assert!(
        root.join("Cargo.lock").exists(),
        "som-service/Cargo.lock is missing"
    );
    for manifest in [root.join("Cargo.toml"), Path::new(CRATE).join("Cargo.toml")] {
        let text = read(&manifest);
        let core: Vec<&str> = text
            .lines()
            .filter(|l| l.contains("github.com/faberline/core"))
            .collect();
        assert!(
            !core.is_empty(),
            "{} has no core dependency",
            manifest.display()
        );
        for line in core {
            assert!(
                line.contains("tag = \"v") && !line.contains("rev =") && !line.contains("branch ="),
                "{}: core is not pinned to a tag: {line}",
                manifest.display()
            );
        }
    }
}

#[test]
fn fmt_clippy_clean_no_unwrap_no_forbidden_code() {
    let manifest = read(&Path::new(CRATE).join("Cargo.toml"));
    assert!(
        manifest.lines().any(|l| l.trim() == FORBID_LINE),
        "Cargo.toml must have the forbid line"
    );
    assert!(
        manifest
            .lines()
            .any(|l| l.trim() == "unwrap_used = \"deny\""),
        "Cargo.toml must deny unwrap_used"
    );
    let mut files = Vec::new();
    collect_rs(&Path::new(CRATE).join("src"), &mut files);
    assert!(!files.is_empty(), "no source files under src/");
    for file in files {
        for (i, line) in read(&file).lines().enumerate() {
            let t = line.trim();
            if t.starts_with("//") {
                continue;
            }
            assert!(
                !t.contains(UNWRAP_CALL),
                "{}:{}: {UNWRAP_CALL} in source",
                file.display(),
                i + 1
            );
            assert!(
                !has_word(t, UNCHECKED_KEYWORD),
                "{}:{}: {UNCHECKED_KEYWORD} in source",
                file.display(),
                i + 1
            );
        }
    }
}

#[test]
fn each_operation_has_success_and_error_tests_and_pagination_property_test() {
    let dir = Path::new(CRATE).join("tests/it");
    let entries =
        std::fs::read_dir(&dir).unwrap_or_else(|e| panic!("read dir {}: {e}", dir.display()));
    let mut text = String::new();
    for entry in entries {
        let path = entry.must("read dir entry").path();
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or_default();
        let is_self = name == "quality.rs" || name == "readme.rs";
        if path.extension().is_some_and(|e| e == "rs") && !is_self {
            text.push_str(&read(&path));
        }
    }
    let names = [
        "create_returns_201_location_and_full_resource",
        "read_returns_200_and_unknown_id_returns_404",
        "list_returns_200_with_standard_pagination",
        "replace_returns_200_with_full_resource_and_no_patch_route",
        "delete_returns_204_and_second_delete_returns_404",
        "unknown_field_is_refused_and_bad_fields_give_422_with_one_entry_each",
        "etag_is_returned_and_if_match_mismatch_412_missing_428",
        "body_over_1_mib_returns_413",
        "default_limit_50_max_200_and_larger_limit_returns_400",
        "bad_cursor_returns_400_and_cursor_is_opaque",
        "idempotency_key_same_body_same_result_different_body_409_and_expires_after_24h",
    ];
    for name in names {
        assert!(
            text.contains(&format!("fn {name}(")),
            "no test `{name}` in tests/it"
        );
    }
    assert!(
        text.contains("fn pagination_walk_gives_all_items_once("),
        "no pagination walk property test in tests/it"
    );
    assert!(text.contains("proptest!"), "no proptest! block in tests/it");
}
