//! Exploration of #311: runs the compass parser (core tag v0.5.2) on the
//! generated Customer crate and on five damaged copies of it. It checks the
//! layers, the dependency direction and the named public symbols, and prints
//! the counts. It is not a gate: exit 0 means the tool ran, not that the
//! numbers are good.
use compass::{Language, MultiParser, ParsedFile, outline};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::process::ExitCode;
use std::time::Instant;

const CUSTOMER: &str = include_str!("../../../contract/customer.resource.prompt.json");
const LAYERS: [&str; 4] = ["domain", "application", "infrastructure", "interfaces"];

/// The files of a crate, keyed by their path in the crate.
type Files = BTreeMap<String, String>;

/// The public items that the Customer crate must have: (file, kind, name).
/// Kind `fn` is found by the outline of compass, `item` by its parsed tree.
const SYMBOLS: [(&str, &str, &str); 10] = [
    ("src/application/store.rs", "item", "CustomerStore"),
    ("src/application/customers.rs", "fn", "create_customer"),
    ("src/application/customers.rs", "fn", "get_customer"),
    ("src/application/customers.rs", "fn", "replace_customer"),
    ("src/application/customers.rs", "fn", "delete_customer"),
    ("src/application/customers.rs", "fn", "list_customers"),
    ("src/domain/customer.rs", "item", "Customer"),
    ("src/domain/customer.rs", "item", "CustomerInput"),
    ("src/domain/customer.rs", "fn", "parse_input"),
    ("src/infrastructure/memory_store.rs", "item", "MemoryStore"),
];

/// One run: a name, the files and the defects that the run must find.
struct Variant {
    name: &'static str,
    files: Files,
    expected: Vec<String>,
}

/// Whether a file of layer `from` may import layer `to`.
fn allowed(from: &str, to: &str) -> bool {
    if from == to {
        return true;
    }
    match from {
        "domain" => false,
        "application" => to == "domain",
        "infrastructure" => to == "domain" || to == "application",
        "interfaces" => LAYERS.contains(&to),
        _ => false,
    }
}

/// The layer of a file of the crate (`src/<layer>/...`), or `None`.
fn layer_of(path: &str) -> Option<&'static str> {
    let rest = path.strip_prefix("src/")?;
    let layer = rest.split('/').next()?;
    LAYERS.iter().copied().find(|known| *known == layer)
}

/// The layers that a file imports with `use crate::<layer>...`.
fn imports_of(parsed: &ParsedFile) -> Vec<&'static str> {
    let mut found = Vec::new();
    parsed.walk(|node, _depth| {
        if node.kind() == "use_declaration" {
            let text = parsed.node_text(node);
            if let Some(rest) = text.split("crate::").nth(1) {
                let first = rest.split("::").next().unwrap_or_default();
                if let Some(layer) = LAYERS.iter().copied().find(|known| *known == first) {
                    found.push(layer);
                }
            }
        }
        true
    });
    found
}

/// The clean Customer crate: the files that the emitter gives for the contract.
fn clean_crate() -> Result<Files, String> {
    let prompt: Value = serde_json::from_str(CUSTOMER).map_err(|error| error.to_string())?;
    let bundle = som_service::compile_prompt_v3(&prompt).map_err(describe)?;
    som_service::assemble_resource(&bundle).map_err(describe)
}

fn describe(error: som_service::Error) -> String {
    format!("{} {}", error.status, error.body)
}

/// A copy of the clean crate with one edit of one file. `None` from the edit means it does not apply.
fn with_edit(
    clean: &Files,
    path: &str,
    edit: impl FnOnce(&str) -> Option<String>,
) -> Result<Files, String> {
    let mut files = clean.clone();
    let text = files
        .get(path)
        .ok_or_else(|| format!("no {path} in the clean crate"))?
        .clone();
    let edited = edit(&text).ok_or_else(|| format!("the edit of {path} does not apply"))?;
    files.insert(path.to_owned(), edited);
    Ok(files)
}

fn expect(defects: &[&str]) -> Vec<String> {
    defects.iter().map(|defect| (*defect).to_owned()).collect()
}

fn variants(clean: &Files) -> Result<Vec<Variant>, String> {
    let domain = "src/domain/customer.rs";
    let application = "src/application/customers.rs";
    let store = "src/application/store.rs";

    let mut m1 = clean.clone();
    if m1.remove("src/interfaces/mod.rs").is_none() {
        return Err("no src/interfaces/mod.rs in the clean crate".to_owned());
    }
    let m2 = with_edit(clean, domain, |text| {
        Some(format!("{text}use crate::interfaces::routes;\n"))
    })?;
    let m3 = with_edit(clean, application, |text| {
        Some(format!(
            "{text}use crate::infrastructure::memory_store::MemoryStore;\n"
        ))
    })?;
    let m4 = with_edit(clean, store, |text| {
        text.contains("pub trait CustomerStore")
            .then(|| text.replace("pub trait CustomerStore", "pub trait CustomerRepo"))
    })?;
    let m5 = with_edit(clean, application, |text| {
        text.contains("pub fn list_customers(")
            .then(|| text.replace("pub fn list_customers(", "pub fn list_customers_all("))
    })?;

    Ok(vec![
        Variant {
            name: "clean",
            files: clean.clone(),
            expected: expect(&[]),
        },
        Variant {
            name: "m1_no_interfaces",
            files: m1,
            expected: expect(&["missing-layer:interfaces"]),
        },
        Variant {
            name: "m2_domain_imports_interfaces",
            files: m2,
            expected: expect(&["bad-direction:domain->interfaces"]),
        },
        Variant {
            name: "m3_application_imports_infrastructure",
            files: m3,
            expected: expect(&["bad-direction:application->infrastructure"]),
        },
        Variant {
            name: "m4_store_trait_renamed",
            files: m4,
            expected: expect(&["missing-symbol:CustomerStore"]),
        },
        Variant {
            name: "m5_use_case_renamed",
            files: m5,
            expected: expect(&["missing-symbol:list_customers"]),
        },
    ])
}

/// Whether the parsed tree has a struct or a trait with this name.
fn has_item(tree: &ParsedFile, name: &str) -> bool {
    let mut found = false;
    tree.walk(|node, _depth| {
        if matches!(node.kind(), "struct_item" | "trait_item")
            && let Some(id) = node.child_by_field_name("name")
            && tree.node_text(&id) == name
        {
            found = true;
        }
        !found
    });
    found
}

/// The defects that compass finds in the files of one crate (texts read from disk).
fn check(files: &Files) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    let mut parser = match MultiParser::new() {
        Ok(parser) => parser,
        Err(_) => {
            found.insert("unsupported:parser".to_owned());
            return found;
        }
    };
    let mut parsed: BTreeMap<&str, ParsedFile> = BTreeMap::new();
    for (path, text) in files {
        if path.ends_with(".rs")
            && let Some(tree) = parser.parse(text, Language::Rust)
        {
            parsed.insert(path.as_str(), tree);
        }
    }

    for layer in LAYERS {
        let mod_file = format!("src/{layer}/mod.rs");
        if !parsed.contains_key(mod_file.as_str()) {
            found.insert(format!("missing-layer:{layer}"));
        }
    }

    for (path, tree) in &parsed {
        let Some(from) = layer_of(path) else {
            continue;
        };
        for to in imports_of(tree) {
            if !allowed(from, to) {
                found.insert(format!("bad-direction:{from}->{to}"));
            }
        }
    }

    for (path, kind, name) in SYMBOLS {
        let present = match (parsed.get(path), files.get(path)) {
            (Some(tree), _) if kind == "item" => has_item(tree, name),
            (_, Some(text)) if kind == "fn" => match outline(text, Language::Rust) {
                Ok(functions) => functions.iter().any(|function| function.name == name),
                Err(_) => false,
            },
            _ => false,
        };
        if !present {
            found.insert(format!("missing-symbol:{name}"));
        }
    }
    found
}

fn write_variant(dir: &Path, files: &Files) -> std::io::Result<()> {
    for (path, text) in files {
        let target = dir.join(path);
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&target, text)?;
    }
    Ok(())
}

fn read_variant(dir: &Path, files: &Files) -> std::io::Result<Files> {
    let mut read = Files::new();
    for path in files.keys() {
        read.insert(path.clone(), std::fs::read_to_string(dir.join(path))?);
    }
    Ok(read)
}

fn list(items: &BTreeSet<String>) -> String {
    items.iter().cloned().collect::<Vec<_>>().join(", ")
}

fn main() -> ExitCode {
    let total = Instant::now();
    let clean = match clean_crate() {
        Ok(files) => files,
        Err(error) => {
            eprintln!("the clean crate could not be made: {error}");
            return ExitCode::from(2);
        }
    };
    let runs = match variants(&clean) {
        Ok(runs) => runs,
        Err(error) => {
            eprintln!("the damaged copies could not be made: {error}");
            return ExitCode::from(2);
        }
    };
    let root = std::env::temp_dir().join(format!("som-compass-check-{}", std::process::id()));
    let (mut sum_true, mut sum_false, mut sum_missed, mut sum_ms) = (0, 0, 0, 0);
    for run in &runs {
        let dir = root.join(run.name);
        if let Err(error) = write_variant(&dir, &run.files) {
            eprintln!("{}: writing failed: {error}", run.name);
            return ExitCode::from(2);
        }
        let read = match read_variant(&dir, &run.files) {
            Ok(read) => read,
            Err(error) => {
                eprintln!("{}: reading failed: {error}", run.name);
                return ExitCode::from(2);
            }
        };
        let started = Instant::now();
        let found = check(&read);
        let ms = started.elapsed().as_millis();

        let expected: BTreeSet<String> = run.expected.iter().cloned().collect();
        let true_count = found.intersection(&expected).count();
        let false_count = found.difference(&expected).count();
        let missed = expected.difference(&found).count();
        println!(
            "{} expected=[{}] found=[{}] true={true_count} false={false_count} missed={missed} ms={ms}",
            run.name,
            list(&expected),
            list(&found),
        );
        sum_true += true_count;
        sum_false += false_count;
        sum_missed += missed;
        sum_ms += ms;
    }
    println!(
        "sums true={sum_true} false={sum_false} missed={sum_missed} compass_ms={sum_ms} total_ms={}",
        total.elapsed().as_millis()
    );
    println!("files written under {}", root.display());
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(source: &str) -> ParsedFile {
        let mut parser = MultiParser::new().expect("the parser starts");
        parser
            .parse(source, Language::Rust)
            .expect("the source parses")
    }

    #[test]
    fn allowed_edges_follow_the_layer_rules() {
        assert!(allowed("domain", "domain"));
        assert!(!allowed("domain", "application"));
        assert!(!allowed("domain", "interfaces"));
        assert!(allowed("application", "domain"));
        assert!(!allowed("application", "infrastructure"));
        assert!(allowed("infrastructure", "domain"));
        assert!(allowed("infrastructure", "application"));
        assert!(!allowed("infrastructure", "interfaces"));
        assert!(allowed("interfaces", "domain"));
        assert!(allowed("interfaces", "application"));
        assert!(allowed("interfaces", "infrastructure"));
    }

    #[test]
    fn layer_of_reads_the_directory() {
        assert_eq!(layer_of("src/domain/customer.rs"), Some("domain"));
        assert_eq!(layer_of("src/interfaces/mod.rs"), Some("interfaces"));
        assert_eq!(layer_of("src/lib.rs"), None);
        assert_eq!(layer_of("openapi.json"), None);
    }

    #[test]
    fn imports_of_reads_the_crate_path() {
        let parsed = parse(
            "use crate::interfaces::routes;\nuse crate::infrastructure::memory_store::MemoryStore;\nuse serde_json::Value;\n",
        );
        assert_eq!(imports_of(&parsed), vec!["interfaces", "infrastructure"]);
    }

    #[test]
    fn each_damaged_copy_expects_its_defect_and_the_clean_crate_none() {
        let clean = clean_crate().expect("the clean crate");
        let runs = variants(&clean).expect("the variants");
        let expected: Vec<(&str, Vec<&str>)> = vec![
            ("clean", vec![]),
            ("m1_no_interfaces", vec!["missing-layer:interfaces"]),
            (
                "m2_domain_imports_interfaces",
                vec!["bad-direction:domain->interfaces"],
            ),
            (
                "m3_application_imports_infrastructure",
                vec!["bad-direction:application->infrastructure"],
            ),
            (
                "m4_store_trait_renamed",
                vec!["missing-symbol:CustomerStore"],
            ),
            ("m5_use_case_renamed", vec!["missing-symbol:list_customers"]),
        ];
        assert_eq!(runs.len(), expected.len());
        for (run, (name, defects)) in runs.iter().zip(expected) {
            assert_eq!(run.name, name);
            assert_eq!(run.expected, defects, "{name}");
        }
    }
}
