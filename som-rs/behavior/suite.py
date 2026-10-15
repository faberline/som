"""The black-box HTTP behavior suite for phase 1 products (issue #46).

The suite never imports product code. It derives a request script from the
typed prompt alone, sends it to a running product over HTTP, and keeps the raw
exchange. Two things are computed from each exchange:

- the normalized transcript (contracts/rust-output-v1/behavior-normalization.json),
  which the parity mode compares between the Python and the Rust product;
- the oracle verdict: an in-memory model of the rows predicts the status and the
  normalized body of every request, so the suite checks one product alone.

Every step carries a check category (CATEGORIES). A failure names the step and its
category, so the seeded faults in faults.py can show which check caught them.
"""
from __future__ import annotations

import dataclasses
import datetime
import http.client
import json
import re
import urllib.parse
from typing import Any, Callable

CATEGORIES = (
    "crud", "not_found", "unique", "validation", "defaults", "null", "filter", "date_filter",
    "limit", "pagination", "order", "timestamps", "fallback",
    "scope", "isolation", "restrict", "batch", "batch_atomic", "bounds", "healthz",
)
MISSING_ID = 2147483000  # inside signed 32 bits, never created: D3 sends no id outside i32
ROWS = 23  # more than the default limit 20, so the default and offsets are visible
TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")
STR_CYCLE = ["alpha", "alpha beta", "ALPHA", "béta ✓", "", " alpha"]
INT_CYCLE = [0, -2147483648, 42, 2147483647, -7]
FLOAT_CYCLE = [0.5, -2.25, 3.0, 1e10, 7]
WRONG_TYPE = {"str": 123, "int": 1.5, "float": "abc", "bool": "maybe", "date": "2026-02-30"}


# ---------------------------------------------------------------- profile

@dataclasses.dataclass
class Table:
    """One resource of the prompt and the model of its rows."""
    name: str
    path: str
    fields: list[dict]
    query: dict
    service: bool  # timestamps, extra="forbid", 422 for null on a non-nullable column
    not_found: str
    conflict: str
    fk: str | None = None
    parent: "Table | None" = None
    rows: dict[str, dict] = dataclasses.field(default_factory=dict)  # token -> values, creation order
    created: int = 0  # tokens handed out
    counter: int = 0  # value index for fresh rows

    def field(self, name: str) -> dict:
        return next(f for f in self.fields if f["name"] == name)

    def nullable(self, field: dict) -> bool:
        if field["required"]:
            return False
        return not (self.service and field.get("default") is False)

    def default(self, field: dict) -> Any:
        return field.get("default")

    def types(self) -> dict[str, str]:
        return {f["name"]: f["type"] for f in self.fields}


@dataclasses.dataclass
class Profile:
    version: str
    tables: list[Table]

    @property
    def v5(self) -> bool:
        return self.version == "som.prompt.v5"


def profile(prompt: dict) -> Profile:
    version = prompt["version"]
    if version == "som.prompt.v5":
        parent_spec = prompt["resources"]["parent"]
        child_spec = prompt["resources"]["child"]
        conflict = "relationship or unique value conflict"
        parent = Table(parent_spec["table"], parent_spec["path"], parent_spec["fields"],
                       parent_spec["query"], True, "parent not found", conflict)
        child = Table(child_spec["table"], child_spec["path"], child_spec["fields"], child_spec["query"],
                      True, "child not found", conflict, fk=prompt["relation"]["foreign_key"], parent=parent)
        for table in (parent, child):
            for key in ("filter_field",):
                if table.query.get(key) and table.field(table.query[key])["type"] != "bool":
                    raise ValueError(f"{table.name}: a v5 filter field must be bool")
        return Profile(version, [parent, child])
    spec = prompt["resource"]
    service = version in ("som.prompt.v3", "som.prompt.v4")
    table = Table(spec["table"], spec["path"], spec["fields"], prompt.get("query") or {}, service,
                  "not found", "conflict")
    return Profile(version, [table])


# ---------------------------------------------------------------- values

def value(field: dict, k: int) -> Any:
    kind = field["type"]
    if kind == "str":
        return f"{field['name']}-{k}" if field["unique"] else STR_CYCLE[k % len(STR_CYCLE)]
    if kind == "int":
        return 100000 + 7 * k if field["unique"] else INT_CYCLE[k % len(INT_CYCLE)]
    if kind == "float":
        return FLOAT_CYCLE[k % len(FLOAT_CYCLE)]
    if kind == "bool":
        return k % 3 == 0
    if kind == "date":
        return (datetime.date(2026, 1, 1) + datetime.timedelta(days=(k * 37) % 365)).isoformat()
    raise ValueError(kind)


def changed(field: dict, current: Any, k: int) -> Any:
    """A valid value different from current. Unique fields get a value no row has used."""
    kind = field["type"]
    if kind == "str":
        return f"{field['name']}-patched-{k}" if field["unique"] else (
            "patched ✓" if current != "patched ✓" else "patched again")
    if kind == "int":
        return 900000 + k if field["unique"] else (-5 if current != -5 else 5)
    if kind == "float":
        return 6.5 if current != 6.5 else -6.5
    if kind == "bool":
        return not current if current is not None else True
    if kind == "date":
        return "2027-02-28" if current != "2027-02-28" else "2027-03-01"
    raise ValueError(kind)


def as_float(raw: Any) -> Any:
    return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else raw


# ---------------------------------------------------------------- normalization

def parse_ts(text: Any) -> datetime.datetime | None:
    if not isinstance(text, str):
        return None
    match = TS.match(text)
    if not match:
        return None
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    micro = int(((fraction or ".")[1:] + "000000")[:6])
    if offset in ("Z", "z"):
        zone = datetime.timezone.utc
    else:
        sign = 1 if offset[0] == "+" else -1
        zone = datetime.timezone(sign * datetime.timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6])))
    try:
        return datetime.datetime(int(year), int(month), int(day), int(hour), int(minute), int(second),
                                 micro, tzinfo=zone)
    except ValueError:
        return None


class Normalizer:
    """behavior-normalization.json for one product run. Ids become per-table creation-order
    tokens; timestamps become <timestamp> after their checks; float fields compare as floats."""

    def __init__(self) -> None:
        self.tokens: dict[str, dict[int, str]] = {}
        self.ids: dict[str, dict[str, int]] = {}
        self.stamps: dict[tuple[str, str], tuple[datetime.datetime, datetime.datetime]] = {}
        self.problems: list[str] = []

    def token(self, table: Table, raw: Any, assign: bool) -> Any:
        if type(raw) is not int:
            return {"<not-an-int-id>": raw}
        known = self.tokens.setdefault(table.name, {})
        if raw not in known:
            if not assign:
                return {"<unknown-id>": raw}
            known[raw] = f"id#{len(known) + 1}"
            self.ids.setdefault(table.name, {})[known[raw]] = raw
        return known[raw]

    def actual(self, table: Table, token: str) -> int:
        return self.ids.get(table.name, {}).get(token, MISSING_ID + 1)

    def row(self, table: Table, raw: Any, step: str, stamp: str | None) -> Any:
        if not isinstance(raw, dict):
            return {"<not-an-object>": raw}
        out: dict[str, Any] = {}
        types = table.types()
        token = self.token(table, raw.get("id"), True) if "id" in raw else None
        for key, item in raw.items():
            if key == "id":
                out[key] = token
            elif table.fk and key == table.fk and table.parent is not None:
                out[key] = self.token(table.parent, item, False)
            elif table.service and key in ("created_at", "updated_at"):
                out[key] = "<timestamp>" if parse_ts(item) else {"<not-rfc3339>": item}
            elif types.get(key) == "float":
                out[key] = as_float(item)
            elif types.get(key) == "int":
                out[key] = item if item is None or type(item) is int else {"<not-a-json-integer>": item}
            else:
                out[key] = item
        if table.service and isinstance(token, str):
            self.timestamps(table, token, raw, step, stamp)
        return out

    def timestamps(self, table: Table, token: str, raw: dict, step: str, stamp: str | None) -> None:
        created, updated = parse_ts(raw.get("created_at")), parse_ts(raw.get("updated_at"))
        where = f"{step}: {table.name} {token}"
        if created is None or updated is None:
            self.problems.append(f"{where}: created_at and updated_at must be RFC 3339 with an offset")
            return
        if created > updated:
            self.problems.append(f"{where}: created_at is after updated_at")
        before = self.stamps.get((table.name, token))
        if before is not None:
            if created != before[0]:
                self.problems.append(f"{where}: created_at changed")
            if updated < before[1]:
                self.problems.append(f"{where}: updated_at went back")
            if stamp == "advance" and not updated > before[1]:
                self.problems.append(f"{where}: updated_at did not advance after a change")
            if stamp == "same" and updated != before[1]:
                self.problems.append(f"{where}: updated_at changed without a change")
        self.stamps[(table.name, token)] = (created, updated)

    def response(self, status: int, media: str | None, raw: bytes, kind: str, tables: tuple[Table, ...],
                 step: str, stamp: str | None) -> Any:
        if status == 204:
            return None if raw == b"" else {"<204-body>": raw.decode("utf-8", "replace")[:200]}
        try:
            body = json.loads(raw)
        except ValueError:
            return {"<not-json>": raw.decode("utf-8", "replace")[:200]}
        if status == 422:
            return error_422(body)
        if not 200 <= status < 300:
            return body
        if kind == "row":
            return self.row(tables[0], body, step, stamp)
        if kind == "rows":
            if not isinstance(body, list):
                return {"<not-an-array>": body}
            return [self.row(tables[0], item, step, stamp) for item in body]
        if kind == "batch":
            if not isinstance(body, dict) or set(body) != {"parent", "children"} or not isinstance(
                    body["children"], list):
                return {"<not-a-batch>": body}
            parent = self.row(tables[0], body["parent"], step, stamp)
            return {"parent": parent, "children": [self.row(tables[1], item, step, stamp)
                                                   for item in body["children"]]}
        return body


def error_422(body: Any) -> Any:
    """Only the set of loc[0] values is compared. The body shape is checked."""
    detail = body.get("detail") if isinstance(body, dict) and set(body) == {"detail"} else None
    if not isinstance(detail, list) or not detail:
        return {"<invalid-422>": body}
    heads = set()
    for item in detail:
        if not (isinstance(item, dict) and {"type", "loc", "msg"} <= set(item)
                and isinstance(item["loc"], list) and item["loc"]):
            return {"<invalid-422>": body}
        heads.add(str(item["loc"][0]))
    return {"422_loc0": sorted(heads)}


def media_type(value: str | None) -> str | None:
    return value.split(";")[0].strip().lower() if value else None


# ---------------------------------------------------------------- transport

def http_send(base: str, method: str, path: str, body: bytes | None,
              content_type: str = "application/json") -> tuple[int, str | None, bytes]:
    parts = urllib.parse.urlsplit(base)
    connection = http.client.HTTPConnection(parts.hostname or "127.0.0.1", parts.port, timeout=60)
    try:
        headers = {"content-type": content_type} if body is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.getheader("content-type"), response.read()
    finally:
        connection.close()


Send = Callable[[str, str, bytes | None, str], tuple[int, str | None, bytes]]


# ---------------------------------------------------------------- script and oracle

class Run:
    """One run of the script against one product."""

    def __init__(self, prof: Profile, send: Send) -> None:
        self.prof = prof
        self.send = send
        self.norm = Normalizer()
        self.transcript: list[dict] = []  # normalized, compared for parity
        self.raw: list[dict] = []  # the exchange as sent and received
        self.failures: list[dict] = []  # oracle mismatches

    # -- one request

    def call(self, category: str, step: str, method: str, path: list, *, query: list | None = None,
             body: Any = None, raw_body: bytes | None = None, status: int, expect: Any,
             kind: str = "error", tables: tuple[Table, ...] = (), stamp: str | None = None,
             media: str | None = "application/json", content_type: str = "application/json") -> Any:
        """path is a list of text and (table, token) pieces. Tokens resolve to this product's ids."""
        symbolic = "".join(p if isinstance(p, str) else "{" + p[1] + "}" for p in path)
        actual = "".join(p if isinstance(p, str) else str(self.norm.actual(p[0], p[1])) for p in path)
        if query:
            encoded = urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
            symbolic += "?" + encoded
            actual += "?" + encoded
        payload = raw_body if raw_body is not None else (
            None if body is None else json.dumps(body, ensure_ascii=False).encode())
        if body is None and raw_body is None and method in ("POST", "PATCH"):
            payload = b""
        got_status, got_type, got_body = self.send(method, actual, payload, content_type)
        before = len(self.norm.problems)
        normalized = self.norm.response(got_status, got_type, got_body, kind, tables, step, stamp)
        entry = {"step": step, "category": category, "request": {"method": method, "path": symbolic},
                 "status": got_status, "media": media_type(got_type), "body": normalized}
        if payload is not None:
            entry["request"]["body"] = payload.decode("utf-8", "replace")
            if content_type != "application/json":
                entry["request"]["content_type"] = content_type
        self.transcript.append(entry)
        self.raw.append({"step": step, "method": method, "path": actual, "content_type": content_type,
                         "body": None if payload is None else payload.decode("utf-8", "replace"),
                         "status": got_status, "response_content_type": got_type,
                         "response": got_body.decode("utf-8", "replace")})
        # A 204 has no body, but its media type is still compared: v1 to v4 Python answers with
        # FastAPI's JSONResponse, which keeps application/json; v5 Python sends a bare Response.
        expected_media = (None if self.prof.v5 else "application/json") if status == 204 else media
        problems = []
        if got_status != status:
            problems.append(f"status {got_status}, expected {status}")
        if entry["media"] != expected_media:
            problems.append(f"media type {entry['media']}, expected {expected_media}")
        if not same(normalized, expect):
            problems.append("body differs")
        if problems:
            self.failures.append({"step": step, "category": category, "problems": problems,
                                  "expected": expect, "got": normalized})
        for problem in self.norm.problems[before:]:
            self.failures.append({"step": step, "category": "timestamps", "problems": [problem]})
        return normalized

    # -- expected bodies from the model

    def expected_row(self, table: Table, token: str) -> dict:
        row = {"id": token, **table.rows[token]["values"]}
        if table.fk:
            row[table.fk] = table.rows[token]["parent"]
        if table.service:
            row["created_at"] = row["updated_at"] = "<timestamp>"
        for field in table.fields:
            if field["type"] == "float":
                row[field["name"]] = as_float(row[field["name"]])
        return row

    def new_token(self, table: Table) -> str:
        table.created += 1
        return f"id#{table.created}"

    def store(self, table: Table, values: dict, parent: str | None = None) -> str:
        token = self.new_token(table)
        full = {f["name"]: values.get(f["name"], table.default(f)) for f in table.fields}
        table.rows[token] = {"values": full, "parent": parent}
        return token

    def fresh(self, table: Table, shape: str = "full") -> dict:
        """Values for a new row. shape: full, required (optional fields omitted) or nulls
        (nullable optional fields sent as null)."""
        table.counter += 1
        k = table.counter
        out = {}
        for field in table.fields:
            if field["required"] or shape == "full":
                out[field["name"]] = value(field, k)
            elif shape == "nulls":
                out[field["name"]] = None if table.nullable(field) else value(field, k)
        return out

    # -- paths

    def coll(self, table: Table, parent: str | None) -> list:
        if table.parent is None:
            return [table.path]
        return [table.parent.path + "/", (table.parent, parent), table.path]

    def item(self, table: Table, parent: str | None, token: str) -> list:
        return self.coll(table, parent) + ["/", (table, token)]

    def item_raw(self, table: Table, parent: str | None, text: str) -> list:
        return self.coll(table, parent) + ["/" + text]

    def top(self, table: Table) -> list:
        """The query for the largest page; v1 lists take no query."""
        return [] if self.prof.version == "som.prompt.v1" else [("limit", str(table.query["max_limit"]))]

    def harmless(self, table: Table) -> dict:
        """A valid patch body that cannot conflict."""
        field = next((f for f in table.fields if not f["unique"]), None)
        return {} if field is None else {field["name"]: value(field, 1)}

    def alive(self, table: Table, parent: str | None) -> list[str]:
        return [token for token, row in table.rows.items() if row["parent"] == parent]

    # -- the resource script, shared by v1 to v4, the v5 parent and the v5 child

    def create(self, table: Table, parent: str | None, values: dict, category: str = "crud",
               step: str = "create") -> str:
        token = self.store(table, values, parent)
        self.call(category, step, "POST", self.coll(table, parent), body=values, status=201,
                  expect=self.expected_row(table, token), kind="row", tables=(table,))
        return token

    def get(self, table: Table, parent: str | None, token: str, category: str = "crud",
            step: str = "get") -> None:
        self.call(category, step, "GET", self.item(table, parent, token), status=200,
                  expect=self.expected_row(table, token), kind="row", tables=(table,))

    def listing(self, table: Table, parent: str | None, query: list, category: str, step: str) -> None:
        rows = self.alive(table, parent)
        params = dict(query)
        q = table.query
        if self.prof.version == "som.prompt.v2" and "filter_value" in params:
            rows = [t for t in rows if table.rows[t]["values"][q["filter_field"]] == params["filter_value"]]
        if self.prof.version != "som.prompt.v2" and q.get("filter_field") and q["filter_field"] in params:
            wanted = params[q["filter_field"]] == "true"
            rows = [t for t in rows if table.rows[t]["values"][q["filter_field"]] is wanted]
        if q.get("date_field") and "before" in params:
            rows = [t for t in rows if table.rows[t]["values"][q["date_field"]] is not None
                    and table.rows[t]["values"][q["date_field"]] < params["before"]]
        if self.prof.version != "som.prompt.v1":
            offset = int(params.get("offset", 0))
            rows = rows[offset:offset + int(params.get("limit", 20))]
        self.call(category, step, "GET", self.coll(table, parent), query=query or None, status=200,
                  expect=[self.expected_row(table, t) for t in rows], kind="rows", tables=(table,))

    def refuse(self, category: str, step: str, method: str, path: list, loc: list[str], **kw: Any) -> None:
        self.call(category, step, method, path, status=422, expect={"422_loc0": loc}, **kw)

    def resource(self, table: Table, parent: str | None) -> list[str]:
        """Create, read, list, patch, refuse and delete rows of one table under one parent.
        Returns the tokens it leaves alive."""
        name = table.name
        coll = self.coll(table, parent)
        self.listing(table, parent, [], "crud", f"{name}: list when empty")
        tokens = []
        for k in range(1, ROWS + 1):
            shape = "required" if k % 5 == 0 else "nulls" if k % 5 == 1 else "full"
            category = "defaults" if shape == "required" else "null" if shape == "nulls" else "crud"
            tokens.append(self.create(table, parent, self.fresh(table, shape), category,
                                      f"{name}: create row {k} ({shape})"))
        for k, token in enumerate(tokens, 1):
            self.get(table, parent, token, "crud", f"{name}: get row {k}")
        self.lists(table, parent)
        self.not_found(table, parent)
        self.unique(table, parent, tokens)
        self.validation(table, parent, tokens)
        self.patches(table, parent, tokens)
        # delete, then the id is gone and the unique values are free again
        victim = tokens[2]
        values = dict(table.rows[victim]["values"])
        self.call("crud", f"{name}: delete row 3", "DELETE", self.item(table, parent, victim), status=204,
                  expect=None, media=None)
        del table.rows[victim]
        tokens.remove(victim)
        self.call("crud", f"{name}: get deleted row", "GET", self.item(table, parent, victim), status=404,
                  expect={"detail": table.not_found})
        self.call("crud", f"{name}: delete deleted row", "DELETE", self.item(table, parent, victim),
                  status=404, expect={"detail": table.not_found})
        self.call("crud", f"{name}: patch deleted row", "PATCH", self.item(table, parent, victim),
                  body={}, status=404, expect={"detail": table.not_found})
        tokens.append(self.create(table, parent, values, "unique", f"{name}: reuse deleted unique values"))
        self.listing(table, parent, self.top(table),
                     "order", f"{name}: list after delete")
        self.fallbacks(table, parent, tokens[0], coll)
        return tokens

    def lists(self, table: Table, parent: str | None) -> None:
        name, version, q = table.name, self.prof.version, table.query
        if version == "som.prompt.v1":
            self.listing(table, parent, [], "order", f"{name}: list all in id order")
            return
        top = str(q["max_limit"])
        self.listing(table, parent, [], "limit", f"{name}: list with the default limit")
        for query, category in (
                ([("limit", "1")], "limit"), ([("limit", top)], "limit"),
                ([("limit", "5"), ("offset", "3")], "pagination"), ([("offset", "20")], "pagination"),
                ([("offset", str(ROWS))], "pagination"), ([("offset", "1000")], "pagination"),
                ([("limit", top), ("offset", "0")], "order")):
            self.listing(table, parent, query, category, f"{name}: list {urllib.parse.urlencode(query)}")
        filters: list[list] = []
        if version == "som.prompt.v2":
            for text in STR_CYCLE + ["no such value", "alpha%"]:
                filters.append([("filter_value", text)])
        elif q.get("filter_field"):
            filters += [[(q["filter_field"], "true")], [(q["filter_field"], "false")]]
        for query in list(filters):
            filters.append(query + [("limit", "3"), ("offset", "1")])
        for query in filters:
            self.listing(table, parent, query, "filter", f"{name}: filter {urllib.parse.urlencode(query)}")
        if q.get("date_field"):
            cut = sorted(v["values"][q["date_field"]] for v in table.rows.values()
                         if v["values"][q["date_field"]] is not None and v["parent"] == parent)
            for before in (cut[len(cut) // 2], cut[0], "2999-12-31", "1999-01-01"):
                self.listing(table, parent, [("before", before)], "date_filter", f"{name}: before {before}")
            if q.get("filter_field"):
                query = [(q["filter_field"], "false"), ("before", cut[len(cut) // 2]), ("limit", "2")]
                self.listing(table, parent, query, "date_filter", f"{name}: filter and before")
        coll = self.coll(table, parent)
        for query, category in (([("limit", "0")], "limit"), ([("limit", str(q["max_limit"] + 1))], "limit"),
                                ([("limit", "many")], "limit"), ([("offset", "-1")], "pagination"),
                                ([("offset", "x")], "pagination")):
            self.refuse(category, f"{name}: refuse {urllib.parse.urlencode(query)}", "GET", coll, ["query"],
                        query=query)
        if version != "som.prompt.v2" and q.get("filter_field"):
            self.refuse("filter", f"{name}: refuse a bad boolean", "GET", coll, ["query"],
                        query=[(q["filter_field"], "maybe")])
        if q.get("date_field"):
            for bad in ("2026-02-30", "tomorrow"):
                self.refuse("date_filter", f"{name}: refuse before {bad}", "GET", coll, ["query"],
                            query=[("before", bad)])

    def not_found(self, table: Table, parent: str | None) -> None:
        name = table.name
        for text in (str(MISSING_ID), "0", "-1"):
            path = self.item_raw(table, parent, text)
            self.call("not_found", f"{name}: get missing {text}", "GET", path, status=404,
                      expect={"detail": table.not_found})
            self.call("not_found", f"{name}: patch missing {text}", "PATCH", path,
                      body=self.harmless(table), status=404, expect={"detail": table.not_found})
            self.call("not_found", f"{name}: delete missing {text}", "DELETE", path, status=404,
                      expect={"detail": table.not_found})
        bad = self.item_raw(table, parent, "abc")
        self.refuse("validation", f"{name}: get with a text id", "GET", bad, ["path"])
        self.refuse("validation", f"{name}: patch with a text id", "PATCH", bad, ["path"], body={})
        self.refuse("validation", f"{name}: delete with a text id", "DELETE", bad, ["path"])

    def unique(self, table: Table, parent: str | None, tokens: list[str]) -> None:
        name = table.name
        for field in (f for f in table.fields if f["unique"]):
            taken = table.rows[tokens[1]]["values"][field["name"]]
            values = self.fresh(table)
            values[field["name"]] = taken
            self.call("unique", f"{name}: create with a taken {field['name']}", "POST",
                      self.coll(table, parent), body=values, status=409, expect={"detail": table.conflict})
            self.call("unique", f"{name}: patch to a taken {field['name']}", "PATCH",
                      self.item(table, parent, tokens[3]), body={field["name"]: taken}, status=409,
                      expect={"detail": table.conflict})
            self.get(table, parent, tokens[3], "unique", f"{name}: row unchanged after a {field['name']} conflict")
        self.listing(table, parent, self.top(table), "unique", f"{name}: no row added by conflicts")

    def validation(self, table: Table, parent: str | None, tokens: list[str]) -> None:
        name, coll = table.name, self.coll(table, parent)
        base = self.fresh(table, "required")
        for field in table.fields:
            fname = field["name"]
            if field["required"]:
                values = {k: v for k, v in base.items() if k != fname}
                self.refuse("validation", f"{name}: create without {fname}", "POST", coll, ["body"], body=values)
                self.refuse("null", f"{name}: create with null {fname}", "POST", coll, ["body"],
                            body={**base, fname: None})
            elif not table.nullable(field):
                self.refuse("null", f"{name}: create with null {fname}", "POST", coll, ["body"],
                            body={**base, fname: None})
            self.refuse("validation", f"{name}: create with a wrong {fname} type", "POST", coll, ["body"],
                        body={**base, fname: WRONG_TYPE[field["type"]]})
            self.refuse("validation", f"{name}: patch with a wrong {fname} type", "PATCH",
                        self.item(table, parent, tokens[4]), ["body"], body={fname: WRONG_TYPE[field["type"]]})
        positional = json.dumps([base[f["name"]] for f in table.fields if f["name"] in base]).encode()
        for text, raw, content_type in (
                ("invalid JSON", b'{"a": ', "application/json"), ("an array body", b"[]", "application/json"),
                ("an empty body", b"", "application/json"), ("a number body", b"1", "application/json"),
                ("a null body", b"null", "application/json"),
                ("the values as a positional array", positional, "application/json"),
                ("a JSON object sent as text/plain", json.dumps(base).encode(), "text/plain")):
            self.refuse("validation", f"{name}: create with {text}", "POST", coll, ["body"], raw_body=raw,
                        content_type=content_type)
            self.refuse("validation", f"{name}: patch with {text}", "PATCH", self.item(table, parent, tokens[4]),
                        ["body"], raw_body=raw, content_type=content_type)
        if table.service:
            self.refuse("validation", f"{name}: create with an unknown member", "POST", coll, ["body"],
                        body={**base, "zz_unknown": 1})
            self.refuse("validation", f"{name}: patch with an unknown member", "PATCH",
                        self.item(table, parent, tokens[4]), ["body"], body={"zz_unknown": 1})
        else:
            token = self.store(table, base, parent)
            self.call("validation", f"{name}: create ignores an unknown member", "POST", coll,
                      body={**base, "zz_unknown": 1}, status=201, expect=self.expected_row(table, token),
                      kind="row", tables=(table,))
            tokens.append(token)
        self.get(table, parent, tokens[4], "validation", f"{name}: row unchanged after refused patches")
        self.listing(table, parent, self.top(table), "validation",
                     f"{name}: no row added by refused creates")

    def patches(self, table: Table, parent: str | None, tokens: list[str]) -> None:
        name = table.name
        target = tokens[6]  # a full row
        path = self.item(table, parent, target)
        k = 0
        for field in table.fields:
            k += 1
            current = table.rows[target]["values"][field["name"]]
            new = changed(field, current, k)
            table.rows[target]["values"][field["name"]] = new
            self.call("crud", f"{name}: patch {field['name']}", "PATCH", path, body={field["name"]: new},
                      status=200, expect=self.expected_row(table, target), kind="row", tables=(table,),
                      stamp="advance")
            self.get(table, parent, target, "crud", f"{name}: get after patching {field['name']}")
        self.call("crud", f"{name}: empty patch", "PATCH", path, body={}, status=200,
                  expect=self.expected_row(table, target), kind="row", tables=(table,), stamp="same")
        long_field = next((f for f in table.fields if f["type"] == "str"), None)
        if long_field is not None:
            table.rows[target]["values"][long_field["name"]] = "é" * 255
            self.call("crud", f"{name}: patch {long_field['name']} to 255 characters", "PATCH", path,
                      body={long_field["name"]: "é" * 255}, status=200, expect=self.expected_row(table, target),
                      kind="row", tables=(table,), stamp="advance")
        whole = {}
        for field in table.fields:
            k += 1
            whole[field["name"]] = changed(field, table.rows[target]["values"][field["name"]], k)
        table.rows[target]["values"].update(whole)
        self.call("crud", f"{name}: patch every field", "PATCH", path, body=whole, status=200,
                  expect=self.expected_row(table, target), kind="row", tables=(table,), stamp="advance")
        for field in table.fields:
            fname = field["name"]
            if table.nullable(field):
                table.rows[target]["values"][fname] = None
                self.call("null", f"{name}: patch {fname} to null", "PATCH", path, body={fname: None},
                          status=200, expect=self.expected_row(table, target), kind="row", tables=(table,),
                          stamp="advance")
            elif table.service:
                self.refuse("null", f"{name}: refuse null {fname}", "PATCH", path, ["body"], body={fname: None})
            else:
                # v1 and v2: the patch passes validation and the NOT NULL column refuses it.
                self.call("null", f"{name}: null {fname} conflicts", "PATCH", path, body={fname: None},
                          status=409, expect={"detail": table.conflict})
        self.get(table, parent, target, "null", f"{name}: get after null patches")
        if not table.service:
            self.call("validation", f"{name}: patch ignores an unknown member", "PATCH", path,
                      body={"zz_unknown": 1}, status=200, expect=self.expected_row(table, target), kind="row",
                      tables=(table,))

    def fallbacks(self, table: Table, parent: str | None, token: str, coll: list) -> None:
        name = table.name
        self.call("fallback", f"{name}: unknown route", "GET", coll + ["-none/x"], status=404,
                  expect={"detail": "Not Found"})
        self.call("fallback", f"{name}: put on the collection", "PUT", coll, body={}, status=405,
                  expect={"detail": "Method Not Allowed"})
        self.call("fallback", f"{name}: delete on the collection", "DELETE", coll, status=405,
                  expect={"detail": "Method Not Allowed"})
        self.call("fallback", f"{name}: post on an item", "POST", self.item(table, parent, token), body={},
                  status=405, expect={"detail": "Method Not Allowed"})

    # -- v5

    def all_parents(self, parent: Table, category: str, step: str) -> None:
        """Every parent row, page by page."""
        top = parent.query["max_limit"]
        count = len(parent.rows)
        for offset in range(0, count + 1, top):
            self.listing(parent, None, [("limit", str(top)), ("offset", str(offset))], category,
                         f"{step} (offset {offset})")

    def v5(self) -> None:
        parent, child = self.prof.tables
        self.call("healthz", "healthz", "GET", ["/healthz"], status=200, expect={"status": "ok"})
        parents = self.resource(parent, None)
        first, second = parents[0], parents[1]
        children = self.resource(child, first)
        # a second parent with its own children
        others = [self.create(child, second, self.fresh(child), "isolation", f"child of the second parent {k}")
                  for k in range(1, 4)]
        self.listing(child, second, [], "isolation", "second parent lists only its children")
        self.listing(child, first, self.top(child), "isolation", "first parent lists only its children")
        for method, body in (("GET", None), ("PATCH", {}), ("DELETE", None)):
            self.call("isolation", f"{method} a child under the wrong parent", method,
                      self.item(child, second, children[0]), body=body, status=404,
                      expect={"detail": "child not found"})
        self.get(child, first, children[0], "isolation", "child unchanged after wrong-parent requests")
        # a missing parent
        missing = self.coll(parent, None) + [f"/{MISSING_ID}"]
        cpath = missing + [child.path]
        self.call("scope", "create a child under a missing parent", "POST", cpath, body=self.fresh(child),
                  status=404, expect={"detail": "parent not found"})
        self.call("scope", "list children of a missing parent", "GET", cpath, status=404,
                  expect={"detail": "parent not found"})
        for method, body in (("GET", None), ("PATCH", {}), ("DELETE", None)):
            self.call("scope", f"{method} a child under a missing parent", method,
                      cpath + ["/", (child, children[0])], body=body, status=404,
                      expect={"detail": "parent not found"})
        self.listing(child, first, self.top(child), "scope", "no child added under a missing parent")
        # restricted parent delete
        for token, label in ((first, "first"), (second, "second")):
            self.call("restrict", f"delete the {label} parent with children", "DELETE",
                      self.item(parent, None, token), status=409, expect={"detail": parent.conflict})
            self.get(parent, None, token, "restrict", f"{label} parent kept")
        self.listing(child, second, [], "restrict", "children kept after a refused parent delete")
        for token in others:
            self.call("restrict", "delete a child of the second parent", "DELETE",
                      self.item(child, second, token), status=204, expect=None, media=None)
            del child.rows[token]
        self.call("restrict", "delete the emptied second parent", "DELETE", self.item(parent, None, second),
                  status=204, expect=None, media=None)
        del parent.rows[second]
        self.call("restrict", "list children of the deleted parent", "GET", self.coll(child, second),
                  status=404, expect={"detail": "parent not found"})
        self.bounds(parent, child, first)
        self.batches(parent, child)

    def bounds(self, parent: Table, child: Table, first: str) -> None:
        """D2 is compared for v5: 255 characters and i32 are the limits."""
        for table, owner in ((parent, None), (child, first)):
            coll = self.coll(table, owner)
            for field in table.fields:
                fname = field["name"]
                if field["type"] == "str":
                    over = self.fresh(table)
                    over[fname] = "é" * 256
                    self.refuse("bounds", f"{table.name}: create with 256 characters in {fname}", "POST", coll,
                                ["body"], body=over)
                    edge = self.fresh(table)
                    edge[fname] = ("é" * 255 + str(table.counter))[-255:] if field["unique"] else "é" * 255
                    self.create(table, owner, edge, "bounds", f"{table.name}: create with 255 characters in {fname}")
                if field["type"] == "int":
                    for wrong in (2147483648, -2147483649):
                        over = self.fresh(table)
                        over[fname] = wrong
                        self.refuse("bounds", f"{table.name}: create with {fname} {wrong}", "POST", coll,
                                    ["body"], body=over)
            if table.fields:
                token = next(iter(self.alive(table, owner)))
                field = next((f for f in table.fields if f["type"] in ("str", "int")), None)
                if field is not None:
                    wrong = "é" * 256 if field["type"] == "str" else 2147483648
                    self.refuse("bounds", f"{table.name}: patch {field['name']} out of bounds", "PATCH",
                                self.item(table, owner, token), ["body"], body={field["name"]: wrong})

    def batch_body(self, parent: Table, child: Table, count: int) -> tuple[dict, dict, list[dict]]:
        top = self.fresh(parent)
        kids = [self.fresh(child) for _ in range(count)]
        return {"parent": top, "children": kids}, top, kids

    def batches(self, parent: Table, child: Table) -> None:
        path = [parent.path + "/with-children"]
        for count in (1, 20):
            body, top, kids = self.batch_body(parent, child, count)
            ptoken = self.store(parent, top)
            ctokens = [self.store(child, kid, ptoken) for kid in kids]
            expect = {"parent": self.expected_row(parent, ptoken),
                      "children": [self.expected_row(child, t) for t in ctokens]}
            self.call("batch", f"batch with {count} children", "POST", path, body=body, status=201,
                      expect=expect, kind="batch", tables=(parent, child))
            self.get(parent, None, ptoken, "batch", f"parent of the {count}-child batch")
            self.listing(child, ptoken, self.top(child), "batch", f"children of the {count}-child batch")
        for count in (0, 21):
            body, _, _ = self.batch_body(parent, child, count)
            self.refuse("batch", f"refuse a batch with {count} children", "POST", path, ["body"], body=body)
        body, _, kids = self.batch_body(parent, child, 3)
        long_field = next((f for f in child.fields if f["type"] == "str"), None)
        if long_field is not None:
            kids[2][long_field["name"]] = "é" * 256
            self.refuse("bounds", "refuse a batch with a child out of bounds", "POST", path, ["body"], body=body)
        # failures inside the transaction: nothing may stay behind
        for field in (f for f in child.fields if f["unique"]):
            body, _, kids = self.batch_body(parent, child, 3)
            kids[2][field["name"]] = kids[0][field["name"]]
            self.call("batch_atomic", f"batch with two children sharing {field['name']}", "POST", path,
                      body=body, status=409, expect={"detail": parent.conflict})
            body, _, kids = self.batch_body(parent, child, 2)
            taken = next(iter(child.rows.values()))["values"][field["name"]]
            kids[1][field["name"]] = taken
            self.call("batch_atomic", f"batch with a child taking an existing {field['name']}", "POST", path,
                      body=body, status=409, expect={"detail": parent.conflict})
        for field in (f for f in parent.fields if f["unique"]):
            body, top, _ = self.batch_body(parent, child, 2)
            top[field["name"]] = next(iter(parent.rows.values()))["values"][field["name"]]
            self.call("batch_atomic", f"batch with a parent taking an existing {field['name']}", "POST", path,
                      body=body, status=409, expect={"detail": parent.conflict})
        self.all_parents(parent, "batch_atomic", "no parent left by refused batches")
        # the next batch still works and gets the next tokens
        body, top, kids = self.batch_body(parent, child, 2)
        ptoken = self.store(parent, top)
        ctokens = [self.store(child, kid, ptoken) for kid in kids]
        self.call("batch_atomic", "batch after refused batches", "POST", path, body=body, status=201,
                  expect={"parent": self.expected_row(parent, ptoken),
                          "children": [self.expected_row(child, t) for t in ctokens]},
                  kind="batch", tables=(parent, child))

    # -- D1 to D5 on a Rust product alone

    def strict(self) -> None:
        """The strict refusals of the divergences D1 to D5 (behavior-normalization.json). Python
        behaves differently on these inputs by contract, so they are checked on Rust alone."""
        table = self.prof.tables[0]
        coll = self.coll(table, None)
        token = next(iter(self.alive(table, None)))
        item = self.item(table, None, token)
        base = self.fresh(table, "required")
        lax = {"int": ["1", 1.0], "float": ["1.5"], "bool": ["true", 1]}
        for field in table.fields:
            fname, kind = field["name"], field["type"]
            for value in lax.get(kind, []):
                label = json.dumps(value)
                self.refuse("D1", f"D1: create with {fname} as {label}", "POST", coll, ["body"],
                            body={**base, fname: value})
                self.refuse("D1", f"D1: patch {fname} as {label}", "PATCH", item, ["body"], body={fname: value})
            wrong = ["é" * 256] if kind == "str" else [2147483648, -2147483649] if kind == "int" else []
            for bad in wrong:
                label = "256 characters" if kind == "str" else str(bad)
                self.refuse("D2", f"D2: create with {fname} {label}", "POST", coll, ["body"], body={**base, fname: bad})
                self.refuse("D2", f"D2: patch {fname} {label}", "PATCH", item, ["body"], body={fname: bad})
        for text in ("2147483648", "-2147483649", "99999999999999999999"):
            path = self.item_raw(table, None, text)
            self.call("D3", f"D3: get {text}", "GET", path, status=404, expect={"detail": table.not_found})
            self.call("D3", f"D3: patch {text}", "PATCH", path, body=self.harmless(table), status=404,
                      expect={"detail": table.not_found})
            self.call("D3", f"D3: delete {text}", "DELETE", path, status=404, expect={"detail": table.not_found})
        if self.prof.version != "som.prompt.v1":
            repeats = [[("limit", "1"), ("limit", "2")], [("offset", "0"), ("offset", "1")]]
            if table.query.get("filter_field"):
                name = "filter_value" if self.prof.version == "som.prompt.v2" else table.query["filter_field"]
                repeats.append([(name, "true"), (name, "true")])
            if table.query.get("date_field"):
                repeats.append([("before", "2026-06-01"), ("before", "2026-06-01")])
            for query in repeats:
                self.refuse("D4", f"D4: repeated {query[0][0]}", "GET", coll, ["query"], query=query)
        for method, path, body in (("GET", coll + ["/"], None), ("POST", coll + ["/"], base),
                                   ("GET", item + ["/"], None), ("PATCH", item + ["/"], {}),
                                   ("DELETE", item + ["/"], None)):
            self.call("D5", f"D5: {method} with a trailing slash", method, path, body=body, status=404,
                      expect={"detail": "Not Found"})
        self.get(table, None, token, "D2", "D1 to D5 changed no row")
        self.listing(table, None, self.top(table), "D2", "D1 to D5 added no row")

    # -- entry

    def script(self) -> None:
        if self.prof.v5:
            self.v5()
        else:
            self.resource(self.prof.tables[0], None)


def same(left: Any, right: Any) -> bool:
    """Equality where member order is ignored, array order counts and bool is not a number."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(same(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return type(left) is type(right) and left == right
    return type(left) is type(right) and left == right


def run(prompt: dict, base: str, send: Send | None = None) -> Run:
    """Run the script for prompt against the product at base."""
    sender = send or (lambda method, path, body, content_type: http_send(base, method, path, body, content_type))
    result = Run(profile(prompt), sender)
    result.script()
    return result


def compare(python: list[dict], rust: list[dict]) -> list[dict]:
    """Parity: the normalized transcripts must agree step by step on status, media type and body."""
    diffs = []
    if [e["step"] for e in python] != [e["step"] for e in rust]:
        return [{"step": "<script>", "problem": "the two runs sent different steps"}]
    for left, right in zip(python, rust):
        if left["request"] != right["request"]:
            diffs.append({"step": left["step"], "problem": "different requests",
                          "python": left["request"], "rust": right["request"]})
            continue
        for key in ("status", "media", "body"):
            if not same(left[key], right[key]):
                diffs.append({"step": left["step"], "category": left["category"], "member": key,
                              "python": left[key], "rust": right[key]})
    return diffs
