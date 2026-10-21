//! Render a validated som.dsl.typescript.v1 composition plan into the 11 TypeScript product files.
//!
//! Each block in contracts/typescript-output-v1/catalog/typescript-catalog.v1.json has one arm in
//! `block` (or `related::render` for v5). `.ts` files are formatted through prettier in `format.rs`.
use serde_json::Value;
use std::collections::BTreeMap;

mod related;

pub const PACKAGE_JSON: &str =
    include_str!("../../../contracts/typescript-output-v1/reference/package.json");
pub const PACKAGE_LOCK: &str =
    include_str!("../../../contracts/typescript-output-v1/reference/package-lock.json");
pub const TSCONFIG_JSON: &str =
    include_str!("../../../contracts/typescript-output-v1/reference/tsconfig.json");

const MAIN_TS: &str = r#"import { serve } from "@hono/node-server";
import pg from "pg";
import { createApp } from "./app.ts";
import { migrate } from "./migrate.ts";

function fail(message: string): never {
  process.stderr.write(`${message}\n`);
  process.exit(2);
}

function parseAddress(raw: string): { host: string; port: number } {
  const colon = raw.lastIndexOf(":");
  if (colon <= 0 || colon === raw.length - 1) {
    fail(`invalid SOM_APP_ADDR: ${raw}`);
  }
  const host = raw.slice(0, colon);
  const portText = raw.slice(colon + 1);
  if (!/^[0-9]+$/.test(portText)) {
    fail(`invalid SOM_APP_ADDR: ${raw}`);
  }
  const port = Number(portText);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    fail(`invalid SOM_APP_ADDR: ${raw}`);
  }
  return { host, port };
}

const databaseUrl = process.env["DATABASE_URL"];
if (databaseUrl === undefined || databaseUrl === "") {
  fail("DATABASE_URL is required");
}
const { host, port } = parseAddress(
  process.env["SOM_APP_ADDR"] ?? "127.0.0.1:8000",
);

const pool = new pg.Pool({ connectionString: databaseUrl });
await migrate(pool);

const app = createApp(pool);
const server = serve({ fetch: app.fetch, hostname: host, port });

function shutdown(): void {
  server.close(() => {
    void pool.end().finally(() => {
      process.exit(0);
    });
  });
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
"#;

const MIGRATE_TS: &str = r#"import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import type pg from "pg";

const REVISION = "@REVISION@";
const MIGRATION = new URL("../migrations/0001_@REVISION@.sql", import.meta.url);

const CREATE_TABLE = `CREATE TABLE IF NOT EXISTS "_som_ts_migrations" (
  "version" INTEGER PRIMARY KEY,
  "revision" TEXT NOT NULL,
  "checksum" TEXT NOT NULL,
  "applied_at" TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
)`;
const LOCK = "SELECT pg_advisory_xact_lock(7305915)";
const FIND = `SELECT "revision", "checksum" FROM "_som_ts_migrations" WHERE "version" = 1`;
const RECORD = `INSERT INTO "_som_ts_migrations" ("version", "revision", "checksum") VALUES (1, $1, $2)`;

export async function migrate(pool: pg.Pool): Promise<void> {
  const bytes = await readFile(MIGRATION);
  const checksum = createHash("sha256").update(bytes).digest("hex");
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query(LOCK);
    await client.query(CREATE_TABLE);
    const found = await client.query<{ revision: string; checksum: string }>(
      FIND,
    );
    const row = found.rows[0];
    if (row === undefined) {
      await client.query(bytes.toString("utf8"));
      await client.query(RECORD, [REVISION, checksum]);
    } else if (row["revision"] !== REVISION || row["checksum"] !== checksum) {
      throw new Error(
        "migration 1 was recorded with another revision or checksum",
      );
    }
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}
"#;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum Kind {
    Int,
    Str,
    Bool,
    Float,
    Date,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Filter {
    All,
    Text,
    Boolean,
    BooleanDate,
}

#[derive(Clone)]
pub(super) struct Field {
    pub(super) name: String,
    pub(super) kind: Kind,
    pub(super) required: bool,
    pub(super) unique: bool,
    pub(super) default_false: bool,
}

impl Field {
    pub(super) fn nullable(&self) -> bool {
        !self.required && !self.default_false
    }

    pub(super) fn kind_str(&self) -> &'static str {
        match self.kind {
            Kind::Int => "int",
            Kind::Str => "str",
            Kind::Bool => "bool",
            Kind::Float => "float",
            Kind::Date => "date",
        }
    }

    pub(super) fn ts_type(&self) -> &'static str {
        match self.kind {
            Kind::Int | Kind::Float => "number",
            Kind::Bool => "boolean",
            Kind::Str | Kind::Date => "string",
        }
    }

    pub(super) fn stored_ts_type(&self) -> String {
        if self.nullable() {
            format!("{} | null", self.ts_type())
        } else {
            self.ts_type().to_owned()
        }
    }

    pub(super) fn sql(&self) -> &'static str {
        match self.kind {
            Kind::Int => "INTEGER",
            Kind::Str => "VARCHAR(255)",
            Kind::Bool => "BOOLEAN",
            Kind::Float => "DOUBLE PRECISION",
            Kind::Date => "DATE",
        }
    }

    /// Valid test sample expression in TypeScript for row k (0..25) and field index.
    pub(super) fn sample(&self, index: usize) -> String {
        let shift = index + 1;
        match self.kind {
            Kind::Int => format!("k * 10 + {shift}"),
            Kind::Str => format!("`{}-${{k}}`", self.name),
            Kind::Bool => {
                if self.unique {
                    "k % 2 === 0".to_owned()
                } else {
                    format!("(k + {index}) % 2 === 0")
                }
            }
            Kind::Float => format!("k * 1.5 + {index}.25"),
            Kind::Date => "`2030-01-${String((k % 27) + 1).padStart(2, \"0\")}`".to_owned(),
        }
    }
}

pub(super) fn parse_fields(value: &Value) -> Result<Vec<Field>, String> {
    let items = value
        .as_array()
        .ok_or_else(|| "fields param is not an array".to_owned())?;
    if items.is_empty() {
        return Err("fields param is empty".to_owned());
    }
    items
        .iter()
        .map(|item| {
            let name = text(item, "name")?.to_owned();
            let kind = match text(item, "type")? {
                "int" => Kind::Int,
                "str" => Kind::Str,
                "bool" => Kind::Bool,
                "float" => Kind::Float,
                "date" => Kind::Date,
                other => return Err(format!("unsupported field type {other}")),
            };
            let required = item["required"]
                .as_bool()
                .ok_or_else(|| format!("field {name}.required is not a bool"))?;
            let unique = item["unique"]
                .as_bool()
                .ok_or_else(|| format!("field {name}.unique is not a bool"))?;
            let default_false = match item.get("default") {
                None | Some(Value::Null) => false,
                Some(Value::Bool(false)) if !required && kind == Kind::Bool => true,
                Some(other) => return Err(format!("unsupported default on {name}: {other}")),
            };
            Ok(Field {
                name,
                kind,
                required,
                unique,
                default_false,
            })
        })
        .collect()
}

pub(super) fn text<'a>(params: &'a Value, key: &str) -> Result<&'a str, String> {
    params[key]
        .as_str()
        .ok_or_else(|| format!("param {key} is not a string"))
}

fn limit(params: &Value) -> Result<i64, String> {
    params["max_limit"]
        .as_i64()
        .ok_or_else(|| "param max_limit is not an integer".to_owned())
}

pub(super) fn quoted(name: &str) -> String {
    format!("\"{name}\"")
}

fn resolve(params: &Value, prompt: &Value) -> Value {
    let Some(members) = params.as_object() else {
        return params.clone();
    };
    let resolved: serde_json::Map<String, Value> = members
        .iter()
        .map(|(key, value)| {
            let found = match value.get("$ref").and_then(Value::as_str) {
                Some(name) if value.as_object().is_some_and(|object| object.len() == 1) => prompt
                    .pointer(&format!("/{}", name.replace('.', "/")))
                    .cloned()
                    .unwrap_or(Value::Null),
                _ => value.clone(),
            };
            (key.clone(), found)
        })
        .collect();
    Value::Object(resolved)
}

/// Render every file in a validated TypeScript DSL plan.
pub fn files(prompt: &Value, dsl: &Value) -> Result<BTreeMap<String, String>, String> {
    let mut out = BTreeMap::new();
    let entries = dsl["files"]
        .as_array()
        .ok_or_else(|| "dsl.files is not an array".to_owned())?;
    for entry in entries {
        let path = text(entry, "path")?;
        let blocks = entry["blocks"]
            .as_array()
            .ok_or_else(|| format!("{path}: blocks is not an array"))?;
        let mut content = String::new();
        for item in blocks {
            let id = text(item, "id")?;
            let params = resolve(&item["params"], prompt);
            if !content.is_empty() && !content.ends_with('\n') {
                content.push('\n');
            }
            content.push_str(&block(id, &params)?);
        }
        out.insert(path.to_owned(), content);
    }
    Ok(out)
}

fn block(id: &str, params: &Value) -> Result<String, String> {
    if id.contains(".related") {
        return related::render(id, params);
    }
    let fields = || parse_fields(&params["fields"]);
    Ok(match id {
        "ts.package.manifest" => PACKAGE_JSON.to_owned(),
        "ts.package.lock" => PACKAGE_LOCK.to_owned(),
        "ts.tsconfig.config" => TSCONFIG_JSON.to_owned(),
        "ts.main.startup" => MAIN_TS.to_owned(),
        "ts.migrate.runner" => MIGRATE_TS.replace("@REVISION@", text(params, "revision")?),

        "ts.schemas.create.plain" => schemas_create(&fields()?, false),
        "ts.schemas.create.service" => schemas_create(&fields()?, true),
        "ts.schemas.update.plain" => schemas_update(&fields()?, false),
        "ts.schemas.update.service" => schemas_update(&fields()?, true),
        "ts.schemas.read.plain" => schemas_read("Read", None, &fields()?, false),
        "ts.schemas.read.service" => schemas_read("Read", None, &fields()?, true),

        "ts.app.support" => [APP_IMPORTS, APP_SUPPORT].concat(),
        "ts.app.health" => APP_HEALTH.to_owned(),
        "ts.app.list.all" => APP_LIST_ALL.to_owned(),
        "ts.app.list.text" => app_list(params, Filter::Text)?,
        "ts.app.list.boolean" => app_list(params, Filter::Boolean)?,
        "ts.app.list.boolean.date" => app_list(params, Filter::BooleanDate)?,
        "ts.app.create" => APP_CREATE.to_owned(),
        "ts.app.get" => APP_GET.to_owned(),
        "ts.app.update" => APP_UPDATE.to_owned(),
        "ts.app.delete" => APP_DELETE.to_owned(),
        "ts.app.router" => APP_ROUTER.replace("@PATH@", text(params, "path")?),

        "ts.db.ping" => DB_PING.to_owned(),
        "ts.db.insert.plain" => db_insert(params, false)?,
        "ts.db.insert.service" => db_insert(params, true)?,
        "ts.db.fetch.plain" => db_fetch(params, false)?,
        "ts.db.fetch.service" => db_fetch(params, true)?,
        "ts.db.list.all" => db_list(params, Filter::All)?,
        "ts.db.list.text" => db_list(params, Filter::Text)?,
        "ts.db.list.boolean" => db_list(params, Filter::Boolean)?,
        "ts.db.list.boolean.date" => db_list(params, Filter::BooleanDate)?,
        "ts.db.update.plain" => db_update(params, false)?,
        "ts.db.update.service" => db_update(params, true)?,
        "ts.db.delete" => db_delete(params)?,

        "ts.migration.table.plain" => migration_table(params, false)?,
        "ts.migration.table.service" => migration_table(params, true)?,
        "ts.migration.indexes.boolean" => migration_indexes(params, false)?,
        "ts.migration.indexes.boolean.date" => migration_indexes(params, true)?,

        "ts.tests.harness" => tests_harness(params)?,
        "ts.tests.health" => TESTS_HEALTH.to_owned(),
        "ts.tests.crud.plain" => tests_crud(&fields()?, false),
        "ts.tests.crud.service" => tests_crud(&fields()?, true),
        "ts.tests.validation.plain" => tests_validation(&fields()?, false),
        "ts.tests.validation.service" => tests_validation(&fields()?, true),
        "ts.tests.unique" => tests_unique(&fields()?),
        "ts.tests.list.all" => TESTS_LIST_ALL.to_owned(),
        "ts.tests.list.text" => tests_list(params, Filter::Text)?,
        "ts.tests.list.boolean" => tests_list(params, Filter::Boolean)?,
        "ts.tests.list.boolean.date" => tests_list(params, Filter::BooleanDate)?,
        "ts.tests.fallback" => TESTS_FALLBACK.to_owned(),

        "ts.readme.all" => readme(params, Filter::All)?,
        "ts.readme.text" => readme(params, Filter::Text)?,
        "ts.readme.boolean" => readme(params, Filter::Boolean)?,
        "ts.readme.boolean.date" => readme(params, Filter::BooleanDate)?,

        _ => return Err(format!("no renderer for block {id}")),
    })
}

// ---------------------------------------------------------------- src/schemas.ts

pub(super) const SCHEMA_SUPPORT: &str = r#"export interface Problem {
  type: string;
  loc: (string | number)[];
  msg: string;
}

export type FieldKind = "str" | "int" | "float" | "bool" | "date";

export interface FieldSpec {
  name: string;
  kind: FieldKind;
  required: boolean;
  nullable: boolean;
  defaultFalse: boolean;
}

const MIN_I32 = -2_147_483_648;
const MAX_I32 = 2_147_483_647;

export function problem(
  type: string,
  loc: (string | number)[],
  msg: string,
): Problem {
  return { type, loc, msg };
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function own(record: Record<string, unknown>, key: string): unknown {
  return Object.hasOwn(record, key) ? record[key] : undefined;
}

export function isCalendarDate(text: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    return false;
  }
  const [y, m, d] = text.split("-").map(Number);
  if (
    y === undefined ||
    m === undefined ||
    d === undefined ||
    m < 1 ||
    m > 12 ||
    d < 1 ||
    d > 31
  ) {
    return false;
  }
  const dt = new Date(0);
  dt.setUTCFullYear(y, m - 1, d);
  return (
    dt.getUTCFullYear() === y &&
    dt.getUTCMonth() + 1 === m &&
    dt.getUTCDate() === d
  );
}

export function parseInt32(text: string): number | "invalid" | "out_of_range" {
  if (!/^[+-]?[0-9]+$/.test(text)) {
    return "invalid";
  }
  const value = Number(text);
  if (!Number.isSafeInteger(value) || value < MIN_I32 || value > MAX_I32) {
    return "out_of_range";
  }
  return value;
}

function validateField(
  kind: FieldKind,
  value: unknown,
  loc: (string | number)[],
): { ok: true; data: unknown } | { ok: false; problem: Problem } {
  switch (kind) {
    case "str": {
      if (typeof value !== "string") {
        return {
          ok: false,
          problem: problem("string_type", loc, "Input should be a valid string"),
        };
      }
      if (Array.from(value).length > 255) {
        return {
          ok: false,
          problem: problem(
            "string_too_long",
            loc,
            "String should have at most 255 characters",
          ),
        };
      }
      return { ok: true, data: value };
    }
    case "int": {
      if (
        typeof value !== "number" ||
        !Number.isInteger(value) ||
        value < MIN_I32 ||
        value > MAX_I32
      ) {
        return {
          ok: false,
          problem: problem(
            "int_type",
            loc,
            "Input should be a valid 32-bit integer",
          ),
        };
      }
      return { ok: true, data: value };
    }
    case "float": {
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return {
          ok: false,
          problem: problem("float_type", loc, "Input should be a valid number"),
        };
      }
      return { ok: true, data: value };
    }
    case "bool": {
      if (typeof value !== "boolean") {
        return {
          ok: false,
          problem: problem("bool_type", loc, "Input should be a valid boolean"),
        };
      }
      return { ok: true, data: value };
    }
    case "date": {
      if (typeof value !== "string" || !isCalendarDate(value)) {
        return {
          ok: false,
          problem: problem("date_parsing", loc, "Input should be a valid date"),
        };
      }
      return { ok: true, data: value };
    }
  }
}

export function parseCreateFields<T>(
  raw: unknown,
  specs: readonly FieldSpec[],
  service: boolean,
  locPrefix: (string | number)[] = ["body"],
): { ok: true; value: T } | { ok: false; problems: Problem[] } {
  if (!isObject(raw)) {
    return {
      ok: false,
      problems: [
        problem(
          "model_attributes_type",
          locPrefix,
          "Input should be a valid dictionary or object to extract fields from",
        ),
      ],
    };
  }
  const problems: Problem[] = [];
  if (service) {
    const known = new Set(specs.map((s) => s.name));
    for (const key of Object.keys(raw)) {
      if (!known.has(key)) {
        problems.push(
          problem(
            "extra_forbidden",
            [...locPrefix, key],
            "Extra inputs are not permitted",
          ),
        );
      }
    }
  }
  const out: Record<string, unknown> = {};
  for (const spec of specs) {
    const val = own(raw, spec.name);
    if (val === undefined) {
      if (spec.required) {
        problems.push(
          problem("missing", [...locPrefix, spec.name], "Field required"),
        );
      } else if (spec.defaultFalse) {
        out[spec.name] = false;
      } else {
        out[spec.name] = null;
      }
    } else if (val === null) {
      if (spec.nullable) {
        out[spec.name] = null;
      } else {
        const res = validateField(spec.kind, val, [...locPrefix, spec.name]);
        if (!res.ok) {
          problems.push(res.problem);
        }
      }
    } else {
      const res = validateField(spec.kind, val, [...locPrefix, spec.name]);
      if (!res.ok) {
        problems.push(res.problem);
      } else {
        out[spec.name] = res.data;
      }
    }
  }
  if (problems.length > 0) {
    return { ok: false, problems };
  }
  return { ok: true, value: out as unknown as T };
}

/** Patch keeps only own members so Object.prototype names never count as supplied fields. */
export function parseUpdateFields<T>(
  raw: unknown,
  specs: readonly FieldSpec[],
  service: boolean,
  locPrefix: (string | number)[] = ["body"],
): { ok: true; value: T } | { ok: false; problems: Problem[] } {
  if (!isObject(raw)) {
    return {
      ok: false,
      problems: [
        problem(
          "model_attributes_type",
          locPrefix,
          "Input should be a valid dictionary or object to extract fields from",
        ),
      ],
    };
  }
  const problems: Problem[] = [];
  if (service) {
    const known = new Set(specs.map((s) => s.name));
    for (const key of Object.keys(raw)) {
      if (!known.has(key)) {
        problems.push(
          problem(
            "extra_forbidden",
            [...locPrefix, key],
            "Extra inputs are not permitted",
          ),
        );
      }
    }
  }
  const out: Record<string, unknown> = {};
  for (const spec of specs) {
    if (Object.hasOwn(raw, spec.name)) {
      const val = raw[spec.name];
      if (val === null) {
        if (spec.nullable || !service) {
          out[spec.name] = null;
        } else {
          problems.push(
            problem(
              "value_error",
              [...locPrefix, spec.name],
              `Value error, ${spec.name} cannot be null`,
            ),
          );
        }
      } else {
        const res = validateField(spec.kind, val, [...locPrefix, spec.name]);
        if (!res.ok) {
          problems.push(res.problem);
        } else {
          out[spec.name] = res.data;
        }
      }
    }
  }
  if (problems.length > 0) {
    return { ok: false, problems };
  }
  return { ok: true, value: out as unknown as T };
}
"#;

pub(super) fn field_specs_const(const_name: &str, fields: &[Field]) -> String {
    let mut out = format!("const {const_name}: readonly FieldSpec[] = [\n");
    for field in fields {
        out += &format!(
            "  {{ name: \"{}\", kind: \"{}\", required: {}, nullable: {}, defaultFalse: {} }},\n",
            field.name,
            field.kind_str(),
            field.required,
            field.nullable(),
            field.default_false
        );
    }
    out + "];\n"
}

pub(super) fn create_interface(name: &str, fields: &[Field]) -> String {
    let mut out = format!("export interface {name} {{\n");
    for field in fields {
        out += &format!("  \"{}\": {};\n", field.name, field.stored_ts_type());
    }
    out + "}\n"
}

pub(super) fn update_interface(name: &str, fields: &[Field]) -> String {
    let mut out = format!("export interface {name} {{\n");
    for field in fields {
        out += &format!(
            "  \"{}\": {} | undefined;\n",
            field.name,
            field.stored_ts_type()
        );
    }
    out + "}\n"
}

pub(super) fn schemas_read(
    name: &str,
    key: Option<&Field>,
    fields: &[Field],
    service: bool,
) -> String {
    let mut out = format!("export interface {name} {{\n  id: number;\n");
    if let Some(fk) = key {
        out += &format!("  \"{}\": number;\n", fk.name);
    }
    for field in fields {
        out += &format!("  \"{}\": {};\n", field.name, field.stored_ts_type());
    }
    if service {
        out += "  created_at: string;\n  updated_at: string;\n";
    }
    out + "}\n"
}

fn schemas_create(fields: &[Field], service: bool) -> String {
    let mut out = String::from(SCHEMA_SUPPORT);
    out.push('\n');
    out += &field_specs_const("FIELDS", fields);
    out.push('\n');
    out += &create_interface("Create", fields);
    out += &format!(
        r#"
export function parseCreate(
  raw: unknown,
): {{ ok: true; value: Create }} | {{ ok: false; problems: Problem[] }} {{
  return parseCreateFields<Create>(raw, FIELDS, {service});
}}
"#
    );
    out
}

fn schemas_update(fields: &[Field], service: bool) -> String {
    let mut out = update_interface("Update", fields);
    out += &format!(
        r#"
export function parseUpdate(
  raw: unknown,
): {{ ok: true; value: Update }} | {{ ok: false; problems: Problem[] }} {{
  return parseUpdateFields<Update>(raw, FIELDS, {service});
}}
"#
    );
    out
}

// ---------------------------------------------------------------- src/app.ts

pub(super) const APP_IMPORTS: &str = r#"import { Hono, type Context } from "hono";
import type { ContentfulStatusCode } from "hono/utils/http-status";
import type pg from "pg";
import * as db from "./db.ts";
"#;

const APP_SUPPORT: &str = r#"import {
  isCalendarDate,
  parseCreate,
  parseInt32,
  parseUpdate,
  problem,
  type Problem,
} from "./schemas.ts";

const NOT_INTEGER =
  "Input should be a valid integer, unable to parse string as an integer";

function reply(
  c: Context,
  status: ContentfulStatusCode,
  body: unknown,
): Response {
  return c.json(body, status);
}

function invalid(c: Context, problems: Problem[]): Response {
  return reply(c, 422, { detail: problems });
}

function notFound(c: Context): Response {
  return reply(c, 404, { detail: "not found" });
}

function isJsonContentType(header: string | undefined): boolean {
  if (header === undefined) {
    return true;
  }
  const media = (header.split(";")[0] ?? "").trim().toLowerCase();
  return (
    media === "application/json" ||
    (media.startsWith("application/") && media.endsWith("+json"))
  );
}

function itemId(c: Context, problems: Problem[]): number | null {
  const parsed = parseInt32(c.req.param("item_id") ?? "");
  if (parsed === "invalid") {
    problems.push(problem("int_parsing", ["path", "item_id"], NOT_INTEGER));
    return null;
  }
  return parsed === "out_of_range" ? null : parsed;
}

async function readBody(
  c: Context,
  problems: Problem[],
): Promise<{ present: true; value: unknown } | { present: false } | "invalid_json"> {
  const bytes = await c.req.arrayBuffer();
  if (bytes.byteLength === 0) {
    problems.push(problem("missing", ["body"], "Field required"));
    return { present: false };
  }
  if (!isJsonContentType(c.req.header("content-type"))) {
    problems.push(
      problem(
        "model_attributes_type",
        ["body"],
        "Input should be a valid dictionary or object to extract fields from",
      ),
    );
    return { present: false };
  }
  try {
    return {
      present: true,
      value: JSON.parse(new TextDecoder().decode(bytes)) as unknown,
    };
  } catch {
    problems.push(problem("json_invalid", ["body", 0], "JSON decode error"));
    return "invalid_json";
  }
}
"#;

pub(super) const APP_HEALTH: &str = r#"
async function health(pool: pg.Pool, c: Context): Promise<Response> {
  try {
    await db.ping(pool);
    return reply(c, 200, { status: "ok" });
  } catch {
    return reply(c, 503, { detail: "database unavailable" });
  }
}
"#;

const APP_LIST_ALL: &str = r#"
async function list(pool: pg.Pool, c: Context): Promise<Response> {
  void isCalendarDate;
  return reply(c, 200, await db.list(pool));
}
"#;

pub(super) const APP_LIST_HELPERS: &str = r#"
function queryPairs(c: Context): [string, string][] {
  return [...new URL(c.req.url).searchParams.entries()];
}

function single(
  pairs: [string, string][],
  name: string,
  problems: Problem[],
): string | undefined {
  const matches = pairs.filter(([key]) => key === name);
  if (matches.length > 1) {
    problems.push(
      problem("value_error", ["query", name], "Give this query parameter at most once"),
    );
    return undefined;
  }
  return matches[0]?.[1];
}

function queryNumber(
  text: string,
  name: string,
  low: number,
  high: number,
  problems: Problem[],
): number | undefined {
  if (!/^[+-]?[0-9]+$/.test(text)) {
    problems.push(problem("int_parsing", ["query", name], NOT_INTEGER));
    return undefined;
  }
  const value = Number(text);
  if (!Number.isSafeInteger(value)) {
    problems.push(problem("int_parsing", ["query", name], NOT_INTEGER));
    return undefined;
  }
  if (value < low) {
    problems.push(
      problem(
        "greater_than_equal",
        ["query", name],
        `Input should be greater than or equal to ${low}`,
      ),
    );
    return undefined;
  }
  if (value > high) {
    problems.push(
      problem(
        "less_than_equal",
        ["query", name],
        `Input should be less than or equal to ${high}`,
      ),
    );
    return undefined;
  }
  return value;
}
"#;

pub(super) const APP_FLAG: &str = r#"
function flag(
  text: string,
  name: string,
  problems: Problem[],
): boolean | undefined {
  const lower = text.toLowerCase();
  if (["1", "on", "t", "true", "y", "yes"].includes(lower)) {
    return true;
  }
  if (["0", "off", "f", "false", "n", "no"].includes(lower)) {
    return false;
  }
  problems.push(
    problem(
      "bool_parsing",
      ["query", name],
      "Input should be a valid boolean, unable to interpret input",
    ),
  );
  return undefined;
}
"#;

pub(super) const APP_DAY: &str = r#"
function day(
  text: string,
  name: string,
  problems: Problem[],
): string | undefined {
  if (isCalendarDate(text)) {
    return text;
  }
  problems.push(
    problem("date_from_datetime_parsing", ["query", name], "Input should be a valid date"),
  );
  return undefined;
}
"#;

fn app_list(params: &Value, filter: Filter) -> Result<String, String> {
    let max = limit(params)?;
    let mut out = String::from(APP_LIST_HELPERS);
    let (filter_lines, call) = match filter {
        Filter::Text => (
            "  void isCalendarDate;\n  const filter = single(pairs, \"filter_value\", problems) ?? null;\n".to_owned(),
            "await db.list(pool, filter, limit, offset)",
        ),
        Filter::Boolean => {
            let name = text(params, "filter_field")?;
            out += APP_FLAG;
            (
                format!(
                    "  void isCalendarDate;\n  const rawFilter = single(pairs, \"{name}\", problems);\n  const filter = rawFilter === undefined ? null : (flag(rawFilter, \"{name}\", problems) ?? null);\n"
                ),
                "await db.list(pool, filter, limit, offset)",
            )
        }
        Filter::BooleanDate => {
            let name = text(params, "filter_field")?;
            out += APP_FLAG;
            out += APP_DAY;
            (
                format!(
                    "  const rawFilter = single(pairs, \"{name}\", problems);\n  const filter = rawFilter === undefined ? null : (flag(rawFilter, \"{name}\", problems) ?? null);\n  const rawBefore = single(pairs, \"before\", problems);\n  const before = rawBefore === undefined ? null : (day(rawBefore, \"before\", problems) ?? null);\n"
                ),
                "await db.list(pool, filter, before, limit, offset)",
            )
        }
        Filter::All => return Err("app_list requires a filter".to_owned()),
    };
    out += &format!(
        r#"
const MAX_LIMIT = {max};

async function list(pool: pg.Pool, c: Context): Promise<Response> {{
  const problems: Problem[] = [];
  const pairs = queryPairs(c);
{filter_lines}  const rawLimit = single(pairs, "limit", problems);
  const limit = rawLimit === undefined ? 20 : queryNumber(rawLimit, "limit", 1, MAX_LIMIT, problems);
  const rawOffset = single(pairs, "offset", problems);
  const offset = rawOffset === undefined ? 0 : queryNumber(rawOffset, "offset", 0, Number.MAX_SAFE_INTEGER, problems);
  if (problems.length > 0 || limit === undefined || offset === undefined) {{
    return invalid(c, problems);
  }}
  return reply(c, 200, {call});
}}
"#
    );
    Ok(out)
}

const APP_CREATE: &str = r#"
async function create(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const raw = await readBody(c, problems);
  if (raw === "invalid_json" || !raw.present) {
    return invalid(c, problems);
  }
  const parsed = parseCreate(raw.value);
  if (!parsed.ok) {
    return invalid(c, parsed.problems);
  }
  try {
    return reply(c, 201, await db.insert(pool, parsed.value));
  } catch (error) {
    if (error instanceof db.Conflict) {
      return reply(c, 409, { detail: "conflict" });
    }
    throw error;
  }
}
"#;

const APP_GET: &str = r#"
async function fetchRow(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const id = itemId(c, problems);
  if (problems.length > 0) {
    return invalid(c, problems);
  }
  if (id === null) {
    return notFound(c);
  }
  const row = await db.fetch(pool, id);
  return row === null ? notFound(c) : reply(c, 200, row);
}
"#;

const APP_UPDATE: &str = r#"
async function updateRow(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const id = itemId(c, problems);
  const raw = await readBody(c, problems);
  if (raw === "invalid_json") {
    return invalid(c, problems);
  }
  const parsed = raw.present ? parseUpdate(raw.value) : null;
  if (parsed !== null && !parsed.ok) {
    problems.push(...parsed.problems);
  }
  if (problems.length > 0 || parsed === null || !parsed.ok) {
    return invalid(c, problems);
  }
  if (id === null) {
    return notFound(c);
  }
  try {
    const row = await db.update(pool, id, parsed.value);
    return row === null ? notFound(c) : reply(c, 200, row);
  } catch (error) {
    if (error instanceof db.Conflict) {
      return reply(c, 409, { detail: "conflict" });
    }
    throw error;
  }
}
"#;

const APP_DELETE: &str = r#"
async function removeRow(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const id = itemId(c, problems);
  if (problems.length > 0) {
    return invalid(c, problems);
  }
  if (id === null || !(await db.remove(pool, id))) {
    return notFound(c);
  }
  return new Response(null, {
    status: 204,
    headers: { "content-type": "application/json" },
  });
}
"#;

const APP_ROUTER: &str = r#"
export function createApp(pool: pg.Pool): Hono {
  const app = new Hono();

  app.get("/healthz", (c) => health(pool, c));
  app.all("/healthz", (c) => reply(c, 405, { detail: "Method Not Allowed" }));

  app.get("@PATH@", (c) => list(pool, c));
  app.post("@PATH@", (c) => create(pool, c));
  app.all("@PATH@", (c) => reply(c, 405, { detail: "Method Not Allowed" }));

  app.get("@PATH@/:item_id", (c) => fetchRow(pool, c));
  app.patch("@PATH@/:item_id", (c) => updateRow(pool, c));
  app.delete("@PATH@/:item_id", (c) => removeRow(pool, c));
  app.all("@PATH@/:item_id", (c) =>
    reply(c, 405, { detail: "Method Not Allowed" }),
  );

  app.notFound((c) => reply(c, 404, { detail: "Not Found" }));
  app.onError((_error, c) => reply(c, 500, { detail: "Internal Server Error" }));
  return app;
}
"#;

// ---------------------------------------------------------------- src/db.ts

const DB_PING: &str = r#"import pg from "pg";
import type { Create, Read, Update } from "./schemas.ts";

pg.types.setTypeParser(1082, (text: string) => text);
pg.types.setTypeParser(1184, (text: string) => {
  const iso = text.replace(" ", "T").replace(/([+-]\d{2})$/, "$1:00");
  const frac = (/\.(\d+)/.exec(iso)?.[1] ?? "000000").padEnd(6, "0").slice(0, 6);
  return new Date(iso).toISOString().replace(/\.\d{3}Z$/, `.${frac}Z`);
});

const PING = "SELECT 1";

export class Conflict extends Error {}

/** PostgreSQL integrity constraint violations (SQLSTATE class 23) are conflicts. */
async function run<T extends pg.QueryResultRow>(
  pool: pg.Pool,
  text: string,
  values: unknown[],
): Promise<pg.QueryResult<T>> {
  try {
    return await pool.query<T>(text, values);
  } catch (error) {
    const code: unknown =
      error instanceof Error && Object.hasOwn(error, "code")
        ? Reflect.get(error, "code")
        : undefined;
    if (typeof code === "string" && code.startsWith("23")) {
      throw new Conflict("conflict");
    }
    throw error;
  }
}

export async function ping(pool: pg.Pool): Promise<void> {
  await pool.query(PING);
}
"#;

pub(super) fn columns(fields: &[Field], service: bool) -> String {
    let mut names = vec![quoted("id")];
    names.extend(fields.iter().map(|field| quoted(&field.name)));
    if service {
        names.push(quoted("created_at"));
        names.push(quoted("updated_at"));
    }
    names.join(", ")
}

pub(super) fn to_read_fn(
    fn_name: &str,
    return_type: &str,
    key: Option<&Field>,
    fields: &[Field],
    service: bool,
) -> String {
    let mut out = format!(
        "function {fn_name}(row: Record<string, unknown>): {return_type} {{\n  return {{\n    id: row[\"id\"] as number,\n"
    );
    if let Some(fk) = key {
        out += &format!("    \"{}\": row[\"{}\"] as number,\n", fk.name, fk.name);
    }
    for field in fields {
        out += &format!(
            "    \"{}\": row[\"{}\"] as {},\n",
            field.name,
            field.name,
            field.stored_ts_type()
        );
    }
    if service {
        out += "    created_at: row[\"created_at\"] as string,\n    updated_at: row[\"updated_at\"] as string,\n";
    }
    out + "  };\n}\n"
}

fn db_insert(params: &Value, service: bool) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let table = text(params, "table")?;
    let names: Vec<String> = fields.iter().map(|field| quoted(&field.name)).collect();
    let slots: Vec<String> = (1..=fields.len()).map(|i| format!("${i}")).collect();
    let args: Vec<String> = fields
        .iter()
        .map(|field| format!("body[\"{}\"]", field.name))
        .collect();
    let mut out = format!(
        "\nconst INSERT = `INSERT INTO {} ({}) VALUES ({}) RETURNING {}`;\n\n",
        quoted(table),
        names.join(", "),
        slots.join(", "),
        columns(&fields, service)
    );
    out += &to_read_fn("toRead", "Read", None, &fields, service);
    out += &format!(
        r#"
export async function insert(pool: pg.Pool, body: Create): Promise<Read> {{
  const result = await run(pool, INSERT, [{}]);
  return toRead(result.rows[0] as Record<string, unknown>);
}}
"#,
        args.join(", ")
    );
    Ok(out)
}

fn db_fetch(params: &Value, service: bool) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let table = text(params, "table")?;
    Ok(format!(
        r#"
const FETCH = `SELECT {} FROM {} WHERE "id" = $1`;

export async function fetch(pool: pg.Pool, id: number): Promise<Read | null> {{
  const result = await run(pool, FETCH, [id]);
  const row = result.rows[0];
  return row === undefined ? null : toRead(row);
}}
"#,
        columns(&fields, service),
        quoted(table)
    ))
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
        Filter::All => format!(
            r#"
const LIST = `{select} ORDER BY "id"`;

export async function list(pool: pg.Pool): Promise<Read[]> {{
  const result = await run(pool, LIST, []);
  return result.rows.map(toRead);
}}
"#
        ),
        Filter::Text | Filter::Boolean => {
            let (cast, ts_type) = if filter == Filter::Text {
                ("text", "string | null")
            } else {
                ("boolean", "boolean | null")
            };
            let field = quoted(text(params, "filter_field")?);
            format!(
                r#"
const LIST = `{select} WHERE ($1::{cast} IS NULL OR {field} = $1) ORDER BY "id" LIMIT $2 OFFSET $3`;

export async function list(
  pool: pg.Pool,
  filter: {ts_type},
  limit: number,
  offset: number,
): Promise<Read[]> {{
  const result = await run(pool, LIST, [filter, limit, offset]);
  return result.rows.map(toRead);
}}
"#
            )
        }
        Filter::BooleanDate => {
            let field = quoted(text(params, "filter_field")?);
            let date = quoted(text(params, "date_field")?);
            format!(
                r#"
const LIST = `{select} WHERE ($1::boolean IS NULL OR {field} = $1) AND ($2::date IS NULL OR {date} < $2) ORDER BY "id" LIMIT $3 OFFSET $4`;

export async function list(
  pool: pg.Pool,
  filter: boolean | null,
  before: string | null,
  limit: number,
  offset: number,
): Promise<Read[]> {{
  const result = await run(pool, LIST, [filter, before, limit, offset]);
  return result.rows.map(toRead);
}}
"#
            )
        }
    })
}

fn db_update(params: &Value, service: bool) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let table = text(params, "table")?;
    let mut sets = Vec::new();
    let mut flags = Vec::new();
    let mut binds = vec!["id".to_owned()];
    for (index, field) in fields.iter().enumerate() {
        let column = quoted(&field.name);
        let flag = 2 + 2 * index;
        sets.push(format!(
            "{column} = CASE WHEN ${flag} THEN ${} ELSE {column} END",
            flag + 1
        ));
        flags.push(format!("${flag}"));
        binds.push(format!("Object.hasOwn(body, \"{}\")", field.name));
        binds.push(format!(
            "Object.hasOwn(body, \"{0}\") ? body[\"{0}\"] : null",
            field.name
        ));
    }
    if service {
        sets.push(format!(
            "\"updated_at\" = CASE WHEN {} THEN now() ELSE \"updated_at\" END",
            flags.join(" OR ")
        ));
    }
    Ok(format!(
        r#"
const UPDATE = `UPDATE {} SET {} WHERE "id" = $1 RETURNING {}`;

/** Each member binds whether it was present, then its value. Absent members keep the column. */
export async function update(
  pool: pg.Pool,
  id: number,
  body: Update,
): Promise<Read | null> {{
  const result = await run(pool, UPDATE, [{}]);
  const row = result.rows[0];
  return row === undefined ? null : toRead(row);
}}
"#,
        quoted(table),
        sets.join(", "),
        columns(&fields, service),
        binds.join(", ")
    ))
}

fn db_delete(params: &Value) -> Result<String, String> {
    let table = text(params, "table")?;
    Ok(format!(
        r#"
const DELETE = `DELETE FROM {} WHERE "id" = $1`;

export async function remove(pool: pg.Pool, id: number): Promise<boolean> {{
  const result = await run(pool, DELETE, [id]);
  return (result.rowCount ?? 0) > 0;
}}
"#,
        quoted(table)
    ))
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

pub(super) fn column_line(field: &Field) -> String {
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

// ---------------------------------------------------------------- test/api.test.ts

pub(super) fn sample_fns(role: &str, fields: &[Field]) -> String {
    let prefix = if role.is_empty() {
        String::new()
    } else {
        format!("{role}_")
    };
    let sample_entries: Vec<String> = fields
        .iter()
        .enumerate()
        .map(|(i, f)| format!("    \"{}\": {},", f.name, f.sample(i)))
        .collect();
    let minimal_entries: Vec<String> = fields
        .iter()
        .enumerate()
        .filter(|(_, f)| f.required)
        .map(|(i, f)| format!("    \"{}\": {},", f.name, f.sample(i)))
        .collect();
    let default_entries: Vec<String> = fields
        .iter()
        .enumerate()
        .map(|(i, f)| {
            let val = if f.required {
                f.sample(i)
            } else if f.default_false {
                "false".to_owned()
            } else {
                "null".to_owned()
            };
            format!("    \"{}\": {val},", f.name)
        })
        .collect();

    format!(
        r#"function {prefix}sample(k: number): Record<string, unknown> {{
  void k;
  return {{
{}
  }};
}}

function {prefix}minimal(k: number): Record<string, unknown> {{
  void k;
  return {{
{}
  }};
}}

function {prefix}defaults(k: number): Record<string, unknown> {{
  void k;
  return {{
{}
  }};
}}
"#,
        sample_entries.join("\n"),
        minimal_entries.join("\n"),
        default_entries.join("\n")
    )
}

pub(super) const HARNESS_HEAD: &str = r#"import assert from "node:assert/strict";
import { after, before, beforeEach, test } from "node:test";
import pg from "pg";
import { createApp } from "../src/app.ts";
import { migrate } from "../src/migrate.ts";

const url = process.env["TEST_DATABASE_URL"];
assert.ok(url !== undefined && url !== "", "TEST_DATABASE_URL is required");

const pool = new pg.Pool({ connectionString: url });
const app = createApp(pool);

before(async () => {
  await pool.query("SELECT 1");
});

beforeEach(async () => {
  await pool.query("DROP SCHEMA public CASCADE");
  await pool.query("CREATE SCHEMA public");
  await migrate(pool);
});

after(async () => {
  await pool.end();
});

async function call(
  method: string,
  path: string,
  body?: unknown,
  contentType = "application/json",
): Promise<{ status: number; body: unknown; contentType: string | null }> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init["body"] = typeof body === "string" ? body : JSON.stringify(body);
    init["headers"] = { "content-type": contentType };
  }
  const response = await app.request(path, init);
  const text = await response.text();
  return {
    status: response.status,
    body: text === "" ? null : (JSON.parse(text) as unknown),
    contentType: response.headers.get("content-type"),
  };
}

function locs(body: unknown): string[] {
  const detail = (body as { detail: { loc: (string | number)[] }[] }).detail;
  return [...new Set(detail.map((d) => String(d.loc[0])))].sort();
}

function stripStamps(row: Record<string, unknown>): Record<string, unknown> {
  const copy = { ...row };
  delete copy["created_at"];
  delete copy["updated_at"];
  return copy;
}
"#;

fn tests_harness(params: &Value) -> Result<String, String> {
    let fields = parse_fields(&params["fields"])?;
    let path = text(params, "path")?;
    let rows = if fields
        .iter()
        .any(|f| f.kind == Kind::Bool && f.unique && !f.nullable())
    {
        2
    } else {
        25
    };
    let mut out = String::from(HARNESS_HEAD);
    out += &format!(
        r#"
const PATH = "{path}";
const ROWS = {rows};

function item(row: Record<string, unknown>): string {{
  return `${{PATH}}/${{String(row["id"])}}`;
}}

"#
    );
    out += &sample_fns("", &fields);
    out += r#"
async function createRow(k: number): Promise<Record<string, unknown>> {
  const res = await call("POST", PATH, sample(k));
  assert.equal(res.status, 201);
  return res.body as Record<string, unknown>;
}
"#;
    Ok(out)
}

pub(super) const TESTS_HEALTH: &str = r#"
test("healthz answers ok", async () => {
  const res = await call("GET", "/healthz");
  assert.equal(res.status, 200);
  assert.deepEqual(res.body, { status: "ok" });
});
"#;

fn tests_crud(_fields: &[Field], service: bool) -> String {
    let stamp_check = if service {
        "  assert.equal(typeof created[\"created_at\"], \"string\");\n  assert.equal(typeof created[\"updated_at\"], \"string\");\n"
    } else {
        ""
    };
    format!(
        r#"
test("CRUD round trip", async () => {{
  void ROWS;
  const created = await createRow(0);
  const wanted: Record<string, unknown> = {{ ...sample(0), id: created["id"] }};
  assert.deepEqual(stripStamps(created), wanted);
{stamp_check}  const uri = item(created);
  assert.deepEqual(await call("GET", uri), {{
    status: 200,
    body: created,
    contentType: "application/json",
  }});
  const listed = await call("GET", PATH);
  assert.equal(listed.status, 200);
  assert.deepEqual(listed.body, [created]);
  for (const [name, value] of Object.entries(sample(1))) {{
    const patched = await call("PATCH", uri, {{ [name]: value }});
    assert.equal(patched.status, 200);
    wanted[name] = value;
    assert.deepEqual(stripStamps(patched.body as Record<string, unknown>), wanted);
  }}
  const unchanged = await call("PATCH", uri, {{}});
  assert.equal(unchanged.status, 200);
  const del = await call("DELETE", uri);
  assert.equal(del.status, 204);
  assert.equal(del.body, null);
  for (const method of ["GET", "DELETE"] as const) {{
    const missing = await call(method, uri);
    assert.equal(missing.status, 404);
    assert.deepEqual(missing.body, {{ detail: "not found" }});
  }}
  const minRes = await call("POST", PATH, minimal(2));
  assert.equal(minRes.status, 201);
  const minBody = minRes.body as Record<string, unknown>;
  assert.deepEqual(stripStamps(minBody), {{ ...defaults(2), id: minBody["id"] }});
}});

test("migrate is idempotent", async () => {{
  await migrate(pool);
  const rows = await pool.query(
    `SELECT "version", "revision" FROM "_som_ts_migrations"`,
  );
  assert.equal(rows.rows.length, 1);
}});
"#
    )
}

fn tests_validation(fields: &[Field], service: bool) -> String {
    let first = &fields[0].name;
    let extra_check = if service {
        "  const extra = await call(\"POST\", PATH, { ...sample(0), zz_unknown: 1 });\n  assert.equal(extra.status, 422);\n  assert.deepEqual(locs(extra.body), [\"body\"]);\n"
    } else {
        "  const extra = await call(\"POST\", PATH, { ...sample(0), zz_unknown: 1 });\n  assert.equal(extra.status, 201);\n  await call(\"DELETE\", item(extra.body as Record<string, unknown>));\n"
    };
    format!(
        r#"
test("invalid requests are 422 and out-of-range ids are 404", async () => {{
  for (const [ct, raw] of [
    ["application/json", "{{"],
    ["application/json", "[]"],
    ["application/json", "1"],
    ["text/plain", JSON.stringify(sample(0))],
  ] as const) {{
    const res = await call("POST", PATH, raw, ct);
    assert.equal(res.status, 422);
    assert.deepEqual(locs(res.body), ["body"]);
  }}
  const wrong = await call("POST", PATH, {{ ...sample(0), "{first}": {{}} }});
  assert.equal(wrong.status, 422);
  assert.deepEqual(locs(wrong.body), ["body"]);
{extra_check}  for (const badId of ["abc", "1.5", "%201", "1_0"]) {{
    const res = await call("GET", `${{PATH}}/${{badId}}`);
    assert.equal(res.status, 422);
    assert.deepEqual(locs(res.body), ["path"]);
  }}
  for (const bigId of ["2147483648", "-2147483649", "99999999999"]) {{
    const res = await call("GET", `${{PATH}}/${{bigId}}`);
    assert.equal(res.status, 404);
    assert.deepEqual(res.body, {{ detail: "not found" }});
  }}
  const combined = await call("PATCH", `${{PATH}}/abc`, "{{}}", "text/plain");
  assert.equal(combined.status, 422);
  assert.deepEqual(locs(combined.body), ["body", "path"]);
}});
"#
    )
}

fn tests_unique(fields: &[Field]) -> String {
    let unique: Vec<String> = fields
        .iter()
        .filter(|f| f.unique)
        .map(|f| format!("\"{}\"", f.name))
        .collect();
    if unique.is_empty() {
        return r#"
test("duplicate values are allowed", async () => {
  const first = await createRow(0);
  const second = await createRow(0);
  assert.notEqual(first["id"], second["id"]);
});
"#
        .to_owned();
    }
    format!(
        r#"
const UNIQUE_FIELDS: readonly string[] = [{}];

test("unique values conflict", async () => {{
  const first = await createRow(0);
  const second = await createRow(1);
  for (const name of UNIQUE_FIELDS) {{
    const conflictCreate = await call("POST", PATH, {{
      ...sample(1),
      [name]: first[name],
    }});
    assert.equal(conflictCreate.status, 409);
    assert.deepEqual(conflictCreate.body, {{ detail: "conflict" }});
    const conflictPatch = await call("PATCH", item(second), {{
      [name]: first[name],
    }});
    assert.equal(conflictPatch.status, 409);
    assert.deepEqual(conflictPatch.body, {{ detail: "conflict" }});
  }}
}});
"#,
        unique.join(", ")
    )
}

const TESTS_LIST_ALL: &str = r#"
test("list returns every row in id order", async () => {
  const rows: Record<string, unknown>[] = [];
  for (let k = 0; k < ROWS; k += 1) {
    rows.push(await createRow(k));
  }
  const res = await call("GET", PATH);
  assert.equal(res.status, 200);
  assert.deepEqual(res.body, rows);
});
"#;

fn tests_list(params: &Value, filter: Filter) -> Result<String, String> {
    let max = limit(params)?;
    let over = max + 1;
    let extra = match filter {
        Filter::Text => {
            let name = text(params, "filter_field")?;
            format!(
                r#"  const targetVal = String(rows[0]!["{name}"]);
  const filtered = await call("GET", `${{PATH}}?filter_value=${{encodeURIComponent(targetVal)}}`);
  assert.equal(filtered.status, 200);
  const dupFilter = await call("GET", `${{PATH}}?filter_value=a&filter_value=b`);
  assert.equal(dupFilter.status, 422);
"#
            )
        }
        Filter::Boolean => {
            let name = text(params, "filter_field")?;
            format!(
                r#"  const filtered = await call("GET", `${{PATH}}?{name}=true`);
  assert.equal(filtered.status, 200);
  const badBool = await call("GET", `${{PATH}}?{name}=maybe`);
  assert.equal(badBool.status, 422);
"#
            )
        }
        Filter::BooleanDate => {
            let name = text(params, "filter_field")?;
            format!(
                r#"  const filtered = await call("GET", `${{PATH}}?{name}=true&before=2030-01-15`);
  assert.equal(filtered.status, 200);
  const badDate = await call("GET", `${{PATH}}?before=2030-02-30`);
  assert.equal(badDate.status, 422);
"#
            )
        }
        Filter::All => return Err("tests_list requires a filter".to_owned()),
    };
    Ok(format!(
        r#"
test("list pagination and query validation", async () => {{
  const rows: Record<string, unknown>[] = [];
  for (let k = 0; k < ROWS; k += 1) {{
    rows.push(await createRow(k));
  }}
  const firstPage = await call("GET", PATH);
  assert.equal(firstPage.status, 200);
  assert.deepEqual(firstPage.body, rows.slice(0, 20));
  for (const q of ["limit=0", "limit={over}", "offset=-1", "limit=abc", "limit=1&limit=2", "limit=1.0"]) {{
    const bad = await call("GET", `${{PATH}}?${{q}}`);
    assert.equal(bad.status, 422);
    assert.deepEqual(locs(bad.body), ["query"]);
  }}
{extra}}});
"#
    ))
}

const TESTS_FALLBACK: &str = r#"
test("fallbacks are JSON", async () => {
  for (const uri of ["/_missing", `${PATH}/`, `${PATH}/1/`]) {
    const res = await call("GET", uri);
    assert.equal(res.status, 404);
    assert.deepEqual(res.body, { detail: "Not Found" });
  }
  for (const [method, uri] of [
    ["PUT", PATH],
    ["DELETE", PATH],
    ["POST", `${PATH}/1`],
  ] as const) {
    const res = await call(method, uri);
    assert.equal(res.status, 405);
    assert.deepEqual(res.body, { detail: "Method Not Allowed" });
  }
});
"#;

// ---------------------------------------------------------------- README.md

fn readme(params: &Value, filter: Filter) -> Result<String, String> {
    let package = text(params, "package")?;
    let version = text(params, "version")?;
    let path = text(params, "path")?;
    let revision = text(params, "revision")?;
    let down = params["down_revision"].as_str().unwrap_or("none");
    let number = version.strip_prefix("som.prompt.v").unwrap_or(version);
    let query_note = match filter {
        Filter::All => String::new(),
        Filter::Text => format!(
            "\nQuery parameters on `GET {path}`: `filter_value` (exact `{}`), `limit` (1..{}), `offset`.\n",
            text(params, "filter_field")?,
            limit(params)?
        ),
        Filter::Boolean => format!(
            "\nQuery parameters on `GET {path}`: `{}` (boolean), `limit` (1..{}), `offset`.\n",
            text(params, "filter_field")?,
            limit(params)?
        ),
        Filter::BooleanDate => format!(
            "\nQuery parameters on `GET {path}`: `{}` (boolean), `before` (`{}` < date), `limit` (1..{}), `offset`.\n",
            text(params, "filter_field")?,
            text(params, "date_field")?,
            limit(params)?
        ),
    };
    Ok(format!(
        r#"# {package}

Generated from a SOM prompt, package `{package}`, prompt version {number} (migration `{revision}`, down `{down}`).

## Routes

- `GET /healthz`
- `GET {path}`
- `POST {path}`
- `GET {path}/{{item_id}}`
- `PATCH {path}/{{item_id}}`
- `DELETE {path}/{{item_id}}`
{query_note}
## Commands

Install, offline from the vendored npm cache:

```
npm ci --offline --ignore-scripts --no-audit --no-fund
```

Run, with `DATABASE_URL` set and `SOM_APP_ADDR` optional (default `127.0.0.1:8000`):

```
node src/main.ts
```

Test, with `TEST_DATABASE_URL` set:

```
node --test --test-concurrency=1 test/api.test.ts
```

Type-check:

```
node node_modules/typescript/bin/tsc -p tsconfig.json
```

Format check:

```
node node_modules/prettier/bin/prettier.cjs --check --no-config --no-editorconfig src/main.ts src/app.ts src/schemas.ts src/db.ts src/migrate.ts test/api.test.ts
```
"#
    ))
}
