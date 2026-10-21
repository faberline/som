import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import type pg from "pg";

const REVISION = "rev0000";
const MIGRATION = new URL("../migrations/0001_rev0000.sql", import.meta.url);

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
