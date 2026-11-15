//! No developer machine path in the files of som-service.

use std::path::{Path, PathBuf};

const MACHINE_PATH: &str = concat!("/Us", "ers/");
const PLACEHOLDER: &str = concat!("/Us", "ers/you");

fn collect(dir: &Path, out: &mut Vec<PathBuf>) {
    let entries = std::fs::read_dir(dir).unwrap_or_else(|e| panic!("read {}: {e}", dir.display()));
    for entry in entries {
        let path = entry.expect("directory entry").path();
        if path.is_dir() {
            if path.file_name().and_then(|n| n.to_str()) == Some("target") {
                continue;
            }
            collect(&path, out);
        } else {
            out.push(path);
        }
    }
}

#[test]
fn service_files_have_no_machine_path() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut files = Vec::new();
    collect(root, &mut files);

    let mut hits = Vec::new();
    for path in files {
        let Ok(bytes) = std::fs::read(&path) else {
            continue;
        };
        let Ok(text) = String::from_utf8(bytes) else {
            continue;
        };
        for (index, line) in text.lines().enumerate() {
            if line.contains(MACHINE_PATH) && !line.contains(PLACEHOLDER) {
                let rel = path.strip_prefix(root).unwrap_or(&path);
                hits.push(format!("{}:{}", rel.display(), index + 1));
            }
        }
    }

    assert!(hits.is_empty(), "machine path found in: {hits:?}");
}
