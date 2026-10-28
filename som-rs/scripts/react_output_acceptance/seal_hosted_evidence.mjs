#!/usr/bin/env node
/** Retain raw acceptance evidence as encrypted files. Never infer acceptance. */
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import * as fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { decodeKey, MAX_FILES, sealFiles, sha256, validateRelativePath } from "./sealed_inputs.mjs";

export const INDEX_VERSION = "som.m6-evidence-index.v1";
export const CHUNK_BYTES = 32 * 1024 * 1024;
export const MAX_SINGLE_FILE_BYTES = 128 * 1024 * 1024;
export const REFUSAL = "hosted evidence sealing refused";
const EXCLUDED = new Set(["node_modules", "pgdata", "__pycache__"]);
const CANDIDATE_FILES = new Set([
  "som", "build.json", "runtime.json", "source.json", "source.tar", "server.log",
  "assets-package.stdout", "assets-package.stderr",
]);
const HASH = /^[a-f0-9]{64}$/;

export class EvidenceSealError extends Error {
  constructor() {
    super(REFUSAL);
    this.name = "EvidenceSealError";
  }
}

function need(condition) {
  if (!condition) throw new EvidenceSealError();
}

function canonicalAbsolute(value) {
  need(typeof value === "string" && path.isAbsolute(value) && path.resolve(value) === value);
  need(!value.includes("\0"));
  return value;
}

async function lstatOrMissing(filename) {
  try {
    return await fs.lstat(filename);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

/** Every existing directory component must be a physical directory. */
async function physicalDirectory(directory) {
  canonicalAbsolute(directory);
  const root = path.parse(directory).root;
  let current = root;
  const rootInfo = await fs.lstat(root);
  need(rootInfo.isDirectory() && !rootInfo.isSymbolicLink());
  for (const part of directory.slice(root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const info = await fs.lstat(current);
    need(info.isDirectory() && !info.isSymbolicLink());
  }
}

export function evidenceKey(environ = process.env) {
  try {
    need(typeof environ.M6_INPUTS_KEYRING === "string");
    const ring = JSON.parse(environ.M6_INPUTS_KEYRING);
    need(ring !== null && typeof ring === "object" && !Array.isArray(ring));
    const names = Object.keys(ring).sort();
    need(names.length > 0 && names.every((name) => HASH.test(name)));
    const keyId = names[0];
    return { keyId, key: decodeKey(ring[keyId]) };
  } catch {
    throw new EvidenceSealError();
  }
}

function sameFile(left, right) {
  return left.dev === right.dev && left.ino === right.ino && left.size === right.size
    && left.mtimeMs === right.mtimeMs && left.mode === right.mode;
}

async function readSource(filename, expected = null, keepBytes = false) {
  canonicalAbsolute(filename);
  await physicalDirectory(path.dirname(filename));
  const handle = await fs.open(filename, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const before = await handle.stat();
    need(before.isFile() && before.size <= MAX_SINGLE_FILE_BYTES);
    if (expected !== null) need(sameFile(before, expected.info));
    const hash = createHash("sha256");
    const chunks = [];
    const scratch = Buffer.alloc(64 * 1024);
    let bytes = 0;
    while (true) {
      const { bytesRead } = await handle.read(scratch, 0, scratch.length, null);
      if (bytesRead === 0) break;
      bytes += bytesRead;
      need(bytes <= MAX_SINGLE_FILE_BYTES);
      const chunk = scratch.subarray(0, bytesRead);
      hash.update(chunk);
      if (keepBytes) chunks.push(Buffer.from(chunk));
    }
    const after = await handle.stat();
    need(bytes === before.size && sameFile(before, after));
    await physicalDirectory(path.dirname(filename));
    const current = await fs.lstat(filename);
    need(!current.isSymbolicLink() && sameFile(before, current));
    const fileHash = hash.digest("hex");
    if (expected !== null) need(bytes === expected.bytes && fileHash === expected.sha256);
    return { info: before, bytes, sha256: fileHash, data: keepBytes ? Buffer.concat(chunks, bytes) : null };
  } finally {
    await handle.close();
  }
}

/** Read only the named top-level files, raw run tree, and candidate identity. */
export async function collectEvidence(root) {
  try {
    canonicalAbsolute(root);
    await physicalDirectory(root);
    const records = [];

    async function addFile(filename) {
      const relative = path.relative(root, filename).split(path.sep).join("/");
      validateRelativePath(relative);
      const result = await readSource(filename);
      records.push({ source: filename, path: relative, bytes: result.bytes, sha256: result.sha256,
        mode: result.info.mode & 0o111 ? 0o755 : 0o644, info: result.info });
    }

    async function walk(directory) {
      await physicalDirectory(directory);
      for (const entry of (await fs.readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
        if (EXCLUDED.has(entry.name)) continue;
        const filename = path.join(directory, entry.name);
        const info = await fs.lstat(filename);
        need(!info.isSymbolicLink());
        if (info.isDirectory()) await walk(filename);
        else {
          need(info.isFile());
          await addFile(filename);
        }
      }
    }

    for (const entry of await fs.readdir(root, { withFileTypes: true })) {
      const filename = path.join(root, entry.name);
      if (["summary.json", "audit.json"].includes(entry.name) || /^[A-Za-z0-9][A-Za-z0-9_.-]*\.(stdout|stderr)$/.test(entry.name)) {
        await addFile(filename);
      } else if (entry.name === "run") {
        await walk(filename);
      } else if (entry.name === "candidate") {
        await physicalDirectory(filename);
        for (const candidate of await fs.readdir(filename, { withFileTypes: true })) {
          const child = path.join(filename, candidate.name);
          if (candidate.name === "assets") await walk(child);
          else if (CANDIDATE_FILES.has(candidate.name) || /^build-.*\.(stdout|stderr)$/.test(candidate.name)) await addFile(child);
        }
      }
    }
    records.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
    need(records.length > 0);
    need(new Set(records.map((record) => record.path)).size === records.length);
    return records;
  } catch {
    throw new EvidenceSealError();
  }
}

/** Small groups stay below 32 MiB. One larger file may occupy its own group. */
export function planPackages(records) {
  try {
    need(Array.isArray(records) && records.length > 0);
    const packages = [];
    let group = [];
    let bytes = 0;
    const names = new Set();
    function flush() {
      if (group.length > 0) packages.push(group);
      group = [];
      bytes = 0;
    }
    for (const record of records) {
      validateRelativePath(record.path);
      need(!names.has(record.path));
      names.add(record.path);
      need(Number.isSafeInteger(record.bytes) && record.bytes >= 0 && record.bytes <= MAX_SINGLE_FILE_BYTES);
      need(record.mode === 0o644 || record.mode === 0o755);
      need(typeof record.sha256 === "string" && HASH.test(record.sha256));
      if (record.bytes >= CHUNK_BYTES) {
        flush();
        packages.push([record]);
      } else {
        if (bytes + record.bytes >= CHUNK_BYTES || group.length === MAX_FILES) flush();
        group.push(record);
        bytes += record.bytes;
      }
    }
    flush();
    for (const name of names) {
      const parts = name.split("/");
      for (let index = 1; index < parts.length; index += 1) need(!names.has(parts.slice(0, index).join("/")));
    }
    need(packages.length <= 1000);
    return packages;
  } catch {
    throw new EvidenceSealError();
  }
}

async function outputState(out, root) {
  canonicalAbsolute(out);
  const relative = path.relative(root, out);
  need(relative !== "" && (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)));
  await physicalDirectory(path.dirname(out));
  const info = await lstatOrMissing(out);
  if (info !== null) {
    need(info.isDirectory() && !info.isSymbolicLink());
    need((await fs.readdir(out)).length === 0);
  }
  return info !== null;
}

async function privateFile(filename, bytes) {
  const handle = await fs.open(filename, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
  try {
    await handle.writeFile(bytes);
    await handle.chmod(0o600);
  } finally {
    await handle.close();
  }
}

async function publish(stage, out, root) {
  const existed = await outputState(out, root);
  const created = [];
  let madeDirectory = false;
  try {
    if (!existed) {
      await fs.mkdir(out, { mode: 0o700 });
      madeDirectory = true;
    }
    await physicalDirectory(out);
    need((await fs.readdir(out)).length === 0);
    await fs.chmod(out, 0o700);
    for (const filename of (await fs.readdir(stage)).sort()) {
      const destination = path.join(out, filename);
      // A hard link cannot replace a destination created after the empty check.
      await fs.link(path.join(stage, filename), destination);
      created.push(destination);
    }
  } catch (error) {
    await Promise.allSettled(created.map((filename) => fs.unlink(filename)));
    if (madeDirectory) await fs.rmdir(out).catch(() => {});
    throw error;
  }
}

export async function sealEvidence({ root, out, environ = process.env }) {
  let selected = null;
  let stage = null;
  try {
    canonicalAbsolute(root);
    await physicalDirectory(root);
    await outputState(out, root);
    selected = evidenceKey(environ);
    const records = await collectEvidence(root);
    const packages = planPackages(records);
    stage = await fs.mkdtemp(path.join(path.dirname(out), ".som-m6-evidence-stage-"));
    await fs.chmod(stage, 0o700);
    const entries = [];
    for (const [index, group] of packages.entries()) {
      const files = [];
      for (const record of group) {
        const result = await readSource(record.source, record, true);
        files.push({ path: record.path, mode: record.mode, data: result.data });
      }
      const sealed = sealFiles(files, { key: selected.key });
      sealed.key.fill(0);
      const name = `package-${String(index).padStart(3, "0")}`;
      const archive = `${name}.sealed`;
      const manifest = `${name}.manifest.json`;
      const manifestBytes = Buffer.from(`${JSON.stringify(sealed.manifest, null, 2)}\n`, "utf8");
      need(sealed.manifest.total_files === group.length);
      need(sealed.manifest.files.every((file, position) => {
        const expected = group[position];
        return file.path === expected.path && file.sha256 === expected.sha256 && file.bytes === expected.bytes && file.mode === expected.mode;
      }));
      entries.push({ archive, manifest, manifest_sha256: sha256(manifestBytes),
        cipher_sha256: sealed.manifest.cipher_sha256, total_files: group.length,
        bytes: group.reduce((total, record) => total + record.bytes, 0) });
      await privateFile(path.join(stage, archive), sealed.archive);
      await privateFile(path.join(stage, manifest), manifestBytes);
    }
    const index = { version: INDEX_VERSION, key_id: selected.keyId, packages: entries,
      status: "encrypted", product_acceptance: "read-summary" };
    need(index.packages.length === packages.length && index.packages.reduce((total, entry) => total + entry.total_files, 0) === records.length);
    for (const record of records) {
      await physicalDirectory(path.dirname(record.source));
      const current = await fs.lstat(record.source);
      need(!current.isSymbolicLink() && sameFile(current, record.info));
    }
    await privateFile(path.join(stage, "evidence-index.json"), Buffer.from(`${JSON.stringify(index, null, 2)}\n`, "utf8"));
    await publish(stage, out, root);
    return { index, package_count: packages.length, file_count: records.length };
  } catch {
    throw new EvidenceSealError();
  } finally {
    if (selected !== null) selected.key.fill(0);
    if (stage !== null) await fs.rm(stage, { recursive: true, force: true }).catch(() => {});
  }
}

function argumentsFor(argv) {
  need(argv.length === 4);
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    need(["--root", "--out"].includes(argv[index]) && !Object.hasOwn(values, argv[index]));
    need(typeof argv[index + 1] === "string" && argv[index + 1].length > 0);
    values[argv[index]] = argv[index + 1];
  }
  need(Object.hasOwn(values, "--root") && Object.hasOwn(values, "--out"));
  return { root: values["--root"], out: values["--out"] };
}

export async function main(argv = process.argv.slice(2)) {
  try {
    const result = await sealEvidence(argumentsFor(argv));
    process.stdout.write(`${JSON.stringify({ package_count: result.package_count, file_count: result.file_count })}\n`);
    return 0;
  } catch {
    process.stderr.write(`${REFUSAL}\n`);
    return 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
