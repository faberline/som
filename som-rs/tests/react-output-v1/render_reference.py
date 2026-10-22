#!/usr/bin/env python3
"""The reference renderer of a React product: a som.prompt.v1 to v4 prompt to src/schema.ts and README.md.

The other files of a product are the fixed files in contracts/react-output-v1/reference. The Rust renderer
must give the same bytes as this one. The output is already in Prettier's default format.

Usage: render_reference.py <prompt.json> <out dir (new)> [--reference <fixed files dir>]
Without --out content, prints the two generated files as JSON.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
REFERENCE = HERE.parent.parent / "contracts" / "react-output-v1" / "reference"
VERSIONS = ("som.prompt.v1", "som.prompt.v2", "som.prompt.v3", "som.prompt.v4")
TS_TYPES = {"str": "string", "int": "number", "float": "number", "bool": "boolean", "date": "string"}
PAGE_SIZE = 20
LINE_WIDTH = 80


def literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def spec(prompt: dict[str, Any]) -> dict[str, Any]:
    """The prompt's resource, fields and list query, in the terms of src/spec.ts."""
    version = prompt["version"]
    if version not in VERSIONS:
        raise ValueError(f"unsupported prompt version {version}")
    resource = prompt["resource"]
    service = version in ("som.prompt.v3", "som.prompt.v4")
    fields = []
    for field in resource["fields"]:
        row: dict[str, Any] = {"name": field["name"], "type": field["type"], "required": field["required"]}
        if field["required"]:
            row["nullable"] = False
        else:
            # v1 and v2 optional fields have no default member and default to null.
            row["default"] = field.get("default")
            row["nullable"] = row["default"] is None
        row["unique"] = field["unique"]
        fields.append(row)
    query = None
    if version != "som.prompt.v1":
        q = prompt["query"]
        by_name = {f["name"]: f for f in fields}
        name = q["filter_field"]
        query = {"filter": {"field": name, "param": name if service else "filter_value",
                            "type": by_name[name]["type"]},
                 "before": q.get("date_field"), "pageSize": PAGE_SIZE, "maxLimit": q["max_limit"]}
    return {"version": version, "package": prompt["package"],
            "resource": {"name": resource["name"], "path": resource["path"], "timestamps": service},
            "fields": fields, "query": query}


def ts_type(field: dict[str, Any], nullable: bool) -> str:
    return TS_TYPES[field["type"]] + (" | null" if nullable else "")


def object_lines(head: str, members: list[str], tail: str) -> list[str]:
    return [head] + [f"  {member};" for member in members] + [tail]


def inline_or_block(indent: str, key: str, members: list[tuple[str, str]], end: str) -> list[str]:
    """An object literal on one line when Prettier keeps it there, else one member per line."""
    one = f"{indent}{key}: {{ " + ", ".join(f"{k}: {v}" for k, v in members) + f" }}{end}"
    if len(one) <= LINE_WIDTH:
        return [one]
    return ([f"{indent}{key}: {{"] + [f"{indent}  {k}: {v}," for k, v in members] + [f"{indent}}}{end}"])


def schema_ts(s: dict[str, Any]) -> str:
    fields = s["fields"]
    query = s["query"]
    lines = [f"// Generated from the {s['version']} prompt for package {s['package']}. Edit the prompt, not this file.",
             'import type { FieldSpec, QuerySpec, ResourceSpec } from "./spec.ts";', ""]
    row = ["id: number"] + [f"{f['name']}: {ts_type(f, f['nullable'])}" for f in fields]
    if s["resource"]["timestamps"]:
        row += ["created_at: string", "updated_at: string"]
    lines += object_lines("export type Row = {", row, "};") + [""]
    create = [f"{f['name']}{'' if f['required'] else '?'}: {ts_type(f, f['nullable'])}" for f in fields]
    lines += object_lines("export type CreateBody = {", create, "};") + [""]
    patch = [f"{f['name']}?: {ts_type(f, f['nullable'])}" for f in fields]
    lines += object_lines("export type PatchBody = {", patch, "};") + [""]
    if query is None:
        # A v1 service takes no list query.
        listed = ["limit?: never", "offset?: never"]
    else:
        listed = [f"{query['filter']['param']}?: {TS_TYPES[query['filter']['type']]}"]
        if query["before"] is not None:
            listed.append("before?: string")
        listed += ["limit?: number", "offset?: number"]
    lines += object_lines("export type ListQuery = {", listed, "};") + [""]
    r = s["resource"]
    lines += ["export const RESOURCE: ResourceSpec = {", f"  name: {literal(r['name'])},",
              f"  path: {literal(r['path'])},", f"  timestamps: {literal(r['timestamps'])},", "};", ""]
    lines.append("export const FIELDS: readonly FieldSpec[] = [")
    for f in fields:
        lines += ["  {", f"    name: {literal(f['name'])},", f"    type: {literal(f['type'])},",
                  f"    required: {literal(f['required'])},", f"    nullable: {literal(f['nullable'])},",
                  f"    unique: {literal(f['unique'])},"]
        if not f["required"]:
            lines.append(f"    default: {literal(f['default'])},")
        lines.append("  },")
    lines += ["];", ""]
    if query is None:
        lines.append("export const QUERY: QuerySpec | null = null;")
    else:
        flt = query["filter"]
        lines.append("export const QUERY: QuerySpec | null = {")
        lines += inline_or_block("  ", "filter", [("field", literal(flt["field"])), ("param", literal(flt["param"])),
                                                  ("type", literal(flt["type"]))], ",")
        lines += [f"  before: {literal(query['before'])},", f"  pageSize: {query['pageSize']},",
                  f"  maxLimit: {query['maxLimit']},", "};"]
    return "\n".join(lines) + "\n"


def table(rows: list[list[str]]) -> list[str]:
    widths = [max(3, *(len(row[i]) for row in rows)) for i in range(len(rows[0]))]

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(width) for cell, width in zip(cells, widths)) + " |"

    return [line(rows[0]), line(["-" * width for width in widths])] + [line(row) for row in rows[1:]]


def readme(s: dict[str, Any]) -> str:
    r = s["resource"]
    query = s["query"]
    if query is None:
        listed = "the list of every row in id order."
    else:
        listed = f"the list, {query['pageSize']} rows per page, filtered by `{query['filter']['field']}`"
        if query["before"] is not None:
            listed += f" and `{query['before']}` before a date"
        listed += "."
    rows = [["Field", "Type", "Required", "Nullable", "Default"]]
    for f in s["fields"]:
        rows.append([f"`{f['name']}`", f["type"], "yes" if f["required"] else "no", "yes" if f["nullable"] else "no",
                     "" if f["required"] else literal(f["default"])])
    lines = [f"# `{r['name']}` front end", "",
             f"A React front end for the `{r['path']}` service that som generated from the same prompt.", "",
             "## Run", "", "```bash", "npm ci", "SOM_API_URL=http://127.0.0.1:8000 npm run dev", "```", "",
             "The dev server and `npm run preview` send `/api/*` to `SOM_API_URL` without the `/api` prefix.",
             "`SOM_API_URL` defaults to `http://127.0.0.1:8000`.", "",
             "## Check", "", "```bash", "npm run typecheck", "npm run format:check", "npm test", "npm run build",
             "```", "",
             "## Pages", "", f"- `/`: {listed}", "- `/new`: the create form.", "- `/:id`: one row, with edit and delete.",
             "- `/:id/edit`: the edit form. It sends only the fields you changed.", "",
             "## Fields", ""] + table(rows) + [
             "", "Only `src/schema.ts` and this file depend on the prompt. Every other file is the same in every "
             "generated product."]
    return "\n".join(lines) + "\n"


def render(prompt: dict[str, Any]) -> dict[str, str]:
    s = spec(prompt)
    return {"README.md": readme(s), "src/schema.ts": schema_ts(s)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", type=pathlib.Path)
    parser.add_argument("out", type=pathlib.Path, nargs="?")
    parser.add_argument("--reference", type=pathlib.Path, default=REFERENCE)
    args = parser.parse_args()
    generated = render(json.loads(args.prompt.read_text()))
    if args.out is None:
        print(json.dumps(generated, indent=2))
        return 0
    shutil.copytree(args.reference, args.out)
    for name, text in generated.items():
        path = args.out / name
        if path.exists():
            raise SystemExit(f"generated file {name} is also a fixed file")
        path.write_text(text)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
