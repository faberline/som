export interface Problem {
  type: string;
  loc: (string | number)[];
  msg: string;
}

export interface Create {
  title: string;
}

export interface Update {
  title?: string | null;
}

export interface Read {
  id: number;
  title: string;
}

export type Checked<T> =
  { ok: true; value: T } | { ok: false; problems: Problem[] };

function codePoints(text: string): number {
  return Array.from(text).length;
}

function checkTitle(value: unknown, problems: Problem[]): string | undefined {
  if (typeof value !== "string") {
    problems.push({
      type: "string_type",
      loc: ["body", "title"],
      msg: "Input should be a valid string",
    });
    return undefined;
  }
  if (codePoints(value) > 255) {
    problems.push({
      type: "string_too_long",
      loc: ["body", "title"],
      msg: "String should have at most 255 characters",
    });
    return undefined;
  }
  return value;
}

export function checkCreate(body: Record<string, unknown>): Checked<Create> {
  const problems: Problem[] = [];
  if (!Object.hasOwn(body, "title")) {
    problems.push({
      type: "missing",
      loc: ["body", "title"],
      msg: "Field required",
    });
    return { ok: false, problems: problems };
  }
  const title = checkTitle(body["title"], problems);
  if (title === undefined) {
    return { ok: false, problems: problems };
  }
  return { ok: true, value: { title: title } };
}

export function checkUpdate(body: Record<string, unknown>): Checked<Update> {
  const problems: Problem[] = [];
  const value: Update = {};
  if (Object.hasOwn(body, "title")) {
    const raw = body["title"];
    if (raw === null) {
      value["title"] = null;
    } else {
      const title = checkTitle(raw, problems);
      if (title !== undefined) {
        value["title"] = title;
      }
    }
  }
  if (problems.length > 0) {
    return { ok: false, problems: problems };
  }
  return { ok: true, value: value };
}
