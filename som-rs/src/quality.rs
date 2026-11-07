//! Native, in-memory formatting. No Python process is used here.
use crate::SomError;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

pub const VERSION: &str = "som.python-quality.v1";
pub const FORMATTER_VERSION: &str = "ruff 0.16.6";
pub const PROFILE_TEXT: &str = include_str!("../contracts/python-quality-v1/pyproject.toml");
pub const CATALOG_TEXT: &str =
    include_str!("../../som-code-python/data/block-contracts/composed-quality.json");

fn failed(detail: impl Into<String>) -> SomError {
    SomError::new("OUTPUT_QUALITY_FAILED", detail)
}

// A native header alone never admits a tool: its digest and exact version are also checked.
fn native_formatter(bytes: &[u8]) -> bool {
    if cfg!(all(target_os = "linux", target_arch = "x86_64", target_env = "gnu")) {
        bytes.get(..7) == Some(&[0x7f, b'E', b'L', b'F', 2, 1, 1])
            && (bytes.get(16..18) == Some(&[2, 0]) || bytes.get(16..18) == Some(&[3, 0]))
            && bytes.get(18..20) == Some(&[62, 0])
    } else if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        bytes.get(..4) == Some(&[0xcf, 0xfa, 0xed, 0xfe])
            && bytes.get(4..8) == Some(&[0x0c, 0, 0, 1])
    } else {
        false
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct AssetIdentity {
    #[cfg(unix)]
    fields: (u64, u64, u64, i64, i64, i64, i64, u32),
    #[cfg(not(unix))]
    fields: (u64, Option<std::time::SystemTime>),
}

impl AssetIdentity {
    fn from_metadata(meta: &std::fs::Metadata) -> Self {
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            Self {
                fields: (
                    meta.dev(),
                    meta.ino(),
                    meta.len(),
                    meta.mtime(),
                    meta.mtime_nsec(),
                    meta.ctime(),
                    meta.ctime_nsec(),
                    meta.mode(),
                ),
            }
        }
        #[cfg(not(unix))]
        {
            Self {
                fields: (meta.len(), meta.modified().ok()),
            }
        }
    }
    fn at(path: &Path) -> Result<Self, SomError> {
        let meta = std::fs::symlink_metadata(path).map_err(|e| failed(e.to_string()))?;
        if !meta.is_file() {
            return Err(failed("quality assets must be regular files, not symlinks"));
        }
        Ok(Self::from_metadata(&meta))
    }
}

fn read_asset(path: &Path) -> Result<(Vec<u8>, AssetIdentity), SomError> {
    let entry = std::fs::symlink_metadata(path).map_err(|e| failed(e.to_string()))?;
    if !entry.is_file() {
        return Err(failed("quality assets must be regular files, not symlinks"));
    }
    let mut file = std::fs::File::open(path).map_err(|e| failed(e.to_string()))?;
    let identity =
        AssetIdentity::from_metadata(&file.metadata().map_err(|e| failed(e.to_string()))?);
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)
        .map_err(|e| failed(e.to_string()))?;
    if identity
        != AssetIdentity::from_metadata(&file.metadata().map_err(|e| failed(e.to_string()))?)
        || identity != AssetIdentity::at(path)?
    {
        return Err(failed("quality asset changed while loading"));
    }
    Ok((bytes, identity))
}

#[derive(Clone)]
pub struct Quality {
    pub binary: PathBuf,
    pub profile: PathBuf,
    pub catalog: Value,
    binary_identity: AssetIdentity,
    profile_identity: AssetIdentity,
}

impl Quality {
    pub fn load(directory: &Path) -> Result<Option<Self>, SomError> {
        Self::load_checked(directory, None)
    }

    /// The compiler supplies its built-in approved native formatter digest.
    pub fn load_pinned(
        directory: &Path,
        expected_formatter_sha256: &str,
    ) -> Result<Option<Self>, SomError> {
        Self::load_checked(directory, Some(expected_formatter_sha256))
    }

    fn load_checked(
        directory: &Path,
        expected_formatter_sha256: Option<&str>,
    ) -> Result<Option<Self>, SomError> {
        let path = directory.join("quality.json");
        if !path.exists() {
            return Ok(None);
        }
        let (config_bytes, _) = read_asset(&path)?;
        let config: Value =
            serde_json::from_slice(&config_bytes).map_err(|e| failed(e.to_string()))?;
        let binary = directory.join("ruff");
        let profile = directory.join("pyproject.toml");
        let (binary_bytes, binary_identity) = read_asset(&binary)?;
        let (profile_bytes, profile_identity) = read_asset(&profile)?;
        let binary_hash = format!("{:x}", Sha256::digest(&binary_bytes));
        if config["formatter_sha256"] != binary_hash
            || config["profile_sha256"] != format!("{:x}", Sha256::digest(&profile_bytes))
            || expected_formatter_sha256.is_some_and(|expected| expected != binary_hash)
        {
            return Err(failed("quality asset digest does not match"));
        }
        if config["id"] != VERSION
            || config["formatter_version"] != FORMATTER_VERSION
            || profile_bytes != PROFILE_TEXT.as_bytes()
            || !native_formatter(&binary_bytes)
        {
            return Err(failed("quality profile or native formatter does not match"));
        }
        let state = Self {
            binary,
            profile,
            binary_identity,
            profile_identity,
            catalog: serde_json::from_str(CATALOG_TEXT).map_err(|e| failed(e.to_string()))?,
        };
        if state.execute(&["--version".into()], "")?.trim() != FORMATTER_VERSION {
            return Err(failed("formatter version does not match"));
        }
        Ok(Some(state))
    }

    fn verify_loaded_assets(&self) -> Result<(), SomError> {
        if AssetIdentity::at(&self.binary)? != self.binary_identity
            || AssetIdentity::at(&self.profile)? != self.profile_identity
        {
            return Err(failed("quality assets changed after admission"));
        }
        Ok(())
    }

    fn execute(&self, args: &[String], input: &str) -> Result<String, SomError> {
        self.verify_loaded_assets()?;
        let mut child = Command::new(&self.binary)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| failed(e.to_string()))?;
        let mut stdin = child.stdin.take().unwrap();
        let mut stdout = child.stdout.take().unwrap();
        let mut stderr = child.stderr.take().unwrap();
        let bytes = input.as_bytes().to_vec();
        let writer = std::thread::spawn(move || stdin.write_all(&bytes));
        let reader = std::thread::spawn(move || {
            let mut data = Vec::new();
            stdout
                .by_ref()
                .take(4 * 1024 * 1024)
                .read_to_end(&mut data)
                .map(|_| data)
        });
        let errors = std::thread::spawn(move || {
            let mut data = Vec::new();
            stderr
                .by_ref()
                .take(4 * 1024 * 1024)
                .read_to_end(&mut data)
                .map(|_| data)
        });
        let started = Instant::now();
        let status = loop {
            if let Some(status) = child.try_wait().map_err(|e| failed(e.to_string()))? {
                break Some(status);
            }
            if started.elapsed() > Duration::from_secs(10) {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
            std::thread::sleep(Duration::from_millis(5));
        };
        let written = writer
            .join()
            .map_err(|_| failed("formatter input worker failed"))?;
        let out = reader
            .join()
            .map_err(|_| failed("formatter output worker failed"))?
            .map_err(|e| failed(e.to_string()))?;
        let err = errors
            .join()
            .map_err(|_| failed("formatter error worker failed"))?
            .map_err(|e| failed(e.to_string()))?;
        self.verify_loaded_assets()?;
        let Some(status) = status else {
            return Err(failed("formatter timed out"));
        };
        written.map_err(|e| failed(e.to_string()))?;
        if !status.success() {
            return Err(failed(format!(
                "formatter rejected output: {}{}",
                String::from_utf8_lossy(&out),
                String::from_utf8_lossy(&err)
            )));
        }
        String::from_utf8(out).map_err(|e| failed(e.to_string()))
    }

    pub fn format_source(
        &self,
        path: &str,
        source: &str,
        package: &str,
    ) -> Result<String, SomError> {
        let common = [
            "--config".into(),
            self.profile.to_string_lossy().into_owned(),
            "--no-cache".into(),
            "--stdin-filename".into(),
            path.into(),
            "--config".into(),
            format!(
                "lint.isort.known-first-party={}",
                serde_json::to_string(&vec![package]).unwrap()
            ),
        ];
        let mut sorted = vec!["check".into()];
        sorted.extend(common.clone());
        sorted.extend(["--select".into(), "I".into(), "--fix".into(), "-".into()]);
        let source = self.execute(&sorted, source)?;
        let mut formatted = vec!["format".into()];
        formatted.extend(common.clone());
        formatted.push("-".into());
        let source = self.execute(&formatted, &source)?;
        let mut checked = vec!["check".into()];
        checked.extend(common);
        checked.extend(["--output-format".into(), "json".into(), "-".into()]);
        self.execute(&checked, &source)?;
        Ok(source)
    }

    pub fn finalize(
        &self,
        files: BTreeMap<String, String>,
    ) -> Result<BTreeMap<String, String>, SomError> {
        self.finalize_with_byte_limit(files, Some(65536))
    }

    pub fn finalize_with_byte_limit(
        &self,
        files: BTreeMap<String, String>,
        byte_limit: Option<usize>,
    ) -> Result<BTreeMap<String, String>, SomError> {
        let expression = regex::Regex::new(r#"(?m)^name = "([a-zA-Z0-9_]+)""#).unwrap();
        let package = files
            .get("pyproject.toml")
            .and_then(|s| expression.captures(s))
            .map(|c| c[1].to_owned())
            .ok_or_else(|| failed("missing service package"))?;
        let mut result = BTreeMap::new();
        for (path, source) in files {
            let source = if path.ends_with(".py") {
                self.format_source(&path, &assemble_imports(&source), &package)?
            } else {
                source
            };
            result.insert(path, source);
        }
        if result.len() > 12
            || byte_limit
                .is_some_and(|limit| result.values().map(|s| s.len()).sum::<usize>() > limit)
        {
            return Err(SomError::new(
                "SCOPE_TOO_LARGE",
                "final quality output exceeds limits",
            ));
        }
        Ok(result)
    }
}

pub fn identifiers(source: &str) -> BTreeSet<String> {
    let chars: Vec<char> = source.chars().collect();
    let mut index = 0;
    let mut result = BTreeSet::new();
    while index < chars.len() {
        let ch = chars[index];
        if ch == '#' {
            while index < chars.len() && chars[index] != '\n' {
                index += 1;
            }
        } else if ch == '\'' || ch == '"' {
            let width = if chars.get(index..index + 3) == Some(&[ch, ch, ch]) {
                3
            } else {
                1
            };
            index += width;
            while index < chars.len() {
                if chars[index] == '\\' {
                    index += 2;
                } else if chars
                    .get(index..index + width)
                    .is_some_and(|s| s.iter().all(|c| *c == ch))
                {
                    index += width;
                    break;
                } else {
                    index += 1;
                }
            }
        } else if ch.is_ascii_alphabetic() || ch == '_' {
            let start = index;
            index += 1;
            while index < chars.len()
                && (chars[index].is_ascii_alphanumeric() || chars[index] == '_')
            {
                index += 1;
            }
            result.insert(chars[start..index].iter().collect());
        } else {
            index += 1;
        }
    }
    result
}

pub fn assemble_imports(source: &str) -> String {
    let lines: Vec<&str> = source.split_inclusive('\n').collect();
    let split = lines
        .iter()
        .take_while(|line| {
            line.starts_with("import ") || line.starts_with("from ") || line.trim().is_empty()
        })
        .count();
    let body = lines[split..].concat();
    let used = identifiers(&body);
    let mut imports = vec![];
    for line in &lines[..split] {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let (line, suffix) = if let Some((text, comment)) = line.split_once(" #") {
            (text.trim_end(), format!(" #{comment}"))
        } else {
            (line, String::new())
        };
        let (prefix, items) = if line.starts_with("from ") {
            line.split_once(" import ").unwrap()
        } else {
            ("", line.strip_prefix("import ").unwrap())
        };
        let kept: Vec<&str> = items
            .split(", ")
            .filter(|item| {
                let name = item
                    .split(" as ")
                    .last()
                    .unwrap()
                    .split('.')
                    .next()
                    .unwrap();
                used.contains(name)
            })
            .collect();
        if !kept.is_empty() {
            imports.push(format!(
                "{}import {}{suffix}",
                if prefix.is_empty() {
                    String::new()
                } else {
                    format!("{prefix} ")
                },
                kept.join(", ")
            ));
        }
    }
    format!(
        "\"\"\"Generated service module. See the service contract.\"\"\"\n\n{}\n\n{body}",
        imports.join("\n")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn asset_dir() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("compiler-assets/current")
    }

    fn profile() -> Quality {
        let manifest = crate::compiler::asset_manifest();
        let expected = manifest["files"]["ruff"].as_str().expect("trusted Ruff digest");
        Quality::load_pinned(&asset_dir(), expected)
            .expect("load generated compiler assets")
            .expect("generated quality profile")
    }

    #[test]
    fn an_unapproved_formatter_digest_is_rejected() {
        let error = Quality::load_pinned(&asset_dir(), &"0".repeat(64))
            .err()
            .expect("an unapproved digest must refuse the generated tool");
        assert_eq!(error.code, "OUTPUT_QUALITY_FAILED");
        assert_eq!(error.detail, "quality asset digest does not match");
    }

    #[test]
    fn asset_reads_and_live_identity_checks_refuse_symlinks() {
        let dir = std::env::temp_dir().join(format!("som-quality-link-{}", std::process::id()));
        std::fs::create_dir(&dir).unwrap();
        let file = dir.join("asset");
        std::fs::write(&file, b"asset bytes").unwrap();
        let link = dir.join("link");
        std::os::unix::fs::symlink(&file, &link).unwrap();
        let read_refused = read_asset(&link).is_err();
        let identity_refused = AssetIdentity::at(&link).is_err();
        std::fs::remove_dir_all(&dir).unwrap();
        assert!(read_refused && identity_refused);
    }

    #[test]
    fn scripts_foreign_architectures_and_truncated_headers_are_not_native() {
        let mut foreign_elf = [0_u8; 64];
        foreign_elf[..7].copy_from_slice(b"\x7fELF\x02\x01\x01");
        foreign_elf[16] = 3;
        foreign_elf[18..20].copy_from_slice(&183_u16.to_le_bytes());
        let mut foreign_macho = [0_u8; 32];
        foreign_macho[..4].copy_from_slice(&[0xcf, 0xfa, 0xed, 0xfe]);
        foreign_macho[4..8].copy_from_slice(&0x0100_0007_u32.to_le_bytes());
        for bytes in [
            &b""[..],
            &b"\x7fELF"[..],
            &b"\xcf\xfa\xed\xfe"[..],
            &b"#!/bin/sh\nexit 0\n"[..],
            &foreign_elf[..],
            &foreign_macho[..],
        ] {
            assert!(!native_formatter(bytes));
        }
    }

    #[test]
    fn formatting_is_idempotent_and_preserves_values() {
        let state = profile();
        let source = "from decimal import Decimal\n\ndef total(value: str) -> Decimal:\n    return Decimal(value) + Decimal('0.2')\n";
        let once = state
            .format_source("example/app.py", source, "example")
            .unwrap();
        let twice = state
            .format_source("example/app.py", &once, "example")
            .unwrap();
        assert_eq!(once, twice);
        assert!(once.contains("Decimal(\"0.2\")"));
    }

    #[test]
    fn missing_annotations_and_non_import_lint_are_rejected() {
        let state = profile();
        for source in [
            "def bad(value):\n    return value\n",
            "def bad(value: int) -> int:\n    unused = 1\n    return value\n",
        ] {
            let error = state
                .format_source("example/app.py", source, "example")
                .unwrap_err();
            assert_eq!(error.code, "OUTPUT_QUALITY_FAILED");
        }
    }

    #[test]
    fn unused_metadata_imports_are_removed_without_touching_bodies() {
        let source = "import os\nfrom decimal import Decimal\n\nVALUE = Decimal(\"0.1\")\n";
        let result = assemble_imports(source);
        assert!(!result.contains("import os"));
        assert!(result.ends_with("VALUE = Decimal(\"0.1\")\n"));
        assert!(result.contains("from decimal import Decimal"));
    }
}
