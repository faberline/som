//! Issue #54: the flow_postgresql blocks. Each one is its capability block with the changes the
//! flow runtime needs: a revision column per row, the run journal, and the src/flows.rs routes.
//! The capability products themselves do not change.

use serde_json::Value;

use super::super::{Field, Kind};
use super::Product;

pub fn render(id: &str, params: &Value) -> Result<String, String> {
    let base = || super::render(&id.replace("capability_flow", "capability"), params);
    Ok(match id {
        "rust.flows.capability_flow" => flows(params)?,
        "rust.lib.capability_flow" => lib(base()?)?,
        "rust.schemas.capability_flow" => {
            let product = Product::read(params)?;
            base()? + "\n" + &super::models(&product)
        }
        "rust.db.capability_flow" => db(base()?)?,
        "rust.api.capability_flow" => api(base()?)?,
        "rust.migration.capability_flow" => migration(base()?)?,
        "rust.tests.capability_flow" => tests(base()?, flow_name(params)?)?,
        _ => base()?,
    })
}

/// Replace the one anchor, or refuse the block: a capability block lost a line the flow needs.
fn once(text: &str, anchor: &str, with: &str) -> Result<String, String> {
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

fn api(text: String) -> Result<String, String> {
    let text = once(
        &text,
        "        .fallback(missing)\n",
        "        .merge(crate::flows::routes())\n        .fallback(missing)\n",
    )?;
    let text = once(&text, "\nfn problem(", "\npub(crate) fn problem(")?;
    let text = once(&text, "\nstruct Body<T> {", "\npub(crate) struct Body<T> {")?;
    let text = once(&text, "    fn finish(self", "    pub(crate) fn finish(self")?;
    once(&text, "\nfn read_body<", "\npub(crate) fn read_body<")
}

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

fn flows(params: &Value) -> Result<String, String> {
    let flow = &params["flow"];
    // The runtime interprets the steps the target profile delivers, and no other.
    if !crate::profile::flow_delivered(&flow["steps"], &params["workflow"]) {
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
    Ok(FLOWS
        .replace("@FLOW_TEXT@", &format!("{flow_text:?}"))
        .replace("@FLOW_NAME@", &format!("{:?}", flow_name(params)?))
        .replace("@ROLES@", &roles))
}

const FLOWS: &str = include_str!("flow_runtime.rs.txt");
