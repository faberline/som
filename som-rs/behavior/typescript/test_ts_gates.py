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
    assert {"D1", "D2", "D3", "D4", "D5", "D7"} <= strict_cats
    assert run.failures == []


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

    # 5. Defect in D7: accepting non-canonical path id or Object.prototype toString
    sim_d7 = InMemoryProduct(prompt, allow_non_canonical_path_id=True, allow_prototype_keys=True)
    run_d7 = ts_suite.run(prompt, "http://127.0.0.1:18999", send=sim_d7.send)
    run_d7.strict()
    d7_steps = [f["step"] for f in run_d7.failures if f["category"] == "D7"]
    assert any("path id" in s for s in d7_steps)
    assert any("Object.prototype toString" in s for s in d7_steps)


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
    d7_failures = [f for f in run_buggy.failures if f["category"] == "D7"]
    assert len(d7_failures) >= 2

