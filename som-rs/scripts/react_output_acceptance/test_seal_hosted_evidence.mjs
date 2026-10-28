import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { openArchive, sha256 } from "./sealed_inputs.mjs";
import {
  CHUNK_BYTES,
  INDEX_VERSION,
  MAX_SINGLE_FILE_BYTES,
  REFUSAL,
  collectEvidence,
  evidenceKey,
  planPackages,
  sealEvidence,
} from "./seal_hosted_evidence.mjs";

// Tests make their own bytes and keys. They never read an acceptance fixture.
const SCRIPT = fileURLToPath(new URL("./seal_hosted_evidence.mjs", import.meta.url));
const refusal = { name: "EvidenceSealError", message: REFUSAL };
const selectedKey = Buffer.alloc(32, 17);
const otherKey = Buffer.alloc(32, 29);
const selectedId = "1".repeat(64);
const otherId = "e".repeat(64);
const environ = () => ({ M6_INPUTS_KEYRING: JSON.stringify({ [otherId]: otherKey.toString("base64"), [selectedId]: selectedKey.toString("base64") }) });

async function fixture(t) {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "som-m6-evidence-test-"));
  // The tested tool requires physical paths. macOS may alias the temp parent.
  const base = await fs.realpath(temporary);
  t.after(() => fs.rm(base, { recursive: true, force: true }));
  const root = path.join(base, "acceptance");
  const out = path.join(base, "encrypted");
  await fs.mkdir(root);
  const expected = new Map();
  async function write(relative, data = Buffer.from(`fake bytes for ${relative}`), mode = 0o644, retain = true) {
    const filename = path.join(root, ...relative.split("/"));
    await fs.mkdir(path.dirname(filename), { recursive: true });
    await fs.writeFile(filename, data);
    await fs.chmod(filename, mode);
    if (retain) expected.set(relative, Buffer.from(data));
    return filename;
  }
  async function huge(relative) {
    const filename = await write(relative, Buffer.alloc(0), 0o644, false);
    const handle = await fs.open(filename, "r+");
    await handle.truncate(MAX_SINGLE_FILE_BYTES + 1);
    await handle.close();
  }
  return { base, root, out, expected, write, huge };
}

async function decryptOutput(out, key = selectedKey) {
  const indexBytes = await fs.readFile(path.join(out, "evidence-index.json"));
  const index = JSON.parse(indexBytes);
  const files = new Map();
  for (const entry of index.packages) {
    const manifestBytes = await fs.readFile(path.join(out, entry.manifest));
    assert.equal(sha256(manifestBytes), entry.manifest_sha256);
    const manifest = JSON.parse(manifestBytes);
    const archive = await fs.readFile(path.join(out, entry.archive));
    assert.equal(sha256(archive), entry.cipher_sha256);
    assert.equal(manifest.cipher_sha256, entry.cipher_sha256);
    assert.equal(manifest.total_files, entry.total_files);
    assert.equal(manifest.files.reduce((sum, file) => sum + file.bytes, 0), entry.bytes);
    for (const file of openArchive(archive, manifest, key)) {
      assert.equal(files.has(file.path), false);
      files.set(file.path, file.data);
    }
  }
  return { index, indexBytes, files };
}

test("all raw run inputs and named candidate files survive encryption", async (t) => {
  const data = await fixture(t);
  for (const name of ["summary.json", "audit.json", "bootstrap-assets.json", "driver.stdout", "driver.stderr", "driver-128.stdout", "driver-128.stderr",
    "baseline.stdout", "baseline.stderr", "prepare.stdout", "prepare.stderr", "acceptance.stdout", "acceptance.stderr",
    "audit.stdout", "audit.stderr", "head-before.stdout", "head-after.stdout", "candidate-ps.stdout", "cleanup.stdout", "cleanup.stderr",
    "reconstructed-inputs/receipt.json", "reconstructed-inputs/names/RN01.request.raw",
    "reconstructed-inputs/names/RN01.reply.raw", "reconstructed-inputs/rust-reference/phase2/cases/fake.json",
    "run/receipts/01-contract.json", "run/logs/raw.stderr", "run/inputs/request.raw", "run/products/fixture/src/api.ts",
    "candidate/som", "candidate/build.json", "candidate/runtime.json", "candidate/source.json", "candidate/source.tar",
    "candidate/server.log", "candidate/assets/manifest.json", "candidate/assets/library.bin", "candidate/assets-package.stdout",
    "candidate/assets-package.stderr", "candidate/build-0.stdout", "candidate/build-0.stderr"]) {
    await data.write(name, Buffer.from(`fake-private-evidence:${name}`), name === "candidate/som" ? 0o755 : 0o644);
  }
  await data.write("run/inputs/binary.bin", Buffer.from([0, 255, 128, 10, 0]));
  for (const excluded of ["run/node_modules/huge", "run/products/fixture/pgdata/huge", "run/python/__pycache__/huge",
    "candidate/target/release/huge", "candidate/target-assets/huge", "candidate/target-other/huge",
    "candidate/other-build-scratch/huge"]) await data.huge(excluded);
  await data.write("private.key", Buffer.from("fake unselected key bytes"), 0o600, false);
  await data.write("other.log", Buffer.from("fake unselected log"), 0o644, false);
  await data.write("-unsafe.stdout", Buffer.from("fake unselected log name"), 0o644, false);
  await data.write("unsafe name.stderr", Buffer.from("fake unselected log name"), 0o644, false);
  await data.write("candidate/not-allowed.stdout", Buffer.from("fake unselected candidate log"), 0o644, false);
  const result = await sealEvidence({ root: data.root, out: data.out, environ: environ() });
  const restored = await decryptOutput(data.out);
  assert.equal(restored.index.version, INDEX_VERSION);
  assert.equal(restored.index.key_id, selectedId);
  assert.equal(restored.index.status, "encrypted");
  assert.equal(restored.index.product_acceptance, "read-summary");
  assert.equal(result.file_count, data.expected.size);
  assert.deepEqual(restored.files, data.expected);
  assert.equal((await fs.stat(data.out)).mode & 0o777, 0o700);
  for (const filename of await fs.readdir(data.out)) {
    assert.match(filename, /^(evidence-index\.json|package-[0-9]{3}\.(sealed|manifest\.json))$/);
    assert.equal((await fs.stat(path.join(data.out, filename))).mode & 0o777, 0o600);
    const bytes = await fs.readFile(path.join(data.out, filename));
    for (const key of [selectedKey, otherKey]) assert.equal(bytes.includes(Buffer.from(key.toString("base64"))), false);
    assert.equal(bytes.includes(Buffer.from("fake-private-evidence:")), false);
    assert.equal(bytes.includes(Buffer.from(data.root)), false);
  }
  for (const [name, bytes] of data.expected) assert.deepEqual(await fs.readFile(path.join(data.root, name)), bytes);
  assert.equal((await fs.stat(path.join(data.root, "candidate/som"))).mode & 0o777, 0o755);
  assert.deepEqual((await fs.readdir(data.base)).sort(), ["acceptance", "encrypted"]);
});

test("package planning keeps normal groups below 32 MiB and bounds one large file", () => {
  const record = (name, bytes) => ({ path: `run/${name}`, bytes, mode: 0o644, sha256: "a".repeat(64) });
  const groups = planPackages([record("a", CHUNK_BYTES - 1), record("b", 1), record("large", MAX_SINGLE_FILE_BYTES), record("empty", 0)]);
  assert.deepEqual(groups.map((group) => group.map((file) => file.path)), [["run/a"], ["run/b"], ["run/large"], ["run/empty"]]);
  for (const group of groups) {
    const bytes = group.reduce((sum, file) => sum + file.bytes, 0);
    assert.ok(bytes < CHUNK_BYTES || group.length === 1 && bytes <= MAX_SINGLE_FILE_BYTES);
  }
  assert.throws(() => planPackages([record("too-large", MAX_SINGLE_FILE_BYTES + 1)]), refusal);
  assert.throws(() => planPackages([record("same", 1), record("same", 1)]), refusal);
  assert.throws(() => planPackages([record("parent", 1), record("parent/child", 1)]), refusal);
  assert.throws(() => planPackages([record("../escape", 1)]), refusal);
  assert.throws(() => planPackages(Array.from({ length: 1001 }, (_, index) => record(String(index), CHUNK_BYTES))), refusal);
  assert.throws(() => planPackages([]), refusal);
});

test("wrong decrypt keys and changed archive or manifest hashes fail closed", async (t) => {
  const data = await fixture(t);
  await data.write("summary.json", Buffer.from("fake confidential result"));
  await sealEvidence({ root: data.root, out: data.out, environ: environ() });
  const { index } = await decryptOutput(data.out);
  const entry = index.packages[0];
  const archive = await fs.readFile(path.join(data.out, entry.archive));
  const manifest = JSON.parse(await fs.readFile(path.join(data.out, entry.manifest)));
  assert.throws(() => openArchive(archive, manifest, otherKey));
  assert.throws(() => openArchive(archive, { ...manifest, cipher_sha256: "0".repeat(64) }, selectedKey));
  const changed = Buffer.from(archive);
  changed[changed.length - 1] ^= 1;
  assert.throws(() => openArchive(changed, manifest, selectedKey));
  assert.throws(() => openArchive(changed, { ...manifest, cipher_sha256: sha256(changed) }, selectedKey));
  const changedManifest = structuredClone(manifest);
  changedManifest.files[0].sha256 = "0".repeat(64);
  assert.throws(() => openArchive(archive, changedManifest, selectedKey));
});

test("only the selected canonical key is decoded and no key file is made", async (t) => {
  const valid = evidenceKey({ M6_INPUTS_KEYRING: JSON.stringify({ [otherId]: "unused invalid value", [selectedId]: selectedKey.toString("base64") }) });
  assert.equal(valid.keyId, selectedId);
  assert.deepEqual(valid.key, selectedKey);
  valid.key.fill(0);
  for (const keyring of [undefined, "not-json", "[]", "{}", JSON.stringify({ bad: selectedKey.toString("base64") }),
    JSON.stringify({ [selectedId]: "wrong" }), JSON.stringify({ [selectedId]: `${selectedKey.toString("base64")}\n` })]) {
    assert.throws(() => evidenceKey({ M6_INPUTS_KEYRING: keyring }), refusal);
  }
  const data = await fixture(t);
  await data.write("summary.json");
  await assert.rejects(sealEvidence({ root: data.root, out: data.out, environ: { M6_INPUTS_KEYRING: "{}" } }), refusal);
  await assert.rejects(fs.lstat(data.out), { code: "ENOENT" });
  assert.deepEqual(await fs.readdir(data.base), ["acceptance"]);
});

test("selected symlink files and directories are refused without following them", async (t) => {
  for (const name of ["summary.json", "run", "run/input.json", "candidate", "candidate/som", "candidate/assets", "candidate/assets/input.json"]) {
    await t.test(name, async (subtest) => {
      const data = await fixture(subtest);
      await data.write("audit.json");
      const target = path.join(data.base, "outside");
      const destination = path.join(data.root, name);
      await fs.mkdir(path.dirname(destination), { recursive: true });
      if (["run", "candidate", "candidate/assets"].includes(name)) {
        await fs.mkdir(target);
        await fs.writeFile(path.join(target, "private.txt"), "fake outside bytes");
      } else await fs.writeFile(target, "fake outside bytes");
      await fs.symlink(target, destination);
      await assert.rejects(sealEvidence({ root: data.root, out: data.out, environ: environ() }), refusal);
      await assert.rejects(fs.lstat(data.out), { code: "ENOENT" });
      assert.equal((await fs.lstat(destination)).isSymbolicLink(), true);
    });
  }
});

test("excluded symlinks and unrelated private files are not opened or retained", async (t) => {
  const data = await fixture(t);
  await data.write("summary.json");
  await fs.mkdir(path.join(data.root, "run"));
  await fs.mkdir(path.join(data.root, "candidate"));
  const outside = path.join(data.base, "unselected-outside");
  await fs.mkdir(outside);
  await fs.writeFile(path.join(outside, "private.txt"), "fake excluded private bytes");
  for (const name of ["run/node_modules", "run/pgdata", "run/__pycache__", "candidate/target", "candidate/other-scratch", "unrelated-private"]) {
    await fs.symlink(outside, path.join(data.root, name));
  }
  await sealEvidence({ root: data.root, out: data.out, environ: environ() });
  assert.deepEqual((await decryptOutput(data.out)).files, data.expected);
});

test("a selected oversized file fails before any encrypted output is published", async (t) => {
  const data = await fixture(t);
  await data.write("summary.json");
  await data.huge("run/too-large.raw");
  await assert.rejects(sealEvidence({ root: data.root, out: data.out, environ: environ() }), refusal);
  await assert.rejects(fs.lstat(data.out), { code: "ENOENT" });
  assert.deepEqual(await fs.readdir(data.base), ["acceptance"]);
});

test("a nonempty output stays unchanged and an empty output becomes private", async (t) => {
  const data = await fixture(t);
  await data.write("summary.json");
  await fs.mkdir(data.out);
  const sentinel = path.join(data.out, "package-000.sealed");
  await fs.writeFile(sentinel, "keep fake existing bytes");
  await assert.rejects(sealEvidence({ root: data.root, out: data.out, environ: environ() }), refusal);
  assert.equal(await fs.readFile(sentinel, "utf8"), "keep fake existing bytes");
  assert.deepEqual(await fs.readdir(data.out), ["package-000.sealed"]);
  await fs.unlink(sentinel);
  await fs.chmod(data.out, 0o755);
  await sealEvidence({ root: data.root, out: data.out, environ: environ() });
  assert.equal((await fs.stat(data.out)).mode & 0o777, 0o700);
  assert.deepEqual((await decryptOutput(data.out)).files, data.expected);
});

test("root and output paths must be physical and output must stay outside source", async (t) => {
  const data = await fixture(t);
  await data.write("summary.json");
  await data.write("run/input.json");
  const alias = path.join(data.base, "root-alias");
  await fs.symlink(data.root, alias);
  await assert.rejects(sealEvidence({ root: alias, out: data.out, environ: environ() }), refusal);
  await assert.rejects(sealEvidence({ root: data.root, out: path.join(data.root, "run/output"), environ: environ() }), refusal);
  await assert.rejects(sealEvidence({ root: `${data.root}/../acceptance`, out: data.out, environ: environ() }), refusal);
  const parent = path.join(data.base, "real-parent");
  const linked = path.join(data.base, "linked-parent");
  await fs.mkdir(parent);
  await fs.symlink(parent, linked);
  await assert.rejects(sealEvidence({ root: data.root, out: path.join(linked, "output"), environ: environ() }), refusal);
  await fs.symlink(parent, data.out);
  await assert.rejects(sealEvidence({ root: data.root, out: data.out, environ: environ() }), refusal);
  assert.deepEqual(await fs.readdir(parent), []);
  assert.deepEqual((await collectEvidence(data.root)).map((record) => record.path), ["run/input.json", "summary.json"]);
});

test("CLI reports counts only and uses generic failure text", async (t) => {
  const data = await fixture(t);
  const secretBytes = "fake confidential source bytes must remain encrypted";
  await data.write("summary.json", Buffer.from(secretBytes));
  const success = spawnSync(process.execPath, [SCRIPT, "--root", data.root, "--out", data.out], {
    encoding: "utf8", env: { ...process.env, ...environ() },
  });
  assert.equal(success.status, 0);
  assert.deepEqual(JSON.parse(success.stdout), { package_count: 1, file_count: 1 });
  assert.equal(success.stderr, "");
  for (const forbidden of [data.root, data.out, secretBytes, selectedKey.toString("base64"), otherKey.toString("base64")]) assert.equal(success.stdout.includes(forbidden), false);
  const failure = spawnSync(process.execPath, [SCRIPT, "--root", data.root, "--out", data.out], {
    encoding: "utf8", env: { ...process.env, ...environ() },
  });
  assert.equal(failure.status, 1);
  assert.equal(failure.stdout, "");
  assert.equal(failure.stderr, `${REFUSAL}\n`);
  const badArguments = spawnSync(process.execPath, [SCRIPT, "--key", selectedKey.toString("base64")], { encoding: "utf8" });
  assert.equal(badArguments.status, 1);
  assert.equal(badArguments.stdout, "");
  assert.equal(badArguments.stderr, `${REFUSAL}\n`);
});
