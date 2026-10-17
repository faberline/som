//! The Rust block library (som.rust-block-library.v1).
//!
//! Each admitted rust. block id renders one part of a generated file from its resolved params.
//! Rust item names come from this fixed table by role. Prompt names appear only as struct
//! fields, SQL identifiers in double quotes, JSON members and string literals.
use serde_json::Value;

mod related;

const MANIFEST: &str = include_str!("../../../contracts/rust-output-v1/reference/Cargo.toml");
const LOCK: &str = include_str!("../../../contracts/rust-output-v1/reference/Cargo.lock");

/// Rust keywords that a raw identifier can spell. The ones it cannot spell get an R2 name.
const KEYWORDS: &[&str] = &[
    "abstract", "as", "async", "await", "become", "box", "break", "const", "continue", "do", "dyn",
    "else", "enum", "extern", "false", "final", "fn", "for", "gen", "if", "impl", "in", "let",
    "loop", "macro", "match", "mod", "move", "mut", "override", "priv", "pub", "ref", "return",
    "static", "struct", "trait", "true", "try", "type", "typeof", "unsafe", "unsized", "use",
    "virtual", "where", "while", "yield",
];

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Str,
    Int,
    Float,
    Bool,
    Date,
}

#[derive(Clone)]
struct Field {
    name: String,
    /// The Rust struct member name (naming.json R2).
    rust: String,
    /// The R2 (c) name: field_<k>, or foreign_key for a relation foreign key.
    fallback: String,
    kind: Kind,
    required: bool,
    unique: bool,
    /// Optional with default false: a NOT NULL bool column with DEFAULT false.
    default_false: bool,
}

impl Field {
    fn nullable(&self) -> bool {
        !self.required && !self.default_false
    }
    /// The Rust name differs from the wire name, so serde and sqlx rename it.
    fn raw(&self) -> bool {
        self.rust != self.name
    }
    fn ident(&self) -> String {
        self.rust.clone()
    }
    fn rust(&self) -> &'static str {
        match self.kind {
            Kind::Str => "String",
            Kind::Int => "i32",
            Kind::Float => "f64",
            Kind::Bool => "bool",
            Kind::Date => "chrono::NaiveDate",
        }
    }
    fn stored(&self) -> String {
        if self.nullable() {
            format!("Option<{}>", self.rust())
        } else {
            self.rust().to_owned()
        }
    }
    fn sql(&self) -> &'static str {
        match self.kind {
            Kind::Str => "VARCHAR(255)",
            Kind::Int => "INTEGER",
            Kind::Float => "DOUBLE PRECISION",
            Kind::Bool => "BOOLEAN",
            Kind::Date => "DATE",
        }
    }
    /// A serde attribute line for a struct member, or nothing.
    fn serde(&self, mut options: Vec<String>) -> String {
        if self.raw() {
            options.push(format!("rename = \"{}\"", self.name));
        }
        if options.is_empty() {
            String::new()
        } else {
            format!("    #[serde({})]\n", options.join(", "))
        }
    }
    /// The test sample value for row k, as a Rust expression.
    fn sample(&self, index: usize) -> String {
        match self.kind {
            Kind::Str => format!("json!(format!(\"v{{k}}-{index}\"))"),
            Kind::Int => "json!(100 + k)".to_owned(),
            Kind::Float => "json!(k as f64 + 0.5)".to_owned(),
            Kind::Bool if self.unique && self.nullable() => {
                "if k < 2 { json!(k.is_multiple_of(2)) } else { Value::Null }".to_owned()
            }
            Kind::Bool => "json!(k.is_multiple_of(2))".to_owned(),
            Kind::Date if self.nullable() => {
                "if k % 3 == 2 { Value::Null } else { json!(format!(\"2030-01-{:02}\", k + 1)) }"
                    .to_owned()
            }
            Kind::Date => "json!(format!(\"2030-01-{:02}\", k + 1))".to_owned(),
        }
    }
}

/// naming.json R2 for the members of one struct, in order: every (a) name and every (b) r#name
/// first, then each (c) fallback with trailing underscores until it is unique.
fn named(members: Vec<Field>) -> Vec<Field> {
    let direct = |name: &str| {
        if KEYWORDS.contains(&name) {
            Some(format!("r#{name}"))
        } else if crate::profile::is_rust_snake_case(name)
            && !["crate", "self", "super"].contains(&name)
        {
            Some(name.to_owned())
        } else {
            None
        }
    };
    let mut taken: Vec<String> = members
        .iter()
        .filter(|field| direct(&field.name).is_some())
        .map(|field| field.name.clone())
        .collect();
    members
        .into_iter()
        .map(|mut field| {
            field.rust = direct(&field.name).unwrap_or_else(|| {
                let mut name = field.fallback.clone();
                while taken.contains(&name) {
                    name.push('_');
                }
                taken.push(name.clone());
                name
            });
            field
        })
        .collect()
}

/// The members of a struct that carries a foreign key before the fields, named together.
fn with_key(key: Option<&Field>, fields: &[Field]) -> Vec<Field> {
    named(key.into_iter().chain(fields).cloned().collect())
}

fn parse_fields(value: &Value) -> Result<Vec<Field>, String> {
    let rows = value.as_array().ok_or("fields param is not an array")?;
    let fields: Result<Vec<Field>, String> = rows
        .iter()
        .enumerate()
        .map(|(index, row)| {
            let name = row["name"].as_str().ok_or("field name is not a string")?;
            let kind = match row["type"].as_str() {
                Some("str") => Kind::Str,
                Some("int") => Kind::Int,
                Some("float") => Kind::Float,
                Some("bool") => Kind::Bool,
                Some("date") => Kind::Date,
                _ => return Err(format!("field {name} has no phase 1 type")),
            };
            Ok(Field {
                name: name.to_owned(),
                rust: String::new(),
                fallback: format!("field_{}", index + 1),
                kind,
                required: row["required"] == true,
                unique: row["unique"] == true,
                default_false: row["required"] != true
                    && row.get("default") == Some(&Value::Bool(false)),
            })
        })
        .collect();
    Ok(named(fields?))
}

fn text<'a>(params: &'a Value, key: &str) -> Result<&'a str, String> {
    params[key]
        .as_str()
        .ok_or_else(|| format!("param {key} is not a string"))
}

fn limit(params: &Value) -> Result<i64, String> {
    params["max_limit"]
        .as_i64()
        .ok_or_else(|| "param max_limit is not an integer".to_owned())
}

/// Render one block from its resolved params.
pub fn render(id: &str, params: &Value) -> Result<String, String> {
    if id.contains(".related") {
        return related::render(id, params);
    }
    let fields = || parse_fields(&params["fields"]);
    Ok(match id {
        "rust.cargo.manifest" => MANIFEST.to_owned(),
        "rust.cargo.lock" => LOCK.to_owned(),
        "rust.main.startup" => MAIN.to_owned(),
        "rust.lib.modules" => LIB.to_owned(),
        "rust.lib.migrator" => LIB_MIGRATOR.replace("@REVISION@", text(params, "revision")?),
        "rust.models.row.plain" => row(&fields()?, false),
        "rust.models.row.service" => row(&fields()?, true),
        "rust.schemas.create.plain" => create(&fields()?, false),
        "rust.schemas.create.service" => create(&fields()?, true),
        "rust.schemas.update.plain" => update(&fields()?, false),
        "rust.schemas.update.service" => update(&fields()?, true),
        "rust.schemas.read.plain" => read(&fields()?, false),
        "rust.schemas.read.service" => read(&fields()?, true),
        "rust.api.support" => [API_USES, API_SUPPORT, API_BODY].concat(),
        "rust.api.health" => API_HEALTH.to_owned(),
        "rust.api.list.all" => API_LIST_ALL.to_owned(),
        "rust.api.list.text" => api_list(params, Filter::Text)?,
        "rust.api.list.boolean" => api_list(params, Filter::Boolean)?,
        "rust.api.list.boolean.date" => api_list(params, Filter::BooleanDate)?,
        "rust.api.create" => API_CREATE.to_owned(),
        "rust.api.get" => API_GET.to_owned(),
        "rust.api.update" => API_UPDATE.to_owned(),
        "rust.api.delete" => API_DELETE.to_owned(),
        "rust.api.router" => API_ROUTER.replace("@PATH@", text(params, "path")?),
        "rust.api.router.owned" => API_ROUTER
            .replace(HEALTH_ROUTE, "")
            .replace("@PATH@", text(params, "path")?),
        "rust.db.ping" => DB_PING.to_owned(),
        "rust.db.insert.plain" => db_insert(params, false)?,
        "rust.db.insert.service" => db_insert(params, true)?,
        "rust.db.fetch.plain" => db_fetch(params, false)?,
        "rust.db.fetch.service" => db_fetch(params, true)?,
        "rust.db.list.all" => db_list(params, Filter::All)?,
        "rust.db.list.text" => db_list(params, Filter::Text)?,
        "rust.db.list.boolean" => db_list(params, Filter::Boolean)?,
        "rust.db.list.boolean.date" => db_list(params, Filter::BooleanDate)?,
        "rust.db.update.plain" => db_update(params, false)?,
        "rust.db.update.service" => db_update(params, true)?,
        "rust.db.delete" => db_delete(params)?,
        "rust.migration.table.plain" => migration_table(params, false)?,
        "rust.migration.table.service" => migration_table(params, true)?,
        "rust.migration.indexes.boolean" => migration_indexes(params, false)?,
        "rust.migration.indexes.boolean.date" => migration_indexes(params, true)?,
        "rust.tests.harness" => tests_harness(params)?,
        "rust.tests.health" => TESTS_HEALTH.to_owned(),
        "rust.tests.crud.plain" => tests_crud(&fields()?, false),
        "rust.tests.crud.service" => tests_crud(&fields()?, true),
        "rust.tests.validation.plain" => tests_validation(&fields()?, false),
        "rust.tests.validation.service" => tests_validation(&fields()?, true),
        "rust.tests.unique" => tests_unique(&fields()?),
        "rust.tests.list.all" => TESTS_LIST_ALL.to_owned(),
        "rust.tests.list.text" => tests_list(params, Filter::Text)?,
        "rust.tests.list.boolean" => tests_list(params, Filter::Boolean)?,
        "rust.tests.list.boolean.date" => tests_list(params, Filter::BooleanDate)?,
        "rust.tests.fallback" => TESTS_FALLBACK.to_owned(),
        "rust.readme.all" => readme(params, Filter::All)?,
        "rust.readme.text" => readme(params, Filter::Text)?,
        "rust.readme.boolean" => readme(params, Filter::Boolean)?,
        "rust.readme.boolean.date" => readme(params, Filter::BooleanDate)?,
        _ => return Err(format!("no renderer for block {id}")),
    })
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Filter {
    All,
    Text,
    Boolean,
    BooleanDate,
}

// ---------------------------------------------------------------- src/main.rs, src/lib.rs

const MAIN: &str = r#"use std::process::ExitCode;
use std::time::Duration;

#[tokio::main]
async fn main() -> ExitCode {
    let Ok(url) = std::env::var("DATABASE_URL") else {
        eprintln!("som_app: DATABASE_URL is required");
        return ExitCode::from(2);
    };
    let address = std::env::var("SOM_APP_ADDR").unwrap_or_else(|_| "127.0.0.1:8000".to_owned());
    match serve(&url, &address).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("som_app: {error}");
            ExitCode::FAILURE
        }
    }
}

async fn serve(url: &str, address: &str) -> Result<(), Box<dyn std::error::Error>> {
    let pool = sqlx::postgres::PgPoolOptions::new()
        .acquire_timeout(Duration::from_secs(5))
        .connect(url)
        .await?;
    som_app::MIGRATOR.run(&pool).await?;
    let listener = tokio::net::TcpListener::bind(address).await?;
    axum::serve(listener, som_app::router(pool)).await?;
    Ok(())
}
"#;

const LIB: &str = r#"pub mod api;
pub mod db;
pub mod models;
pub mod schemas;

pub use api::router;

pub static MIGRATOR: sqlx::migrate::Migrator = sqlx::migrate!();
"#;

/// src/lib.rs for a prompt the phase 1 profile refuses: the hand-written migrator records the
/// migration in _som_rust_migrations (naming.json W3), so a prompt table may be _sqlx_migrations.
const LIB_MIGRATOR: &str = r#"pub mod api;
pub mod db;
pub mod models;
pub mod schemas;

pub use api::router;

pub static MIGRATOR: Migrator = Migrator;

const REVISION: &str = "@REVISION@";
const UPGRADE: &str = include_str!("../migrations/0001_@REVISION@.sql");

/// Applies the one migration once, in a transaction, and records it in _som_rust_migrations.
pub struct Migrator;

impl Migrator {
    pub async fn run(&self, pool: &sqlx::PgPool) -> Result<(), sqlx::Error> {
        let mut tx = pool.begin().await?;
        sqlx::query(
            "CREATE TABLE IF NOT EXISTS \"_som_rust_migrations\" (\"version\" BIGINT PRIMARY KEY, \"revision\" TEXT NOT NULL, \"applied_at\" TIMESTAMPTZ NOT NULL DEFAULT now())",
        )
        .execute(&mut *tx)
        .await?;
        let applied: Option<i64> =
            sqlx::query_scalar("SELECT \"version\" FROM \"_som_rust_migrations\" WHERE \"version\" = 1")
                .fetch_optional(&mut *tx)
                .await?;
        if applied.is_none() {
            for statement in UPGRADE.split(";\n").filter(|statement| !statement.trim().is_empty()) {
                sqlx::query(statement).execute(&mut *tx).await?;
            }
            sqlx::query("INSERT INTO \"_som_rust_migrations\" (\"version\", \"revision\") VALUES (1, $1)")
                .bind(REVISION)
                .execute(&mut *tx)
                .await?;
        }
        tx.commit().await
    }
}
"#;

// ---------------------------------------------------------------- src/models.rs

fn row(fields: &[Field], service: bool) -> String {
    row_struct("Row", None, fields, service)
}

/// A FromRow struct: id, the foreign key if any, the fields, and the timestamps if service.
fn row_struct(name: &str, key: Option<&Field>, fields: &[Field], service: bool) -> String {
    let mut out =
        format!("#[derive(Debug, sqlx::FromRow)]\npub struct {name} {{\n    pub id: i32,\n");
    for field in &with_key(key, fields) {
        if field.raw() {
            out += &format!("    #[sqlx(rename = \"{}\")]\n", field.name);
        }
        out += &format!("    pub {}: {},\n", field.ident(), field.stored());
    }
    if service {
        out += "    pub created_at: chrono::DateTime<chrono::Utc>,\n";
        out += "    pub updated_at: chrono::DateTime<chrono::Utc>,\n";
    }
    out + "}\n"
}

// ---------------------------------------------------------------- src/schemas.rs

fn problems(checks: &[String]) -> String {
    let mut out = String::from("    pub fn problems(&self) -> Vec<serde_json::Value> {\n");
    if checks.is_empty() {
        out += "        Vec::new()\n";
    } else {
        out += "        let mut found = Vec::new();\n";
        for check in checks {
            out += check;
        }
        out += "        found\n";
    }
    out + "    }\n"
}

const SCHEMA_USES: &str = "use serde::{Deserialize, Deserializer, Serialize};\n\n";

const TOO_LONG: &str = r#"fn too_long(name: &str) -> serde_json::Value {
    serde_json::json!({"type": "string_too_long", "loc": ["body", name], "msg": "String should have at most 255 characters"})
}

"#;

const NOT_NULL: &str = r#"fn not_null(name: &str) -> serde_json::Value {
    serde_json::json!({"type": "value_error", "loc": ["body"], "msg": format!("Value error, {name} cannot be null")})
}

"#;

const PRESENT: &str = r#"/// A present member, including null, is Some. An absent member stays None through default.
fn present<'de, D, T>(deserializer: D) -> Result<Option<Option<T>>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer).map(Some)
}
"#;

fn create(fields: &[Field], service: bool) -> String {
    let mut out = String::from(SCHEMA_USES);
    if fields.iter().any(|field| field.kind == Kind::Str) {
        out += TOO_LONG;
    }
    out + &create_struct("Create", fields, service)
}

/// A create body and its problems(): strings over 255 characters.
fn create_struct(name: &str, fields: &[Field], service: bool) -> String {
    let mut out = String::from("#[derive(Debug, Deserialize)]\n");
    if service {
        out += "#[serde(deny_unknown_fields)]\n";
    }
    out += &format!("pub struct {name} {{\n");
    let mut checks = Vec::new();
    for field in fields {
        let options = if field.required {
            Vec::new()
        } else {
            vec!["default".to_owned()]
        };
        out += &field.serde(options);
        out += &format!("    pub {}: {},\n", field.ident(), field.stored());
        if field.kind == Kind::Str {
            let test = if field.nullable() {
                format!(
                    "matches!(&self.{}, Some(value) if value.chars().count() > 255)",
                    field.ident()
                )
            } else {
                format!("self.{}.chars().count() > 255", field.ident())
            };
            checks.push(format!(
                "        if {test} {{\n            found.push(too_long(\"{}\"));\n        }}\n",
                field.name
            ));
        }
    }
    out += &format!("}}\n\nimpl {name} {{\n");
    out += &problems(&checks);
    out + "}\n"
}

fn update(fields: &[Field], service: bool) -> String {
    let mut out = String::new();
    if service && fields.iter().any(|field| !field.nullable()) {
        out += NOT_NULL;
    }
    let (shape, checks) = update_struct("Update", fields, service);
    out + &shape + "\n" + PRESENT + "\n" + &checks
}

/// A patch body with present() members, and its impl with problems(): strings over 255
/// characters and, if service, nulls for NOT NULL columns.
fn update_struct(name: &str, fields: &[Field], service: bool) -> (String, String) {
    let mut out = String::from("#[derive(Debug, Deserialize)]\n");
    if service {
        out += "#[serde(deny_unknown_fields)]\n";
    }
    out += &format!("pub struct {name} {{\n");
    let mut checks = Vec::new();
    for field in fields {
        out += &field.serde(vec![
            "default".to_owned(),
            "deserialize_with = \"present\"".to_owned(),
        ]);
        out += &format!(
            "    pub {}: Option<Option<{}>>,\n",
            field.ident(),
            field.rust()
        );
        if field.kind == Kind::Str {
            checks.push(format!(
                "        if matches!(&self.{}, Some(Some(value)) if value.chars().count() > 255) {{\n            found.push(too_long(\"{}\"));\n        }}\n",
                field.ident(),
                field.name
            ));
        }
        if service && !field.nullable() {
            checks.push(format!(
                "        if matches!(self.{}, Some(None)) {{\n            found.push(not_null(\"{}\"));\n        }}\n",
                field.ident(),
                field.name
            ));
        }
    }
    (
        out + "}\n",
        format!("impl {name} {{\n{}}}\n", problems(&checks)),
    )
}

fn read(fields: &[Field], service: bool) -> String {
    read_struct("Read", "Row", None, fields, service)
}

/// A response struct and its From for the row struct.
fn read_struct(
    name: &str,
    row: &str,
    key: Option<&Field>,
    fields: &[Field],
    service: bool,
) -> String {
    let mut out = format!("#[derive(Debug, Serialize)]\npub struct {name} {{\n    pub id: i32,\n");
    let mut moves = String::from("            id: row.id,\n");
    for field in &with_key(key, fields) {
        out += &field.serde(Vec::new());
        out += &format!("    pub {}: {},\n", field.ident(), field.stored());
        moves += &format!("            {0}: row.{0},\n", field.ident());
    }
    if service {
        out += "    pub created_at: chrono::DateTime<chrono::Utc>,\n";
        out += "    pub updated_at: chrono::DateTime<chrono::Utc>,\n";
        moves +=
            "            created_at: row.created_at,\n            updated_at: row.updated_at,\n";
    }
    out += &format!(
        "}}\n\nimpl From<crate::models::{row}> for {name} {{\n    fn from(row: crate::models::{row}) -> Self {{\n        Self {{\n"
    );
    out += &moves;
    out + "        }\n    }\n}\n"
}

// ---------------------------------------------------------------- src/api.rs

/// The uses that the v1 to v4 and the v5 support share.
const API_USES: &str = r#"use axum::{
    Json, Router,
    body::Bytes,
    extract::{
        Path, State,
        rejection::{BytesRejection, PathRejection},
    },
    http::{HeaderMap, StatusCode, header::CONTENT_TYPE},
    response::{IntoResponse, Response},
    routing::get,
};
use serde_json::{Value, json};
use sqlx::PgPool;

use crate::db;
"#;

const API_SUPPORT: &str = r#"use crate::schemas::{Create, Read, Update};

const NOT_INTEGER: &str = "Input should be a valid integer, unable to parse string as an integer";

pub enum ApiError {
    NotFound,
    Conflict,
    Invalid(Vec<Value>),
    Internal,
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let (status, body) = match self {
            Self::NotFound => (StatusCode::NOT_FOUND, json!({"detail": "not found"})),
            Self::Conflict => (StatusCode::CONFLICT, json!({"detail": "conflict"})),
            Self::Invalid(problems) => (StatusCode::UNPROCESSABLE_ENTITY, json!({"detail": problems})),
            Self::Internal => (StatusCode::INTERNAL_SERVER_ERROR, json!({"detail": "Internal Server Error"})),
        };
        (status, Json(body)).into_response()
    }
}

/// PostgreSQL integrity constraint violations (SQLSTATE class 23) are conflicts.
impl From<sqlx::Error> for ApiError {
    fn from(error: sqlx::Error) -> Self {
        match error.as_database_error().and_then(|found| found.code()) {
            Some(code) if code.starts_with("23") => Self::Conflict,
            _ => Self::Internal,
        }
    }
}

fn problem(kind: &str, loc: Value, msg: &str) -> Value {
    json!({"type": kind, "loc": loc, "msg": msg})
}

/// An optional sign and ASCII digits, else a path problem. Outside i32 there is no row.
fn item_id(path: Result<Path<String>, PathRejection>, problems: &mut Vec<Value>) -> Option<i32> {
    let text = match path {
        Ok(Path(text)) => text,
        Err(_) => String::new(),
    };
    let digits = text.strip_prefix(['+', '-']).unwrap_or(&text);
    if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        problems.push(problem("int_parsing", json!(["path", "item_id"]), NOT_INTEGER));
        return None;
    }
    text.parse().ok()
}

"#;

/// The body readers that the v1 to v4 and the v5 support share.
const API_BODY: &str = r#"/// No content type, application/json or application/*+json.
fn json_type(headers: &HeaderMap) -> bool {
    let Some(value) = headers.get(CONTENT_TYPE) else {
        return true;
    };
    let Ok(text) = value.to_str() else {
        return false;
    };
    let media = text.split(';').next().unwrap_or_default().trim().to_ascii_lowercase();
    media == "application/json" || (media.starts_with("application/") && media.ends_with("+json"))
}

/// A JSON syntax error refuses at once. Other body problems join the path problems.
fn read_body<T: serde::de::DeserializeOwned>(
    headers: &HeaderMap,
    payload: Result<Bytes, BytesRejection>,
    problems: &mut Vec<Value>,
) -> Result<Option<T>, ApiError> {
    let Ok(bytes) = payload else {
        problems.push(problem("value_error", json!(["body"]), "The request body could not be read"));
        return Ok(None);
    };
    if bytes.is_empty() {
        problems.push(problem("missing", json!(["body"]), "Field required"));
        return Ok(None);
    }
    if !json_type(headers) {
        problems.push(problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
        ));
        return Ok(None);
    }
    let value: Value = serde_json::from_slice(&bytes).map_err(|error| {
        ApiError::Invalid(vec![problem("json_invalid", json!(["body", error.column()]), "JSON decode error")])
    })?;
    if !value.is_object() {
        problems.push(problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
        ));
        return Ok(None);
    }
    match serde_json::from_value::<T>(value) {
        Ok(body) => Ok(Some(body)),
        Err(error) => {
            problems.push(problem("value_error", json!(["body"]), &error.to_string()));
            Ok(None)
        }
    }
}
"#;

const API_HEALTH: &str = r#"async fn health(State(pool): State<PgPool>) -> Response {
    match db::ping(&pool).await {
        Ok(()) => Json(json!({"status": "ok"})).into_response(),
        Err(_) => (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"detail": "database unavailable"}))).into_response(),
    }
}
"#;

const API_LIST_ALL: &str = r#"async fn list(State(pool): State<PgPool>) -> Result<Json<Vec<Read>>, ApiError> {
    let rows = db::list(&pool).await?;
    Ok(Json(rows.into_iter().map(Read::from).collect()))
}
"#;

const API_LIST_HELPERS: &str = r#"type Pairs = Result<axum::extract::Query<Vec<(String, String)>>, axum::extract::rejection::QueryRejection>;

/// The only value of a query parameter. A repeated parameter is a query problem.
fn single<'a>(pairs: &'a [(String, String)], name: &str, problems: &mut Vec<Value>) -> Option<&'a str> {
    let mut found = pairs.iter().filter(|(key, _)| key == name).map(|(_, value)| value.as_str());
    let first = found.next();
    if found.next().is_some() {
        problems.push(problem("value_error", json!(["query", name]), "Give this query parameter at most once"));
        return None;
    }
    first
}

fn number(text: &str, name: &str, low: i64, high: i64, problems: &mut Vec<Value>) -> Option<i64> {
    let Ok(value) = text.parse::<i64>() else {
        problems.push(problem("int_parsing", json!(["query", name]), NOT_INTEGER));
        return None;
    };
    if value < low {
        problems.push(problem(
            "greater_than_equal",
            json!(["query", name]),
            &format!("Input should be greater than or equal to {low}"),
        ));
        return None;
    }
    if value > high {
        problems.push(problem(
            "less_than_equal",
            json!(["query", name]),
            &format!("Input should be less than or equal to {high}"),
        ));
        return None;
    }
    Some(value)
}
"#;

const API_FLAG: &str = r#"
fn flag(text: &str, name: &str, problems: &mut Vec<Value>) -> Option<bool> {
    match text.to_ascii_lowercase().as_str() {
        "1" | "on" | "t" | "true" | "y" | "yes" => Some(true),
        "0" | "off" | "f" | "false" | "n" | "no" => Some(false),
        _ => {
            problems.push(problem(
                "bool_parsing",
                json!(["query", name]),
                "Input should be a valid boolean, unable to interpret input",
            ));
            None
        }
    }
}
"#;

const API_DAY: &str = r#"
fn day(text: &str, name: &str, problems: &mut Vec<Value>) -> Option<chrono::NaiveDate> {
    let parsed = if text.len() == 10 {
        chrono::NaiveDate::parse_from_str(text, "%Y-%m-%d").ok()
    } else {
        None
    };
    if parsed.is_none() {
        problems.push(problem("date_from_datetime_parsing", json!(["query", name]), "Input should be a valid date"));
    }
    parsed
}
"#;

fn api_list(params: &Value, filter: Filter) -> Result<String, String> {
    let max = limit(params)?;
    let mut out = String::from(API_LIST_HELPERS);
    let (filter_line, call) = match filter {
        Filter::Text => (
            "    let filter = single(&pairs, \"filter_value\", &mut problems);\n".to_owned(),
            "db::list(&pool, filter, limit, offset)",
        ),
        _ => {
            let name = text(params, "filter_field")?;
            out += API_FLAG;
            (
                format!(
                    "    let filter = single(&pairs, \"{name}\", &mut problems).and_then(|text| flag(text, \"{name}\", &mut problems));\n"
                ),
                if filter == Filter::BooleanDate {
                    "db::list(&pool, filter, before, limit, offset)"
                } else {
                    "db::list(&pool, filter, limit, offset)"
                },
            )
        }
    };
    if filter == Filter::BooleanDate {
        out += API_DAY;
    }
    out += &format!("\nconst MAX_LIMIT: i64 = {max};\n\n");
    out += r#"async fn list(State(pool): State<PgPool>, query: Pairs) -> Result<Json<Vec<Read>>, ApiError> {
    let Ok(axum::extract::Query(pairs)) = query else {
        return Err(ApiError::Invalid(vec![problem("value_error", json!(["query"]), "The query string could not be read")]));
    };
    let mut problems = Vec::new();
"#;
    out += &filter_line;
    if filter == Filter::BooleanDate {
        out += "    let before = single(&pairs, \"before\", &mut problems).and_then(|text| day(text, \"before\", &mut problems));\n";
    }
    out += r#"    let limit = single(&pairs, "limit", &mut problems).map_or(Some(20), |text| number(text, "limit", 1, MAX_LIMIT, &mut problems));
    let offset = single(&pairs, "offset", &mut problems).map_or(Some(0), |text| number(text, "offset", 0, i64::MAX, &mut problems));
    let (Some(limit), Some(offset), true) = (limit, offset, problems.is_empty()) else {
        return Err(ApiError::Invalid(problems));
    };
"#;
    out += &format!("    let rows = {call}.await?;\n");
    out += "    Ok(Json(rows.into_iter().map(Read::from).collect()))\n}\n";
    Ok(out)
}

const API_CREATE: &str = r#"async fn create(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<(StatusCode, Json<Read>), ApiError> {
    let mut problems = Vec::new();
    let body = read_body::<Create>(&headers, payload, &mut problems)?;
    if let Some(body) = &body {
        problems.extend(body.problems());
    }
    let (Some(body), true) = (body, problems.is_empty()) else {
        return Err(ApiError::Invalid(problems));
    };
    let row = db::insert(&pool, body).await?;
    Ok((StatusCode::CREATED, Json(Read::from(row))))
}
"#;

const API_GET: &str = r#"async fn fetch(
    State(pool): State<PgPool>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Json<Read>, ApiError> {
    let mut problems = Vec::new();
    let id = item_id(path, &mut problems);
    if !problems.is_empty() {
        return Err(ApiError::Invalid(problems));
    }
    let id = id.ok_or(ApiError::NotFound)?;
    let row = db::fetch(&pool, id).await?.ok_or(ApiError::NotFound)?;
    Ok(Json(Read::from(row)))
}
"#;

const API_UPDATE: &str = r#"async fn update(
    State(pool): State<PgPool>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<Json<Read>, ApiError> {
    let mut problems = Vec::new();
    let id = item_id(path, &mut problems);
    let body = read_body::<Update>(&headers, payload, &mut problems)?;
    if let Some(body) = &body {
        problems.extend(body.problems());
    }
    let (Some(body), true) = (body, problems.is_empty()) else {
        return Err(ApiError::Invalid(problems));
    };
    let id = id.ok_or(ApiError::NotFound)?;
    let row = db::update(&pool, id, body).await?.ok_or(ApiError::NotFound)?;
    Ok(Json(Read::from(row)))
}
"#;

const API_DELETE: &str = r#"async fn remove(
    State(pool): State<PgPool>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ApiError> {
    let mut problems = Vec::new();
    let id = item_id(path, &mut problems);
    if !problems.is_empty() {
        return Err(ApiError::Invalid(problems));
    }
    let id = id.ok_or(ApiError::NotFound)?;
    if db::delete(&pool, id).await? {
        // As in the Python product, the empty 204 answer keeps the JSON media type.
        Ok((StatusCode::NO_CONTENT, [(CONTENT_TYPE, "application/json")]).into_response())
    } else {
        Err(ApiError::NotFound)
    }
}
"#;

/// The fixed health route. A resource whose path is /healthz owns that path (naming.json W2).
pub(super) const HEALTH_ROUTE: &str = "        .route(\"/healthz\", get(health))\n";

const API_ROUTER: &str = r#"pub fn router(pool: PgPool) -> Router {
    Router::new()
        .route("/healthz", get(health))
        .route("@PATH@", get(list).post(create))
        .route("@PATH@/{id}", get(fetch).patch(update).delete(remove))
        .fallback(missing)
        .method_not_allowed_fallback(not_allowed)
        .with_state(pool)
}

async fn missing() -> Response {
    (StatusCode::NOT_FOUND, Json(json!({"detail": "Not Found"}))).into_response()
}

async fn not_allowed() -> Response {
    (StatusCode::METHOD_NOT_ALLOWED, Json(json!({"detail": "Method Not Allowed"}))).into_response()
}
"#;

// ---------------------------------------------------------------- src/db.rs

const DB_PING: &str = r#"use sqlx::PgPool;

use crate::models::Row;
use crate::schemas::{Create, Update};

pub async fn ping(pool: &PgPool) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT 1").execute(pool).await.map(|_| ())
}
"#;

fn quoted(name: &str) -> String {
    format!("\"{name}\"")
}

fn columns(fields: &[Field], service: bool) -> String {
    let mut names = vec![quoted("id")];
    names.extend(fields.iter().map(|field| quoted(&field.name)));
    if service {
        names.push(quoted("created_at"));
        names.push(quoted("updated_at"));
    }
    names.join(", ")
}

/// A Rust raw string literal holding SQL. SQL text never contains a hash mark.
fn sql_const(name: &str, sql: &str) -> String {
    format!("const {name}: &str = r#\"{sql}\"#;\n\n")
}

fn db_insert(params: &Value, service: bool) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let table = text(params, "table")?;
    let names: Vec<String> = fields.iter().map(|field| quoted(&field.name)).collect();
    let slots: Vec<String> = (1..=fields.len())
        .map(|index| format!("${index}"))
        .collect();
    let mut out = sql_const(
        "INSERT",
        &format!(
            "INSERT INTO {} ({}) VALUES ({}) RETURNING {}",
            quoted(table),
            names.join(", "),
            slots.join(", "),
            columns(&fields, service)
        ),
    );
    out += "pub async fn insert(pool: &PgPool, body: Create) -> Result<Row, sqlx::Error> {\n    sqlx::query_as::<_, Row>(INSERT)\n";
    for field in &fields {
        out += &format!("        .bind(body.{})\n", field.ident());
    }
    out += "        .fetch_one(pool)\n        .await\n}\n";
    Ok(out)
}

fn db_fetch(params: &Value, service: bool) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let table = text(params, "table")?;
    let mut out = sql_const(
        "FETCH",
        &format!(
            "SELECT {} FROM {} WHERE \"id\" = $1",
            columns(&fields, service),
            quoted(table)
        ),
    );
    out += "pub async fn fetch(pool: &PgPool, id: i32) -> Result<Option<Row>, sqlx::Error> {\n    sqlx::query_as::<_, Row>(FETCH).bind(id).fetch_optional(pool).await\n}\n";
    Ok(out)
}

fn db_list(params: &Value, filter: Filter) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let table = text(params, "table")?;
    let service = matches!(filter, Filter::Boolean | Filter::BooleanDate);
    let select = format!(
        "SELECT {} FROM {}",
        columns(&fields, service),
        quoted(table)
    );
    Ok(match filter {
        Filter::All => {
            sql_const("LIST", &format!("{select} ORDER BY \"id\""))
                + "pub async fn list(pool: &PgPool) -> Result<Vec<Row>, sqlx::Error> {\n    sqlx::query_as::<_, Row>(LIST).fetch_all(pool).await\n}\n"
        }
        Filter::Text | Filter::Boolean => {
            let (cast, rust) = if filter == Filter::Text {
                ("text", "Option<&str>")
            } else {
                ("boolean", "Option<bool>")
            };
            let field = quoted(text(params, "filter_field")?);
            sql_const(
                "LIST",
                &format!(
                    "{select} WHERE ($1::{cast} IS NULL OR {field} = $1) ORDER BY \"id\" LIMIT $2 OFFSET $3"
                ),
            ) + &format!(
                "pub async fn list(pool: &PgPool, filter: {rust}, limit: i64, offset: i64) -> Result<Vec<Row>, sqlx::Error> {{\n    sqlx::query_as::<_, Row>(LIST).bind(filter).bind(limit).bind(offset).fetch_all(pool).await\n}}\n"
            )
        }
        Filter::BooleanDate => {
            let field = quoted(text(params, "filter_field")?);
            let date = quoted(text(params, "date_field")?);
            sql_const(
                "LIST",
                &format!(
                    "{select} WHERE ($1::boolean IS NULL OR {field} = $1) AND ($2::date IS NULL OR {date} < $2) ORDER BY \"id\" LIMIT $3 OFFSET $4"
                ),
            ) + "pub async fn list(\n    pool: &PgPool,\n    filter: Option<bool>,\n    before: Option<chrono::NaiveDate>,\n    limit: i64,\n    offset: i64,\n) -> Result<Vec<Row>, sqlx::Error> {\n    sqlx::query_as::<_, Row>(LIST).bind(filter).bind(before).bind(limit).bind(offset).fetch_all(pool).await\n}\n"
        }
    })
}

fn db_update(params: &Value, service: bool) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let table = text(params, "table")?;
    let mut sets = Vec::new();
    let mut flags = Vec::new();
    for (index, field) in fields.iter().enumerate() {
        let column = quoted(&field.name);
        let flag = 2 + 2 * index;
        sets.push(format!(
            "{column} = CASE WHEN ${flag} THEN ${} ELSE {column} END",
            flag + 1
        ));
        flags.push(format!("${flag}"));
    }
    if service {
        sets.push(format!(
            "\"updated_at\" = CASE WHEN {} THEN now() ELSE \"updated_at\" END",
            flags.join(" OR ")
        ));
    }
    let mut out = sql_const(
        "UPDATE",
        &format!(
            "UPDATE {} SET {} WHERE \"id\" = $1 RETURNING {}",
            quoted(table),
            sets.join(", "),
            columns(&fields, service)
        ),
    );
    out += "/// Each member binds whether it was present, then its value. Absent members keep the column.\npub async fn update(pool: &PgPool, id: i32, body: Update) -> Result<Option<Row>, sqlx::Error> {\n    sqlx::query_as::<_, Row>(UPDATE)\n        .bind(id)\n";
    for field in &fields {
        out += &format!(
            "        .bind(body.{0}.is_some())\n        .bind(body.{0}.flatten())\n",
            field.ident()
        );
    }
    out += "        .fetch_optional(pool)\n        .await\n}\n";
    Ok(out)
}

fn db_delete(params: &Value) -> Result<String, String> {
    let table = text(params, "table")?;
    Ok(sql_const(
        "DELETE",
        &format!("DELETE FROM {} WHERE \"id\" = $1", quoted(table)),
    ) + "pub async fn delete(pool: &PgPool, id: i32) -> Result<bool, sqlx::Error> {\n    let done = sqlx::query(DELETE).bind(id).execute(pool).await?;\n    Ok(done.rows_affected() > 0)\n}\n")
}

// ---------------------------------------------------------------- migrations/0001_<revision>.sql

fn migration_table(params: &Value, service: bool) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let table = text(params, "table")?;
    let revision = text(params, "revision")?;
    let down = params["down_revision"].as_str().unwrap_or("none");
    let mut lines = vec!["    \"id\" SERIAL PRIMARY KEY".to_owned()];
    lines.extend(fields.iter().map(column_line));
    if service {
        // Amendment 1: v3 and v4 use CURRENT_TIMESTAMP; v5 output (#49) must use now().
        for name in ["created_at", "updated_at"] {
            lines.push(format!(
                "    \"{name}\" TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ));
        }
    }
    Ok(format!(
        "-- revision: {revision}, down_revision: {down}\nCREATE TABLE {} (\n{}\n);\n",
        quoted(table),
        lines.join(",\n")
    ))
}

/// A column definition line of CREATE TABLE.
fn column_line(field: &Field) -> String {
    let mut line = format!("    {} {}", quoted(&field.name), field.sql());
    if !field.nullable() {
        line += " NOT NULL";
    }
    if field.unique {
        line += " UNIQUE";
    }
    if field.default_false {
        line += " DEFAULT false";
    }
    line
}

fn migration_indexes(params: &Value, date: bool) -> Result<String, String> {
    let table = text(params, "table")?;
    let mut names = vec![text(params, "filter_field")?];
    if date {
        names.push(text(params, "date_field")?);
    }
    let mut out = String::new();
    for name in names {
        out += &format!(
            "\nCREATE INDEX {} ON {} ({}, \"id\");\n",
            quoted(&format!("ix_{table}_{name}_id")),
            quoted(table),
            quoted(name)
        );
    }
    Ok(out)
}

// ---------------------------------------------------------------- tests/api.rs

/// A Map built member by member, as a Rust expression of type Value.
fn object(members: &[(String, String)]) -> String {
    if members.is_empty() {
        return "Value::Object(serde_json::Map::new())".to_owned();
    }
    let mut out = String::from("{\n        let mut body = serde_json::Map::new();\n");
    for (name, value) in members {
        out += &format!("        body.insert(\"{name}\".to_owned(), {value});\n");
    }
    out + "        Value::Object(body)\n    }"
}

fn sample_fn(name: &str, doc: &str, members: &[(String, String)]) -> String {
    let uses_k = members.iter().any(|(_, value)| value.contains('k'));
    let parameter = if uses_k { "k" } else { "_k" };
    format!(
        "/// {doc}\nfn {name}({parameter}: usize) -> Value {}\n\n",
        object(members)
    )
}

/// The sample, minimal and defaults fns of row k. A role prefixes their names.
fn sample_fns(role: &str, fields: &[Field]) -> String {
    let (prefix, row) = if role.is_empty() {
        (String::new(), "row".to_owned())
    } else {
        (format!("{role}_"), format!("{role} row"))
    };
    let sample: Vec<(String, String)> = fields
        .iter()
        .enumerate()
        .map(|(index, field)| (field.name.clone(), field.sample(index)))
        .collect();
    let minimal: Vec<(String, String)> = fields
        .iter()
        .enumerate()
        .filter(|(_, field)| field.required)
        .map(|(index, field)| (field.name.clone(), field.sample(index)))
        .collect();
    let defaults: Vec<(String, String)> = fields
        .iter()
        .enumerate()
        .map(|(index, field)| {
            let value = if field.required {
                field.sample(index)
            } else if field.default_false {
                "json!(false)".to_owned()
            } else {
                "Value::Null".to_owned()
            };
            (field.name.clone(), value)
        })
        .collect();
    sample_fn(
        &format!("{prefix}sample"),
        &format!("Valid values for every field of {row} k."),
        &sample,
    ) + &sample_fn(
        &format!("{prefix}minimal"),
        &format!("Only the required fields of {row} k."),
        &minimal,
    ) + &sample_fn(
        &format!("{prefix}defaults"),
        &format!("The fields that {prefix}minimal(k) creates, with the null and false defaults."),
        &defaults,
    )
}

fn tests_harness(params: &Value) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let path = text(params, "path")?;
    let rows = if fields
        .iter()
        .any(|field| field.kind == Kind::Bool && field.unique && !field.nullable())
    {
        2
    } else {
        25
    };
    let mut out = format!(
        "{HARNESS_USES}const PATH: &str = \"{path}\";\nconst ROWS: usize = {rows};\n\n{HARNESS_TOOLS}fn item(body: &Value) -> String {{\n    format!(\"{{PATH}}/{{}}\", body[\"id\"])\n}}\n\n"
    );
    out += &sample_fns("", &fields);
    out += r#"async fn create(app: &Router, k: usize) -> Value {
    let (status, body) = send(app, "POST", PATH, Some(sample(k))).await;
    assert_eq!(status, StatusCode::CREATED, "{body}");
    body
}
"#;
    Ok(out)
}

const HARNESS_USES: &str = r#"use axum::{
    Router,
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use tower::ServiceExt;

"#;

const HARNESS_TOOLS: &str = r#"/// A fresh schema public on TEST_DATABASE_URL. A missing database fails the test.
async fn app() -> Router {
    let url = std::env::var("TEST_DATABASE_URL").expect("TEST_DATABASE_URL is required");
    let pool = sqlx::PgPool::connect(&url).await.expect("connect to TEST_DATABASE_URL");
    sqlx::query("DROP SCHEMA public CASCADE").execute(&pool).await.expect("drop schema public");
    sqlx::query("CREATE SCHEMA public").execute(&pool).await.expect("create schema public");
    som_app::MIGRATOR.run(&pool).await.expect("run migrations");
    som_app::router(pool)
}

async fn send_request(app: &Router, request: Request<Body>) -> (StatusCode, Value) {
    let response = app.clone().oneshot(request).await.expect("response");
    let status = response.status();
    let bytes = response.into_body().collect().await.expect("body").to_bytes();
    let body = if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&bytes).expect("JSON body")
    };
    (status, body)
}

async fn send(app: &Router, method: &str, uri: &str, body: Option<Value>) -> (StatusCode, Value) {
    let builder = Request::builder().method(method).uri(uri);
    let request = match body {
        Some(body) => builder.header("content-type", "application/json").body(Body::from(body.to_string())),
        None => builder.body(Body::empty()),
    };
    send_request(app, request.expect("request")).await
}

async fn send_raw(app: &Router, method: &str, uri: &str, content_type: &str, body: &str) -> (StatusCode, Value) {
    let request = Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", content_type)
        .body(Body::from(body.to_owned()))
        .expect("request");
    send_request(app, request).await
}

/// The sorted loc[0] values of a 422 body.
fn locs(body: &Value) -> Vec<String> {
    let mut found: Vec<String> = body["detail"]
        .as_array()
        .expect("422 detail list")
        .iter()
        .map(|item| item["loc"][0].as_str().expect("loc[0]").to_owned())
        .collect();
    found.sort();
    found.dedup();
    found
}

/// A row without its timestamps.
fn fields(body: &Value) -> Value {
    let mut body = body.clone();
    if let Some(members) = body.as_object_mut() {
        members.remove("created_at");
        members.remove("updated_at");
    }
    body
}

"#;

const TESTS_HEALTH: &str = r#"#[tokio::test]
async fn health_reports_ok() {
    let app = app().await;
    let (status, body) = send(&app, "GET", "/healthz", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"status": "ok"}));
}
"#;

const STAMPS: &str = r#"type Stamp = chrono::DateTime<chrono::FixedOffset>;

/// created_at and updated_at as RFC 3339 timestamps with a UTC offset.
fn stamps(body: &Value) -> (Stamp, Stamp) {
    let read = |name: &str| {
        let text = body[name].as_str().expect("timestamp string");
        let parsed = chrono::DateTime::parse_from_rfc3339(text).expect("RFC 3339 timestamp");
        assert_eq!(parsed.offset().local_minus_utc(), 0, "{text}");
        parsed
    };
    let (created, updated) = (read("created_at"), read("updated_at"));
    assert!(created <= updated, "{body}");
    (created, updated)
}

"#;

fn tests_crud(_fields: &[Field], service: bool) -> String {
    let mut out = String::new();
    if service {
        out += STAMPS;
    }
    out += r#"#[tokio::test]
async fn crud_round_trip() {
    let app = app().await;
    let created = create(&app, 0).await;
    let mut wanted = sample(0);
    wanted["id"] = created["id"].clone();
    assert_eq!(fields(&created), wanted);
"#;
    if service {
        out += "    let (first, _) = stamps(&created);\n";
    }
    out += r#"    let uri = item(&created);
    let (status, body) = send(&app, "GET", &uri, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, created);
    let (status, body) = send(&app, "GET", PATH, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!([created]));
    let mut last = created.clone();
    for (name, value) in sample(1).as_object().expect("sample object") {
        let mut patch = serde_json::Map::new();
        patch.insert(name.clone(), value.clone());
        let (status, body) = send(&app, "PATCH", &uri, Some(Value::Object(patch))).await;
        assert_eq!(status, StatusCode::OK, "{body}");
        wanted[name.as_str()] = value.clone();
        assert_eq!(fields(&body), wanted);
"#;
    if service {
        out += "        let (created_at, _) = stamps(&body);\n        assert_eq!(created_at, first);\n";
    }
    out += r#"        last = body;
    }
    let (status, body) = send(&app, "PATCH", &uri, Some(json!({}))).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, last);
    let request = Request::builder().method("DELETE").uri(uri.as_str()).body(Body::empty()).expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::NO_CONTENT);
    assert_eq!(response.headers()["content-type"], "application/json");
    assert!(response.into_body().collect().await.expect("body").to_bytes().is_empty());
    for (method, payload) in [("GET", None), ("DELETE", None), ("PATCH", Some(json!({})))] {
        let (status, body) = send(&app, method, &uri, payload).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{method}");
        assert_eq!(body, json!({"detail": "not found"}));
    }
    let (status, body) = send(&app, "POST", PATH, Some(minimal(2))).await;
    assert_eq!(status, StatusCode::CREATED, "{body}");
    let mut wanted = defaults(2);
    wanted["id"] = body["id"].clone();
    assert_eq!(fields(&body), wanted);
"#;
    if service {
        out += "    stamps(&body);\n";
    }
    out + "}\n"
}

fn tests_validation(fields: &[Field], service: bool) -> String {
    let first = &fields[0];
    let mut out = String::from(
        r#"async fn refused(app: &Router, body: Value) {
    let (status, reply) = send(app, "POST", PATH, Some(body)).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{reply}");
    assert_eq!(locs(&reply), ["body"]);
}

/// Create a row, check it, and delete it again.
async fn accepted(app: &Router, body: Value) -> Value {
    let (status, reply) = send(app, "POST", PATH, Some(body)).await;
    assert_eq!(status, StatusCode::CREATED, "{reply}");
    let (status, _) = send(app, "DELETE", &item(&reply), None).await;
    assert_eq!(status, StatusCode::NO_CONTENT);
    reply
}

fn with(name: &str, value: Value) -> Value {
    let mut body = sample(0);
    body[name] = value;
    body
}

#[tokio::test]
async fn validation_refusals() {
    let app = app().await;
    accepted(&app, sample(0)).await;
    let (status, body) = send(&app, "POST", PATH, None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(locs(&body), ["body"]);
    for (content_type, text) in [
        ("application/json", "{".to_owned()),
        ("application/json", "[]".to_owned()),
        ("application/json", "1".to_owned()),
        ("text/plain", sample(0).to_string()),
    ] {
        let (status, body) = send_raw(&app, "POST", PATH, content_type, &text).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{content_type}");
        assert_eq!(locs(&body), ["body"]);
    }
"#,
    );
    out += &format!(
        "    refused(&app, with(\"{}\", json!({{}}))).await;\n",
        first.name
    );
    if let Some(required) = fields.iter().find(|field| field.required) {
        out += &format!(
            "    let mut body = sample(0);\n    body.as_object_mut().expect(\"object\").remove(\"{}\");\n    refused(&app, body).await;\n",
            required.name
        );
    }
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Int) {
        out += &format!(
            "    // D1 and D2: no lax coercion, and a signed 32-bit range.\n    refused(&app, with(\"{0}\", json!(\"1\"))).await;\n    refused(&app, with(\"{0}\", json!(2_147_483_648_i64))).await;\n    refused(&app, with(\"{0}\", json!(-2_147_483_649_i64))).await;\n    accepted(&app, with(\"{0}\", json!(-2_147_483_648_i64))).await;\n",
            field.name
        );
    }
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Bool) {
        out += &format!(
            "    // D1: no lax coercion for a bool.\n    refused(&app, with(\"{0}\", json!(\"true\"))).await;\n    refused(&app, with(\"{0}\", json!(1))).await;\n",
            field.name
        );
    }
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Str) {
        out += &format!(
            "    // D2: at most 255 Unicode scalars.\n    refused(&app, with(\"{0}\", json!(\"x\".repeat(256)))).await;\n    refused(&app, with(\"{0}\", json!(5))).await;\n    let reply = accepted(&app, with(\"{0}\", json!(\"\\u{{e9}}\".repeat(255)))).await;\n    assert_eq!(reply[\"{0}\"], json!(\"\\u{{e9}}\".repeat(255)));\n",
            field.name
        );
    }
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Float) {
        out += &format!(
            "    // A float field accepts a JSON integer and answers a float.\n    let reply = accepted(&app, with(\"{0}\", json!(3))).await;\n    assert_eq!(reply[\"{0}\"], json!(3.0));\n    // D1: no lax coercion for a float.\n    refused(&app, with(\"{0}\", json!(\"3.5\"))).await;\n",
            field.name
        );
    }
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Date) {
        out += &format!(
            "    refused(&app, with(\"{0}\", json!(\"2030-02-30\"))).await;\n    // D1: no lax coercion for a date.\n    refused(&app, with(\"{0}\", json!(20300101))).await;\n",
            field.name
        );
    }
    if service {
        out += "    refused(&app, with(\"zz_unknown\", json!(1))).await;\n";
    } else {
        out += "    accepted(&app, with(\"zz_unknown\", json!(1))).await;\n";
    }
    out += r#"    for (method, uri) in [("GET", format!("{PATH}/abc")), ("DELETE", format!("{PATH}/abc")), ("GET", format!("{PATH}/1.5"))] {
        let (status, body) = send(&app, method, &uri, None).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{method} {uri}");
        assert_eq!(locs(&body), ["path"]);
    }
    // D3: an id outside signed 32 bits has no row.
    for uri in [format!("{PATH}/99999999999"), format!("{PATH}/-99999999999")] {
        let (status, body) = send(&app, "GET", &uri, None).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{uri}");
        assert_eq!(body, json!({"detail": "not found"}));
    }
    let (status, body) = send(&app, "PATCH", &format!("{PATH}/abc"), Some(json!({}))).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(locs(&body), ["path"]);
    let (status, body) = send_raw(&app, "PATCH", &format!("{PATH}/abc"), "text/plain", "{}").await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(locs(&body), ["body", "path"]);
    let created = create(&app, 0).await;
    let uri = item(&created);
"#;
    let names = fields
        .iter()
        .map(|field| format!("{:?}", field.name))
        .collect::<Vec<_>>()
        .join(", ");
    out += &format!(
        "    // A JSON body that is not an object is refused, also when its items fit the fields in order.\n    let row: Vec<Value> = [{names}].iter().map(|name| sample(0)[*name].clone()).collect();\n    let (status, body) = send(&app, \"POST\", PATH, Some(Value::Array(row))).await;\n    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, \"{{body}}\");\n    assert_eq!(locs(&body), [\"body\"]);\n    for text in [\"[]\", \"1\", \"null\"] {{\n        let (status, body) = send_raw(&app, \"PATCH\", &uri, \"application/json\", text).await;\n        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, \"{{text}}\");\n        assert_eq!(locs(&body), [\"body\"]);\n    }}\n"
    );
    let patch_refused = |name: &str, value: &str| {
        format!(
            "    let (status, body) = send(&app, \"PATCH\", &uri, Some(json!({{\"{name}\": {value}}}))).await;\n    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, \"{{body}}\");\n    assert_eq!(locs(&body), [\"body\"]);\n"
        )
    };
    out += &patch_refused(&first.name, "{}");
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Str) {
        out += &patch_refused(&field.name, "\"x\".repeat(256)");
    }
    if service {
        out += &patch_refused("zz_unknown", "1");
        if let Some(field) = fields.iter().find(|field| !field.nullable()) {
            out += &patch_refused(&field.name, "null");
        }
    } else {
        if let Some(field) = fields.iter().find(|field| field.required) {
            out += &format!(
                "    // A null for a NOT NULL column is an integrity conflict in the plain products.\n    let (status, body) = send(&app, \"PATCH\", &uri, Some(json!({{\"{}\": null}}))).await;\n    assert_eq!(status, StatusCode::CONFLICT);\n    assert_eq!(body, json!({{\"detail\": \"conflict\"}}));\n",
                field.name
            );
        }
        out += "    let (status, body) = send(&app, \"PATCH\", &uri, Some(json!({\"zz_unknown\": 1}))).await;\n    assert_eq!(status, StatusCode::OK);\n    assert_eq!(body, created);\n";
    }
    if let Some(field) = fields.iter().find(|field| field.nullable()) {
        out += &format!(
            "    let (status, body) = send(&app, \"PATCH\", &uri, Some(json!({{\"{0}\": null}}))).await;\n    assert_eq!(status, StatusCode::OK, \"{{body}}\");\n    assert_eq!(body[\"{0}\"], Value::Null);\n",
            field.name
        );
    }
    out + "}\n"
}

fn tests_unique(fields: &[Field]) -> String {
    let unique: Vec<&Field> = fields.iter().filter(|field| field.unique).collect();
    if unique.is_empty() {
        return r#"#[tokio::test]
async fn duplicate_values_are_allowed() {
    let app = app().await;
    let first = create(&app, 0).await;
    let second = create(&app, 0).await;
    assert_ne!(first["id"], second["id"]);
    let mut wanted = second.clone();
    wanted["id"] = first["id"].clone();
    assert_eq!(fields(&first), fields(&wanted));
}
"#
        .to_owned();
    }
    let names: Vec<String> = unique
        .iter()
        .map(|field| format!("\"{}\"", field.name))
        .collect();
    format!(
        r#"const UNIQUE: &[&str] = &[{}];

#[tokio::test]
async fn unique_conflicts() {{
    let app = app().await;
    let first = create(&app, 0).await;
    for name in UNIQUE {{
        let mut body = sample(1);
        body[*name] = first[*name].clone();
        let (status, reply) = send(&app, "POST", PATH, Some(body)).await;
        assert_eq!(status, StatusCode::CONFLICT, "{{name}}");
        assert_eq!(reply, json!({{"detail": "conflict"}}));
    }}
    let second = create(&app, 1).await;
    for name in UNIQUE {{
        let mut patch = serde_json::Map::new();
        patch.insert((*name).to_owned(), first[*name].clone());
        let (status, reply) = send(&app, "PATCH", &item(&second), Some(Value::Object(patch))).await;
        assert_eq!(status, StatusCode::CONFLICT, "{{name}}");
        assert_eq!(reply, json!({{"detail": "conflict"}}));
    }}
    let (status, body) = send(&app, "GET", &item(&second), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, second);
}}
"#,
        names.join(", ")
    )
}

const TESTS_LIST_ALL: &str = r#"#[tokio::test]
async fn list_returns_every_row() {
    let app = app().await;
    let mut rows = Vec::new();
    for k in 0..ROWS {
        rows.push(create(&app, k).await);
    }
    for uri in [PATH.to_owned(), format!("{PATH}?limit=1&offset=5&zz_unknown=1")] {
        let (status, body) = send(&app, "GET", &uri, None).await;
        assert_eq!(status, StatusCode::OK, "{uri}");
        assert_eq!(body, Value::Array(rows.clone()));
    }
}
"#;

const TESTS_LIST_HELPERS: &str = r#"fn page(rows: &[Value], offset: usize, limit: usize) -> Value {
    Value::Array(rows.iter().skip(offset).take(limit).cloned().collect())
}

fn matching(rows: &[Value], keep: impl Fn(&Value) -> bool) -> Vec<Value> {
    rows.iter().filter(|row| keep(row)).cloned().collect()
}

async fn listed(app: &Router, query: &str) -> Value {
    let (status, body) = send(app, "GET", &format!("{PATH}?{query}"), None).await;
    assert_eq!(status, StatusCode::OK, "{query}: {body}");
    body
}

async fn rejected(app: &Router, query: &str) {
    let (status, body) = send(app, "GET", &format!("{PATH}?{query}"), None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{query}: {body}");
    assert_eq!(locs(&body), ["query"]);
}

"#;

fn tests_list(params: &Value, filter: Filter) -> Result<String, String> {
    let max = limit(params)?;
    let mut out = String::from(TESTS_LIST_HELPERS);
    out += &format!(
        r#"#[tokio::test]
async fn list_queries() {{
    let app = app().await;
    let mut rows = Vec::new();
    for k in 0..ROWS {{
        rows.push(create(&app, k).await);
    }}
    let (status, body) = send(&app, "GET", PATH, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, page(&rows, 0, 20));
    assert_eq!(listed(&app, "limit=5&offset=1").await, page(&rows, 1, 5));
    assert_eq!(listed(&app, "limit={max}").await, page(&rows, 0, {max}));
    assert_eq!(listed(&app, "limit=1&offset=0").await, page(&rows, 0, 1));
    assert_eq!(listed(&app, &format!("offset={{}}", ROWS + 5)).await, json!([]));
    assert_eq!(listed(&app, "zz_unknown=1").await, page(&rows, 0, 20));
    // D4: a repeated query parameter is refused.
    for query in ["limit=0", "limit={over}", "offset=-1", "limit=abc", "offset=1.5", "limit=1&limit=2"] {{
        rejected(&app, query).await;
    }}
"#,
        over = max + 1
    );
    match filter {
        Filter::Text => {
            let name = text(params, "filter_field")?;
            out += &format!(
                r#"    let wanted = rows[1]["{name}"].clone();
    let value = wanted.as_str().expect("text filter value").to_owned();
    let found = matching(&rows, |row| row["{name}"] == wanted);
    assert!(!found.is_empty());
    assert_eq!(listed(&app, &format!("filter_value={{value}}")).await, page(&found, 0, 20));
    assert_eq!(listed(&app, &format!("filter_value={{value}}&limit=1&offset=1")).await, page(&found, 1, 1));
    assert_eq!(listed(&app, "filter_value=zz-none").await, json!([]));
    rejected(&app, "filter_value=a&filter_value=b").await;
"#
            );
        }
        Filter::Boolean | Filter::BooleanDate => {
            let name = text(params, "filter_field")?;
            out += &format!(
                r#"    for (text, value) in [("true", true), ("yes", true), ("1", true), ("false", false), ("Off", false), ("0", false)] {{
        let found = matching(&rows, |row| row["{name}"] == value);
        assert_eq!(listed(&app, &format!("{name}={{text}}")).await, page(&found, 0, 20), "{{text}}");
    }}
    rejected(&app, "{name}=maybe").await;
    rejected(&app, "{name}=true&{name}=false").await;
"#
            );
            if filter == Filter::BooleanDate {
                let date = text(params, "date_field")?;
                out += &format!(
                    r#"    let found = matching(&rows, |row| row["{date}"].as_str().is_some_and(|day| day < "2030-01-10"));
    assert_eq!(listed(&app, "before=2030-01-10").await, page(&found, 0, 20));
    let found = matching(&rows, |row| row["{name}"] == true && row["{date}"].as_str().is_some_and(|day| day < "2030-01-20"));
    assert_eq!(listed(&app, "{name}=true&before=2030-01-20").await, page(&found, 0, 20));
    for query in ["before=2030-13-01", "before=tomorrow", "before=2030-1-1", "before=2030-01-01&before=2030-01-02"] {{
        rejected(&app, query).await;
    }}
"#
                );
            }
        }
        Filter::All => return Err("list tests need a filter".to_owned()),
    }
    Ok(out + "}\n")
}

const TESTS_FALLBACK: &str = r#"#[tokio::test]
async fn fallback_routes() {
    let app = app().await;
    // D5: a trailing slash is not a route.
    for uri in ["/_missing".to_owned(), format!("{PATH}/"), format!("{PATH}/1/x")] {
        let (status, body) = send(&app, "GET", &uri, None).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{uri}");
        assert_eq!(body, json!({"detail": "Not Found"}));
    }
    for (method, uri) in [("PUT", PATH.to_owned()), ("DELETE", PATH.to_owned()), ("POST", format!("{PATH}/1"))] {
        let (status, body) = send(&app, method, &uri, None).await;
        assert_eq!(status, StatusCode::METHOD_NOT_ALLOWED, "{method} {uri}");
        assert_eq!(body, json!({"detail": "Method Not Allowed"}));
    }
}
"#;

// ---------------------------------------------------------------- README.md

/// The README health route line, absent when a resource owns /healthz.
pub(super) const HEALTH_LINE: &str =
    "- `GET /healthz`: `{\"status\": \"ok\"}`, or 503 when the database is unavailable.\n";

fn readme(params: &Value, filter: Filter) -> Result<String, String> {
    let package = text(params, "package")?;
    let version = text(params, "version")?;
    let path = text(params, "path")?;
    let revision = text(params, "revision")?;
    let down = params["down_revision"].as_str().unwrap_or("none");
    let health = if path == "/healthz" { "" } else { HEALTH_LINE };
    let query = match filter {
        Filter::All => "No query parameters. All rows in id order.".to_owned(),
        _ => {
            let max = limit(params)?;
            let filter_text = match filter {
                Filter::Text => format!(
                    "`filter_value` (exact match on `{}`), ",
                    text(params, "filter_field")?
                ),
                Filter::Boolean => format!("`{}` (boolean), ", text(params, "filter_field")?),
                _ => format!(
                    "`{}` (boolean), `before` (a date; rows whose `{}` is earlier), ",
                    text(params, "filter_field")?,
                    text(params, "date_field")?
                ),
            };
            format!(
                "Query: {filter_text}`limit` (1 to {max}, default 20) and `offset` (default 0). Rows in id order."
            )
        }
    };
    Ok(format!(
        r#"# {package}

A Rust service generated by SOM from a `{version}` prompt for the package `{package}`.
The Cargo package, library and binary are `som_app`. It uses axum, sqlx and tokio on PostgreSQL.

## Routes

{health}- `GET {path}`: list rows. {query}
- `POST {path}`: create a row. 201, or 409 for a unique value conflict.
- `GET {path}/{{id}}`: get a row. 404 if it does not exist.
- `PATCH {path}/{{id}}`: update the supplied fields. 404 if the row does not exist.
- `DELETE {path}/{{id}}`: delete a row. 204, or 404 if it does not exist.

Invalid input returns 422 with `{{"detail": [{{"type", "loc", "msg"}}]}}`.

## Run

```sh
DATABASE_URL=postgres://user@127.0.0.1:5432/app SOM_APP_ADDR=127.0.0.1:8000 cargo run --locked --offline
```

`DATABASE_URL` is required. `SOM_APP_ADDR` defaults to `127.0.0.1:8000`.

## Test and check

```sh
TEST_DATABASE_URL=postgres://user@127.0.0.1:5432/app_test cargo test --locked --offline -- --test-threads=1
cargo build --locked --offline
cargo clippy --locked --offline --all-targets -- -D warnings
rustfmt --edition 2024 --check src/*.rs tests/*.rs
```

The tests drop and recreate schema `public` in `TEST_DATABASE_URL`. A missing variable fails them.

## Migration

`migrations/0001_{revision}.sql` holds revision `{revision}` (down revision `{down}`).
The service runs it at startup through `som_app::MIGRATOR`.
"#
    ))
}
