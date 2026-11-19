//! The standard catalog som.std.v1 keeps all 37 decisions, each with a proof test (issue #277).
use std::path::Path;

const FILES: [(&str, u32, u32); 4] = [
    ("standards/crud-resource.md", 1, 12),
    ("standards/standard-pagination.md", 13, 20),
    ("standards/standard-errors.md", 21, 24),
    ("standards/service-baseline.md", 25, 37),
];

const SECTIONS: [&str; 6] = [
    "Name",
    "Decisions",
    "Facts that CNL must give",
    "Tests",
    "Usual changes",
    "Version",
];

fn read(relative: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join(relative);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

/// The cells of one table row, trimmed. The row must start and end with `|`.
fn cells(line: &str) -> Vec<String> {
    line.trim()
        .trim_matches('|')
        .split('|')
        .map(|c| c.trim().to_string())
        .collect()
}

/// For each table row whose first cell is a decision number: (number, third cell).
fn decision_rows(text: &str) -> Vec<(u32, String)> {
    text.lines()
        .filter(|l| l.starts_with("| "))
        .filter_map(|l| {
            let c = cells(l);
            let n = c.first()?.parse::<u32>().ok()?;
            Some((n, c.get(2)?.clone()))
        })
        .collect()
}

/// The `## ` headings of a file, in order.
fn headings(text: &str) -> Vec<&str> {
    text.lines().filter_map(|l| l.strip_prefix("## ")).collect()
}

/// The lines under one `## ` heading, up to the next `## ` heading.
fn section<'a>(text: &'a str, heading: &str) -> Vec<&'a str> {
    let mut out = Vec::new();
    let mut inside = false;
    for line in text.lines() {
        if let Some(h) = line.strip_prefix("## ") {
            if inside {
                break;
            }
            inside = h == heading;
            continue;
        }
        if inside {
            out.push(line);
        }
    }
    out
}

#[test]
fn decision_rows_reads_number_and_test_cell() {
    assert_eq!(
        decision_rows("| 3 | text | `a_test` |\n| x | y | z |"),
        vec![(3, "`a_test`".to_string())]
    );
}

#[test]
fn each_standard_has_exactly_its_decision_numbers() {
    for &(file, lo, hi) in FILES.iter() {
        let numbers: Vec<u32> = decision_rows(&read(file))
            .into_iter()
            .map(|(n, _)| n)
            .collect();
        let expected: Vec<u32> = (lo..=hi).collect();
        assert_eq!(numbers, expected, "{file}");
    }
}

#[test]
fn each_decision_names_a_proof_test() {
    for &(file, _, _) in FILES.iter() {
        for (n, cell) in decision_rows(&read(file)) {
            let inner = cell.strip_prefix('`').and_then(|c| c.strip_suffix('`'));
            let ok = inner.is_some_and(|t| {
                !t.is_empty()
                    && t.starts_with(|c: char| c.is_ascii_lowercase())
                    && t.chars()
                        .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
            });
            assert!(ok, "{file} decision {n}: proof test cell is {cell:?}");
        }
    }
}

#[test]
fn each_standard_has_the_six_sections_in_order() {
    for &(file, _, _) in FILES.iter() {
        let text = read(file);
        let mut expected: Vec<&str> = SECTIONS.to_vec();
        if file.ends_with("service-baseline.md") {
            expected.insert(5, "Core mapping");
        }
        assert_eq!(headings(&text), expected, "{file}");
        let version = section(&text, "Version")
            .into_iter()
            .map(str::trim)
            .find(|l| !l.is_empty());
        assert_eq!(version, Some("som.std.v1"), "{file}: version line");
    }
}

#[test]
fn each_standard_has_usual_changes_rows() {
    for &(file, _, _) in FILES.iter() {
        let text = read(file);
        // The first `| ` row is the header; the rest are data rows.
        let data_rows = section(&text, "Usual changes")
            .iter()
            .filter(|l| l.starts_with("| "))
            .count()
            .saturating_sub(1);
        assert!(
            data_rows >= 2,
            "{file}: Usual changes has {data_rows} data rows"
        );
    }
}

#[test]
fn readme_lists_the_four_standards_and_the_version() {
    let text = read("standards/README.md");
    for link in [
        "](crud-resource.md)",
        "](standard-pagination.md)",
        "](standard-errors.md)",
        "](service-baseline.md)",
    ] {
        assert!(text.contains(link), "README lacks {link}");
    }
    assert!(
        text.lines().any(|l| l.trim() == "som.std.v1"),
        "README lacks the version line"
    );
    assert!(
        headings(&text).contains(&"How to change"),
        "README lacks the heading How to change"
    );
}

#[test]
fn core_mapping_covers_decisions_25_to_32() {
    let text = read("standards/service-baseline.md");
    let mut numbers = Vec::new();
    for line in section(&text, "Core mapping")
        .into_iter()
        .filter(|l| l.starts_with("| "))
    {
        let c = cells(line);
        let Some(id) = c.first().and_then(|id| id.strip_prefix('D')) else {
            continue; // the header row
        };
        let Ok(n) = id.parse::<u32>() else {
            continue;
        };
        let status = c.last().cloned().unwrap_or_default();
        assert!(
            matches!(status.as_str(), "present" | "gap" | "partial"),
            "D{n}: status is {status:?}"
        );
        numbers.push(n);
    }
    let expected: Vec<u32> = (25..=32).collect();
    assert_eq!(numbers, expected);
}

#[test]
fn aw_toml_allows_the_standards_root() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../aw.toml");
    let text = std::fs::read_to_string(&path).expect("read aw.toml");
    assert!(
        text.lines().any(|l| l.trim() == "path = \"standards\""),
        "aw.toml lacks the standards root"
    );
}
