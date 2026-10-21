import { Hono } from "hono";
import type { Context } from "hono";
import type pg from "pg";
import * as db from "./db.ts";
import { checkCreate, checkUpdate } from "./schemas.ts";
import type { Problem } from "./schemas.ts";

const NOT_INTEGER =
  "Input should be a valid integer, unable to parse string as an integer";
const NOT_OBJECT =
  "Input should be a valid dictionary or object to extract fields from";
const INT32_MIN = -2147483648n;
const INT32_MAX = 2147483647n;

type Body =
  | { kind: "object"; value: Record<string, unknown> }
  | { kind: "problem"; problem: Problem }
  | { kind: "invalid_json"; problem: Problem };

/** An optional sign and ASCII digits, else a path problem. Outside i32 there is no row. */
function readId(text: string, problems: Problem[]): number | null | undefined {
  if (!/^[+-]?[0-9]+$/.test(text)) {
    problems.push({
      type: "int_parsing",
      loc: ["path", "item_id"],
      msg: NOT_INTEGER,
    });
    return undefined;
  }
  const value = BigInt(text);
  if (value < INT32_MIN || value > INT32_MAX) {
    return null;
  }
  return Number(value);
}

/** No content type, application/json or application/*+json. */
function jsonType(header: string | undefined): boolean {
  if (header === undefined) {
    return true;
  }
  const media = (header.split(";")[0] ?? "").trim().toLowerCase();
  return (
    media === "application/json" ||
    (media.startsWith("application/") && media.endsWith("+json"))
  );
}

async function readBody(c: Context): Promise<Body> {
  const text = await c.req.text();
  if (text.length === 0) {
    return {
      kind: "problem",
      problem: { type: "missing", loc: ["body"], msg: "Field required" },
    };
  }
  if (!jsonType(c.req.header("content-type"))) {
    return {
      kind: "problem",
      problem: {
        type: "model_attributes_type",
        loc: ["body"],
        msg: NOT_OBJECT,
      },
    };
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (error) {
    const found = /position (\d+)/.exec(String(error));
    return {
      kind: "invalid_json",
      problem: {
        type: "json_invalid",
        loc: ["body", found === null ? 0 : Number(found[1])],
        msg: "JSON decode error",
      },
    };
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {
      kind: "problem",
      problem: {
        type: "model_attributes_type",
        loc: ["body"],
        msg: NOT_OBJECT,
      },
    };
  }
  return { kind: "object", value: value as Record<string, unknown> };
}

function invalid(c: Context, problems: Problem[]): Response {
  return c.json({ detail: problems }, 422);
}

function notFound(c: Context): Response {
  return c.json({ detail: "not found" }, 404);
}

function conflict(c: Context): Response {
  return c.json({ detail: "conflict" }, 409);
}

async function guard(
  c: Context,
  work: () => Promise<Response>,
): Promise<Response> {
  try {
    return await work();
  } catch (error) {
    if (error instanceof db.Conflict) {
      return conflict(c);
    }
    throw error;
  }
}

function notAllowed(c: Context): Response {
  return c.json({ detail: "Method Not Allowed" }, 405);
}

export function createApp(pool: pg.Pool): Hono {
  const app = new Hono({ strict: true });

  app.get("/healthz", async (c) => {
    try {
      await db.ping(pool);
    } catch {
      return c.json({ detail: "database unavailable" }, 503);
    }
    return c.json({ status: "ok" }, 200);
  });
  app.all("/healthz", notAllowed);

  app.get("/items", async (c) => c.json(await db.list(pool), 200));

  app.post("/items", (c) =>
    guard(c, async () => {
      const body = await readBody(c);
      if (body["kind"] !== "object") {
        return invalid(c, [body["problem"]]);
      }
      const checked = checkCreate(body["value"]);
      if (!checked["ok"]) {
        return invalid(c, checked["problems"]);
      }
      return c.json(await db.insert(pool, checked["value"]), 201);
    }),
  );
  app.all("/items", notAllowed);

  app.get("/items/:item_id", async (c) => {
    const problems: Problem[] = [];
    const id = readId(c.req.param("item_id"), problems);
    if (problems.length > 0) {
      return invalid(c, problems);
    }
    if (id === null || id === undefined) {
      return notFound(c);
    }
    const row = await db.fetch(pool, id);
    return row === null ? notFound(c) : c.json(row, 200);
  });

  app.patch("/items/:item_id", (c) =>
    guard(c, async () => {
      const problems: Problem[] = [];
      const id = readId(c.req.param("item_id"), problems);
      const body = await readBody(c);
      if (body["kind"] === "invalid_json") {
        return invalid(c, [body["problem"]]);
      }
      if (body["kind"] === "problem") {
        return invalid(c, [...problems, body["problem"]]);
      }
      const checked = checkUpdate(body["value"]);
      if (!checked["ok"]) {
        return invalid(c, [...problems, ...checked["problems"]]);
      }
      if (problems.length > 0) {
        return invalid(c, problems);
      }
      if (id === null || id === undefined) {
        return notFound(c);
      }
      const row = await db.update(pool, id, checked["value"]);
      return row === null ? notFound(c) : c.json(row, 200);
    }),
  );

  app.delete("/items/:item_id", async (c) => {
    const problems: Problem[] = [];
    const id = readId(c.req.param("item_id"), problems);
    if (problems.length > 0) {
      return invalid(c, problems);
    }
    if (id === null || id === undefined) {
      return notFound(c);
    }
    if (!(await db.remove(pool, id))) {
      return notFound(c);
    }
    // As in the Python product, the empty 204 answer keeps the JSON media type.
    return c.body(null, 204, { "content-type": "application/json" });
  });
  app.all("/items/:item_id", notAllowed);

  app.notFound((c) => c.json({ detail: "Not Found" }, 404));
  app.onError((error, c) => {
    process.stderr.write(`error: ${String(error)}\n`);
    return c.json({ detail: "Internal Server Error" }, 500);
  });
  return app;
}
