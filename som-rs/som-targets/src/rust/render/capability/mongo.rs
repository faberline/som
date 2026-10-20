//! The capability_mongodb blocks (issue #53 H1 and H2): the capability product of capability.rs
//! on MongoDB. The product keeps the routes, schemas, auth providers, object storage and workflows
//! of the PostgreSQL product and replaces its database: ids from `_som_counters`, every write in one transaction,
//! and the indexes that the Python product's initialize() creates, which `serve` and
//! `migrate upgrade` both create.
use serde_json::Value;

use super::super::{Field, Kind, related::Resource, text, with_key};
use super::{
    API_MISSING_PARENT, Auth, Flow, LIB, LOCK, MANIFEST, Product, Storage, Workflow, auth, storage,
};

/// Render one capability_mongodb block from its resolved params.
pub(super) fn render(id: &str, params: &Value) -> Result<String, String> {
    let product = || Product::read(params);
    Ok(match id {
        "rust.cargo.capability_mongodb.manifest" => MANIFEST.to_owned(),
        "rust.cargo.capability_mongodb.lock" => LOCK.to_owned(),
        "rust.main.capability_mongodb" => storage::main(Storage::read(params)?, MAIN.to_owned()),
        "rust.lib.capability_mongodb" => lib()?,
        "rust.models.capability_mongodb" => models(&product()?),
        "rust.schemas.capability_mongodb" => super::schemas(&product()?),
        "rust.api.capability_mongodb" => api(super::api(params, &product()?)?)?,
        "rust.db.capability_mongodb" => db(&product()?),
        "rust.integrations.capability_mongodb" => {
            let v5 = params["version"] == "som.prompt.v5";
            let auth = Auth::read(params)?;
            let text = match auth {
                Auth::Jwt => super::integrations(v5),
                other => auth::integrations(other, v5, text(params, "package")?),
            };
            integrations(storage::integrations(Storage::read(params)?, auth, text))?
        }
        "rust.tests.capability_mongodb" => {
            let storage = Storage::read(params)?;
            let tests = super::tests(Flow::read(params)?, storage, &product()?);
            // The identity test of a provider calls app() too; the object store joins the
            // Mongo state.
            storage::tests(
                storage,
                tests_on_mongo(auth::tests(Auth::read(params)?, tests))?,
            )
        }
        "rust.readme.capability_mongodb" => {
            let auth = Auth::read(params)?;
            let readme = readme(params, &product()?)?;
            let readme = auth::readme(auth, text(params, "package")?, readme);
            database(
                storage::readme(Storage::read(params)?, auth, readme),
                &product()?,
            )?
        }
        _ => return Err(format!("no renderer for block {id}")),
    })
}

/// Replace `old` with `new` in `text`, which must hold `old` exactly `count` times.
fn swap(text: &str, old: &str, new: &str, count: usize) -> Result<String, String> {
    let found = text.matches(old).count();
    if found != count {
        return Err(format!(
            "capability_mongodb: expected {count} of {old:?}, found {found}"
        ));
    }
    Ok(text.replace(old, new))
}

/// Replace the text from `start` through the first `end` after it.
fn swap_span(text: &str, start: &str, end: &str, new: &str) -> Result<String, String> {
    let missing = || format!("capability_mongodb: no span from {start:?} to {end:?}");
    let from = text.find(start).ok_or_else(missing)?;
    let to = from + text[from..].find(end).ok_or_else(missing)? + end.len();
    Ok(format!("{}{new}{}", &text[..from], &text[to..]))
}

// ---------------------------------------------------------------- src/main.rs, src/lib.rs

const MAIN: &str = r#"use std::process::ExitCode;
use std::sync::Arc;

use som_app::db::Store;
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

/// serve prepares the database first, as the Python lifespan does: a ping, a replica set or
/// mongos for transactions, and the indexes.
async fn serve() -> Result<(), String> {
    let settings = Settings::load()?;
    let address = std::env::var("SOM_APP_ADDR")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "127.0.0.1:8000".to_owned());
    let store = Store::open(&settings.database).await?;
    store.upgrade().await?;
    let state = som_app::AppState {
        store: store.clone(),
        auth: Arc::new(settings.auth),
    };
    let listener = tokio::net::TcpListener::bind(&address)
        .await
        .map_err(|error| format!("{address}: {error}"))?;
    let served = axum::serve(listener, som_app::router(state))
        .await
        .map_err(|error| error.to_string());
    store.close().await;
    served
}

/// Migrations read MONGODB_URL and MONGODB_DATABASE alone.
async fn migrate(direction: &str) -> Result<(), String> {
    let store = Store::open(&database()?).await?;
    let done = if direction == "upgrade" {
        store.upgrade().await
    } else {
        store.downgrade().await
    };
    store.close().await;
    done
}
"#;

/// The PostgreSQL lib.rs without its migration: MongoDB has no migration files.
fn lib() -> Result<String, String> {
    let end = "pub use api::{AppState, router};\n";
    let at = LIB
        .find(end)
        .ok_or("capability_mongodb: lib.rs has no router export")?;
    Ok(LIB[..at + end.len()].to_owned())
}

// ---------------------------------------------------------------- src/models.rs

/// The PostgreSQL rows without sqlx: the store reads them from documents.
fn models(product: &Product) -> String {
    super::models(product)
        .lines()
        .filter(|line| !line.trim_start().starts_with("#[sqlx("))
        .map(|line| {
            if line == "#[derive(Debug, sqlx::FromRow)]" {
                "#[derive(Debug)]\n".to_owned()
            } else {
                format!("{line}\n")
            }
        })
        .collect()
}

// ---------------------------------------------------------------- src/api.rs

const API_FROM_SQLX: &str = r#"/// A foreign key (23503) or unique (23505) violation is a conflict. A parent delete that its
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

const API_FROM_STORE: &str = r#"/// The answers of the store: a duplicate key is a conflict, and a write that MongoDB cannot
/// complete is 503.
impl From<db::Error> for ApiError {
    fn from(error: db::Error) -> Self {
        match error {
            db::Error::ParentMissing => Self::ParentMissing,
            db::Error::HasChildren => Self::HasChildren,
            db::Error::Conflict => Self::Conflict,
            db::Error::Unavailable => Self::Unavailable,
            db::Error::Internal => Self::Internal,
        }
    }
}
"#;

const API_BATCH_SQL: &str = r#"    let mut transaction = state.pool.begin().await?;
    let parent = db::insert_parent(&mut *transaction, body.parent).await?;
    let mut children = Vec::new();
    for child in body.children {
        children.push(ChildRead::from(db::insert_child(&mut *transaction, parent.id, child).await?));
    }
    transaction.commit().await?;
"#;

const API_BATCH_STORE: &str = r#"    let (parent, rows) = db::insert_batch(&state.store, body.parent, body.children).await?;
    let children = rows.into_iter().map(ChildRead::from).collect();
"#;

const API_IMPORT_SQL: &str = r#"    let mut transaction = state.pool.begin().await?;
    let mut children = Vec::new();
    for record in records {
        let row = db::insert_child(&mut *transaction, parent, record).await.map_err(missing_parent)?;
        children.push(ChildRead::from(row));
    }
    transaction.commit().await?;
"#;

const API_IMPORT_STORE: &str = r#"    let rows = db::insert_children(&state.store, parent, records).await?;
    let children: Vec<ChildRead> = rows.into_iter().map(ChildRead::from).collect();
"#;

/// The PostgreSQL api.rs on the store: the same routes and checks, the Python Mongo answers.
fn api(text: String) -> Result<String, String> {
    let batch = usize::from(text.contains(API_BATCH_SQL));
    let import = usize::from(text.contains(API_IMPORT_SQL));
    let text = swap(&text, API_BATCH_SQL, API_BATCH_STORE, batch)?;
    let text = swap(&text, API_IMPORT_SQL, API_IMPORT_STORE, import)?;
    let text = text.replace(API_MISSING_PARENT, "");
    let text = swap(&text, "use sqlx::PgPool;\n", "", 1)?;
    // The pool and the token checks, the caller checks, or the object store and either.
    let text = swap(&text, "/// The pool", "/// The MongoDB store", 1)?;
    let text = swap(
        &text,
        "    pub pool: PgPool,\n",
        "    pub store: db::Store,\n",
        1,
    )?;
    let text = swap(
        &text,
        "pub enum ApiError {\n",
        "pub enum ApiError {\n    Unavailable,\n    HasChildren,\n",
        1,
    )?;
    let text = swap(
        &text,
        r#"json!({"detail": "relationship or unique value conflict"})"#,
        r#"json!({"detail": "unique value conflict"})"#,
        1,
    )?;
    let internal = "            Self::Internal => (StatusCode::INTERNAL_SERVER_ERROR,";
    let text = swap(
        &text,
        internal,
        &format!(
            "            Self::Unavailable => (StatusCode::SERVICE_UNAVAILABLE, json!({{\"detail\": \"database unavailable\"}})),\n            Self::HasChildren => (StatusCode::CONFLICT, json!({{\"detail\": \"parent has children\"}})),\n{internal}"
        ),
        1,
    )?;
    let text = swap(&text, API_FROM_SQLX, API_FROM_STORE, 1)?;
    let text = swap(
        &text,
        "async fn known_parent(pool: &PgPool, id: Option<i32>)",
        "async fn known_parent(store: &db::Store, id: Option<i32>)",
        1,
    )?;
    let text = swap(
        &text,
        "db::parent_exists(pool, id)",
        "db::parent_exists(store, id)",
        1,
    )?;
    let delete = usize::from(text.contains("async fn remove_parent("));
    let text = swap(
        &text,
        "/// A parent that still has children answers 409: the foreign key restricts the delete.\n",
        "/// A parent that still has children answers 409 parent has children.\n",
        delete,
    )?;
    let text = text
        .replace(".await.map_err(missing_parent)?", ".await?")
        .replace("state.pool", "state.store");
    for left in ["sqlx", "PgPool", "missing_parent", "transaction.commit"] {
        if text.contains(left) {
            return Err(format!("capability_mongodb: api.rs still names {left}"));
        }
    }
    Ok(text)
}

// ---------------------------------------------------------------- src/db.rs

/// How a field is written to and read from a document.
fn writer(kind: Kind) -> &'static str {
    match kind {
        Kind::Str => "Bson::String",
        Kind::Int => "int",
        Kind::Float => "Bson::Double",
        Kind::Bool => "Bson::Boolean",
        Kind::Date => "date",
    }
}

fn reader(kind: Kind) -> &'static str {
    match kind {
        Kind::Str => "read_str",
        Kind::Int => "read_int",
        Kind::Float => "read_float",
        Kind::Bool => "read_bool",
        Kind::Date => "read_date",
    }
}

/// A string literal for a document key.
fn key(name: &str) -> String {
    format!("{name:?}")
}

const DB_HEAD: &str = r#"use std::time::{Duration, Instant};

use mongodb::bson::{Bson, Document, doc};
use mongodb::error::{
    ErrorKind, TRANSIENT_TRANSACTION_ERROR, UNKNOWN_TRANSACTION_COMMIT_RESULT, WriteFailure,
};
use mongodb::options::{@OPTIONS@};
use mongodb::{Client, ClientSession, Collection, Cursor, Database, IndexModel};

use crate::integrations::Mongo;
use crate::models::{ChildRow, ParentRow};
use crate::schemas::{ChildCreate, ChildUpdate, ParentCreate, ParentUpdate};

const PARENTS: &str = @PARENTS@;
const CHILDREN: &str = @CHILDREN@;
const COUNTERS: &str = "_som_counters";

/// What a store call answers besides its rows.
#[derive(Debug)]
pub enum Error {
    /// The parent does not exist (404 parent not found).
    ParentMissing,
    /// A parent delete while children refer to it (409 parent has children).
    HasChildren,
    /// A duplicate key (409 unique value conflict).
    Conflict,
    /// A write that MongoDB could not complete (503 database unavailable).
    Unavailable,
    /// A read that failed or a document that cannot be read (500).
    Internal,
}

/// Why a transaction body stopped: an answer for the caller, or a driver error.
enum Failure {
    Answer(Error),
    Driver(mongodb::error::Error),
}

impl From<mongodb::error::Error> for Failure {
    fn from(error: mongodb::error::Error) -> Self {
        Self::Driver(error)
    }
}

/// A duplicate key error, as pymongo raises DuplicateKeyError.
fn duplicate(error: &mongodb::error::Error) -> bool {
    let code = match &*error.kind {
        ErrorKind::Command(found) => found.code,
        ErrorKind::Write(WriteFailure::WriteError(found)) => found.code,
        _ => return false,
    };
    matches!(code, 11000 | 11001 | 12582)
}

/// A failed write: 409 for a duplicate key, else 503.
fn write_error(error: mongodb::error::Error) -> Error {
    if duplicate(&error) {
        Error::Conflict
    } else {
        Error::Unavailable
    }
}

/// A failed read outside a transaction answers 500.
fn read_error(_error: mongodb::error::Error) -> Error {
    Error::Internal
}

/// with_transaction retries for at most 120 seconds, as pymongo's does.
const RETRY_LIMIT: Duration = Duration::from_secs(120);

/// The client and the database the settings name.
#[derive(Clone)]
pub struct Store {
    client: Client,
    database: Database,
}

impl Store {
    /// The client connects on first use and selects a server for at most 2 seconds.
    pub async fn open(settings: &Mongo) -> Result<Self, String> {
        let mut options = ClientOptions::parse(&settings.url)
            .await
            .map_err(|error| error.to_string())?;
        options.server_selection_timeout = Some(Duration::from_secs(2));
        let client = Client::with_options(options).map_err(|error| error.to_string())?;
        let database = client.database(&settings.name);
        Ok(Self { client, database })
    }

    pub async fn close(self) {
        self.client.shutdown().await;
    }

    fn collection(&self, name: &str) -> Collection<Document> {
        self.database.collection(name)
    }

    /// initialize(): a ping, a replica set or mongos for transactions, then each model's unique
    /// and query indexes.
    pub async fn upgrade(&self) -> Result<(), String> {
        let failed = |error: mongodb::error::Error| error.to_string();
        self.database
            .run_command(doc! {"ping": 1})
            .await
            .map_err(failed)?;
        let hello = self
            .client
            .database("admin")
            .run_command(doc! {"hello": 1})
            .await
            .map_err(failed)?;
        let replica_set = hello.get_str("setName").is_ok_and(|name| !name.is_empty());
        let mongos = hello.get_str("msg").is_ok_and(|msg| msg == "isdbgrid");
        if !replica_set && !mongos {
            return Err("MongoDB transactions require a replica set or mongos".to_owned());
        }
@INDEXES@        Ok(())
    }

    /// downgrade drops the collections of an explicitly named isolated test database only.
    pub async fn downgrade(&self) -> Result<(), String> {
        let name = self.database.name();
        let expected = std::env::var("SOM_TEST_DATABASE_NAME").unwrap_or_default();
        let test = std::env::var("APP_ENV").is_ok_and(|value| value == "test");
        if !test || !name.starts_with("som_test_") || expected != name {
            return Err("downgrade requires the explicitly named isolated test database".to_owned());
        }
        for collection in [CHILDREN, PARENTS, COUNTERS] {
            self.collection(collection)
                .drop()
                .await
                .map_err(|error| error.to_string())?;
        }
        Ok(())
    }

    /// A query index on the keys in order, ascending.
    async fn index(&self, collection: &str, keys: &[&str]) -> Result<(), String> {
        let mut key = Document::new();
        for name in keys {
            key.insert(*name, 1);
        }
        let model = IndexModel::builder().keys(key).build();
        self.collection(collection)
            .create_index(model)
            .await
            .map(drop)
            .map_err(|error| error.to_string())
    }
@UNIQUE_INDEX@}

/// One write in one transaction, as pymongo's with_transaction runs it.
struct Transaction {
    session: ClientSession,
    started: Instant,
}

impl Transaction {
    async fn begin(store: &Store) -> Result<Self, Error> {
        let mut session = store.client.start_session().await.map_err(write_error)?;
        session.start_transaction().await.map_err(write_error)?;
        Ok(Self {
            session,
            started: Instant::now(),
        })
    }

    /// An error with the label, within the 120 seconds of retries.
    fn retries(&self, error: &mongodb::error::Error, label: &str) -> bool {
        error.contains_label(label) && self.started.elapsed() < RETRY_LIMIT
    }

    /// The value once the transaction commits, or None when the body runs again in a new
    /// transaction: a transient error retries the transaction and an unknown commit result
    /// retries the commit.
    async fn end<T>(&mut self, outcome: Result<T, Failure>) -> Result<Option<T>, Error> {
        let value = match outcome {
            Ok(value) => value,
            Err(failure) => {
                let _ = self.session.abort_transaction().await;
                return match failure {
                    Failure::Answer(answer) => Err(answer),
                    Failure::Driver(error) if self.retries(&error, TRANSIENT_TRANSACTION_ERROR) => {
                        self.restart().await
                    }
                    Failure::Driver(error) => Err(write_error(error)),
                };
            }
        };
        loop {
            match self.session.commit_transaction().await {
                Ok(()) => return Ok(Some(value)),
                Err(error) if self.retries(&error, UNKNOWN_TRANSACTION_COMMIT_RESULT) => {}
                Err(error) if self.retries(&error, TRANSIENT_TRANSACTION_ERROR) => {
                    return self.restart().await;
                }
                Err(error) => return Err(write_error(error)),
            }
        }
    }

    async fn restart<T>(&mut self) -> Result<Option<T>, Error> {
        self.session.start_transaction().await.map_err(write_error)?;
        Ok(None)
    }
}

/// Run the body in one transaction until it commits. The body answers Result<T, Failure>.
macro_rules! transaction {
    ($store:expr, |$session:ident| $body:block) => {{
        let mut transaction = Transaction::begin($store).await?;
        loop {
            let $session = &mut transaction.session;
            let outcome: Result<_, Failure> = async $body.await;
            if let Some(value) = transaction.end(outcome).await? {
                break value;
            }
        }
    }};
}

/// MongoDB answers ping.
pub async fn ping(store: &Store) -> Result<(), Error> {
    store
        .database
        .run_command(doc! {"ping": 1})
        .await
        .map(drop)
        .map_err(write_error)
}

/// The next id of a collection: its counter in _som_counters, in the transaction of the insert.
async fn next_id(store: &Store, session: &mut ClientSession, name: &str) -> Result<i32, Failure> {
    let counter = store
        .collection(COUNTERS)
        .find_one_and_update(doc! {"_id": name}, doc! {"$inc": {"value": 1}})
        .upsert(true)
        .return_document(ReturnDocument::After)
        .session(session)
        .await?;
    read_int(counter.as_ref().and_then(|found| found.get("value"))).map_err(Failure::Answer)
}

/// A new document: its id, the fields, then both timestamps at the same millisecond.
async fn insert(
    store: &Store,
    session: &mut ClientSession,
    collection: &str,
    fields: Document,
) -> Result<Document, Failure> {
    let id = next_id(store, session, collection).await?;
    let now = Bson::DateTime(mongodb::bson::DateTime::now());
    let mut document = doc! {"_id": id};
    document.extend(fields);
    document.insert("created_at", now.clone());
    document.insert("updated_at", now);
    store
        .collection(collection)
        .insert_one(&document)
        .session(session)
        .await?;
    Ok(document)
}

/// require_parent with touch: the parent revision rises in the transaction, so a child write and
/// a parent delete conflict. Whether the parent exists.
async fn touch(store: &Store, session: &mut ClientSession, id: i32) -> Result<bool, Failure> {
    let found = store
        .collection(PARENTS)
        .find_one_and_update(doc! {"_id": id}, doc! {"$inc": {"_som_revision": 1}})
        .session(session)
        .await?;
    Ok(found.is_some())
}

/// A $set of the present members and updated_at, then the document again. None if it is gone.
async fn update(
    store: &Store,
    session: &mut ClientSession,
    collection: &str,
    filter: &Document,
    changes: &Document,
) -> Result<Option<Document>, Failure> {
    let documents = store.collection(collection);
    if documents
        .find_one(filter.clone())
        .session(&mut *session)
        .await?
        .is_none()
    {
        return Ok(None);
    }
    if !changes.is_empty() {
        let mut changes = changes.clone();
        changes.insert("updated_at", mongodb::bson::DateTime::now());
        documents
            .update_one(filter.clone(), doc! {"$set": changes})
            .session(&mut *session)
            .await?;
    }
    Ok(documents
        .find_one(filter.clone())
        .session(&mut *session)
        .await?)
}

/// The rows of a cursor in order.
async fn rows<T>(
    mut cursor: Cursor<Document>,
    row: fn(&Document) -> Result<T, Error>,
) -> Result<Vec<T>, Error> {
    let mut found = Vec::new();
    while cursor.advance().await.map_err(read_error)? {
        let document: Document = cursor.deserialize_current().map_err(read_error)?;
        found.push(row(&document)?);
    }
    Ok(found)
}

/// One document by filter, outside a transaction.
async fn find_one(store: &Store, collection: &str, filter: Document) -> Result<Option<Document>, Error> {
    store
        .collection(collection)
        .find_one(filter)
        .await
        .map_err(read_error)
}

/// A stored integer: pymongo writes int32 when it fits, else int64.
fn read_int(value: Option<&Bson>) -> Result<i32, Error> {
    match value {
        Some(Bson::Int32(found)) => Ok(*found),
        Some(Bson::Int64(found)) => i32::try_from(*found).map_err(|_| Error::Internal),
        _ => Err(Error::Internal),
    }
}

/// A stored timestamp at millisecond precision.
fn read_time(value: Option<&Bson>) -> Result<chrono::DateTime<chrono::Utc>, Error> {
    match value {
        Some(Bson::DateTime(found)) => {
            chrono::DateTime::from_timestamp_millis(found.timestamp_millis()).ok_or(Error::Internal)
        }
        _ => Err(Error::Internal),
    }
}
"#;

const DB_UNIQUE_INDEX: &str = r#"
    /// A unique index on one field, partial on its BSON type, so that nulls do not collide.
    async fn unique(&self, collection: &str, field: &str, kind: &str) -> Result<(), String> {
        let options = IndexOptions::builder()
            .unique(true)
            .partial_filter_expression(doc! {field: {"$type": kind}})
            .build();
        let model = IndexModel::builder()
            .keys(doc! {field: 1})
            .options(options)
            .build();
        self.collection(collection)
            .create_index(model)
            .await
            .map(drop)
            .map_err(|error| error.to_string())
    }
"#;

const DB_INT: &str = r#"
/// An integer as pymongo writes it: int32 when it fits, else int64.
fn int(value: i64) -> Bson {
    i32::try_from(value).map_or(Bson::Int64(value), Bson::Int32)
}
"#;

const DB_DATE: &str = r#"
/// A date as the Python product writes it: midnight UTC.
fn date(value: chrono::NaiveDate) -> Bson {
    let millis = value.and_time(chrono::NaiveTime::MIN).and_utc().timestamp_millis();
    Bson::DateTime(mongodb::bson::DateTime::from_millis(millis))
}

/// A stored date: the day of its midnight UTC.
fn read_date(value: Option<&Bson>) -> Result<chrono::NaiveDate, Error> {
    read_time(value).map(|time| time.date_naive())
}
"#;

const DB_STR: &str = r#"
fn read_str(value: Option<&Bson>) -> Result<String, Error> {
    match value {
        Some(Bson::String(found)) => Ok(found.clone()),
        _ => Err(Error::Internal),
    }
}
"#;

const DB_BOOL: &str = r#"
fn read_bool(value: Option<&Bson>) -> Result<bool, Error> {
    match value {
        Some(Bson::Boolean(found)) => Ok(*found),
        _ => Err(Error::Internal),
    }
}
"#;

const DB_FLOAT: &str = r#"
/// A stored number as a float, as pydantic reads an integer into a float field.
fn read_float(value: Option<&Bson>) -> Result<f64, Error> {
    match value {
        Some(Bson::Double(found)) => Ok(*found),
        Some(Bson::Int32(found)) => Ok(f64::from(*found)),
        Some(Bson::Int64(found)) => Ok(*found as f64),
        _ => Err(Error::Internal),
    }
}
"#;

const DB_NULLABLE: &str = r#"
/// A missing or null member is None.
fn nullable<T>(
    value: Option<&Bson>,
    read: fn(Option<&Bson>) -> Result<T, Error>,
) -> Result<Option<T>, Error> {
    match value {
        None | Some(Bson::Null) => Ok(None),
        found => read(found).map(Some),
    }
}
"#;

/// The BSON type of a unique field's partial index, as the Python models name it.
fn bson_type(kind: Kind) -> &'static str {
    match kind {
        Kind::Str => "string",
        Kind::Int => "int",
        Kind::Float => "double",
        Kind::Bool => "bool",
        Kind::Date => "date",
    }
}

/// The initialize() index calls of one model: its unique fields, then its query indexes.
fn index_calls(collection: &str, resource: &Resource, prefix: Option<&Field>) -> String {
    let mut out = String::new();
    for field in resource.fields.iter().filter(|field| field.unique) {
        out += &format!(
            "        self.unique({collection}, {}, {:?}).await?;\n",
            key(&field.name),
            bson_type(field.kind)
        );
    }
    let mut indexes: Vec<Vec<&str>> = Vec::new();
    if let Some(prefix) = prefix {
        indexes.push(vec![&prefix.name, "_id"]);
    }
    for name in [&resource.filter, &resource.date].into_iter().flatten() {
        let mut keys: Vec<&str> = prefix.iter().map(|field| field.name.as_str()).collect();
        keys.push(name);
        keys.push("_id");
        indexes.push(keys);
    }
    for keys in indexes {
        let keys: Vec<String> = keys.iter().map(|name| key(name)).collect();
        out += &format!(
            "        self.index({collection}, &[{}]).await?;\n",
            keys.join(", ")
        );
    }
    out
}

/// The statements that put the members of a create body into `fields`, in field order.
fn field_inserts(fields: &[Field], target: &str) -> String {
    let mut out = String::new();
    for field in fields {
        let member = field.ident();
        let value = if field.nullable() {
            format!("body.{member}.map_or(Bson::Null, {})", writer(field.kind))
        } else {
            format!("{}(body.{member})", writer(field.kind))
        };
        out += &format!("    {target}.insert({}, {value});\n", key(&field.name));
    }
    out
}

/// The function that reads a row of `row` from a document.
fn row_reader(name: &str, row: &str, key_field: Option<&Field>, fields: &[Field]) -> String {
    let mut out = format!(
        "\nfn {name}(document: &Document) -> Result<{row}, Error> {{\n    Ok({row} {{\n        id: read_int(document.get(\"_id\"))?,\n"
    );
    // Named together, as the row struct is: a foreign key and a field can share a fallback.
    for field in &with_key(key_field, fields) {
        let get = format!("document.get({})", key(&field.name));
        let read = if field.nullable() {
            format!("nullable({get}, {})?", reader(field.kind))
        } else {
            format!("{}({get})?", reader(field.kind))
        };
        out += &format!("        {}: {read},\n", field.ident());
    }
    out + "        created_at: read_time(document.get(\"created_at\"))?,\n        updated_at: read_time(document.get(\"updated_at\"))?,\n    })\n}\n"
}

/// The present members of an update body as a $set document.
fn changes(name: &str, role: &str, fields: &[Field]) -> String {
    let mut out = format!(
        "\n/// The present members of a patch, null included.\nfn {name}(body: {role}Update) -> Document {{\n    let mut changes = Document::new();\n"
    );
    for field in fields {
        out += &format!(
            "    if let Some(value) = body.{} {{\n        changes.insert({}, value.map_or(Bson::Null, {}));\n    }}\n",
            field.ident(),
            key(&field.name),
            writer(field.kind)
        );
    }
    out + "    changes\n}\n"
}

/// A list: the filter, then the date bound, in id order with skip and limit.
fn list(
    name: &str,
    row: &str,
    reader: &str,
    resource: &Resource,
    key_field: Option<&Field>,
) -> String {
    let mut args = String::new();
    let mut filters = String::new();
    if let Some(field) = &resource.filter {
        args += "    filter: Option<bool>,\n";
        filters += &format!(
            "    if let Some(value) = filter {{\n        query.insert({}, value);\n    }}\n",
            key(field)
        );
    }
    if let Some(field) = &resource.date {
        args += "    before: Option<chrono::NaiveDate>,\n";
        filters += &format!(
            "    if let Some(value) = before {{\n        query.insert({}, doc! {{\"$lt\": date(value), \"$ne\": Bson::Null}});\n    }}\n",
            key(field)
        );
    }
    let (parent_arg, start) = match key_field {
        Some(field) => (
            "    parent: i32,\n",
            format!("doc! {{{}: parent}}", key(&field.name)),
        ),
        None => ("", "Document::new()".to_owned()),
    };
    let binding = if filters.is_empty() {
        "let query"
    } else {
        "let mut query"
    };
    let collection = if key_field.is_some() {
        "CHILDREN"
    } else {
        "PARENTS"
    };
    format!(
        "\npub async fn {name}(\n    store: &Store,\n{parent_arg}{args}    limit: i64,\n    offset: i64,\n) -> Result<Vec<{row}>, Error> {{\n    {binding} = {start};\n{filters}    let cursor = store\n        .collection({collection})\n        .find(query)\n        .sort(doc! {{\"_id\": 1}})\n        .skip(u64::try_from(offset).unwrap_or_default())\n        .limit(limit)\n        .await\n        .map_err(read_error)?;\n    rows(cursor, {reader}).await\n}}\n"
    )
}

const DB_PARENT: &str = r#"
pub async fn insert_parent(store: &Store, body: ParentCreate) -> Result<ParentRow, Error> {
    let fields = parent_fields(body);
    let document = transaction!(store, |session| { insert(store, session, PARENTS, fields.clone()).await });
    parent_row(&document)
}

pub async fn parent_exists(store: &Store, id: i32) -> Result<bool, Error> {
    Ok(find_one(store, PARENTS, doc! {"_id": id}).await?.is_some())
}

pub async fn fetch_parent(store: &Store, id: i32) -> Result<Option<ParentRow>, Error> {
    let found = find_one(store, PARENTS, doc! {"_id": id}).await?;
    found.as_ref().map(parent_row).transpose()
}

/// Absent members keep their values. updated_at changes when a member is present.
pub async fn update_parent(
    store: &Store,
    id: i32,
    body: ParentUpdate,
) -> Result<Option<ParentRow>, Error> {
    let changes = parent_changes(body);
    let filter = doc! {"_id": id};
    let found = transaction!(store, |session| { update(store, session, PARENTS, &filter, &changes).await });
    found.as_ref().map(parent_row).transpose()
}

/// The parent is touched first, so a concurrent child insert conflicts with the delete. A parent
/// with children answers 409.
pub async fn delete_parent(store: &Store, id: i32) -> Result<bool, Error> {
    Ok(transaction!(store, |session| {
            if !touch(store, session, id).await? {
                return Ok(false);
            }
            let child = store
                .collection(CHILDREN)
                .find_one(doc! {@KEY@: id})
                .session(&mut *session)
                .await?;
            if child.is_some() {
                return Err(Failure::Answer(Error::HasChildren));
            }
            store
                .collection(PARENTS)
                .delete_one(doc! {"_id": id})
                .session(&mut *session)
                .await?;
            Ok(true)
        }))
}
"#;

const DB_CHILD: &str = r#"
/// A child document's fields: the foreign key first.
fn child_document(parent: i32, fields: &Document) -> Document {
    let mut document = doc! {@KEY@: parent};
    document.extend(fields.clone());
    document
}

/// The parent is touched in the transaction: a parent gone by then answers 404.
pub async fn insert_child(store: &Store, parent: i32, body: ChildCreate) -> Result<ChildRow, Error> {
    let fields = child_fields(body);
    let document = transaction!(store, |session| {
            if !touch(store, session, parent).await? {
                return Err(Failure::Answer(Error::ParentMissing));
            }
            insert(store, session, CHILDREN, child_document(parent, &fields)).await
        });
    child_row(&document)
}

pub async fn fetch_child(store: &Store, id: i32, parent: i32) -> Result<Option<ChildRow>, Error> {
    let found = find_one(store, CHILDREN, doc! {"_id": id, @KEY@: parent}).await?;
    found.as_ref().map(child_row).transpose()
}

/// Absent members keep their values. updated_at changes when a member is present.
pub async fn update_child(
    store: &Store,
    id: i32,
    parent: i32,
    body: ChildUpdate,
) -> Result<Option<ChildRow>, Error> {
    let changes = child_changes(body);
    let filter = doc! {"_id": id, @KEY@: parent};
    let found = transaction!(store, |session| { update(store, session, CHILDREN, &filter, &changes).await });
    found.as_ref().map(child_row).transpose()
}

pub async fn delete_child(store: &Store, id: i32, parent: i32) -> Result<bool, Error> {
    Ok(transaction!(store, |session| {
            let done = store
                .collection(CHILDREN)
                .delete_one(doc! {"_id": id, @KEY@: parent})
                .session(&mut *session)
                .await?;
            Ok(done.deleted_count > 0)
        }))
}

/// Every child of a parent in id order, one row past the export limit.
pub async fn export_children(store: &Store, parent: i32) -> Result<Vec<ChildRow>, Error> {
    let cursor = store
        .collection(CHILDREN)
        .find(doc! {@KEY@: parent})
        .sort(doc! {"_id": 1})
        .limit(1001)
        .await
        .map_err(read_error)?;
    rows(cursor, child_row).await
}

/// The parent, then its children in order, in one transaction: a failing child leaves no row.
pub async fn insert_batch(
    store: &Store,
    parent: ParentCreate,
    children: Vec<ChildCreate>,
) -> Result<(ParentRow, Vec<ChildRow>), Error> {
    let parent_fields = parent_fields(parent);
    let children: Vec<Document> = children.into_iter().map(child_fields).collect();
    let (parent, children) = transaction!(store, |session| {
            let parent = insert(store, session, PARENTS, parent_fields.clone()).await?;
            let id = read_int(parent.get("_id")).map_err(Failure::Answer)?;
            let mut rows = Vec::new();
            for fields in &children {
                rows.push(insert(store, session, CHILDREN, child_document(id, fields)).await?);
            }
            Ok((parent, rows))
        });
    let children = children.iter().map(child_row).collect::<Result<_, _>>()?;
    Ok((parent_row(&parent)?, children))
}
"#;

const DB_IMPORT: &str = r#"
/// The children of an import, in one transaction after the parent is touched.
pub async fn insert_children(
    store: &Store,
    parent: i32,
    bodies: Vec<ChildCreate>,
) -> Result<Vec<ChildRow>, Error> {
    let children: Vec<Document> = bodies.into_iter().map(child_fields).collect();
    let documents = transaction!(store, |session| {
            if !touch(store, session, parent).await? {
                return Err(Failure::Answer(Error::ParentMissing));
            }
            let mut rows = Vec::new();
            for fields in &children {
                rows.push(insert(store, session, CHILDREN, child_document(parent, fields)).await?);
            }
            Ok(rows)
        });
    documents.iter().map(child_row).collect()
}
"#;

fn db(product: &Product) -> String {
    let (parent, child, fk) = (&product.parent, &product.child, &product.key);
    let fields: Vec<&Field> = parent.fields.iter().chain(&child.fields).collect();
    let has = |kind: Kind| fields.iter().any(|field| field.kind == kind);
    let unique = fields.iter().any(|field| field.unique);
    let indexes = index_calls("PARENTS", parent, None) + &index_calls("CHILDREN", child, Some(fk));
    let mut out = DB_HEAD
        .replace("@PARENTS@", &key(&parent.table))
        .replace("@CHILDREN@", &key(&child.table))
        .replace("@INDEXES@", &indexes)
        .replace("@UNIQUE_INDEX@", if unique { DB_UNIQUE_INDEX } else { "" })
        .replace(
            "@OPTIONS@",
            if unique {
                "ClientOptions, IndexOptions, ReturnDocument"
            } else {
                "ClientOptions, ReturnDocument"
            },
        );
    for (needed, helper) in [
        (has(Kind::Int), DB_INT),
        (has(Kind::Date), DB_DATE),
        (has(Kind::Str), DB_STR),
        (has(Kind::Bool), DB_BOOL),
        (has(Kind::Float), DB_FLOAT),
        (fields.iter().any(|field| field.nullable()), DB_NULLABLE),
    ] {
        if needed {
            out += helper;
        }
    }
    out += &format!(
        "\n/// The members of a parent create body, in field order.\nfn parent_fields(body: ParentCreate) -> Document {{\n    let mut fields = Document::new();\n{}    fields\n}}\n",
        field_inserts(&parent.fields, "fields")
    );
    out += &format!(
        "\n/// The members of a child create body, in field order.\nfn child_fields(body: ChildCreate) -> Document {{\n    let mut fields = Document::new();\n{}    fields\n}}\n",
        field_inserts(&child.fields, "fields")
    );
    out += &changes("parent_changes", "Parent", &parent.fields);
    out += &changes("child_changes", "Child", &child.fields);
    out += &row_reader("parent_row", "ParentRow", None, &parent.fields);
    out += &row_reader("child_row", "ChildRow", Some(fk), &child.fields);
    out += &DB_PARENT.replace("@KEY@", &key(&fk.name));
    out += &list("list_parents", "ParentRow", "parent_row", parent, None);
    out += &DB_CHILD.replace("@KEY@", &key(&fk.name));
    out += &list("list_children", "ChildRow", "child_row", child, Some(fk));
    // The csv import calls it; it is public, so the other workflows keep it without a warning.
    out + DB_IMPORT
}

// ---------------------------------------------------------------- src/integrations.rs

const SETTINGS_MONGO: &str = r#"/// The MongoDB settings: a mongodb:// or mongodb+srv:// URL and a database name.
#[derive(Clone, Debug)]
pub struct Mongo {
    pub url: String,
    pub name: String,
}

/// The MONGODB_URL and MONGODB_DATABASE settings, which migrations read alone.
pub fn database() -> Result<Mongo, String> {
    let read = |name: &str| {
        std::env::var_os(name)
            .map(|found| found.to_string_lossy().into_owned())
            .unwrap_or_default()
    };
    database_named(&read("MONGODB_URL"), &read("MONGODB_DATABASE"))
}

/// A URL that is not mongodb:// or mongodb+srv://, or an empty name, refuses startup.
pub fn database_named(url: &str, name: &str) -> Result<Mongo, String> {
    let scheme = url.starts_with("mongodb://") || url.starts_with("mongodb+srv://");
    if !scheme || name.is_empty() {
        return Err("MONGODB_URL and MONGODB_DATABASE are required".to_owned());
    }
    Ok(Mongo {
        url: url.to_owned(),
        name: name.to_owned(),
    })
}
"#;

fn integrations(text: String) -> Result<String, String> {
    // FromStr parsed the PostgreSQL URL only. Alone among the std imports it leaves a blank line.
    let alone = "use std::str::FromStr;\n\n";
    let from_str = if text.contains(alone) {
        alone
    } else {
        "use std::str::FromStr;\n"
    };
    let text = swap(&text, from_str, "", 1)?;
    let text = swap(&text, "use sqlx::postgres::PgConnectOptions;\n", "", 1)?;
    let text = swap(
        &text,
        "    pub database: PgConnectOptions,\n",
        "    pub database: Mongo,\n",
        1,
    )?;
    let text = swap(
        &text,
        "    /// The tests read their database from TEST_DATABASE_URL and the rest as the product does.\n    pub fn load_with(url: &str) -> Result<Self, String> {\n",
        "    /// The tests read their database from MONGODB_URL and SOM_TEST_DATABASE_NAME and the rest\n    /// as the product does.\n    pub fn load_with(url: &str, name: &str) -> Result<Self, String> {\n",
        1,
    )?;
    let text = swap(
        &text,
        "database: database_url(url)?,",
        "database: database_named(url, name)?,",
        1,
    )?;
    let text = swap_span(
        &text,
        "/// The DATABASE_URL setting, which migrations read alone.\n",
        "    Ok(options)\n}\n",
        SETTINGS_MONGO,
    )?;
    if text.contains("sqlx") || text.contains("PgConnectOptions") {
        return Err("capability_mongodb: integrations.rs still names sqlx".to_owned());
    }
    Ok(text)
}

// ---------------------------------------------------------------- tests/api.rs

const TESTS_APP: &str = r#"/// The application on the isolated test database that MONGODB_URL and SOM_TEST_DATABASE_NAME
/// name, with the token settings the service reads.
async fn app() -> Router {
    let url = std::env::var("MONGODB_URL").unwrap_or_default();
    let name = std::env::var("SOM_TEST_DATABASE_NAME").unwrap_or_default();
    assert!(
        name.starts_with("som_test_"),
        "SOM_TEST_DATABASE_NAME must name an isolated test database"
    );
    let settings = som_app::integrations::Settings::load_with(&url, &name).expect("settings");
    let store = som_app::db::Store::open(&settings.database)
        .await
        .expect("store");
    som_app::router(som_app::AppState {
        store,
        auth: Arc::new(settings.auth),
    })
}
"#;

fn tests_on_mongo(text: String) -> Result<String, String> {
    let text = swap_span(
        &text,
        "/// The application on TEST_DATABASE_URL",
        "\n}\n",
        TESTS_APP,
    )?;
    let calls = text.matches("let app = app();").count();
    let text = swap(&text, "let app = app();", "let app = app().await;", calls)?;
    if text.contains("TEST_DATABASE_URL") || text.contains("sqlx") {
        return Err("capability_mongodb: tests/api.rs still names PostgreSQL".to_owned());
    }
    Ok(text)
}

// ---------------------------------------------------------------- README.md

/// The PostgreSQL README with the MongoDB database, settings, commands and collections.
fn readme(params: &Value, product: &Product) -> Result<String, String> {
    let mut named = params.clone();
    named["revision"] = Value::from("none");
    let workflow = Workflow::read(params, &product.child)?;
    let package = text(params, "package")?;
    let version = text(params, "version")?;
    let text = super::readme(&named, product)?;
    let (parent, child) = (&product.parent, &product.child);
    let head = format!(
        r#"# {package}

A Rust service generated by SOM from a `{version}` prompt for the package `{package}`.
The Cargo package, library and binary are `som_app`. It uses axum, the mongodb driver and tokio on
MongoDB. Its shape is `capability_mongodb`: RS256 bearer tokens (`auth: jwt`), no object storage
(`storage: none`) and {}.

Collection `{}` holds the parents and collection `{}` the children. A child document belongs to
one parent through `{}`. Ids come from the `_som_counters` collection, and every write runs in one
transaction, so MongoDB must be a replica set or mongos.
"#,
        workflow.title(),
        parent.table,
        child.table,
        product.key.name
    );
    let routes = text
        .find("\n## Routes\n")
        .ok_or("capability_mongodb: README has no routes")?;
    let text = head + &text[routes..];
    let text = swap(
        &text,
        "A unique value\nor foreign key conflict answers 409 `relationship or unique value conflict`. Invalid input returns\n422 with",
        "A unique value\nconflict answers 409 `unique value conflict`, a parent that still has children answers 409\n`parent has children`, and a write MongoDB cannot complete answers 503 `database unavailable`.\nInvalid input returns 422 with",
        1,
    )?;
    let text = swap(
        &text,
        "- `DATABASE_URL` (required): a `postgresql+psycopg://` URL.\n",
        "- `MONGODB_URL` (required): a `mongodb://` or `mongodb+srv://` URL.\n- `MONGODB_DATABASE` (required): the database name.\n",
        1,
    )?;
    let text = swap(
        &text,
        "A missing or empty required setting refuses startup with `<NAME> is required`.\n",
        "A missing or empty required setting refuses startup with `<NAME> is required`, and a missing\nMongoDB setting with `MONGODB_URL and MONGODB_DATABASE are required`.\n",
        1,
    )?;
    let text = swap(
        &text,
        "`serve` does not migrate: run `som_app migrate upgrade` first.",
        "`serve` and `migrate upgrade` both check for a replica set or mongos and create the indexes.",
        1,
    )?;
    let text = swap(
        &text,
        "TEST_DATABASE_URL=postgresql+psycopg://user@127.0.0.1:5432/app_test ",
        "MONGODB_URL=mongodb://127.0.0.1:27017/?replicaSet=rs0 SOM_TEST_DATABASE_NAME=som_test_app ",
        1,
    )?;
    Ok(text)
}

/// The Database section of the README in place of the Migration section, once the auth and
/// storage text is in.
fn database(text: String, product: &Product) -> Result<String, String> {
    let (parent, child) = (&product.parent, &product.child);
    let migration = text
        .find("\n## Migration\n")
        .ok_or("capability_mongodb: README has no migration")?;
    Ok(format!(
        "{}\n## Database\n\n`som_app migrate upgrade` pings MongoDB, requires a replica set or mongos, and creates a\npartial unique index for each unique field and the list indexes. `som_app migrate downgrade` drops\n`{}`, `{}` and `_som_counters`, only with `APP_ENV=test` and a database named `som_test_*` that\n`SOM_TEST_DATABASE_NAME` names too.\n",
        &text[..migration],
        child.table,
        parent.table
    ))
}
