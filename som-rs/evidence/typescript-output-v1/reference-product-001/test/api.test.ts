import assert from "node:assert/strict";
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
): Promise<{ status: number; body: unknown }> {
  const init: RequestInit = { method: method };
  if (body !== undefined) {
    init["body"] = typeof body === "string" ? body : JSON.stringify(body);
    init["headers"] = { "content-type": "application/json" };
  }
  const response = await app.request(path, init);
  const text = await response.text();
  return {
    status: response.status,
    body: text === "" ? null : JSON.parse(text),
  };
}

test("healthz answers ok", async () => {
  assert.deepEqual(await call("GET", "/healthz"), {
    status: 200,
    body: { status: "ok" },
  });
});

test("CRUD round trip", async () => {
  const created = await call("POST", "/items", { title: "first" });
  assert.deepEqual(created, {
    status: 201,
    body: { id: 1, title: "first" },
  });
  await call("POST", "/items", { title: "second" });
  assert.deepEqual(await call("GET", "/items"), {
    status: 200,
    body: [
      { id: 1, title: "first" },
      { id: 2, title: "second" },
    ],
  });
  assert.deepEqual(await call("GET", "/items/1"), {
    status: 200,
    body: { id: 1, title: "first" },
  });
  assert.deepEqual(await call("PATCH", "/items/1", { title: "renamed" }), {
    status: 200,
    body: { id: 1, title: "renamed" },
  });
  assert.deepEqual(await call("PATCH", "/items/1", {}), {
    status: 200,
    body: { id: 1, title: "renamed" },
  });
  assert.deepEqual(await call("DELETE", "/items/1"), {
    status: 204,
    body: null,
  });
  assert.equal((await call("GET", "/items/1"))["status"], 404);
});

test("missing ids are 404", async () => {
  const missing = { status: 404, body: { detail: "not found" } };
  assert.deepEqual(await call("GET", "/items/7"), missing);
  assert.deepEqual(await call("PATCH", "/items/7", { title: "x" }), missing);
  assert.deepEqual(await call("DELETE", "/items/7"), missing);
  assert.deepEqual(await call("GET", "/items/99999999999"), missing);
});

test("unique values conflict", async () => {
  await call("POST", "/items", { title: "same" });
  await call("POST", "/items", { title: "other" });
  const conflict = { status: 409, body: { detail: "conflict" } };
  assert.deepEqual(await call("POST", "/items", { title: "same" }), conflict);
  assert.deepEqual(
    await call("PATCH", "/items/2", { title: "same" }),
    conflict,
  );
});

test("invalid requests are 422", async () => {
  const missing = await call("POST", "/items", {});
  assert.equal(missing["status"], 422);
  assert.deepEqual(missing["body"], {
    detail: [
      { type: "missing", loc: ["body", "title"], msg: "Field required" },
    ],
  });
  const long = await call("POST", "/items", { title: "é".repeat(256) });
  assert.equal(long["status"], 422);
  const fits = await call("POST", "/items", { title: "é".repeat(255) });
  assert.equal(fits["status"], 201);
  assert.equal((await call("POST", "/items", "[1]"))["status"], 422);
  assert.equal((await call("POST", "/items", "{"))["status"], 422);
  const path = await call("GET", "/items/abc");
  assert.deepEqual(path, {
    status: 422,
    body: {
      detail: [
        {
          type: "int_parsing",
          loc: ["path", "item_id"],
          msg: "Input should be a valid integer, unable to parse string as an integer",
        },
      ],
    },
  });
});

test("Object.prototype names are not read from the body", async () => {
  const body = await call("POST", "/items", '{"toString": "x"}');
  assert.equal(body["status"], 422);
});

test("fallbacks are JSON", async () => {
  assert.deepEqual(await call("GET", "/nothing"), {
    status: 404,
    body: { detail: "Not Found" },
  });
  assert.deepEqual(await call("GET", "/items/"), {
    status: 404,
    body: { detail: "Not Found" },
  });
  assert.deepEqual(await call("PUT", "/items/1", { title: "x" }), {
    status: 405,
    body: { detail: "Method Not Allowed" },
  });
  assert.deepEqual(await call("DELETE", "/items"), {
    status: 405,
    body: { detail: "Method Not Allowed" },
  });
});

test("migrate is idempotent", async () => {
  await migrate(pool);
  const rows = await pool.query(
    `SELECT "version", "revision" FROM "_som_ts_migrations"`,
  );
  assert.deepEqual(rows.rows, [{ version: 1, revision: "rev0000" }]);
});
