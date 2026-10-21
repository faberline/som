//! Pinned prettier 3.9.10 formatter for TypeScript products (contracts/typescript-output-v1/toolchain.json).
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::Mutex,
};

pub const NODE_PATH: &str = "/opt/homebrew/Cellar/node/26.5.0/bin/node";
pub const NODE_SHA256: &str = "70851490e028b3d699a8d6d4e1de909af2a989359ae807974c92af9c6580a8e8";
pub const LIBNODE_PATH: &str = "/opt/homebrew/Cellar/node/26.5.0/lib/libnode.147.dylib";
pub const LIBNODE_SHA256: &str = "0c2dfacde99b9381ec37fbd222891271c7f455cc0524294928e3e34199d57ec3";
pub const PRETTIER_TREE_SHA256: &str =
    "f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299";
pub const FORMATTER_ID: &str = "som.typescript-formatter.prettier-3.9.10.v1";
pub const FORMATTER_SHA256: &str =
    "636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8";
pub const PRETTIER_ENV: &str = "SOM_TS_PRETTIER";

#[cfg(test)]
pub(crate) static TEST_ENV_LOCK: Mutex<()> = Mutex::new(());

/// Size, modification time, inode, device and symlink flag of a verified file or directory entry.
pub type Stamp = (u64, i64, i64, u64, u64, bool);

#[derive(Clone, Debug, PartialEq, Eq)]
struct VerifiedFormatter {
    prettier_root: PathBuf,
    node: Stamp,
    libnode: Stamp,
    tree: Vec<(PathBuf, Stamp)>,
}

static VERIFIED: Mutex<Option<VerifiedFormatter>> = Mutex::new(None);

/// Asset descriptor for the pinned TypeScript formatter in a som.dsl-bundle.v2 bundle.
pub fn descriptor() -> Value {
    json!({"id": FORMATTER_ID, "sha256": FORMATTER_SHA256})
}

fn stamp(path: &Path) -> Result<Stamp, String> {
    let meta =
        fs::symlink_metadata(path).map_err(|e| format!("cannot stat {}: {e}", path.display()))?;
    Ok((
        meta.len(),
        meta.mtime(),
        meta.mtime_nsec(),
        meta.ino(),
        meta.dev(),
        meta.file_type().is_symlink(),
    ))
}

fn verify_file(path: &str, expected: &str) -> Result<Stamp, String> {
    let p = Path::new(path);
    let before = stamp(p)?;
    if before.5 {
        return Err(format!("{path} is not a regular file"));
    }
    let meta = fs::symlink_metadata(p).map_err(|e| format!("cannot stat {path}: {e}"))?;
    if !meta.file_type().is_file() {
        return Err(format!("{path} is not a regular file"));
    }
    let bytes = fs::read(p).map_err(|e| format!("cannot read {path}: {e}"))?;
    let actual = som::compiler::digest(&bytes);
    if actual != expected {
        return Err(format!(
            "hash mismatch for {path}: expected {expected}, got {actual}"
        ));
    }
    if stamp(p)? != before {
        return Err(format!("{path} changed while it was verified"));
    }
    Ok(before)
}

fn collect_tree_paths(root: &Path, current: &Path, out: &mut Vec<PathBuf>) -> Result<(), String> {
    let entries = fs::read_dir(current)
        .map_err(|e| format!("cannot read directory {}: {e}", current.display()))?;
    for entry in entries {
        let entry = entry
            .map_err(|e| format!("cannot read directory entry in {}: {e}", current.display()))?;
        let path = entry.path();
        let rel = path
            .strip_prefix(root)
            .map_err(|e| format!("strip_prefix failed for {}: {e}", path.display()))?
            .to_path_buf();
        let file_type = entry
            .file_type()
            .map_err(|e| format!("cannot read file type for {}: {e}", path.display()))?;
        out.push(rel);
        if file_type.is_dir() {
            collect_tree_paths(root, &path, out)?;
        }
    }
    Ok(())
}

fn collect_tree_stamps(root: &Path) -> Result<Vec<(PathBuf, Stamp)>, String> {
    let meta = fs::symlink_metadata(root)
        .map_err(|e| format!("cannot stat prettier directory {}: {e}", root.display()))?;
    if !meta.file_type().is_dir() {
        return Err(format!(
            "prettier path {} is not a directory",
            root.display()
        ));
    }
    let mut rels = Vec::new();
    collect_tree_paths(root, root, &mut rels)?;
    rels.sort();
    let mut stamps = Vec::with_capacity(rels.len());
    for rel in rels {
        let full = root.join(&rel);
        stamps.push((rel, stamp(&full)?));
    }
    Ok(stamps)
}

/// Compute the tree SHA-256 of `root` using the frozen rule in
/// `som-rs/tests/typescript-output-v1/tree_sha.py`.
pub fn compute_tree_sha256(root: &Path) -> Result<(String, Vec<(PathBuf, Stamp)>), String> {
    let stamps = collect_tree_stamps(root)?;
    let mut lines = String::new();
    for (rel, _) in &stamps {
        let full = root.join(rel);
        let rel_posix = rel
            .components()
            .map(|c| {
                c.as_os_str().to_str().ok_or_else(|| {
                    format!("non-UTF-8 path under {}: {}", root.display(), rel.display())
                })
            })
            .collect::<Result<Vec<_>, _>>()?
            .join("/");
        let meta = fs::symlink_metadata(&full)
            .map_err(|e| format!("cannot stat {}: {e}", full.display()))?;
        let ft = meta.file_type();
        if ft.is_symlink() {
            let target = fs::read_link(&full)
                .map_err(|e| format!("cannot read symlink {}: {e}", full.display()))?;
            let target_str = target
                .to_str()
                .ok_or_else(|| format!("non-UTF-8 symlink target at {}", full.display()))?;
            lines.push_str(&format!("{rel_posix}\0symlink:{target_str}\n"));
        } else if ft.is_file() {
            let bytes =
                fs::read(&full).map_err(|e| format!("cannot read {}: {e}", full.display()))?;
            let sha = som::compiler::digest(&bytes);
            lines.push_str(&format!("{rel_posix}\0{sha}\n"));
        } else if ft.is_dir() {
            // Directories add nothing to the tree digest.
        } else {
            return Err(format!(
                "unsupported entry type in prettier tree at {}",
                full.display()
            ));
        }
    }
    if collect_tree_stamps(root)? != stamps {
        return Err(format!(
            "prettier directory {} changed while it was verified",
            root.display()
        ));
    }
    Ok((som::compiler::digest(lines.as_bytes()), stamps))
}

fn prettier_root_from_env() -> Result<PathBuf, String> {
    let raw = std::env::var(PRETTIER_ENV).map_err(|_| format!("{PRETTIER_ENV} is not set"))?;
    if raw.is_empty() {
        return Err(format!("{PRETTIER_ENV} is empty"));
    }
    Ok(PathBuf::from(raw))
}

/// Verify the pinned Node binary, libnode dylib, and SOM_TS_PRETTIER tree hash.
/// Re-verifies whenever SOM_TS_PRETTIER or any file/directory stamp changes.
pub fn verify() -> Result<PathBuf, String> {
    let prettier_root = prettier_root_from_env()?;
    let mut guard = VERIFIED
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some(cached) = guard.as_ref()
        && cached.prettier_root == prettier_root
        && stamp(Path::new(NODE_PATH)).ok().as_ref() == Some(&cached.node)
        && stamp(Path::new(LIBNODE_PATH)).ok().as_ref() == Some(&cached.libnode)
        && collect_tree_stamps(&prettier_root).ok().as_ref() == Some(&cached.tree)
    {
        return Ok(prettier_root);
    }
    *guard = None;
    let node = verify_file(NODE_PATH, NODE_SHA256)?;
    let libnode = verify_file(LIBNODE_PATH, LIBNODE_SHA256)?;
    let (tree_sha, tree) = compute_tree_sha256(&prettier_root)?;
    if tree_sha != PRETTIER_TREE_SHA256 {
        return Err(format!(
            "prettier tree hash mismatch for {}: expected {PRETTIER_TREE_SHA256}, got {tree_sha}",
            prettier_root.display()
        ));
    }
    *guard = Some(VerifiedFormatter {
        prettier_root: prettier_root.clone(),
        node,
        libnode,
        tree,
    });
    Ok(prettier_root)
}

fn format_one(prettier_bin: &Path, path: &str, content: &str) -> Result<String, String> {
    let mut child = Command::new(NODE_PATH)
        .arg(prettier_bin)
        .arg("--stdin-filepath")
        .arg(path)
        .arg("--no-config")
        .arg("--no-editorconfig")
        .current_dir("/")
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("cannot spawn node prettier for {path}: {e}"))?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(content.as_bytes())
            .map_err(|e| format!("cannot write {path} to prettier stdin: {e}"))?;
    }
    let output = child
        .wait_with_output()
        .map_err(|e| format!("cannot wait on prettier for {path}: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "prettier failed on {path}: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    let formatted = String::from_utf8(output.stdout)
        .map_err(|e| format!("prettier output for {path} is not UTF-8: {e}"))?;
    if formatted.is_empty() {
        return Err(format!("prettier produced empty output for {path}"));
    }
    Ok(formatted)
}

/// Format every `.ts` file in `files` through the verified prettier 3.9.10 installation.
pub fn prettier(files: &mut BTreeMap<String, String>) -> Result<(), String> {
    let prettier_root = verify()?;
    let prettier_bin = prettier_root.join("bin").join("prettier.cjs");
    let ts_entries: Vec<(String, String)> = files
        .iter()
        .filter(|(path, _)| path.ends_with(".ts"))
        .map(|(path, content)| (path.clone(), content.clone()))
        .collect();
    let results: Vec<Result<(String, String), String>> = std::thread::scope(|scope| {
        let handles: Vec<_> = ts_entries
            .iter()
            .map(|(path, content)| {
                let bin = &prettier_bin;
                scope.spawn(move || {
                    let formatted = format_one(bin, path, content)?;
                    Ok((path.clone(), formatted))
                })
            })
            .collect();
        handles
            .into_iter()
            .map(|h| {
                h.join()
                    .unwrap_or_else(|_| Err("prettier worker thread panicked".to_owned()))
            })
            .collect()
    });
    for result in results {
        let (path, formatted) = result?;
        files.insert(path, formatted);
    }
    Ok(())
}
