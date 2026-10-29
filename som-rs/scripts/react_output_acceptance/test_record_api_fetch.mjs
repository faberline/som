// Fake fetch only. Run on GitHub; this test opens no network connection.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

assert.equal(process.env.GITHUB_ACTIONS, "true", "Run this fake test on GitHub only");
const directory = mkdtempSync(path.join(tmpdir(), "som-api-fetch-"));
const previousFetch = globalThis.fetch;
const previousDirectory = process.env.SOM_API_RAW_DIR;
try {
  process.env.SOM_API_RAW_DIR = directory;
  globalThis.fetch = async (url) => {
    if (url.endsWith(":9/items")) throw new TypeError("fake refused fetch");
    return new Response('{"id":1}', { status: 201 });
  };
  await import("./record_api_fetch.mjs");
  const response = await fetch("http://127.0.0.1:20001/items", {
    method: "POST", headers: { "content-type": "application/json" }, body: '{"name":"full name"}',
  });
  assert.equal(await response.text(), '{"id":1}');
  await assert.rejects(fetch("http://127.0.0.1:9/items"), /fake refused/);
  const rows = readFileSync(path.join(directory, `fetch-${process.pid}.jsonl`), "utf8").trim().split("\n").map(JSON.parse);
  assert.deepEqual(rows.map((row) => row.sequence), [1, 2]);
  assert.equal(Buffer.from(rows[0].request.body.base64, "base64").toString(), '{"name":"full name"}');
  assert.equal(rows[0].response.body.sha256, createHash("sha256").update('{"id":1}').digest("hex"));
  assert.equal(rows[1].response, null);
  assert.equal(rows[1].error.name, "TypeError");
  assert.equal(rows[1].request.url, "http://127.0.0.1:9/items");
} finally {
  globalThis.fetch = previousFetch;
  if (previousDirectory === undefined) delete process.env.SOM_API_RAW_DIR;
  else process.env.SOM_API_RAW_DIR = previousDirectory;
  rmSync(directory, { recursive: true, force: true });
}
