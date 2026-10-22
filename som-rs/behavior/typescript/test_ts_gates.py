"""Unit tests for TypeScript phase 1 behavior suite, host rules and product gates (issue #76).

Runs without PostgreSQL or Node:
  som-rs/runtime/acceptance-m3-venv/bin/python -m pytest -q som-rs/behavior/typescript/test_ts_gates.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.parse
from typing import Any

import pytest

HERE = pathlib.Path(__file__).resolve().parent
BEHAVIOR_DIR = HERE.parent
SOM_RS = BEHAVIOR_DIR.parent
QA_DIR = SOM_RS / "som-targets" / "qa"
EXAMPLES_DIR = SOM_RS / "contracts" / "compiler-v1" / "examples"

for _p in (str(HERE), str(BEHAVIOR_DIR), str(QA_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import check_ts_products as gates_mod  # noqa: E402
from typescript import host as ts_host  # noqa: E402
from typescript import suite as ts_suite  # noqa: E402


# ---------------------------------------------------------------- in-memory product simulator


class InMemoryProduct:
    """In-memory HTTP simulator for v1-v4 prompts to test TsRun parity and strict checks without DB/Node."""

    def __init__(
        self,
        prompt: dict[str, Any],
        *,
        allow_lax_int_string: bool = False,
        use_utf16_length: bool = False,
        allow_prototype_keys: bool = False,
        allow_non_canonical_path_id: bool = False,
        allow_repeated_query: bool = False,
        redirect_trailing_slash: bool = False,
        patch_required_null_409_in_v3: bool = False,
    ) -> None:
        self.prof = ts_suite.profile(prompt)
        self.table = self.prof.tables[0]
        self.rows: dict[int, dict[str, Any]] = {}
        self.next_id = 1
        self.clock = 100
        self.allow_lax_int_string = allow_lax_int_string
        self.use_utf16_length = use_utf16_length
        self.allow_prototype_keys = allow_prototype_keys
        self.allow_non_canonical_path_id = allow_non_canonical_path_id
        self.allow_repeated_query = allow_repeated_query
        self.redirect_trailing_slash = redirect_trailing_slash
        self.patch_required_null_409_in_v3 = patch_required_null_409_in_v3

    def _ts(self) -> str:
        ms = self.clock % 1000
        sec = (self.clock // 1000) % 60
        return f"2026-10-21T12:00:{sec:02d}.{ms:03d}Z"

    def _err422(self, loc0: str, loc1: str | int = "x") -> tuple[int, str, bytes]:
        body = {"detail": [{"type": "invalid", "loc": [loc0, loc1], "msg": "invalid"}]}
        return 422, "application/json", json.dumps(body).encode()

    def _json(self, status: int, body: Any) -> tuple[int, str | None, bytes]:
        if status == 204:
            media = None if self.prof.v5 else "application/json"
            return 204, media, b""
        return status, "application/json", json.dumps(body).encode()

    def _validate_field(self, field: dict[str, Any], val: Any) -> tuple[bool, Any]:
        kind = field["type"]
        if kind == "str":
            if not isinstance(val, str):
                return False, None
            length = len(val.encode("utf-16-le")) // 2 if self.use_utf16_length else len(val)
            if length > 255:
                return False, None
            return True, val
        if kind == "int":
            if isinstance(val, bool):
                return False, None
            if self.allow_lax_int_string and isinstance(val, str) and val.isdigit():
                val = int(val)
            if isinstance(val, float) and val.is_integer():
                val = int(val)
            if not isinstance(val, int) or val < -2147483648 or val > 2147483647:
                return False, None
            return True, val
        if kind == "float":
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                return False, None
            return True, float(val)
        if kind == "bool":
            if not isinstance(val, bool):
                return False, None
            return True, val
        if kind == "date":
            if not isinstance(val, str) or len(val) != 10:
                return False, None
            try:
                y, m, d = map(int, val.split("-"))
                import datetime as dt

                dt.date(y, m, d)
            except Exception:  # noqa: BLE001
                return False, None
            return True, val
        return False, None

    def send(self, method: str, path: str, body: bytes | None, content_type: str = "application/json") -> tuple[int, str | None, bytes]:
        parsed = urllib.parse.urlsplit(path)
        route = parsed.path
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        coll_path = self.table.path

        if route != "/" and route.endswith("/"):
            if self.redirect_trailing_slash:
                return self._json(307, {"detail": "Redirect"})
            return self._json(404, {"detail": "Not Found"})

        if route == "/healthz":
            if method == "GET":
                return self._json(200, {"status": "ok"})
            return self._json(405, {"detail": "Method Not Allowed"})

        if route == coll_path:
            if method == "GET":
                return self._handle_list(query_pairs)
            if method == "POST":
                return self._handle_create(body, content_type)
            return self._json(405, {"detail": "Method Not Allowed"})

        prefix = coll_path + "/"
        if route.startswith(prefix) and "/" not in route[len(prefix):]:
            raw_id = urllib.parse.unquote(route[len(prefix):])
            if method not in ("GET", "PATCH", "DELETE"):
                return self._json(405, {"detail": "Method Not Allowed"})
            return self._handle_item(method, raw_id, body, content_type)

        return self._json(404, {"detail": "Not Found"})

    def _parse_id(self, raw_id: str) -> tuple[str, int | None]:
        if self.allow_non_canonical_path_id:
            try:
                val = int(raw_id.strip())
            except ValueError:
                return "invalid", None
        else:
            import re

            if not re.match(r"^[+-]?[0-9]+$", raw_id):
                return "invalid", None
            val = int(raw_id)
        if val < -2147483648 or val > 2147483647:
            return "out_of_range", None
        return "ok", val

    def _parse_body(self, body: bytes | None, content_type: str) -> tuple[bool, dict[str, Any]]:
        if body is None or len(body) == 0:
            return False, {}
        media = content_type.split(";")[0].strip().lower()
        if media != "application/json" and not (media.startswith("application/") and media.endswith("+json")):
            return False, {}
        try:
            # Parse floats with zero fraction (like 1.0) into float as Python json.loads does
            val = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return False, {}
        if not isinstance(val, dict):
            return False, {}
        return True, val

    def _handle_create(self, body: bytes | None, content_type: str) -> tuple[int, str | None, bytes]:
        ok, data = self._parse_body(body, content_type)
        if not ok:
            return self._err422("body")
        field_map = {f["name"]: f for f in self.table.fields}
        allowed_extra = {"toString"} if self.allow_prototype_keys else set()
        if self.table.service and any(k not in field_map and k not in allowed_extra for k in data):
            return self._err422("body")

        row_vals: dict[str, Any] = {}
        for f in self.table.fields:
            fname = f["name"]
            present = fname in data or (self.allow_prototype_keys and "toString" in data)
            if not present:
                if f["required"]:
                    return self._err422("body", fname)
                row_vals[fname] = self.table.default(f)
                continue
            raw_val = data.get(fname, data.get("toString") if self.allow_prototype_keys else None)
            if raw_val is None:
                if self.table.nullable(f):
                    row_vals[fname] = None
                    continue
                return self._err422("body", fname)
            valid, cleaned = self._validate_field(f, raw_val)
            if not valid:
                return self._err422("body", fname)
            row_vals[fname] = cleaned

        for f in self.table.fields:
            if f["unique"] and row_vals[f["name"]] is not None:
                if any(r[f["name"]] == row_vals[f["name"]] for r in self.rows.values()):
                    return self._json(409, {"detail": self.table.conflict})

        rid = self.next_id
        self.next_id += 1
        self.clock += 1
        now = self._ts()
        stored = {"id": rid, **row_vals}
        if self.table.service:
            stored["created_at"] = now
            stored["updated_at"] = now
        self.rows[rid] = stored
        return self._json(201, dict(stored))

    def _handle_item(
        self,
        method: str,
        raw_id: str,
        body: bytes | None,
        content_type: str,
    ) -> tuple[int, str | None, bytes]:
        id_state, item_id = self._parse_id(raw_id)
        if method == "PATCH":
            ok_body, data = self._parse_body(body, content_type)
            if not ok_body:
                return self._err422("body")
            field_map = {f["name"]: f for f in self.table.fields}
            allowed_extra = {"toString"} if self.allow_prototype_keys else set()
            if self.table.service and any(k not in field_map and k not in allowed_extra for k in data):
                return self._err422("body")
            updates: dict[str, Any] = {}
            for k, v in data.items():
                if k not in field_map:
                    if self.allow_prototype_keys and k == "toString":
                        first_f = self.table.fields[0]["name"]
                        updates[first_f] = v
                    continue
                f = field_map[k]
                if v is None:
                    if self.table.nullable(f):
                        updates[k] = None
                    elif self.table.service and not self.patch_required_null_409_in_v3:
                        return self._err422("body", k)
                    else:
                        updates[k] = "__NOT_NULL_CONFLICT__"
                else:
                    valid, cleaned = self._validate_field(f, v)
                    if not valid:
                        return self._err422("body", k)
                    updates[k] = cleaned
            if id_state == "invalid":
                return self._err422("path", "id")
            if id_state == "out_of_range" or item_id not in self.rows:
                return self._json(404, {"detail": self.table.not_found})
            if "__NOT_NULL_CONFLICT__" in updates.values():
                return self._json(409, {"detail": self.table.conflict})
            for f in self.table.fields:
                fname = f["name"]
                if f["unique"] and fname in updates and updates[fname] is not None:
                    if any(oid != item_id and r[fname] == updates[fname] for oid, r in self.rows.items()):
                        return self._json(409, {"detail": self.table.conflict})
            row = self.rows[item_id]
            if updates:
                row.update(updates)
                if self.table.service:
                    self.clock += 1
                    row["updated_at"] = self._ts()
            return self._json(200, dict(row))

        if id_state == "invalid":
            return self._err422("path", "id")
        if id_state == "out_of_range" or item_id not in self.rows:
            return self._json(404, {"detail": self.table.not_found})
        if method == "GET":
            return self._json(200, dict(self.rows[item_id]))
        del self.rows[item_id]
        return self._json(204, None)

    def _handle_list(self, query_pairs: list[tuple[str, str]]) -> tuple[int, str | None, bytes]:
        if self.prof.version == "som.prompt.v1":
            return self._json(200, [dict(self.rows[k]) for k in sorted(self.rows)])
        seen: set[str] = set()
        params: dict[str, str] = {}
        for k, v in query_pairs:
            if k in seen and not self.allow_repeated_query:
                return self._err422("query", k)
            seen.add(k)
            params[k] = v

        import re

        q = self.table.query
        limit = 20
        if "limit" in params:
            if not re.match(r"^[+-]?[0-9]+$", params["limit"]):
                return self._err422("query", "limit")
            limit = int(params["limit"])
            if limit < 1 or limit > q["max_limit"]:
                return self._err422("query", "limit")
        offset = 0
        if "offset" in params:
            if not re.match(r"^[+-]?[0-9]+$", params["offset"]):
                return self._err422("query", "offset")
            offset = int(params["offset"])
            if offset < 0 or offset > 9223372036854775807:
                return self._err422("query", "offset")

        items = [dict(self.rows[k]) for k in sorted(self.rows)]
        if self.prof.version == "som.prompt.v2" and "filter_value" in params:
            ff = q["filter_field"]
            items = [r for r in items if r[ff] == params["filter_value"]]
        elif self.prof.version != "som.prompt.v2" and q.get("filter_field") and q["filter_field"] in params:
            ff = q["filter_field"]
            raw_b = params[ff].lower()
            if raw_b in ("1", "on", "t", "true", "y", "yes"):
                wanted = True
            elif raw_b in ("0", "off", "f", "false", "n", "no"):
                wanted = False
            else:
                return self._err422("query", ff)
            items = [r for r in items if r[ff] is wanted]

        if q.get("date_field") and "before" in params:
            bf = params["before"]
            ok_d, _ = self._validate_field({"type": "date"}, bf)
            if not ok_d:
                return self._err422("query", "before")
            df = q["date_field"]
            items = [r for r in items if r[df] is not None and r[df] < bf]

        return self._json(200, items[offset : offset + limit])


# ---------------------------------------------------------------- tests


def RichV4Prompt() -> dict[str, Any]:
    """A v4 prompt with str, int, float, bool and date fields to exercise all added_inputs and D1-D7."""
    return {
        "version": "som.prompt.v4",
        "package": "catalog_app",
        "migration": {"revision": "rev0004", "down_revision": None},
        "query": {
            "max_limit": 50,
            "filter_field": "active",
            "date_field": "due_date",
        },
        "resource": {
            "name": "Item",
            "table": "items",
            "path": "/items",
            "fields": [
                {"name": "title", "type": "str", "required": True, "unique": True},
                {"name": "quantity", "type": "int", "required": True, "unique": False},
                {"name": "price", "type": "float", "required": False, "unique": False, "default": 1.5},
                {"name": "active", "type": "bool", "required": False, "unique": False, "default": True},
                {"name": "due_date", "type": "date", "required": False, "unique": False, "default": None},
            ],
        },
    }


def test_non_bmp_code_point_helpers() -> None:
    s255 = ts_suite.non_bmp_255(1)
    assert len(s255) == 255
    assert len(s255.encode("utf-16-le")) // 2 == 256
    assert any(ord(ch) > 0xFFFF for ch in s255)

    s256 = ts_suite.non_bmp_256(1)
    assert len(s256) == 256
    assert len(s256.encode("utf-16-le")) // 2 == 257


def test_parity_and_added_inputs_on_compliant_product() -> None:
    prompt = RichV4Prompt()
    py_sim = InMemoryProduct(prompt)
    ts_sim = InMemoryProduct(prompt)

    py_run = ts_suite.run(prompt, "http://127.0.0.1:18999", send=py_sim.send)
    ts_run = ts_suite.run(prompt, "http://127.0.0.1:18999", send=ts_sim.send)

    assert py_run.failures == []
    assert ts_run.failures == []
    diffs = ts_suite.compare(py_run.transcript, ts_run.transcript)
    assert diffs == []

    # Verify the 3 added_inputs for v1-v4 were actually sent on the wire:
    steps = {entry["step"]: entry for entry in ts_run.transcript}
    int_step = steps["items: create quantity with JSON 1.0"]
    assert '"quantity": 1.0' in int_step["request"]["body"]
    assert int_step["body"]["quantity"] == 1
    assert isinstance(int_step["body"]["quantity"], int)

    float_step = next(e for s, e in steps.items() if s.startswith("items: create price with JSON integer "))
    assert re.search(r'"price": \d+\b(?!\.)', float_step["request"]["body"])
    assert isinstance(float_step["body"]["price"], float)

    str_step = steps["items: create title with 255 non-BMP code points"]
    sent_title = json.loads(str_step["request"]["body"])["title"]
    assert len(sent_title) == 255
    assert len(sent_title.encode("utf-16-le")) // 2 == 256
    assert str_step["status"] == 201


def test_parity_excludes_divergences_and_strict_checks_them() -> None:
    prompt = RichV4Prompt()
    sim = InMemoryProduct(prompt)
    run = ts_suite.run(prompt, "http://127.0.0.1:18999", send=sim.send)
    parity_categories = {e["category"] for e in run.transcript}
    assert parity_categories.isdisjoint({"D1", "D2", "D3", "D4", "D5", "D7"})

    before_steps = len(run.transcript)
    run.strict()
    strict_entries = run.transcript[before_steps:]
    strict_cats = {e["category"] for e in strict_entries}
    assert {"D1", "D2", "D3", "D4", "D5", "D7", "prototype_members", "strict_no_row_change"} <= strict_cats
    assert run.failures == []

    # Verify evaluate_strict_refusals reports prototype_members and strict_no_row_change apart from D1-D5 and D7:
    gate = gates_mod.evaluate_strict_refusals(strict_entries, run.failures, 10, ["node", "src/main.ts"])
    assert gate["passed"] is True
    assert set(gate["by_divergence"]) == {"D1", "D2", "D3", "D4", "D5", "D7"}
    assert "prototype_members" not in gate["by_divergence"]
    assert "strict_no_row_change" not in gate["by_divergence"]
    assert gate["prototype_members"] >= 2
    assert gate["no_row_change_checks"] == 2
    assert (
        sum(gate["by_divergence"].values())
        + gate["prototype_members"]
        + gate["no_row_change_checks"]
        == gate["strict_steps"]
    )


def test_schema_without_str_or_int_has_no_d2_entries_and_counts_strict_no_row_change() -> None:
    prompt = {
        "version": "som.prompt.v4",
        "package": "metrics_app",
        "migration": {"revision": "rev0005", "down_revision": None},
        "query": {
            "max_limit": 50,
            "filter_field": "active",
            "date_field": "recorded_on",
        },
        "resource": {
            "name": "Metric",
            "table": "metrics",
            "path": "/metrics",
            "fields": [
                {"name": "score", "type": "float", "required": True, "unique": False},
                {"name": "active", "type": "bool", "required": False, "unique": False, "default": True},
                {"name": "recorded_on", "type": "date", "required": False, "unique": False, "default": None},
            ],
        },
    }
    sim = InMemoryProduct(prompt)
    run = ts_suite.run(prompt, "http://127.0.0.1:18999", send=sim.send)
    before = len(run.transcript)
    run.strict()
    strict_entries = run.transcript[before:]
    assert run.failures == []

    d2_entries = [e for e in strict_entries if e["category"] == "D2"]
    assert d2_entries == []
    no_row_entries = [e for e in strict_entries if e["category"] == "strict_no_row_change"]
    assert [e["step"] for e in no_row_entries] == [
        "D1 to D7 changed no row",
        "D1 to D7 added no row",
    ]

    gate = gates_mod.evaluate_strict_refusals(strict_entries, run.failures, 10, ["node", "src/main.ts"])
    assert gate["passed"] is True
    assert "D2" not in gate["by_divergence"]
    assert "strict_no_row_change" not in gate["by_divergence"]
    assert gate["no_row_change_checks"] == 2
    assert (
        sum(gate["by_divergence"].values())
        + gate["prototype_members"]
        + gate["no_row_change_checks"]
        == gate["strict_steps"]
    )

    # Also verify strict_no_row_change entries participate in failure and 500 checks:
    bad_500 = [
        *strict_entries[:-1],
        {**strict_entries[-1], "status": 500},
    ]
    gate_500 = gates_mod.evaluate_strict_refusals(bad_500, [], 10, ["node", "src/main.ts"])
    assert gate_500["passed"] is False
    assert "500" in gate_500["reason"]
    assert gate_500["no_row_change_checks"] == 2

    gate_fail = gates_mod.evaluate_strict_refusals(
        strict_entries,
        [{"step": "D1 to D7 changed no row", "category": "strict_no_row_change", "problems": ["row changed"]}],
        10,
        ["node", "src/main.ts"],
    )
    assert gate_fail["passed"] is False
    assert "D1 to D7 changed no row" in gate_fail["reason"]
    assert gate_fail["no_row_change_checks"] == 2


def test_strict_detects_each_divergence_defect() -> None:
    prompt = RichV4Prompt()

    # 1. Defect in D1: accepting "1" for int field
    sim_d1 = InMemoryProduct(prompt, allow_lax_int_string=True)
    run_d1 = ts_suite.run(prompt, "http://127.0.0.1:18999", send=sim_d1.send)
    run_d1.strict()
    assert any(f["category"] == "D1" for f in run_d1.failures)

    # 2. Defect in added_inputs / D2: checking UTF-16 .length > 255 instead of code points
    sim_utf16 = InMemoryProduct(prompt, use_utf16_length=True)
    run_utf16 = ts_suite.run(prompt, "http://127.0.0.1:18999", send=sim_utf16.send)
    assert any("255 non-BMP code points" in f["step"] for f in run_utf16.failures)

    # 3. Defect in D4: accepting repeated query parameters
    sim_d4 = InMemoryProduct(prompt, allow_repeated_query=True)
    run_d4 = ts_suite.run(prompt, "http://127.0.0.1:18999", send=sim_d4.send)
    run_d4.strict()
    assert any(f["category"] == "D4" for f in run_d4.failures)

    # 4. Defect in D5: 307 redirect on trailing slash
    sim_d5 = InMemoryProduct(prompt, redirect_trailing_slash=True)
    run_d5 = ts_suite.run(prompt, "http://127.0.0.1:18999", send=sim_d5.send)
    run_d5.strict()
    assert any(f["category"] == "D5" for f in run_d5.failures)

    # 5. Defect in D7 and prototype_members: accepting non-canonical path id or Object.prototype toString
    sim_d7 = InMemoryProduct(prompt, allow_non_canonical_path_id=True, allow_prototype_keys=True)
    run_d7 = ts_suite.run(prompt, "http://127.0.0.1:18999", send=sim_d7.send)
    run_d7.strict()
    d7_steps = [f["step"] for f in run_d7.failures if f["category"] == "D7"]
    proto_steps = [f["step"] for f in run_d7.failures if f["category"] == "prototype_members"]
    assert any("path id" in s for s in d7_steps)
    assert any("Object.prototype toString" in s for s in proto_steps)


def test_compare_flags_500_as_parity_failure() -> None:
    step = {
        "step": "items: create",
        "category": "crud",
        "request": {"method": "POST", "path": "/items"},
        "status": 500,
        "media": "application/json",
        "body": {"detail": "Internal Server Error"},
    }
    diffs = ts_suite.compare([step], [step])
    assert len(diffs) == 1
    assert "500" in diffs[0]["problem"]


def test_schema_equivalence_judgment() -> None:
    base_schema = {
        "items": {
            "columns": [
                ["id", "integer", None, "NO", "nextval('items_id_seq'::regclass)"],
                ["title", "character varying", 255, "NO", None],
            ],
            "primary_key": [["id"]],
            "unique": [["title"]],
            "foreign_keys": [],
            "indexes": [[["id"], True], [["title"], True]],
        }
    }
    passed = gates_mod.evaluate_schema_equivalence(base_schema, dict(base_schema), ["node"], 12)
    assert passed["passed"] is True
    assert passed["status"] == "passed"

    altered = {
        "items": {
            **base_schema["items"],
            "unique": [],
        }
    }
    failed = gates_mod.evaluate_schema_equivalence(altered, base_schema, ["node"], 12)
    assert failed["passed"] is False
    assert failed["status"] == "failed"
    assert failed["differences"] == [{"table": "items", "part": "unique", "typescript": [], "python": [["title"]]}]


def test_startup_gate_judgment() -> None:
    cmd = ["/opt/homebrew/Cellar/node/26.5.0/bin/node", "src/main.ts"]
    good = gates_mod.evaluate_startup(
        {"exit_code": 2, "stderr": "DATABASE_URL is required\n"},
        {"exit_code": 2, "stderr": "SOM_APP_ADDR must be host:port\n"},
        {"ready": True, "cwd_verified": True, "exit_code": 0},
        cmd,
        50,
    )
    assert good["passed"] is True
    assert good["status"] == "passed"

    # Multi-line stderr on missing DATABASE_URL fails startup contract
    bad_multiline = gates_mod.evaluate_startup(
        {"exit_code": 2, "stderr": "line 1\nline 2\n"},
        {"exit_code": 2, "stderr": "SOM_APP_ADDR must be host:port\n"},
        {"ready": True, "cwd_verified": True, "exit_code": 0},
        cmd,
        50,
    )
    assert bad_multiline["passed"] is False
    assert "missing DATABASE_URL" in bad_multiline["reason"]

    # Non-zero exit on SIGTERM fails startup contract
    bad_sigterm = gates_mod.evaluate_startup(
        {"exit_code": 2, "stderr": "DATABASE_URL is required\n"},
        {"exit_code": 2, "stderr": "SOM_APP_ADDR must be host:port\n"},
        {"ready": True, "cwd_verified": True, "exit_code": 143},
        cmd,
        50,
    )
    assert bad_sigterm["passed"] is False
    assert "SIGTERM" in bad_sigterm["reason"]


def test_test_gate_and_lock_unchanged_judgment() -> None:
    spec_out = "ℹ tests 8\nℹ suites 0\nℹ pass 8\nℹ fail 0\nℹ cancelled 0\nℹ skipped 0\nℹ todo 0\n"
    good_test = gates_mod.evaluate_test_gate(
        {"command": ["node", "--test"], "exit_code": 0, "stdout": spec_out, "stderr": "", "duration_ms": 100},
        {"command": ["node", "--test"], "exit_code": 1, "stdout": "", "stderr": "AssertionError\n", "duration_ms": 20},
    )
    assert good_test["passed"] is True

    # If omitting TEST_DATABASE_URL exits 0 (e.g. skipped instead of failing), test gate must fail
    bad_test = gates_mod.evaluate_test_gate(
        {"command": ["node", "--test"], "exit_code": 0, "stdout": spec_out, "stderr": "", "duration_ms": 100},
        {"command": ["node", "--test"], "exit_code": 0, "stdout": "ℹ skipped 8\n", "stderr": "", "duration_ms": 20},
    )
    assert bad_test["passed"] is False
    assert "omitting TEST_DATABASE_URL" in bad_test["reason"]

    before = {**gates_mod.REFERENCE_FILE_HASHES, "src/main.ts": "aaa"}
    assert gates_mod.evaluate_lock_unchanged(before, dict(before))["passed"] is True

    after_added = {**before, "extra.txt": "bbb"}
    lock_fail = gates_mod.evaluate_lock_unchanged(before, after_added)
    assert lock_fail["passed"] is False
    assert "extra.txt" in lock_fail["reason"]


def test_report_format_and_no_sampling_enforcement() -> None:
    all_ok_gates = {
        name: gates_mod.make_gate("passed", command=["cmd", name], exit_code=0, duration_ms=10)
        for name in gates_mod.GATES_ORDER
    }
    row_ok = gates_mod.evaluate_product_row("example-v1", "som.prompt.v1", "0" * 64, all_ok_gates)
    assert row_ok["passed"] is True
    assert len(row_ok["passed_gates"]) == 9
    assert row_ok["failed_gates"] == []
    assert row_ok["not_run_gates"] == []

    report_ok, code_ok = gates_mod.build_report([row_ok], {}, {}, [], 0.0)
    assert code_ok == 0
    assert report_ok["summary"]["all_passed"] is True
    assert report_ok["summary"]["no_sampling_complete"] is True

    # Missing a gate is recorded as not_run and forces a non-zero exit code
    incomplete_gates = {k: v for k, v in all_ok_gates.items() if k != "startup"}
    row_incomplete = gates_mod.evaluate_product_row("example-v1", "som.prompt.v1", "0" * 64, incomplete_gates)
    assert row_incomplete["passed"] is False
    assert row_incomplete["not_run_gates"] == ["startup"]
    assert row_incomplete["gates"]["startup"]["status"] == "not_run"

    report_inc, code_inc = gates_mod.build_report([row_incomplete], {}, {}, [], 0.0)
    assert code_inc == 2
    assert report_inc["summary"]["all_passed"] is False
    assert report_inc["summary"]["no_sampling_complete"] is False


def test_forbidden_ports_and_pids_rejected() -> None:
    for bad_port in (8765, 18765):
        with pytest.raises(RuntimeError):
            ts_host.check_port_allowed(bad_port)
        with pytest.raises(RuntimeError):
            ts_host.check_url_allowed(f"http://127.0.0.1:{bad_port}")

    assert 61463 in ts_host.forbidden_pids()
    ok, cwd = ts_host.verify_process_cwd(61463, pathlib.Path("/tmp"))
    assert ok is False
    assert cwd is None


def test_timestamp_normalization_and_monotonicity() -> None:
    prompt = RichV4Prompt()
    prof = ts_suite.profile(prompt)
    table = prof.tables[0]
    norm = ts_suite.Normalizer()

    row1 = {
        "id": 42,
        "title": "alpha",
        "quantity": 10,
        "price": 1,
        "active": True,
        "due_date": None,
        "created_at": "2026-10-21T10:00:00.123Z",
        "updated_at": "2026-10-21T10:00:00.123Z",
    }
    res1 = norm.response(201, "application/json", json.dumps(row1).encode(), "row", (table,), "create", None)
    assert res1["id"] == "id#1"
    assert res1["price"] == 1.0
    assert isinstance(res1["price"], float)
    assert res1["created_at"] == "<timestamp>"
    assert res1["updated_at"] == "<timestamp>"
    assert norm.problems == []

    # Advancing updated_at with Python microsecond offset format succeeds
    row2 = {
        **row1,
        "updated_at": "2026-10-21T10:00:01.000500+00:00",
    }
    norm.response(200, "application/json", json.dumps(row2).encode(), "row", (table,), "patch", "advance")
    assert norm.problems == []

    # Decreasing updated_at is flagged
    row3 = {
        **row1,
        "updated_at": "2026-10-21T10:00:00.500Z",
    }
    norm.response(200, "application/json", json.dumps(row3).encode(), "row", (table,), "patch-backwards", "advance")
    assert any("updated_at" in p for p in norm.problems)


def test_v1_prototype_pollution_on_patch_caught_when_mutating_row() -> None:
    v1_prompt = json.loads((EXAMPLES_DIR / "v1.prompt.json").read_text())
    sim_clean = InMemoryProduct(v1_prompt)
    run_clean = ts_suite.run(v1_prompt, "http://127.0.0.1:18999", send=sim_clean.send)
    run_clean.strict()
    assert run_clean.failures == []

    sim_buggy = InMemoryProduct(v1_prompt, allow_prototype_keys=True)
    run_buggy = ts_suite.run(v1_prompt, "http://127.0.0.1:18999", send=sim_buggy.send)
    run_buggy.strict()
    proto_failures = [f for f in run_buggy.failures if f["category"] == "prototype_members"]
    assert len(proto_failures) >= 2


def test_v5_strict_covers_child_routes_parent_id_batch_and_with_children() -> None:
    v5_prompt = json.loads((EXAMPLES_DIR / "v5.prompt.json").read_text())

    # 0. Verify that script() on a v5 profile sends no GET, PATCH or DELETE to {parent_path}/with-children:
    script_sent: list[tuple[str, str]] = []

    def fake_script_send(
        method: str,
        path: str,
        body: bytes | None,
        content_type: str = "application/json",
    ) -> tuple[int, str | None, bytes]:
        del body, content_type
        script_sent.append((method, path))
        return 200, "application/json", b'{"id": 1}'

    script_run = ts_suite.TsRun(ts_suite.profile(v5_prompt), fake_script_send)
    script_run.script()
    with_children_methods = {m for m, p in script_sent if p.endswith("/with-children")}
    assert with_children_methods == {"POST"}
    assert ("GET", "/projects/with-children") not in script_sent
    assert ("PATCH", "/projects/with-children") not in script_sent
    assert ("DELETE", "/projects/with-children") not in script_sent

    # 1. Build a TsRun for v5.prompt.json against a fake client, call strict(), and verify:
    # - _ensure_live_token creates the parent and child rows through POST requests;
    # - child collection and item routes, out-of-range/non-canonical parent & child ids,
    #   trailing slashes on child & with-children routes, GET/PATCH/DELETE /projects/with-children,
    #   and batch D1/D2 checks all run.
    sent: list[tuple[str, str, bytes | None]] = []
    seeded_paths: set[str] = set()

    def fake_send(method: str, path: str, body: bytes | None, content_type: str = "application/json") -> tuple[int, str | None, bytes]:
        del content_type
        sent.append((method, path, body))
        if method == "POST" and path in ("/projects", "/projects/1/todos") and path not in seeded_paths:
            seeded_paths.add(path)
            return 201, "application/json", b'{"id":1,"created_at":"2026-10-21T12:00:00.000Z","updated_at":"2026-10-21T12:00:00.000Z"}'
        return 422, "application/json", b'{"detail":[{"type":"invalid","loc":["body","x"],"msg":"err"}]}'

    run = ts_suite.TsRun(ts_suite.profile(v5_prompt), fake_send)
    run.strict()

    paths = [p for _, p, _ in sent]
    methods_and_paths = {(m, p) for m, p, _ in sent}

    # Verify _ensure_live_token seeded parent and child via POST requests:
    assert sent[0][:2] == ("POST", "/projects")
    assert sent[1][:2] == ("POST", "/projects/1/todos")

    # Child collection and item routes with a live parent token:
    assert any(p.startswith("/projects/1/todos") for p in paths)
    assert ("POST", "/projects/1/todos") in methods_and_paths
    assert ("PATCH", "/projects/1/todos/1") in methods_and_paths

    # D3: out-of-range parent id (2147483648) and out-of-range child id (2147483648) on child routes:
    d3_entries = [e for e in run.transcript if e["category"] == "D3"]
    d3_paths = {e["request"]["path"] for e in d3_entries}
    assert "/projects/2147483648/todos" in d3_paths
    assert "/projects/2147483648/todos/{id#1}" in d3_paths
    assert "/projects/{id#1}/todos/2147483648" in d3_paths
    assert ("GET", "/projects/2147483648/todos") in methods_and_paths
    assert ("POST", "/projects/2147483648/todos") in methods_and_paths
    assert ("GET", "/projects/2147483648/todos/1") in methods_and_paths
    assert ("PATCH", "/projects/2147483648/todos/1") in methods_and_paths
    assert ("DELETE", "/projects/2147483648/todos/1") in methods_and_paths
    assert ("GET", "/projects/1/todos/2147483648") in methods_and_paths
    assert ("PATCH", "/projects/1/todos/2147483648") in methods_and_paths
    assert ("DELETE", "/projects/1/todos/2147483648") in methods_and_paths

    # D7: non-canonical integer text in parent id position and child id position of child routes,
    # plus child query parameters and GET/PATCH/DELETE on /projects/with-children:
    d7_entries = [e for e in run.transcript if e["category"] == "D7"]
    d7_paths = {e["request"]["path"] for e in d7_entries}
    assert "/projects/%201/todos" in d7_paths
    assert "/projects/1_0/todos" in d7_paths
    assert "/projects/1.0/todos" in d7_paths
    assert "/projects/%201/todos/{id#1}" in d7_paths
    assert "/projects/{id#1}/todos/%201" in d7_paths
    assert "/projects/{id#1}/todos/1_0" in d7_paths
    assert "/projects/{id#1}/todos/1.0" in d7_paths
    assert "/projects/with-children" in d7_paths
    assert ("GET", "/projects/with-children") in methods_and_paths
    assert ("PATCH", "/projects/with-children") in methods_and_paths
    assert ("DELETE", "/projects/with-children") in methods_and_paths
    assert any(p.startswith("/projects/1/todos?limit=") for p in paths)

    # D4: repeated query parameters on child route (including filter_field completed and before):
    d4_entries = [e for e in run.transcript if e["category"] == "D4"]
    d4_paths = {e["request"]["path"] for e in d4_entries}
    assert any(p.startswith("/projects/{id#1}/todos?") and "completed=true&completed=true" in p for p in d4_paths)
    assert any(p.startswith("/projects/{id#1}/todos?") and "before=2026-06-01&before=2026-06-01" in p for p in d4_paths)

    # D5: trailing slash on child collection, child item and with-children routes:
    d5_entries = [e for e in run.transcript if e["category"] == "D5"]
    d5_paths = {(e["request"]["method"], e["request"]["path"]) for e in d5_entries}
    assert ("GET", "/projects/{id#1}/todos/") in d5_paths
    assert ("POST", "/projects/{id#1}/todos/") in d5_paths
    assert ("GET", "/projects/{id#1}/todos/{id#1}/") in d5_paths
    assert ("PATCH", "/projects/{id#1}/todos/{id#1}/") in d5_paths
    assert ("DELETE", "/projects/{id#1}/todos/{id#1}/") in d5_paths
    assert ("GET", "/projects/with-children/") in d5_paths
    assert ("POST", "/projects/with-children/") in d5_paths

    # D1 and D2 on the with-children batch body: bad parent field and bad field inside one child:
    batch_posts = [
        (e["category"], e["step"], json.loads(e["request"]["body"]))
        for e in run.transcript
        if e["request"]["method"] == "POST" and e["request"]["path"] == "/projects/with-children"
    ]
    assert any(cat == "D2" and "batch parent name" in step and len(b["parent"]["name"]) == 256 for cat, step, b in batch_posts)
    assert any(cat == "D2" and "batch child title" in step and len(b["children"][1]["title"]) == 256 for cat, step, b in batch_posts)
    assert any(cat == "D1" and "batch child completed" in step and b["children"][1]["completed"] == "true" for cat, step, b in batch_posts)

    # Also verify a v5 prompt with int and float fields on parent and child covers int/float D1 and D2 in batch:
    v5_numeric = json.loads(json.dumps(v5_prompt))
    v5_numeric["resources"]["parent"]["fields"].append(
        {"name": "budget", "type": "int", "required": True, "unique": False}
    )
    v5_numeric["resources"]["child"]["fields"].append(
        {"name": "hours", "type": "int", "required": True, "unique": False}
    )
    seeded_paths.clear()
    run_num = ts_suite.TsRun(ts_suite.profile(v5_numeric), fake_send)
    run_num.strict()
    num_batch_posts = [
        (e["category"], e["step"], json.loads(e["request"]["body"]))
        for e in run_num.transcript
        if e["request"]["method"] == "POST" and e["request"]["path"] == "/projects/with-children"
    ]
    assert any(cat == "D1" and "batch parent budget" in step and b["parent"]["budget"] == "1" for cat, step, b in num_batch_posts)
    assert any(cat == "D2" and "batch parent budget" in step and b["parent"]["budget"] == 2147483648 for cat, step, b in num_batch_posts)
    assert any(cat == "D1" and "batch child hours" in step and b["children"][1]["hours"] == "1" for cat, step, b in num_batch_posts)
    assert any(cat == "D2" and "batch child hours" in step and b["children"][1]["hours"] == 2147483648 for cat, step, b in num_batch_posts)


class InMemoryV5Product(InMemoryProduct):
    """In-memory HTTP simulator for v5 prompts to verify v5 parity, added_inputs and strict checks end-to-end."""

    def __init__(
        self,
        prompt: dict[str, Any],
        *,
        wrong_out_of_range_parent_on_child: bool = False,
        redirect_child_trailing_slash: bool = False,
        allow_lax_batch_child: bool = False,
    ) -> None:
        super().__init__(prompt)
        self.parent_table, self.child_table = self.prof.tables
        self.parent_rows: dict[int, dict[str, Any]] = {}
        self.child_rows: dict[int, dict[str, Any]] = {}
        self.next_parent_id = 1
        self.next_child_id = 1
        self.wrong_out_of_range_parent_on_child = wrong_out_of_range_parent_on_child
        self.redirect_child_trailing_slash = redirect_child_trailing_slash
        self.allow_lax_batch_child = allow_lax_batch_child

    def _validate_row_fields(
        self,
        table: ts_suite.Table,
        data: dict[str, Any],
        *,
        allow_lax: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        field_map = {f["name"]: f for f in table.fields}
        if any(k not in field_map for k in data):
            return False, {}
        out: dict[str, Any] = {}
        for f in table.fields:
            fname = f["name"]
            if fname not in data:
                if f["required"]:
                    return False, {}
                out[fname] = table.default(f)
                continue
            val = data[fname]
            if val is None:
                if table.nullable(f):
                    out[fname] = None
                    continue
                return False, {}
            if allow_lax and f["type"] == "bool" and val == "true":
                out[fname] = True
                continue
            valid, cleaned = self._validate_field(f, val)
            if not valid:
                return False, {}
            out[fname] = cleaned
        return True, out

    def send(self, method: str, path: str, body: bytes | None, content_type: str = "application/json") -> tuple[int, str | None, bytes]:
        parsed = urllib.parse.urlsplit(path)
        route = parsed.path
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        p_path = self.parent_table.path
        c_path = self.child_table.path

        if route != "/" and route.endswith("/"):
            if self.redirect_child_trailing_slash and c_path in route:
                return self._json(307, {"detail": "Redirect"})
            return self._json(404, {"detail": "Not Found"})

        if route == "/healthz":
            return self._json(200, {"status": "ok"}) if method == "GET" else self._json(405, {"detail": "Method Not Allowed"})

        if route == p_path:
            if method == "GET":
                return self._list_table(self.parent_table, self.parent_rows, None, query_pairs)
            if method == "POST":
                return self._create_parent(body, content_type)
            return self._json(405, {"detail": "Method Not Allowed"})

        if route == p_path + "/with-children":
            if method == "POST":
                return self._create_batch(body, content_type)
            if method in ("GET", "PATCH", "DELETE"):
                return self._err422("path", "parent_id")
            return self._json(405, {"detail": "Method Not Allowed"})

        prefix = p_path + "/"
        if route.startswith(prefix):
            rest = route[len(prefix):]
            if "/" not in rest:
                raw_pid = urllib.parse.unquote(rest)
                if method not in ("GET", "PATCH", "DELETE"):
                    return self._json(405, {"detail": "Method Not Allowed"})
                return self._item_parent(method, raw_pid, body, content_type)

            # Check child routes: {p_path}/{raw_pid}{c_path} or {p_path}/{raw_pid}{c_path}/{raw_cid}
            c_idx = rest.find(c_path)
            if c_idx > 0:
                raw_pid = urllib.parse.unquote(rest[:c_idx])
                tail = rest[c_idx + len(c_path):]
                if tail == "":
                    if method == "GET":
                        return self._list_children(raw_pid, query_pairs)
                    if method == "POST":
                        return self._create_child(raw_pid, body, content_type)
                    return self._json(405, {"detail": "Method Not Allowed"})
                if tail.startswith("/") and "/" not in tail[1:]:
                    raw_cid = urllib.parse.unquote(tail[1:])
                    if method not in ("GET", "PATCH", "DELETE"):
                        return self._json(405, {"detail": "Method Not Allowed"})
                    return self._item_child(method, raw_pid, raw_cid, body, content_type)

        return self._json(404, {"detail": "Not Found"})

    def _list_table(
        self,
        table: ts_suite.Table,
        store: dict[int, dict[str, Any]],
        parent_id: int | None,
        query_pairs: list[tuple[str, str]],
    ) -> tuple[int, str | None, bytes]:
        seen: set[str] = set()
        params: dict[str, str] = {}
        for k, v in query_pairs:
            if k in seen:
                return self._err422("query", k)
            seen.add(k)
            params[k] = v
        q = table.query
        limit = 20
        if "limit" in params:
            if not re.match(r"^[+-]?[0-9]+$", params["limit"]):
                return self._err422("query", "limit")
            limit = int(params["limit"])
            if limit < 1 or limit > q["max_limit"]:
                return self._err422("query", "limit")
        offset = 0
        if "offset" in params:
            if not re.match(r"^[+-]?[0-9]+$", params["offset"]):
                return self._err422("query", "offset")
            offset = int(params["offset"])
            if offset < 0 or offset > 9223372036854775807:
                return self._err422("query", "offset")
        items = [
            dict(store[k])
            for k in sorted(store)
            if parent_id is None or store[k].get(self.child_table.fk) == parent_id
        ]
        if q.get("filter_field") and q["filter_field"] in params:
            ff = q["filter_field"]
            raw_b = params[ff].lower()
            if raw_b in ("1", "on", "t", "true", "y", "yes"):
                wanted = True
            elif raw_b in ("0", "off", "f", "false", "n", "no"):
                wanted = False
            else:
                return self._err422("query", ff)
            items = [r for r in items if r[ff] is wanted]
        if q.get("date_field") and "before" in params:
            bf = params["before"]
            ok_d, _ = self._validate_field({"type": "date"}, bf)
            if not ok_d:
                return self._err422("query", "before")
            df = q["date_field"]
            items = [r for r in items if r[df] is not None and r[df] < bf]
        return self._json(200, items[offset : offset + limit])

    def _create_parent(self, body: bytes | None, content_type: str) -> tuple[int, str | None, bytes]:
        ok, data = self._parse_body(body, content_type)
        if not ok:
            return self._err422("body")
        valid, vals = self._validate_row_fields(self.parent_table, data)
        if not valid:
            return self._err422("body")
        for f in self.parent_table.fields:
            if f["unique"] and vals[f["name"]] is not None:
                if any(r[f["name"]] == vals[f["name"]] for r in self.parent_rows.values()):
                    return self._json(409, {"detail": self.parent_table.conflict})
        pid = self.next_parent_id
        self.next_parent_id += 1
        self.clock += 1
        now = self._ts()
        row = {"id": pid, **vals, "created_at": now, "updated_at": now}
        self.parent_rows[pid] = row
        return self._json(201, dict(row))

    def _item_parent(self, method: str, raw_pid: str, body: bytes | None, content_type: str) -> tuple[int, str | None, bytes]:
        p_state, pid = self._parse_id(raw_pid)
        if method == "PATCH":
            ok_b, data = self._parse_body(body, content_type)
            if not ok_b:
                return self._err422("body")
            fmap = {f["name"]: f for f in self.parent_table.fields}
            if any(k not in fmap for k in data):
                return self._err422("body")
            updates: dict[str, Any] = {}
            for k, v in data.items():
                f = fmap[k]
                if v is None:
                    if not self.parent_table.nullable(f):
                        return self._err422("body", k)
                    updates[k] = None
                else:
                    valid, cleaned = self._validate_field(f, v)
                    if not valid:
                        return self._err422("body", k)
                    updates[k] = cleaned
            if p_state == "invalid":
                return self._err422("path", "parent_id")
            if p_state == "out_of_range" or pid not in self.parent_rows:
                return self._json(404, {"detail": self.parent_table.not_found})
            for f in self.parent_table.fields:
                fname = f["name"]
                if f["unique"] and fname in updates and updates[fname] is not None:
                    if any(oid != pid and r[fname] == updates[fname] for oid, r in self.parent_rows.items()):
                        return self._json(409, {"detail": self.parent_table.conflict})
            row = self.parent_rows[pid]
            if updates:
                row.update(updates)
                self.clock += 1
                row["updated_at"] = self._ts()
            return self._json(200, dict(row))

        if p_state == "invalid":
            return self._err422("path", "parent_id")
        if p_state == "out_of_range" or pid not in self.parent_rows:
            return self._json(404, {"detail": self.parent_table.not_found})
        if method == "GET":
            return self._json(200, dict(self.parent_rows[pid]))
        fk = self.child_table.fk
        if any(c.get(fk) == pid for c in self.child_rows.values()):
            return self._json(409, {"detail": self.parent_table.conflict})
        del self.parent_rows[pid]
        return self._json(204, None)

    def _list_children(self, raw_pid: str, query_pairs: list[tuple[str, str]]) -> tuple[int, str | None, bytes]:
        p_state, pid = self._parse_id(raw_pid)
        # Validate query params before missing parent check, matching Rust/Python
        dummy = self._list_table(self.child_table, {}, 0, query_pairs)
        if dummy[0] == 422 or p_state == "invalid":
            return self._err422("query" if dummy[0] == 422 else "path", "x")
        if p_state == "out_of_range" or pid not in self.parent_rows:
            if self.wrong_out_of_range_parent_on_child and p_state == "out_of_range":
                return self._json(500, {"detail": "Internal Server Error"})
            return self._json(404, {"detail": self.parent_table.not_found})
        return self._list_table(self.child_table, self.child_rows, pid, query_pairs)

    def _create_child(self, raw_pid: str, body: bytes | None, content_type: str) -> tuple[int, str | None, bytes]:
        p_state, pid = self._parse_id(raw_pid)
        ok_b, data = self._parse_body(body, content_type)
        valid_b, vals = self._validate_row_fields(self.child_table, data) if ok_b else (False, {})
        if p_state == "invalid" or not valid_b:
            return self._err422("path" if p_state == "invalid" else "body")
        if p_state == "out_of_range" or pid not in self.parent_rows:
            return self._json(404, {"detail": self.parent_table.not_found})
        for f in self.child_table.fields:
            if f["unique"] and vals[f["name"]] is not None:
                if any(r[f["name"]] == vals[f["name"]] for r in self.child_rows.values()):
                    return self._json(409, {"detail": self.child_table.conflict})
        cid = self.next_child_id
        self.next_child_id += 1
        self.clock += 1
        now = self._ts()
        fk = self.child_table.fk
        assert fk is not None
        row = {"id": cid, fk: pid, **vals, "created_at": now, "updated_at": now}
        self.child_rows[cid] = row
        return self._json(201, dict(row))

    def _item_child(
        self,
        method: str,
        raw_pid: str,
        raw_cid: str,
        body: bytes | None,
        content_type: str,
    ) -> tuple[int, str | None, bytes]:
        p_state, pid = self._parse_id(raw_pid)
        c_state, cid = self._parse_id(raw_cid)
        updates: dict[str, Any] = {}
        if method == "PATCH":
            ok_b, data = self._parse_body(body, content_type)
            if not ok_b:
                return self._err422("body")
            fmap = {f["name"]: f for f in self.child_table.fields}
            if any(k not in fmap for k in data):
                return self._err422("body")
            for k, v in data.items():
                f = fmap[k]
                if v is None:
                    if not self.child_table.nullable(f):
                        return self._err422("body", k)
                    updates[k] = None
                else:
                    valid, cleaned = self._validate_field(f, v)
                    if not valid:
                        return self._err422("body", k)
                    updates[k] = cleaned
        if p_state == "invalid" or c_state == "invalid":
            return self._err422("path")
        if p_state == "out_of_range" or pid not in self.parent_rows:
            return self._json(404, {"detail": self.parent_table.not_found})
        fk = self.child_table.fk
        if c_state == "out_of_range" or cid not in self.child_rows or self.child_rows[cid].get(fk) != pid:
            return self._json(404, {"detail": self.child_table.not_found})
        if method == "GET":
            return self._json(200, dict(self.child_rows[cid]))
        if method == "DELETE":
            del self.child_rows[cid]
            return self._json(204, None)
        for f in self.child_table.fields:
            fname = f["name"]
            if f["unique"] and fname in updates and updates[fname] is not None:
                if any(oid != cid and r[fname] == updates[fname] for oid, r in self.child_rows.items()):
                    return self._json(409, {"detail": self.child_table.conflict})
        row = self.child_rows[cid]
        if updates:
            row.update(updates)
            self.clock += 1
            row["updated_at"] = self._ts()
        return self._json(200, dict(row))

    def _create_batch(self, body: bytes | None, content_type: str) -> tuple[int, str | None, bytes]:
        ok, data = self._parse_body(body, content_type)
        if not ok or set(data) != {"parent", "children"}:
            return self._err422("body")
        p_raw, c_raw = data["parent"], data["children"]
        if not isinstance(p_raw, dict) or not isinstance(c_raw, list) or not (1 <= len(c_raw) <= 20):
            return self._err422("body")
        ok_p, p_vals = self._validate_row_fields(self.parent_table, p_raw)
        if not ok_p:
            return self._err422("body")
        c_vals_list: list[dict[str, Any]] = []
        for item in c_raw:
            if not isinstance(item, dict):
                return self._err422("body")
            ok_c, c_vals = self._validate_row_fields(self.child_table, item, allow_lax=self.allow_lax_batch_child)
            if not ok_c:
                return self._err422("body")
            c_vals_list.append(c_vals)
        for f in self.parent_table.fields:
            if f["unique"] and p_vals[f["name"]] is not None:
                if any(r[f["name"]] == p_vals[f["name"]] for r in self.parent_rows.values()):
                    return self._json(409, {"detail": self.parent_table.conflict})
        for f in self.child_table.fields:
            if f["unique"]:
                seen_u: set[Any] = set()
                for cv in c_vals_list:
                    uval = cv[f["name"]]
                    if uval is not None:
                        if uval in seen_u or any(r[f["name"]] == uval for r in self.child_rows.values()):
                            return self._json(409, {"detail": self.parent_table.conflict})
                        seen_u.add(uval)
        pid = self.next_parent_id
        self.next_parent_id += 1
        self.clock += 1
        now = self._ts()
        p_row = {"id": pid, **p_vals, "created_at": now, "updated_at": now}
        self.parent_rows[pid] = p_row
        fk = self.child_table.fk
        assert fk is not None
        created_children: list[dict[str, Any]] = []
        for cv in c_vals_list:
            cid = self.next_child_id
            self.next_child_id += 1
            self.clock += 1
            c_now = self._ts()
            c_row = {"id": cid, fk: pid, **cv, "created_at": c_now, "updated_at": c_now}
            self.child_rows[cid] = c_row
            created_children.append(dict(c_row))
        return self._json(201, {"parent": dict(p_row), "children": created_children})


def test_v5_parity_and_strict_against_simulator() -> None:
    v5_prompt = json.loads((EXAMPLES_DIR / "v5.prompt.json").read_text())
    sim = InMemoryV5Product(v5_prompt)
    run = ts_suite.run(v5_prompt, "http://127.0.0.1:18999", send=sim.send)
    assert run.failures == []
    assert all(
        e["request"]["method"] == "POST"
        for e in run.transcript
        if e["request"]["path"].endswith("/with-children")
    )

    before = len(run.transcript)
    run.strict()
    assert run.failures == []
    strict_entries = run.transcript[before:]
    no_row_v5 = [e for e in strict_entries if e["category"] == "strict_no_row_change"]
    assert [e["step"] for e in no_row_v5] == [
        "projects: D1 to D7 changed no row",
        "projects: D1 to D7 added no row",
        "todos: D1 to D7 changed no row",
        "todos: D1 to D7 added no row",
    ]
    gate = gates_mod.evaluate_strict_refusals(strict_entries, run.failures, 15, ["node", "src/main.ts"])
    assert gate["passed"] is True
    assert set(gate["by_divergence"]) == {"D1", "D2", "D3", "D4", "D5", "D7"}
    assert "strict_no_row_change" not in gate["by_divergence"]
    assert gate["prototype_members"] >= 5
    assert gate["no_row_change_checks"] == 4

    # Standalone strict() on a fresh simulator (without running script() first) creates rows via POST and passes:
    standalone_sim = InMemoryV5Product(v5_prompt)
    standalone_run = ts_suite.TsRun(ts_suite.profile(v5_prompt), standalone_sim.send)
    standalone_run.strict()
    assert standalone_run.failures == []

    # Verify child-route defects on v5 are caught by strict():
    for kw, expected_cat in (
        ({"wrong_out_of_range_parent_on_child": True}, "D3"),
        ({"redirect_child_trailing_slash": True}, "D5"),
        ({"allow_lax_batch_child": True}, "D1"),
    ):
        bad_sim = InMemoryV5Product(v5_prompt, **kw)
        bad_run = ts_suite.run(v5_prompt, "http://127.0.0.1:18999", send=bad_sim.send)
        bad_run.strict()
        assert any(f["category"] == expected_cat for f in bad_run.failures), f"expected {expected_cat} failure for {kw}"


class ControlledPorts:
    """Deterministic Ports double over hosts.QA_PORTS (81 ports) without opening real sockets."""

    def __init__(self) -> None:
        self.used: set[int] = set()
        self.active_processes: set[int] = set()
        self.events: list[tuple[str, int]] = []
        self.take_count = 0
        self.give_count = 0
        self.max_concurrent_used = 0

    def take(self) -> int:
        for port in gates_mod.base_hosts.QA_PORTS:
            if port in self.used or port in gates_mod.base_hosts.FORBIDDEN_PORTS:
                continue
            self.used.add(port)
            self.take_count += 1
            self.max_concurrent_used = max(self.max_concurrent_used, len(self.used))
            self.events.append(("take", port))
            return port
        raise RuntimeError("no free QA port")

    def give(self, port: int) -> None:
        assert port not in self.active_processes, f"port {port} was returned while its process was still active"
        assert port in self.used, f"port {port} was given back when not in used={self.used}"
        self.used.discard(port)
        self.give_count += 1
        self.events.append(("give", port))


def _install_fake_gate_environment(
    monkeypatch: pytest.MonkeyPatch,
    ports: ControlledPorts,
    prompt: dict[str, Any],
) -> None:
    """Stub out external processes/DBs in check_ts_products so unit tests run purely in-memory."""
    import contextlib

    spec_out = "ℹ tests 8\nℹ suites 0\nℹ pass 8\nℹ fail 0\nℹ cancelled 0\nℹ skipped 0\nℹ todo 0\n"
    sample_schema = {
        "items": {
            "columns": [["id", "integer", None, "NO", None]],
            "primary_key": [["id"]],
            "unique": [],
            "foreign_keys": [],
            "indexes": [],
        }
    }

    monkeypatch.setattr(gates_mod.base_hosts, "Ports", lambda: ports)
    monkeypatch.setattr(
        ts_host,
        "verify_toolchain",
        lambda _cache_dir: {
            "node_version": "26.5.0",
            "cache": {"tree_sha256": "cache-sha", "tree_files": 1, "tree_symlinks": 0},
        },
    )
    monkeypatch.setattr(ts_host, "tree_sha", lambda _cache_dir: ("cache-sha", 1, 0))
    monkeypatch.setattr(
        ts_host,
        "copy_product",
        lambda _src, dest: (
            dest.mkdir(parents=True, exist_ok=True),
            {**gates_mod.REFERENCE_FILE_HASHES, "src/main.ts": "main-sha"},
        )[1],
    )
    monkeypatch.setattr(
        ts_host,
        "snapshot_outside_node_modules",
        lambda _root: {**gates_mod.REFERENCE_FILE_HASHES, "src/main.ts": "main-sha"},
    )
    monkeypatch.setattr(ts_host, "toolchain_env", lambda _cache, _scratch: {"PATH": "/usr/bin:/bin"})
    monkeypatch.setattr(gates_mod.rust_products, "fresh_database", lambda _pg, name: f"postgresql://127.0.0.1/{name}")
    monkeypatch.setattr(gates_mod.rust_products, "schema", lambda _url, _excl: sample_schema)
    monkeypatch.setattr(gates_mod.base_hosts, "bundle_files", lambda _qa, _prompt, _target: {"pkg/api.py": "# py"})
    monkeypatch.setattr(gates_mod.base_hosts.products, "write", lambda root, _files: root.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(ts_host, "migrate_python_product", lambda *_a, **_kw: None)

    @contextlib.contextmanager
    def fake_pg_cluster(_work: pathlib.Path, port: int, _tracker: ts_host.ProcessTracker):
        assert port in ports.used
        ports.active_processes.add(port)
        ports.events.append(("start_pg", port))
        try:
            yield f"127.0.0.1:{port}"
        finally:
            ports.events.append(("stop_pg", port))
            ports.active_processes.discard(port)

    @contextlib.contextmanager
    def fake_qa_compiler(_work: pathlib.Path, port: int, _tracker: ts_host.ProcessTracker):
        assert port in ports.used
        ports.active_processes.add(port)
        ports.events.append(("start_qa", port))
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            ports.events.append(("stop_qa", port))
            ports.active_processes.discard(port)

    @contextlib.contextmanager
    def fake_python_product(
        _work: pathlib.Path,
        _name: str,
        _prompt: dict[str, Any],
        _files: dict[str, str],
        _db_url: str,
        port: int,
        _python_exe: str,
        _tracker: ts_host.ProcessTracker,
    ):
        assert port in ports.used
        ports.active_processes.add(port)
        ports.events.append(("start_py", port))
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            ports.events.append(("stop_py", port))
            ports.active_processes.discard(port)

    @contextlib.contextmanager
    def fake_typescript_product(
        _root: pathlib.Path,
        _db_url: str,
        port: int,
        _env: dict[str, str],
        _tracker: ts_host.ProcessTracker,
    ):
        assert port in ports.used
        ports.active_processes.add(port)
        ports.events.append(("start_ts", port))
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            ports.events.append(("stop_ts", port))
            ports.active_processes.discard(port)

    def fake_suite_run(p: dict[str, Any], base_url: str) -> ts_suite.TsRun:
        prof = ts_suite.profile(p)
        table = prof.tables[0]
        r = ts_suite.TsRun(prof, lambda *_a, **_kw: (200, "application/json", b"{}"))
        r.transcript = [
            {
                "step": f"{table.name}: healthz ({base_url})",
                "category": "health",
                "request": {"method": "GET", "path": "/healthz"},
                "status": 200,
                "media": "application/json",
                "body": {"status": "ok"},
            }
        ]
        r.raw = [{"step": r.transcript[0]["step"]}]
        r.failures = []

        def fake_strict() -> None:
            for div in ("D1", "D2", "D3", "D4", "D5", "D7", "prototype_members"):
                r.transcript.append(
                    {
                        "step": f"strict {div}",
                        "category": div,
                        "request": {"method": "POST", "path": table.path},
                        "status": 422,
                        "media": "application/json",
                        "body": {"detail": []},
                    }
                )

        monkeypatch.setattr(r, "strict", fake_strict)
        return r

    def fake_run_cmd(
        command: list[str],
        _cwd: pathlib.Path,
        env: dict[str, str],
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        del timeout
        cmd_str = " ".join(command)
        if "src/main.ts" in cmd_str:
            addr = env.get("SOM_APP_ADDR", "")
            if addr.startswith("127.0.0.1:"):
                port = int(addr.split(":")[1])
                assert port in ports.used
                ports.active_processes.add(port)
                ports.events.append(("run_missing_db", port))
                ports.active_processes.discard(port)
                return {
                    "command": command,
                    "exit_code": 2,
                    "stdout": "",
                    "stderr": "DATABASE_URL is required\n",
                    "duration_ms": 1,
                }
            return {
                "command": command,
                "exit_code": 2,
                "stdout": "",
                "stderr": "SOM_APP_ADDR must be host:port\n",
                "duration_ms": 1,
            }
        if "--test" in command:
            if "TEST_DATABASE_URL" in env:
                return {"command": command, "exit_code": 0, "stdout": spec_out, "stderr": "", "duration_ms": 1}
            return {"command": command, "exit_code": 1, "stdout": "", "stderr": "missing TEST_DATABASE_URL\n", "duration_ms": 1}
        return {"command": command, "exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1}

    class FakePopen:
        _next_pid = 70000

        def __init__(self, command: list[str], cwd: pathlib.Path, env: dict[str, str], **_kw: Any) -> None:
            del command, cwd
            FakePopen._next_pid += 1
            self.pid = FakePopen._next_pid
            self.returncode: int | None = None
            addr = env.get("SOM_APP_ADDR", "")
            self.port = int(addr.split(":")[1])
            assert self.port in ports.used
            ports.active_processes.add(self.port)
            ports.events.append(("start_sigterm_probe", self.port))

        def poll(self) -> int | None:
            return self.returncode

    def fake_register(self: ts_host.ProcessTracker, pid: int, role: str, cwd: pathlib.Path, command: list[str]) -> dict[str, Any]:
        entry = {"pid": pid, "role": role, "expected_cwd": str(cwd), "command": command, "stopped": False, "exit_code": None}
        self.records.append(entry)
        return entry

    def fake_stop(
        self: ts_host.ProcessTracker,
        proc: FakePopen,
        _cwd: pathlib.Path,
        entry: dict[str, Any],
        sig: int = 15,
        timeout: float = 10.0,
    ) -> int:
        del self, sig, timeout
        proc.returncode = 0
        entry["stopped"] = True
        entry["exit_code"] = 0
        ports.events.append(("stop_sigterm_probe", proc.port))
        ports.active_processes.discard(proc.port)
        return 0

    monkeypatch.setattr(ts_host, "pg_cluster", fake_pg_cluster)
    monkeypatch.setattr(ts_host, "qa_compiler", fake_qa_compiler)
    monkeypatch.setattr(ts_host, "python_product", fake_python_product)
    monkeypatch.setattr(ts_host, "typescript_product", fake_typescript_product)
    monkeypatch.setattr(ts_suite, "run", fake_suite_run)
    monkeypatch.setattr(gates_mod, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(gates_mod.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(ts_host, "wait_http_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(ts_host, "verify_process_cwd", lambda pid, cwd: (True, str(cwd)))
    monkeypatch.setattr(ts_host.ProcessTracker, "register", fake_register)
    monkeypatch.setattr(ts_host.ProcessTracker, "stop", fake_stop)
    del prompt


def test_check_ts_products_returns_ports_across_more_than_81_products(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    prompt = RichV4Prompt()
    ports = ControlledPorts()
    _install_fake_gate_environment(monkeypatch, ports, prompt)

    num_products = 90  # > len(hosts.QA_PORTS) == 81; requires 90 * 4 + 2 = 362 port leases
    assert len(gates_mod.base_hosts.QA_PORTS) == 81
    manifest_entries = []
    for i in range(num_products):
        prod_dir = tmp_path / "products" / f"p_{i:03d}"
        prod_dir.mkdir(parents=True, exist_ok=True)
        manifest_entries.append({"id": f"case-{i:03d}", "product": str(prod_dir), "prompt": prompt})

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"products": manifest_entries}))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    out_dir = tmp_path / "out"

    exit_code = gates_mod.main(["--manifest", str(manifest_path), "--cache", str(cache_dir), "--out", str(out_dir)])
    assert exit_code == 0

    # 2 cluster-level ports (pg + qa) + 4 ports per product (py parity, ts parity, missing_db, sigterm)
    assert ports.take_count == 2 + 4 * num_products
    assert ports.give_count == ports.take_count
    assert ports.used == set()
    assert ports.active_processes == set()
    # Only pg_port, qa_port, and at most 1 per-product port are ever held simultaneously
    assert ports.max_concurrent_used == 3

    # Verify pg_cluster and qa_compiler ports were taken first and given back only at the end of the run,
    # after stop_qa and stop_pg respectively:
    pg_port = ports.events[0][1]
    qa_port = ports.events[2][1]
    assert ports.events[0] == ("take", pg_port)
    assert ports.events[1] == ("start_pg", pg_port)
    assert ports.events[2] == ("take", qa_port)
    assert ports.events[3] == ("start_qa", qa_port)
    assert ports.events[-4:] == [
        ("stop_qa", qa_port),
        ("give", qa_port),
        ("stop_pg", pg_port),
        ("give", pg_port),
    ]


def test_check_ts_products_returns_ports_when_gates_raise_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    prompt = RichV4Prompt()
    ports = ControlledPorts()
    _install_fake_gate_environment(monkeypatch, ports, prompt)

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    prod_src = tmp_path / "prod_src"
    prod_src.mkdir()
    tracker = ts_host.ProcessTracker()

    # Reserve pg_port and qa_port as main() does during a run
    pg_port = ports.take()
    qa_port = ports.take()
    baseline_used = {pg_port, qa_port}

    # 1. Exception inside parity Python product -> py_port returned after stop_py
    orig_suite_run = ts_suite.run

    def boom_on_py_parity(_p: dict[str, Any], base_url: str) -> ts_suite.TsRun:
        raise RuntimeError(f"simulated python parity crash on {base_url}")

    monkeypatch.setattr(ts_suite, "run", boom_on_py_parity)
    row_py_fail = gates_mod.check_one_product(
        "case-py-boom",
        prod_src,
        prompt,
        cache_dir,
        tmp_path / "work1",
        tmp_path / "out1",
        f"127.0.0.1:{pg_port}",
        f"http://127.0.0.1:{qa_port}",
        sys.executable,
        ports,
        tracker,
    )
    assert row_py_fail["gates"]["parity"]["status"] == "failed"
    assert row_py_fail["gates"]["strict_refusals"]["status"] == "failed"
    assert row_py_fail["gates"]["startup"]["status"] == "passed"
    assert ports.used == baseline_used
    assert ports.active_processes == set()

    # 2. Exception inside TypeScript parity/strict_refusals -> both py_port and ts_port returned
    call_count = 0

    def boom_on_ts_parity(p: dict[str, Any], base_url: str) -> ts_suite.TsRun:
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 0:
            raise RuntimeError(f"simulated ts parity crash on {base_url}")
        return orig_suite_run(p, base_url)

    monkeypatch.setattr(ts_suite, "run", boom_on_ts_parity)
    row_ts_fail = gates_mod.check_one_product(
        "case-ts-boom",
        prod_src,
        prompt,
        cache_dir,
        tmp_path / "work2",
        tmp_path / "out2",
        f"127.0.0.1:{pg_port}",
        f"http://127.0.0.1:{qa_port}",
        sys.executable,
        ports,
        tracker,
    )
    assert row_ts_fail["gates"]["parity"]["status"] == "failed"
    assert row_ts_fail["gates"]["strict_refusals"]["status"] == "failed"
    assert row_ts_fail["gates"]["startup"]["status"] == "passed"
    assert ports.used == baseline_used
    assert ports.active_processes == set()
    monkeypatch.setattr(ts_suite, "run", orig_suite_run)

    # 3. Exception during startup missing-DATABASE_URL -> missing_db port returned
    orig_run_cmd = gates_mod.run_cmd

    def boom_on_missing_db(
        command: list[str],
        cwd: pathlib.Path,
        env: dict[str, str],
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        if "src/main.ts" in " ".join(command) and "DATABASE_URL" not in env:
            addr = env["SOM_APP_ADDR"]
            port = int(addr.split(":")[1])
            assert port in ports.used
            raise RuntimeError("simulated missing_db subprocess failure")
        return orig_run_cmd(command, cwd, env, timeout=timeout)

    monkeypatch.setattr(gates_mod, "run_cmd", boom_on_missing_db)
    row_startup_db_fail = gates_mod.check_one_product(
        "case-startup-db-boom",
        prod_src,
        prompt,
        cache_dir,
        tmp_path / "work3",
        tmp_path / "out3",
        f"127.0.0.1:{pg_port}",
        f"http://127.0.0.1:{qa_port}",
        sys.executable,
        ports,
        tracker,
    )
    assert row_startup_db_fail["gates"]["parity"]["status"] == "passed"
    assert row_startup_db_fail["gates"]["startup"]["status"] == "failed"
    assert ports.used == baseline_used
    assert ports.active_processes == set()
    monkeypatch.setattr(gates_mod, "run_cmd", orig_run_cmd)

    # 4. Exception during startup SIGTERM (both caught wait_http_ready error and uncaught register error)
    def boom_wait_ready(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("simulated wait_http_ready timeout")

    monkeypatch.setattr(ts_host, "wait_http_ready", boom_wait_ready)
    row_sigterm_wait_fail = gates_mod.check_one_product(
        "case-sigterm-wait-boom",
        prod_src,
        prompt,
        cache_dir,
        tmp_path / "work4",
        tmp_path / "out4",
        f"127.0.0.1:{pg_port}",
        f"http://127.0.0.1:{qa_port}",
        sys.executable,
        ports,
        tracker,
    )
    assert row_sigterm_wait_fail["gates"]["startup"]["status"] == "failed"
    assert ports.used == baseline_used
    assert ports.active_processes == set()

    monkeypatch.setattr(ts_host, "wait_http_ready", lambda *_a, **_kw: None)
    orig_eval_startup = gates_mod.evaluate_startup

    def boom_eval_startup(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise RuntimeError("simulated evaluate_startup failure")

    monkeypatch.setattr(gates_mod, "evaluate_startup", boom_eval_startup)
    row_sigterm_eval_fail = gates_mod.check_one_product(
        "case-sigterm-eval-boom",
        prod_src,
        prompt,
        cache_dir,
        tmp_path / "work5",
        tmp_path / "out5",
        f"127.0.0.1:{pg_port}",
        f"http://127.0.0.1:{qa_port}",
        sys.executable,
        ports,
        tracker,
    )
    assert row_sigterm_eval_fail["gates"]["startup"]["status"] == "failed"
    assert ports.used == baseline_used
    assert ports.active_processes == set()
    monkeypatch.setattr(gates_mod, "evaluate_startup", orig_eval_startup)

    ports.give(qa_port)
    ports.give(pg_port)
    assert ports.used == set()




