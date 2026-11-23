//! The reference tests under `reference/order/tests/it/` hold none of the
//! three texts that the emitted-test check bans in an emitted `.rs` file.
//! Each banned text is built from two parts so that this file is clean too.

use std::path::{Path, PathBuf};

const EXPECT_CALL: &str = concat!(".exp", "ect(");
const UNWRAP_CALL: &str = concat!(".unw", "rap()");
const UNCHECKED_KEYWORD: &str = concat!("uns", "afe");

#[test]
fn reference_tests_hold_no_banned_text() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("reference/order/tests/it");
    let mut files: Vec<PathBuf> = std::fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("read {}: {e}", dir.display()))
        .map(|entry| entry.unwrap_or_else(|e| panic!("read entry: {e}")).path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "rs"))
        .collect();
    files.sort();
    assert_eq!(
        files.len(),
        18,
        "reference/order/tests/it must hold 18 .rs files"
    );

    for path in &files {
        let text = std::fs::read_to_string(path)
            .unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
        let name = path.display();
        for banned in [EXPECT_CALL, UNWRAP_CALL, UNCHECKED_KEYWORD] {
            assert!(!text.contains(banned), "{name}: {banned} in source");
        }
    }
}
