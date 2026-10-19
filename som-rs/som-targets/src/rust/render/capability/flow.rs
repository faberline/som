//! Issue #54: the flow_postgresql and flow_mongodb blocks. Each one is its capability block with the changes the
//! flow runtime needs: a revision column per row, the run journal, and the src/flows.rs routes.
//! The capability products themselves do not change.

use serde_json::Value;

use super::super::{Field, Kind};
use super::Product;
use super::flow_object;
use super::workflow::Flow;

pub fn render(id: &str, params: &Value) -> Result<String, String> {
    if id.contains("capability_flow_mongodb") {
        return mongo(id, params);
    }
    let base = || super::render(&id.replace("capability_flow", "capability"), params);
    Ok(match id {
        "rust.flows.capability_flow" => flow_object::runtime(params, flows(params, FLOWS)?)?,
        "rust.integrations.capability_flow" => flow_object::integrations(params, base()?)?,
        "rust.main.capability_flow" => flow_object::main(params, base()?)?,
        "rust.readme.capability_flow" => flow_object::readme(params, base()?)?,
        "rust.lib.capability_flow" => lib(base()?)?,
        "rust.schemas.capability_flow" => {
            let product = Product::read(params)?;
            base()? + "\n" + &super::models(&product)
        }
        "rust.db.capability_flow" => db(base()?)?,
        "rust.api.capability_flow" => flow_object::api(params, api(base()?, params)?)?,
        "rust.migration.capability_flow" => migration(base()?)?,
        "rust.tests.capability_flow" => tests(base()?, flow_name(params)?)?,
        _ => base()?,
    })
}

/// The flow_mongodb blocks: the capability_mongodb blocks with the same changes on MongoDB.
fn mongo(id: &str, params: &Value) -> Result<String, String> {
    let base = || {
        super::render(
            &id.replace("capability_flow_mongodb", "capability_mongodb"),
            params,
        )
    };
    let package = || {
        params["package"]
            .as_str()
            .ok_or_else(|| "param package is not text".to_owned())
    };
    Ok(match id {
        "rust.flows.capability_flow_mongodb" => flow_object::runtime(params, flows(params, FLOWS_MONGO)?)?,
        "rust.integrations.capability_flow_mongodb" => flow_object::integrations(params, base()?)?,
        "rust.main.capability_flow_mongodb" => flow_object::main(params, base()?)?,
        "rust.readme.capability_flow_mongodb" => flow_object::readme(params, base()?)?,
        "rust.lib.capability_flow_mongodb" => lib(base()?)?,
        "rust.schemas.capability_flow_mongodb" => {
            base()? + "\n" + &super::mongo::render("rust.models.capability_mongodb", params)?
        }
        "rust.db.capability_flow_mongodb" => mongo_db(base()?, package()?)?,
        "rust.api.capability_flow_mongodb" => flow_object::api(params, api(base()?, params)?)?,
        "rust.tests.capability_flow_mongodb" => tests(base()?, flow_name(params)?)?,
        _ => base()?,
    })
}

/// Every document carries the revision a compensation checks and the guard a child write
/// raises. The journal collections drop first, and the runs keep one key per owner and flow.
fn mongo_db(text: String, package: &str) -> Result<String, String> {
    let mut text = once(
        &text,
        "pub struct Store {\n    client: Client,\n    database: Database,\n}",
        "pub struct Store {\n    pub(crate) client: Client,\n    pub(crate) database: Database,\n}",
    )?;
    let edits = [
        (
            "    fn collection(&self, name: &str) -> Collection<Document> {",
            "    pub(crate) fn collection(&self, name: &str) -> Collection<Document> {".to_owned(),
        ),
        (
            "        options.server_selection_timeout = Some(Duration::from_secs(2));\n",
            format!(
                "        options.server_selection_timeout = Some(Duration::from_secs(2));\n        options.app_name = Some({package:?}.to_owned());\n"
            ),
        ),
        (
            "            return Err(\"MongoDB transactions require a replica set or mongos\".to_owned());\n        }\n",
            "            return Err(\"MongoDB transactions require a replica set or mongos\".to_owned());\n        }\n        let runs = IndexModel::builder()\n            .keys(doc! {\"owner\": 1, \"flow\": 1, \"key\": 1})\n            .options(mongodb::options::IndexOptions::builder().unique(true).build())\n            .build();\n        self.collection(\"_som_flow_runs\")\n            .create_index(runs)\n            .await\n            .map_err(failed)?;\n".to_owned(),
        ),
        (
            "for collection in [CHILDREN, PARENTS, COUNTERS]",
            "for collection in [\"_som_flow_leases\", \"_som_flow_runs\", CHILDREN, PARENTS, COUNTERS]".to_owned(),
        ),
        (
            "    document.insert(\"created_at\", now.clone());\n",
            "    document.insert(\"_som_revision\", 0);\n    document.insert(\"_som_guard\", 0);\n    document.insert(\"created_at\", now.clone());\n".to_owned(),
        ),
        (
            "doc! {\"$inc\": {\"_som_revision\": 1}}",
            "doc! {\"$inc\": {\"_som_guard\": 1}}".to_owned(),
        ),
        (
            "doc! {\"$set\": changes}",
            "doc! {\"$set\": changes, \"$inc\": {\"_som_revision\": 1}}".to_owned(),
        ),
    ];
    for (anchor, with) in edits {
        text = once(&text, anchor, &with)?;
    }
    for name in [
        "parent_fields",
        "child_fields",
        "parent_changes",
        "child_changes",
        "parent_row",
        "child_row",
    ] {
        text = once(
            &text,
            &format!("\nfn {name}("),
            &format!("\npub(crate) fn {name}("),
        )?;
    }
    Ok(text)
}

/// Replace the one anchor, or refuse the block: a capability block lost a line the flow needs.
pub(super) fn once(text: &str, anchor: &str, with: &str) -> Result<String, String> {
    if text.matches(anchor).count() != 1 {
        return Err(format!("flow anchor {anchor:?} is not unique"));
    }
    Ok(text.replacen(anchor, with, 1))
}

fn flow_name(params: &Value) -> Result<&str, String> {
    params["flow"]["name"]
        .as_str()
        .ok_or_else(|| "param flow has no name".to_owned())
}

/// The rows move into src/schemas.rs. crate::models stays a name for them.
fn lib(text: String) -> Result<String, String> {
    let text = once(&text, "pub mod models;\n", "")?;
    let text = once(
        &text,
        "pub mod integrations;\n",
        "pub mod flows;\npub mod integrations;\n",
    )?;
    once(
        &text,
        "pub use api::{AppState, router};\n",
        "pub use api::{AppState, router};\npub use schemas as models;\n",
    )
}

/// Every update moves the private revision with updated_at. The journal tables drop first.
fn db(text: String) -> Result<String, String> {
    let mut out = String::new();
    let mut rest = text.as_str();
    let open = "\"updated_at\" = CASE WHEN ";
    let close = " THEN now() ELSE \"updated_at\" END";
    while let Some(start) = rest.find(open) {
        let after = &rest[start + open.len()..];
        let end = after.find(close).ok_or("flow db update has no end")?;
        let condition = &after[..end];
        let stop = start + open.len() + end + close.len();
        out += &rest[..stop];
        out += &format!(
            ", \"_som_revision\" = \"_som_revision\" + CASE WHEN {condition} THEN 1 ELSE 0 END"
        );
        rest = &rest[stop..];
    }
    out += rest;
    let line = out
        .lines()
        .find(|line| line.starts_with("pub const DOWNGRADE: [&str; 2] = ["))
        .ok_or("flow db has no downgrade")?
        .to_owned();
    let inner = line
        .trim_start_matches("pub const DOWNGRADE: [&str; 2] = [")
        .trim_end_matches("];");
    let mut rows = vec![
        "r#\"DROP TABLE \"_som_flow_leases\"\"#".to_owned(),
        "r#\"DROP TABLE \"_som_flow_runs\"\"#".to_owned(),
    ];
    rows.extend(inner.split(", ").map(str::to_owned));
    let vertical = rows
        .iter()
        .map(|row| format!("    {row},\n"))
        .collect::<String>();
    let downgrade = format!(
        "pub const DOWNGRADE: [&str; {}] = [\n{vertical}];",
        rows.len()
    );
    let out = out.replace(
        "/// alembic downgrade base: the children, then the parents.",
        "/// alembic downgrade base: the run journal, the children, then the parents.",
    );
    once(&out, &line, &downgrade)
}

fn api(text: String, params: &Value) -> Result<String, String> {
    let text = once(
        &text,
        "        .fallback(missing)\n",
        "        .merge(crate::flows::routes())\n        .fallback(missing)\n",
    )?;
    let text = once(&text, "\nfn problem(", "\npub(crate) fn problem(")?;
    let text = once(&text, "\nstruct Body<T> {", "\npub(crate) struct Body<T> {")?;
    let text = once(
        &text,
        "    fn finish(self, problems: Vec<Value>) -> Result<T, ApiError> {",
        "    pub(crate) fn finish(self, problems: Vec<Value>) -> Result<T, ApiError> {",
    )?;
    let text = once(&text, "\nfn read_body<", "\npub(crate) fn read_body<")?;
    let text = export_rows(&text)?;
    if !matches!(Flow::read(params)?, Flow::Csv) {
        return Ok(text + FLOW_CSV_NONE);
    }
    let cells = "const CSV_CELLS: [fn(&str) -> Option<Value>; ";
    let line = text
        .lines()
        .find(|line| line.starts_with(cells))
        .ok_or("flow csv has no cell readers")?;
    let readers = line
        .rsplit_once("= [")
        .and_then(|(_, rest)| rest.strip_suffix("];"))
        .ok_or("flow csv cell readers")?;
    let readers: Vec<&str> = readers
        .split(", ")
        .map(|reader| match reader {
            "csv_int" => "flow_int",
            "csv_float" => "flow_float",
            "csv_bool" => "flow_bool",
            other => other,
        })
        .collect();
    let mut out = format!("{text}{}", FLOW_CSV.replace("@READERS@", &readers.join(", ")));
    for (name, code) in [
        ("flow_int", FLOW_INT),
        ("flow_float", FLOW_FLOAT),
        ("flow_bool", FLOW_BOOL),
    ] {
        if readers.contains(&name) {
            out += code;
        }
    }
    Ok(out)
}

/// The export route answers through export_rows, which a flow's workflow.run calls on its rows.
fn export_rows(text: &str) -> Result<String, String> {
    let anchor = "    if rows.len() > EXPORT_MAX_ROWS {\n        return Err(ApiError::TooMany);\n    }\n";
    if text.matches(anchor).count() != 1 {
        return Err("flow export has no one row limit".to_owned());
    }
    let start = text.find(anchor).ok_or("flow export")? + anchor.len();
    let length = text[start..].find("\n}\n").ok_or("flow export has no end")? + 1;
    let body = &text[start..start + length];
    Ok(format!(
        "{}    export_rows(rows)\n}}\n\n/// The export bytes of rows, as the export route answers them. A flow's workflow.run reads them.\npub(crate) fn export_rows(rows: Vec<crate::schemas::ChildRow>) -> Result<Response, ApiError> {{\n{body}}}\n{}",
        &text[..start],
        &text[start + length + 2..]
    ))
}

/// workflow.import: the children of csv_records in the Python flow, or its 422 detail.
const FLOW_CSV: &str = r#"
/// A CSV text as csv.DictReader(strict=True) reads it in a flow: the header, then 1 to 20 rows
/// of the child fields as Python reads each cell. Err is the 422 detail.
pub(crate) fn flow_csv(text: &str) -> Result<Vec<ChildCreate>, &'static str> {
    const INVALID: &str = "invalid CSV";
    let mut reader = CsvReader {
        state: CsvState::StartRecord,
        record: Vec::new(),
        field: String::new(),
        length: 0,
    };
    let mut header = false;
    let mut children = Vec::new();
    for line in csv_lines(text) {
        for c in line.chars() {
            reader.step(Some(c)).ok_or(INVALID)?;
        }
        reader.step(None).ok_or(INVALID)?;
        if reader.state != CsvState::StartRecord {
            continue;
        }
        let record = std::mem::take(&mut reader.record);
        if !header {
            if record != CSV_HEADER {
                return Err("CSV header mismatch");
            }
            header = true;
        } else if !record.is_empty() {
            children.push(flow_child(&record).ok_or(INVALID)?);
            if children.len() > IMPORT_MAX_ROWS {
                return Err(INVALID);
            }
        }
    }
    if !header {
        return Err("CSV header mismatch");
    }
    if reader.state != CsvState::StartRecord || children.is_empty() {
        return Err(INVALID);
    }
    Ok(children)
}

/// The reader of each column in a flow: the cell as Python int(), float() or a true/false
/// reads it, then the field as pydantic reads that value.
const FLOW_CELLS: [fn(&str) -> Option<Value>; CSV_HEADER.len()] = [@READERS@];

fn flow_child(record: &[String]) -> Option<ChildCreate> {
    if record.len() != CSV_HEADER.len() {
        return None;
    }
    let mut object = serde_json::Map::new();
    for ((name, read), cell) in CSV_HEADER.iter().zip(FLOW_CELLS).zip(record) {
        let value = if cell == NULL_CELL { Value::Null } else { read(cell)? };
        object.insert((*name).to_owned(), value);
    }
    let child: ChildCreate = serde_json::from_value(Value::Object(object)).ok()?;
    child.problems().is_empty().then_some(child)
}
"#;

const FLOW_INT: &str = r#"
/// Python int(): spaces around, one sign, and digits with single underscores between them.
fn flow_int(cell: &str) -> Option<Value> {
    let text = cell.trim();
    let (negative, digits) = match text.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, text.strip_prefix('+').unwrap_or(text)),
    };
    let ok = !digits.is_empty()
        && !digits.starts_with('_')
        && !digits.ends_with('_')
        && !digits.contains("__")
        && digits.bytes().all(|byte| byte.is_ascii_digit() || byte == b'_');
    if !ok {
        return None;
    }
    let magnitude: i64 = digits.replace('_', "").parse().ok()?;
    Some(Value::from(if negative { -magnitude } else { magnitude }))
}
"#;

const FLOW_FLOAT: &str = r#"
/// Python float(), finite: spaces around, and each underscore between two digits.
fn flow_float(cell: &str) -> Option<Value> {
    let text = cell.trim();
    let bytes = text.as_bytes();
    let placed = bytes.iter().enumerate().all(|(index, byte)| {
        *byte != b'_'
            || index > 0
                && bytes[index - 1].is_ascii_digit()
                && bytes.get(index + 1).is_some_and(u8::is_ascii_digit)
    });
    if !placed {
        return None;
    }
    let value: f64 = text.replace('_', "").parse().ok()?;
    value.is_finite().then(|| Value::from(value))
}
"#;

const FLOW_BOOL: &str = r#"
/// A bool cell: true or false in any case.
fn flow_bool(cell: &str) -> Option<Value> {
    match cell.to_lowercase().as_str() {
        "true" => Some(Value::Bool(true)),
        "false" => Some(Value::Bool(false)),
        _ => None,
    }
}
"#;

/// workflow.import needs the csv workflow. compiler-v1 refuses it on any other.
const FLOW_CSV_NONE: &str = r#"
/// workflow.import: compiler-v1 refuses it without the csv workflow.
pub(crate) fn flow_csv(_text: &str) -> Result<Vec<crate::schemas::ChildCreate>, &'static str> {
    Err("invalid CSV")
}
"#;

fn tests(text: String, name: &str) -> Result<String, String> {
    let start = text
        .find("const ROUTES: [(&str, &str); ")
        .ok_or("flow tests have no routes")?;
    let head = &text[start..];
    let count_end = head.find(']').ok_or("flow tests routes")?;
    let count: usize = head["const ROUTES: [(&str, &str); ".len()..count_end]
        .parse()
        .map_err(|_| "flow tests route count")?;
    let close = start + head.find("\n];").ok_or("flow tests routes end")?;
    let routes = format!(
        "\n    (\"POST\", \"/flows/{name}/runs\"),\n    (\"GET\", \"/flow-runs/x\"),\n    (\"POST\", \"/flow-runs/x/recover\"),"
    );
    let text = format!("{}{routes}{}", &text[..close], &text[close..]);
    Ok(text.replacen(
        &format!("const ROUTES: [(&str, &str); {count}]"),
        &format!("const ROUTES: [(&str, &str); {}]", count + 3),
        1,
    ))
}

/// Every resource row carries the revision a compensation checks, then the journal follows.
fn migration(text: String) -> Result<String, String> {
    let id = "\"id\" SERIAL PRIMARY KEY,\n";
    if text.matches(id).count() != 2 {
        return Err("flow migration has no two tables".to_owned());
    }
    Ok(text.replace(
        id,
        &format!("{id}    \"_som_revision\" INTEGER NOT NULL DEFAULT 0,\n"),
    ) + JOURNAL_DDL)
}

const JOURNAL_DDL: &str = r#"
CREATE TABLE "_som_flow_runs" (
    "run_id" VARCHAR(32) PRIMARY KEY,
    "owner" VARCHAR(255) NOT NULL,
    "flow" VARCHAR(80) NOT NULL,
    "key" VARCHAR(128) NOT NULL,
    "request_hash" VARCHAR(64) NOT NULL,
    "epoch" INTEGER NOT NULL,
    "record" JSONB NOT NULL,
    UNIQUE ("owner", "flow", "key")
);

CREATE TABLE "_som_flow_leases" (
    "run_id" VARCHAR(32) PRIMARY KEY REFERENCES "_som_flow_runs" ("run_id") ON DELETE CASCADE,
    "epoch" INTEGER NOT NULL,
    "until" DOUBLE PRECISION NOT NULL
);
"#;

fn kind(field: &Field) -> &'static str {
    match field.kind {
        Kind::Str => "str",
        Kind::Int => "int",
        Kind::Float => "float",
        Kind::Bool => "bool",
        Kind::Date => "date",
    }
}

fn role(name: &str, table: &str, parent: &str, key: &str, fields: &[Field]) -> String {
    let columns = fields
        .iter()
        .map(|field| format!("        ({:?}, {:?}),\n", field.name, kind(field)))
        .collect::<String>();
    format!(
        "const {name}: Role = Role {{\n    child: {},\n    table: {table:?},\n    parent: {parent:?},\n    key: {key:?},\n    columns: &[\n{columns}    ],\n}};\n",
        !parent.is_empty()
    )
}

fn flows(params: &Value, template: &str) -> Result<String, String> {
    let flow = &params["flow"];
    // The runtime interprets the steps the target profile delivers, and no other.
    if !crate::profile::flow_delivered(&flow["steps"]) {
        return Err("flow has a step with no Rust runtime yet".to_owned());
    }
    let product = Product::read(params)?;
    let flow_text = serde_json::to_string(flow).map_err(|error| error.to_string())?;
    let roles = role(
        "PARENT",
        &product.parent.table,
        "",
        "",
        &product.parent.fields,
    ) + "\n"
        + &role(
            "CHILD",
            &product.child.table,
            &product.parent.table,
            &product.key.name,
            &product.child.fields,
        );
    Ok(template
        .replace("@FLOW_TEXT@", &format!("{flow_text:?}"))
        .replace("@FLOW_NAME@", &format!("{:?}", flow_name(params)?))
        .replace("@ROLES@", &roles)
        .replace(
            "@WORKFLOW@",
            &format!("{:?}", params["workflow"].as_str().unwrap_or("json_export")),
        ))
}

const FLOWS: &str = include_str!("flow_runtime.rs.txt");
const FLOWS_MONGO: &str = include_str!("flow_runtime_mongo.rs.txt");
