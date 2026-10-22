// Form text to and from JSON values. This file is the same in every generated product.
import type { FieldSpec, Value } from "./spec.ts";

// Every form control holds text: "" for null, "true" or "false" for a bool.
export type FormText = Record<string, string>;

// A member of a record built from prompt names. A name such as toString must not
// read the Object.prototype member.
export function own<T>(record: Record<string, T>, key: string): T | undefined {
  return Object.hasOwn(record, key) ? record[key] : undefined;
}

export function textOf(value: Value | undefined): string {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

// The JSON value sent for the text of a field. Text that is not a number is sent as typed,
// so the service answers 422 for it instead of the form guessing.
export function valueOf(field: FieldSpec, text: string): Value {
  if (text === "" && field.nullable) {
    return null;
  }
  switch (field.type) {
    case "bool":
      return text === "true" ? true : text === "false" ? false : text;
    case "int":
    case "float": {
      const number = Number(text);
      return text.trim() !== "" && Number.isFinite(number) ? number : text;
    }
    case "str":
    case "date":
      return text;
  }
}

export function display(value: Value | undefined): string {
  return value === null || value === undefined ? "—" : String(value);
}

// A required bool starts unchecked, so it starts as false rather than as no value.
export function initialCreateText(fields: readonly FieldSpec[]): FormText {
  const text: FormText = {};
  for (const field of fields) {
    if (field.required) {
      text[field.name] = field.type === "bool" ? "false" : "";
    } else {
      text[field.name] = textOf(field.default);
    }
  }
  return text;
}

export function rowText(
  fields: readonly FieldSpec[],
  row: Record<string, unknown>,
): FormText {
  const text: FormText = {};
  for (const field of fields) {
    text[field.name] = textOf(own(row, field.name) as Value | undefined);
  }
  return text;
}

// The body members that differ from the starting text, plus every required field.
export function bodyOf(
  fields: readonly FieldSpec[],
  text: FormText,
  start: FormText,
  sendRequired: boolean,
): Record<string, Value> {
  const body: Record<string, Value> = {};
  for (const field of fields) {
    const current = own(text, field.name) ?? "";
    if (
      (sendRequired && field.required) ||
      current !== (own(start, field.name) ?? "")
    ) {
      body[field.name] = valueOf(field, current);
    }
  }
  return body;
}
