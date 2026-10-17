//! The capability_postgresql blocks (issues #53 D and E): the v5 relation of a parent and its
//! children behind RS256 bearer tokens, no object storage, and one workflow over a parent's
//! children (workflow.rs).
//!
//! The product has the som_app subcommands of phase2/services.json. serve does not migrate; the
//! one migration is recorded in _som_rust_migrations (naming.json W3). The Rust item names come
//! from fixed roles: Parent*, Child* and ParentWithChildren*.
use serde_json::Value;

use super::related::{self, Resource};
use super::{
    API_DAY, API_FLAG, API_LIST_HELPERS, Field, HEALTH_LINE, Kind, PRESENT, quoted, read_struct,
    row_struct, sql_const, text,
};

mod auth;
mod workflow;

use auth::Auth;
use workflow::{Flow, Workflow};

const MANIFEST: &str =
    include_str!("../../../../contracts/rust-output-v1/reference-phase2/Cargo.toml");
const LOCK: &str = include_str!("../../../../contracts/rust-output-v1/reference-phase2/Cargo.lock");

/// The operations a prompt selects. A prompt without operations (v5) selects all of them.
struct Ops {
    parent_create: bool,
    parent_get: bool,
    parent_list: bool,
    parent_patch: bool,
    parent_delete: bool,
    child_create: bool,
    child_get: bool,
    child_list: bool,
    child_patch: bool,
    child_delete: bool,
    batch: bool,
}

impl Ops {
    fn read(params: &Value) -> Result<Self, String> {
        let rows = match &params["operations"] {
            Value::Null => None,
            Value::Array(rows) => Some(rows),
            _ => return Err("param operations is not a list".to_owned()),
        };
        let has = |scope: &str, action: &str| {
            rows.is_none_or(|rows| {
                rows.iter()
                    .any(|row| row["scope"] == scope && row["action"] == action)
            })
        };
        Ok(Self {
            parent_create: has("parent", "create"),
            parent_get: has("parent", "get"),
            parent_list: has("parent", "list"),
            parent_patch: has("parent", "patch"),
            parent_delete: has("parent", "delete"),
            child_create: has("child", "create"),
            child_get: has("child", "get"),
            child_list: has("child", "list"),
            child_patch: has("child", "patch"),
            child_delete: has("child", "delete"),
            batch: has("batch", "batch_create"),
        })
    }

    /// A route on the parent item path. The batch then shares it, as in the Python product.
    fn parent_item(&self) -> bool {
        self.parent_get || self.parent_patch || self.parent_delete
    }

    fn child_item(&self) -> bool {
        self.child_get || self.child_patch || self.child_delete
    }

    fn bodies(&self) -> bool {
        self.parent_create
            || self.parent_patch
            || self.child_create
            || self.child_patch
            || self.batch
    }

    fn lists(&self) -> bool {
        self.parent_list || self.child_list
    }

    fn parent_read(&self) -> bool {
        self.parent_create || self.parent_get || self.parent_list || self.parent_patch || self.batch
    }
}

/// Everything a capability block reads from its params.
struct Product {
    ops: Ops,
    parent: Resource,
    child: Resource,
    key: Field,
}

impl Product {
    fn read(params: &Value) -> Result<Self, String> {
        Ok(Self {
            ops: Ops::read(params)?,
            parent: Resource::read(params, "parent")?,
            child: Resource::read(params, "child")?,
            key: related::foreign_key(params)?,
        })
    }

    /// The parent path is /healthz, so the resource owns it (naming.json W2).
    fn owned(&self) -> bool {
        self.parent.path == "/healthz"
    }
}

/// Render one capability_postgresql block from its resolved params.
pub fn render(id: &str, params: &Value) -> Result<String, String> {
    let product = || Product::read(params);
    Ok(match id {
        "rust.cargo.capability.manifest" => MANIFEST.to_owned(),
        "rust.cargo.capability.lock" => LOCK.to_owned(),
        "rust.main.capability" => MAIN.to_owned(),
        "rust.lib.capability" => LIB.replace("@REVISION@", text(params, "revision")?),
        "rust.models.capability" => models(&product()?),
        "rust.schemas.capability" => schemas(&product()?),
        "rust.api.capability" => api(params, &product()?)?,
        "rust.db.capability" => db(&product()?),
        "rust.integrations.capability" => {
            let v5 = params["version"] == "som.prompt.v5";
            match Auth::read(params)? {
                Auth::Jwt => integrations(v5),
                other => auth::integrations(other, v5, text(params, "package")?),
            }
        }
        "rust.migration.capability" => {
            let product = product()?;
            related::migration_parent(params, &product.parent)?
                + &related::migration_child(params, &product.child, &product.key)?
        }
        "rust.tests.capability" => {
            auth::tests(Auth::read(params)?, tests(Flow::read(params)?, &product()?))
        }
        "rust.readme.capability" => auth::readme(
            Auth::read(params)?,
            text(params, "package")?,
            readme(params, &product()?)?,
        ),
        _ => return Err(format!("no renderer for block {id}")),
    })
}

// ---------------------------------------------------------------- src/main.rs, src/lib.rs

const MAIN: &str = r#"use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use som_app::integrations::{Settings, database};

const USAGE: &str = "usage: som_app [serve | migrate upgrade | migrate downgrade | check-config]";

#[tokio::main]
async fn main() -> ExitCode {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let words: Vec<&str> = arguments.iter().map(String::as_str).collect();
    let outcome = match words.as_slice() {
        [] | ["serve"] => serve().await,
        ["migrate", direction @ ("upgrade" | "downgrade")] => migrate(direction).await,
        ["check-config"] => Settings::load().map(|_| ()),
        _ => {
            eprintln!("{USAGE}");
            return ExitCode::from(2);
        }
    };
    match outcome {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("{message}");
            ExitCode::FAILURE
        }
    }
}

fn pool(options: sqlx::postgres::PgConnectOptions) -> sqlx::PgPool {
    sqlx::postgres::PgPoolOptions::new()
        .acquire_timeout(Duration::from_secs(3))
        .connect_lazy_with(options)
}

/// serve does not migrate, as the Python product does not.
async fn serve() -> Result<(), String> {
    let settings = Settings::load()?;
    let address = std::env::var("SOM_APP_ADDR")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "127.0.0.1:8000".to_owned());
    let state = som_app::AppState {
        pool: pool(settings.database),
        auth: Arc::new(settings.auth),
    };
    let listener = tokio::net::TcpListener::bind(&address)
        .await
        .map_err(|error| format!("{address}: {error}"))?;
    axum::serve(listener, som_app::router(state))
        .await
        .map_err(|error| error.to_string())
}

/// Migrations read DATABASE_URL alone.
async fn migrate(direction: &str) -> Result<(), String> {
    let pool = pool(database()?);
    let done = if direction == "upgrade" {
        som_app::upgrade(&pool).await
    } else {
        som_app::downgrade(&pool).await
    };
    pool.close().await;
    done.map_err(|error| error.to_string())
}
"#;

const LIB: &str = r#"pub mod api;
pub mod db;
pub mod integrations;
pub mod models;
pub mod schemas;

pub use api::{AppState, router};

/// The one migration: its version in _som_rust_migrations, its revision and its upgrade.
const VERSION: i64 = 1;
const REVISION: &str = "@REVISION@";
const UPGRADE: &str = include_str!("../migrations/0001_@REVISION@.sql");

/// Serializes migrators on one database.
const MIGRATION_LOCK: i64 = 0x736f_6d5f_6d69_6772;

/// alembic upgrade head: applies the migration once and records it. A second run changes nothing.
pub async fn upgrade(pool: &sqlx::PgPool) -> Result<(), sqlx::Error> {
    let mut transaction = pool.begin().await?;
    sqlx::query(db::ADVISORY_LOCK)
        .bind(MIGRATION_LOCK)
        .execute(&mut *transaction)
        .await?;
    sqlx::raw_sql(db::LEDGER).execute(&mut *transaction).await?;
    let applied: bool = sqlx::query_scalar(db::LEDGER_APPLIED)
        .bind(VERSION)
        .fetch_one(&mut *transaction)
        .await?;
    if !applied {
        sqlx::raw_sql(UPGRADE).execute(&mut *transaction).await?;
        sqlx::query(db::LEDGER_INSERT)
            .bind(VERSION)
            .bind(REVISION)
            .execute(&mut *transaction)
            .await?;
    }
    transaction.commit().await
}

/// alembic downgrade base: drops what the migration created. Without it, nothing changes.
pub async fn downgrade(pool: &sqlx::PgPool) -> Result<(), sqlx::Error> {
    let mut transaction = pool.begin().await?;
    sqlx::query(db::ADVISORY_LOCK)
        .bind(MIGRATION_LOCK)
        .execute(&mut *transaction)
        .await?;
    sqlx::raw_sql(db::LEDGER).execute(&mut *transaction).await?;
    let removed = sqlx::query(db::LEDGER_DELETE)
        .bind(VERSION)
        .execute(&mut *transaction)
        .await?;
    if removed.rows_affected() > 0 {
        for statement in db::DOWNGRADE {
            sqlx::raw_sql(statement).execute(&mut *transaction).await?;
        }
    }
    transaction.commit().await
}
"#;

// ---------------------------------------------------------------- src/models.rs

fn models(product: &Product) -> String {
    row_struct("ParentRow", None, &product.parent.fields, true)
        + "\n"
        + &row_struct("ChildRow", Some(&product.key), &product.child.fields, true)
}

// ---------------------------------------------------------------- src/schemas.rs

/// The input type of a field. Integers read as i64 so that the bound check, not the parser,
/// refuses a value outside signed 32 bits, as the Python BoundedInput does.
fn input(field: &Field) -> String {
    let base = if field.kind == Kind::Int {
        "i64"
    } else {
        field.rust()
    };
    if field.nullable() {
        format!("Option<{base}>")
    } else {
        base.to_owned()
    }
}

/// The bound check of a create member, in field order, or nothing.
fn create_check(field: &Field) -> Option<String> {
    let member = field.ident();
    match (field.kind, field.nullable()) {
        (Kind::Str, false) => Some(format!("long(&self.{member})")),
        (Kind::Str, true) => Some(format!("self.{member}.as_deref().is_some_and(long)")),
        (Kind::Int, false) => Some(format!("wide(self.{member})")),
        (Kind::Int, true) => Some(format!("self.{member}.is_some_and(wide)")),
        _ => None,
    }
    .map(|test| {
        let message = if field.kind == Kind::Str {
            "LONG"
        } else {
            "WIDE"
        };
        format!("        if {test} {{\n            return vec![model_problem(loc, {message})];\n        }}\n")
    })
}

fn create_struct(role: &str, fields: &[Field]) -> String {
    let mut out = format!(
        "\n#[derive(Debug, Deserialize)]\n#[serde(deny_unknown_fields)]\npub struct {role}Create {{\n"
    );
    for field in fields {
        let options = if field.required {
            Vec::new()
        } else {
            vec!["default".to_owned()]
        };
        out += &field.serde(options);
        out += &format!("    pub {}: {},\n", field.ident(), input(field));
    }
    let checks: String = fields.iter().filter_map(create_check).collect();
    let loc = if checks.is_empty() { "_loc" } else { "loc" };
    out += &format!(
        "}}\n\nimpl {role}Create {{\n    /// The first bound problem of the model at loc: a long string or a wide integer.\n    pub fn problems_at(&self, {loc}: &Value) -> Vec<Value> {{\n{checks}        Vec::new()\n    }}\n\n    pub fn problems(&self) -> Vec<Value> {{\n        self.problems_at(&json!([\"body\"]))\n    }}\n}}\n"
    );
    out
}

fn update_struct(role: &str, fields: &[Field]) -> String {
    let mut out = format!(
        "\n#[derive(Debug, Deserialize)]\n#[serde(deny_unknown_fields)]\npub struct {role}Update {{\n"
    );
    let mut checks = String::new();
    for field in fields {
        out += &field.serde(vec![
            "default".to_owned(),
            "deserialize_with = \"present\"".to_owned(),
        ]);
        let base = if field.kind == Kind::Int {
            "i64"
        } else {
            field.rust()
        };
        out += &format!("    pub {}: Option<Option<{base}>>,\n", field.ident());
        let test = match field.kind {
            Kind::Str => format!(
                "matches!(&self.{}, Some(Some(value)) if long(value))",
                field.ident()
            ),
            Kind::Int => format!(
                "matches!(self.{}, Some(Some(value)) if wide(value))",
                field.ident()
            ),
            _ => continue,
        };
        let message = if field.kind == Kind::Str {
            "LONG"
        } else {
            "WIDE"
        };
        checks += &format!(
            "        if {test} {{\n            return vec![model_problem(&loc, {message})];\n        }}\n"
        );
    }
    for field in fields.iter().filter(|field| !field.nullable()) {
        checks += &format!(
            "        if matches!(self.{}, Some(None)) {{\n            return vec![model_problem(&loc, \"{} cannot be null\")];\n        }}\n",
            field.ident(),
            field.name
        );
    }
    let loc = if checks.is_empty() {
        ""
    } else {
        "        let loc = json!([\"body\"]);\n"
    };
    out += &format!(
        "}}\n\nimpl {role}Update {{\n    /// The bound problems first, then a null for a NOT NULL column. Only the first one counts.\n    pub fn problems(&self) -> Vec<Value> {{\n{loc}{checks}        Vec::new()\n    }}\n}}\n"
    );
    out
}

fn schemas(product: &Product) -> String {
    let fields = || product.parent.fields.iter().chain(&product.child.fields);
    let strings = fields().any(|field| field.kind == Kind::Str);
    let integers = fields().any(|field| field.kind == Kind::Int);
    let nulls = fields().any(|field| !field.nullable());
    let mut out = String::from(
        "use serde::{Deserialize, Deserializer, Serialize};\nuse serde_json::{Value, json};\n\n",
    );
    if strings || integers || nulls {
        out += "/// A model check, as the Python BoundedInput and the patch models raise it.\nfn model_problem(loc: &Value, message: &str) -> Value {\n    json!({\"type\": \"value_error\", \"loc\": loc, \"msg\": format!(\"Value error, {message}\")})\n}\n\n";
    }
    if strings {
        out += "const LONG: &str = \"strings must have at most 255 characters\";\n\nfn long(value: &str) -> bool {\n    value.chars().count() > 255\n}\n\n";
    }
    if integers {
        out += "const WIDE: &str = \"integers must fit signed 32 bits\";\n\nfn wide(value: i64) -> bool {\n    i32::try_from(value).is_err()\n}\n\n";
    }
    out += PRESENT;
    out += &create_struct("Parent", &product.parent.fields);
    out += &update_struct("Parent", &product.parent.fields);
    out += "\n";
    out += PY_DATETIME;
    out += &timestamps(read_struct(
        "ParentRead",
        "ParentRow",
        None,
        &product.parent.fields,
        true,
    ));
    out += &create_struct("Child", &product.child.fields);
    out += &update_struct("Child", &product.child.fields);
    out += "\n";
    out += &timestamps(read_struct(
        "ChildRead",
        "ChildRow",
        Some(&product.key),
        &product.child.fields,
        true,
    ));
    out + SCHEMAS_BATCH
}

/// A response struct whose timestamps serialize through py_datetime.
fn timestamps(read: String) -> String {
    ["created_at", "updated_at"].iter().fold(read, |out, name| {
        out.replace(
            &format!("    pub {name}: chrono::DateTime<chrono::Utc>,\n"),
            &format!(
                "    #[serde(serialize_with = \"py_datetime\")]\n    pub {name}: chrono::DateTime<chrono::Utc>,\n"
            ),
        )
    })
}

/// The Python product reads a timestamp from a UTC session and pydantic writes it with Z and
/// with six fraction digits, or none for a whole second. chrono would write 3, 6 or 9 digits.
const PY_DATETIME: &str = r#"/// A timestamp as pydantic writes a UTC datetime: six fraction digits unless zero, then Z.
fn py_datetime<S: serde::Serializer>(
    value: &chrono::DateTime<chrono::Utc>,
    serializer: S,
) -> Result<S::Ok, S::Error> {
    let format = if value.timestamp_subsec_micros() == 0 {
        "%Y-%m-%dT%H:%M:%SZ"
    } else {
        "%Y-%m-%dT%H:%M:%S%.6fZ"
    };
    serializer.collect_str(&value.format(format))
}

"#;

const SCHEMAS_BATCH: &str = r#"
/// One parent and its children, created in one transaction.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ParentWithChildrenCreate {
    pub parent: ParentCreate,
    pub children: Vec<ChildCreate>,
}

impl ParentWithChildrenCreate {
    pub fn problems(&self) -> Vec<Value> {
        let mut found = self.parent.problems_at(&json!(["body", "parent"]));
        if self.children.is_empty() {
            found.push(json!({"type": "too_short", "loc": ["body", "children"], "msg": "List should have at least 1 item after validation, not 0"}));
        }
        if self.children.len() > 20 {
            found.push(json!({"type": "too_long", "loc": ["body", "children"], "msg": format!("List should have at most 20 items after validation, not {}", self.children.len())}));
        }
        for (index, child) in self.children.iter().enumerate() {
            found.extend(child.problems_at(&json!(["body", "children", index])));
        }
        found
    }
}

#[derive(Debug, Serialize)]
pub struct ParentWithChildrenRead {
    pub parent: ParentRead,
    pub children: Vec<ChildRead>,
}
"#;

// ---------------------------------------------------------------- src/api.rs

fn api(params: &Value, product: &Product) -> Result<String, String> {
    let ops = &product.ops;
    let workflow = Workflow::read(params, &product.child)?;
    let flow = workflow.flow;
    let csv = flow == Flow::Csv;
    let bodies = ops.bodies();
    let auth = Auth::read(params)?;
    let routes = routes(product, flow, auth);
    let mut free: Vec<&str> = routes.iter().map(|(_, methods)| methods[0].0).collect();
    free.sort_unstable();
    free.dedup();
    // The json_export product always names ChildRead, as it did before the other workflows.
    let reads = flow == Flow::Json
        || ops.child_list
        || ops.child_create
        || ops.child_get
        || ops.child_patch
        || ops.batch
        || matches!(flow, Flow::Gzip | Flow::Csv);
    let mut schemas = Vec::new();
    for (selected, name) in [
        (reads, "ChildRead"),
        (ops.child_create || csv, "ChildCreate"),
        (ops.child_patch, "ChildUpdate"),
        (ops.parent_create, "ParentCreate"),
        (ops.parent_read(), "ParentRead"),
        (ops.parent_patch, "ParentUpdate"),
        (ops.batch, "ParentWithChildrenCreate"),
        (ops.batch, "ParentWithChildrenRead"),
    ] {
        if selected {
            schemas.push(name);
        }
    }
    let mut out = String::from("use std::sync::Arc;\n\nuse axum::{\n    Json, Router,\n");
    if bodies || csv {
        out += "    body::Bytes,\n    extract::{Path, State, rejection::{BytesRejection, PathRejection}},\n    http::{HeaderMap, StatusCode, header::{CONTENT_TYPE, WWW_AUTHENTICATE}},\n";
    } else {
        out += "    extract::{Path, State, rejection::PathRejection},\n    http::{StatusCode, header::{CONTENT_TYPE, WWW_AUTHENTICATE}},\n";
    }
    out += &format!(
        "    response::{{IntoResponse, Response}},\n    routing::{{{}}},\n}};\nuse serde_json::{{Value, json}};\nuse sqlx::PgPool;\n\nuse crate::db;\n",
        free.join(", ")
    );
    out += &auth::api_imports(auth, bodies || csv);
    out += &format!("use crate::schemas::{{{}}};\n\n", schemas.join(", "));
    let errors = if csv {
        API_ERRORS
            .replace("    TooMany,\n", "    TooMany,\n    Csv,\n")
            .replace(
                "            Self::Internal =>",
                "            Self::Csv => (StatusCode::UNPROCESSABLE_ENTITY, json!({\"detail\": \"invalid CSV input\"})),\n            Self::Internal =>",
            )
    } else {
        API_ERRORS.to_owned()
    };
    out += &auth::api_errors(auth, errors);
    if ops.child_create || csv {
        out += API_MISSING_PARENT;
    }
    out += API_PATH;
    if bodies || csv {
        out += API_JSON_TYPE;
    }
    if bodies {
        out += API_BODY;
    }
    if !product.owned() {
        out += API_HEALTH;
    }
    out += &api_lists(product);
    out += API_LIMITS;
    out += &api_parent(product);
    out += &api_child(ops);
    if flow == Flow::Json {
        out += API_EXPORT;
    } else {
        out += &workflow.handlers(&product.child);
    }
    out += API_WHO_AM_I;
    out += &router(&routes);
    Ok(out + API_FALLBACKS)
}

/// The routes of the selected operations, each with its methods and handlers in method order.
fn routes(
    product: &Product,
    flow: Flow,
    auth: Auth,
) -> Vec<(String, Vec<(&'static str, &'static str)>)> {
    let ops = &product.ops;
    let parents = &product.parent.path;
    let children = format!("{parents}/{{parent_id}}{}", product.child.path);
    let shared_batch = ops.batch && ops.parent_item();
    let mut routes = vec![("/auth/me".to_owned(), vec![("get", "who_am_i")])];
    if auth == Auth::Google {
        routes.push((
            "/auth/google/login".to_owned(),
            vec![("get", "google_login")],
        ));
        routes.push((
            "/auth/google/callback".to_owned(),
            vec![("get", "google_callback")],
        ));
        routes.push(("/auth/logout".to_owned(), vec![("post", "logout")]));
    }
    if !product.owned() {
        routes.push(("/healthz".to_owned(), vec![("get", "health")]));
    }
    let mut add = |path: String, methods: &[(bool, &'static str, &'static str)]| {
        let methods: Vec<(&'static str, &'static str)> = methods
            .iter()
            .filter(|(selected, _, _)| *selected)
            .map(|(_, method, handler)| (*method, *handler))
            .collect();
        if !methods.is_empty() {
            routes.push((path, methods));
        }
    };
    add(
        parents.clone(),
        &[
            (ops.parent_list, "get", "list_parents"),
            (ops.parent_create, "post", "create_parent"),
        ],
    );
    add(
        format!("{parents}/with-children"),
        &[(ops.batch && !shared_batch, "post", "create_batch")],
    );
    add(
        format!("{parents}/{{parent_id}}"),
        &[
            (ops.parent_get, "get", "fetch_parent"),
            (shared_batch, "post", "create_batch"),
            (ops.parent_patch, "patch", "update_parent"),
            (ops.parent_delete, "delete", "remove_parent"),
        ],
    );
    add(
        format!("{parents}/{{parent_id}}/export"),
        &[(true, "get", "export_children")],
    );
    add(
        format!("{parents}/{{parent_id}}/import"),
        &[(flow == Flow::Csv, "post", "import_children")],
    );
    add(
        children.clone(),
        &[
            (ops.child_list, "get", "list_children"),
            (ops.child_create, "post", "create_child"),
        ],
    );
    add(
        format!("{children}/{{child_id}}"),
        &[
            (ops.child_get, "get", "fetch_child"),
            (ops.child_patch, "patch", "update_child"),
            (ops.child_delete, "delete", "remove_child"),
        ],
    );
    routes
}

fn router(routes: &[(String, Vec<(&str, &str)>)]) -> String {
    let mut out = String::from("\npub fn router(state: AppState) -> Router {\n    Router::new()\n");
    for (path, methods) in routes {
        let chain: Vec<String> = methods
            .iter()
            .enumerate()
            .map(|(index, (method, handler))| {
                if *handler == "import_children" {
                    // The import reads its own length limit, past axum's default.
                    format!("{method}({handler}).layer(axum::extract::DefaultBodyLimit::max(IMPORT_MAX_BYTES))")
                } else if index == 0 {
                    format!("{method}({handler})")
                } else {
                    format!(".{method}({handler})")
                }
            })
            .collect();
        out += &format!("        .route(\"{path}\", {})\n", chain.concat());
    }
    out + "        .fallback(missing)\n        .method_not_allowed_fallback(not_allowed)\n        .with_state(state)\n}\n"
}

const API_ERRORS: &str = r#"/// The pool and the token checks every handler shares.
#[derive(Clone)]
pub struct AppState {
    pub pool: PgPool,
    pub auth: Arc<Jwt>,
}

const NOT_INTEGER: &str = "Input should be a valid integer, unable to parse string as an integer";

pub enum ApiError {
    ParentMissing,
    ChildMissing,
    Conflict,
    Invalid(Vec<Value>),
    Finite,
    Unauthorized,
    Forbidden,
    TooMany,
    Internal,
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let (status, body) = match self {
            Self::ParentMissing => (StatusCode::NOT_FOUND, json!({"detail": "parent not found"})),
            Self::ChildMissing => (StatusCode::NOT_FOUND, json!({"detail": "child not found"})),
            Self::Conflict => (StatusCode::CONFLICT, json!({"detail": "relationship or unique value conflict"})),
            Self::Invalid(problems) => (StatusCode::UNPROCESSABLE_ENTITY, json!({"detail": problems})),
            Self::Finite => (
                StatusCode::UNPROCESSABLE_ENTITY,
                json!({"detail": [{"type": "finite_number", "loc": ["body"], "msg": "Input must contain finite JSON numbers"}]}),
            ),
            Self::Unauthorized => {
                let body = Json(json!({"detail": "invalid or expired authentication"}));
                return (StatusCode::UNAUTHORIZED, [(WWW_AUTHENTICATE, "Bearer")], body).into_response();
            }
            Self::Forbidden => (StatusCode::FORBIDDEN, json!({"detail": "editor role required"})),
            Self::TooMany => (StatusCode::PAYLOAD_TOO_LARGE, json!({"detail": "workflow is limited to 1000 rows"})),
            Self::Internal => (StatusCode::INTERNAL_SERVER_ERROR, json!({"detail": "Internal Server Error"})),
        };
        (status, Json(body)).into_response()
    }
}

/// A foreign key (23503) or unique (23505) violation is a conflict. A parent delete that its
/// children restrict is one.
impl From<sqlx::Error> for ApiError {
    fn from(error: sqlx::Error) -> Self {
        match error.as_database_error().and_then(|found| found.code()).as_deref() {
            Some("23503" | "23505") => Self::Conflict,
            _ => Self::Internal,
        }
    }
}
"#;

const API_MISSING_PARENT: &str = r#"
/// A child insert whose parent is gone by then answers 404, not 409.
fn missing_parent(error: sqlx::Error) -> ApiError {
    let lost = error.as_database_error().and_then(|found| found.code()).as_deref() == Some("23503");
    if lost { ApiError::ParentMissing } else { ApiError::from(error) }
}
"#;

const API_PATH: &str = r#"
fn problem(kind: &str, loc: Value, msg: &str) -> Value {
    json!({"type": kind, "loc": loc, "msg": msg})
}

/// The path segments, or empty ones when the path cannot be read.
fn segments<T: Default>(path: Result<Path<T>, PathRejection>) -> T {
    path.map(|Path(found)| found).unwrap_or_default()
}

/// An optional sign and ASCII digits, else a path problem. Outside i32 there is no row.
fn path_id(text: &str, name: &str, problems: &mut Vec<Value>) -> Option<i32> {
    let digits = text.strip_prefix(['+', '-']).unwrap_or(text);
    if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        problems.push(problem("int_parsing", json!(["path", name]), NOT_INTEGER));
        return None;
    }
    text.parse().ok()
}

/// The id of an existing parent row, else 404 parent not found.
async fn known_parent(pool: &PgPool, id: Option<i32>) -> Result<i32, ApiError> {
    let id = id.ok_or(ApiError::ParentMissing)?;
    if db::parent_exists(pool, id).await? { Ok(id) } else { Err(ApiError::ParentMissing) }
}
"#;

const API_JSON_TYPE: &str = r#"
/// No content type, application/json or application/*+json.
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
"#;

const API_BODY: &str = r#"
/// A read body: the model when it validates, and whether the JSON held a non-finite number.
struct Body<T> {
    model: Option<T>,
    nonfinite: bool,
}

impl<T> Body<T> {
    fn none() -> Self {
        Self { model: None, nonfinite: false }
    }

    /// After the caller is checked: a non-finite number replaces every other problem.
    fn finish(self, problems: Vec<Value>) -> Result<T, ApiError> {
        if self.nonfinite {
            return Err(ApiError::Finite);
        }
        match (self.model, problems.is_empty()) {
            (Some(model), true) => Ok(model),
            _ => Err(ApiError::Invalid(problems)),
        }
    }
}

/// A JSON syntax error refuses at once, before the caller is checked. Other body problems join
/// the path problems.
fn read_body<T: serde::de::DeserializeOwned>(
    headers: &HeaderMap,
    payload: Result<Bytes, BytesRejection>,
    problems: &mut Vec<Value>,
    check: impl Fn(&T) -> Vec<Value>,
) -> Result<Body<T>, ApiError> {
    let Ok(bytes) = payload else {
        problems.push(problem("value_error", json!(["body"]), "The request body could not be read"));
        return Ok(Body::none());
    };
    if bytes.is_empty() {
        problems.push(problem("missing", json!(["body"]), "Field required"));
        return Ok(Body::none());
    }
    if !json_type(headers) {
        problems.push(problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
        ));
        return Ok(Body::none());
    }
    let (value, nonfinite) = python_json_detail(&bytes).map_err(|error| {
        ApiError::Invalid(vec![problem("json_invalid", json!(["body", error.column()]), "JSON decode error")])
    })?;
    if nonfinite {
        return Ok(Body { model: None, nonfinite });
    }
    if !value.is_object() {
        problems.push(problem(
            "model_attributes_type",
            json!(["body"]),
            "Input should be a valid dictionary or object to extract fields from",
        ));
        return Ok(Body::none());
    }
    match serde_json::from_value::<T>(value) {
        Ok(model) => {
            problems.extend(check(&model));
            Ok(Body { model: Some(model), nonfinite })
        }
        Err(error) => {
            problems.push(problem("value_error", json!(["body"]), &error.to_string()));
            Ok(Body::none())
        }
    }
}
"#;

const API_HEALTH: &str = r#"
async fn health(State(state): State<AppState>) -> Response {
    match db::ping(&state.pool).await {
        Ok(()) => Json(json!({"status": "ok"})).into_response(),
        Err(_) => (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"detail": "database unavailable"}))).into_response(),
    }
}
"#;

const API_QUERY_PAIRS: &str = r#"
/// The query pairs, or a query problem when the query string cannot be read.
fn query_pairs(query: Pairs, problems: &mut Vec<Value>) -> Vec<(String, String)> {
    match query {
        Ok(axum::extract::Query(pairs)) => pairs,
        Err(_) => {
            problems.push(problem("value_error", json!(["query"]), "The query string could not be read"));
            Vec::new()
        }
    }
}
"#;

/// The query helpers and the list handlers of the selected list operations.
fn api_lists(product: &Product) -> String {
    let ops = &product.ops;
    let (parent, child) = (&product.parent, &product.child);
    if !ops.lists() {
        return String::new();
    }
    let mut out = String::from("\n");
    out += API_LIST_HELPERS;
    let listed = |pick: fn(&Resource) -> bool| {
        (ops.parent_list && pick(parent)) || (ops.child_list && pick(child))
    };
    if listed(|resource| resource.filter.is_some()) {
        out += API_FLAG;
    }
    if listed(|resource| resource.date.is_some()) {
        out += API_DAY;
    }
    out += API_QUERY_PAIRS;
    if ops.parent_list {
        out += &format!(
            "\nconst PARENT_MAX_LIMIT: i64 = {};\n\nasync fn list_parents(\n    State(state): State<AppState>,\n    access: Result<Access, ApiError>,\n    query: Pairs,\n) -> Result<Json<Vec<ParentRead>>, ApiError> {{\n    access?;\n    let mut problems = Vec::new();\n    let pairs = query_pairs(query, &mut problems);\n",
            parent.max_limit
        );
        let (lines, args) = related::query_lines(parent, "PARENT_MAX_LIMIT");
        out += &lines;
        out += &format!(
            "    let rows = db::list_parents(&state.pool, {args}limit, offset).await?;\n    Ok(Json(rows.into_iter().map(ParentRead::from).collect()))\n}}\n"
        );
    }
    if ops.child_list {
        out += &format!(
            "\nconst CHILD_MAX_LIMIT: i64 = {};\n\n/// Query problems come before a missing parent, as in the Python product.\nasync fn list_children(\n    State(state): State<AppState>,\n    access: Result<Access, ApiError>,\n    path: Result<Path<String>, PathRejection>,\n    query: Pairs,\n) -> Result<Json<Vec<ChildRead>>, ApiError> {{\n    access?;\n    let mut problems = Vec::new();\n    let parent = path_id(&segments(path), \"parent_id\", &mut problems);\n    let pairs = query_pairs(query, &mut problems);\n",
            child.max_limit
        );
        let (lines, args) = related::query_lines(child, "CHILD_MAX_LIMIT");
        out += &lines;
        out += &format!(
            "    let parent = known_parent(&state.pool, parent).await?;\n    let rows = db::list_children(&state.pool, parent, {args}limit, offset).await?;\n    Ok(Json(rows.into_iter().map(ChildRead::from).collect()))\n}}\n"
        );
    }
    out
}

const API_LIMITS: &str = r#"
const EXPORT_MAX_ROWS: usize = 1000;

fn invalid(problems: Vec<Value>) -> Result<(), ApiError> {
    if problems.is_empty() { Ok(()) } else { Err(ApiError::Invalid(problems)) }
}
"#;

fn api_parent(product: &Product) -> String {
    let ops = &product.ops;
    let mut out = String::new();
    if ops.parent_create {
        out += API_PARENT_CREATE;
    }
    if ops.parent_get {
        out += API_PARENT_GET;
    }
    if ops.parent_patch {
        out += API_PARENT_PATCH;
    }
    if ops.parent_delete {
        out += API_PARENT_DELETE;
    }
    if ops.batch {
        let (doc, path, guard) = if ops.parent_item() {
            (
                "/// POST on a parent item path: only the with-children segment is a route. The parent and every\n/// child go in one transaction, so a failing child leaves no row behind.\n",
                "    path: Result<Path<String>, PathRejection>,\n",
                "    if segments(path) != \"with-children\" {\n        return Ok(not_allowed().await);\n    }\n",
            )
        } else {
            (
                "/// The parent and every child go in one transaction, so a failing child leaves no row behind.\n",
                "",
                "",
            )
        };
        out += &API_BATCH
            .replace("@DOC@", doc)
            .replace("@PATH_ARG@", path)
            .replace("@GUARD@", guard);
    }
    out
}

const API_PARENT_CREATE: &str = r#"
async fn create_parent(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<(StatusCode, Json<ParentRead>), ApiError> {
    let mut problems = Vec::new();
    let body = read_body(&headers, payload, &mut problems, ParentCreate::problems)?;
    access?;
    let body = body.finish(problems)?;
    let row = db::insert_parent(&state.pool, body).await?;
    Ok((StatusCode::CREATED, Json(ParentRead::from(row))))
}
"#;

const API_PARENT_GET: &str = r#"
async fn fetch_parent(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Json<ParentRead>, ApiError> {
    access?;
    let mut problems = Vec::new();
    let id = path_id(&segments(path), "parent_id", &mut problems);
    invalid(problems)?;
    let id = id.ok_or(ApiError::ParentMissing)?;
    let row = db::fetch_parent(&state.pool, id).await?.ok_or(ApiError::ParentMissing)?;
    Ok(Json(ParentRead::from(row)))
}
"#;

const API_PARENT_PATCH: &str = r#"
async fn update_parent(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<Json<ParentRead>, ApiError> {
    let mut problems = Vec::new();
    let id = path_id(&segments(path), "parent_id", &mut problems);
    let body = read_body(&headers, payload, &mut problems, ParentUpdate::problems)?;
    access?;
    let body = body.finish(problems)?;
    let id = id.ok_or(ApiError::ParentMissing)?;
    let row = db::update_parent(&state.pool, id, body).await?.ok_or(ApiError::ParentMissing)?;
    Ok(Json(ParentRead::from(row)))
}
"#;

const API_PARENT_DELETE: &str = r#"
/// A parent that still has children answers 409: the foreign key restricts the delete.
async fn remove_parent(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<String>, PathRejection>,
) -> Result<StatusCode, ApiError> {
    access?;
    let mut problems = Vec::new();
    let id = path_id(&segments(path), "parent_id", &mut problems);
    invalid(problems)?;
    let id = id.ok_or(ApiError::ParentMissing)?;
    if db::delete_parent(&state.pool, id).await? {
        Ok(StatusCode::NO_CONTENT)
    } else {
        Err(ApiError::ParentMissing)
    }
}
"#;

const API_BATCH: &str = r#"
@DOC@async fn create_batch(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
@PATH_ARG@    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<Response, ApiError> {
@GUARD@    let mut problems = Vec::new();
    let body = read_body(&headers, payload, &mut problems, ParentWithChildrenCreate::problems)?;
    access?;
    let body = body.finish(problems)?;
    let mut transaction = state.pool.begin().await?;
    let parent = db::insert_parent(&mut *transaction, body.parent).await?;
    let mut children = Vec::new();
    for child in body.children {
        children.push(ChildRead::from(db::insert_child(&mut *transaction, parent.id, child).await?));
    }
    transaction.commit().await?;
    let read = ParentWithChildrenRead { parent: ParentRead::from(parent), children };
    Ok((StatusCode::CREATED, Json(read)).into_response())
}
"#;

fn api_child(ops: &Ops) -> String {
    let mut out = String::new();
    if ops.child_item() {
        out += API_CHILD_IDS;
    }
    if ops.child_create {
        out += API_CHILD_CREATE;
    }
    if ops.child_get {
        out += API_CHILD_GET;
    }
    if ops.child_patch {
        out += API_CHILD_PATCH;
    }
    if ops.child_delete {
        out += API_CHILD_DELETE;
    }
    out
}

const API_CHILD_IDS: &str = r#"
/// The parent and child ids of a child item path, with their path problems.
fn child_ids(
    path: Result<Path<(String, String)>, PathRejection>,
    problems: &mut Vec<Value>,
) -> (Option<i32>, Option<i32>) {
    let (parent, child) = segments(path);
    (path_id(&parent, "parent_id", problems), path_id(&child, "child_id", problems))
}
"#;

const API_CHILD_CREATE: &str = r#"
async fn create_child(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<(StatusCode, Json<ChildRead>), ApiError> {
    let mut problems = Vec::new();
    let parent = path_id(&segments(path), "parent_id", &mut problems);
    let body = read_body(&headers, payload, &mut problems, ChildCreate::problems)?;
    access?;
    let body = body.finish(problems)?;
    let parent = known_parent(&state.pool, parent).await?;
    let row = db::insert_child(&state.pool, parent, body).await.map_err(missing_parent)?;
    Ok((StatusCode::CREATED, Json(ChildRead::from(row))))
}
"#;

const API_CHILD_GET: &str = r#"
async fn fetch_child(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Json<ChildRead>, ApiError> {
    access?;
    let mut problems = Vec::new();
    let (parent, id) = child_ids(path, &mut problems);
    invalid(problems)?;
    let parent = known_parent(&state.pool, parent).await?;
    let id = id.ok_or(ApiError::ChildMissing)?;
    let row = db::fetch_child(&state.pool, id, parent).await?.ok_or(ApiError::ChildMissing)?;
    Ok(Json(ChildRead::from(row)))
}
"#;

const API_CHILD_PATCH: &str = r#"
async fn update_child(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<(String, String)>, PathRejection>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<Json<ChildRead>, ApiError> {
    let mut problems = Vec::new();
    let (parent, id) = child_ids(path, &mut problems);
    let body = read_body(&headers, payload, &mut problems, ChildUpdate::problems)?;
    access?;
    let body = body.finish(problems)?;
    let parent = known_parent(&state.pool, parent).await?;
    let id = id.ok_or(ApiError::ChildMissing)?;
    let row = db::update_child(&state.pool, id, parent, body).await?.ok_or(ApiError::ChildMissing)?;
    Ok(Json(ChildRead::from(row)))
}
"#;

const API_CHILD_DELETE: &str = r#"
async fn remove_child(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<StatusCode, ApiError> {
    access?;
    let mut problems = Vec::new();
    let (parent, id) = child_ids(path, &mut problems);
    invalid(problems)?;
    let parent = known_parent(&state.pool, parent).await?;
    let id = id.ok_or(ApiError::ChildMissing)?;
    if db::delete_child(&state.pool, id, parent).await? {
        Ok(StatusCode::NO_CONTENT)
    } else {
        Err(ApiError::ChildMissing)
    }
}
"#;

const API_EXPORT: &str = r#"
/// Every child of a parent as compact JSON, at most 1000 rows.
async fn export_children(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ApiError> {
    access?;
    let mut problems = Vec::new();
    let parent = path_id(&segments(path), "parent_id", &mut problems);
    invalid(problems)?;
    let parent = known_parent(&state.pool, parent).await?;
    let rows = db::export_children(&state.pool, parent).await?;
    if rows.len() > EXPORT_MAX_ROWS {
        return Err(ApiError::TooMany);
    }
    let rows: Vec<ChildRead> = rows.into_iter().map(ChildRead::from).collect();
    let content = serde_json::to_vec(&rows).map_err(|_| ApiError::Internal)?;
    Ok(([(CONTENT_TYPE, "application/json")], content).into_response())
}
"#;

const API_WHO_AM_I: &str = r#"
async fn who_am_i(identity: Result<Identity, ApiError>) -> Result<Json<Identity>, ApiError> {
    Ok(Json(identity?))
}
"#;

const API_FALLBACKS: &str = r#"
async fn missing() -> Response {
    (StatusCode::NOT_FOUND, Json(json!({"detail": "Not Found"}))).into_response()
}

async fn not_allowed() -> Response {
    (StatusCode::METHOD_NOT_ALLOWED, Json(json!({"detail": "Method Not Allowed"}))).into_response()
}
"#;

// ---------------------------------------------------------------- src/db.rs

fn db(product: &Product) -> String {
    let child = &product.child;
    let mut out = String::from(related::DB_PING);
    out += &related::db_parent(&product.parent);
    out += &related::db_child(child, &product.key);
    out += "\n";
    out += &sql_const(
        "CHILD_EXPORT",
        &format!(
            "SELECT {} FROM {} WHERE {} = $1 ORDER BY \"id\" LIMIT 1001",
            related::selected(Some(&product.key), &child.fields),
            quoted(&child.table),
            quoted(&product.key.name)
        ),
    );
    out += "/// Every child of a parent in id order, one row past the export limit.\npub async fn export_children(pool: &PgPool, parent: i32) -> Result<Vec<ChildRow>, sqlx::Error> {\n    sqlx::query_as::<_, ChildRow>(CHILD_EXPORT).bind(parent).fetch_all(pool).await\n}\n";
    out += DB_LEDGER;
    out + &format!(
        "\n/// alembic downgrade base: the children, then the parents.\npub const DOWNGRADE: [&str; 2] = [r#\"DROP TABLE {}\"#, r#\"DROP TABLE {}\"#];\n",
        quoted(&child.table),
        quoted(&product.parent.table)
    )
}

const DB_LEDGER: &str = r##"
/// Serializes migrators on one database, until the transaction ends.
pub const ADVISORY_LOCK: &str = "SELECT pg_advisory_xact_lock($1)";

/// The migration bookkeeping, in a table no prompt can name (naming.json W3).
pub const LEDGER: &str = r#"CREATE TABLE IF NOT EXISTS "_som_rust_migrations" ("version" BIGINT PRIMARY KEY, "revision" TEXT NOT NULL, "applied_at" TIMESTAMPTZ NOT NULL DEFAULT now())"#;

pub const LEDGER_APPLIED: &str = r#"SELECT EXISTS (SELECT 1 FROM "_som_rust_migrations" WHERE "version" = $1)"#;

pub const LEDGER_INSERT: &str = r#"INSERT INTO "_som_rust_migrations" ("version", "revision") VALUES ($1, $2)"#;

pub const LEDGER_DELETE: &str = r#"DELETE FROM "_som_rust_migrations" WHERE "version" = $1"#;
"##;

// ---------------------------------------------------------------- src/integrations.rs

/// The settings, the RS256 token checks and the caller extractors. A v5 product reads the token
/// settings before the database, and its role claim may be any JSON value.
fn integrations(v5: bool) -> String {
    if !v5 {
        return INTEGRATIONS.to_owned();
    }
    INTEGRATIONS
        .replace(
            "            database: database()?,\n            auth: Jwt::load()?,\n",
            "            auth: Jwt::load()?,\n            database: database()?,\n",
        )
        .replace(
            "            database: database_url(url)?,\n            auth: Jwt::load()?,\n",
            "            auth: Jwt::load()?,\n            database: database_url(url)?,\n",
        )
        .replace("    pub role: String,\n", "    pub role: Value,\n")
        .replace(
            "(Some(Value::String(sub)), Value::String(role)) if !sub.is_empty()",
            "(Some(Value::String(sub)), role) if !sub.is_empty()",
        )
}

const INTEGRATIONS: &str = r##"use std::str::FromStr;

use axum::extract::FromRequestParts;
use axum::http::{HeaderMap, Method, header::AUTHORIZATION, request::Parts};
use base64::Engine;
use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use serde::Serialize;
use serde_json::{Map, Value};
use sqlx::postgres::PgConnectOptions;

use crate::api::{ApiError, AppState};

/// The settings of the product, read in the order the Python product reads them at startup.
pub struct Settings {
    pub database: PgConnectOptions,
    pub auth: Jwt,
}

impl Settings {
    pub fn load() -> Result<Self, String> {
        Ok(Self {
            database: database()?,
            auth: Jwt::load()?,
        })
    }

    /// The tests read their database from TEST_DATABASE_URL and the rest as the product does.
    pub fn load_with(url: &str) -> Result<Self, String> {
        Ok(Self {
            database: database_url(url)?,
            auth: Jwt::load()?,
        })
    }
}

/// setting(NAME): a missing or empty value refuses startup.
fn setting(name: &str) -> Result<String, String> {
    let value = std::env::var_os(name)
        .map(|found| found.to_string_lossy().into_owned())
        .unwrap_or_default();
    if value.is_empty() {
        return Err(format!("{name} is required"));
    }
    Ok(value)
}

/// The DATABASE_URL setting, which migrations read alone.
pub fn database() -> Result<PgConnectOptions, String> {
    database_url(&setting("DATABASE_URL")?)
}

/// DATABASE_URL is a postgresql+psycopg URL. The driver reads it without the +psycopg suffix, and
/// a URL without a host takes the host from PGHOST, as libpq does.
pub fn database_url(value: &str) -> Result<PgConnectOptions, String> {
    let Some(rest) = value.strip_prefix("postgresql+psycopg://") else {
        return Err("DATABASE_URL must be a postgresql+psycopg URL".to_owned());
    };
    let mut options = PgConnectOptions::from_str(&format!("postgresql://{rest}"))
        .map_err(|error| format!("DATABASE_URL could not be read: {error}"))?;
    let authority = rest.split(['/', '?']).next().unwrap_or_default();
    let host = authority.rsplit('@').next().unwrap_or_default();
    if host.is_empty() || host.starts_with(':') {
        let local = PgConnectOptions::new_without_pgpass();
        options = match local.get_socket() {
            Some(socket) => options.socket(socket),
            None => options.host(local.get_host()),
        };
    }
    Ok(options)
}

/// One authenticated caller.
#[derive(Clone, Debug, Serialize)]
pub struct Identity {
    pub sub: String,
    pub role: String,
}

/// RS256 bearer tokens checked as PyJWT 2.15 checks them, with sub, exp, iat, iss and aud required.
pub struct Jwt {
    modulus: Vec<u8>,
    exponent: Vec<u8>,
    issuer: String,
    audience: String,
}

const RSA_KEY_TOO_SMALL: &str = "JWT_PUBLIC_KEY_FILE must contain an RSA key of at least 2048 bits";

impl Jwt {
    fn load() -> Result<Self, String> {
        let path = setting("JWT_PUBLIC_KEY_FILE")?;
        let pem = std::fs::read(&path).map_err(|error| python_os_error(&error, &path))?;
        let issuer = setting("JWT_ISSUER")?;
        let audience = setting("JWT_AUDIENCE")?;
        let (modulus, exponent) = match public_key(&pem)? {
            PublicKey::Rsa { modulus, exponent } => (modulus, exponent),
            PublicKey::Other => return Err(RSA_KEY_TOO_SMALL.to_owned()),
        };
        if bit_length(&modulus) < 2048 {
            return Err(RSA_KEY_TOO_SMALL.to_owned());
        }
        Ok(Self {
            modulus,
            exponent,
            issuer,
            audience,
        })
    }

    /// The caller of a request, or 401.
    pub fn authenticate(&self, headers: &HeaderMap) -> Result<Identity, ApiError> {
        let token = bearer(headers).ok_or(ApiError::Unauthorized)?;
        let claims = self.decode(&token).ok_or(ApiError::Unauthorized)?;
        let role = claims
            .get("role")
            .cloned()
            .unwrap_or_else(|| Value::from("reader"));
        match (claims.get("sub"), role) {
            (Some(Value::String(sub)), Value::String(role)) if !sub.is_empty() => Ok(Identity {
                sub: sub.clone(),
                role,
            }),
            _ => Err(ApiError::Unauthorized),
        }
    }

    fn decode(&self, token: &str) -> Option<Map<String, Value>> {
        let (signing_input, signature) = token.rsplit_once('.')?;
        let (header, payload) = signing_input.split_once('.')?;
        let header = match python_json(&segment(header)?)?.0 {
            Value::Object(found) => found,
            _ => return None,
        };
        if let Some(kid) = header.get("kid") {
            kid.as_str()?;
        }
        if header.contains_key("crit") {
            let Some(Value::Array(crit)) = header.get("crit") else {
                return None;
            };
            if crit.is_empty()
                || !crit
                    .iter()
                    .all(|ext| ext.as_str() == Some("b64") && header.contains_key("b64"))
            {
                return None;
            }
        }
        if header.get("b64") == Some(&Value::Bool(false)) {
            return None;
        }
        let payload = segment(payload)?;
        let signature = segment(signature)?;
        if header.get("alg").and_then(Value::as_str) != Some("RS256") {
            return None;
        }
        ring::signature::RsaPublicKeyComponents {
            n: &self.modulus,
            e: &self.exponent,
        }
        .verify(
            &ring::signature::RSA_PKCS1_2048_8192_SHA256,
            signing_input.as_bytes(),
            &signature,
        )
        .ok()?;
        let Value::Object(claims) = python_json(&payload)?.0 else {
            return None;
        };
        self.check_claims(&claims).then_some(claims)
    }

    fn check_claims(&self, claims: &Map<String, Value>) -> bool {
        let required = ["sub", "exp", "iat", "iss", "aud"];
        if required
            .iter()
            .any(|name| claims.get(*name).is_none_or(Value::is_null))
        {
            return false;
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |elapsed| i128::from(elapsed.as_secs()));
        let time = |name: &str| claims.get(name).map(python_int);
        if matches!(time("iat"), Some(None)) || time("iat").flatten().is_some_and(|iat| iat > now) {
            return false;
        }
        if matches!(time("nbf"), Some(None)) || time("nbf").flatten().is_some_and(|nbf| nbf > now) {
            return false;
        }
        if matches!(time("exp"), Some(None)) || time("exp").flatten().is_some_and(|exp| exp <= now)
        {
            return false;
        }
        if claims.get("iss").and_then(Value::as_str) != Some(self.issuer.as_str()) {
            return false;
        }
        let audience = match &claims["aud"] {
            Value::String(one) if !one.is_empty() => vec![one.as_str()],
            Value::Array(many) if !many.is_empty() => {
                let Some(names) = many.iter().map(Value::as_str).collect::<Option<Vec<_>>>() else {
                    return false;
                };
                names
            }
            _ => return false,
        };
        if !audience.contains(&self.audience.as_str()) {
            return false;
        }
        ["sub", "jti"]
            .iter()
            .all(|name| claims.get(*name).is_none_or(Value::is_string))
    }
}

/// The token of an Authorization header "Bearer <token>", read as Starlette reads it (latin-1).
fn bearer(headers: &HeaderMap) -> Option<String> {
    let raw = headers
        .get(AUTHORIZATION)
        .map(|value| value.as_bytes())
        .unwrap_or_default();
    let text: String = raw.iter().map(|&byte| char::from(byte)).collect();
    let (scheme, token) = text.split_once(' ')?;
    if scheme.to_lowercase() != "bearer" || token.is_empty() || token.chars().count() > 16384 {
        return None;
    }
    Some(token.to_owned())
}

/// A base64url segment: canonical, with at most two padding characters when the length allows.
fn segment(text: &str) -> Option<Vec<u8>> {
    let stripped = text.trim_end_matches('=');
    let padding = text.len() - stripped.len();
    if padding > 2 || (padding > 0 && !text.len().is_multiple_of(4)) {
        return None;
    }
    URL_SAFE_NO_PAD.decode(stripped).ok()
}

/// Python int() of a JSON claim: None when it raises.
fn python_int(value: &Value) -> Option<i128> {
    match value {
        Value::Bool(flag) => Some(i128::from(*flag)),
        Value::Number(number) => {
            if let Some(whole) = number.as_i64() {
                Some(i128::from(whole))
            } else if let Some(whole) = number.as_u64() {
                Some(i128::from(whole))
            } else {
                number
                    .as_f64()
                    .filter(|float| float.is_finite())
                    .map(|float| float.trunc() as i128)
            }
        }
        Value::String(text) => {
            let text = text.trim();
            let digits = text.strip_prefix(['+', '-']).unwrap_or(text);
            let valid = !digits.is_empty()
                && !digits.starts_with('_')
                && !digits.ends_with('_')
                && !digits.contains("__")
                && digits.chars().all(|c| c.is_ascii_digit() || c == '_');
            if !valid {
                return None;
            }
            let clean: String = digits.chars().filter(|c| *c != '_').collect();
            let magnitude = clean.parse::<i128>().unwrap_or(i128::MAX);
            Some(if text.starts_with('-') {
                -magnitude
            } else {
                magnitude
            })
        }
        _ => None,
    }
}

/// json.loads also reads NaN, Infinity, -Infinity and numbers beyond a double. Those read as null
/// here, and the flag tells that one was there.
pub fn python_json(bytes: &[u8]) -> Option<(Value, bool)> {
    python_json_detail(bytes).ok()
}

pub fn python_json_detail(bytes: &[u8]) -> Result<(Value, bool), serde_json::Error> {
    let error = match serde_json::from_slice(bytes) {
        Ok(value) => return Ok((value, false)),
        Err(error) => error,
    };
    let mut rewritten = Vec::with_capacity(bytes.len());
    let mut nonfinite = false;
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if byte == b'"' {
            let start = index;
            index += 1;
            while index < bytes.len() && bytes[index] != b'"' {
                index += if bytes[index] == b'\\' { 2 } else { 1 };
            }
            index = (index + 1).min(bytes.len());
            rewritten.extend_from_slice(&bytes[start..index]);
            continue;
        }
        let rest = &bytes[index..];
        let word = [&b"-Infinity"[..], b"Infinity", b"NaN"]
            .into_iter()
            .find(|word| rest.starts_with(word));
        if let Some(word) = word {
            rewritten.extend_from_slice(b"null");
            nonfinite = true;
            index += word.len();
            continue;
        }
        if byte == b'-' || byte.is_ascii_digit() {
            let length = rest
                .iter()
                .take_while(|c| c.is_ascii_digit() || matches!(c, b'-' | b'+' | b'.' | b'e' | b'E'))
                .count();
            let token = &rest[..length];
            let infinite = std::str::from_utf8(token)
                .ok()
                .and_then(|text| text.parse::<f64>().ok())
                .is_some_and(f64::is_infinite);
            if infinite {
                rewritten.extend_from_slice(b"null");
                nonfinite = true;
            } else {
                rewritten.extend_from_slice(token);
            }
            index += length;
            continue;
        }
        rewritten.push(byte);
        index += 1;
    }
    if !nonfinite {
        return Err(error);
    }
    serde_json::from_slice(&rewritten)
        .map(|value| (value, true))
        .map_err(|_| error)
}

enum PublicKey {
    Rsa { modulus: Vec<u8>, exponent: Vec<u8> },
    Other,
}

/// load_pem_public_key: a SubjectPublicKeyInfo ("PUBLIC KEY") or PKCS#1 ("RSA PUBLIC KEY") block.
fn public_key(pem: &[u8]) -> Result<PublicKey, String> {
    let unreadable = || "JWT_PUBLIC_KEY_FILE could not be read as a PEM public key".to_owned();
    let text = String::from_utf8_lossy(pem);
    let (label, body) = pem_block(&text).ok_or_else(unreadable)?;
    let compact: String = body.chars().filter(|c| !c.is_ascii_whitespace()).collect();
    let der = STANDARD.decode(compact).map_err(|_| unreadable())?;
    match label {
        "RSA PUBLIC KEY" => rsa_components(&der).ok_or_else(unreadable),
        "PUBLIC KEY" => {
            let (info, _) = der_element(&der, 0x30).ok_or_else(unreadable)?;
            let (algorithm, rest) = der_element(info, 0x30).ok_or_else(unreadable)?;
            let (oid, _) = der_element(algorithm, 0x06).ok_or_else(unreadable)?;
            let (bits, _) = der_element(rest, 0x03).ok_or_else(unreadable)?;
            const RSA_ENCRYPTION: &[u8] = &[0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01];
            if oid != RSA_ENCRYPTION {
                return Ok(PublicKey::Other);
            }
            let key = bits.strip_prefix(&[0]).ok_or_else(unreadable)?;
            rsa_components(key).ok_or_else(unreadable)
        }
        _ => Err(unreadable()),
    }
}

fn pem_block(text: &str) -> Option<(&str, &str)> {
    let start = text.find("-----BEGIN ")? + "-----BEGIN ".len();
    let label_end = start + text[start..].find("-----")?;
    let label = &text[start..label_end];
    let body_start = label_end + "-----".len();
    let end = format!("-----END {label}-----");
    let body_end = body_start + text[body_start..].find(&end)?;
    Some((label, &text[body_start..body_end]))
}

fn rsa_components(der: &[u8]) -> Option<PublicKey> {
    let (sequence, _) = der_element(der, 0x30)?;
    let (modulus, rest) = der_element(sequence, 0x02)?;
    let (exponent, _) = der_element(rest, 0x02)?;
    let trim = |bytes: &[u8]| {
        let first = bytes
            .iter()
            .position(|byte| *byte != 0)
            .unwrap_or(bytes.len());
        bytes[first..].to_vec()
    };
    Some(PublicKey::Rsa {
        modulus: trim(modulus),
        exponent: trim(exponent),
    })
}

/// The content of one DER element with the given tag, and the bytes after it.
fn der_element(input: &[u8], tag: u8) -> Option<(&[u8], &[u8])> {
    let (&found, rest) = input.split_first()?;
    if found != tag {
        return None;
    }
    let (&first, rest) = rest.split_first()?;
    let (length, rest) = if first < 0x80 {
        (usize::from(first), rest)
    } else {
        let count = usize::from(first & 0x7f);
        if count == 0 || count > 4 || rest.len() < count {
            return None;
        }
        let length = rest[..count]
            .iter()
            .fold(0usize, |total, byte| (total << 8) | usize::from(*byte));
        (length, &rest[count..])
    };
    (rest.len() >= length).then(|| rest.split_at(length))
}

fn bit_length(magnitude: &[u8]) -> usize {
    magnitude.first().map_or(0, |first| {
        magnitude.len() * 8 - first.leading_zeros() as usize
    })
}

/// Python's message for an OSError on a path: "[Errno 2] No such file or directory: 'key.pem'".
fn python_os_error(error: &std::io::Error, path: &str) -> String {
    let text = error.to_string();
    let reason = text.split(" (os error").next().unwrap_or(&text);
    match error.raw_os_error() {
        Some(code) => format!("[Errno {code}] {reason}: '{path}'"),
        None => format!("{reason}: '{path}'"),
    }
}

/// A caller with a valid token. Writes need the editor role.
pub struct Access(pub Identity);

impl FromRequestParts<AppState> for Access {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, state: &AppState) -> Result<Self, ApiError> {
        let identity = state.auth.authenticate(&parts.headers)?;
        let reads = [Method::GET, Method::HEAD, Method::OPTIONS];
        if !reads.contains(&parts.method) && identity.role != "editor" {
            return Err(ApiError::Forbidden);
        }
        Ok(Self(identity))
    }
}

impl FromRequestParts<AppState> for Identity {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, state: &AppState) -> Result<Self, ApiError> {
        state.auth.authenticate(&parts.headers)
    }
}
"##;

// ---------------------------------------------------------------- tests/api.rs

fn tests(flow: Flow, product: &Product) -> String {
    let ops = &product.ops;
    let parents = &product.parent.path;
    let children = format!("{parents}/1{}", product.child.path);
    let mut routes: Vec<(&str, String)> = Vec::new();
    for (selected, method, path) in [
        (ops.parent_create, "POST", parents.clone()),
        (ops.parent_get, "GET", format!("{parents}/1")),
        (ops.parent_list, "GET", parents.clone()),
        (ops.parent_patch, "PATCH", format!("{parents}/1")),
        (ops.parent_delete, "DELETE", format!("{parents}/1")),
        (ops.child_create, "POST", children.clone()),
        (ops.child_get, "GET", format!("{children}/1")),
        (ops.child_list, "GET", children.clone()),
        (ops.child_patch, "PATCH", format!("{children}/1")),
        (ops.child_delete, "DELETE", format!("{children}/1")),
        (ops.batch, "POST", format!("{parents}/with-children")),
        (true, "GET", format!("{parents}/1/export")),
        (flow == Flow::Csv, "POST", format!("{parents}/1/import")),
    ] {
        if selected {
            routes.push((method, path));
        }
    }
    let rows: String = routes
        .iter()
        .map(|(method, path)| format!("    (\"{method}\", \"{path}\"),\n"))
        .collect();
    TESTS
        .replace("@COUNT@", &routes.len().to_string())
        .replace("@ROUTES@\n", &rows)
}

const TESTS: &str = r#"use std::sync::Arc;

use axum::{
    Router,
    body::Body,
    http::{Request, StatusCode},
};
use serde_json::Value;
use tower::ServiceExt;

/// The application on TEST_DATABASE_URL, with the token settings the service reads.
fn app() -> Router {
    let url = std::env::var("TEST_DATABASE_URL").unwrap_or_default();
    assert!(
        url.starts_with("postgresql+psycopg://"),
        "TEST_DATABASE_URL must name an isolated PostgreSQL database"
    );
    let settings = som_app::integrations::Settings::load_with(&url).expect("settings");
    let pool = sqlx::postgres::PgPoolOptions::new()
        .acquire_timeout(std::time::Duration::from_secs(3))
        .connect_lazy_with(settings.database);
    som_app::router(som_app::AppState {
        pool,
        auth: Arc::new(settings.auth),
    })
}

async fn status(app: &Router, request: Request<Body>) -> StatusCode {
    app.clone().oneshot(request).await.expect("response").status()
}

/// The selected routes and the export route, each of which needs a caller.
const ROUTES: [(&str, &str); @COUNT@] = [
@ROUTES@
];

#[tokio::test]
async fn selected_routes_require_identity() {
    let app = app();
    for (method, path) in ROUTES {
        let request = Request::builder()
            .method(method)
            .uri(path)
            .header("content-type", "application/json")
            .body(Body::from("{}"))
            .expect("request");
        assert_eq!(status(&app, request).await, StatusCode::UNAUTHORIZED, "{method} {path}");
    }
}

/// TEST_AUTH_HEADERS is the test client's identity: with an authorization header the caller is
/// known, without one it is refused.
#[tokio::test]
async fn test_client_headers_identify_the_caller() {
    let app = app();
    let headers: serde_json::Map<String, Value> =
        serde_json::from_str(&std::env::var("TEST_AUTH_HEADERS").unwrap_or_else(|_| "{}".into()))
            .expect("TEST_AUTH_HEADERS is a JSON object");
    let mut request = Request::builder().method("GET").uri("/auth/me");
    for (name, value) in &headers {
        request = request.header(name.as_str(), value.as_str().expect("header text"));
    }
    let expected = if headers.contains_key("authorization") {
        StatusCode::OK
    } else {
        StatusCode::UNAUTHORIZED
    };
    let request = request.body(Body::empty()).expect("request");
    assert_eq!(status(&app, request).await, expected);
}
"#;

// ---------------------------------------------------------------- README.md

fn readme(params: &Value, product: &Product) -> Result<String, String> {
    let package = text(params, "package")?;
    let version = text(params, "version")?;
    let revision = text(params, "revision")?;
    let down = params["down_revision"].as_str().unwrap_or("none");
    let ops = &product.ops;
    let (parent, child) = (&product.parent, &product.child);
    let workflow = Workflow::read(params, child)?;
    let parents = &parent.path;
    let children = format!("{parents}/{{parent_id}}{}", child.path);
    let query = |resource: &Resource| {
        let mut text = String::from("Query: ");
        if let Some(name) = &resource.filter {
            text += &format!("`{name}` (boolean), ");
        }
        if let Some(name) = &resource.date {
            text += &format!("`before` (a date; rows whose `{name}` is earlier), ");
        }
        text + &format!(
            "`limit` (1 to {}, default 20) and `offset` (default 0). Rows in id order.",
            resource.max_limit
        )
    };
    let mut routes = String::new();
    if !product.owned() {
        routes += HEALTH_LINE;
    }
    routes += "- `GET /auth/me`: the caller, `{\"sub\", \"role\"}`.\n";
    for (selected, line) in [
        (
            ops.parent_list,
            format!("- `GET {parents}`: list parents. {}\n", query(parent)),
        ),
        (
            ops.parent_create,
            format!(
                "- `POST {parents}`: create a parent. 201, or 409 for a unique value conflict.\n"
            ),
        ),
        (
            ops.parent_get,
            format!(
                "- `GET {parents}/{{parent_id}}`: get a parent. 404 `parent not found` if it does not exist.\n"
            ),
        ),
        (
            ops.parent_patch,
            format!("- `PATCH {parents}/{{parent_id}}`: update the supplied fields of a parent.\n"),
        ),
        (
            ops.parent_delete,
            format!(
                "- `DELETE {parents}/{{parent_id}}`: delete a parent. 204, or 409 while it has children.\n"
            ),
        ),
        (
            ops.child_list,
            format!(
                "- `GET {children}`: list the children of a parent. {}\n",
                query(child)
            ),
        ),
        (
            ops.child_create,
            format!("- `POST {children}`: create a child of a parent.\n"),
        ),
        (
            ops.child_get,
            format!(
                "- `GET {children}/{{child_id}}`: get a child. 404 `child not found` if it does not exist under this parent.\n"
            ),
        ),
        (
            ops.child_patch,
            format!("- `PATCH {children}/{{child_id}}`: update the supplied fields of a child.\n"),
        ),
        (
            ops.child_delete,
            format!("- `DELETE {children}/{{child_id}}`: delete a child. 204.\n"),
        ),
        (
            ops.batch,
            format!(
                "- `POST {parents}/with-children`: create a parent and 1 to 20 children in one transaction.\n  A failing child leaves no parent and no child.\n"
            ),
        ),
        (true, workflow.routes(parents, child)),
    ] {
        if selected {
            routes += &line;
        }
    }
    Ok(format!(
        r#"# {package}

A Rust service generated by SOM from a `{version}` prompt for the package `{package}`.
The Cargo package, library and binary are `som_app`. It uses axum, sqlx and tokio on PostgreSQL.
Its shape is `capability_postgresql`: RS256 bearer tokens (`auth: jwt`), no object storage
(`storage: none`) and {}.

Table `{}` holds the parents and table `{}` the children. A child row belongs to one parent
through `{}`, which is `ON DELETE RESTRICT`.

## Routes

{routes}
Every route but `/healthz` needs `Authorization: Bearer <token>`, an RS256 token with `sub`,
`exp`, `iat`, `iss` and `aud`. A missing, expired or invalid token answers 401. A write needs the
`editor` role and answers 403 otherwise; a token without a role is a `reader`.

Every child route answers 404 `parent not found` when the parent does not exist. A unique value
or foreign key conflict answers 409 `relationship or unique value conflict`. Invalid input returns
422 with `{{"detail": [{{"type", "loc", "msg"}}]}}`.

## Settings

- `DATABASE_URL` (required): a `postgresql+psycopg://` URL.
- `JWT_PUBLIC_KEY_FILE` (required): a PEM RSA public key of at least 2048 bits.
- `JWT_ISSUER` (required): the `iss` every token must carry.
- `JWT_AUDIENCE` (required): an `aud` every token must carry.
- `SOM_APP_ADDR`: the listen address. It defaults to `127.0.0.1:8000`.

A missing or empty required setting refuses startup with `<NAME> is required`.

## Commands

```sh
som_app serve              # also with no subcommand
som_app migrate upgrade
som_app migrate downgrade
som_app check-config
```

`serve` does not migrate: run `som_app migrate upgrade` first. `check-config` reads every setting
and exits 0, or exits 1 with the startup message.

## Test and check

```sh
TEST_DATABASE_URL=postgresql+psycopg://user@127.0.0.1:5432/app_test TEST_AUTH_HEADERS='{{"authorization": "Bearer <token>"}}' cargo test --locked --offline -- --test-threads=1
cargo build --locked --offline
cargo clippy --locked --offline --all-targets -- -D warnings
rustfmt --edition 2024 --check src/*.rs tests/*.rs
```

The tests read the token settings above. `TEST_AUTH_HEADERS` holds the test client's headers.

## Migration

`migrations/0001_{revision}.sql` holds revision `{revision}` (down revision `{down}`). It creates
both tables, the foreign key and the list indexes. `som_app migrate upgrade` applies it once and
records it in `_som_rust_migrations`; `som_app migrate downgrade` drops both tables.
"#,
        workflow.title(),
        parent.table,
        child.table,
        product.key.name
    ))
}
