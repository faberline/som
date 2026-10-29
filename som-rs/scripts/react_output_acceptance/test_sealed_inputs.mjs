import assert from "node:assert/strict";
import { createCipheriv, randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createGzip, gzipSync } from "node:zlib";
import {
  MANIFEST_VERSION,
  MAX_FILES,
  MAX_UNCOMPRESSED_BYTES,
  SealedInputError,
  VERSION,
  decodeKey,
  openArchive,
  restoreInputs,
  sealFiles,
  sealSelection,
  sha256,
  validateRelativePath,
} from "./sealed_inputs.mjs";

// Every fixture is made here. No historical prompt or private file is read.
const SCRIPT = fileURLToPath(new URL("./sealed_inputs.mjs", import.meta.url));
const refusal = { name: "SealedInputError", message: "sealed input operation refused" };
const fakeFiles = () => [
  { path: "fake/case/input.bin", mode: 0o644, data: Buffer.from([0, 255, 128, 13, 10, 0, 42]) },
  { path: "fake/helper.sh", mode: 0o755, data: Buffer.from("#!/bin/sh\nprintf fake-only\\n\n") },
  { path: "empty.txt", mode: 0o644, data: Buffer.alloc(0) },
];
const copy = (value) => JSON.parse(JSON.stringify(value));

async function temporary(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "som-m6-sealed-test-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  return root;
}

async function writeCarrier(root, sealed = sealFiles(fakeFiles())) {
  const archive = path.join(root, "inputs.enc");
  const manifest = path.join(root, "manifest.json");
  await fs.writeFile(archive, sealed.archive);
  await fs.writeFile(manifest, JSON.stringify(sealed.manifest));
  return { archive, manifest, key: sealed.key.toString("base64"), sealed };
}

// A test-only encoder makes malformed but authenticated payloads. It does not
// call a renderer, compiler, shell, formatter, or any input from the repository.
function authenticatedBytes(compressed, key = randomBytes(32)) {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key, iv, { authTagLength: 16 });
  cipher.setAAD(Buffer.from(VERSION, "utf8"));
  const ciphertext = Buffer.concat([cipher.update(compressed), cipher.final()]);
  const archive = Buffer.concat([
    Buffer.from("SOM.M6.SEALED-INPUTS.V1\0", "ascii"), iv, cipher.getAuthTag(), ciphertext,
  ]);
  return { archive, key };
}

function authenticatedPayload(payload, manifestFiles) {
  const plaintext = Buffer.from(JSON.stringify(payload));
  const sealed = authenticatedBytes(gzipSync(plaintext));
  return {
    ...sealed,
    manifest: {
      version: MANIFEST_VERSION,
      cipher_sha256: sha256(sealed.archive),
      uncompressed_bytes: plaintext.length,
      total_files: manifestFiles.length,
      files: manifestFiles,
    },
  };
}

test("pure byte API keeps binary bytes, names, modes, and an empty file", () => {
  const files = fakeFiles();
  const sealed = sealFiles(files);
  assert.equal(sealed.key.length, 32);
  assert.equal(sealed.manifest.version, MANIFEST_VERSION);
  assert.equal(sealed.manifest.cipher_sha256, sha256(sealed.archive));
  assert.equal(sealed.manifest.total_files, files.length);
  assert.deepEqual(openArchive(sealed.archive, sealed.manifest, sealed.key), files);
  assert.deepEqual(openArchive(sealed.archive, sealed.manifest, sealed.key.toString("base64")), files);
  assert.notDeepEqual(sealFiles(files).archive, sealed.archive);
  assert.equal(sealed.archive.includes(files[1].data), false);
});

test("key input uses one canonical 32-byte base64 value", () => {
  const encoded = randomBytes(32).toString("base64");
  assert.equal(decodeKey(encoded).toString("base64"), encoded);
  for (const value of ["", "secret", `${encoded}\n`, encoded.slice(0, -1), Buffer.alloc(31).toString("base64")]) {
    assert.throws(() => decodeKey(value), refusal);
  }
});

test("wrong key, ciphertext changes, tag changes, and truncation fail", () => {
  const sealed = sealFiles(fakeFiles());
  assert.throws(() => openArchive(sealed.archive, sealed.manifest, randomBytes(32)), refusal);
  for (const offset of [0, 25, 40, sealed.archive.length - 1]) {
    const archive = Buffer.from(sealed.archive);
    archive[offset] ^= 1;
    assert.throws(() => openArchive(archive, sealed.manifest, sealed.key), refusal);
    // Updating the public hash must not bypass the authentication check.
    const manifest = { ...sealed.manifest, cipher_sha256: sha256(archive) };
    assert.throws(() => openArchive(archive, manifest, sealed.key), refusal);
  }
  for (const size of [0, 1, 22, 49, sealed.archive.length - 1]) {
    const archive = sealed.archive.subarray(0, size);
    const manifest = { ...sealed.manifest, cipher_sha256: sha256(archive) };
    assert.throws(() => openArchive(archive, manifest, sealed.key), refusal);
  }
});

test("manifest hash, count, sizes, modes, extra entries, and missing entries fail", () => {
  const sealed = sealFiles(fakeFiles());
  const mutations = [
    (manifest) => { manifest.version = "unknown"; },
    (manifest) => { manifest.cipher_sha256 = "0".repeat(64); },
    (manifest) => { manifest.uncompressed_bytes += 1; },
    (manifest) => { manifest.total_files += 1; },
    (manifest) => { manifest.files[0].sha256 = "0".repeat(64); },
    (manifest) => { manifest.files[0].bytes += 1; },
    (manifest) => { manifest.files[0].mode = 0o777; },
    (manifest) => { manifest.files[0].mode = 0o755; },
    (manifest) => { manifest.files.push({ ...manifest.files[0], path: "extra.txt" }); manifest.total_files += 1; },
    (manifest) => { manifest.files.pop(); manifest.total_files -= 1; },
    (manifest) => { manifest.files[0].path = "missing.txt"; },
    (manifest) => { manifest.source = "/do-not-keep-source-paths"; },
  ];
  for (const mutate of mutations) {
    const manifest = copy(sealed.manifest);
    mutate(manifest);
    assert.throws(() => openArchive(sealed.archive, manifest, sealed.key), refusal);
  }
});

test("all paths are canonical relative POSIX file names", () => {
  for (const value of ["../escape", "/absolute", "a/../b", "./a", "a//b", "a/", "a\\b", "a\0b", "C:/escape", "C:escape", "", "a\nb", "\ud800"]) {
    assert.throws(() => validateRelativePath(value), refusal);
    assert.throws(() => sealFiles([{ path: value, mode: 0o644, data: Buffer.from("fake") }]), refusal);
  }
  assert.equal(validateRelativePath("fake/unicode-資料.bin"), "fake/unicode-資料.bin");
  for (const names of [["same", "same"], ["a", "a/b"], ["a", "a-b", "a/b"], ["a/b", "a"]]) {
    assert.throws(() => sealFiles(names.map((name) => ({ path: name, mode: 0o644, data: Buffer.alloc(0) }))), refusal);
  }
});

test("authenticated malformed file maps still fail exact validation", () => {
  const data = Buffer.from("fake");
  const record = { path: "fake.bin", mode: 0o644, bytes: data.length, sha256: sha256(data) };
  const rows = [
    { version: VERSION, files: [{ path: "../escape", mode: 0o644, data: data.toString("base64") }] },
    { version: VERSION, files: [{ path: record.path, mode: 0o644, data: `${data.toString("base64")}!` }] },
    { version: VERSION, files: [{ path: record.path, mode: 0o644, data: data.toString("base64"), source: "/private/fake" }] },
    { version: VERSION, files: [{ path: "other.bin", mode: 0o644, data: data.toString("base64") }] },
    { version: VERSION, files: [{ path: record.path, mode: 0o644, data: "" }] },
    { version: "unknown", files: [{ path: record.path, mode: 0o644, data: data.toString("base64") }] },
  ];
  for (const payload of rows) {
    const sealed = authenticatedPayload(payload, [record]);
    assert.throws(() => openArchive(sealed.archive, sealed.manifest, sealed.key), refusal);
  }
});

test("file count and claimed output size have hard limits", () => {
  const sealed = sealFiles(fakeFiles());
  for (const field of ["total_files", "uncompressed_bytes"]) {
    const manifest = copy(sealed.manifest);
    manifest[field] = field === "total_files" ? MAX_FILES + 1 : MAX_UNCOMPRESSED_BYTES + 1;
    assert.throws(() => openArchive(sealed.archive, manifest, sealed.key), refusal);
  }
  assert.throws(() => sealFiles(Array.from({ length: MAX_FILES + 1 }, (_, index) => ({ path: `fake-${index}`, mode: 0o644, data: Buffer.alloc(0) }))), refusal);
});

test("gzip output is bounded even for an authenticated compression bomb", async () => {
  const chunk = Buffer.alloc(64 * 1024, 32);
  function* chunks() {
    for (let size = 0; size <= MAX_UNCOMPRESSED_BYTES; size += chunk.length) yield chunk;
  }
  const zipped = [];
  for await (const bytes of Readable.from(chunks()).pipe(createGzip())) zipped.push(bytes);
  const sealed = authenticatedBytes(Buffer.concat(zipped));
  const manifest = {
    version: MANIFEST_VERSION, cipher_sha256: sha256(sealed.archive),
    uncompressed_bytes: MAX_UNCOMPRESSED_BYTES, total_files: 0, files: [],
  };
  assert.throws(() => openArchive(sealed.archive, manifest, sealed.key), refusal);
});

test("seal and restore use only new files and preserve modes", async (t) => {
  const root = await temporary(t);
  const source = path.join(root, "source.bin");
  const data = fakeFiles()[0].data;
  await fs.writeFile(source, data);
  const selection = path.join(root, "selection.json");
  await fs.writeFile(selection, JSON.stringify({ files: [{ source, path: "fake/case.bin", mode: 0o755 }] }));
  const archive = path.join(root, "inputs.enc");
  const manifest = path.join(root, "manifest.json");
  const keyFile = path.join(root, "secret.key");
  await sealSelection({ selection, archive, manifest, keyFile });
  const key = await fs.readFile(keyFile, "utf8");
  assert.equal(decodeKey(key).length, 32);
  assert.equal((await fs.stat(keyFile)).mode & 0o777, 0o600);
  assert.equal((await fs.readFile(manifest, "utf8")).includes(source), false);
  const out = path.join(root, "restored");
  assert.deepEqual(await restoreInputs({ archive, manifest, out, key }), { total_files: 1 });
  assert.deepEqual(await fs.readFile(path.join(out, "fake/case.bin")), data);
  assert.equal((await fs.stat(path.join(out, "fake/case.bin"))).mode & 0o777, 0o755);
  await assert.rejects(sealSelection({ selection, archive, manifest, keyFile }), refusal);
  await assert.rejects(restoreInputs({ archive, manifest, out, key }), refusal);
  assert.deepEqual(await fs.readFile(path.join(out, "fake/case.bin")), data);
});

test("restore accepts an empty directory and does not run executable content", async (t) => {
  const root = await temporary(t);
  const marker = path.join(root, "must-not-exist");
  const script = Buffer.from(`#!/bin/sh\ntouch '${marker}'\n`);
  const carrier = await writeCarrier(root, sealFiles([{ path: "fake-only.sh", mode: 0o755, data: script }]));
  const out = path.join(root, "empty");
  await fs.mkdir(out);
  await restoreInputs({ ...carrier, out });
  assert.deepEqual(await fs.readFile(path.join(out, "fake-only.sh")), script);
  await assert.rejects(fs.lstat(marker), { code: "ENOENT" });
});

test("invalid inputs do not create an output directory", async (t) => {
  const root = await temporary(t);
  const carrier = await writeCarrier(root);
  const out = path.join(root, "must-not-be-created");
  const manifest = copy(carrier.sealed.manifest);
  manifest.files[1].sha256 = "0".repeat(64);
  await fs.writeFile(carrier.manifest, JSON.stringify(manifest));
  await assert.rejects(restoreInputs({ ...carrier, out }), refusal);
  await assert.rejects(fs.lstat(out), { code: "ENOENT" });
  await fs.writeFile(carrier.manifest, JSON.stringify(carrier.sealed.manifest));
  await assert.rejects(restoreInputs({ ...carrier, out, key: randomBytes(32).toString("base64") }), refusal);
  await assert.rejects(fs.lstat(out), { code: "ENOENT" });
});

test("nonempty output, a file output, and symbolic link output never overwrite", async (t) => {
  const root = await temporary(t);
  const carrier = await writeCarrier(root);
  const out = path.join(root, "nonempty");
  await fs.mkdir(out);
  const sentinel = path.join(out, "sentinel");
  await fs.writeFile(sentinel, "keep");
  await assert.rejects(restoreInputs({ ...carrier, out }), refusal);
  assert.deepEqual(await fs.readdir(out), ["sentinel"]);
  assert.equal(await fs.readFile(sentinel, "utf8"), "keep");
  await assert.rejects(restoreInputs({ ...carrier, out: sentinel }), refusal);
  const empty = path.join(root, "real-empty");
  await fs.mkdir(empty);
  const link = path.join(root, "link");
  await fs.symlink(empty, link);
  await assert.rejects(restoreInputs({ ...carrier, out: link }), refusal);
  await assert.rejects(restoreInputs({ ...carrier, out: path.join(link, "child") }), refusal);
  assert.deepEqual(await fs.readdir(empty), []);
});

test("a symbolic link ancestor is refused even when it points back inside the output tree", async (t) => {
  const root = await temporary(t);
  const carrier = await writeCarrier(root);
  const real = path.join(root, "real");
  await fs.mkdir(real);
  const link = path.join(root, "parent-link");
  await fs.symlink(real, link);
  await assert.rejects(restoreInputs({ ...carrier, out: path.join(link, "new", "nested") }), refusal);
  assert.deepEqual(await fs.readdir(real), []);
});

test("seal rejects symlink sources, relative sources, duplicate outputs, and existing outputs", async (t) => {
  const root = await temporary(t);
  const source = path.join(root, "source.bin");
  const link = path.join(root, "source-link");
  await fs.writeFile(source, "fake");
  await fs.symlink(source, link);
  const selection = path.join(root, "selection.json");
  const outputs = { archive: path.join(root, "new.enc"), manifest: path.join(root, "new.json"), keyFile: path.join(root, "new.key") };
  for (const chosen of [link, "relative-source.bin"]) {
    await fs.writeFile(selection, JSON.stringify({ files: [{ source: chosen, path: "fake.bin", mode: 0o644 }] }));
    await assert.rejects(sealSelection({ selection, ...outputs }), refusal);
    for (const destination of Object.values(outputs)) await assert.rejects(fs.lstat(destination), { code: "ENOENT" });
  }
  await fs.writeFile(selection, JSON.stringify({ files: [{ source, path: "fake.bin", mode: 0o644 }] }));
  await assert.rejects(sealSelection({ selection, ...outputs, keyFile: outputs.archive }), refusal);
  await fs.writeFile(outputs.archive, "keep");
  await assert.rejects(sealSelection({ selection, ...outputs }), refusal);
  assert.equal(await fs.readFile(outputs.archive, "utf8"), "keep");
  await assert.rejects(fs.lstat(outputs.keyFile), { code: "ENOENT" });
  await assert.rejects(fs.lstat(outputs.manifest), { code: "ENOENT" });
});

test("seal rejects a symlink hidden by a parent segment before opening the source", async (t) => {
  const root = await temporary(t);
  const safe = path.join(root, "safe");
  const other = path.join(root, "other");
  await fs.mkdir(safe);
  await fs.mkdir(path.join(other, "nested"), { recursive: true });
  const safeBytes = "fake-safe-source-bytes";
  const hiddenBytes = "fake-hidden-source-bytes";
  await fs.writeFile(path.join(safe, "private.bin"), safeBytes);
  await fs.writeFile(path.join(other, "private.bin"), hiddenBytes);
  await fs.symlink(path.join(other, "nested"), path.join(safe, "link"));
  // Do not use path.join here: it would remove the parent segment in the test.
  const source = `${safe}/link/../private.bin`;
  assert.equal(await fs.readFile(source, "utf8"), hiddenBytes);
  assert.equal(await fs.readFile(path.resolve(source), "utf8"), safeBytes);
  const selection = path.join(root, "selection.json");
  await fs.writeFile(selection, JSON.stringify({ files: [{ source, path: "fake.bin", mode: 0o644 }] }));
  const outputs = { archive: path.join(root, "new.enc"), manifest: path.join(root, "new.json"), keyFile: path.join(root, "new.key") };
  await assert.rejects(sealSelection({ selection, ...outputs }), refusal);
  for (const destination of Object.values(outputs)) await assert.rejects(fs.lstat(destination), { code: "ENOENT" });
  const cli = spawnSync(process.execPath, [SCRIPT, "seal", "--selection", selection, "--archive", outputs.archive,
    "--manifest", outputs.manifest, "--key-file", outputs.keyFile], { encoding: "utf8" });
  assert.equal(cli.status, 1);
  assert.equal(cli.stdout, "");
  assert.equal(cli.stderr, "sealed input operation refused\n");
  for (const secret of [root, source, safeBytes, hiddenBytes]) assert.equal(cli.stderr.includes(secret), false);
  for (const destination of Object.values(outputs)) await assert.rejects(fs.lstat(destination), { code: "ENOENT" });
});

test("CLI uses the environment key and reports only a generic failure", async (t) => {
  const root = await temporary(t);
  const carrier = await writeCarrier(root);
  const out = path.join(root, "cli-output");
  const success = spawnSync(process.execPath, [SCRIPT, "restore", "--archive", carrier.archive, "--manifest", carrier.manifest, "--out", out], {
    encoding: "utf8", env: { ...process.env, M6_INPUTS_KEY: carrier.key },
  });
  assert.equal(success.status, 0);
  assert.equal(success.stdout, "");
  assert.equal(success.stderr, "");
  const failure = spawnSync(process.execPath, [SCRIPT, "restore", "--archive", carrier.archive, "--manifest", carrier.manifest, "--out", out], {
    encoding: "utf8", env: { ...process.env, M6_INPUTS_KEY: "fake-secret-that-must-not-be-printed" },
  });
  assert.equal(failure.status, 1);
  assert.equal(failure.stdout, "");
  assert.equal(failure.stderr, "sealed input operation refused\n");
  for (const forbidden of [root, carrier.key, "fake-secret-that-must-not-be-printed"]) assert.equal(failure.stderr.includes(forbidden), false);
  const invalidArgs = spawnSync(process.execPath, [SCRIPT, "restore", "--key", "fake-secret-that-must-not-be-printed"], { encoding: "utf8" });
  assert.equal(invalidArgs.status, 1);
  assert.equal(invalidArgs.stderr, "sealed input operation refused\n");
  assert.equal(SealedInputError.prototype instanceof Error, true);
});
