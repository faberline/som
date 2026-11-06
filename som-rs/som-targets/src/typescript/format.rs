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

/// These artifact pins are trusted source, not caller configuration.
#[cfg(all(target_os = "linux", target_arch = "x86_64", target_env = "gnu"))]
pub const NODE_PATH: &str = "/usr/local/bin/node";
#[cfg(all(target_os = "linux", target_arch = "x86_64", target_env = "gnu"))]
pub const NODE_SHA256: &str = "3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538";
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
pub const NODE_PATH: &str = "/opt/homebrew/Cellar/node/26.5.0/bin/node";
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
pub const NODE_SHA256: &str = "70851490e028b3d699a8d6d4e1de909af2a989359ae807974c92af9c6580a8e8";
#[cfg(not(any(
    all(target_os = "linux", target_arch = "x86_64", target_env = "gnu"),
    all(target_os = "macos", target_arch = "aarch64")
)))]
pub const NODE_PATH: &str = "";
#[cfg(not(any(
    all(target_os = "linux", target_arch = "x86_64", target_env = "gnu"),
    all(target_os = "macos", target_arch = "aarch64")
)))]
pub const NODE_SHA256: &str = "";
pub const LIBNODE_PATH: &str = "/opt/homebrew/Cellar/node/26.5.0/lib/libnode.147.dylib";
pub const LIBNODE_SHA256: &str = "0c2dfacde99b9381ec37fbd222891271c7f455cc0524294928e3e34199d57ec3";
pub const PRETTIER_TREE_SHA256: &str =
    "f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299";
pub const FORMATTER_ID: &str = "som.typescript-formatter.prettier-3.9.10.v1";
#[cfg(all(target_os = "linux", target_arch = "x86_64", target_env = "gnu"))]
pub const FORMATTER_SHA256: &str =
    "6070ceb6d781121f6175b7c3992f3aa58bd56f6d871e08f2ff426020bb30f50b";
#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
pub const FORMATTER_SHA256: &str =
    "636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8";
#[cfg(not(any(
    all(target_os = "linux", target_arch = "x86_64", target_env = "gnu"),
    all(target_os = "macos", target_arch = "aarch64")
)))]
pub const FORMATTER_SHA256: &str = "";
pub const PRETTIER_ENV: &str = "SOM_TS_PRETTIER";
pub const NODE_ENV: &str = "SOM_TS_NODE";

#[cfg(test)]
pub(crate) static TEST_ENV_LOCK: Mutex<()> = Mutex::new(());

/// Size, modification time, inode, device and symlink flag of a verified file or directory entry.
pub type Stamp = (u64, i64, i64, u64, u64, bool, i64, i64, u32);

#[derive(Clone, Debug, PartialEq, Eq)]
struct VerifiedFormatter {
    prettier_root: PathBuf,
    node_path: PathBuf,
    node: Stamp,
    libnode: Option<Stamp>,
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
        meta.ctime(),
        meta.ctime_nsec(),
        meta.mode(),
    ))
}

fn verify_file(p: &Path, expected: &str) -> Result<Stamp, String> {
    let path = p.display();
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
    selected_prettier_root(&raw)
}

fn selected_prettier_root(raw: &str) -> Result<PathBuf, String> {
    if raw.is_empty() {
        return Err(format!("{PRETTIER_ENV} is empty"));
    }
    let path = PathBuf::from(raw);
    if !path.is_absolute() {
        return Err(format!("{PRETTIER_ENV} must name an absolute directory"));
    }
    Ok(path)
}

fn selected_node_path(raw: Option<std::ffi::OsString>) -> Result<PathBuf, String> {
    if NODE_SHA256.is_empty() {
        return Err("no trusted native Node artifact is declared for this host".to_owned());
    }
    let path = raw.map_or_else(|| PathBuf::from(NODE_PATH), PathBuf::from);
    if path.as_os_str().is_empty() || !path.is_absolute() {
        return Err(format!("{NODE_ENV} must name an absolute executable path"));
    }
    Ok(path)
}

fn node_path_from_env() -> Result<PathBuf, String> {
    selected_node_path(std::env::var_os(NODE_ENV))
}

fn needs_libnode() -> bool {
    cfg!(all(target_os = "macos", target_arch = "aarch64"))
}

/// Verify the native Node artifact, its required library and the exact Prettier tree.
/// The environment can select an executable path, but never a trusted digest.
fn verified_installation() -> Result<VerifiedFormatter, String> {
    let prettier_root = prettier_root_from_env()?;
    let node_path = node_path_from_env()?;
    let current_library = if needs_libnode() {
        Some(stamp(Path::new(LIBNODE_PATH))?)
    } else {
        None
    };
    let mut guard = VERIFIED
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some(cached) = guard.as_ref()
        && cached.prettier_root == prettier_root
        && cached.node_path == node_path
        && stamp(&node_path).ok().as_ref() == Some(&cached.node)
        && current_library == cached.libnode
        && collect_tree_stamps(&prettier_root).ok().as_ref() == Some(&cached.tree)
    {
        return Ok(cached.clone());
    }
    *guard = None;
    let node = verify_file(&node_path, NODE_SHA256)?;
    let libnode = if needs_libnode() {
        Some(verify_file(Path::new(LIBNODE_PATH), LIBNODE_SHA256)?)
    } else {
        None
    };
    let (tree_sha, tree) = compute_tree_sha256(&prettier_root)?;
    if tree_sha != PRETTIER_TREE_SHA256 {
        return Err(format!(
            "prettier tree hash mismatch for {}: expected {PRETTIER_TREE_SHA256}, got {tree_sha}",
            prettier_root.display()
        ));
    }
    let verified = VerifiedFormatter {
        prettier_root,
        node_path,
        node,
        libnode,
        tree,
    };
    *guard = Some(verified.clone());
    Ok(verified)
}

/// Check the formatter before assembly admits a bundle.
pub fn verify() -> Result<PathBuf, String> {
    verified_installation().map(|verified| verified.prettier_root)
}

fn format_one(node: &Path, prettier_bin: &Path, path: &str, content: &str) -> Result<String, String> {
    let mut child = Command::new(node)
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
    let verified = verified_installation()?;
    let prettier_bin = verified.prettier_root.join("bin").join("prettier.cjs");
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
                let node = &verified.node_path;
                scope.spawn(move || {
                    let formatted = format_one(node, bin, path, content)?;
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    struct FixtureDirectory(PathBuf);

    impl FixtureDirectory {
        fn new() -> Self {
            static NEXT: AtomicU64 = AtomicU64::new(0);
            let path = std::env::temp_dir().join(format!(
                "som-node-admission-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).expect("new fixture directory");
            Self(path)
        }
    }

    impl Drop for FixtureDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn native_formatter_descriptor_names_the_actual_artifact() {
        if NODE_SHA256.is_empty() {
            assert!(selected_node_path(None).is_err());
            return;
        }
        let library = needs_libnode().then_some(LIBNODE_SHA256);
        let identity = json!({
            "node": NODE_SHA256,
            "libnode": library,
            "prettier_tree_sha256": PRETTIER_TREE_SHA256,
        });
        assert_eq!(descriptor()["sha256"], som::compiler::canonical_hash(&identity));
    }

    #[test]
    fn node_path_selection_refuses_empty_and_relative_paths() {
        for path in ["", "node", "relative/bin/node"] {
            assert!(selected_node_path(Some(path.into())).is_err());
        }
        if !NODE_SHA256.is_empty() {
            assert_eq!(selected_node_path(None).unwrap(), Path::new(NODE_PATH));
            assert_eq!(
                selected_node_path(Some("/native/tools/node".into())).unwrap(),
                Path::new("/native/tools/node")
            );
        }
    }

    #[test]
    fn prettier_tree_cannot_be_verified_relative_to_a_different_working_directory() {
        for path in ["", "prettier", "relative/node_modules/prettier"] {
            assert!(selected_prettier_root(path).is_err());
        }
        assert_eq!(selected_prettier_root("/native/prettier").unwrap(), Path::new("/native/prettier"));
    }

    #[test]
    fn executable_admission_refuses_changed_bytes_directories_and_links() {
        let directory = FixtureDirectory::new();
        let binary = directory.0.join("node");
        let original = b"fixture executable bytes";
        fs::write(&binary, original).unwrap();
        let pin = som::compiler::digest(original);
        verify_file(&binary, &pin).expect("unchanged bytes match the fixture pin");
        fs::write(&binary, b"changed executable bytes").unwrap();
        assert!(verify_file(&binary, &pin).unwrap_err().contains("hash mismatch"));
        assert!(verify_file(&directory.0, &pin).is_err());
        let link = directory.0.join("node-link");
        std::os::unix::fs::symlink(&binary, &link).unwrap();
        assert!(verify_file(&link, &pin).unwrap_err().contains("not a regular file"));
    }

    #[test]
    fn cache_identity_detects_same_length_rewrite_with_restored_mtime() {
        let directory = FixtureDirectory::new();
        let path = directory.0.join("node");
        fs::write(&path, b"before").unwrap();
        let modified = fs::metadata(&path).unwrap().modified().unwrap();
        let before = stamp(&path).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(2));
        fs::write(&path, b"after!").unwrap();
        fs::File::open(&path).unwrap().set_times(fs::FileTimes::new().set_modified(modified)).unwrap();
        assert_ne!(before, stamp(&path).unwrap());
    }
}
