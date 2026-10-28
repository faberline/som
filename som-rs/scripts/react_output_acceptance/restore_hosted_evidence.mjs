#!/usr/bin/env node
/** Restore retained raw bytes. This tool never runs them or infers acceptance. */
import { constants } from "node:fs";
import * as fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { decodeKey, MANIFEST_VERSION, MAX_FILES, MAX_UNCOMPRESSED_BYTES,
  openArchive, sha256, validateRelativePath } from "./sealed_inputs.mjs";

export const REFUSAL = "hosted evidence restore refused";
const INDEX_VERSION = "som.m6-evidence-index.v1";
const HASH = /^[a-f0-9]{64}$/;
const INDEX_LIMIT = 1024 * 1024;
const ARCHIVE_LIMIT = MAX_UNCOMPRESSED_BYTES + 1024 * 1024;
const MAX_PACKAGES = 1000;

export class EvidenceRestoreError extends Error {
  constructor() {
    super(REFUSAL);
    this.name = "EvidenceRestoreError";
  }
}

function need(condition) {
  if (!condition) throw new EvidenceRestoreError();
}

function exactKeys(value, keys) {
  need(value !== null && typeof value === "object" && !Array.isArray(value));
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  need(actual.length === expected.length && actual.every((name, index) => name === expected[index]));
}

function integer(value, maximum) {
  return Number.isSafeInteger(value) && value >= 0 && value <= maximum;
}

function absolute(value) {
  need(typeof value === "string" && path.isAbsolute(value) && path.resolve(value) === value && !value.includes("\0"));
  return value;
}

async function missingOrStat(filename) {
  try {
    return await fs.lstat(filename);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

async function physicalDirectory(directory) {
  absolute(directory);
  const root = path.parse(directory).root;
  let current = root;
  const rootInfo = await fs.lstat(root);
  need(rootInfo.isDirectory() && !rootInfo.isSymbolicLink());
  for (const component of directory.slice(root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    const info = await fs.lstat(current);
    need(info.isDirectory() && !info.isSymbolicLink());
  }
}

function sameFile(left, right) {
  return left.dev === right.dev && left.ino === right.ino && left.size === right.size
    && left.mtimeMs === right.mtimeMs && left.ctimeMs === right.ctimeMs && left.mode === right.mode;
}

async function boundedRegular(filename, limit, sources) {
  await physicalDirectory(path.dirname(filename));
  const declared = await fs.lstat(filename);
  need(declared.isFile() && !declared.isSymbolicLink() && integer(declared.size, limit));
  const handle = await fs.open(filename, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const before = await handle.stat();
    need(before.isFile() && integer(before.size, limit) && sameFile(declared, before));
    const data = Buffer.alloc(before.size);
    let offset = 0;
    while (offset < data.length) {
      const result = await handle.read(data, offset, Math.min(64 * 1024, data.length - offset), null);
      need(result.bytesRead > 0);
      offset += result.bytesRead;
    }
    const after = await handle.stat();
    need(sameFile(before, after));
    await physicalDirectory(path.dirname(filename));
    const current = await fs.lstat(filename);
    need(!current.isSymbolicLink() && sameFile(before, current));
    sources.push({ filename, info: before });
    return data;
  } finally {
    await handle.close();
  }
}

function selectedKey(index, environ) {
  need(typeof environ.M6_INPUTS_KEYRING === "string");
  const ring = JSON.parse(environ.M6_INPUTS_KEYRING);
  need(ring !== null && typeof ring === "object" && !Array.isArray(ring));
  need(Object.keys(ring).every((name) => HASH.test(name)) && Object.hasOwn(ring, index.key_id));
  return decodeKey(ring[index.key_id]);
}

function validateIndex(index) {
  exactKeys(index, ["version", "key_id", "packages", "status", "product_acceptance"]);
  need(index.version === INDEX_VERSION && typeof index.key_id === "string" && HASH.test(index.key_id));
  need(index.status === "encrypted" && index.product_acceptance === "read-summary");
  need(Array.isArray(index.packages) && index.packages.length > 0 && index.packages.length <= MAX_PACKAGES);
  for (const [position, entry] of index.packages.entries()) {
    exactKeys(entry, ["archive", "manifest", "manifest_sha256", "cipher_sha256", "total_files", "bytes"]);
    const name = `package-${String(position).padStart(3, "0")}`;
    need(entry.archive === `${name}.sealed` && entry.manifest === `${name}.manifest.json`);
    need(typeof entry.manifest_sha256 === "string" && HASH.test(entry.manifest_sha256)
      && typeof entry.cipher_sha256 === "string" && HASH.test(entry.cipher_sha256));
    need(integer(entry.total_files, MAX_FILES) && entry.total_files > 0 && integer(entry.bytes, MAX_UNCOMPRESSED_BYTES));
  }
}

function inspectManifest(manifest, entry, names, parents) {
  exactKeys(manifest, ["version", "cipher_sha256", "uncompressed_bytes", "total_files", "files"]);
  need(manifest.version === MANIFEST_VERSION && manifest.cipher_sha256 === entry.cipher_sha256);
  need(integer(manifest.uncompressed_bytes, MAX_UNCOMPRESSED_BYTES)
    && manifest.total_files === entry.total_files && Array.isArray(manifest.files)
    && manifest.files.length === entry.total_files);
  let bytes = 0;
  for (const file of manifest.files) {
    exactKeys(file, ["path", "sha256", "bytes", "mode"]);
    const name = validateRelativePath(file.path);
    need(typeof file.sha256 === "string" && HASH.test(file.sha256)
      && integer(file.bytes, MAX_UNCOMPRESSED_BYTES) && (file.mode === 0o644 || file.mode === 0o755));
    need(!names.has(name) && !parents.has(name));
    const parts = name.split("/");
    for (let index = 1; index < parts.length; index += 1) {
      const parent = parts.slice(0, index).join("/");
      need(!names.has(parent));
      parents.add(parent);
    }
    names.add(name);
    bytes += file.bytes;
    need(integer(bytes, MAX_UNCOMPRESSED_BYTES));
  }
  need(bytes === entry.bytes);
}

async function writePackage(stage, files) {
  for (const file of files) {
    const filename = path.join(stage, ...file.path.split("/"));
    need(filename.startsWith(stage + path.sep));
    await fs.mkdir(path.dirname(filename), { recursive: true, mode: 0o700 });
    await physicalDirectory(path.dirname(filename));
    const handle = await fs.open(filename, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, file.mode);
    try {
      await handle.writeFile(file.data);
      await handle.chmod(file.mode);
    } finally {
      await handle.close();
    }
  }
}

/** Authenticate one package at a time; publish only after the full archive passes. */
export async function restoreEvidence({ root, out, indexSha256, environ = process.env }) {
  let key = null;
  let stage = null;
  let reservation = null;
  try {
    absolute(root);
    absolute(out);
    need(typeof indexSha256 === "string" && HASH.test(indexSha256));
    need(root !== out && !out.startsWith(root + path.sep) && !root.startsWith(out + path.sep));
    await physicalDirectory(root);
    await physicalDirectory(path.dirname(out));
    need((await missingOrStat(out)) === null);
    const sources = [];
    const indexBytes = await boundedRegular(path.join(root, "evidence-index.json"), INDEX_LIMIT, sources);
    need(sha256(indexBytes) === indexSha256);
    const index = JSON.parse(indexBytes.toString("utf8"));
    validateIndex(index);
    const expected = new Set(["evidence-index.json", ...index.packages.flatMap((entry) => [entry.archive, entry.manifest])]);
    const entries = await fs.readdir(root, { withFileTypes: true });
    need(entries.length === expected.size && entries.every((entry) => entry.isFile() && expected.has(entry.name)));
    key = selectedKey(index, environ);
    stage = await fs.mkdtemp(path.join(path.dirname(out), ".som-m6-evidence-restore-"));
    await fs.chmod(stage, 0o700);
    const names = new Set();
    const parents = new Set();
    let bytes = 0;
    for (const entry of index.packages) {
      const manifestBytes = await boundedRegular(path.join(root, entry.manifest), MAX_UNCOMPRESSED_BYTES, sources);
      need(sha256(manifestBytes) === entry.manifest_sha256);
      const manifest = JSON.parse(manifestBytes.toString("utf8"));
      inspectManifest(manifest, entry, names, parents);
      const archive = await boundedRegular(path.join(root, entry.archive), ARCHIVE_LIMIT, sources);
      need(sha256(archive) === entry.cipher_sha256);
      const files = openArchive(archive, manifest, key);
      need(files.length === entry.total_files && files.reduce((total, file) => total + file.data.length, 0) === entry.bytes);
      await writePackage(stage, files);
      bytes += entry.bytes;
      need(Number.isSafeInteger(bytes));
    }
    need(names.size === index.packages.reduce((total, entry) => total + entry.total_files, 0));
    for (const source of sources) {
      await physicalDirectory(path.dirname(source.filename));
      const info = await fs.lstat(source.filename);
      need(!info.isSymbolicLink() && sameFile(source.info, info));
    }
    await physicalDirectory(path.dirname(out));
    // Reserve the absent name atomically. Never rename over a pre-existing directory.
    await fs.mkdir(out, { mode: 0o700 });
    reservation = await fs.lstat(out);
    need(reservation.isDirectory() && !reservation.isSymbolicLink() && (await fs.readdir(out)).length === 0);
    const current = await fs.lstat(out);
    need(sameFile(reservation, current));
    await fs.rename(stage, out);
    stage = null;
    reservation = null;
    return { version: "som.m6-evidence-restore-receipt.v1", status: "restored", index_sha256: indexSha256,
      package_count: index.packages.length, file_count: names.size, bytes, product_acceptance: "not_inferred" };
  } catch {
    throw new EvidenceRestoreError();
  } finally {
    if (key !== null) key.fill(0);
    if (stage !== null) await fs.rm(stage, { recursive: true, force: true }).catch(() => {});
    if (reservation !== null) {
      try {
        const current = await fs.lstat(out);
        if (sameFile(reservation, current) && current.isDirectory() && !current.isSymbolicLink()
          && (await fs.readdir(out)).length === 0) await fs.rmdir(out);
      } catch {}
    }
  }
}

function argumentsFor(argv) {
  need(argv.length === 6);
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    need(["--root", "--out", "--index-sha256"].includes(argv[index]) && !Object.hasOwn(values, argv[index]));
    need(typeof argv[index + 1] === "string" && argv[index + 1].length > 0);
    values[argv[index]] = argv[index + 1];
  }
  need(Object.keys(values).length === 3);
  return { root: values["--root"], out: values["--out"], indexSha256: values["--index-sha256"] };
}

export async function main(argv = process.argv.slice(2)) {
  try {
    const receipt = await restoreEvidence(argumentsFor(argv));
    process.stdout.write(`${JSON.stringify(receipt)}\n`);
    return 0;
  } catch {
    process.stderr.write(`${REFUSAL}\n`);
    return 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
