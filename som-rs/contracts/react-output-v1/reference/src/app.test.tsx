// Tests of the pages against a fake service. This file is the same in every generated product.
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App.tsx";
import { FIELDS, QUERY, RESOURCE } from "./schema.ts";
import type { FieldSpec, Value } from "./spec.ts";

type Call = { url: string; method: string; body: unknown };

let calls: Call[] = [];
let replies: { status: number; body: unknown }[] = [];

function sample(field: FieldSpec): Value {
  switch (field.type) {
    case "str":
      return `sample ${field.name}`;
    case "int":
      return 7;
    case "float":
      return 1.5;
    case "bool":
      return true;
    case "date":
      return "2026-01-02";
  }
}

function sampleRow(id: number): Record<string, Value> {
  const row: Record<string, Value> = { id };
  for (const field of FIELDS) {
    row[field.name] = sample(field);
  }
  if (RESOURCE.timestamps) {
    row.created_at = "2026-01-02T03:04:05.000006Z";
    row.updated_at = "2026-01-02T03:04:05.000006Z";
  }
  return row;
}

function reply(status: number, body: unknown = null) {
  replies.push({ status, body });
}

function show(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function setControl(field: FieldSpec, value: Value) {
  const control = screen.getByLabelText(field.name, {
    selector: "input, select",
  });
  if (control instanceof HTMLInputElement && control.type === "checkbox") {
    if (control.checked !== value) {
      fireEvent.click(control);
    }
    return;
  }
  fireEvent.change(control, { target: { value: String(value) } });
}

beforeEach(() => {
  calls = [];
  replies = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    calls.push({
      url,
      method: init?.method ?? "GET",
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    });
    const next = replies.shift();
    if (next === undefined) {
      throw new Error(`no reply for ${url}`);
    }
    const text = next.body === null ? "" : JSON.stringify(next.body);
    return { status: next.status, text: async () => text } as Response;
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("list", () => {
  it("lists rows with the first page query", async () => {
    reply(200, [sampleRow(1), sampleRow(2)]);
    show("/");
    expect(await screen.findByRole("link", { name: "2" })).toBeTruthy();
    const query = QUERY === null ? "" : `?limit=${QUERY.pageSize}&offset=0`;
    expect(calls).toEqual([
      { url: `/api${RESOURCE.path}${query}`, method: "GET", body: undefined },
    ]);
    const rows = screen.getByRole("table").querySelectorAll("tbody tr");
    expect(rows.length).toBe(2);
    const first = FIELDS[0];
    if (first !== undefined) {
      for (const row of rows) {
        expect(row.querySelectorAll("td")[1]?.textContent).toBe(
          String(sample(first)),
        );
      }
    }
  });

  it("shows the service error", async () => {
    reply(500, { detail: "Internal Server Error" });
    show("/");
    expect((await screen.findByRole("alert")).textContent).toContain(
      "HTTP 500",
    );
  });
});

describe("create", () => {
  it("sends the required fields and opens the new row", async () => {
    reply(201, sampleRow(5));
    reply(200, sampleRow(5));
    show("/new");
    const expected: Record<string, Value> = {};
    for (const field of FIELDS.filter((item) => item.required)) {
      setControl(field, sample(field));
      expected[field.name] = sample(field);
    }
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(
      await screen.findByRole("heading", { name: `${RESOURCE.name} 5` }),
    ).toBeTruthy();
    expect(calls[0]).toEqual({
      url: `/api${RESOURCE.path}`,
      method: "POST",
      body: expected,
    });
    await waitFor(() => expect(calls[1]?.url).toBe(`/api${RESOURCE.path}/5`));
  });

  it("shows a 422 message next to its field", async () => {
    const field = FIELDS[0];
    if (field === undefined) {
      return;
    }
    reply(422, {
      detail: [
        { type: "value_error", loc: ["body", field.name], msg: "bad value" },
        { type: "missing", loc: ["body"], msg: "other problem" },
      ],
    });
    show("/new");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("bad value")).toBeTruthy();
    expect(screen.getByText("body: other problem")).toBeTruthy();
  });
});

describe("detail", () => {
  it("shows the not found detail", async () => {
    reply(404, { detail: "not found" });
    show("/9");
    expect((await screen.findByRole("alert")).textContent).toContain(
      "not found",
    );
  });

  it("refuses an id that is not a row id", () => {
    show("/0");
    expect(screen.getByRole("alert").textContent).toBe("Page not found.");
    expect(calls).toEqual([]);
  });

  it("deletes after confirmation and returns to the list", async () => {
    reply(200, sampleRow(3));
    reply(204);
    reply(200, []);
    vi.stubGlobal("confirm", () => true);
    show("/3");
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByText("No rows.")).toBeTruthy();
    expect(calls[1]).toEqual({
      url: `/api${RESOURCE.path}/3`,
      method: "DELETE",
      body: undefined,
    });
  });

  it("edits only the changed fields", async () => {
    const field = FIELDS.find((item) => item.type === "str") ?? FIELDS[0];
    if (field === undefined) {
      return;
    }
    const changed: Value =
      field.type === "str"
        ? "changed"
        : field.type === "bool"
          ? false
          : field.type === "date"
            ? "2026-03-04"
            : 9;
    reply(200, sampleRow(4));
    reply(200, { ...sampleRow(4), [field.name]: changed });
    reply(200, { ...sampleRow(4), [field.name]: changed });
    show("/4/edit");
    await screen.findByRole("button", { name: "Save" });
    setControl(field, changed);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(
      await screen.findByRole("heading", { name: `${RESOURCE.name} 4` }),
    ).toBeTruthy();
    expect(calls[1]).toEqual({
      url: `/api${RESOURCE.path}/4`,
      method: "PATCH",
      body: { [field.name]: changed },
    });
  });
});
