// Tests of the form conversions. This file is the same in every generated product.
import { describe, expect, it } from "vitest";
import {
  bodyOf,
  display,
  initialCreateText,
  own,
  rowText,
  textOf,
  valueOf,
} from "./form.ts";
import type { FieldSpec } from "./spec.ts";

function field(
  type: FieldSpec["type"],
  nullable: boolean,
  required = false,
): FieldSpec {
  return {
    name: type,
    type,
    required,
    nullable,
    unique: false,
    default: nullable ? null : undefined,
  };
}

describe("valueOf", () => {
  it("sends empty text of a nullable field as null", () => {
    for (const type of ["str", "int", "float", "bool", "date"] as const) {
      expect(valueOf(field(type, true), "")).toBeNull();
    }
  });

  it("keeps empty text of a field that is not nullable", () => {
    expect(valueOf(field("str", false, true), "")).toBe("");
    expect(valueOf(field("int", false, true), "")).toBe("");
  });

  it("converts numbers and keeps text that is not a number", () => {
    expect(valueOf(field("int", false), "42")).toBe(42);
    expect(valueOf(field("float", false), "-1.5")).toBe(-1.5);
    expect(valueOf(field("int", false), "4x")).toBe("4x");
    expect(valueOf(field("float", false), "Infinity")).toBe("Infinity");
  });

  it("converts bool text", () => {
    expect(valueOf(field("bool", false), "true")).toBe(true);
    expect(valueOf(field("bool", false), "false")).toBe(false);
  });

  it("keeps str and date text", () => {
    expect(valueOf(field("str", false), " a ")).toBe(" a ");
    expect(valueOf(field("date", false), "2026-01-31")).toBe("2026-01-31");
  });
});

describe("text", () => {
  it("shows null as an empty control and a dash", () => {
    expect(textOf(null)).toBe("");
    expect(textOf(false)).toBe("false");
    expect(display(null)).toBe("—");
    expect(display(0)).toBe("0");
  });

  it("does not read Object.prototype members", () => {
    expect(own({}, "toString")).toBeUndefined();
    expect(own({ toString: "x" }, "toString")).toBe("x");
  });
});

describe("bodyOf", () => {
  const fields: FieldSpec[] = [
    {
      name: "title",
      type: "str",
      required: true,
      nullable: false,
      unique: false,
    },
    {
      name: "note",
      type: "str",
      required: false,
      nullable: true,
      unique: false,
      default: null,
    },
    {
      name: "done",
      type: "bool",
      required: false,
      nullable: false,
      unique: false,
      default: false,
    },
  ];

  it("sends required fields and changed optional fields on create", () => {
    const start = initialCreateText(fields);
    expect(start).toEqual({ title: "", note: "", done: "false" });
    expect(bodyOf(fields, { ...start, title: "a" }, start, true)).toEqual({
      title: "a",
    });
    expect(
      bodyOf(fields, { ...start, title: "a", done: "true" }, start, true),
    ).toEqual({ title: "a", done: true });
  });

  it("starts a required bool as false", () => {
    const flag = field("bool", false, true);
    const start = initialCreateText([flag]);
    expect(start).toEqual({ bool: "false" });
    expect(bodyOf([flag], start, start, true)).toEqual({ bool: false });
  });

  it("sends only changed fields on edit", () => {
    const start = rowText(fields, { id: 1, title: "a", note: "n", done: true });
    expect(start).toEqual({ title: "a", note: "n", done: "true" });
    expect(bodyOf(fields, start, start, false)).toEqual({});
    expect(bodyOf(fields, { ...start, note: "" }, start, false)).toEqual({
      note: null,
    });
  });
});
