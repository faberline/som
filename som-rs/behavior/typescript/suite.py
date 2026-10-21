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
            self.refuse(
                "validation",
                f"{parent.name}: GET with-children refuses non-integer parent_id",
                "GET",
                [parent.path + "/with-children"],
                ["path"],
            )
        else:
            self.table_added_inputs(self.prof.tables[0], None)

    def script(self) -> None:
        super().script()
        self.added_inputs()

    def _ensure_live_token(self, table: Table, parent: str | None) -> str:
        """Return an existing live row token for (table, parent), or seed one when strict() runs standalone."""
        alive = self.alive(table, parent)
        if alive:
            return alive[0]
        token = self.store(table, self.fresh(table, "required"), parent)
        actual_id = len(self.norm.ids.get(table.name, {})) + 1
        self.norm.ids.setdefault(table.name, {})[token] = actual_id
        self.norm.tokens.setdefault(table.name, {})[actual_id] = token
        return token

    def table_strict(self, table: Table, parent: str | None, token: str) -> None:
        """Check divergences D1 to D5, D7 and prototype_members on one resource table."""
        name = table.name
        coll = self.coll(table, parent)
        item = self.item(table, parent, token)
        base = self.fresh(table, "required")
        # D1: Pydantic lax coercion refused by TypeScript.
        # Note: a JSON number with a zero fraction (1.0) for an int field is NOT a divergence in TypeScript.
        lax = {"int": ["1"], "float": ["1.5"], "bool": ["true", 1]}
        for field in table.fields:
            fname, kind = field["name"], field["type"]
            for value in lax.get(kind, []):
                label = json.dumps(value)
                self.refuse(
                    "D1",
                    f"D1: {name}: create with {fname} as {label}",
                    "POST",
                    coll,
                    ["body"],
                    body={**base, fname: value},
                )
                self.refuse(
                    "D1",
                    f"D1: {name}: patch {fname} as {label}",
                    "PATCH",
                    item,
                    ["body"],
                    body={fname: value},
                )
            if kind == "str":
                for bad, label in (("é" * 256, "256 characters"), (non_bmp_256(1), "256 non-BMP code points")):
                    self.refuse(
                        "D2",
                        f"D2: {name}: create with {fname} {label}",
                        "POST",
                        coll,
                        ["body"],
                        body={**base, fname: bad},
                    )
                    self.refuse(
                        "D2",
                        f"D2: {name}: patch {fname} {label}",
                        "PATCH",
                        item,
                        ["body"],
                        body={fname: bad},
                    )
            elif kind == "int":
                for bad in (2147483648, -2147483649):
                    self.refuse(
                        "D2",
                        f"D2: {name}: create with {fname} {bad}",
                        "POST",
                        coll,
                        ["body"],
                        body={**base, fname: bad},
                    )
                    self.refuse(
                        "D2",
                        f"D2: {name}: patch {fname} {bad}",
                        "PATCH",
                        item,
                        ["body"],
                        body={fname: bad},
                    )

        # D3: path id outside signed 32-bit integer returns 404 without querying PostgreSQL.
        for text in ("2147483648", "-2147483649", "99999999999999999999"):
            path = self.item_raw(table, parent, text)
            self.call("D3", f"D3: {name}: get {text}", "GET", path, status=404, expect={"detail": table.not_found})
            self.call(
                "D3",
                f"D3: {name}: patch {text}",
                "PATCH",
                path,
                body=self.harmless(table),
                status=404,
                expect={"detail": table.not_found},
            )
            self.call(
                "D3",
                f"D3: {name}: delete {text}",
                "DELETE",
                path,
                status=404,
                expect={"detail": table.not_found},
            )

        # D4: query parameter given twice returns 422.
        if self.prof.version != "som.prompt.v1":
            repeats = [[("limit", "1"), ("limit", "2")], [("offset", "0"), ("offset", "1")]]
            if table.query.get("filter_field"):
                qname = "filter_value" if self.prof.version == "som.prompt.v2" else table.query["filter_field"]
                repeats.append([(qname, "true"), (qname, "true")])
            if table.query.get("date_field"):
                repeats.append([("before", "2026-06-01"), ("before", "2026-06-01")])
            for query in repeats:
                self.refuse("D4", f"D4: {name}: repeated {query[0][0]}", "GET", coll, ["query"], query=query)

        # D5: trailing slash returns 404 {"detail": "Not Found"}.
        for method, path, body in (
            ("GET", coll + ["/"], None),
            ("POST", coll + ["/"], base),
            ("GET", item + ["/"], None),
            ("PATCH", item + ["/"], {}),
            ("DELETE", item + ["/"], None),
        ):
            self.call(
                "D5",
                f"D5: {name}: {method} with a trailing slash",
                method,
                path,
                body=body,
                status=404,
                expect={"detail": "Not Found"},
            )

        # D7: non-canonical integer text in path and query parameters (behavior-normalization.json divergences D7).
        for raw_id, label in (("%201", "leading space"), ("1_0", "underscore"), ("1.0", "fraction")):
            path = self.item_raw(table, parent, raw_id)
            self.refuse("D7", f"D7: {name}: get path id with {label}", "GET", path, ["path"])
            self.refuse("D7", f"D7: {name}: patch path id with {label}", "PATCH", path, ["path"], body={})
            self.refuse("D7", f"D7: {name}: delete path id with {label}", "DELETE", path, ["path"])

        if self.prof.version != "som.prompt.v1":
            for param in ("limit", "offset"):
                for bad_text in (" 1", "1_0", "1.0"):
                    self.refuse(
                        "D7",
                        f"D7: {name}: query {param}={bad_text!r}",
                        "GET",
                        coll,
                        ["query"],
                        query=[(param, bad_text)],
                    )

        # Hardening check (contracts/typescript-output-v1/scope.json reserved_names_reason and
        # product-layout.json naming.fields): Object.prototype member names such as toString are
        # admitted because generated code reads request members only after Object.hasOwn.
        field_names = {f["name"] for f in table.fields}
        has_required = any(f["required"] for f in table.fields)
        if "toString" not in field_names and has_required:
            self.refuse(
                "prototype_members",
                f"prototype_members: {name}: create with Object.prototype toString",
                "POST",
                coll,
                ["body"],
                body={"toString": "x"},
            )
        if "toString" not in field_names:
            if table.service:
                self.refuse(
                    "prototype_members",
                    f"prototype_members: {name}: patch with Object.prototype toString",
                    "PATCH",
                    item,
                    ["body"],
                    body={"toString": "x"},
                )
            else:
                self.call(
                    "prototype_members",
                    f"prototype_members: {name}: patch ignores Object.prototype toString",
                    "PATCH",
                    item,
                    body={"toString": "x"},
                    status=200,
                    expect=self.expected_row(table, token),
                    kind="row",
                    tables=(table,),
                    stamp="same",
                )

    def v5_strict(self, parent: Table, child: Table, ptoken: str, ctoken: str) -> None:
        """Check v5-specific strict refusals: parent id in child routes, with-children routes and batch bodies."""
        del ptoken
        cname = child.name
        pname = parent.name
        child_base = self.fresh(child, "required")

        # D3: out-of-range parent id in child collection and child item routes returns 404 parent not found.
        for text in ("2147483648", "-2147483649", "99999999999999999999"):
            cpath = [f"{parent.path}/{text}{child.path}"]
            cipath = [f"{parent.path}/{text}{child.path}/", (child, ctoken)]
            self.call(
                "D3",
                f"D3: {cname}: list under parent {text}",
                "GET",
                cpath,
                status=404,
                expect={"detail": parent.not_found},
            )
            self.call(
                "D3",
                f"D3: {cname}: create under parent {text}",
                "POST",
                cpath,
                body=child_base,
                status=404,
                expect={"detail": parent.not_found},
            )
            self.call(
                "D3",
                f"D3: {cname}: get under parent {text}",
                "GET",
                cipath,
                status=404,
                expect={"detail": parent.not_found},
            )
            self.call(
                "D3",
                f"D3: {cname}: patch under parent {text}",
                "PATCH",
                cipath,
                body=self.harmless(child),
                status=404,
                expect={"detail": parent.not_found},
            )
            self.call(
                "D3",
                f"D3: {cname}: delete under parent {text}",
                "DELETE",
                cipath,
                status=404,
                expect={"detail": parent.not_found},
            )

        # D7: non-canonical parent id text in child collection and child item routes returns 422 loc[0]=="path".
        for raw_id, label in (("%201", "leading space"), ("1_0", "underscore"), ("1.0", "fraction")):
            cpath = [f"{parent.path}/{raw_id}{child.path}"]
            cipath = [f"{parent.path}/{raw_id}{child.path}/", (child, ctoken)]
            self.refuse("D7", f"D7: {cname}: list with parent path id {label}", "GET", cpath, ["path"])
            self.refuse(
                "D7",
                f"D7: {cname}: create with parent path id {label}",
                "POST",
                cpath,
                ["path"],
                body=child_base,
            )
            self.refuse("D7", f"D7: {cname}: get with parent path id {label}", "GET", cipath, ["path"])
            self.refuse("D7", f"D7: {cname}: patch with parent path id {label}", "PATCH", cipath, ["path"], body={})
            self.refuse("D7", f"D7: {cname}: delete with parent path id {label}", "DELETE", cipath, ["path"])

        # v5 with-children route: GET, PATCH and DELETE on {parent_path}/with-children fail parent_id path parsing (422),
        # while a trailing slash on {parent_path}/with-children/ returns 404 Not Found (D5).
        batch_path = [parent.path + "/with-children"]
        self.refuse("D7", f"D7: {pname}: GET with-children", "GET", batch_path, ["path"])
        self.refuse("D7", f"D7: {pname}: PATCH with-children", "PATCH", batch_path, ["path"], body={})
        self.refuse("D7", f"D7: {pname}: DELETE with-children", "DELETE", batch_path, ["path"])

        valid_batch, _, _ = self.batch_body(parent, child, 1)
        for method, body in (("GET", None), ("POST", valid_batch)):
            self.call(
                "D5",
                f"D5: {pname}: {method} with-children with a trailing slash",
                method,
                [parent.path + "/with-children/"],
                body=body,
                status=404,
                expect={"detail": "Not Found"},
            )

        # D1 and D2 on the with-children batch body: a bad parent field, and a bad field inside one child.
        lax = {"int": ["1"], "float": ["1.5"], "bool": ["true", 1]}
        for field in parent.fields:
            fname, kind = field["name"], field["type"]
            for value in lax.get(kind, []):
                label = json.dumps(value)
                body, top, _ = self.batch_body(parent, child, 1)
                top[fname] = value
                self.refuse(
                    "D1",
                    f"D1: batch parent {fname} as {label}",
                    "POST",
                    batch_path,
                    ["body"],
                    body=body,
                )
            if kind == "str":
                for bad, label in (("é" * 256, "256 characters"), (non_bmp_256(1), "256 non-BMP code points")):
                    body, top, _ = self.batch_body(parent, child, 1)
                    top[fname] = bad
                    self.refuse(
                        "D2",
                        f"D2: batch parent {fname} {label}",
                        "POST",
                        batch_path,
                        ["body"],
                        body=body,
                    )
            elif kind == "int":
                for bad in (2147483648, -2147483649):
                    body, top, _ = self.batch_body(parent, child, 1)
                    top[fname] = bad
                    self.refuse(
                        "D2",
                        f"D2: batch parent {fname} {bad}",
                        "POST",
                        batch_path,
                        ["body"],
                        body=body,
                    )

        for field in child.fields:
            fname, kind = field["name"], field["type"]
            for value in lax.get(kind, []):
                label = json.dumps(value)
                body, _, kids = self.batch_body(parent, child, 2)
                kids[1][fname] = value
                self.refuse(
                    "D1",
                    f"D1: batch child {fname} as {label}",
                    "POST",
                    batch_path,
                    ["body"],
                    body=body,
                )
            if kind == "str":
                for bad, label in (("é" * 256, "256 characters"), (non_bmp_256(1), "256 non-BMP code points")):
                    body, _, kids = self.batch_body(parent, child, 2)
                    kids[1][fname] = bad
                    self.refuse(
                        "D2",
                        f"D2: batch child {fname} {label}",
                        "POST",
                        batch_path,
                        ["body"],
                        body=body,
                    )
            elif kind == "int":
                for bad in (2147483648, -2147483649):
                    body, _, kids = self.batch_body(parent, child, 2)
                    kids[1][fname] = bad
                    self.refuse(
                        "D2",
                        f"D2: batch child {fname} {bad}",
                        "POST",
                        batch_path,
                        ["body"],
                        body=body,
                    )

        # Hardening check (scope.json reserved_names_reason) on the batch wrapper, parent and child objects.
        body_wrap, _, _ = self.batch_body(parent, child, 1)
        self.refuse(
            "prototype_members",
            "prototype_members: batch wrapper with Object.prototype toString",
            "POST",
            batch_path,
            ["body"],
            body={**body_wrap, "toString": "x"},
        )
        if "toString" not in {f["name"] for f in parent.fields}:
            body_parent_proto, top_proto, _ = self.batch_body(parent, child, 1)
            top_proto["toString"] = "x"
            self.refuse(
                "prototype_members",
                "prototype_members: batch parent with Object.prototype toString",
                "POST",
                batch_path,
                ["body"],
                body=body_parent_proto,
            )
        if "toString" not in {f["name"] for f in child.fields}:
            body_child_proto, _, kids_proto = self.batch_body(parent, child, 1)
            kids_proto[0]["toString"] = "x"
            self.refuse(
                "prototype_members",
                "prototype_members: batch child with Object.prototype toString",
                "POST",
                batch_path,
                ["body"],
                body=body_child_proto,
            )

    def strict(self) -> None:
        """Check divergences D1 to D5, D7 and prototype_members on a TypeScript product alone."""
        if self.prof.v5:
            parent, child = self.prof.tables
            ptoken = self._ensure_live_token(parent, None)
            ctoken = self._ensure_live_token(child, ptoken)
            self.table_strict(parent, None, ptoken)
            self.table_strict(child, ptoken, ctoken)
            self.v5_strict(parent, child, ptoken, ctoken)
            self.get(parent, None, ptoken, "D2", f"{parent.name}: D1 to D7 changed no row")
            self.listing(parent, None, self.top(parent), "D2", f"{parent.name}: D1 to D7 added no row")
            self.get(child, ptoken, ctoken, "D2", f"{child.name}: D1 to D7 changed no row")
            self.listing(child, ptoken, self.top(child), "D2", f"{child.name}: D1 to D7 added no row")
        else:
            table = self.prof.tables[0]
            token = self._ensure_live_token(table, None)
            self.table_strict(table, None, token)
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
