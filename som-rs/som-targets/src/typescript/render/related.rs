//! The v5 TypeScript blocks: a parent and a child resource joined by a restricted foreign key,
//! the scoped child routes, and the atomic batch of one parent with 1 to 20 children.
use serde_json::Value;

use super::{
    APP_DAY, APP_FLAG, APP_IMPORTS, APP_LIST_HELPERS, Field, HARNESS_HEAD, Kind, SCHEMA_SUPPORT,
    column_line, create_interface, field_specs_const, parse_fields, quoted, sample_fns,
    schemas_read, text, to_read_fn, update_interface,
};

pub(super) struct Resource {
    pub(super) table: String,
    pub(super) path: String,
    pub(super) fields: Vec<Field>,
    pub(super) filter: Option<String>,
    pub(super) date: Option<String>,
    pub(super) max_limit: i64,
}

impl Resource {
    pub(super) fn read(params: &Value, key: &str) -> Result<Self, String> {
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

    fn rows(&self) -> usize {
        if self
            .fields
            .iter()
            .any(|f| f.kind == Kind::Bool && f.unique && !f.nullable())
        {
            2
        } else {
            25
        }
    }

    fn unique_value(&self) -> Option<&Field> {
        self.fields
            .iter()
            .find(|f| f.unique && f.kind != Kind::Bool)
    }
}

fn foreign_key(params: &Value) -> Result<Field, String> {
    let name = text(params, "foreign_key")?.to_owned();
    Ok(Field {
        name,
        kind: Kind::Int,
        required: true,
        unique: false,
        default_false: false,
    })
}

pub fn render(id: &str, params: &Value) -> Result<String, String> {
    let parent = || Resource::read(params, "parent");
    let child = || Resource::read(params, "child");
    Ok(match id {
        "ts.schemas.related.support" => SCHEMA_SUPPORT.to_owned(),
        "ts.schemas.related.parent" => schemas_resource("Parent", None, &parent()?),
        "ts.schemas.related.child" => {
            schemas_resource("Child", Some(&foreign_key(params)?), &child()?)
        }
        "ts.schemas.related.batch" => SCHEMAS_BATCH.to_owned(),

        "ts.app.related.support" => [APP_IMPORTS, APP_RELATED_SUPPORT].concat(),
        "ts.app.related.list" => app_list(&parent()?, &child()?),
        "ts.app.related.parent" => APP_PARENT.to_owned(),
        "ts.app.related.child" => APP_CHILD.to_owned(),
        "ts.app.related.router" => APP_ROUTER
            .replace("@PARENTS@", text(params, "parent_path")?)
            .replace("@CHILDREN@", text(params, "child_path")?),

        "ts.db.related.ping" => DB_PING.to_owned(),
        "ts.db.related.parent" => db_parent(&parent()?),
        "ts.db.related.child" => db_child(&child()?, &foreign_key(params)?),

        "ts.migration.related.parent" => migration_parent(params, &parent()?)?,
        "ts.migration.related.child" => migration_child(params, &child()?, &foreign_key(params)?)?,

        "ts.tests.related.harness" => tests_harness(&parent()?, &child()?, params)?,
        "ts.tests.related.crud" => TESTS_CRUD.to_owned(),
        "ts.tests.related.validation" => tests_validation(&parent()?, &child()?),
        "ts.tests.related.unique" => tests_unique(&parent()?, &child()?),
        "ts.tests.related.list" => tests_list(&parent()?, &child()?),
        "ts.tests.related.scope" => TESTS_SCOPE.to_owned(),
        "ts.tests.related.batch" => tests_batch(&parent()?, &child()?),
        "ts.tests.related.fallback" => TESTS_FALLBACK.to_owned(),

        "ts.readme.related" => readme(params, &parent()?, &child()?)?,
        _ => return Err(format!("no renderer for block {id}")),
    })
}

// ---------------------------------------------------------------- src/schemas.ts

fn schemas_resource(role: &str, key: Option<&Field>, resource: &Resource) -> String {
    let const_name = format!("{}_FIELDS", role.to_ascii_uppercase());
    let mut out = String::from("\n");
    out += &field_specs_const(&const_name, &resource.fields);
    out.push('\n');
    out += &create_interface(&format!("{role}Create"), &resource.fields);
    out.push('\n');
    out += &update_interface(&format!("{role}Update"), &resource.fields);
    out.push('\n');
    out += &schemas_read(&format!("{role}Read"), key, &resource.fields, true);
    out += &format!(
        r#"
export function parse{role}Create(
  raw: unknown,
  locPrefix: (string | number)[] = ["body"],
): {{ ok: true; value: {role}Create }} | {{ ok: false; problems: Problem[] }} {{
  return parseCreateFields<{role}Create>(raw, {const_name}, true, locPrefix);
}}

export function parse{role}Update(
  raw: unknown,
  locPrefix: (string | number)[] = ["body"],
): {{ ok: true; value: {role}Update }} | {{ ok: false; problems: Problem[] }} {{
  return parseUpdateFields<{role}Update>(raw, {const_name}, true, locPrefix);
}}
"#
    );
    out
}

const SCHEMAS_BATCH: &str = r#"
export interface ParentWithChildrenCreate {
  parent: ParentCreate;
  children: ChildCreate[];
}

export interface ParentWithChildrenRead {
  parent: ParentRead;
  children: ChildRead[];
}

export function parseBatch(
  raw: unknown,
): { ok: true; value: ParentWithChildrenCreate } | { ok: false; problems: Problem[] } {
  if (!isObject(raw)) {
    return {
      ok: false,
      problems: [
        problem(
          "model_attributes_type",
          ["body"],
          "Input should be a valid dictionary or object to extract fields from",
        ),
      ],
    };
  }
  const problems: Problem[] = [];
  for (const key of Object.keys(raw)) {
    if (key !== "parent" && key !== "children") {
      problems.push(
        problem("extra_forbidden", ["body", key], "Extra inputs are not permitted"),
      );
    }
  }
  let parentVal: ParentCreate | undefined;
  if (!Object.hasOwn(raw, "parent")) {
    problems.push(problem("missing", ["body", "parent"], "Field required"));
  } else {
    const res = parseParentCreate(raw["parent"], ["body", "parent"]);
    if (!res.ok) {
      problems.push(...res.problems);
    } else {
      parentVal = res.value;
    }
  }
  const childrenVal: ChildCreate[] = [];
  if (!Object.hasOwn(raw, "children")) {
    problems.push(problem("missing", ["body", "children"], "Field required"));
  } else if (!Array.isArray(raw["children"])) {
    problems.push(
      problem("list_type", ["body", "children"], "Input should be a valid list"),
    );
  } else {
    const list = raw["children"];
    if (list.length === 0) {
      problems.push(
        problem(
          "too_short",
          ["body", "children"],
          "List should have at least 1 item after validation, not 0",
        ),
      );
    }
    if (list.length > 20) {
      problems.push(
        problem(
          "too_long",
          ["body", "children"],
          `List should have at most 20 items after validation, not ${list.length}`,
        ),
      );
    }
    for (let i = 0; i < list.length; i += 1) {
      const childRes = parseChildCreate(list[i], ["body", "children", i]);
      if (!childRes.ok) {
        problems.push(...childRes.problems);
      } else {
        childrenVal.push(childRes.value);
      }
    }
  }
  if (problems.length > 0 || parentVal === undefined) {
    return { ok: false, problems };
  }
  return { ok: true, value: { parent: parentVal, children: childrenVal } };
}
"#;

// ---------------------------------------------------------------- src/app.ts

const APP_RELATED_SUPPORT: &str = r#"import {
  isCalendarDate,
  parseBatch,
  parseChildCreate,
  parseChildUpdate,
  parseInt32,
  parseParentCreate,
  parseParentUpdate,
  problem,
  type Problem,
} from "./schemas.ts";

const NOT_INTEGER =
  "Input should be a valid integer, unable to parse string as an integer";
const CONFLICT_DETAIL = "relationship or unique value conflict";

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

function parentNotFound(c: Context): Response {
  return reply(c, 404, { detail: "parent not found" });
}

function childNotFound(c: Context): Response {
  return reply(c, 404, { detail: "child not found" });
}

function conflict(c: Context): Response {
  return reply(c, 409, { detail: CONFLICT_DETAIL });
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

function pathId(c: Context, name: string, problems: Problem[]): number | null {
  const parsed = parseInt32(c.req.param(name) ?? "");
  if (parsed === "invalid") {
    problems.push(problem("int_parsing", ["path", name], NOT_INTEGER));
    return null;
  }
  return parsed === "out_of_range" ? null : parsed;
}

async function knownParent(
  pool: pg.Pool,
  id: number | null,
): Promise<number | null> {
  if (id === null) {
    return null;
  }
  return (await db.parentExists(pool, id)) ? id : null;
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

fn query_lines(resource: &Resource, max_const: &str) -> (String, String) {
    let mut out = String::new();
    let mut args = String::new();
    if let Some(name) = &resource.filter {
        out += &format!(
            "  const rawFilter = single(pairs, \"{name}\", problems);\n  const filter = rawFilter === undefined ? null : (flag(rawFilter, \"{name}\", problems) ?? null);\n"
        );
        args += "filter, ";
    }
    if resource.date.is_some() {
        out += "  const rawBefore = single(pairs, \"before\", problems);\n  const before = rawBefore === undefined ? null : (day(rawBefore, \"before\", problems) ?? null);\n";
        args += "before, ";
    }
    out += &format!(
        r#"  const rawLimit = single(pairs, "limit", problems);
  const limit = rawLimit === undefined ? 20 : queryNumber(rawLimit, "limit", 1, {max_const}, problems);
  const rawOffset = single(pairs, "offset", problems);
  const offset = rawOffset === undefined ? 0 : queryNumber(rawOffset, "offset", 0, Number.MAX_SAFE_INTEGER, problems);
  if (problems.length > 0 || limit === undefined || offset === undefined) {{
    return invalid(c, problems);
  }}
"#
    );
    (out, args)
}

fn app_list(parent: &Resource, child: &Resource) -> String {
    let mut out = String::from(APP_LIST_HELPERS);
    if parent.filter.is_some() || child.filter.is_some() {
        out += APP_FLAG;
    }
    if parent.date.is_some() || child.date.is_some() {
        out += APP_DAY;
    }
    out += &format!(
        r#"
const PARENT_MAX_LIMIT = {};
const CHILD_MAX_LIMIT = {};

async function listParents(pool: pg.Pool, c: Context): Promise<Response> {{
  void isCalendarDate;
  const problems: Problem[] = [];
  const pairs = queryPairs(c);
"#,
        parent.max_limit, child.max_limit
    );
    let (p_lines, p_args) = query_lines(parent, "PARENT_MAX_LIMIT");
    out += &p_lines;
    out += &format!(
        r#"  return reply(c, 200, await db.listParents(pool, {p_args}limit, offset));
}}

/** Query problems come before a missing parent, as in the Python product. */
async function listChildren(pool: pg.Pool, c: Context): Promise<Response> {{
  void isCalendarDate;
  const problems: Problem[] = [];
  const parentId = pathId(c, "parent_id", problems);
  const pairs = queryPairs(c);
"#
    );
    let (c_lines, c_args) = query_lines(child, "CHILD_MAX_LIMIT");
    out += &c_lines;
    out + &format!(
        r#"  const parent = await knownParent(pool, parentId);
  if (parent === null) {{
    return parentNotFound(c);
  }}
  return reply(c, 200, await db.listChildren(pool, parent, {c_args}limit, offset));
}}
"#
    )
}

const APP_PARENT: &str = r#"
async function createParent(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const raw = await readBody(c, problems);
  if (raw === "invalid_json" || !raw.present) {
    return invalid(c, problems);
  }
  const parsed = parseParentCreate(raw.value);
  if (!parsed.ok) {
    return invalid(c, parsed.problems);
  }
  try {
    return reply(c, 201, await db.insertParent(pool, parsed.value));
  } catch (error) {
    if (error instanceof db.Conflict) {
      return conflict(c);
    }
    throw error;
  }
}

async function fetchParent(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const id = pathId(c, "parent_id", problems);
  if (problems.length > 0) {
    return invalid(c, problems);
  }
  if (id === null) {
    return parentNotFound(c);
  }
  const row = await db.fetchParent(pool, id);
  return row === null ? parentNotFound(c) : reply(c, 200, row);
}

async function updateParent(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const id = pathId(c, "parent_id", problems);
  const raw = await readBody(c, problems);
  if (raw === "invalid_json") {
    return invalid(c, problems);
  }
  const parsed = raw.present ? parseParentUpdate(raw.value) : null;
  if (parsed !== null && !parsed.ok) {
    problems.push(...parsed.problems);
  }
  if (problems.length > 0 || parsed === null || !parsed.ok) {
    return invalid(c, problems);
  }
  if (id === null) {
    return parentNotFound(c);
  }
  try {
    const row = await db.updateParent(pool, id, parsed.value);
    return row === null ? parentNotFound(c) : reply(c, 200, row);
  } catch (error) {
    if (error instanceof db.Conflict) {
      return conflict(c);
    }
    throw error;
  }
}

async function removeParent(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const id = pathId(c, "parent_id", problems);
  if (problems.length > 0) {
    return invalid(c, problems);
  }
  if (id === null) {
    return parentNotFound(c);
  }
  try {
    if (!(await db.deleteParent(pool, id))) {
      return parentNotFound(c);
    }
    return new Response(null, { status: 204 });
  } catch (error) {
    if (error instanceof db.Conflict) {
      return conflict(c);
    }
    throw error;
  }
}

async function createBatch(pool: pg.Pool, c: Context): Promise<Response> {
  if (c.req.param("parent_id") !== "with-children") {
    return reply(c, 405, { detail: "Method Not Allowed" });
  }
  const problems: Problem[] = [];
  const raw = await readBody(c, problems);
  if (raw === "invalid_json" || !raw.present) {
    return invalid(c, problems);
  }
  const parsed = parseBatch(raw.value);
  if (!parsed.ok) {
    return invalid(c, parsed.problems);
  }
  try {
    return reply(c, 201, await db.insertBatch(pool, parsed.value));
  } catch (error) {
    if (error instanceof db.Conflict) {
      return conflict(c);
    }
    throw error;
  }
}
"#;

const APP_CHILD: &str = r#"
function childIds(
  c: Context,
  problems: Problem[],
): [number | null, number | null] {
  return [
    pathId(c, "parent_id", problems),
    pathId(c, "child_id", problems),
  ];
}

async function createChild(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const parentId = pathId(c, "parent_id", problems);
  const raw = await readBody(c, problems);
  if (raw === "invalid_json") {
    return invalid(c, problems);
  }
  const parsed = raw.present ? parseChildCreate(raw.value) : null;
  if (parsed !== null && !parsed.ok) {
    problems.push(...parsed.problems);
  }
  if (problems.length > 0 || parsed === null || !parsed.ok) {
    return invalid(c, problems);
  }
  const parent = await knownParent(pool, parentId);
  if (parent === null) {
    return parentNotFound(c);
  }
  try {
    return reply(c, 201, await db.insertChild(pool, parent, parsed.value));
  } catch (error) {
    if (error instanceof db.Conflict) {
      return error.code === "23503" ? parentNotFound(c) : conflict(c);
    }
    throw error;
  }
}

async function fetchChild(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const [parentId, childId] = childIds(c, problems);
  if (problems.length > 0) {
    return invalid(c, problems);
  }
  const parent = await knownParent(pool, parentId);
  if (parent === null) {
    return parentNotFound(c);
  }
  if (childId === null) {
    return childNotFound(c);
  }
  const row = await db.fetchChild(pool, childId, parent);
  return row === null ? childNotFound(c) : reply(c, 200, row);
}

async function updateChild(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const [parentId, childId] = childIds(c, problems);
  const raw = await readBody(c, problems);
  if (raw === "invalid_json") {
    return invalid(c, problems);
  }
  const parsed = raw.present ? parseChildUpdate(raw.value) : null;
  if (parsed !== null && !parsed.ok) {
    problems.push(...parsed.problems);
  }
  if (problems.length > 0 || parsed === null || !parsed.ok) {
    return invalid(c, problems);
  }
  const parent = await knownParent(pool, parentId);
  if (parent === null) {
    return parentNotFound(c);
  }
  if (childId === null) {
    return childNotFound(c);
  }
  try {
    const row = await db.updateChild(pool, childId, parent, parsed.value);
    return row === null ? childNotFound(c) : reply(c, 200, row);
  } catch (error) {
    if (error instanceof db.Conflict) {
      return conflict(c);
    }
    throw error;
  }
}

async function removeChild(pool: pg.Pool, c: Context): Promise<Response> {
  const problems: Problem[] = [];
  const [parentId, childId] = childIds(c, problems);
  if (problems.length > 0) {
    return invalid(c, problems);
  }
  const parent = await knownParent(pool, parentId);
  if (parent === null) {
    return parentNotFound(c);
  }
  if (childId === null || !(await db.deleteChild(pool, childId, parent))) {
    return childNotFound(c);
  }
  return new Response(null, { status: 204 });
}
"#;

const APP_ROUTER: &str = r#"
export function createApp(pool: pg.Pool): Hono {
  const app = new Hono();

  app.get("/healthz", (c) => health(pool, c));
  app.all("/healthz", (c) => reply(c, 405, { detail: "Method Not Allowed" }));

  app.get("@PARENTS@", (c) => listParents(pool, c));
  app.post("@PARENTS@", (c) => createParent(pool, c));
  app.all("@PARENTS@", (c) => reply(c, 405, { detail: "Method Not Allowed" }));

  app.get("@PARENTS@/:parent_id", (c) => fetchParent(pool, c));
  app.post("@PARENTS@/:parent_id", (c) => createBatch(pool, c));
  app.patch("@PARENTS@/:parent_id", (c) => updateParent(pool, c));
  app.delete("@PARENTS@/:parent_id", (c) => removeParent(pool, c));
  app.all("@PARENTS@/:parent_id", (c) =>
    reply(c, 405, { detail: "Method Not Allowed" }),
  );

  app.get("@PARENTS@/:parent_id@CHILDREN@", (c) => listChildren(pool, c));
  app.post("@PARENTS@/:parent_id@CHILDREN@", (c) => createChild(pool, c));
  app.all("@PARENTS@/:parent_id@CHILDREN@", (c) =>
    reply(c, 405, { detail: "Method Not Allowed" }),
  );

  app.get("@PARENTS@/:parent_id@CHILDREN@/:child_id", (c) =>
    fetchChild(pool, c),
  );
  app.patch("@PARENTS@/:parent_id@CHILDREN@/:child_id", (c) =>
    updateChild(pool, c),
  );
  app.delete("@PARENTS@/:parent_id@CHILDREN@/:child_id", (c) =>
    removeChild(pool, c),
  );
  app.all("@PARENTS@/:parent_id@CHILDREN@/:child_id", (c) =>
    reply(c, 405, { detail: "Method Not Allowed" }),
  );

  app.notFound((c) => reply(c, 404, { detail: "Not Found" }));
  app.onError((_error, c) => reply(c, 500, { detail: "Internal Server Error" }));
  return app;
}
"#;

// ---------------------------------------------------------------- src/db.ts

const DB_PING: &str = r#"import pg from "pg";
import type {
  ChildCreate,
  ChildRead,
  ChildUpdate,
  ParentCreate,
  ParentRead,
  ParentUpdate,
  ParentWithChildrenCreate,
  ParentWithChildrenRead,
} from "./schemas.ts";

pg.types.setTypeParser(1082, (text: string) => text);
pg.types.setTypeParser(1184, (text: string) => {
  const iso = text.replace(" ", "T").replace(/([+-]\d{2})$/, "$1:00");
  const frac = (/\.(\d+)/.exec(iso)?.[1] ?? "000000").padEnd(6, "0").slice(0, 6);
  return new Date(iso).toISOString().replace(/\.\d{3}Z$/, `.${frac}Z`);
});

export type Queryable = Pick<pg.Pool, "query">;

const PING = "SELECT 1";

export class Conflict extends Error {
  readonly code: string;
  constructor(code: string) {
    super("conflict");
    this.code = code;
  }
}

async function run<T extends pg.QueryResultRow>(
  db: Queryable,
  text: string,
  values: unknown[],
): Promise<pg.QueryResult<T>> {
  try {
    return await db.query<T>(text, values);
  } catch (error) {
    const code: unknown =
      error instanceof Error && Object.hasOwn(error, "code")
        ? Reflect.get(error, "code")
        : undefined;
    if (typeof code === "string" && (code === "23503" || code === "23505")) {
      throw new Conflict(code);
    }
    throw error;
  }
}

export async function ping(pool: pg.Pool): Promise<void> {
  await pool.query(PING);
}
"#;

fn selected(key: Option<&Field>, fields: &[Field]) -> String {
    let mut names = vec![quoted("id")];
    names.extend(key.into_iter().chain(fields).map(|f| quoted(&f.name)));
    names.push(quoted("created_at"));
    names.push(quoted("updated_at"));
    names.join(", ")
}

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
        parameters += "filter: boolean | null, ";
        binds += "filter, ";
        slot += 1;
    }
    if let Some(name) = &resource.date {
        conditions.push(format!(
            "(${slot}::date IS NULL OR {} < ${slot})",
            quoted(name)
        ));
        parameters += "before: string | null, ";
        binds += "before, ";
    }
    (conditions, parameters, binds)
}

fn update_sets(fields: &[Field], first: usize) -> (String, Vec<String>) {
    let mut sets = Vec::new();
    let mut flags = Vec::new();
    let mut binds = Vec::new();
    for (index, field) in fields.iter().enumerate() {
        let column = quoted(&field.name);
        let flag = first + 2 * index;
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
    sets.push(format!(
        "\"updated_at\" = CASE WHEN {} THEN now() ELSE \"updated_at\" END",
        flags.join(" OR ")
    ));
    (sets.join(", "), binds)
}

fn db_parent(parent: &Resource) -> String {
    let table = quoted(&parent.table);
    let cols = selected(None, &parent.fields);
    let names: Vec<String> = parent.fields.iter().map(|f| quoted(&f.name)).collect();
    let slots: Vec<String> = (1..=names.len()).map(|i| format!("${i}")).collect();
    let insert_args: Vec<String> = parent
        .fields
        .iter()
        .map(|f| format!("body[\"{}\"]", f.name))
        .collect();
    let (conditions, parameters, binds) = list_filters(parent, 1);
    let slot = 1 + conditions.len();
    let where_clause = if conditions.is_empty() {
        String::new()
    } else {
        format!(" WHERE {}", conditions.join(" AND "))
    };
    let (upd_sets, mut upd_binds) = update_sets(&parent.fields, 2);
    upd_binds.insert(0, "id".to_owned());

    let mut out = String::from("\n");
    out += &to_read_fn("toParentRead", "ParentRead", None, &parent.fields, true);
    out += &format!(
        r#"
const PARENT_INSERT = `INSERT INTO {table} ({}) VALUES ({}) RETURNING {cols}`;
const PARENT_EXISTS = `SELECT EXISTS (SELECT 1 FROM {table} WHERE "id" = $1) AS "exists"`;
const PARENT_FETCH = `SELECT {cols} FROM {table} WHERE "id" = $1`;
const PARENT_LIST = `SELECT {cols} FROM {table}{where_clause} ORDER BY "id" LIMIT ${slot} OFFSET ${}`;
const PARENT_UPDATE = `UPDATE {table} SET {upd_sets} WHERE "id" = $1 RETURNING {cols}`;
const PARENT_DELETE = `DELETE FROM {table} WHERE "id" = $1`;

export async function insertParent(
  db: Queryable,
  body: ParentCreate,
): Promise<ParentRead> {{
  const result = await run(db, PARENT_INSERT, [{}]);
  return toParentRead(result.rows[0] as Record<string, unknown>);
}}

export async function parentExists(
  pool: pg.Pool,
  id: number,
): Promise<boolean> {{
  const result = await run<{{ exists: boolean }}>(pool, PARENT_EXISTS, [id]);
  return result.rows[0]?.exists === true;
}}

export async function fetchParent(
  pool: pg.Pool,
  id: number,
): Promise<ParentRead | null> {{
  const result = await run(pool, PARENT_FETCH, [id]);
  const row = result.rows[0];
  return row === undefined ? null : toParentRead(row);
}}

export async function listParents(
  pool: pg.Pool,
  {parameters}limit: number,
  offset: number,
): Promise<ParentRead[]> {{
  const result = await run(pool, PARENT_LIST, [{binds}limit, offset]);
  return result.rows.map(toParentRead);
}}

export async function updateParent(
  pool: pg.Pool,
  id: number,
  body: ParentUpdate,
): Promise<ParentRead | null> {{
  const result = await run(pool, PARENT_UPDATE, [{}]);
  const row = result.rows[0];
  return row === undefined ? null : toParentRead(row);
}}

export async function deleteParent(
  pool: pg.Pool,
  id: number,
): Promise<boolean> {{
  const result = await run(pool, PARENT_DELETE, [id]);
  return (result.rowCount ?? 0) > 0;
}}
"#,
        names.join(", "),
        slots.join(", "),
        slot + 1,
        insert_args.join(", "),
        upd_binds.join(", ")
    );
    out
}

fn db_child(child: &Resource, key: &Field) -> String {
    let table = quoted(&child.table);
    let fk = quoted(&key.name);
    let cols = selected(Some(key), &child.fields);
    let mut names = vec![fk.clone()];
    names.extend(child.fields.iter().map(|f| quoted(&f.name)));
    let slots: Vec<String> = (1..=names.len()).map(|i| format!("${i}")).collect();
    let mut insert_args = vec!["parent".to_owned()];
    insert_args.extend(child.fields.iter().map(|f| format!("body[\"{}\"]", f.name)));
    let scoped = format!("WHERE \"id\" = $1 AND {fk} = $2");
    let (mut conditions, parameters, binds) = list_filters(child, 2);
    let slot = 2 + conditions.len();
    conditions.insert(0, format!("{fk} = $1"));
    let (upd_sets, mut upd_binds) = update_sets(&child.fields, 3);
    upd_binds.insert(0, "parent".to_owned());
    upd_binds.insert(0, "id".to_owned());

    let mut out = String::from("\n");
    out += &to_read_fn("toChildRead", "ChildRead", Some(key), &child.fields, true);
    out += &format!(
        r#"
const CHILD_INSERT = `INSERT INTO {table} ({}) VALUES ({}) RETURNING {cols}`;
const CHILD_FETCH = `SELECT {cols} FROM {table} {scoped}`;
const CHILD_LIST = `SELECT {cols} FROM {table} WHERE {} ORDER BY "id" LIMIT ${slot} OFFSET ${}`;
const CHILD_UPDATE = `UPDATE {table} SET {upd_sets} {scoped} RETURNING {cols}`;
const CHILD_DELETE = `DELETE FROM {table} {scoped}`;

export async function insertChild(
  db: Queryable,
  parent: number,
  body: ChildCreate,
): Promise<ChildRead> {{
  const result = await run(db, CHILD_INSERT, [{}]);
  return toChildRead(result.rows[0] as Record<string, unknown>);
}}

export async function fetchChild(
  pool: pg.Pool,
  id: number,
  parent: number,
): Promise<ChildRead | null> {{
  const result = await run(pool, CHILD_FETCH, [id, parent]);
  const row = result.rows[0];
  return row === undefined ? null : toChildRead(row);
}}

export async function listChildren(
  pool: pg.Pool,
  parent: number,
  {parameters}limit: number,
  offset: number,
): Promise<ChildRead[]> {{
  const result = await run(pool, CHILD_LIST, [parent, {binds}limit, offset]);
  return result.rows.map(toChildRead);
}}

export async function updateChild(
  pool: pg.Pool,
  id: number,
  parent: number,
  body: ChildUpdate,
): Promise<ChildRead | null> {{
  const result = await run(pool, CHILD_UPDATE, [{}]);
  const row = result.rows[0];
  return row === undefined ? null : toChildRead(row);
}}

export async function deleteChild(
  pool: pg.Pool,
  id: number,
  parent: number,
): Promise<boolean> {{
  const result = await run(pool, CHILD_DELETE, [id, parent]);
  return (result.rowCount ?? 0) > 0;
}}

export async function insertBatch(
  pool: pg.Pool,
  body: ParentWithChildrenCreate,
): Promise<ParentWithChildrenRead> {{
  const client = await pool.connect();
  try {{
    await client.query("BEGIN");
    const parent = await insertParent(client, body.parent);
    const children: ChildRead[] = [];
    for (const child of body.children) {{
      children.push(await insertChild(client, parent.id, child));
    }}
    await client.query("COMMIT");
    return {{ parent, children }};
  }} catch (error) {{
    await client.query("ROLLBACK");
    throw error;
  }} finally {{
    client.release();
  }}
}}
"#,
        names.join(", "),
        slots.join(", "),
        conditions.join(" AND "),
        slot + 1,
        insert_args.join(", "),
        upd_binds.join(", ")
    );
    out
}

// ---------------------------------------------------------------- migrations/0001_<revision>.sql

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

fn hashed_index(table: &str, field: &str) -> String {
    let hex = som::compiler::digest(format!("{table}:{field}").as_bytes());
    quoted(&format!("ix_{}", &hex[..24]))
}

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

// ---------------------------------------------------------------- test/api.test.ts

fn tests_harness(parent: &Resource, child: &Resource, params: &Value) -> Result<String, String> {
    let key = text(params, "foreign_key")?;
    let mut out = String::from(HARNESS_HEAD);
    out += &format!(
        r#"
const PARENTS = "{}";
const CHILDREN = "{}";
const FOREIGN_KEY = "{key}";
const PARENT_ROWS = {};
const CHILD_ROWS = {};

function parentItem(row: Record<string, unknown>): string {{
  return `${{PARENTS}}/${{String(row["id"])}}`;
}}

function childList(parent: Record<string, unknown>): string {{
  return `${{PARENTS}}/${{String(parent["id"])}}${{CHILDREN}}`;
}}

function childItem(row: Record<string, unknown>): string {{
  return `${{PARENTS}}/${{String(row[FOREIGN_KEY])}}${{CHILDREN}}/${{String(row["id"])}}`;
}}

"#,
        parent.path,
        child.path,
        parent.rows(),
        child.rows()
    );
    out += &sample_fns("parent", &parent.fields);
    out += "\n";
    out += &sample_fns("child", &child.fields);
    out += r#"
async function createParent(k: number): Promise<Record<string, unknown>> {
  const res = await call("POST", PARENTS, parent_sample(k));
  assert.equal(res.status, 201);
  return res.body as Record<string, unknown>;
}

async function createChild(
  parent: Record<string, unknown>,
  k: number,
): Promise<Record<string, unknown>> {
  const res = await call("POST", childList(parent), child_sample(k));
  assert.equal(res.status, 201);
  const body = res.body as Record<string, unknown>;
  assert.equal(body[FOREIGN_KEY], parent["id"]);
  return body;
}
"#;
    Ok(out)
}

const TESTS_CRUD: &str = r#"
test("parent and child CRUD round trip", async () => {
  void PARENT_ROWS;
  void CHILD_ROWS;
  const parent = await createParent(0);
  assert.deepEqual(stripStamps(parent), {
    ...parent_sample(0),
    id: parent["id"],
  });
  const child = await createChild(parent, 0);
  assert.deepEqual(stripStamps(child), {
    ...child_sample(0),
    id: child["id"],
    [FOREIGN_KEY]: parent["id"],
  });
  const cUri = childItem(child);
  assert.equal((await call("GET", cUri)).status, 200);
  for (const [name, value] of Object.entries(child_sample(1))) {
    const patched = await call("PATCH", cUri, { [name]: value });
    assert.equal(patched.status, 200);
  }
  const delChild = await call("DELETE", cUri);
  assert.equal(delChild.status, 204);
  assert.equal(delChild.contentType, null);
  assert.deepEqual(await call("GET", cUri), {
    status: 404,
    body: { detail: "child not found" },
    contentType: "application/json",
  });
  const pUri = parentItem(parent);
  const delParent = await call("DELETE", pUri);
  assert.equal(delParent.status, 204);
  assert.equal(delParent.contentType, null);
  const minParent = await call("POST", PARENTS, parent_minimal(2));
  assert.equal(minParent.status, 201);
  const minParentBody = minParent.body as Record<string, unknown>;
  assert.deepEqual(stripStamps(minParentBody), {
    ...parent_defaults(2),
    id: minParentBody["id"],
  });
  const minChild = await call("POST", childList(minParentBody), child_minimal(2));
  assert.equal(minChild.status, 201);
  const minChildBody = minChild.body as Record<string, unknown>;
  assert.deepEqual(stripStamps(minChildBody), {
    ...child_defaults(2),
    id: minChildBody["id"],
    [FOREIGN_KEY]: minParentBody["id"],
  });
});

test("migrate is idempotent", async () => {
  await migrate(pool);
  const rows = await pool.query(
    `SELECT "version", "revision" FROM "_som_ts_migrations"`,
  );
  assert.equal(rows.rows.length, 1);
});
"#;

fn tests_validation(parent: &Resource, child: &Resource) -> String {
    let p_first = &parent.fields[0].name;
    let c_first = &child.fields[0].name;
    format!(
        r#"
test("parent and child validation refusals", async () => {{
  const badParent = await call("POST", PARENTS, {{
    ...parent_sample(0),
    "{p_first}": {{}},
  }});
  assert.equal(badParent.status, 422);
  assert.deepEqual(locs(badParent.body), ["body"]);
  const extraParent = await call("POST", PARENTS, {{
    ...parent_sample(0),
    zz_unknown: 1,
  }});
  assert.equal(extraParent.status, 422);
  const parent = await createParent(0);
  const listUri = childList(parent);
  const badChild = await call("POST", listUri, {{
    ...child_sample(0),
    "{c_first}": {{}},
  }});
  assert.equal(badChild.status, 422);
  assert.deepEqual(locs(badChild.body), ["body"]);
  for (const uri of [
    `${{PARENTS}}/abc`,
    `${{PARENTS}}/with-children`,
    `${{listUri}}/abc`,
    `${{PARENTS}}/abc${{CHILDREN}}`,
  ]) {{
    const res = await call("GET", uri);
    assert.equal(res.status, 422);
    assert.deepEqual(locs(res.body), ["path"]);
  }}
  for (const [uri, detail] of [
    [`${{PARENTS}}/2147483648`, "parent not found"],
    [`${{PARENTS}}/2147483648${{CHILDREN}}`, "parent not found"],
    [`${{listUri}}/2147483648`, "child not found"],
  ] as const) {{
    const res = await call("GET", uri);
    assert.equal(res.status, 404);
    assert.deepEqual(res.body, {{ detail }});
  }}
}});
"#
    )
}

fn tests_unique(parent: &Resource, child: &Resource) -> String {
    let p_unique = parent.unique_value().map(|f| f.name.clone());
    let c_unique = child.unique_value().map(|f| f.name.clone());
    let mut checks = String::new();
    if let Some(name) = p_unique {
        checks += &format!(
            r#"  const pConflict = await call("POST", PARENTS, {{
    ...parent_sample(1),
    "{name}": p0["{name}"],
  }});
  assert.equal(pConflict.status, 409);
"#
        );
    }
    if let Some(name) = c_unique {
        checks += &format!(
            r#"  const c0 = await createChild(p0, 0);
  const cConflict = await call("POST", childList(p0), {{
    ...child_sample(1),
    "{name}": c0["{name}"],
  }});
  assert.equal(cConflict.status, 409);
"#
        );
    }
    format!(
        r#"
test("unique constraints on parent and child", async () => {{
  const p0 = await createParent(0);
  void p0;
{checks}}});
"#
    )
}

fn tests_list(parent: &Resource, child: &Resource) -> String {
    let p_over = parent.max_limit + 1;
    let c_over = child.max_limit + 1;
    format!(
        r#"
test("parent and child list queries", async () => {{
  const p0 = await createParent(0);
  await createChild(p0, 0);
  assert.equal((await call("GET", PARENTS)).status, 200);
  assert.equal((await call("GET", childList(p0))).status, 200);
  for (const [base, over] of [
    [PARENTS, {p_over}],
    [childList(p0), {c_over}],
  ] as const) {{
    for (const q of ["limit=0", `limit=${{String(over)}}`, "offset=-1", "limit=1&limit=2"]) {{
      const res = await call("GET", `${{base}}?${{q}}`);
      assert.equal(res.status, 422);
      assert.deepEqual(locs(res.body), ["query"]);
    }}
  }}
}});
"#
    )
}

const TESTS_SCOPE: &str = r#"
test("children are scoped to their parent and parent delete is restricted", async () => {
  const p0 = await createParent(0);
  const p1 = await createParent(1);
  const c0 = await createChild(p0, 0);
  const crossed = `${PARENTS}/${String(p1["id"])}${CHILDREN}/${String(c0["id"])}`;
  const crossRes = await call("GET", crossed);
  assert.equal(crossRes.status, 404);
  assert.deepEqual(crossRes.body, { detail: "child not found" });
  const restricted = await call("DELETE", parentItem(p0));
  assert.equal(restricted.status, 409);
  assert.deepEqual(restricted.body, {
    detail: "relationship or unique value conflict",
  });
  await call("DELETE", childItem(c0));
  assert.equal((await call("DELETE", parentItem(p0))).status, 204);
});
"#;

fn tests_batch(_parent: &Resource, child: &Resource) -> String {
    let size = if child.rows() < 20 { 1 } else { 2 };
    format!(
        r#"
test("with-children batch creates atomically and refuses invalid batches", async () => {{
  const batchUri = `${{PARENTS}}/with-children`;
  const children = Array.from({{ length: {size} }}, (_, i) => child_sample(i));
  const created = await call("POST", batchUri, {{
    parent: parent_sample(0),
    children,
  }});
  assert.equal(created.status, 201);
  const emptyBatch = await call("POST", batchUri, {{
    parent: parent_sample(1),
    children: [],
  }});
  assert.equal(emptyBatch.status, 422);
  assert.deepEqual(locs(emptyBatch.body), ["body"]);
}});
"#
    )
}

const TESTS_FALLBACK: &str = r#"
test("fallbacks are JSON", async () => {
  for (const uri of [
    "/_missing",
    `${PARENTS}/`,
    `${PARENTS}/1${CHILDREN}/`,
    `${PARENTS}/with-children/`,
  ]) {
    const res = await call("GET", uri);
    assert.equal(res.status, 404);
    assert.deepEqual(res.body, { detail: "Not Found" });
  }
  for (const [method, uri] of [
    ["PUT", PARENTS],
    ["DELETE", PARENTS],
    ["POST", `${PARENTS}/1`],
    ["PUT", `${PARENTS}/1${CHILDREN}`],
    ["POST", `${PARENTS}/1${CHILDREN}/1`],
  ] as const) {
    const res = await call(method, uri);
    assert.equal(res.status, 405);
    assert.deepEqual(res.body, { detail: "Method Not Allowed" });
  }
});
"#;

// ---------------------------------------------------------------- README.md

fn readme(params: &Value, parent: &Resource, child: &Resource) -> Result<String, String> {
    let package = text(params, "package")?;
    let version = text(params, "version")?;
    let key = text(params, "foreign_key")?;
    let revision = text(params, "revision")?;
    let down = params["down_revision"].as_str().unwrap_or("none");
    let number = version.strip_prefix("som.prompt.v").unwrap_or(version);
    let parents = &parent.path;
    let children = &child.path;
    Ok(format!(
        r#"# {package}

Generated from a SOM prompt, package `{package}`, prompt version {number} (migration `{revision}`, down `{down}`).
Parent table `{}` and child table `{}` are joined by `{key}` (`ON DELETE RESTRICT`).

## Routes

- `GET /healthz`
- `GET {parents}`
- `POST {parents}`
- `POST {parents}/with-children`
- `GET {parents}/{{parent_id}}`
- `PATCH {parents}/{{parent_id}}`
- `DELETE {parents}/{{parent_id}}`
- `GET {parents}/{{parent_id}}{children}`
- `POST {parents}/{{parent_id}}{children}`
- `GET {parents}/{{parent_id}}{children}/{{child_id}}`
- `PATCH {parents}/{{parent_id}}{children}/{{child_id}}`
- `DELETE {parents}/{{parent_id}}{children}/{{child_id}}`

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
"#,
        parent.table, child.table
    ))
}
