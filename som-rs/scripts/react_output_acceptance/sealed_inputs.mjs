#!/usr/bin/env node
/** An encrypted carrier for historical inputs. This tool never runs its files. */
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import * as fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gunzipSync, gzipSync } from "node:zlib";

export const VERSION = "som.m6-sealed-inputs.v1";
export const MANIFEST_VERSION = "som.m6-sealed-inputs-manifest.v1";
export const MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024;
export const MAX_FILES = 20_000;
const MAX_ARCHIVE_BYTES = MAX_UNCOMPRESSED_BYTES + 1024 * 1024;
const MAGIC = Buffer.from("SOM.M6.SEALED-INPUTS.V1\0", "ascii");
const AAD = Buffer.from(VERSION, "utf8");
const IV_BYTES = 12;
const TAG_BYTES = 16;
const HEADER_BYTES = MAGIC.length + IV_BYTES + TAG_BYTES;
const REFUSAL = "sealed input operation refused";
const HEX = /^[a-f0-9]{64}$/;

export class SealedInputError extends Error {
  constructor() {
    super(REFUSAL);
    this.name = "SealedInputError";
  }
}

function need(condition) {
  if (!condition) throw new SealedInputError();
}

function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value, keys) {
  need(object(value));
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  need(actual.length === expected.length && actual.every((key, index) => key === expected[index]));
}

function integer(value, maximum) {
  return Number.isSafeInteger(value) && value >= 0 && value <= maximum;
}

export function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

/** Paths are literal POSIX names, with no decoding or normalization at restore. */
export function validateRelativePath(value) {
  need(typeof value === "string" && value.length > 0 && Buffer.byteLength(value) <= 4096);
  need(Buffer.from(value, "utf8").toString("utf8") === value);
  need(!/[\\\u0000-\u001f\u007f]/.test(value) && !/^[a-zA-Z]:/.test(value));
  need(!value.startsWith("/") && path.posix.normalize(value) === value);
  need(value.split("/").every((part) => part !== "" && part !== "." && part !== ".."));
  return value;
}

function validatePaths(files) {
  const names = files.map((file) => validateRelativePath(file.path));
  const unique = new Set(names);
  need(unique.size === names.length);
  for (const name of names) {
    const parts = name.split("/");
    for (let index = 1; index < parts.length; index += 1) {
      need(!unique.has(parts.slice(0, index).join("/")));
    }
  }
}

function mode(value) {
  need(value === 0o644 || value === 0o755);
  return value;
}

export function decodeKey(value) {
  need(typeof value === "string" && /^[A-Za-z0-9+/]{43}=$/.test(value));
  const decoded = Buffer.from(value, "base64");
  need(decoded.length === 32 && decoded.toString("base64") === value);
  return decoded;
}

function keyBytes(value) {
  if (typeof value === "string") return decodeKey(value);
  need(Buffer.isBuffer(value) && value.length === 32);
  return value;
}

function validateManifest(manifest) {
  exactKeys(manifest, ["version", "cipher_sha256", "uncompressed_bytes", "total_files", "files"]);
  need(manifest.version === MANIFEST_VERSION && typeof manifest.cipher_sha256 === "string" && HEX.test(manifest.cipher_sha256));
  need(integer(manifest.uncompressed_bytes, MAX_UNCOMPRESSED_BYTES));
  need(integer(manifest.total_files, MAX_FILES) && Array.isArray(manifest.files));
  need(manifest.files.length === manifest.total_files);
  let totalBytes = 0;
  for (const file of manifest.files) {
    exactKeys(file, ["path", "sha256", "bytes", "mode"]);
    validateRelativePath(file.path);
    need(typeof file.sha256 === "string" && HEX.test(file.sha256));
    need(integer(file.bytes, MAX_UNCOMPRESSED_BYTES));
    mode(file.mode);
    totalBytes += file.bytes;
    need(totalBytes <= MAX_UNCOMPRESSED_BYTES);
  }
  validatePaths(manifest.files);
}

/** Pure byte API. Callers receive the key; the CLI writes it only to a new file. */
export function sealFiles(files, { key = randomBytes(32), iv = randomBytes(IV_BYTES) } = {}) {
  try {
    need(Array.isArray(files) && files.length <= MAX_FILES);
    key = keyBytes(key);
    need(Buffer.isBuffer(iv) && iv.length === IV_BYTES);
    let totalBytes = 0;
    const payloadFiles = files.map((file) => {
      exactKeys(file, ["path", "mode", "data"]);
      validateRelativePath(file.path);
      mode(file.mode);
      need(Buffer.isBuffer(file.data));
      totalBytes += file.data.length;
      need(totalBytes <= MAX_UNCOMPRESSED_BYTES);
      return { path: file.path, mode: file.mode, data: file.data.toString("base64") };
    });
    validatePaths(payloadFiles);
    const plaintext = Buffer.from(JSON.stringify({ version: VERSION, files: payloadFiles }), "utf8");
    need(plaintext.length <= MAX_UNCOMPRESSED_BYTES);
    const compressed = gzipSync(plaintext);
    const cipher = createCipheriv("aes-256-gcm", key, iv, { authTagLength: TAG_BYTES });
    cipher.setAAD(AAD);
    const encrypted = Buffer.concat([cipher.update(compressed), cipher.final()]);
    const archive = Buffer.concat([MAGIC, iv, cipher.getAuthTag(), encrypted]);
    need(archive.length <= MAX_ARCHIVE_BYTES);
    const manifest = {
      version: MANIFEST_VERSION,
      cipher_sha256: sha256(archive),
      uncompressed_bytes: plaintext.length,
      total_files: files.length,
      files: files.map((file) => ({ path: file.path, sha256: sha256(file.data), bytes: file.data.length, mode: file.mode })),
    };
    return { archive, manifest, key: Buffer.from(key) };
  } catch {
    throw new SealedInputError();
  }
}

/** Pure byte API. Authentication finishes before any gzip or JSON processing. */
export function openArchive(archive, manifest, key) {
  try {
    validateManifest(manifest);
    need(Buffer.isBuffer(archive) && archive.length > HEADER_BYTES && archive.length <= MAX_ARCHIVE_BYTES);
    need(sha256(archive) === manifest.cipher_sha256 && archive.subarray(0, MAGIC.length).equals(MAGIC));
    key = keyBytes(key);
    const iv = archive.subarray(MAGIC.length, MAGIC.length + IV_BYTES);
    const tag = archive.subarray(MAGIC.length + IV_BYTES, HEADER_BYTES);
    const decipher = createDecipheriv("aes-256-gcm", key, iv, { authTagLength: TAG_BYTES });
    decipher.setAAD(AAD);
    decipher.setAuthTag(tag);
    const compressed = Buffer.concat([decipher.update(archive.subarray(HEADER_BYTES)), decipher.final()]);
    const plaintext = gunzipSync(compressed, { maxOutputLength: MAX_UNCOMPRESSED_BYTES });
    need(plaintext.length === manifest.uncompressed_bytes);
    const text = plaintext.toString("utf8");
    need(Buffer.from(text, "utf8").equals(plaintext));
    const payload = JSON.parse(text);
    exactKeys(payload, ["version", "files"]);
    need(payload.version === VERSION && Array.isArray(payload.files) && payload.files.length === manifest.total_files);
    validatePaths(payload.files);
    const expected = new Map(manifest.files.map((file) => [file.path, file]));
    const restored = payload.files.map((file) => {
      exactKeys(file, ["path", "mode", "data"]);
      const record = expected.get(file.path);
      need(record !== undefined && file.mode === record.mode && typeof file.data === "string");
      // Buffer's base64 decoder is permissive. The round trip makes this strict.
      const data = Buffer.from(file.data, "base64");
      need(data.toString("base64") === file.data && data.length === record.bytes && sha256(data) === record.sha256);
      return { path: file.path, mode: file.mode, data };
    });
    need(restored.length === expected.size);
    return restored;
  } catch {
    throw new SealedInputError();
  }
}

async function lstatOrMissing(filename) {
  try {
    return await fs.lstat(filename);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

/** Check each existing component without following a symbolic link. */
async function safeAncestors(filename, allowMissing = false) {
  const absolute = path.resolve(filename);
  const root = path.parse(absolute).root;
  let current = root;
  const rootStat = await fs.lstat(root);
  need(rootStat.isDirectory() && !rootStat.isSymbolicLink());
  for (const part of absolute.slice(root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const stat = await lstatOrMissing(current);
    if (stat === null) {
      need(allowMissing);
    } else {
      need(stat.isDirectory() && !stat.isSymbolicLink());
    }
  }
  return absolute;
}

async function readRegularFile(filename, limit) {
  need(typeof filename === "string" && path.isAbsolute(filename) && path.resolve(filename) === filename);
  await safeAncestors(path.dirname(filename));
  const handle = await fs.open(filename, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = await handle.stat();
    need(stat.isFile() && stat.size <= limit);
    const bytes = await handle.readFile();
    need(bytes.length <= limit && bytes.length === stat.size);
    return bytes;
  } finally {
    await handle.close();
  }
}

async function newFilePath(filename) {
  const absolute = path.resolve(filename);
  await safeAncestors(path.dirname(absolute));
  need((await lstatOrMissing(absolute)) === null);
  return absolute;
}

async function writeNewFile(filename, bytes, permissions) {
  await safeAncestors(path.dirname(filename));
  const handle = await fs.open(filename, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, permissions);
  try {
    await handle.writeFile(bytes);
    await handle.chmod(permissions);
  } finally {
    await handle.close();
  }
}

/** Read a local selection and create three new files. No source names are kept. */
export async function sealSelection({ selection, archive, manifest, keyFile }) {
  try {
    const outputs = await Promise.all([archive, manifest, keyFile].map(newFilePath));
    need(new Set(outputs).size === 3);
    const spec = JSON.parse((await readRegularFile(path.resolve(selection), MAX_UNCOMPRESSED_BYTES)).toString("utf8"));
    exactKeys(spec, ["files"]);
    need(Array.isArray(spec.files) && spec.files.length <= MAX_FILES);
    validatePaths(spec.files);
    let totalBytes = 0;
    const files = [];
    for (const file of spec.files) {
      exactKeys(file, ["source", "path", "mode"]);
      validateRelativePath(file.path);
      mode(file.mode);
      const data = await readRegularFile(file.source, MAX_UNCOMPRESSED_BYTES - totalBytes);
      totalBytes += data.length;
      files.push({ path: file.path, mode: file.mode, data });
    }
    const sealed = sealFiles(files);
    const created = [];
    try {
      await writeNewFile(outputs[2], Buffer.from(sealed.key.toString("base64"), "ascii"), 0o600);
      created.push(outputs[2]);
      await writeNewFile(outputs[0], sealed.archive, 0o644);
      created.push(outputs[0]);
      await writeNewFile(outputs[1], Buffer.from(`${JSON.stringify(sealed.manifest, null, 2)}\n`, "utf8"), 0o644);
      created.push(outputs[1]);
    } catch (error) {
      // Remove only files this call created, never a pre-existing destination.
      await Promise.allSettled(created.map((filename) => fs.unlink(filename)));
      throw error;
    } finally {
      sealed.key.fill(0);
    }
    return sealed.manifest;
  } catch {
    throw new SealedInputError();
  }
}

async function emptyOutput(out) {
  const absolute = path.resolve(out);
  await safeAncestors(path.dirname(absolute), true);
  const stat = await lstatOrMissing(absolute);
  if (stat !== null) {
    need(stat.isDirectory() && !stat.isSymbolicLink());
    need((await fs.readdir(absolute)).length === 0);
  }
  return absolute;
}

/** Fully validate and authenticate the package before creating any output file. */
export async function restoreInputs({ archive, manifest, out, key = process.env.M6_INPUTS_KEY }) {
  try {
    const keyBuffer = keyBytes(key);
    const archiveBytes = await readRegularFile(path.resolve(archive), MAX_ARCHIVE_BYTES);
    const manifestBytes = await readRegularFile(path.resolve(manifest), MAX_UNCOMPRESSED_BYTES);
    const files = openArchive(archiveBytes, JSON.parse(manifestBytes.toString("utf8")), keyBuffer);
    const output = await emptyOutput(out);
    // The complete file list and all ancestors pass before the first mkdir.
    for (const file of files) {
      const destination = path.join(output, ...file.path.split("/"));
      need(destination.startsWith(`${output}${path.sep}`));
      await safeAncestors(path.dirname(destination), true);
      need((await lstatOrMissing(destination)) === null);
    }
    await fs.mkdir(output, { recursive: true, mode: 0o700 });
    await safeAncestors(output);
    need((await fs.readdir(output)).length === 0);
    for (const file of files) {
      const destination = path.join(output, ...file.path.split("/"));
      await safeAncestors(path.dirname(destination), true);
      await fs.mkdir(path.dirname(destination), { recursive: true, mode: 0o700 });
      await writeNewFile(destination, file.data, file.mode);
    }
    return { total_files: files.length };
  } catch {
    throw new SealedInputError();
  }
}

function argumentsFor(argv) {
  const [command, ...rest] = argv;
  const wanted = command === "seal"
    ? ["selection", "archive", "manifest", "key-file"]
    : command === "restore" ? ["archive", "manifest", "out"] : null;
  need(wanted !== null && rest.length === wanted.length * 2);
  const values = {};
  for (let index = 0; index < rest.length; index += 2) {
    const flag = rest[index];
    need(typeof flag === "string" && flag.startsWith("--"));
    const name = flag.slice(2);
    need(wanted.includes(name) && !Object.hasOwn(values, name) && typeof rest[index + 1] === "string" && rest[index + 1].length > 0);
    values[name] = rest[index + 1];
  }
  need(wanted.every((name) => Object.hasOwn(values, name)));
  return { command, values };
}

export async function main(argv = process.argv.slice(2)) {
  try {
    const { command, values } = argumentsFor(argv);
    if (command === "seal") {
      await sealSelection({ selection: values.selection, archive: values.archive, manifest: values.manifest, keyFile: values["key-file"] });
    } else {
      await restoreInputs(values);
    }
    return 0;
  } catch {
    process.stderr.write(`${REFUSAL}\n`);
    return 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
