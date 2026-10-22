// The client for the Python service. This file is the same in every generated product.
import type { CreateBody, ListQuery, PatchBody, Row } from "./schema.ts";
import { own } from "./form.ts";
import { FIELDS, RESOURCE } from "./schema.ts";

export type ApiError = {
  // The HTTP status, or 0 when no response arrived.
  status: number;
  detail: string;
  // 422 messages for a body member, by field name.
  fieldErrors: Record<string, string[]>;
  // Every other 422 message, prefixed with its location.
  otherErrors: string[];
};

export type Result<T> = { ok: true; value: T } | { ok: false; error: ApiError };

// The development server and `vite preview` proxy /api to the service (vite.config.ts).
let base = "/api";

export function setBase(value: string): void {
  base = value;
}

const fieldNames = new Set(FIELDS.map((field) => field.name));

function errorFrom(status: number, body: unknown): ApiError {
  const error: ApiError = {
    status,
    detail: `HTTP ${status}`,
    fieldErrors: {},
    otherErrors: [],
  };
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return error;
  }
  const detail = body.detail;
  if (typeof detail === "string") {
    error.detail = detail;
    return error;
  }
  if (!Array.isArray(detail)) {
    return error;
  }
  error.detail = "validation failed";
  for (const item of detail as unknown[]) {
    if (typeof item !== "object" || item === null) {
      continue;
    }
    const loc = "loc" in item && Array.isArray(item.loc) ? item.loc : [];
    const msg = "msg" in item ? String(item.msg) : "invalid";
    const name = loc[1];
    if (
      loc.length === 2 &&
      loc[0] === "body" &&
      typeof name === "string" &&
      fieldNames.has(name)
    ) {
      const messages = own(error.fieldErrors, name) ?? [];
      messages.push(msg);
      error.fieldErrors[name] = messages;
    } else {
      error.otherErrors.push(loc.length > 0 ? `${loc.join(".")}: ${msg}` : msg);
    }
  }
  return error;
}

async function call<T>(
  method: string,
  path: string,
  expected: number,
  body?: unknown,
): Promise<Result<T>> {
  let response: Response;
  try {
    response = await fetch(base + path, {
      method,
      headers:
        body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    return {
      ok: false,
      error: {
        status: 0,
        detail: "network error",
        fieldErrors: {},
        otherErrors: [],
      },
    };
  }
  const text = await response.text();
  let parsed: unknown = null;
  if (text !== "") {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  if (response.status !== expected) {
    return { ok: false, error: errorFrom(response.status, parsed) };
  }
  return { ok: true, value: parsed as T };
}

function itemPath(id: number): string {
  return `${RESOURCE.path}/${encodeURIComponent(String(id))}`;
}

export function listRows(query: ListQuery): Promise<Result<Row[]>> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) {
      params.append(key, String(value));
    }
  }
  const search = params.toString();
  return call<Row[]>(
    "GET",
    search === "" ? RESOURCE.path : `${RESOURCE.path}?${search}`,
    200,
  );
}

export function getRow(id: number): Promise<Result<Row>> {
  return call<Row>("GET", itemPath(id), 200);
}

export function createRow(body: CreateBody): Promise<Result<Row>> {
  return call<Row>("POST", RESOURCE.path, 201, body);
}

export function patchRow(id: number, body: PatchBody): Promise<Result<Row>> {
  return call<Row>("PATCH", itemPath(id), 200, body);
}

export function deleteRow(id: number): Promise<Result<null>> {
  return call<null>("DELETE", itemPath(id), 204);
}
