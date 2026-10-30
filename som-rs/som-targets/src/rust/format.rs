//! The pinned native rustfmt formatter from contracts/native-tools-v1.
//!
//! The binary is verified by its sha256, and verified again whenever its size, modification
//! time or inode changes, so a replaced file is never run unchecked. A missing binary, a
//! different binary or a formatter failure is ASSET_UNAVAILABLE: the service never returns
//! unformatted Rust.
use std::io::Write;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;

pub const RUSTFMT: &str = "/opt/homebrew/Cellar/rust/1.96.1/bin/rustfmt";
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
pub const RUSTFMT_SHA256: &str = "2c56fd066853c80c786a8a3a0e5434b7f808ace8b2cf66ae6e1a13566aa2815f";
#[cfg(all(target_os = "linux", target_arch = "x86_64", target_env = "gnu"))]
pub const RUSTFMT_SHA256: &str = "dbb30c6037c51a2bacb94d46e2d6435d9487531c06cf82330fdee0bda507db0d";
#[cfg(not(any(
    all(target_os = "macos", target_arch = "aarch64"),
    all(target_os = "linux", target_arch = "x86_64", target_env = "gnu")
)))]
pub const RUSTFMT_SHA256: &str = "";
pub const FORMATTER_ID: &str = "som.rust-formatter.rustfmt-1.9.0.v1";

/// Size, modification time and inode of the last verified file.
type Stamp = (u64, i64, i64, u64, u64, i64, i64, u32);

fn stamp(path: &Path) -> Result<Stamp, String> {
    let meta =
        std::fs::symlink_metadata(path).map_err(|error| format!("rustfmt is unavailable: {error}"))?;
    if !meta.is_file() {
        return Err("rustfmt must be a regular file, not a symlink".to_owned());
    }
    Ok((
        meta.len(),
        meta.mtime(),
        meta.mtime_nsec(),
        meta.ino(),
        meta.dev(),
        meta.ctime(),
        meta.ctime_nsec(),
        meta.mode(),
    ))
}

/// The pinned binary path, or why it cannot be used.
fn verified() -> Result<PathBuf, String> {
    if RUSTFMT_SHA256.is_empty() {
        return Err("native rustfmt is not pinned for this target".to_owned());
    }
    let path = match std::env::var_os("SOM_RUSTFMT") {
        Some(value) if !value.is_empty() => PathBuf::from(value),
        Some(_) => return Err("SOM_RUSTFMT is empty".to_owned()),
        None if cfg!(all(target_os = "macos", target_arch = "aarch64")) => PathBuf::from(RUSTFMT),
        None => return Err("SOM_RUSTFMT must select the pinned native rustfmt".to_owned()),
    };
    verify_path(&path)?;
    Ok(path)
}

fn verify_path(path: &Path) -> Result<(), String> {
    if !path.is_absolute() {
        return Err("SOM_RUSTFMT must be an absolute path".to_owned());
    }
    static VERIFIED: Mutex<Option<(PathBuf, Stamp)>> = Mutex::new(None);
    let mut last = VERIFIED.lock().unwrap_or_else(|poison| poison.into_inner());
    let before = stamp(path)?;
    if last.as_ref() != Some(&(path.to_path_buf(), before)) {
        *last = None;
        let bytes =
            std::fs::read(path).map_err(|error| format!("rustfmt is unavailable: {error}"))?;
        if som::compiler::digest(&bytes) != RUSTFMT_SHA256 {
            return Err("rustfmt does not match the pinned sha256".to_owned());
        }
        if stamp(path)? != before {
            return Err("rustfmt changed while it was verified".to_owned());
        }
        *last = Some((path.to_path_buf(), before));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_explicit_path_cannot_supply_a_different_formatter() {
        let dir = std::env::temp_dir().join(format!("som-rustfmt-pin-{}", std::process::id()));
        std::fs::create_dir(&dir).expect("new fixture directory");
        let binary = dir.join("rustfmt");
        std::fs::write(&binary, b"an unapproved executable").unwrap();
        let result = verify_path(&binary);
        let link = dir.join("link");
        std::os::unix::fs::symlink(&binary, &link).unwrap();
        let linked = verify_path(&link);
        std::fs::remove_dir_all(&dir).unwrap();
        assert!(result.unwrap_err().contains("pinned sha256"));
        assert!(linked.unwrap_err().contains("regular file"));
        assert!(verify_path(Path::new("relative/rustfmt")).is_err());
    }

    #[test]
    fn cache_identity_detects_same_length_rewrite_with_restored_mtime() {
        let path = std::env::temp_dir().join(format!("som-rustfmt-stamp-{}", std::process::id()));
        std::fs::write(&path, b"before").unwrap();
        let modified = std::fs::metadata(&path).unwrap().modified().unwrap();
        let before = stamp(&path).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(2));
        std::fs::write(&path, b"after!").unwrap();
        std::fs::File::open(&path).unwrap().set_times(std::fs::FileTimes::new().set_modified(modified)).unwrap();
        let after = stamp(&path).unwrap();
        std::fs::remove_file(&path).unwrap();
        assert_ne!(before, after);
    }
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
