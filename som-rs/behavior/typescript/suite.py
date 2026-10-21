"""TypeScript phase 1 behavior parity and strict refusal suite (issue #76).

Implements som-rs/contracts/typescript-output-v1/behavior-normalization.json on top of
som-rs/behavior/suite.py without modifying existing Rust behavior suite files.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
BEHAVIOR_DIR = HERE.parent
if str(BEHAVIOR_DIR) not in sys.path:
    sys.path.insert(0, str(BEHAVIOR_DIR))

_suite_path = BEHAVIOR_DIR / "suite.py"
if "som_behavior_base_suite" in sys.modules:
    base_suite = sys.modules["som_behavior_base_suite"]
else:
    _spec = importlib.util.spec_from_file_location("som_behavior_base_suite", _suite_path)
    assert _spec and _spec.loader
    base_suite = importlib.util.module_from_spec(_spec)
    sys.modules["som_behavior_base_suite"] = base_suite
    _spec.loader.exec_module(base_suite)

Profile = base_suite.Profile
Table = base_suite.Table
Send = base_suite.Send
Normalizer = base_suite.Normalizer
error_422 = base_suite.error_422
http_send = base_suite.http_send
media_type = base_suite.media_type
profile = base_suite.profile
same = base_suite.same


def non_bmp_255(serial: int) -> str:
    """A string of exactly 255 Unicode code points containing one non-BMP character (256 UTF-16 code units)."""
    prefix = f"\U0001f600{serial:04d}-"
    text = prefix + ("a" * (255 - len(prefix)))
    assert len(text) == 255
    assert len(text.encode("utf-16-le")) // 2 == 256
    return text


def non_bmp_256(serial: int = 1) -> str:
    """A string of 256 Unicode code points containing one non-BMP character (257 UTF-16 code units)."""
    prefix = f"\U0001f600{serial:04d}-"
    text = prefix + ("a" * (256 - len(prefix)))
    assert len(text) == 256
    assert len(text.encode("utf-16-le")) // 2 == 257
    return text


def _unused_int(table: Table, fname: str, preferred: int) -> int:
    taken = {row["values"].get(fname) for row in table.rows.values()}
    candidate = preferred
    while candidate in taken:
        candidate += 1
    return candidate


class TsRun(base_suite.Run):
    """Behavior run for TypeScript phase 1 products (parity script + added_inputs + D1-D5 & D7 strict checks)."""

    def table_added_inputs(self, table: Table, parent: str | None) -> None:
        """Execute behavior-normalization.json request_scripts.added_inputs for one table."""
        name = table.name
        coll = self.coll(table, parent)
        alive = self.alive(table, parent)
        if not alive:
            return
        item = self.item(table, parent, alive[0])

        for field in table.fields:
            fname, kind = field["name"], field["type"]
            if kind == "int":
                # 1. Each int field sent as a JSON number with a zero fraction (e.g. 1.0):
                # both products accept it and return the JSON integer.
                n1 = _unused_int(table, fname, 1)
                base = self.fresh(table, "required")
                wire_create = {**base, fname: float(n1)}
                oracle_create = {**base, fname: int(n1)}
                tok = self.store(table, oracle_create, parent)
                self.call(
                    "validation",
                    f"{name}: create {fname} with JSON {float(n1):.1f}",
                    "POST",
                    coll,
                    body=wire_create,
                    status=201,
                    expect=self.expected_row(table, tok),
                    kind="row",
                    tables=(table,),
                )
                n2 = _unused_int(table, fname, 2 if n1 != 2 else 3)
                table.rows[tok]["values"][fname] = int(n2)
                self.call(
                    "validation",
                    f"{name}: patch {fname} with JSON {float(n2):.1f}",
                    "PATCH",
                    self.item(table, parent, tok),
                    body={fname: float(n2)},
                    status=200,
                    expect=self.expected_row(table, tok),
                    kind="row",
                    tables=(table,),
                    stamp="advance",
                )
            elif kind == "float":
                # 2. Each float field sent as a JSON integer:
                # both products accept it; the numbers rule compares the returned values as float.
                i1 = int(_unused_int(table, fname, 7))
                base = self.fresh(table, "required")
                wire_create = {**base, fname: i1}
                oracle_create = {**base, fname: float(i1)}
                tok = self.store(table, oracle_create, parent)
                self.call(
                    "validation",
                    f"{name}: create {fname} with JSON integer {i1}",
                    "POST",
                    coll,
                    body=wire_create,
                    status=201,
                    expect=self.expected_row(table, tok),
                    kind="row",
                    tables=(table,),
                )
                i2 = int(_unused_int(table, fname, 8 if i1 != 8 else 9))
                table.rows[tok]["values"][fname] = float(i2)
                self.call(
                    "validation",
                    f"{name}: patch {fname} with JSON integer {i2}",
                    "PATCH",
                    self.item(table, parent, tok),
                    body={fname: i2},
                    status=200,
                    expect=self.expected_row(table, tok),
                    kind="row",
                    tables=(table,),
                    stamp="advance",
                )
            elif kind == "str":
                # 3. Each str field of 255 Unicode code points containing a non-BMP character
                # (256 UTF-16 code units): both products accept it.
                table.counter += 1
                s1 = non_bmp_255(table.counter)
                base = self.fresh(table, "required")
                values = {**base, fname: s1}
                tok = self.store(table, values, parent)
                self.call(
                    "validation",
                    f"{name}: create {fname} with 255 non-BMP code points",
                    "POST",
                    coll,
                    body=values,
                    status=201,
                    expect=self.expected_row(table, tok),
                    kind="row",
                    tables=(table,),
                )
                table.counter += 1
                s2 = non_bmp_255(table.counter)
                table.rows[tok]["values"][fname] = s2
                self.call(
                    "validation",
                    f"{name}: patch {fname} with 255 non-BMP code points",
                    "PATCH",
                    self.item(table, parent, tok),
                    body={fname: s2},
                    status=200,
                    expect=self.expected_row(table, tok),
                    kind="row",
                    tables=(table,),
                    stamp="advance",
                )
                # 4. For v5 only, a str field value of 256 Unicode code points: both products return 422.
                if self.prof.v5:
                    s256 = non_bmp_256(table.counter)
                    self.refuse(
                        "bounds",
                        f"{name}: refuse create {fname} of 256 non-BMP code points",
                        "POST",
                        coll,
                        ["body"],
                        body={**base, fname: s256},
                    )
                    self.refuse(
                        "bounds",
                        f"{name}: refuse patch {fname} of 256 non-BMP code points",
                        "PATCH",
                        item,
                        ["body"],
                        body={fname: s256},
                    )

        self.listing(table, parent, self.top(table), "order", f"{name}: list after TypeScript added inputs")

    def added_inputs(self) -> None:
        """Run all TypeScript added_inputs across the prompt's tables."""
        if self.prof.v5:
            parent, child = self.prof.tables
            self.table_added_inputs(parent, None)
            ptoken = self.alive(parent, None)[0]
            self.table_added_inputs(child, ptoken)
        else:
            self.table_added_inputs(self.prof.tables[0], None)

    def script(self) -> None:
        super().script()
        self.added_inputs()

    def strict(self) -> None:
        """Check divergences D1 to D5 and D7 on a TypeScript product alone."""
        table = self.prof.tables[0]
        coll = self.coll(table, None)
        token = next(iter(self.alive(table, None)))
        item = self.item(table, None, token)
        base = self.fresh(table, "required")
        # D1: Pydantic lax coercion refused by TypeScript.
        # Note: a JSON number with a zero fraction (1.0) for an int field is NOT a divergence in TypeScript.
        lax = {"int": ["1"], "float": ["1.5"], "bool": ["true", 1]}
        for field in table.fields:
            fname, kind = field["name"], field["type"]
            for value in lax.get(kind, []):
                label = json.dumps(value)
                self.refuse("D1", f"D1: create with {fname} as {label}", "POST", coll, ["body"],
                            body={**base, fname: value})
                self.refuse("D1", f"D1: patch {fname} as {label}", "PATCH", item, ["body"], body={fname: value})
            if kind == "str":
                for bad, label in (("é" * 256, "256 characters"), (non_bmp_256(1), "256 non-BMP code points")):
                    self.refuse("D2", f"D2: create with {fname} {label}", "POST", coll, ["body"],
                                body={**base, fname: bad})
                    self.refuse("D2", f"D2: patch {fname} {label}", "PATCH", item, ["body"], body={fname: bad})
            elif kind == "int":
                for bad in (2147483648, -2147483649):
                    self.refuse("D2", f"D2: create with {fname} {bad}", "POST", coll, ["body"],
                                body={**base, fname: bad})
                    self.refuse("D2", f"D2: patch {fname} {bad}", "PATCH", item, ["body"], body={fname: bad})

        # D3: path id outside signed 32-bit integer returns 404 without querying PostgreSQL.
        for text in ("2147483648", "-2147483649", "99999999999999999999"):
            path = self.item_raw(table, None, text)
            self.call("D3", f"D3: get {text}", "GET", path, status=404, expect={"detail": table.not_found})
            self.call("D3", f"D3: patch {text}", "PATCH", path, body=self.harmless(table), status=404,
                      expect={"detail": table.not_found})
            self.call("D3", f"D3: delete {text}", "DELETE", path, status=404, expect={"detail": table.not_found})

        # D4: query parameter given twice returns 422.
        if self.prof.version != "som.prompt.v1":
            repeats = [[("limit", "1"), ("limit", "2")], [("offset", "0"), ("offset", "1")]]
            if table.query.get("filter_field"):
                name = "filter_value" if self.prof.version == "som.prompt.v2" else table.query["filter_field"]
                repeats.append([(name, "true"), (name, "true")])
            if table.query.get("date_field"):
                repeats.append([("before", "2026-06-01"), ("before", "2026-06-01")])
            for query in repeats:
                self.refuse("D4", f"D4: repeated {query[0][0]}", "GET", coll, ["query"], query=query)

        # D5: trailing slash returns 404 {"detail": "Not Found"}.
        for method, path, body in (
            ("GET", coll + ["/"], None),
            ("POST", coll + ["/"], base),
            ("GET", item + ["/"], None),
            ("PATCH", item + ["/"], {}),
            ("DELETE", item + ["/"], None),
        ):
            self.call("D5", f"D5: {method} with a trailing slash", method, path, body=body, status=404,
                      expect={"detail": "Not Found"})

        # D7: non-canonical integer text in path/query and Object.prototype property names in JSON bodies.
        for raw_id, label in (("%201", "leading space"), ("1_0", "underscore"), ("1.0", "fraction")):
            path = self.item_raw(table, None, raw_id)
            self.refuse("D7", f"D7: get path id with {label}", "GET", path, ["path"])
            self.refuse("D7", f"D7: patch path id with {label}", "PATCH", path, ["path"], body={})
            self.refuse("D7", f"D7: delete path id with {label}", "DELETE", path, ["path"])

        if self.prof.version != "som.prompt.v1":
            for param in ("limit", "offset"):
                for bad_text in (" 1", "1_0", "1.0"):
                    self.refuse("D7", f"D7: query {param}={bad_text!r}", "GET", coll, ["query"],
                                query=[(param, bad_text)])

        field_names = {f["name"] for f in table.fields}
        has_required = any(f["required"] for f in table.fields)
        if "toString" not in field_names and has_required:
            self.refuse("D7", "D7: create with Object.prototype toString", "POST", coll, ["body"],
                        body={"toString": "x"})
        if "toString" not in field_names:
            if table.service:
                self.refuse("D7", "D7: patch with Object.prototype toString", "PATCH", item, ["body"],
                            body={"toString": "x"})
            else:
                self.call("D7", "D7: patch ignores Object.prototype toString", "PATCH", item,
                          body={"toString": "x"}, status=200, expect=self.expected_row(table, token),
                          kind="row", tables=(table,), stamp="same")

        self.get(table, None, token, "D2", "D1 to D7 changed no row")
        self.listing(table, None, self.top(table), "D2", "D1 to D7 added no row")


def run(prompt: dict, base: str, send: Send | None = None) -> TsRun:
    """Run the TypeScript parity script (including added_inputs) for prompt against base."""
    sender = send or (lambda method, path, body, content_type: http_send(base, method, path, body, content_type))
    result = TsRun(profile(prompt), sender)
    result.script()
    return result


def compare(python: list[dict], typescript: list[dict]) -> list[dict]:
    """Compare normalized Python and TypeScript transcripts step by step. Any 500 is a parity failure."""
    diffs: list[dict] = []
    if [e["step"] for e in python] != [e["step"] for e in typescript]:
        return [{"step": "<script>", "problem": "the two runs sent different steps"}]
    for left, right in zip(python, typescript):
        if left["request"] != right["request"]:
            diffs.append({
                "step": left["step"],
                "problem": "different requests",
                "python": left["request"],
                "typescript": right["request"],
            })
            continue
        if left["status"] == 500 or right["status"] == 500:
            diffs.append({
                "step": left["step"],
                "category": left["category"],
                "member": "status",
                "problem": "500 internal error is always a parity failure",
                "python": left["status"],
                "typescript": right["status"],
            })
            continue
        for key in ("status", "media", "body"):
            if not same(left[key], right[key]):
                diffs.append({
                    "step": left["step"],
                    "category": left["category"],
                    "member": key,
                    "python": left[key],
                    "typescript": right[key],
                })
    return diffs
