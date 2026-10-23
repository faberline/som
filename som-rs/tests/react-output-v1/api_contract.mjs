// Drives a generated React product's own src/api.ts against a live Python product.
// Usage: node api_contract.mjs <product dir> <service url> <prompt.json>
// The steps come from the product's src/schema.ts and the prompt version (api-contract.json in the contract).
// Prints one JSON report; exits 1 if a step fails.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";

const [productDir, serviceUrl, promptPath] = process.argv.slice(2);
const load = (name) => import(pathToFileURL(path.join(productDir, "src", name)).href);
const api = await load("api.ts");
const { FIELDS, QUERY, RESOURCE } = await load("schema.ts");
const version = JSON.parse(readFileSync(promptPath, "utf8")).version;
// v3 and v4 bodies forbid unknown members and refuse null for a field that is not nullable;
// v1 and v2 bodies ignore unknown members, and the database refuses null for a required field.
const strict = version === "som.prompt.v3" || version === "som.prompt.v4";

api.setBase(serviceUrl);
const steps = [];
let failed = false;

function check(name, ok, detail) {
  steps.push({ name, ok, ...(ok ? {} : { detail }) });
  if (!ok) failed = true;
}

// A step whose when condition (api-contract.json) is false is recorded, not dropped.
function skip(name, reason) {
  steps.push({ name, ok: null, skipped: reason });
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function sample(field, tag) {
  switch (field.type) {
    case "str":
      return `${tag} ${field.name}`;
    case "int":
      return { min: 3, extra: 11, changed: 19 }[tag] ?? 7;
    case "float":
      return { min: 0.5, extra: 2.5, changed: 4.5 }[tag] ?? 1.5;
    case "bool":
      return field.default === undefined ? tag !== "min" : !field.default;
    case "date":
      return { min: "2026-03-01", changed: "2026-04-01" }[tag] ?? "2026-01-05";
  }
}

function expectRow(name, result, body) {
  if (!result.ok) {
    check(name, false, result.error);
    return null;
  }
  const row = result.value;
  const problems = [];
  if (!Number.isInteger(row.id) || row.id < 1) problems.push(`id ${row.id}`);
  for (const field of FIELDS) {
    const want = Object.hasOwn(body, field.name) ? body[field.name] : field.default;
    if (!same(row[field.name], want)) problems.push(`${field.name}: ${JSON.stringify(row[field.name])} != ${JSON.stringify(want)}`);
  }
  if (RESOURCE.timestamps && (typeof row.created_at !== "string" || typeof row.updated_at !== "string")) {
    problems.push("timestamps missing");
  }
  check(name, problems.length === 0, problems);
  return row;
}

function expectError(name, result, status, detail, predicate = () => true) {
  const ok = !result.ok && result.error.status === status && result.error.detail === detail && predicate(result.error);
  check(name, ok, result.ok ? { value: result.value } : result.error);
}

const page = QUERY === null ? {} : { limit: QUERY.pageSize, offset: 0 };

// List, create, get.
let listed = await api.listRows(page);
check("list empty", listed.ok && same(listed.value, []), listed);

const minBody = {};
for (const field of FIELDS.filter((item) => item.required)) minBody[field.name] = sample(field, "min");
const minRow = expectRow("create required fields", await api.createRow(minBody), minBody);

const fullBody = {};
for (const field of FIELDS) fullBody[field.name] = sample(field, "full");
const fullRow = expectRow("create every field", await api.createRow(fullBody), fullBody);
const rows = [minRow, fullRow].filter((row) => row !== null);

const got = await api.getRow(fullRow?.id ?? 0);
check("get row", got.ok && same(got.value, fullRow), got);

listed = await api.listRows(page);
check("list in id order", listed.ok && same(listed.value, rows), listed);

// Filters and pages.
if (QUERY !== null && QUERY.filter !== null) {
  const value = fullBody[QUERY.filter.field];
  listed = await api.listRows({ ...page, [QUERY.filter.param]: value });
  const want = rows.filter((row) => same(row[QUERY.filter.field], value));
  check("list filter", listed.ok && same(listed.value, want), listed);
} else {
  skip("list filter", "no filter");
}
if (QUERY !== null && QUERY.before !== null) {
  const before = "2026-02-01";
  listed = await api.listRows({ ...page, before });
  const want = rows.filter((row) => row[QUERY.before] !== null && row[QUERY.before] < before);
  check("list before", listed.ok && same(listed.value, want), listed);
} else {
  skip("list before", "no before filter");
}
if (QUERY !== null) {
  listed = await api.listRows({ limit: 1, offset: 1 });
  check("list page", listed.ok && same(listed.value, rows.slice(1, 2)), listed);
  listed = await api.listRows({ limit: QUERY.maxLimit + 1, offset: 0 });
  expectError("list limit too large", listed, 422, "validation failed");
} else {
  skip("list page", "no list query");
  skip("list limit too large", "no list query");
}

// Patch.
// A row a step needs but an earlier step failed to create fails that step; it is never a skip.
const changeable = FIELDS.find((field) => field.type === "str") ?? FIELDS[0];
if (minRow === null) {
  check("patch changed field", false, "no min row");
} else {
  const patch = { [changeable.name]: sample(changeable, "changed") };
  const patched = await api.patchRow(minRow.id, patch);
  const ok =
    patched.ok &&
    FIELDS.every((field) => same(patched.value[field.name], field === changeable ? patch[field.name] : minRow[field.name])) &&
    (!RESOURCE.timestamps || patched.value.updated_at >= minRow.updated_at);
  check("patch changed field", ok, patched);
}
const notNullable = FIELDS.find((field) => !field.nullable);
if (notNullable === undefined) {
  skip("patch null on a field that is not nullable", "no field that is not nullable");
} else if (minRow === null) {
  check("patch null on a field that is not nullable", false, "no min row");
} else {
  const patched = await api.patchRow(minRow.id, { [notNullable.name]: null });
  if (strict) {
    expectError(
      "patch null on a field that is not nullable",
      patched,
      422,
      "validation failed",
      (error) => Object.keys(error.fieldErrors).length + error.otherErrors.length > 0,
    );
  } else {
    expectError("patch null on a field that is not nullable", patched, 409, "conflict");
  }
}

// Validation messages.
const required = FIELDS.find((field) => field.required);
if (required !== undefined) {
  expectError("create missing a required field", await api.createRow({}), 422, "validation failed", (error) =>
    (error.fieldErrors[required.name] ?? []).length > 0,
  );
} else {
  skip("create missing a required field", "no required field");
}
const extraBody = {};
for (const field of FIELDS.filter((item) => item.required)) extraBody[field.name] = sample(field, "extra");
const extra = await api.createRow({ ...extraBody, unknown_field: 1 });
if (strict) {
  expectError("create with an unknown field", extra, 422, "validation failed", (error) =>
    error.otherErrors.some((message) => message.startsWith("body.unknown_field: ")),
  );
} else {
  const row = expectRow("create with an unknown field", extra, extraBody);
  if (row !== null && Object.hasOwn(row, "unknown_field")) {
    steps[steps.length - 1] = { name: "create with an unknown field", ok: false, detail: "unknown_field returned" };
    failed = true;
  }
}

// Unique conflict.
const unique = FIELDS.find((field) => field.unique);
if (unique !== undefined) {
  expectError("create unique conflict", await api.createRow(fullBody), 409, "conflict");
} else {
  skip("create unique conflict", "no unique field");
}

// Missing rows and delete.
expectError("get missing row", await api.getRow(2147483647), 404, "not found");
expectError("patch missing row", await api.patchRow(2147483647, {}), 404, "not found");
if (fullRow !== null) {
  const deleted = await api.deleteRow(fullRow.id);
  check("delete row", deleted.ok && deleted.value === null, deleted);
  expectError("get deleted row", await api.getRow(fullRow.id), 404, "not found");
  expectError("delete deleted row", await api.deleteRow(fullRow.id), 404, "not found");
} else {
  for (const name of ["delete row", "get deleted row", "delete deleted row"]) check(name, false, "no full row");
}

// A service that does not answer.
api.setBase("http://127.0.0.1:9");
expectError("network error", await api.listRows(page), 0, "network error");

// The facts the when conditions of api-contract.json read, so the evidence can be checked against them.
const facts = {
  query: QUERY !== null,
  filter: QUERY !== null && QUERY.filter !== null,
  before: QUERY !== null && QUERY.before !== null,
  not_nullable: notNullable !== undefined,
  required: required !== undefined,
  unique: unique !== undefined,
};
process.stdout.write(JSON.stringify({ resource: RESOURCE.name, failed, facts, steps }, null, 2) + "\n");
process.exit(failed ? 1 : 0);
