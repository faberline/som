import pg from "pg";
import type { Create, Read, Update } from "./schemas.ts";

pg.types.setTypeParser(1082, (text: string) => text);

const PING = "SELECT 1";
const INSERT = `INSERT INTO "items" ("title") VALUES ($1) RETURNING "id", "title"`;
const FETCH = `SELECT "id", "title" FROM "items" WHERE "id" = $1`;
const LIST = `SELECT "id", "title" FROM "items" ORDER BY "id"`;
const UPDATE = `UPDATE "items" SET "title" = CASE WHEN $2 THEN $3 ELSE "title" END WHERE "id" = $1 RETURNING "id", "title"`;
const DELETE = `DELETE FROM "items" WHERE "id" = $1`;

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

function toRead(row: Record<string, unknown>): Read {
  return { id: row["id"] as number, title: row["title"] as string };
}

export async function ping(pool: pg.Pool): Promise<void> {
  await pool.query(PING);
}

export async function insert(pool: pg.Pool, body: Create): Promise<Read> {
  const result = await run(pool, INSERT, [body["title"]]);
  return toRead(result.rows[0] as Record<string, unknown>);
}

export async function fetch(pool: pg.Pool, id: number): Promise<Read | null> {
  const result = await run(pool, FETCH, [id]);
  const row = result.rows[0];
  return row === undefined ? null : toRead(row);
}

export async function list(pool: pg.Pool): Promise<Read[]> {
  const result = await run(pool, LIST, []);
  return result.rows.map(toRead);
}

/** Each member binds whether it was present, then its value. Absent members keep the column. */
export async function update(
  pool: pg.Pool,
  id: number,
  body: Update,
): Promise<Read | null> {
  const present = Object.hasOwn(body, "title");
  const result = await run(pool, UPDATE, [
    id,
    present,
    present ? body["title"] : null,
  ]);
  const row = result.rows[0];
  return row === undefined ? null : toRead(row);
}

export async function remove(pool: pg.Pool, id: number): Promise<boolean> {
  const result = await run(pool, DELETE, [id]);
  return (result.rowCount ?? 0) > 0;
}
