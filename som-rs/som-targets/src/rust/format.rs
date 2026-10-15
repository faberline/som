//! The pinned rustfmt formatter from contracts/rust-output-v1/toolchain.json.
//!
//! The binary is verified by its sha256, and verified again whenever its size, modification
//! time or inode changes, so a replaced file is never run unchecked. A missing binary, a
//! different binary or a formatter failure is ASSET_UNAVAILABLE: the service never returns
//! unformatted Rust.
use std::io::Write;
use std::os::unix::fs::MetadataExt;
use std::process::{Command, Stdio};
use std::sync::Mutex;

pub const RUSTFMT: &str = "/opt/homebrew/Cellar/rust/1.96.1/bin/rustfmt";
pub const RUSTFMT_SHA256: &str = "2c56fd066853c80c786a8a3a0e5434b7f808ace8b2cf66ae6e1a13566aa2815f";
pub const FORMATTER_ID: &str = "som.rust-formatter.rustfmt-1.9.0.v1";

/// Size, modification time and inode of the last verified file.
type Stamp = (u64, i64, i64, u64, u64);

fn stamp() -> Result<Stamp, String> {
    let meta =
        std::fs::metadata(RUSTFMT).map_err(|error| format!("rustfmt is unavailable: {error}"))?;
    Ok((
        meta.len(),
        meta.mtime(),
        meta.mtime_nsec(),
        meta.ino(),
        meta.dev(),
    ))
}

/// The pinned binary path, or why it cannot be used.
fn verified() -> Result<&'static str, String> {
    static VERIFIED: Mutex<Option<Stamp>> = Mutex::new(None);
    let mut last = VERIFIED.lock().unwrap_or_else(|poison| poison.into_inner());
    let before = stamp()?;
    if *last != Some(before) {
        *last = None;
        let bytes =
            std::fs::read(RUSTFMT).map_err(|error| format!("rustfmt is unavailable: {error}"))?;
        if som::compiler::digest(&bytes) != RUSTFMT_SHA256 {
            return Err("rustfmt does not match the pinned sha256".to_owned());
        }
        if stamp()? != before {
            return Err("rustfmt changed while it was verified".to_owned());
        }
        *last = Some(before);
    }
    Ok(RUSTFMT)
}

/// Format one Rust source file with rustfmt defaults and edition 2024.
pub fn rustfmt(source: &str) -> Result<String, String> {
    let binary = verified()?;
    let mut child = Command::new(binary)
        .args([
            "--edition",
            "2024",
            "--emit",
            "stdout",
            "--config-path",
            "/dev/null",
        ])
        .current_dir("/")
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("rustfmt did not start: {error}"))?;
    let mut stdin = child.stdin.take().ok_or("rustfmt has no stdin")?;
    let input = source.to_owned();
    let writer = std::thread::spawn(move || stdin.write_all(input.as_bytes()));
    let output = child
        .wait_with_output()
        .map_err(|error| format!("rustfmt failed: {error}"))?;
    writer
        .join()
        .map_err(|_| "rustfmt input writer failed".to_owned())?
        .map_err(|error| format!("rustfmt input failed: {error}"))?;
    if !output.status.success() {
        return Err(format!(
            "rustfmt rejected generated source: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    String::from_utf8(output.stdout).map_err(|_| "rustfmt output is not UTF-8".to_owned())
}
