//! The v5 blocks: a parent and a child resource joined by a restricted foreign key, the scoped
//! child routes, and the atomic batch of one parent with 1 to 20 children (issue #49).
//!
//! The Rust item names come from fixed roles: Parent*, Child* and ParentWithChildren*.
use serde_json::Value;

use super::{
    API_BODY, API_DAY, API_FLAG, API_LIST_HELPERS, API_USES, Field, HARNESS_TOOLS, HARNESS_USES,
    Kind, NOT_NULL, PRESENT, SCHEMA_USES, STAMPS, TOO_LONG, column_line, create_struct,
    parse_fields, quoted, read_struct, row_struct, sample_fns, sql_const, text, update_struct,
};

/// One side of the relation, read from its resources.parent or resources.child param.
struct Resource {
    table: String,
    path: String,
    fields: Vec<Field>,
    filter: Option<String>,
    date: Option<String>,
    max_limit: i64,
}

impl Resource {
    fn read(params: &Value, key: &str) -> Result<Self, String> {
        let value = &params[key];
        let named = |name: &str| {
            value[name]
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| format!("param {key}.{name} is not a string"))
        };
        let query = |name: &str| value["query"][name].as_str().map(str::to_owned);
        Ok(Self {
            table: named("table")?,
            path: named("path")?,
            fields: parse_fields(&value["fields"])?,
            filter: query("filter_field"),
            date: query("date_field"),
            max_limit: value["query"]["max_limit"]
                .as_i64()
                .ok_or_else(|| format!("param {key}.query.max_limit is not an integer"))?,
        })
    }

    /// Only one row per value of a unique NOT NULL bool can exist.
    fn rows(&self) -> usize {
        if self
            .fields
            .iter()
            .any(|field| field.kind == Kind::Bool && field.unique && !field.nullable())
        {
            2
        } else {
            25
        }
    }

    /// The first unique field that a test can copy into another row.
    fn unique_value(&self) -> Option<&Field> {
        self.fields
            .iter()
            .find(|field| field.unique && field.kind != Kind::Bool)
    }
}

/// The foreign key column of the child: a required INTEGER.
fn foreign_key(params: &Value) -> Result<Field, String> {
    Ok(Field {
        name: text(params, "foreign_key")?.to_owned(),
        kind: Kind::Int,
        required: true,
        unique: false,
        default_false: false,
    })
}

/// Render one v5 block from its resolved params.
pub fn render(id: &str, params: &Value) -> Result<String, String> {
    let parent = || Resource::read(params, "parent");
    let child = || Resource::read(params, "child");
    Ok(match id {
        "rust.models.related.rows" => models(&parent()?, &child()?, &foreign_key(params)?),
        "rust.schemas.related.support" => schema_support(&parent()?, &child()?),
        "rust.schemas.related.parent" => schemas("Parent", None, &parent()?),
        "rust.schemas.related.child" => schemas("Child", Some(&foreign_key(params)?), &child()?),
        "rust.schemas.related.batch" => SCHEMAS_BATCH.to_owned(),
        "rust.api.related.support" => [API_USES, API_SUPPORT, API_BODY].concat(),
        "rust.api.related.list" => api_list(&parent()?, &child()?),
        "rust.api.related.parent" => API_PARENT.to_owned(),
        "rust.api.related.child" => API_CHILD.to_owned(),
        "rust.api.related.router" => API_ROUTER
            .replace("@PARENTS@", text(params, "parent_path")?)
            .replace("@CHILDREN@", text(params, "child_path")?),
        "rust.db.related.ping" => DB_PING.to_owned(),
        "rust.db.related.parent" => db_parent(&parent()?),
        "rust.db.related.child" => db_child(&child()?, &foreign_key(params)?),
        "rust.migration.related.parent" => migration_parent(params, &parent()?)?,
        "rust.migration.related.child" => {
            migration_child(params, &child()?, &foreign_key(params)?)?
        }
        "rust.tests.related.harness" => tests_harness(&parent()?, &child()?, params)?,
        "rust.tests.related.crud" => TESTS_CRUD.to_owned(),
        "rust.tests.related.validation" => tests_validation(&parent()?, &child()?),
        "rust.tests.related.unique" => tests_unique(&parent()?, &child()?),
        "rust.tests.related.list" => tests_list(&parent()?, &child()?),
        "rust.tests.related.scope" => TESTS_SCOPE.to_owned(),
        "rust.tests.related.batch" => tests_batch(&parent()?, &child()?),
        "rust.tests.related.fallback" => TESTS_FALLBACK.to_owned(),
        "rust.readme.related" => readme(params, &parent()?, &child()?)?,
        _ => return Err(format!("no renderer for block {id}")),
    })
}

// ---------------------------------------------------------------- src/models.rs

fn models(parent: &Resource, child: &Resource, key: &Field) -> String {
    row_struct("ParentRow", None, &parent.fields, true)
        + "\n"
        + &row_struct("ChildRow", Some(key), &child.fields, true)
}

// ---------------------------------------------------------------- src/schemas.rs

fn schema_support(parent: &Resource, child: &Resource) -> String {
    let fields = || parent.fields.iter().chain(&child.fields);
    let mut out = String::from(SCHEMA_USES);
    if fields().any(|field| field.kind == Kind::Str) {
        out += TOO_LONG;
    }
    if fields().any(|field| !field.nullable()) {
        out += NOT_NULL;
    }
    out + PRESENT
}

/// Create, Update and Read for one side. Read carries the foreign key after id.
fn schemas(role: &str, key: Option<&Field>, resource: &Resource) -> String {
    let (update, checks) = update_struct(&format!("{role}Update"), &resource.fields, true);
    format!(
        "\n{}\n{update}\n{checks}\n{}",
        create_struct(&format!("{role}Create"), &resource.fields, true),
        read_struct(
            &format!("{role}Read"),
            &format!("{role}Row"),
            key,
            &resource.fields,
            true
        )
    )
}

const SCHEMAS_BATCH: &str = r#"
/// One parent and its children, created in one transaction.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ParentWithChildrenCreate {
    pub parent: ParentCreate,
    pub children: Vec<ChildCreate>,
}

impl ParentWithChildrenCreate {
    pub fn problems(&self) -> Vec<serde_json::Value> {
        let mut found = self.parent.problems();
        if self.children.is_empty() {
            found.push(serde_json::json!({"type": "too_short", "loc": ["body", "children"], "msg": "List should have at least 1 item after validation, not 0"}));
        }
        if self.children.len() > 20 {
            found.push(serde_json::json!({"type": "too_long", "loc": ["body", "children"], "msg": format!("List should have at most 20 items after validation, not {}", self.children.len())}));
        }
        for child in &self.children {
            found.extend(child.problems());
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

const API_SUPPORT: &str = r#"use crate::schemas::{
    ChildCreate, ChildRead, ChildUpdate, ParentCreate, ParentRead, ParentUpdate, ParentWithChildrenCreate,
    ParentWithChildrenRead,
};

const NOT_INTEGER: &str = "Input should be a valid integer, unable to parse string as an integer";

pub enum ApiError {
    ParentMissing,
    ChildMissing,
    Conflict,
    Invalid(Vec<Value>),
    Internal,
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let (status, body) = match self {
            Self::ParentMissing => (StatusCode::NOT_FOUND, json!({"detail": "parent not found"})),
            Self::ChildMissing => (StatusCode::NOT_FOUND, json!({"detail": "child not found"})),
            Self::Conflict => (StatusCode::CONFLICT, json!({"detail": "relationship or unique value conflict"})),
            Self::Invalid(problems) => (StatusCode::UNPROCESSABLE_ENTITY, json!({"detail": problems})),
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

/// A child insert whose parent is gone by then answers 404, not 409.
fn missing_parent(error: sqlx::Error) -> ApiError {
    let lost = error.as_database_error().and_then(|found| found.code()).as_deref() == Some("23503");
    if lost { ApiError::ParentMissing } else { ApiError::from(error) }
}

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

/// The query lines of one list handler and the db call arguments they give.
fn query_lines(resource: &Resource, max: &str) -> (String, String) {
    let mut out = String::new();
    let mut args = String::new();
    if let Some(name) = &resource.filter {
        out += &format!(
            "    let filter = single(&pairs, \"{name}\", &mut problems).and_then(|text| flag(text, \"{name}\", &mut problems));\n"
        );
        args += "filter, ";
    }
    if resource.date.is_some() {
        out += "    let before = single(&pairs, \"before\", &mut problems).and_then(|text| day(text, \"before\", &mut problems));\n";
        args += "before, ";
    }
    out += &format!(
        r#"    let limit = single(&pairs, "limit", &mut problems).map_or(Some(20), |text| number(text, "limit", 1, {max}, &mut problems));
    let offset = single(&pairs, "offset", &mut problems).map_or(Some(0), |text| number(text, "offset", 0, i64::MAX, &mut problems));
    let (Some(limit), Some(offset), true) = (limit, offset, problems.is_empty()) else {{
        return Err(ApiError::Invalid(problems));
    }};
"#
    );
    (out, args)
}

fn api_list(parent: &Resource, child: &Resource) -> String {
    let mut out = String::from(API_LIST_HELPERS);
    if parent.filter.is_some() || child.filter.is_some() {
        out += API_FLAG;
    }
    if parent.date.is_some() || child.date.is_some() {
        out += API_DAY;
    }
    out += &format!(
        r#"
/// The query pairs, or a query problem when the query string cannot be read.
fn query_pairs(query: Pairs, problems: &mut Vec<Value>) -> Vec<(String, String)> {{
    match query {{
        Ok(axum::extract::Query(pairs)) => pairs,
        Err(_) => {{
            problems.push(problem("value_error", json!(["query"]), "The query string could not be read"));
            Vec::new()
        }}
    }}
}}

const PARENT_MAX_LIMIT: i64 = {};
const CHILD_MAX_LIMIT: i64 = {};

async fn list_parents(State(pool): State<PgPool>, query: Pairs) -> Result<Json<Vec<ParentRead>>, ApiError> {{
    let mut problems = Vec::new();
    let pairs = query_pairs(query, &mut problems);
"#,
        parent.max_limit, child.max_limit
    );
    let (lines, args) = query_lines(parent, "PARENT_MAX_LIMIT");
    out += &lines;
    out += &format!(
        r#"    let rows = db::list_parents(&pool, {args}limit, offset).await?;
    Ok(Json(rows.into_iter().map(ParentRead::from).collect()))
}}

/// Query problems come before a missing parent, as in the Python product.
async fn list_children(
    State(pool): State<PgPool>,
    path: Result<Path<String>, PathRejection>,
    query: Pairs,
) -> Result<Json<Vec<ChildRead>>, ApiError> {{
    let mut problems = Vec::new();
    let parent = path_id(&segments(path), "parent_id", &mut problems);
    let pairs = query_pairs(query, &mut problems);
"#
    );
    let (lines, args) = query_lines(child, "CHILD_MAX_LIMIT");
    out += &lines;
    out + &format!(
        r#"    let parent = known_parent(&pool, parent).await?;
    let rows = db::list_children(&pool, parent, {args}limit, offset).await?;
    Ok(Json(rows.into_iter().map(ChildRead::from).collect()))
}}
"#
    )
}

const API_PARENT: &str = r#"
async fn create_parent(
    State(pool): State<PgPool>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<(StatusCode, Json<ParentRead>), ApiError> {
    let mut problems = Vec::new();
    let body = read_body::<ParentCreate>(&headers, payload, &mut problems)?;
    if let Some(body) = &body {
        problems.extend(body.problems());
    }
    let (Some(body), true) = (body, problems.is_empty()) else {
        return Err(ApiError::Invalid(problems));
    };
    let row = db::insert_parent(&pool, body).await?;
    Ok((StatusCode::CREATED, Json(ParentRead::from(row))))
}

async fn fetch_parent(
    State(pool): State<PgPool>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Json<ParentRead>, ApiError> {
    let mut problems = Vec::new();
    let id = path_id(&segments(path), "parent_id", &mut problems);
    if !problems.is_empty() {
        return Err(ApiError::Invalid(problems));
    }
    let id = id.ok_or(ApiError::ParentMissing)?;
    let row = db::fetch_parent(&pool, id).await?.ok_or(ApiError::ParentMissing)?;
    Ok(Json(ParentRead::from(row)))
}

async fn update_parent(
    State(pool): State<PgPool>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<Json<ParentRead>, ApiError> {
    let mut problems = Vec::new();
    let id = path_id(&segments(path), "parent_id", &mut problems);
    let body = read_body::<ParentUpdate>(&headers, payload, &mut problems)?;
    if let Some(body) = &body {
        problems.extend(body.problems());
    }
    let (Some(body), true) = (body, problems.is_empty()) else {
        return Err(ApiError::Invalid(problems));
    };
    let id = id.ok_or(ApiError::ParentMissing)?;
    let row = db::update_parent(&pool, id, body).await?.ok_or(ApiError::ParentMissing)?;
    Ok(Json(ParentRead::from(row)))
}

/// A parent that still has children answers 409: the foreign key restricts the delete.
async fn remove_parent(
    State(pool): State<PgPool>,
    path: Result<Path<String>, PathRejection>,
) -> Result<StatusCode, ApiError> {
    let mut problems = Vec::new();
    let id = path_id(&segments(path), "parent_id", &mut problems);
    if !problems.is_empty() {
        return Err(ApiError::Invalid(problems));
    }
    let id = id.ok_or(ApiError::ParentMissing)?;
    if db::delete_parent(&pool, id).await? {
        Ok(StatusCode::NO_CONTENT)
    } else {
        Err(ApiError::ParentMissing)
    }
}

/// POST on a parent item path: only the with-children segment is a route. The parent and every
/// child go in one transaction, so a failing child leaves no row behind.
async fn create_batch(
    State(pool): State<PgPool>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<Response, ApiError> {
    if segments(path) != "with-children" {
        return Ok(not_allowed().await);
    }
    let mut problems = Vec::new();
    let body = read_body::<ParentWithChildrenCreate>(&headers, payload, &mut problems)?;
    if let Some(body) = &body {
        problems.extend(body.problems());
    }
    let (Some(body), true) = (body, problems.is_empty()) else {
        return Err(ApiError::Invalid(problems));
    };
    let mut transaction = pool.begin().await?;
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

const API_CHILD: &str = r#"
/// The parent and child ids of a child item path, with their path problems.
fn child_ids(
    path: Result<Path<(String, String)>, PathRejection>,
    problems: &mut Vec<Value>,
) -> (Option<i32>, Option<i32>) {
    let (parent, child) = segments(path);
    (path_id(&parent, "parent_id", problems), path_id(&child, "child_id", problems))
}

async fn create_child(
    State(pool): State<PgPool>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<(StatusCode, Json<ChildRead>), ApiError> {
    let mut problems = Vec::new();
    let parent = path_id(&segments(path), "parent_id", &mut problems);
    let body = read_body::<ChildCreate>(&headers, payload, &mut problems)?;
    if let Some(body) = &body {
        problems.extend(body.problems());
    }
    let (Some(body), true) = (body, problems.is_empty()) else {
        return Err(ApiError::Invalid(problems));
    };
    let parent = known_parent(&pool, parent).await?;
    let row = db::insert_child(&pool, parent, body).await.map_err(missing_parent)?;
    Ok((StatusCode::CREATED, Json(ChildRead::from(row))))
}

async fn fetch_child(
    State(pool): State<PgPool>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Json<ChildRead>, ApiError> {
    let mut problems = Vec::new();
    let (parent, id) = child_ids(path, &mut problems);
    if !problems.is_empty() {
        return Err(ApiError::Invalid(problems));
    }
    let parent = known_parent(&pool, parent).await?;
    let id = id.ok_or(ApiError::ChildMissing)?;
    let row = db::fetch_child(&pool, id, parent).await?.ok_or(ApiError::ChildMissing)?;
    Ok(Json(ChildRead::from(row)))
}

async fn update_child(
    State(pool): State<PgPool>,
    path: Result<Path<(String, String)>, PathRejection>,
    headers: HeaderMap,
    payload: Result<Bytes, BytesRejection>,
) -> Result<Json<ChildRead>, ApiError> {
    let mut problems = Vec::new();
    let (parent, id) = child_ids(path, &mut problems);
    let body = read_body::<ChildUpdate>(&headers, payload, &mut problems)?;
    if let Some(body) = &body {
        problems.extend(body.problems());
    }
    let (Some(body), true) = (body, problems.is_empty()) else {
        return Err(ApiError::Invalid(problems));
    };
    let parent = known_parent(&pool, parent).await?;
    let id = id.ok_or(ApiError::ChildMissing)?;
    let row = db::update_child(&pool, id, parent, body).await?.ok_or(ApiError::ChildMissing)?;
    Ok(Json(ChildRead::from(row)))
}

async fn remove_child(
    State(pool): State<PgPool>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<StatusCode, ApiError> {
    let mut problems = Vec::new();
    let (parent, id) = child_ids(path, &mut problems);
    if !problems.is_empty() {
        return Err(ApiError::Invalid(problems));
    }
    let parent = known_parent(&pool, parent).await?;
    let id = id.ok_or(ApiError::ChildMissing)?;
    if db::delete_child(&pool, id, parent).await? {
        Ok(StatusCode::NO_CONTENT)
    } else {
        Err(ApiError::ChildMissing)
    }
}
"#;

const API_ROUTER: &str = r#"
pub fn router(pool: PgPool) -> Router {
    Router::new()
        .route("/healthz", get(health))
        .route("@PARENTS@", get(list_parents).post(create_parent))
        .route(
            "@PARENTS@/{parent_id}",
            get(fetch_parent).post(create_batch).patch(update_parent).delete(remove_parent),
        )
        .route("@PARENTS@/{parent_id}@CHILDREN@", get(list_children).post(create_child))
        .route(
            "@PARENTS@/{parent_id}@CHILDREN@/{child_id}",
            get(fetch_child).patch(update_child).delete(remove_child),
        )
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

use crate::models::{ChildRow, ParentRow};
use crate::schemas::{ChildCreate, ChildUpdate, ParentCreate, ParentUpdate};

pub async fn ping(pool: &PgPool) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT 1").execute(pool).await.map(|_| ())
}
"#;

/// id, the foreign key if any, the fields and the timestamps, quoted.
fn selected(key: Option<&Field>, fields: &[Field]) -> String {
    let mut names = vec![quoted("id")];
    names.extend(
        key.into_iter()
            .chain(fields)
            .map(|field| quoted(&field.name)),
    );
    names.push(quoted("created_at"));
    names.push(quoted("updated_at"));
    names.join(", ")
}

/// The list conditions after the first slot, and the Rust parameters they bind.
fn list_filters(resource: &Resource, first: usize) -> (Vec<String>, String, String) {
    let mut conditions = Vec::new();
    let mut parameters = String::new();
    let mut binds = String::new();
    let mut slot = first;
    if let Some(name) = &resource.filter {
        conditions.push(format!(
            "(${slot}::boolean IS NULL OR {} = ${slot})",
            quoted(name)
        ));
        parameters += "filter: Option<bool>, ";
        binds += ".bind(filter)";
        slot += 1;
    }
    if let Some(name) = &resource.date {
        conditions.push(format!(
            "(${slot}::date IS NULL OR {} < ${slot})",
            quoted(name)
        ));
        parameters += "before: Option<chrono::NaiveDate>, ";
        binds += ".bind(before)";
    }
    (conditions, parameters, binds)
}

/// UPDATE sets with a presence flag and a value per field, starting at slot first.
fn update_sets(fields: &[Field], first: usize) -> String {
    let mut sets = Vec::new();
    let mut flags = Vec::new();
    for (index, field) in fields.iter().enumerate() {
        let column = quoted(&field.name);
        let flag = first + 2 * index;
        sets.push(format!(
            "{column} = CASE WHEN ${flag} THEN ${} ELSE {column} END",
            flag + 1
        ));
        flags.push(format!("${flag}"));
    }
    sets.push(format!(
        "\"updated_at\" = CASE WHEN {} THEN now() ELSE \"updated_at\" END",
        flags.join(" OR ")
    ));
    sets.join(", ")
}

fn field_binds(fields: &[Field], update: bool) -> String {
    fields
        .iter()
        .map(|field| {
            if update {
                format!(
                    "        .bind(body.{0}.is_some())\n        .bind(body.{0}.flatten())\n",
                    field.ident()
                )
            } else {
                format!("        .bind(body.{})\n", field.ident())
            }
        })
        .collect()
}

fn db_parent(parent: &Resource) -> String {
    let table = quoted(&parent.table);
    let columns = selected(None, &parent.fields);
    let names: Vec<String> = parent
        .fields
        .iter()
        .map(|field| quoted(&field.name))
        .collect();
    let slots: Vec<String> = (1..=names.len()).map(|slot| format!("${slot}")).collect();
    let mut out = String::from("\n");
    out += &sql_const(
        "PARENT_INSERT",
        &format!(
            "INSERT INTO {table} ({}) VALUES ({}) RETURNING {columns}",
            names.join(", "),
            slots.join(", ")
        ),
    );
    out += "/// The executor is the pool, or the transaction of a batch.\npub async fn insert_parent<'e>(executor: impl sqlx::PgExecutor<'e>, body: ParentCreate) -> Result<ParentRow, sqlx::Error> {\n    sqlx::query_as::<_, ParentRow>(PARENT_INSERT)\n";
    out += &field_binds(&parent.fields, false);
    out += "        .fetch_one(executor)\n        .await\n}\n\n";
    out += &sql_const(
        "PARENT_EXISTS",
        &format!("SELECT EXISTS (SELECT 1 FROM {table} WHERE \"id\" = $1)"),
    );
    out += "pub async fn parent_exists(pool: &PgPool, id: i32) -> Result<bool, sqlx::Error> {\n    sqlx::query_scalar::<_, bool>(PARENT_EXISTS).bind(id).fetch_one(pool).await\n}\n\n";
    out += &sql_const(
        "PARENT_FETCH",
        &format!("SELECT {columns} FROM {table} WHERE \"id\" = $1"),
    );
    out += "pub async fn fetch_parent(pool: &PgPool, id: i32) -> Result<Option<ParentRow>, sqlx::Error> {\n    sqlx::query_as::<_, ParentRow>(PARENT_FETCH).bind(id).fetch_optional(pool).await\n}\n\n";
    let (conditions, parameters, binds) = list_filters(parent, 1);
    let slot = 1 + conditions.len();
    let filters = if conditions.is_empty() {
        String::new()
    } else {
        format!(" WHERE {}", conditions.join(" AND "))
    };
    out += &sql_const(
        "PARENT_LIST",
        &format!(
            "SELECT {columns} FROM {table}{filters} ORDER BY \"id\" LIMIT ${slot} OFFSET ${}",
            slot + 1
        ),
    );
    out += &format!(
        "pub async fn list_parents(pool: &PgPool, {parameters}limit: i64, offset: i64) -> Result<Vec<ParentRow>, sqlx::Error> {{\n    sqlx::query_as::<_, ParentRow>(PARENT_LIST){binds}.bind(limit).bind(offset).fetch_all(pool).await\n}}\n\n"
    );
    out += &sql_const(
        "PARENT_UPDATE",
        &format!(
            "UPDATE {table} SET {} WHERE \"id\" = $1 RETURNING {columns}",
            update_sets(&parent.fields, 2)
        ),
    );
    out += "/// Each member binds whether it was present, then its value. Absent members keep the column.\npub async fn update_parent(pool: &PgPool, id: i32, body: ParentUpdate) -> Result<Option<ParentRow>, sqlx::Error> {\n    sqlx::query_as::<_, ParentRow>(PARENT_UPDATE)\n        .bind(id)\n";
    out += &field_binds(&parent.fields, true);
    out += "        .fetch_optional(pool)\n        .await\n}\n\n";
    out += &sql_const(
        "PARENT_DELETE",
        &format!("DELETE FROM {table} WHERE \"id\" = $1"),
    );
    out + "pub async fn delete_parent(pool: &PgPool, id: i32) -> Result<bool, sqlx::Error> {\n    let done = sqlx::query(PARENT_DELETE).bind(id).execute(pool).await?;\n    Ok(done.rows_affected() > 0)\n}\n"
}

fn db_child(child: &Resource, key: &Field) -> String {
    let table = quoted(&child.table);
    let fk = quoted(&key.name);
    let columns = selected(Some(key), &child.fields);
    let mut names = vec![fk.clone()];
    names.extend(child.fields.iter().map(|field| quoted(&field.name)));
    let slots: Vec<String> = (1..=names.len()).map(|slot| format!("${slot}")).collect();
    let scoped = format!("WHERE \"id\" = $1 AND {fk} = $2");
    let mut out = String::from("\n");
    out += &sql_const(
        "CHILD_INSERT",
        &format!(
            "INSERT INTO {table} ({}) VALUES ({}) RETURNING {columns}",
            names.join(", "),
            slots.join(", ")
        ),
    );
    out += "/// The executor is the pool, or the transaction of a batch.\npub async fn insert_child<'e>(executor: impl sqlx::PgExecutor<'e>, parent: i32, body: ChildCreate) -> Result<ChildRow, sqlx::Error> {\n    sqlx::query_as::<_, ChildRow>(CHILD_INSERT)\n        .bind(parent)\n";
    out += &field_binds(&child.fields, false);
    out += "        .fetch_one(executor)\n        .await\n}\n\n";
    out += &sql_const(
        "CHILD_FETCH",
        &format!("SELECT {columns} FROM {table} {scoped}"),
    );
    out += "pub async fn fetch_child(pool: &PgPool, id: i32, parent: i32) -> Result<Option<ChildRow>, sqlx::Error> {\n    sqlx::query_as::<_, ChildRow>(CHILD_FETCH).bind(id).bind(parent).fetch_optional(pool).await\n}\n\n";
    let (mut conditions, parameters, binds) = list_filters(child, 2);
    let slot = 2 + conditions.len();
    conditions.insert(0, format!("{fk} = $1"));
    out += &sql_const(
        "CHILD_LIST",
        &format!(
            "SELECT {columns} FROM {table} WHERE {} ORDER BY \"id\" LIMIT ${slot} OFFSET ${}",
            conditions.join(" AND "),
            slot + 1
        ),
    );
    out += &format!(
        "pub async fn list_children(pool: &PgPool, parent: i32, {parameters}limit: i64, offset: i64) -> Result<Vec<ChildRow>, sqlx::Error> {{\n    sqlx::query_as::<_, ChildRow>(CHILD_LIST).bind(parent){binds}.bind(limit).bind(offset).fetch_all(pool).await\n}}\n\n"
    );
    out += &sql_const(
        "CHILD_UPDATE",
        &format!(
            "UPDATE {table} SET {} {scoped} RETURNING {columns}",
            update_sets(&child.fields, 3)
        ),
    );
    out += "/// Each member binds whether it was present, then its value. Absent members keep the column.\npub async fn update_child(pool: &PgPool, id: i32, parent: i32, body: ChildUpdate) -> Result<Option<ChildRow>, sqlx::Error> {\n    sqlx::query_as::<_, ChildRow>(CHILD_UPDATE)\n        .bind(id)\n        .bind(parent)\n";
    out += &field_binds(&child.fields, true);
    out += "        .fetch_optional(pool)\n        .await\n}\n\n";
    out += &sql_const("CHILD_DELETE", &format!("DELETE FROM {table} {scoped}"));
    out + "pub async fn delete_child(pool: &PgPool, id: i32, parent: i32) -> Result<bool, sqlx::Error> {\n    let done = sqlx::query(CHILD_DELETE).bind(id).bind(parent).execute(pool).await?;\n    Ok(done.rows_affected() > 0)\n}\n"
}

// ---------------------------------------------------------------- migrations/0001_<revision>.sql

/// CREATE TABLE with id, the foreign key line if any, the fields and the now() timestamps.
fn create_table(table: &str, key: Option<String>, fields: &[Field]) -> String {
    let mut lines = vec!["    \"id\" SERIAL PRIMARY KEY".to_owned()];
    lines.extend(key);
    lines.extend(fields.iter().map(column_line));
    for name in ["created_at", "updated_at"] {
        lines.push(format!(
            "    \"{name}\" TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()"
        ));
    }
    format!(
        "CREATE TABLE {} (\n{}\n);\n",
        quoted(table),
        lines.join(",\n")
    )
}

/// The index name of the Python product: ix_ and 24 hex digits of sha256("table:field").
fn hashed_index(table: &str, field: &str) -> String {
    let hex = som::compiler::digest(format!("{table}:{field}").as_bytes());
    quoted(&format!("ix_{}", &hex[..24]))
}

/// One index line per query column. The child indexes lead with the foreign key.
fn query_indexes(resource: &Resource, lead: Option<&str>) -> String {
    let lead = lead.map_or(String::new(), |name| format!("{}, ", quoted(name)));
    resource
        .filter
        .iter()
        .chain(&resource.date)
        .map(|name| {
            format!(
                "\nCREATE INDEX {} ON {} ({lead}{}, \"id\");\n",
                hashed_index(&resource.table, name),
                quoted(&resource.table),
                quoted(name)
            )
        })
        .collect()
}

fn migration_parent(params: &Value, parent: &Resource) -> Result<String, String> {
    let revision = text(params, "revision")?;
    let down = params["down_revision"].as_str().unwrap_or("none");
    Ok(format!(
        "-- revision: {revision}, down_revision: {down}\n{}{}",
        create_table(&parent.table, None, &parent.fields),
        query_indexes(parent, None)
    ))
}

fn migration_child(params: &Value, child: &Resource, key: &Field) -> Result<String, String> {
    let parent_table = text(params, "parent_table")?;
    let fk = quoted(&key.name);
    let reference = format!(
        "    {fk} INTEGER NOT NULL REFERENCES {} (\"id\") ON DELETE RESTRICT",
        quoted(parent_table)
    );
    Ok(format!(
        "\n{}\nCREATE INDEX {} ON {} ({fk}, \"id\");\n{}",
        create_table(&child.table, Some(reference), &child.fields),
        quoted(&format!("ix_{}_parent_id", child.table)),
        quoted(&child.table),
        query_indexes(child, Some(&key.name))
    ))
}

// ---------------------------------------------------------------- tests/api.rs

fn tests_harness(parent: &Resource, child: &Resource, params: &Value) -> Result<String, String> {
    let key = text(params, "foreign_key")?;
    let mut out = format!(
        "{HARNESS_USES}const PARENTS: &str = \"{}\";\nconst CHILDREN: &str = \"{}\";\nconst FOREIGN_KEY: &str = \"{key}\";\nconst PARENT_ROWS: usize = {};\nconst CHILD_ROWS: usize = {};\n\n{HARNESS_TOOLS}",
        parent.path,
        child.path,
        parent.rows(),
        child.rows()
    );
    out += HARNESS_ITEMS;
    out += &sample_fns("parent", &parent.fields);
    out += &sample_fns("child", &child.fields);
    out += HARNESS_CREATE;
    out += STAMPS;
    Ok(out)
}

const HARNESS_ITEMS: &str = r#"fn parent_item(body: &Value) -> String {
    format!("{PARENTS}/{}", body["id"])
}

/// The child list path of a parent row.
fn child_list(parent: &Value) -> String {
    format!("{PARENTS}/{}{CHILDREN}", parent["id"])
}

/// The item path of a child row, under its own parent.
fn child_item(body: &Value) -> String {
    format!("{PARENTS}/{}{CHILDREN}/{}", body[FOREIGN_KEY], body["id"])
}

/// DELETE answers 204 with no body and, as in the Python product, no media type.
async fn deleted(app: &Router, uri: &str) {
    let request = Request::builder().method("DELETE").uri(uri).body(Body::empty()).expect("request");
    let response = app.clone().oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::NO_CONTENT, "{uri}");
    assert!(response.headers().get("content-type").is_none(), "{uri}");
    assert!(response.into_body().collect().await.expect("body").to_bytes().is_empty());
}

"#;

const HARNESS_CREATE: &str = r#"async fn create_parent(app: &Router, k: usize) -> Value {
    let (status, body) = send(app, "POST", PARENTS, Some(parent_sample(k))).await;
    assert_eq!(status, StatusCode::CREATED, "{body}");
    body
}

async fn create_child(app: &Router, parent: &Value, k: usize) -> Value {
    let (status, body) = send(app, "POST", &child_list(parent), Some(child_sample(k))).await;
    assert_eq!(status, StatusCode::CREATED, "{body}");
    assert_eq!(body[FOREIGN_KEY], parent["id"]);
    body
}

"#;

const TESTS_CRUD: &str = r#"#[tokio::test]
async fn parent_crud_round_trip() {
    let app = app().await;
    let created = create_parent(&app, 0).await;
    let mut wanted = parent_sample(0);
    wanted["id"] = created["id"].clone();
    assert_eq!(fields(&created), wanted);
    let (first, _) = stamps(&created);
    let uri = parent_item(&created);
    let (status, body) = send(&app, "GET", &uri, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, created);
    let (status, body) = send(&app, "GET", PARENTS, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!([created]));
    let mut last = created.clone();
    for (name, value) in parent_sample(1).as_object().expect("sample object") {
        let mut patch = serde_json::Map::new();
        patch.insert(name.clone(), value.clone());
        let (status, body) = send(&app, "PATCH", &uri, Some(Value::Object(patch))).await;
        assert_eq!(status, StatusCode::OK, "{body}");
        wanted[name.as_str()] = value.clone();
        assert_eq!(fields(&body), wanted);
        let (created_at, _) = stamps(&body);
        assert_eq!(created_at, first);
        last = body;
    }
    let (status, body) = send(&app, "PATCH", &uri, Some(json!({}))).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, last);
    deleted(&app, &uri).await;
    for (method, payload) in [("GET", None), ("DELETE", None), ("PATCH", Some(json!({})))] {
        let (status, body) = send(&app, method, &uri, payload).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{method}");
        assert_eq!(body, json!({"detail": "parent not found"}));
    }
    let (status, body) = send(&app, "POST", PARENTS, Some(parent_minimal(2))).await;
    assert_eq!(status, StatusCode::CREATED, "{body}");
    let mut wanted = parent_defaults(2);
    wanted["id"] = body["id"].clone();
    assert_eq!(fields(&body), wanted);
    stamps(&body);
}

#[tokio::test]
async fn child_crud_round_trip() {
    let app = app().await;
    let parent = create_parent(&app, 0).await;
    let created = create_child(&app, &parent, 0).await;
    let mut wanted = child_sample(0);
    wanted["id"] = created["id"].clone();
    wanted[FOREIGN_KEY] = parent["id"].clone();
    assert_eq!(fields(&created), wanted);
    let (first, _) = stamps(&created);
    let uri = child_item(&created);
    let (status, body) = send(&app, "GET", &uri, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, created);
    let (status, body) = send(&app, "GET", &child_list(&parent), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!([created]));
    let mut last = created.clone();
    for (name, value) in child_sample(1).as_object().expect("sample object") {
        let mut patch = serde_json::Map::new();
        patch.insert(name.clone(), value.clone());
        let (status, body) = send(&app, "PATCH", &uri, Some(Value::Object(patch))).await;
        assert_eq!(status, StatusCode::OK, "{body}");
        wanted[name.as_str()] = value.clone();
        assert_eq!(fields(&body), wanted);
        let (created_at, _) = stamps(&body);
        assert_eq!(created_at, first);
        last = body;
    }
    let (status, body) = send(&app, "PATCH", &uri, Some(json!({}))).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, last);
    deleted(&app, &uri).await;
    for (method, payload) in [("GET", None), ("DELETE", None), ("PATCH", Some(json!({})))] {
        let (status, body) = send(&app, method, &uri, payload).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{method}");
        assert_eq!(body, json!({"detail": "child not found"}));
    }
    let (status, body) = send(&app, "POST", &child_list(&parent), Some(child_minimal(2))).await;
    assert_eq!(status, StatusCode::CREATED, "{body}");
    let mut wanted = child_defaults(2);
    wanted["id"] = body["id"].clone();
    wanted[FOREIGN_KEY] = parent["id"].clone();
    assert_eq!(fields(&body), wanted);
    stamps(&body);
}
"#;

const VALIDATION_HELPERS: &str = r#"
async fn refused(app: &Router, method: &str, uri: &str, body: Value) {
    let (status, reply) = send(app, method, uri, Some(body)).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{method} {uri}: {reply}");
    assert_eq!(locs(&reply), ["body"]);
}

/// POST a row to a list path, check it, and delete it again.
async fn accepted(app: &Router, uri: &str, body: Value, item: fn(&Value) -> String) -> Value {
    let (status, reply) = send(app, "POST", uri, Some(body)).await;
    assert_eq!(status, StatusCode::CREATED, "{uri}: {reply}");
    deleted(app, &item(&reply)).await;
    reply
}

fn with(mut body: Value, name: &str, value: Value) -> Value {
    body[name] = value;
    body
}

/// Bodies that are not a JSON object, or not JSON, on a POST route.
async fn shapes_refused(app: &Router, uri: &str, sample: Value) {
    let (status, body) = send(app, "POST", uri, None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{uri}");
    assert_eq!(locs(&body), ["body"]);
    for (content_type, text) in [
        ("application/json", "{".to_owned()),
        ("application/json", "[]".to_owned()),
        ("application/json", "1".to_owned()),
        ("text/plain", sample.to_string()),
    ] {
        let (status, body) = send_raw(app, "POST", uri, content_type, &text).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{uri} {content_type}");
        assert_eq!(locs(&body), ["body"]);
    }
    let row: Vec<Value> = sample.as_object().expect("sample object").values().cloned().collect();
    refused(app, "POST", uri, Value::Array(row)).await;
}

/// Patches that are refused with 422 body.
async fn patches_refused(app: &Router, uri: &str, patches: &[Value]) {
    for text in ["[]", "1", "null"] {
        let (status, body) = send_raw(app, "PATCH", uri, "application/json", text).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{text}");
        assert_eq!(locs(&body), ["body"]);
    }
    for patch in patches {
        refused(app, "PATCH", uri, patch.clone()).await;
    }
}
"#;

/// The body refusals of one side on its POST route, with the D1 and D2 markers.
fn body_checks(role: &str, uri: &str, item: &str, fields: &[Field]) -> String {
    let sample = format!("{role}_sample(0)");
    let refused = |name: &str, value: &str| {
        format!("    refused(&app, \"POST\", {uri}, with({sample}, \"{name}\", {value})).await;\n")
    };
    let accepted = |name: &str, value: &str| {
        format!("accepted(&app, {uri}, with({sample}, \"{name}\", {value}), {item}).await")
    };
    let mut out = format!(
        "    accepted(&app, {uri}, {sample}, {item}).await;\n    shapes_refused(&app, {uri}, {sample}).await;\n"
    );
    out += &refused(&fields[0].name, "json!({})");
    if let Some(field) = fields.iter().find(|field| field.required) {
        out += &format!(
            "    let mut body = {sample};\n    body.as_object_mut().expect(\"object\").remove(\"{}\");\n    refused(&app, \"POST\", {uri}, body).await;\n",
            field.name
        );
    }
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Int) {
        out += "    // D1 and D2: no lax coercion, and a signed 32-bit range.\n";
        out += &refused(&field.name, "json!(\"1\")");
        out += &refused(&field.name, "json!(2_147_483_648_i64)");
        out += &refused(&field.name, "json!(-2_147_483_649_i64)");
        out += &format!(
            "    {};\n",
            accepted(&field.name, "json!(-2_147_483_648_i64)")
        );
    }
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Bool) {
        out += "    // D1: no lax coercion for a bool.\n";
        out += &refused(&field.name, "json!(\"true\")");
        out += &refused(&field.name, "json!(1)");
    }
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Str) {
        out += "    // D2: at most 255 Unicode scalars.\n";
        out += &refused(&field.name, "json!(\"x\".repeat(256))");
        out += &refused(&field.name, "json!(5)");
        out += &format!(
            "    let reply = {};\n    assert_eq!(reply[\"{}\"], json!(\"\\u{{e9}}\".repeat(255)));\n",
            accepted(&field.name, "json!(\"\\u{e9}\".repeat(255))"),
            field.name
        );
    }
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Float) {
        out += &format!(
            "    // A float field accepts a JSON integer and answers a float.\n    let reply = {};\n    assert_eq!(reply[\"{}\"], json!(3.0));\n    // D1: no lax coercion for a float.\n",
            accepted(&field.name, "json!(3)"),
            field.name
        );
        out += &refused(&field.name, "json!(\"3.5\")");
    }
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Date) {
        out += &refused(&field.name, "json!(\"2030-02-30\")");
        out += "    // D1: no lax coercion for a date.\n";
        out += &refused(&field.name, "json!(20300101)");
    }
    out + &refused("zz_unknown", "json!(1)")
}

/// The patch refusals of one side, and a null patch of a nullable field.
fn patch_checks(fields: &[Field]) -> String {
    let mut patches = vec![format!("json!({{\"{}\": {{}}}})", fields[0].name)];
    if let Some(field) = fields.iter().find(|field| field.kind == Kind::Str) {
        patches.push(format!("json!({{\"{}\": \"x\".repeat(256)}})", field.name));
    }
    patches.push("json!({\"zz_unknown\": 1})".to_owned());
    if let Some(field) = fields.iter().find(|field| !field.nullable()) {
        patches.push(format!("json!({{\"{}\": null}})", field.name));
    }
    let mut out = format!(
        "    patches_refused(&app, &uri, &[{}]).await;\n",
        patches.join(", ")
    );
    if let Some(field) = fields.iter().find(|field| field.nullable()) {
        out += &format!(
            "    let (status, body) = send(&app, \"PATCH\", &uri, Some(json!({{\"{0}\": null}}))).await;\n    assert_eq!(status, StatusCode::OK, \"{{body}}\");\n    assert_eq!(body[\"{0}\"], Value::Null);\n",
            field.name
        );
    }
    out
}

fn tests_validation(parent: &Resource, child: &Resource) -> String {
    let mut out = String::from(VALIDATION_HELPERS);
    out +=
        "\n#[tokio::test]\nasync fn parent_validation_refusals() {\n    let app = app().await;\n";
    out += &body_checks("parent", "PARENTS", "parent_item", &parent.fields);
    out += r#"    for (method, uri) in [
        ("GET", format!("{PARENTS}/abc")),
        ("DELETE", format!("{PARENTS}/abc")),
        ("GET", format!("{PARENTS}/1.5")),
        ("GET", format!("{PARENTS}/with-children")),
        ("DELETE", format!("{PARENTS}/with-children")),
    ] {
        let (status, body) = send(&app, method, &uri, None).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{method} {uri}");
        assert_eq!(locs(&body), ["path"]);
    }
    // D3: an id outside signed 32 bits has no row.
    for uri in [format!("{PARENTS}/99999999999"), format!("{PARENTS}/-99999999999")] {
        let (status, body) = send(&app, "GET", &uri, None).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{uri}");
        assert_eq!(body, json!({"detail": "parent not found"}));
    }
    let (status, body) = send_raw(&app, "PATCH", &format!("{PARENTS}/abc"), "text/plain", "{}").await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(locs(&body), ["body", "path"]);
    let created = create_parent(&app, 0).await;
    let uri = parent_item(&created);
"#;
    out += &patch_checks(&parent.fields);
    out += "}\n\n#[tokio::test]\nasync fn child_validation_refusals() {\n    let app = app().await;\n    let parent = create_parent(&app, 0).await;\n    let list = child_list(&parent);\n";
    out += &body_checks("child", "&list", "child_item", &child.fields);
    out += r#"    for (method, uri) in [
        ("GET", format!("{list}/abc")),
        ("DELETE", format!("{list}/abc")),
        ("GET", format!("{list}/1.5")),
        ("GET", format!("{PARENTS}/abc{CHILDREN}")),
        ("GET", format!("{PARENTS}/abc{CHILDREN}/1")),
    ] {
        let (status, body) = send(&app, method, &uri, None).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{method} {uri}");
        assert_eq!(locs(&body), ["path"]);
    }
    let (status, body) = send(&app, "POST", &format!("{PARENTS}/abc{CHILDREN}"), Some(child_sample(0))).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(locs(&body), ["path"]);
    // D3: an id outside signed 32 bits has no row.
    for (uri, detail) in [
        (format!("{PARENTS}/99999999999{CHILDREN}"), "parent not found"),
        (format!("{PARENTS}/99999999999{CHILDREN}/1"), "parent not found"),
        (format!("{list}/99999999999"), "child not found"),
    ] {
        let (status, body) = send(&app, "GET", &uri, None).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{uri}");
        assert_eq!(body, json!({"detail": detail}));
    }
    let (status, body) = send_raw(&app, "PATCH", &format!("{list}/abc"), "text/plain", "{}").await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(locs(&body), ["body", "path"]);
    let created = create_child(&app, &parent, 0).await;
    let uri = child_item(&created);
"#;
    out += &patch_checks(&child.fields);
    out + "}\n"
}

fn tests_unique(parent: &Resource, child: &Resource) -> String {
    let names = |fields: &[Field]| {
        fields
            .iter()
            .filter(|field| field.unique)
            .map(|field| format!("\"{}\"", field.name))
            .collect::<Vec<_>>()
            .join(", ")
    };
    let (parent_names, child_names) = (names(&parent.fields), names(&child.fields));
    let mut out = String::new();
    if !parent_names.is_empty() || !child_names.is_empty() {
        out += r#"
/// POST bodies that copy a unique value answer 409, and so do patches.
async fn conflicts(app: &Router, uri: &str, body: &Value, taken: &Value, item: &str, names: &[&str]) {
    for name in names {
        let mut copy = body.clone();
        copy[*name] = taken[*name].clone();
        let (status, reply) = send(app, "POST", uri, Some(copy)).await;
        assert_eq!(status, StatusCode::CONFLICT, "{name}");
        assert_eq!(reply, json!({"detail": "relationship or unique value conflict"}));
        let mut patch = serde_json::Map::new();
        patch.insert((*name).to_owned(), taken[*name].clone());
        let (status, reply) = send(app, "PATCH", item, Some(Value::Object(patch))).await;
        assert_eq!(status, StatusCode::CONFLICT, "{name}");
        assert_eq!(reply, json!({"detail": "relationship or unique value conflict"}));
    }
}
"#;
    }
    if parent_names.is_empty() {
        out += r#"
#[tokio::test]
async fn duplicate_parent_values_are_allowed() {
    let app = app().await;
    let first = create_parent(&app, 0).await;
    let second = create_parent(&app, 0).await;
    assert_ne!(first["id"], second["id"]);
    let mut wanted = second.clone();
    wanted["id"] = first["id"].clone();
    assert_eq!(fields(&first), fields(&wanted));
}
"#;
    } else {
        out += &format!(
            r#"
#[tokio::test]
async fn parent_unique_conflicts() {{
    let app = app().await;
    let first = create_parent(&app, 0).await;
    let second = create_parent(&app, 1).await;
    conflicts(&app, PARENTS, &parent_sample(2), &first, &parent_item(&second), &[{parent_names}]).await;
    let (status, body) = send(&app, "GET", &parent_item(&second), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, second);
    let (status, body) = send(&app, "GET", PARENTS, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!([first, second]));
}}
"#
        );
    }
    if child_names.is_empty() {
        out += r#"
#[tokio::test]
async fn duplicate_child_values_are_allowed() {
    let app = app().await;
    let parent = create_parent(&app, 0).await;
    let first = create_child(&app, &parent, 0).await;
    let second = create_child(&app, &parent, 0).await;
    assert_ne!(first["id"], second["id"]);
    let mut wanted = second.clone();
    wanted["id"] = first["id"].clone();
    assert_eq!(fields(&first), fields(&wanted));
}
"#;
    } else {
        out += &format!(
            r#"
/// A unique child value is unique across parents.
#[tokio::test]
async fn child_unique_conflicts() {{
    let app = app().await;
    let parent = create_parent(&app, 0).await;
    let other = create_parent(&app, 1).await;
    let first = create_child(&app, &parent, 0).await;
    let second = create_child(&app, &other, 1).await;
    conflicts(&app, &child_list(&other), &child_sample(2), &first, &child_item(&second), &[{child_names}]).await;
    let (status, body) = send(&app, "GET", &child_item(&second), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, second);
    let (status, body) = send(&app, "GET", &child_list(&other), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!([second]));
}}
"#
        );
    }
    out
}

/// The list checks of one side after its rows exist, against the list path in `base`.
fn list_checks(resource: &Resource, rows: &str) -> String {
    let max = resource.max_limit;
    let mut out = format!(
        r#"    let (status, body) = send(&app, "GET", base, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, page(&rows, 0, 20));
    assert_eq!(listed(&app, base, "limit=5&offset=1").await, page(&rows, 1, 5));
    assert_eq!(listed(&app, base, "limit={max}").await, page(&rows, 0, {max}));
    assert_eq!(listed(&app, base, "limit=1&offset=0").await, page(&rows, 0, 1));
    assert_eq!(listed(&app, base, &format!("offset={{}}", {rows} + 5)).await, json!([]));
    assert_eq!(listed(&app, base, "zz_unknown=1").await, page(&rows, 0, 20));
    for query in ["limit=0", "limit={over}", "offset=-1", "limit=abc", "offset=1.5"] {{
        rejected(&app, base, query).await;
    }}
    // D4: a repeated query parameter is refused.
    let (status, body) = send(&app, "GET", &format!("{{base}}?limit=1&limit=2"), None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(locs(&body), ["query"]);
"#,
        over = max + 1
    );
    if let Some(name) = &resource.filter {
        out += &format!(
            r#"    for (text, value) in [("true", true), ("yes", true), ("1", true), ("false", false), ("Off", false), ("0", false)] {{
        let found = matching(&rows, |row| row["{name}"] == value);
        assert_eq!(listed(&app, base, &format!("{name}={{text}}")).await, page(&found, 0, 20), "{{text}}");
    }}
    rejected(&app, base, "{name}=maybe").await;
    rejected(&app, base, "{name}=true&{name}=false").await;
"#
        );
    }
    if let Some(date) = &resource.date {
        out += &format!(
            r#"    let found = matching(&rows, |row| row["{date}"].as_str().is_some_and(|day| day < "2030-01-10"));
    assert_eq!(listed(&app, base, "before=2030-01-10").await, page(&found, 0, 20));
    for query in ["before=2030-13-01", "before=tomorrow", "before=2030-1-1", "before=2030-01-01&before=2030-01-02"] {{
        rejected(&app, base, query).await;
    }}
"#
        );
        if let Some(name) = &resource.filter {
            out += &format!(
                r#"    let found = matching(&rows, |row| row["{name}"] == true && row["{date}"].as_str().is_some_and(|day| day < "2030-01-20"));
    assert_eq!(listed(&app, base, "{name}=true&before=2030-01-20").await, page(&found, 0, 20));
"#
            );
        }
    }
    out
}

fn tests_list(parent: &Resource, child: &Resource) -> String {
    let mut out = String::from(
        r#"
fn page(rows: &[Value], offset: usize, limit: usize) -> Value {
    Value::Array(rows.iter().skip(offset).take(limit).cloned().collect())
}

async fn listed(app: &Router, base: &str, query: &str) -> Value {
    let (status, body) = send(app, "GET", &format!("{base}?{query}"), None).await;
    assert_eq!(status, StatusCode::OK, "{base}?{query}: {body}");
    body
}

async fn rejected(app: &Router, base: &str, query: &str) {
    let (status, body) = send(app, "GET", &format!("{base}?{query}"), None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{base}?{query}: {body}");
    assert_eq!(locs(&body), ["query"]);
}
"#,
    );
    let queried = |resource: &Resource| resource.filter.is_some() || resource.date.is_some();
    if queried(parent) || queried(child) {
        out += r#"
fn matching(rows: &[Value], keep: impl Fn(&Value) -> bool) -> Vec<Value> {
    rows.iter().filter(|row| keep(row)).cloned().collect()
}
"#;
    }
    out += r#"
#[tokio::test]
async fn parent_list_queries() {
    let app = app().await;
    let mut rows = Vec::new();
    for k in 0..PARENT_ROWS {
        rows.push(create_parent(&app, k).await);
    }
    let base = PARENTS;
"#;
    out += &list_checks(parent, "PARENT_ROWS");
    out += r#"}

#[tokio::test]
async fn child_list_queries() {
    let app = app().await;
    let parent = create_parent(&app, 0).await;
    let list = child_list(&parent);
    let mut rows = Vec::new();
    for k in 0..CHILD_ROWS {
        rows.push(create_child(&app, &parent, k).await);
    }
    let base: &str = &list;
"#;
    out += &list_checks(child, "CHILD_ROWS");
    out + r#"    for query in ["limit=0", "limit=1&limit=2"] {
        let (status, body) = send(&app, "GET", &format!("{PARENTS}/2147483000{CHILDREN}?{query}"), None).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{query}");
        assert_eq!(locs(&body), ["query"]);
    }
}
"#
}

const TESTS_SCOPE: &str = r#"
#[tokio::test]
async fn children_are_scoped_to_their_parent() {
    let app = app().await;
    let parent = create_parent(&app, 0).await;
    let other = create_parent(&app, 1).await;
    let child = create_child(&app, &parent, 0).await;
    let stranger = create_child(&app, &other, 1).await;
    for (owner, rows) in [(&parent, json!([child])), (&other, json!([stranger]))] {
        let (status, body) = send(&app, "GET", &child_list(owner), None).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body, rows);
    }
    let crossed = format!("{PARENTS}/{}{CHILDREN}/{}", parent["id"], stranger["id"]);
    for (method, payload) in [("GET", None), ("PATCH", Some(json!({}))), ("DELETE", None)] {
        let (status, body) = send(&app, method, &crossed, payload).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{method}");
        assert_eq!(body, json!({"detail": "child not found"}));
    }
    let gone = format!("{PARENTS}/2147483000{CHILDREN}");
    let requests = [
        ("GET", gone.clone(), None),
        ("POST", gone.clone(), Some(child_sample(2))),
        ("GET", format!("{gone}/{}", child["id"]), None),
        ("PATCH", format!("{gone}/{}", child["id"]), Some(json!({}))),
        ("DELETE", format!("{gone}/{}", child["id"]), None),
    ];
    for (method, uri, payload) in requests {
        let (status, body) = send(&app, method, &uri, payload).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{method} {uri}");
        assert_eq!(body, json!({"detail": "parent not found"}));
    }
    for (row, uri) in [(&child, child_item(&child)), (&stranger, child_item(&stranger))] {
        let (status, body) = send(&app, "GET", &uri, None).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(&body, row);
    }
    let (status, body) = send(&app, "GET", &child_list(&parent), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!([child]));
}

#[tokio::test]
async fn parent_delete_is_restricted() {
    let app = app().await;
    let parent = create_parent(&app, 0).await;
    let child = create_child(&app, &parent, 0).await;
    let (status, body) = send(&app, "DELETE", &parent_item(&parent), None).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body, json!({"detail": "relationship or unique value conflict"}));
    for (row, uri) in [(&parent, parent_item(&parent)), (&child, child_item(&child))] {
        let (status, body) = send(&app, "GET", &uri, None).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(&body, row);
    }
    deleted(&app, &child_item(&child)).await;
    deleted(&app, &parent_item(&parent)).await;
    let (status, body) = send(&app, "GET", PARENTS, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!([]));
}
"#;

fn tests_batch(parent: &Resource, child: &Resource) -> String {
    // Only two children can exist when a child field is a unique NOT NULL bool.
    let largest = if child.rows() < 20 { 1 } else { 20 };
    let mut out = format!(
        r#"
/// A batch body: parent row k and size children from child row k on.
fn batch(k: usize, size: usize) -> Value {{
    json!({{"parent": parent_sample(k), "children": (k..k + size).map(child_sample).collect::<Vec<_>>()}})
}}

fn batch_path() -> String {{
    format!("{{PARENTS}}/with-children")
}}

#[tokio::test]
async fn batch_creates_a_parent_with_children() {{
    let app = app().await;
    for (k, size) in [(0, 1), (1, {largest})] {{
        let (status, body) = send(&app, "POST", &batch_path(), Some(batch(k, size))).await;
        assert_eq!(status, StatusCode::CREATED, "{{body}}");
        let parent = &body["parent"];
        let mut wanted = parent_sample(k);
        wanted["id"] = parent["id"].clone();
        assert_eq!(fields(parent), wanted);
        stamps(parent);
        let children = body["children"].as_array().expect("children");
        assert_eq!(children.len(), size);
        for (index, child) in children.iter().enumerate() {{
            let mut wanted = child_sample(k + index);
            wanted["id"] = child["id"].clone();
            wanted[FOREIGN_KEY] = parent["id"].clone();
            assert_eq!(fields(child), wanted);
            stamps(child);
        }}
        let (status, listed) = send(&app, "GET", &child_list(parent), None).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(listed, Value::Array(children.clone()));
        let (status, found) = send(&app, "GET", &parent_item(parent), None).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(&found, parent);
    }}
}}

#[tokio::test]
async fn batch_refusals() {{
    let app = app().await;
    let mut empty = batch(9, 1);
    empty["children"] = json!([]);
    let mut over = batch(9, 1);
    over["children"] = Value::Array((10..31).map(child_sample).collect());
    let mut missing = batch(9, 1);
    missing.as_object_mut().expect("object").remove("children");
    let mut unknown = batch(9, 1);
    unknown["zz_unknown"] = json!(1);
    let mut stray = batch(9, 2);
    stray["children"][1]["zz_unknown"] = json!(1);
    for body in [empty, over, missing, unknown, stray, json!({{"children": [child_sample(9)]}})] {{
        let (status, reply) = send(&app, "POST", &batch_path(), Some(body)).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{{reply}}");
        assert_eq!(locs(&reply), ["body"]);
    }}
    for text in ["{{", "[]", "1"] {{
        let (status, reply) = send_raw(&app, "POST", &batch_path(), "application/json", text).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{{text}}");
        assert_eq!(locs(&reply), ["body"]);
    }}
    let (status, body) = send(&app, "GET", PARENTS, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!([]));
}}
"#
    );
    let (parent_unique, child_unique) = (parent.unique_value(), child.unique_value());
    if parent_unique.is_none() && child_unique.is_none() {
        return out;
    }
    out += "\n/// A batch that fails on a unique value adds no parent and no child. Rows 1 and 2 keep\n/// clear of row 0 even when a unique NOT NULL bool allows only two rows.\n#[tokio::test]\nasync fn batch_is_atomic() {\n    let app = app().await;\n";
    let mut bodies = Vec::new();
    if let Some(field) = parent_unique {
        out += &format!(
            "    let taken = create_parent(&app, 0).await;\n    let mut copied = batch(1, 1);\n    copied[\"parent\"][\"{0}\"] = taken[\"{0}\"].clone();\n",
            field.name
        );
        bodies.push("copied");
    }
    if let Some(field) = child_unique {
        out += &format!(
            "    let mut twice = batch(1, 2);\n    twice[\"children\"][1][\"{0}\"] = twice[\"children\"][0][\"{0}\"].clone();\n",
            field.name
        );
        bodies.push("twice");
    }
    out += &format!("    let bodies = [{}];\n", bodies.join(", "));
    out + r#"    let (_, before) = send(&app, "GET", PARENTS, None).await;
    for body in bodies {
        let (status, reply) = send(&app, "POST", &batch_path(), Some(body)).await;
        assert_eq!(status, StatusCode::CONFLICT, "{reply}");
        assert_eq!(reply, json!({"detail": "relationship or unique value conflict"}));
        let (status, after) = send(&app, "GET", PARENTS, None).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(after, before);
    }
    // Every value of the refused batches is free again.
    let (status, body) = send(&app, "POST", &batch_path(), Some(batch(1, 2))).await;
    assert_eq!(status, StatusCode::CREATED, "{body}");
}
"#
}

const TESTS_FALLBACK: &str = r#"
#[tokio::test]
async fn fallback_routes() {
    let app = app().await;
    // D5: a trailing slash is not a route.
    for uri in [
        "/_missing".to_owned(),
        format!("{PARENTS}/"),
        format!("{PARENTS}/1{CHILDREN}/"),
        format!("{PARENTS}/1{CHILDREN}/1/x"),
    ] {
        let (status, body) = send(&app, "GET", &uri, None).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{uri}");
        assert_eq!(body, json!({"detail": "Not Found"}));
    }
    for (method, uri) in [
        ("PUT", PARENTS.to_owned()),
        ("DELETE", PARENTS.to_owned()),
        ("POST", format!("{PARENTS}/1")),
        ("POST", format!("{PARENTS}/abc")),
        ("PUT", format!("{PARENTS}/1{CHILDREN}")),
        ("POST", format!("{PARENTS}/1{CHILDREN}/1")),
    ] {
        let (status, body) = send(&app, method, &uri, None).await;
        assert_eq!(status, StatusCode::METHOD_NOT_ALLOWED, "{method} {uri}");
        assert_eq!(body, json!({"detail": "Method Not Allowed"}));
    }
}
"#;

// ---------------------------------------------------------------- README.md

fn query_text(resource: &Resource) -> String {
    let mut out = String::from("Query: ");
    if let Some(name) = &resource.filter {
        out += &format!("`{name}` (boolean), ");
    }
    if let Some(name) = &resource.date {
        out += &format!("`before` (a date; rows whose `{name}` is earlier), ");
    }
    out + &format!(
        "`limit` (1 to {}, default 20) and `offset` (default 0). Rows in id order.",
        resource.max_limit
    )
}

fn readme(params: &Value, parent: &Resource, child: &Resource) -> Result<String, String> {
    let package = text(params, "package")?;
    let version = text(params, "version")?;
    let key = text(params, "foreign_key")?;
    let revision = text(params, "revision")?;
    let down = params["down_revision"].as_str().unwrap_or("none");
    let (parents, children) = (&parent.path, &child.path);
    let item = format!("{parents}/{{parent_id}}");
    let list = format!("{item}{children}");
    Ok(format!(
        r#"# {package}

A Rust service generated by SOM from a `{version}` prompt for the package `{package}`.
The Cargo package, library and binary are `som_app`. It uses axum, sqlx and tokio on PostgreSQL.

Table `{}` holds the parents and table `{}` the children. A child row belongs to one parent
through `{key}`, which is `ON DELETE RESTRICT`.

## Routes

- `GET /healthz`: `{{"status": "ok"}}`, or 503 when the database is unavailable.
- `GET {parents}`: list parents. {}
- `POST {parents}`: create a parent. 201, or 409 for a unique value conflict.
- `GET {item}`: get a parent. 404 `parent not found` if it does not exist.
- `PATCH {item}`: update the supplied fields of a parent.
- `DELETE {item}`: delete a parent. 204, or 409 while it has children.
- `GET {list}`: list the children of a parent. {}
- `POST {list}`: create a child of a parent.
- `GET {list}/{{child_id}}`: get a child. 404 `child not found` if it does not exist under this parent.
- `PATCH {list}/{{child_id}}`: update the supplied fields of a child.
- `DELETE {list}/{{child_id}}`: delete a child. 204.
- `POST {parents}/with-children`: create a parent and 1 to 20 children in one transaction.
  A failing child leaves no parent and no child.

Every child route answers 404 `parent not found` when the parent does not exist. A unique value
or foreign key conflict answers 409 `relationship or unique value conflict`. Invalid input returns
422 with `{{"detail": [{{"type", "loc", "msg"}}]}}`.

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

`migrations/0001_{revision}.sql` holds revision `{revision}` (down revision `{down}`). It creates
both tables, the foreign key and the list indexes. The service runs it at startup through
`som_app::MIGRATOR`.
"#,
        parent.table,
        child.table,
        query_text(parent),
        query_text(child)
    ))
}
