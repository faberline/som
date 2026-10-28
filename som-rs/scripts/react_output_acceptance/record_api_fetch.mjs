// Record the unchanged API probe's fetch traffic. Do not write to stdout.
import { appendFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";

const directory = process.env.SOM_API_RAW_DIR;
if (!directory || !path.isAbsolute(directory)) throw new Error("API raw directory is required");
const filename = path.join(directory, `fetch-${process.pid}.jsonl`);
const original = globalThis.fetch;
let sequence = 0;
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const body = (bytes) => ({ base64: bytes.toString("base64"), sha256: digest(bytes) });

globalThis.fetch = async function (...args) {
  const request = new Request(...args);
  const bytes = Buffer.from(await request.clone().arrayBuffer());
  const row = {
    version: "som.react-api-fetch.v1", sequence: ++sequence, pid: process.pid,
    request: { url: request.url, method: request.method,
      headers: [...request.headers], body: body(bytes) },
    response: null, error: null,
  };
  let response;
  try {
    response = await original.apply(this, args);
  } catch (error) {
    row.error = { name: error.name, message: error.message,
      cause_code: error.cause?.code ?? null, cause_errno: error.cause?.errno ?? null };
    appendFileSync(filename, `${JSON.stringify(row)}\n`, { mode: 0o600 });
    throw error;
  }
  const reply = Buffer.from(await response.clone().arrayBuffer());
  row.response = { status: response.status, headers: [...response.headers], body: body(reply) };
  appendFileSync(filename, `${JSON.stringify(row)}\n`, { mode: 0o600 });
  return response;
};
