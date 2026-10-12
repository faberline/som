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
pub const CATALOG_TEXT: &str = include_str!("../../som-code-python/data/block-contracts/composed-quality.json");

fn failed(detail: impl Into<String>) -> SomError {
    SomError::new("OUTPUT_QUALITY_FAILED", detail)
}

#[derive(Clone)]
pub struct Quality {
    pub binary: PathBuf,
    pub profile: PathBuf,
    pub catalog: Value,
}

impl Quality {
    pub fn load(model: &Path) -> Result<Option<Self>, SomError> {
        let path = model.join("quality.json");
        if !path.exists() { return Ok(None); }
        let config: Value = serde_json::from_slice(&std::fs::read(path).map_err(|e| failed(e.to_string()))?)
            .map_err(|e| failed(e.to_string()))?;
        let binary = model.join("ruff");
        let profile = model.join("pyproject.toml");
        for (key, path) in [("formatter_sha256", &binary), ("profile_sha256", &profile)] {
            let bytes = std::fs::read(path).map_err(|e| failed(e.to_string()))?;
            if config[key] != format!("{:x}", Sha256::digest(&bytes)) {
                return Err(failed(format!("{key} does not match")));
            }
        }
        if config["id"] != VERSION || config["formatter_version"] != FORMATTER_VERSION
            || std::fs::read(&profile).map_err(|e|failed(e.to_string()))? != PROFILE_TEXT.as_bytes()
            || std::fs::read(&binary).map_err(|e|failed(e.to_string()))?.get(..4) != Some(&[0xcf,0xfa,0xed,0xfe]) {
            return Err(failed("quality profile or native formatter does not match"));
        }
        let state = Self {binary, profile, catalog: serde_json::from_str(CATALOG_TEXT).map_err(|e|failed(e.to_string()))?};
        if state.execute(&["--version".into()], "")?.trim() != FORMATTER_VERSION {
            return Err(failed("formatter version does not match"));
        }
        Ok(Some(state))
    }

    fn execute(&self, args: &[String], input: &str) -> Result<String, SomError> {
        let mut child = Command::new(&self.binary).args(args)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
            .spawn().map_err(|e|failed(e.to_string()))?;
        let mut stdin = child.stdin.take().unwrap();
        let mut stdout = child.stdout.take().unwrap();
        let mut stderr = child.stderr.take().unwrap();
        let bytes = input.as_bytes().to_vec();
        let writer = std::thread::spawn(move || { stdin.write_all(&bytes) });
        let reader = std::thread::spawn(move || {
            let mut data = Vec::new();
            stdout.by_ref().take(4 * 1024 * 1024).read_to_end(&mut data).map(|_|data)
        });
        let errors = std::thread::spawn(move || {
            let mut data = Vec::new();
            stderr.by_ref().take(4 * 1024 * 1024).read_to_end(&mut data).map(|_|data)
        });
        let started = Instant::now();
        let status = loop {
            if let Some(status) = child.try_wait().map_err(|e|failed(e.to_string()))? { break Some(status); }
            if started.elapsed() > Duration::from_secs(10) {
                let _ = child.kill(); let _ = child.wait(); break None;
            }
            std::thread::sleep(Duration::from_millis(5));
        };
        let written = writer.join().map_err(|_|failed("formatter input worker failed"))?;
        let out = reader.join().map_err(|_|failed("formatter output worker failed"))?.map_err(|e|failed(e.to_string()))?;
        let err = errors.join().map_err(|_|failed("formatter error worker failed"))?.map_err(|e|failed(e.to_string()))?;
        let Some(status) = status else { return Err(failed("formatter timed out")); };
        written.map_err(|e|failed(e.to_string()))?;
        if !status.success() {
            return Err(failed(format!("formatter rejected output: {}{}", String::from_utf8_lossy(&out), String::from_utf8_lossy(&err))));
        }
        String::from_utf8(out).map_err(|e|failed(e.to_string()))
    }

    pub fn format_source(&self, path: &str, source: &str, package: &str) -> Result<String, SomError> {
        let common = ["--config".into(), self.profile.to_string_lossy().into_owned(), "--no-cache".into(), "--stdin-filename".into(), path.into(), "--config".into(), format!("lint.isort.known-first-party={}", serde_json::to_string(&vec![package]).unwrap())];
        let mut sorted = vec!["check".into()]; sorted.extend(common.clone());
        sorted.extend(["--select".into(),"I".into(),"--fix".into(),"-".into()]);
        let source = self.execute(&sorted, source)?;
        let mut formatted = vec!["format".into()]; formatted.extend(common.clone()); formatted.push("-".into());
        let source = self.execute(&formatted, &source)?;
        let mut checked = vec!["check".into()]; checked.extend(common); checked.extend(["--output-format".into(),"json".into(),"-".into()]);
        self.execute(&checked, &source)?;
        Ok(source)
    }

    pub fn finalize(&self, files: BTreeMap<String, String>) -> Result<BTreeMap<String, String>, SomError> {
        self.finalize_with_byte_limit(files, Some(65536))
    }

    pub fn finalize_with_byte_limit(&self, files: BTreeMap<String, String>, byte_limit: Option<usize>) -> Result<BTreeMap<String, String>, SomError> {
        let expression = regex::Regex::new(r#"(?m)^name = "([a-zA-Z0-9_]+)""#).unwrap();
        let package = files.get("pyproject.toml").and_then(|s|expression.captures(s)).map(|c|c[1].to_owned()).ok_or_else(||failed("missing service package"))?;
        let mut result = BTreeMap::new();
        for (path, source) in files {
            let source = if path.ends_with(".py") { self.format_source(&path, &assemble_imports(&source), &package)? } else {source};
            result.insert(path, source);
        }
        if result.len()>12 || byte_limit.is_some_and(|limit| result.values().map(|s|s.len()).sum::<usize>()>limit) {
            return Err(SomError::new("SCOPE_TOO_LARGE", "final quality output exceeds limits"));
        }
        Ok(result)
    }
}

pub fn identifiers(source: &str) -> BTreeSet<String> {
    let chars: Vec<char> = source.chars().collect();
    let mut index = 0; let mut result = BTreeSet::new();
    while index < chars.len() {
        let ch = chars[index];
        if ch == '#' {
            while index < chars.len() && chars[index] != '\n' { index += 1; }
        } else if ch == '\'' || ch == '"' {
            let width = if chars.get(index..index+3) == Some(&[ch,ch,ch]) {3} else {1};
            index += width;
            while index < chars.len() {
                if chars[index] == '\\' { index += 2; }
                else if chars.get(index..index+width).is_some_and(|s|s.iter().all(|c|*c==ch)) {index+=width;break;}
                else { index += 1; }
            }
        } else if ch.is_ascii_alphabetic() || ch == '_' {
            let start = index; index += 1;
            while index < chars.len() && (chars[index].is_ascii_alphanumeric() || chars[index]=='_') {index+=1;}
            result.insert(chars[start..index].iter().collect());
        } else { index += 1; }
    }
    result
}

pub fn assemble_imports(source: &str) -> String {
    let lines: Vec<&str> = source.split_inclusive('\n').collect();
    let split = lines.iter().take_while(|line|line.starts_with("import ") || line.starts_with("from ") || line.trim().is_empty()).count();
    let body = lines[split..].concat(); let used = identifiers(&body); let mut imports = vec![];
    for line in &lines[..split] {
        let line = line.trim(); if line.is_empty() {continue;}
        let (line, suffix) = if let Some((text, comment)) = line.split_once(" #") {(text.trim_end(),format!(" #{comment}"))} else {(line,String::new())};
        let (prefix, items) = if line.starts_with("from ") { line.split_once(" import ").unwrap() } else {("",line.strip_prefix("import ").unwrap())};
        let kept: Vec<&str> = items.split(", ").filter(|item| {
            let name = item.split(" as ").last().unwrap().split('.').next().unwrap(); used.contains(name)
        }).collect();
        if !kept.is_empty() { imports.push(format!("{}import {}{suffix}", if prefix.is_empty() {String::new()} else {format!("{prefix} ")}, kept.join(", "))); }
    }
    format!("\"\"\"Generated service module. See the service contract.\"\"\"\n\n{}\n\n{body}",imports.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile() -> Quality {
        let model = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("models/composed-quality-candidate");
        Quality::load(&model).unwrap().unwrap()
    }

    #[test]
    fn formatting_is_idempotent_and_preserves_values() {
        let state = profile();
        let source = "from decimal import Decimal\n\ndef total(value: str) -> Decimal:\n    return Decimal(value) + Decimal('0.2')\n";
        let once = state.format_source("example/app.py", source, "example").unwrap();
        let twice = state.format_source("example/app.py", &once, "example").unwrap();
        assert_eq!(once, twice);
        assert!(once.contains("Decimal(\"0.2\")"));
    }

    #[test]
    fn missing_annotations_and_non_import_lint_are_rejected() {
        let state = profile();
        for source in ["def bad(value):\n    return value\n",
                       "def bad(value: int) -> int:\n    unused = 1\n    return value\n"] {
            let error = state.format_source("example/app.py", source, "example").unwrap_err();
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
