"""Read-only React file rules. This module never loads an acceptance tool."""
from __future__ import annotations

import json


def render_files(prompt: dict) -> dict[str, str]:
    version = prompt["version"]
    if version not in {f"som.prompt.v{number}" for number in range(1, 5)}:
        raise ValueError("React rendering is defined only for prompt versions 1 to 4")
    service = version in ("som.prompt.v3", "som.prompt.v4")
    resource = prompt["resource"]
    fields = []
    for source in resource["fields"]:
        field = dict(source)
        field["nullable"] = not source["required"] and source.get("default") is None
        fields.append(field)
    query = prompt.get("query") if version != "som.prompt.v1" else None
    types = {"str": "string", "int": "number", "float": "number", "bool": "boolean", "date": "string"}
    literal = lambda value: json.dumps(value, ensure_ascii=False)

    def declaration(name, members):
        return [f"export type {name} = {{", *(f"  {member};" for member in members), "};", ""]

    typed = lambda field: types[field["type"]] + (" | null" if field["nullable"] else "")
    lines = [f"// Generated from the {version} prompt for package {prompt['package']}. Edit the prompt, not this file.",
             'import type { FieldSpec, QuerySpec, ResourceSpec } from "./spec.ts";', ""]
    row = ["id: number", *(f"{field['name']}: {typed(field)}" for field in fields)]
    if service:
        row.extend(["created_at: string", "updated_at: string"])
    lines.extend(declaration("Row", row))
    lines.extend(declaration("CreateBody", [f"{field['name']}{'' if field['required'] else '?'}: {typed(field)}" for field in fields]))
    lines.extend(declaration("PatchBody", [f"{field['name']}?: {typed(field)}" for field in fields]))
    if query is None:
        listed = ["limit?: never", "offset?: never"]
    else:
        selected = next(field for field in fields if field["name"] == query["filter_field"])
        parameter = selected["name"] if service else "filter_value"
        listed = [f"{parameter}?: {types[selected['type']]}"]
        if query.get("date_field") is not None:
            listed.append("before?: string")
        listed.extend(["limit?: number", "offset?: number"])
    lines.extend(declaration("ListQuery", listed))
    lines.extend(["export const RESOURCE: ResourceSpec = {", f"  name: {literal(resource['name'])},",
                  f"  path: {literal(resource['path'])},", f"  timestamps: {literal(service)},", "};", "",
                  "export const FIELDS: readonly FieldSpec[] = ["])
    for field in fields:
        members = [("name", field["name"]), ("type", field["type"]), ("required", field["required"]),
                   ("nullable", field["nullable"]), ("unique", field["unique"])]
        if not field["required"]:
            members.append(("default", field.get("default")))
        lines.extend(["  {", *(f"    {key}: {literal(value)}," for key, value in members), "  },"])
    lines.extend(["];", ""])
    if query is None:
        lines.append("export const QUERY: QuerySpec | null = null;")
    else:
        members = [("field", literal(selected["name"])), ("param", literal(parameter)), ("type", literal(selected["type"]))]
        inline = "  filter: { " + ", ".join(f"{key}: {value}" for key, value in members) + " },"
        lines.append("export const QUERY: QuerySpec | null = {")
        if len(inline) <= 80:
            lines.append(inline)
        else:
            lines.extend(["  filter: {", *(f"    {key}: {value}," for key, value in members), "  },"])
        lines.extend([f"  before: {literal(query.get('date_field'))},", "  pageSize: 20,",
                      f"  maxLimit: {query['max_limit']},", "};"])
    schema = "\n".join(lines) + "\n"

    listed = "the list of every row in id order."
    if query is not None:
        listed = f"the list, 20 rows per page, filtered by `{query['filter_field']}`"
        if query.get("date_field") is not None:
            listed += f" and `{query['date_field']}` before a date"
        listed += "."
    cells = [["Field", "Type", "Required", "Nullable", "Default"]]
    for field in fields:
        cells.append([f"`{field['name']}`", field["type"], "yes" if field["required"] else "no",
                      "yes" if field["nullable"] else "no", "" if field["required"] else literal(field.get("default"))])
    widths = [max(3, *(len(row[column]) for row in cells)) for column in range(5)]
    table = [cells[0], ["-" * width for width in widths], *cells[1:]]
    table_lines = ["| " + " | ".join(value.ljust(width) for value, width in zip(row, widths)) + " |" for row in table]
    readme = [f"# `{resource['name']}` front end", "",
              f"A React front end for the `{resource['path']}` service that som generated from the same prompt.", "",
              "## Run", "", "```bash", "npm ci", "SOM_API_URL=http://127.0.0.1:8000 npm run dev", "```", "",
              "The dev server and `npm run preview` send `/api/*` to `SOM_API_URL` without the `/api` prefix.",
              "`SOM_API_URL` defaults to `http://127.0.0.1:8000`.", "", "## Check", "", "```bash",
              "npm run typecheck", "npm run format:check", "npm test", "npm run build", "```", "",
              "## Pages", "", f"- `/`: {listed}", "- `/new`: the create form.", "- `/:id`: one row, with edit and delete.",
              "- `/:id/edit`: the edit form. It sends only the fields you changed.", "", "## Fields", "", *table_lines, "",
              "Only `src/schema.ts` and this file depend on the prompt. Every other file is the same in every generated product."]
    return {"src/schema.ts": schema, "README.md": "\n".join(readme) + "\n"}
