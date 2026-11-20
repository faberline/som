//! Decision 37 (issue #295): the README says how to run the service, has one curl
//! example per operation, and its decisions table matches the standard catalog.
use std::path::Path;

fn read_readme() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("README.md");
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

/// The rows under `## Decisions` of the README: (number, fourth cell, the test).
fn readme_decision_rows(readme: &str) -> Vec<(u32, String)> {
    let mut inside = false;
    let mut out = Vec::new();
    for line in readme.lines() {
        if let Some(h) = line.strip_prefix("## ") {
            inside = h.trim() == "Decisions";
            continue;
        }
        if !inside || !line.starts_with("| ") {
            continue;
        }
        let c = cells(line);
        if let Some(n) = c.first().and_then(|s| s.parse::<u32>().ok()) {
            out.push((n, c.get(3).cloned().unwrap_or_default()));
        }
    }
    out
}

/// Checks the `## Decisions` table: numbers 1 to 37 in order, four cells, a
/// backticked test name, and a file under `crate_dir` that holds `fn <test>(`.
fn check_table(readme: &str, crate_dir: &Path) -> Result<(), String> {
    let mut rows: Vec<(u32, Vec<String>)> = Vec::new();
    let mut inside = false;
    for line in readme.lines() {
        if let Some(h) = line.strip_prefix("## ") {
            inside = h.trim() == "Decisions";
            continue;
        }
        if !inside || !line.starts_with("| ") {
            continue;
        }
        let c = cells(line);
        if let Some(n) = c.first().and_then(|s| s.parse::<u32>().ok()) {
            rows.push((n, c));
        }
    }
    let numbers: Vec<u32> = rows.iter().map(|(n, _)| *n).collect();
    let expected: Vec<u32> = (1..=37).collect();
    if numbers != expected {
        return Err(format!("decision rows are {numbers:?}, expected 1 to 37"));
    }
    for (n, c) in &rows {
        if c.len() != 4 {
            return Err(format!("row {n} has {} cells, expected 4", c.len()));
        }
        let test = &c[3];
        if test.len() < 3 || !test.starts_with('`') || !test.ends_with('`') {
            return Err(format!("row {n}: test cell {test} is not backticked"));
        }
        let name = test.trim_matches('`');
        let file = crate_dir.join(&c[2]);
        let text = std::fs::read_to_string(&file)
            .map_err(|e| format!("row {n}: cannot read {}: {e}", file.display()))?;
        if !text.contains(&format!("fn {name}(")) {
            return Err(format!("row {n}: {} has no test `{name}`", c[2]));
        }
    }
    Ok(())
}

#[test]
fn readme_has_run_steps_and_one_curl_per_operation() {
    let readme = read_readme();
    for heading in ["## Run", "## Examples", "## Decisions"] {
        assert!(
            readme.lines().any(|l| l.trim() == heading),
            "README has no `{heading}` heading"
        );
    }
    let curls: Vec<&str> = readme
        .lines()
        .map(str::trim)
        .filter(|l| l.starts_with("curl"))
        .collect();
    assert_eq!(
        curls.len(),
        5,
        "README must have exactly 5 curl lines, one per operation: {curls:#?}"
    );
    type IsOp = fn(&str) -> bool;
    let ops: [(&str, IsOp); 5] = [
        ("create", |l| {
            l.contains("-X POST") && l.contains("/v1/orders ")
        }),
        ("read", |l| !l.contains("-X") && l.contains("/v1/orders/")),
        ("list", |l| !l.contains("-X") && l.contains("/v1/orders?")),
        ("replace", |l| {
            l.contains("-X PUT") && l.contains("/v1/orders/")
        }),
        ("delete", |l| {
            l.contains("-X DELETE") && l.contains("/v1/orders/")
        }),
    ];
    for (name, is_op) in ops {
        let found = curls.iter().filter(|l| is_op(l)).count();
        assert_eq!(
            found, 1,
            "operation `{name}`: expected one curl line, found {found}"
        );
    }
}

#[test]
fn readme_table_has_37_rows_and_each_row_names_an_existing_test() {
    let readme = read_readme();
    check_table(&readme, Path::new(env!("CARGO_MANIFEST_DIR"))).expect("README decisions table");
}

#[test]
fn readme_rows_equal_the_catalog() {
    let readme = read_readme();
    let rows = readme_decision_rows(&readme);
    let files = [
        "crud-resource.md",
        "standard-pagination.md",
        "standard-errors.md",
        "service-baseline.md",
    ];
    let mut total = 0;
    for f in files {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../standards")
            .join(f);
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
        for (n, test_cell) in decision_rows(&text) {
            total += 1;
            let readme_cell = rows.iter().find(|(m, _)| *m == n).map(|(_, t)| t.as_str());
            assert_eq!(
                readme_cell,
                Some(test_cell.as_str()),
                "decision {n}: the README row differs from standards/{f}"
            );
        }
    }
    assert_eq!(total, 37, "the catalog has 37 decision rows");
}

/// Issue #296: the README has a `## How to change` section with the five changes, in
/// order; each has one `Files:` line that names 1 to 3 existing files of the crate.
#[test]
fn readme_how_to_change_has_the_five_changes() {
    const TITLES: [&str; 5] = [
        "Add a field with a constraint",
        "Add a filter to the list",
        "Replace the in-memory store",
        "Add POST /v1/orders/{id}/cancel",
        "Change hard delete to soft delete",
    ];
    let readme = read_readme();
    let crate_dir = Path::new(env!("CARGO_MANIFEST_DIR"));

    // The `### ` sections under `## How to change`, up to the next `## ` heading.
    let mut inside = false;
    let mut found_section = false;
    let mut sections: Vec<(String, Vec<String>)> = Vec::new();
    for line in readme.lines() {
        if let Some(h) = line.strip_prefix("## ") {
            inside = h.trim() == "How to change";
            found_section |= inside;
            continue;
        }
        if !inside {
            continue;
        }
        if let Some(t) = line.strip_prefix("### ") {
            sections.push((t.trim().to_string(), Vec::new()));
        } else if let Some((_, body)) = sections.last_mut() {
            body.push(line.to_string());
        }
    }
    assert!(found_section, "README has no `## How to change` heading");
    let titles: Vec<&str> = sections.iter().map(|(t, _)| t.as_str()).collect();
    assert_eq!(
        titles, TITLES,
        "the `### ` headings under `## How to change`, in order"
    );

    for (title, body) in &sections {
        let files: Vec<&String> = body.iter().filter(|l| l.starts_with("Files:")).collect();
        assert_eq!(
            files.len(),
            1,
            "section `{title}`: expected one `Files:` line, found {}",
            files.len()
        );
        if let Err(e) = check_files_line(title, files[0], crate_dir) {
            panic!("{e}");
        }
    }
}

/// Checks one `Files:` line of a `## How to change` section: 1 to 3 backticked paths,
/// each under `src/`. A path is a file of the crate, or, when the line also has the
/// word `new` outside backticks, a `.rs` file whose parent directory exists (issue #296).
fn check_files_line(title: &str, line: &str, crate_dir: &Path) -> Result<(), String> {
    let paths: Vec<&str> = line.split('`').skip(1).step_by(2).collect();
    if !(1..=3).contains(&paths.len()) {
        return Err(format!(
            "section `{title}`: `Files:` lists {} paths, expected 1 to 3",
            paths.len()
        ));
    }
    let outside: String = line.split('`').step_by(2).collect::<Vec<_>>().join(" ");
    let new_marked = outside
        .split(|c: char| !c.is_ascii_alphanumeric())
        .any(|w| w == "new");
    for p in paths {
        if !p.starts_with("src/") {
            return Err(format!("section `{title}`: path `{p}` is not under src/"));
        }
        let full = crate_dir.join(p);
        if full.is_file() {
            continue;
        }
        let parent_exists = full.parent().is_some_and(|d| d.is_dir());
        if new_marked && p.ends_with(".rs") && parent_exists {
            continue;
        }
        return Err(format!(
            "section `{title}`: `{p}` is not a file of the crate (a new file needs `(new)` and an existing parent directory)"
        ));
    }
    Ok(())
}

#[test]
fn readme_how_to_change_new_file_needs_the_word_new() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    assert!(
        check_files_line("t", "Files: `src/infrastructure/vec_store.rs`", dir).is_err(),
        "a missing file without `(new)` is refused"
    );
    assert!(
        check_files_line("t", "Files: `src/infrastructure/vec_store.rs` (new)", dir).is_ok(),
        "a new .rs file marked `(new)` in an existing directory is allowed"
    );
    assert!(
        check_files_line("t", "Files: `src/infrastructure/nope/x.rs` (new)", dir).is_err(),
        "a new file whose parent directory does not exist is refused"
    );
    assert!(
        check_files_line("t", "Files: `tests/it/store.rs`", dir).is_err(),
        "a path outside src/ is refused"
    );
}

/// A `## Decisions` table with one row per number in `rows`, all naming `test` in `file`.
fn table(rows: std::ops::RangeInclusive<u32>, test: &str, file: &str) -> String {
    let mut s = String::from("## Decisions\n\n| # | Decision | File | Test |\n|---|---|---|---|\n");
    for n in rows {
        s.push_str(&format!("| {n} | text | {file} | `{test}` |\n"));
    }
    s
}

#[test]
fn table_check_refuses_a_row_without_its_test() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let missing = table(1..=37, "no_such_test", "tests/it/routes.rs");
    let err = check_table(&missing, dir).expect_err("a row whose test does not exist is refused");
    assert!(
        err.contains("no_such_test"),
        "the error names the test: {err}"
    );

    let short = table(
        1..=36,
        "readme_has_run_steps_and_one_curl_per_operation",
        "tests/it/readme.rs",
    );
    assert!(
        check_table(&short, dir).is_err(),
        "a table with 36 rows is refused"
    );
}
