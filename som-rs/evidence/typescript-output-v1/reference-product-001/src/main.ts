import { serve } from "@hono/node-server";
import pg from "pg";
import { createApp } from "./app.ts";
import { migrate } from "./migrate.ts";

function fail(message: string): never {
  process.stderr.write(`${message}\n`);
  process.exit(2);
}

function readAddress(text: string): { host: string; port: number } {
  const found = /^(.+):([0-9]{1,5})$/.exec(text);
  const port = found === null ? 0 : Number(found[2]);
  if (found === null || port < 1 || port > 65535) {
    fail("SOM_APP_ADDR must be host:port with a port from 1 to 65535");
  }
  return { host: found[1] as string, port: port };
}

const url = process.env["DATABASE_URL"];
if (url === undefined || url === "") {
  fail("DATABASE_URL is required");
}
const address = readAddress(process.env["SOM_APP_ADDR"] ?? "127.0.0.1:8000");

const pool = new pg.Pool({ connectionString: url });
try {
  await migrate(pool);
} catch (error) {
  await pool.end();
  fail(`migration failed: ${String(error)}`);
}

const server = serve({
  fetch: createApp(pool).fetch,
  hostname: address["host"],
  port: address["port"],
});

function stop(): void {
  server.close(() => {
    void pool.end().then(() => process.exit(0));
  });
}

process.on("SIGINT", stop);
process.on("SIGTERM", stop);
