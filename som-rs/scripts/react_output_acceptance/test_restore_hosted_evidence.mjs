import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { sealEvidence } from "./seal_hosted_evidence.mjs";
import { sealFiles, sha256 } from "./sealed_inputs.mjs";
import { REFUSAL, restoreEvidence } from "./restore_hosted_evidence.mjs";

// Only fabricated files and keys are used. No retained acceptance data is read.
const key = Buffer.alloc(32, 37);
const wrongKey = Buffer.alloc(32, 91);
const keyId = "b".repeat(64);
const environ = () => ({ M6_INPUTS_KEYRING: JSON.stringify({
  ["0".repeat(64)]: wrongKey.toString("base64"), [keyId]: key.toString("base64"),
}) });
const refusal = { name: "EvidenceRestoreError", message: REFUSAL };
const script = fileURLToPath(new URL("./restore_hosted_evidence.mjs", import.meta.url));

async function fixture(t) {
  const base = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "som-m6-restore-evidence-test-")));
  t.after(() => fs.rm(base, { recursive: true, force: true }));
  const source = path.join(base, "source");
  const root = path.join(base, "encrypted");
  const out = path.join(base, "plaintext");
  const expected = new Map([
    ["summary.json", Buffer.from('{"fake":"no acceptance claim"}\n')],
    ["run/request.raw", Buffer.from("fake-private-body-never-log-this")],
    ["run/reply.raw", Buffer.from([0, 255, 10, 128, 0])],
    ["candidate/som", Buffer.from("fake executable bytes; never run")],
  ]);
  for (const [relative, bytes] of expected) {
    const filename = path.join(source, ...relative.split("/"));
    await fs.mkdir(path.dirname(filename), { recursive: true });
    await fs.writeFile(filename, bytes, { mode: relative === "candidate/som" ? 0o755 : 0o644 });
  }
  await sealEvidence({ root: source, out: root,
    environ: { M6_INPUTS_KEYRING: JSON.stringify({ [keyId]: key.toString("base64") }) } });
  return { base, source, root, out, expected };
}

async function indexOf(data) {
  const bytes = await fs.readFile(path.join(data.root, "evidence-index.json"));
  return { index: JSON.parse(bytes), hash: sha256(bytes) };
}

async function saveIndex(data, index) {
  const bytes = Buffer.from(`${JSON.stringify(index, null, 2)}\n`);
  await fs.writeFile(path.join(data.root, "evidence-index.json"), bytes);
  return sha256(bytes);
}

async function appendPackage(data, files) {
  const { index } = await indexOf(data);
  const sealed = sealFiles(files, { key });
  const name = `package-${String(index.packages.length).padStart(3, "0")}`;
  const manifestBytes = Buffer.from(`${JSON.stringify(sealed.manifest, null, 2)}\n`);
  const entry = { archive: `${name}.sealed`, manifest: `${name}.manifest.json`,
    manifest_sha256: sha256(manifestBytes), cipher_sha256: sealed.manifest.cipher_sha256,
    total_files: files.length, bytes: files.reduce((total, file) => total + file.data.length, 0) };
  await fs.writeFile(path.join(data.root, entry.archive), sealed.archive, { mode: 0o600 });
  await fs.writeFile(path.join(data.root, entry.manifest), manifestBytes, { mode: 0o600 });
  sealed.key.fill(0);
  index.packages.push(entry);
  await saveIndex(data, index);
  return entry;
}

async function noStage(data) {
  assert.deepEqual((await fs.readdir(data.base)).filter((name) => name.startsWith(".som-m6-evidence-restore-")), []);
}

async function encryptedHashes(data) {
  return Object.fromEntries(await Promise.all((await fs.readdir(data.root)).sort().map(async (name) =>
    [name, sha256(await fs.readFile(path.join(data.root, name)))])));
}

test("producer files restore exactly with index-selected key and no acceptance inference", async (t) => {
  const data = await fixture(t);
  await appendPackage(data, [{ path: "run/second-package.raw", mode: 0o644, data: Buffer.from("second fake package") }]);
  data.expected.set("run/second-package.raw", Buffer.from("second fake package"));
  const before = await encryptedHashes(data);
  const { hash } = await indexOf(data);
  const receipt = await restoreEvidence({ root: data.root, out: data.out, indexSha256: hash, environ: environ() });
  assert.deepEqual(receipt, { version: "som.m6-evidence-restore-receipt.v1", status: "restored", index_sha256: hash,
    package_count: 2, file_count: data.expected.size,
    bytes: [...data.expected.values()].reduce((total, bytes) => total + bytes.length, 0), product_acceptance: "not_inferred" });
  for (const [relative, bytes] of data.expected) assert.deepEqual(await fs.readFile(path.join(data.out, relative)), bytes);
  assert.equal((await fs.stat(data.out)).mode & 0o777, 0o700);
  assert.equal((await fs.stat(path.join(data.out, "candidate/som"))).mode & 0o777, 0o755);
  assert.deepEqual(await encryptedHashes(data), before);
  await noStage(data);
});

test("corrupt final package removes staged plaintext and never publishes output", async (t) => {
  const data = await fixture(t);
  const last = await appendPackage(data, [{ path: "run/final.raw", mode: 0o644, data: Buffer.from("last fake package") }]);
  const filename = path.join(data.root, last.archive);
  const corrupt = await fs.readFile(filename);
  corrupt[corrupt.length - 1] ^= 1;
  await fs.writeFile(filename, corrupt);
  const before = await encryptedHashes(data);
  await assert.rejects(restoreEvidence({ root: data.root, out: data.out, indexSha256: (await indexOf(data)).hash,
    environ: environ() }), refusal);
  await assert.rejects(fs.lstat(data.out), { code: "ENOENT" });
  assert.deepEqual(await encryptedHashes(data), before);
  await noStage(data);
});

test("wrong key and index pin fail without plaintext publication", async (t) => {
  const data = await fixture(t);
  const { hash } = await indexOf(data);
  for (const options of [
    { indexSha256: "0".repeat(64), environ: environ() },
    { indexSha256: hash, environ: { M6_INPUTS_KEYRING: JSON.stringify({ [keyId]: wrongKey.toString("base64") }) } },
    { indexSha256: hash, environ: { M6_INPUTS_KEYRING: "{}" } },
    { indexSha256: hash, environ: { M6_INPUTS_KEYRING: "not-json fake-key-never-log" } },
  ]) {
    await assert.rejects(restoreEvidence({ root: data.root, out: data.out, ...options }), refusal);
    await assert.rejects(fs.lstat(data.out), { code: "ENOENT" });
    await noStage(data);
  }
});

test("manifest hashes, package totals and exact flat names remain mandatory", async (t) => {
  for (const change of ["manifest-hash", "files", "bytes", "archive-name", "manifest-name", "extra-key", "extra-file"]) {
    await t.test(change, async (part) => {
      const data = await fixture(part);
      const { index } = await indexOf(data);
      if (change === "manifest-hash") index.packages[0].manifest_sha256 = "0".repeat(64);
      if (change === "files") index.packages[0].total_files += 1;
      if (change === "bytes") index.packages[0].bytes += 1;
      if (change === "archive-name") index.packages[0].archive = "../outside.sealed";
      if (change === "manifest-name") index.packages[0].manifest = "package-999.manifest.json";
      if (change === "extra-key") index.product_status = "passed";
      if (change === "extra-file") await fs.writeFile(path.join(data.root, "unlisted"), "fake");
      const hash = await saveIndex(data, index);
      await assert.rejects(restoreEvidence({ root: data.root, out: data.out, indexSha256: hash, environ: environ() }), refusal);
      await assert.rejects(fs.lstat(data.out), { code: "ENOENT" });
      await noStage(data);
    });
  }
});

test("cross-package duplicate and file-parent conflicts never publish", async (t) => {
  for (const relative of ["run/request.raw", "run", "candidate/som/child"]) {
    await t.test(relative, async (part) => {
      const data = await fixture(part);
      await appendPackage(data, [{ path: relative, mode: 0o644, data: Buffer.from("conflicting fake bytes") }]);
      await assert.rejects(restoreEvidence({ root: data.root, out: data.out, indexSha256: (await indexOf(data)).hash,
        environ: environ() }), refusal);
      await assert.rejects(fs.lstat(data.out), { code: "ENOENT" });
      await noStage(data);
    });
  }
});

test("existing outputs including empty directories are never overwritten", async (t) => {
  const data = await fixture(t);
  const { hash } = await indexOf(data);
  await fs.mkdir(data.out);
  await assert.rejects(restoreEvidence({ root: data.root, out: data.out, indexSha256: hash, environ: environ() }), refusal);
  assert.deepEqual(await fs.readdir(data.out), []);
  await fs.writeFile(path.join(data.out, "keep"), "original output");
  await assert.rejects(restoreEvidence({ root: data.root, out: data.out, indexSha256: hash, environ: environ() }), refusal);
  assert.equal(await fs.readFile(path.join(data.out, "keep"), "utf8"), "original output");
  await noStage(data);
});

test("symlink roots, parents, package files and nested output are refused", async (t) => {
  const data = await fixture(t);
  const { hash, index } = await indexOf(data);
  const alias = path.join(data.base, "alias");
  await fs.symlink(data.root, alias, "dir");
  await assert.rejects(restoreEvidence({ root: alias, out: data.out, indexSha256: hash, environ: environ() }), refusal);
  await assert.rejects(restoreEvidence({ root: data.root, out: path.join(data.root, "nested"), indexSha256: hash,
    environ: environ() }), refusal);
  const linkedParent = path.join(data.base, "parent-alias");
  await fs.symlink(data.base, linkedParent, "dir");
  await assert.rejects(restoreEvidence({ root: data.root, out: path.join(linkedParent, "output"), indexSha256: hash,
    environ: environ() }), refusal);
  const packageFile = path.join(data.root, index.packages[0].archive);
  const moved = path.join(data.base, "archive");
  await fs.rename(packageFile, moved);
  await fs.symlink(moved, packageFile);
  await assert.rejects(restoreEvidence({ root: data.root, out: data.out, indexSha256: hash, environ: environ() }), refusal);
  await noStage(data);
});

test("a special index file is refused before opening it and oversized indexes are bounded", async (t) => {
  const data = await fixture(t);
  const { hash } = await indexOf(data);
  const filename = path.join(data.root, "evidence-index.json");
  await fs.unlink(filename);
  const fifo = spawnSync("mkfifo", [filename], { encoding: "utf8", timeout: 10_000 });
  assert.equal(fifo.status, 0);
  await assert.rejects(restoreEvidence({ root: data.root, out: data.out, indexSha256: hash, environ: environ() }), refusal);
  await fs.unlink(filename);
  const handle = await fs.open(filename, "wx");
  await handle.truncate(1024 * 1024 + 1);
  await handle.close();
  await assert.rejects(restoreEvidence({ root: data.root, out: data.out, indexSha256: hash, environ: environ() }), refusal);
  await noStage(data);
});

test("CLI emits only a safe receipt or a generic refusal", async (t) => {
  const data = await fixture(t);
  const { hash } = await indexOf(data);
  const args = [script, "--root", data.root, "--out", data.out, "--index-sha256", hash];
  const result = spawnSync(process.execPath, args, { encoding: "utf8", env: { ...process.env, ...environ() }, timeout: 30_000 });
  assert.equal(result.status, 0);
  assert.equal(result.stderr, "");
  assert.equal(JSON.parse(result.stdout).product_acceptance, "not_inferred");
  for (const sensitive of [data.root, "fake-private-body-never-log-this", key.toString("base64")]) {
    assert.equal(result.stdout.includes(sensitive), false);
  }
  const refused = spawnSync(process.execPath, args, { encoding: "utf8", env: { ...process.env, ...environ() }, timeout: 30_000 });
  assert.equal(refused.status, 1);
  assert.equal(refused.stdout, "");
  assert.equal(refused.stderr, `${REFUSAL}\n`);
});
