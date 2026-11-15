//! The trimmed flow-m3-r9 training file is its first line only, and the
//! owner-approved amendment records the old and new values (issue #227).
use std::path::Path;

use sha2::{Digest, Sha256};

const TRAIN: &str = "../som-core/data/flow-m3-r9/train.jsonl";
const AMENDMENT: &str = "../som-rs/evidence/rust-training-data-trim-amendment-2026-11-15.json";
const CLEANUP: &str = "../som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json";
const NEW_BYTES: u64 = 13301;
const NEW_SHA256: &str = "6200f4164804eca205f72e99be95df711940fe0d6e74e60037754d5a7891f645";
const OLD_BYTES: u64 = 32833985;
const OLD_SHA256: &str = "73c3864270c3fb8e9887ce1b99571f37c9489459e3fe187ac5cd0cdf890fae0d";

fn repo_file(rel: &str) -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join(rel)
}

fn read(rel: &str) -> Vec<u8> {
    let path = repo_file(rel);
    std::fs::read(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

#[test]
fn flow_m3_r9_train_is_the_first_line_only() {
    let bytes = read(TRAIN);
    assert_eq!(
        bytes.len() as u64,
        NEW_BYTES,
        "train.jsonl must be 13301 bytes"
    );
    assert_eq!(
        bytes.iter().filter(|&&b| b == b'\n').count(),
        1,
        "train.jsonl must have exactly one line"
    );
    assert_eq!(
        bytes.last(),
        Some(&b'\n'),
        "train.jsonl must end with one newline"
    );
    assert_eq!(
        sha256_hex(&bytes),
        NEW_SHA256,
        "train.jsonl sha256 must match the amendment"
    );
}

#[test]
fn trim_amendment_records_old_and_new_values() {
    let text = String::from_utf8(read(AMENDMENT)).expect("amendment is UTF-8");
    let v: serde_json::Value = serde_json::from_str(&text).expect("amendment is JSON");
    assert_eq!(v["path"], "som-core/data/flow-m3-r9/train.jsonl");
    assert_eq!(v["old"]["bytes"], OLD_BYTES);
    assert_eq!(v["old"]["sha256"], OLD_SHA256);
    assert_eq!(v["new"]["bytes"], NEW_BYTES);
    assert_eq!(v["new"]["lines"], 1);
    let actual = sha256_hex(&read(TRAIN));
    assert_eq!(
        v["new"]["sha256"].as_str(),
        Some(actual.as_str()),
        "amendment new sha256 must equal the sha256 of the real file"
    );
}

#[test]
fn frozen_cleanup_evidence_is_unchanged() {
    let text = String::from_utf8(read(CLEANUP)).expect("cleanup evidence is UTF-8");
    assert!(
        text.contains(&format!("\"bytes\": {OLD_BYTES}")),
        "frozen cleanup evidence must still record the old bytes"
    );
    assert!(
        text.contains(OLD_SHA256),
        "frozen cleanup evidence must still record the old sha256"
    );
}
