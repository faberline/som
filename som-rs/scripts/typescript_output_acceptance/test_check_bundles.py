#!/usr/bin/env python3
"""Unit tests for check_bundles.py using a fake loopback HTTP server (issue #99)."""
from __future__ import annotations

import contextlib
import copy
import errno
import http.client
import http.server
import io
import json
import pathlib
import socket
import sys
import tempfile
import threading
import unittest
import unittest.mock
from typing import Any, Callable

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_bundles  # noqa: E402


def _build_fixture_tables() -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, str]],
    dict[str, Any],
    dict[str, str],
    list[dict[str, Any]],
]:
    cnl_to_prompt: dict[str, dict[str, Any]] = {}
    inv = json.loads(check_bundles.build_case_inventory.OUT.read_text(encoding="utf-8"))
    for pub in inv["public_examples"]:
        prompt_obj = json.loads((check_bundles.REPO / pub["prompt"]).read_text(encoding="utf-8"))
        if pub.get("cnl"):
            cnl_text = (check_bundles.REPO / pub["cnl"]).read_text(encoding="utf-8")
            cnl_to_prompt[cnl_text] = prompt_obj

    fixtures_doc = json.loads(check_bundles.build_case_inventory.FIXTURES.read_text(encoding="utf-8"))
    for fix in fixtures_doc["cases"]:
        cnl_to_prompt[fix["cnl"]] = fix["expected_prompt"]

    cov_doc = json.loads(check_bundles.build_case_inventory.COVERAGE.read_text(encoding="utf-8"))
    for cov in cov_doc["cases"]:
        prompt_obj = json.loads(
            (check_bundles.COVERAGE_CASES / cov["prompt"]).read_text(encoding="utf-8")
        )
        cnl_text = (check_bundles.COVERAGE_CASES / cov["cnl"]).read_text(encoding="utf-8")
        cnl_to_prompt[cnl_text] = prompt_obj

    target_examples_doc = json.loads(
        (check_bundles.CONTRACT / "target-examples.json").read_text(encoding="utf-8")
    )
    for nc in target_examples_doc["name_cases"]:
        if nc["form"] == "cnl":
            cnl_text = json.loads(nc["body"])["source"]
            base_prompt_file = nc["base_example"].replace(".cnl", ".prompt.json")
            base_prompt = json.loads(
                (check_bundles.EXAMPLES / base_prompt_file).read_text(encoding="utf-8")
            )
            base_prompt["package"] = f"name_case_{nc['id'].lower()}"
            cnl_to_prompt[cnl_text] = copy.deepcopy(base_prompt)

    ts_specific = check_bundles.compute_expected_ts_specific_descriptors()
    catalog_doc = json.loads(check_bundles.CATALOG_PATH.read_text(encoding="utf-8"))
    reference_texts = {
        name: (check_bundles.REFERENCE_DIR / name).read_text(encoding="utf-8")
        for name in ("package.json", "package-lock.json", "tsconfig.json")
    }
    acceptance_plan_doc = json.loads(
        (check_bundles.CONTRACT / "acceptance-plan.json").read_text(encoding="utf-8")
    )
    recipes = acceptance_plan_doc["assemble_refusals"]["recipes"]
    return cnl_to_prompt, ts_specific, catalog_doc, reference_texts, recipes


CNL_TO_PROMPT, TS_SPECIFIC, CATALOG_DOC, REFERENCE_TEXTS, RECIPES = _build_fixture_tables()
RECIPES_BY_ID = {r["id"]: r for r in RECIPES}


def build_ts_dsl(prompt: dict[str, Any]) -> dict[str, Any]:
    version = prompt["version"]
    revision = prompt["migration"]["revision"]
    files = []
    for file_entry in CATALOG_DOC["files"]:
        blocks = [
            {
                "id": blk["id"],
                "name": blk["name"],
                "params": copy.deepcopy(blk["params"]),
                "refs": list(blk["refs"]),
            }
            for blk in file_entry["blocks"]
            if version in blk["versions"]
        ]
        if blocks:
            files.append(
                {
                    "path": file_entry["path"].replace("{migration.revision}", revision),
                    "blocks": blocks,
                }
            )
    return {"version": check_bundles.TS_DSL_VERSION, "files": files}


def build_valid_ts_bundle(prompt: dict[str, Any]) -> dict[str, Any]:
    shared = check_bundles.compute_expected_shared_descriptors(prompt)
    bundle = {
        "version": check_bundles.TS_BUNDLE_VERSION,
        "target": "typescript",
        "prompt": copy.deepcopy(prompt),
        "dsl": build_ts_dsl(prompt),
        "assets": {**shared, **copy.deepcopy(TS_SPECIFIC)},
    }
    return check_bundles.rehash_bundle(bundle)


def build_valid_rust_bundle(prompt: dict[str, Any]) -> dict[str, Any]:
    shared = check_bundles.compute_expected_shared_descriptors(prompt)
    rust_specific = {
        "target_contract": {"id": "som.rust-output-contract.v1", "sha256": "a" * 64},
        "catalog": {"id": "som.rust-catalog.v1", "sha256": "b" * 64},
        "block_library": {"id": "som.rust-block-library.v1", "sha256": "c" * 64},
        "backend": {"id": "som.rust-backend.v1", "sha256": "d" * 64},
        "formatter": {"id": "som.rust-formatter.v1", "sha256": "e" * 64},
    }
    bundle = {
        "version": "som.dsl-bundle.v2",
        "target": "rust",
        "prompt": copy.deepcopy(prompt),
        "dsl": {
            "version": "som.dsl.rust.v1",
            "files": [
                {
                    "path": "Cargo.toml",
                    "blocks": [
                        {
                            "id": "rust.cargo_manifest",
                            "name": "cargo_manifest",
                            "params": {},
                            "refs": [],
                        }
                    ],
                }
            ],
        },
        "assets": {**shared, **rust_specific},
    }
    return check_bundles.rehash_bundle(bundle)


def build_valid_py_bundle(prompt: dict[str, Any]) -> dict[str, Any]:
    shared = check_bundles.compute_expected_shared_descriptors(prompt)
    py_specific = {
        "catalog": {"id": "som.compiler-catalog.legacy.v1", "sha256": "1" * 64},
        "block_library": {"id": "som.compiler-block-library.v1", "sha256": "2" * 64},
        "backend": {"id": "som.compiler-backend.v1", "sha256": "3" * 64},
        "formatter": {"id": "som.compiler-render-only.v1", "sha256": "4" * 64},
    }
    bundle = {
        "version": check_bundles.PY_BUNDLE_VERSION,
        "prompt": copy.deepcopy(prompt),
        "dsl": {
            "version": "som.dsl.v1",
            "files": [
                {
                    "path": "app/models.py",
                    "blocks": [
                        {"id": "orm.base", "name": "base", "params": {}, "refs": []}
                    ],
                }
            ],
        },
        "assets": {**shared, **py_specific},
    }
    return check_bundles.rehash_bundle(bundle)


def build_product_files(prompt: dict[str, Any]) -> dict[str, str]:
    revision = prompt["migration"]["revision"]
    return {
        "package.json": REFERENCE_TEXTS["package.json"],
        "package-lock.json": REFERENCE_TEXTS["package-lock.json"],
        "tsconfig.json": REFERENCE_TEXTS["tsconfig.json"],
        "src/main.ts": "// main.ts\n",
        "src/app.ts": "// app.ts\n",
        "src/schemas.ts": "// schemas.ts\n",
        "src/db.ts": "// db.ts\n",
        "src/migrate.ts": "// migrate.ts\n",
        f"migrations/0001_{revision}.sql": f"-- revision: {revision}\n",
        "test/api.test.ts": "// api.test.ts\n",
        "README.md": f"# {prompt.get('package', 'app')}\n",
    }


def compute_default_compile_response(body_str: str) -> tuple[int, bytes]:
    req = json.loads(body_str)
    prompt_obj = req["prompt"] if "prompt" in req else CNL_TO_PROMPT[req["source"]]
    target = req.get("target")
    if target in (None, "python"):
        bundle = build_valid_py_bundle(prompt_obj)
    elif target == "rust":
        bundle = build_valid_rust_bundle(prompt_obj)
    else:
        bundle = build_valid_ts_bundle(prompt_obj)
    return 200, check_bundles.canonical_json({"status": "ok", "bundle": bundle}).encode("utf-8")


def _classify_invalid_bundle(bundle: dict[str, Any]) -> tuple[int, str, str]:
    """Return (http_status, code, prompt_pointer) in api.schema.json x-assemble-check-order."""
    if bundle.get("version") != "som.dsl-bundle.v2":
        if "target" in bundle:
            return 400, "INVALID_SCHEMA", "/bundle/target"
        return 400, "INVALID_SCHEMA", "/bundle/version"
    if bundle.get("target") == "rust":
        if bundle.get("dsl", {}).get("version") != "som.dsl.rust.v1":
            return 400, "INVALID_SCHEMA", "/bundle/dsl/version"
    if bundle.get("target") != "typescript":
        return 400, "INVALID_SCHEMA", "/bundle/target"
    dsl = bundle.get("dsl", {})
    if dsl.get("version") != check_bundles.TS_DSL_VERSION:
        return 400, "INVALID_SCHEMA", "/bundle/dsl/version"
    files = dsl.get("files", [])
    if not (1 <= len(files) <= 12):
        return 400, "INVALID_SCHEMA", "/bundle/dsl/files"
    recomputed = check_bundles.rehash_bundle(bundle)["hashes"]
    for hkey in ("prompt_sha256", "dsl_sha256", "payload_sha256"):
        if bundle.get("hashes", {}).get(hkey) != recomputed[hkey]:
            return 400, "BUNDLE_HASH_MISMATCH", f"/bundle/hashes/{hkey}"
    prompt = bundle["prompt"]
    expected_assets = {
        **check_bundles.compute_expected_shared_descriptors(prompt),
        **TS_SPECIFIC,
    }
    if bundle.get("assets") != expected_assets:
        return 400, "ASSET_HASH_MISMATCH", "/bundle/assets"
    found = check_bundles.common.ts_profile.check(
        prompt, check_bundles.common.ts_profile.load_scope()
    )
    if found is not None:
        return 422, "UNSUPPORTED_TARGET", "/bundle/prompt" + found["pointer"]
    expected_dsl = build_ts_dsl(prompt)
    for f_idx, (act_f, exp_f) in enumerate(zip(files, expected_dsl["files"])):
        for b_idx, (act_b, exp_b) in enumerate(zip(act_f["blocks"], exp_f["blocks"])):
            if act_b.get("id") != exp_b.get("id"):
                return (
                    400,
                    "INVALID_COMPOSITION",
                    f"/bundle/dsl/files/{f_idx}/blocks/{b_idx}/id",
                )
    return 200, "OK", ""


def compute_default_assemble_response(body_str: str) -> tuple[int, bytes]:
    req = json.loads(body_str)
    bundle = req["bundle"]
    status, code, pointer = _classify_invalid_bundle(bundle)
    if status == 200:
        files = build_product_files(bundle["prompt"])
        return 200, check_bundles.canonical_json({"status": "ok", "files": files}).encode("utf-8")

    spans = check_bundles.rust_cases.Spans(body_str)
    loc = check_bundles.rust_cases.location(body_str, *spans.values[pointer])
    cause = f"Refused at {pointer} with {code}."
    diag = {
        "code": code,
        "prompt_pointer": pointer,
        "cause": cause,
        "allowed_form": "valid TypeScript bundle",
        **loc,
    }
    reply = {
        "status": "error",
        "code": code,
        "detail": cause,
        "diagnostics": [diag],
    }
    return status, check_bundles.canonical_json(reply).encode("utf-8")


OverrideFn = Callable[[str, str, tuple[int, bytes]], tuple[int, bytes]]

_ACTIVE_SERVERS: dict[int, FakeSomServer] = {}
_ORIG_HTTP_CONNECT = http.client.HTTPConnection.connect


def _sandbox_aware_http_connect(self: http.client.HTTPConnection) -> None:
    try:
        _ORIG_HTTP_CONNECT(self)
    except PermissionError as exc:
        if exc.errno != errno.EPERM or self.host not in check_bundles.LOOPBACK_HOSTS:
            raise
        srv = _ACTIVE_SERVERS.get(int(self.port))
        if srv is None:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused") from exc
        cli_sock, srv_sock = socket.socketpair()
        self.sock = cli_sock
        srv.accept_socketpair(srv_sock)


http.client.HTTPConnection.connect = _sandbox_aware_http_connect  # type: ignore[method-assign]


class FakeSomServer:
    def __init__(self, override: OverrideFn | None = None) -> None:
        self.override = override
        self.request_count = 0
        self.assemble_requests: list[str] = []
        self._lock = threading.Lock()
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length).decode("utf-8")
                with parent._lock:
                    parent.request_count += 1
                    if self.path == "/v1/assemble":
                        parent.assemble_requests.append(raw_body)
                if self.path == "/v1/compile":
                    default_resp = compute_default_compile_response(raw_body)
                else:
                    default_resp = compute_default_assemble_response(raw_body)
                status, payload = (
                    parent.override(self.path, raw_body, default_resp)
                    if parent.override is not None
                    else default_resp
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if self._httpd.server_port in check_bundles.FORBIDDEN_PORTS:
            self._httpd.server_close()
            self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def accept_socketpair(self, srv_sock: socket.socket) -> None:
        self._httpd.process_request(srv_sock, ("127.0.0.1", 0))

    @property
    def port(self) -> int:
        return int(self._httpd.server_port)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> FakeSomServer:
        _ACTIVE_SERVERS[self.port] = self
        self._thread.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        _ACTIVE_SERVERS.pop(self.port, None)
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def create_tiny_legacy_run(
    base_dir: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path, dict[str, Any]]:
    """Build a synthetic tiny legacy run (1 in-scope + 1 out-of-scope job) using public prompts."""
    v3_prompt = json.loads(
        (check_bundles.EXAMPLES / "v3.prompt.json").read_text(encoding="utf-8")
    )
    in_prompt = {**v3_prompt, "package": "fake_legacy_in_scope"}
    in_sha = check_bundles.canonical_hash(in_prompt)

    v6_prompt = json.loads(
        (check_bundles.EXAMPLES / "v6.prompt.json").read_text(encoding="utf-8")
    )
    out_prompt = {**v6_prompt, "package": "fake_legacy_out_of_scope"}
    out_sha = check_bundles.canonical_hash(out_prompt)

    leg_dir = base_dir / "legacy544" / "run-005"
    for idx, (job_id, p_obj, p_sha) in enumerate(
        (
            ("legacy100-0000", in_prompt, in_sha),
            ("services14-0000", out_prompt, out_sha),
        )
    ):
        comp_dir = leg_dir / "compile" / p_sha
        comp_dir.mkdir(parents=True, exist_ok=True)
        req_text = (
            check_bundles.compact_json(
                {"format": "som.prompt-json.v1", "prompt": p_obj}
            )
            + "\n"
        )
        (comp_dir / "compile-json.request.json").write_text(req_text, encoding="utf-8")
        idx_dir = leg_dir / f"{idx:04d}"
        idx_dir.mkdir(parents=True, exist_ok=True)
        (idx_dir / "compile-json.request.json").write_text(req_text, encoding="utf-8")

    result_doc = {
        "required_job_count": 2,
        "passed_job_count": 2,
        "records": [
            {
                "id": "legacy100-0000",
                "group": "legacy100",
                "prompt_sha256": in_sha,
            },
            {
                "id": "services14-0000",
                "group": "services14",
                "prompt_sha256": out_sha,
            },
        ],
    }
    (leg_dir / "result.json").write_text(
        json.dumps(result_doc, indent=2) + "\n", encoding="utf-8"
    )

    inv = json.loads(
        (check_bundles.CONTRACT / "case-inventory.json").read_text(encoding="utf-8")
    )
    inv["legacy_in_scope"] = [
        {
            "id": "legacy100-0000",
            "group": "legacy100",
            "inventory_index": 0,
            "prompt_version": in_prompt["version"],
            "prompt_sha256": in_sha,
        }
    ]
    inv["counts"]["legacy_in_scope"] = 1
    inv["counts"]["acceptance_total"] = 26
    inv_path = base_dir / "case-inventory.json"
    inv_path.write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")
    return leg_dir, inv_path, in_prompt


class CheckBundlesTests(unittest.TestCase):
    def _run_main(
        self,
        argv: list[str],
        *,
        expected_legacy_in_scope: int | None = 1,
        expected_manifest_products: int | None = 26,
        inv_path: pathlib.Path | None = None,
        patch_inventory: bool = True,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as leg_tmp:
            if "--legacy-run" not in argv:
                leg_run, default_inv, _ = create_tiny_legacy_run(pathlib.Path(leg_tmp))
                argv = [*argv, "--legacy-run", str(leg_run)]
                if inv_path is None:
                    inv_path = default_inv
            if patch_inventory and inv_path is not None:
                with (
                    unittest.mock.patch.object(
                        check_bundles.build_case_inventory, "OUT", inv_path
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    rc = check_bundles.main(
                        argv,
                        expected_legacy_in_scope=expected_legacy_in_scope,
                        expected_manifest_products=expected_manifest_products,
                    )
            else:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    rc = check_bundles.main(
                        argv,
                        expected_legacy_in_scope=expected_legacy_in_scope,
                        expected_manifest_products=expected_manifest_products,
                    )
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_pass_path_and_recipes_a1_to_a12_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeSomServer() as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, out, err = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 0, msg=f"stderr={err}\nstdout={out}")

            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["total"], 38)
            self.assertEqual(report["passed_count"], 38)
            self.assertEqual(report["failed_count"], 0)
            self.assertEqual(report["not_run_count"], 0)
            self.assertNotIn("legacy_in_scope", report)

            bundle_rows = [r for r in report["cases"] if r["section"] == "bundle_checks"]
            refusal_rows = [r for r in report["cases"] if r["section"] == "assemble_refusals"]
            self.assertEqual(len(bundle_rows), 26)
            self.assertEqual(len(refusal_rows), 12)
            # Existing 25 non-legacy bundle_checks + 12 assemble_refusals come first; legacy row goes after them
            self.assertEqual(
                [r["section"] for r in report["cases"][:25]],
                ["bundle_checks"] * 25,
            )
            self.assertEqual(
                [r["section"] for r in report["cases"][25:37]],
                ["assemble_refusals"] * 12,
            )
            self.assertEqual(report["cases"][37]["id"], "legacy100-0000")
            self.assertEqual(report["cases"][37]["section"], "bundle_checks")
            self.assertEqual(report["cases"][37]["forms"], ["json"])
            self.assertIn("no CNL source", report["cases"][37]["single_form"])

            # Single-form cases (4 JSON-only name_cases + 1 legacy job) vs two-form cases (15 + 6 CNL name_cases = 21)
            single_form_rows = [r for r in bundle_rows if r["single_form"] is not None]
            two_form_rows = [r for r in bundle_rows if r["single_form"] is None]
            self.assertEqual(len(single_form_rows), 5)
            self.assertEqual(len(two_form_rows), 21)
            tn01_row = next(r for r in bundle_rows if r["id"] == "TN01_CNL_FIELD_UPPERCASE")
            self.assertEqual(tn01_row["forms"], ["cnl", "json"])
            self.assertIsNone(tn01_row["single_form"])
            self.assertTrue(
                (
                    out_dir
                    / "responses"
                    / "bundle_checks__TN01_CNL_FIELD_UPPERCASE__compile_json_1.json"
                ).is_file()
            )
            for r in single_form_rows:
                self.assertEqual(r["forms"], ["json"])

            # Verify products/manifest.json and assembled product directories (25 non-legacy + 1 legacy = 26)
            manifest_path = out_dir / "products" / "manifest.json"
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["products"]), 26)
            self.assertEqual(manifest["products"][-1]["id"], "legacy100-0000")
            for entry in manifest["products"]:
                self.assertIn("id", entry)
                self.assertIn("product", entry)
                self.assertIsInstance(entry["prompt"], dict)
                pdir = pathlib.Path(entry["product"])
                self.assertTrue(pdir.is_dir())
                for ref_name, ref_text in REFERENCE_TEXTS.items():
                    self.assertEqual((pdir / ref_name).read_text(encoding="utf-8"), ref_text)

            # Verify the 12 mutated assemble request bodies sent for A1-A12
            # The last 12 assemble requests sent to the server are A1..A12 in order.
            self.assertEqual(len(srv.assemble_requests), 26 * 2 + 12)
            recipe_bodies = srv.assemble_requests[-12:]
            v1_prompt = json.loads((check_bundles.EXAMPLES / "v1.prompt.json").read_text(encoding="utf-8"))
            v5_prompt = json.loads((check_bundles.EXAMPLES / "v5.prompt.json").read_text(encoding="utf-8"))
            v6_prompt = json.loads((check_bundles.EXAMPLES / "v6.prompt.json").read_text(encoding="utf-8"))
            base_ts_v1 = build_valid_ts_bundle(v1_prompt)
            base_ts_v5 = build_valid_ts_bundle(v5_prompt)
            base_rust_v1 = build_valid_rust_bundle(v1_prompt)

            by_recipe = {
                RECIPES[i]["id"]: json.loads(recipe_bodies[i])["bundle"]
                for i in range(12)
            }
            # A1: formatter.sha256 replaced with 64 zeros, rehashed
            self.assertEqual(by_recipe["A1"]["assets"]["formatter"]["sha256"], "0" * 64)
            self.assertEqual(
                by_recipe["A1"]["hashes"],
                check_bundles.rehash_bundle(by_recipe["A1"])["hashes"],
            )
            # A2: 5 TypeScript descriptors replaced with Rust manifest descriptors, rehashed
            for k in check_bundles.TS_SPECIFIC_ASSET_KEYS:
                self.assertEqual(by_recipe["A2"]["assets"][k], base_rust_v1["assets"][k])
            self.assertEqual(
                by_recipe["A2"]["hashes"],
                check_bundles.rehash_bundle(by_recipe["A2"])["hashes"],
            )
            # A3: som.dsl-bundle.v1 Python bundle with target: "typescript"
            self.assertEqual(by_recipe["A3"]["version"], "som.dsl-bundle.v1")
            self.assertEqual(by_recipe["A3"]["target"], "typescript")
            # A4: TypeScript bundle with target: "python"
            self.assertEqual(by_recipe["A4"]["version"], "som.dsl-bundle.v2")
            self.assertEqual(by_recipe["A4"]["target"], "python")
            # A5: first block id is orm.base
            self.assertEqual(
                by_recipe["A5"]["dsl"]["files"][0]["blocks"][0]["id"], "orm.base"
            )
            # A6: v5 TypeScript DSL & TS descriptors + v6 prompt & v6 shared descriptors
            self.assertEqual(by_recipe["A6"]["prompt"], v6_prompt)
            self.assertEqual(by_recipe["A6"]["dsl"], base_ts_v5["dsl"])
            for k in check_bundles.TS_SPECIFIC_ASSET_KEYS:
                self.assertEqual(by_recipe["A6"]["assets"][k], base_ts_v5["assets"][k])
            # A7: payload_sha256 changed by 1 hex digit without rehashing
            self.assertNotEqual(
                by_recipe["A7"]["hashes"]["payload_sha256"],
                base_ts_v1["hashes"]["payload_sha256"],
            )
            self.assertEqual(
                by_recipe["A7"]["hashes"]["prompt_sha256"],
                base_ts_v1["hashes"]["prompt_sha256"],
            )
            # A8: first prompt field renamed to __proto__
            self.assertEqual(
                by_recipe["A8"]["prompt"]["resource"]["fields"][0]["name"],
                "__proto__",
            )
            # A9: 13 files (12th and 13th are copies of the last file)
            self.assertEqual(len(by_recipe["A9"]["dsl"]["files"]), 13)
            self.assertEqual(
                by_recipe["A9"]["dsl"]["files"][11],
                by_recipe["A9"]["dsl"]["files"][10],
            )
            self.assertEqual(
                by_recipe["A9"]["dsl"]["files"][12],
                by_recipe["A9"]["dsl"]["files"][10],
            )
            # A10: target set to rust in TypeScript bundle
            self.assertEqual(by_recipe["A10"]["target"], "rust")
            self.assertEqual(by_recipe["A10"]["dsl"]["version"], "som.dsl.typescript.v1")
            # A11: target set to typescript in Rust bundle
            self.assertEqual(by_recipe["A11"]["target"], "typescript")
            self.assertEqual(by_recipe["A11"]["dsl"]["version"], "som.dsl.rust.v1")
            # A12: first block id replaced with first block id of candidate's Rust bundle
            self.assertEqual(
                by_recipe["A12"]["dsl"]["files"][0]["blocks"][0]["id"],
                base_rust_v1["dsl"]["files"][0]["blocks"][0]["id"],
            )

    def test_cnl_not_equal_to_json_bundle_bytes_fails(self) -> None:
        def tamper_cnl(path: str, body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            if path == "/v1/compile":
                req = json.loads(body)
                if req.get("format") == "som.cnl.v1":
                    return default[0], default[1] + b"\n"
            return default

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer(override=tamper_cnl) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            v1_row = next(r for r in report["cases"] if r["id"] == "example-v1")
            self.assertFalse(v1_row["passed"])
            self.assertIn("CNL and JSON compile bytes differ", v1_row["reason"])

    def test_non_ts_block_id_fails(self) -> None:
        def bad_block_id(path: str, body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            if path == "/v1/compile":
                req = json.loads(body)
                if req.get("target") == "typescript":
                    doc = json.loads(default[1])
                    doc["bundle"]["dsl"]["files"][0]["blocks"][0]["id"] = "rust.bad_block"
                    doc["bundle"] = check_bundles.rehash_bundle(doc["bundle"])
                    return 200, check_bundles.canonical_json(doc).encode("utf-8")
            return default

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer(override=bad_block_id) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            v1_row = next(r for r in report["cases"] if r["id"] == "example-v1")
            self.assertFalse(v1_row["passed"])
            self.assertIn("does not start with 'ts.'", v1_row["reason"])

    def test_nondeterministic_second_response_fails(self) -> None:
        seen_compile: set[str] = set()

        def nondet_compile(path: str, body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            if path == "/v1/compile":
                if body in seen_compile:
                    return default[0], default[1] + b"\n"
                seen_compile.add(body)
            return default

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer(override=nondet_compile) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            v1_row = next(r for r in report["cases"] if r["id"] == "example-v1")
            self.assertFalse(v1_row["passed"])
            self.assertIn("nondeterministic compile", v1_row["reason"])

        seen_assemble: set[str] = set()

        def nondet_assemble(path: str, body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            if path == "/v1/assemble" and default[0] == 200:
                if body in seen_assemble:
                    return default[0], default[1] + b"\n"
                seen_assemble.add(body)
            return default

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer(override=nondet_assemble) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            v1_row = next(r for r in report["cases"] if r["id"] == "example-v1")
            self.assertFalse(v1_row["passed"])
            self.assertIn("nondeterministic assemble", v1_row["reason"])

    def test_wrong_file_set_fails(self) -> None:
        def drop_readme(path: str, body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            if path == "/v1/assemble" and default[0] == 200:
                doc = json.loads(default[1])
                doc["files"].pop("README.md", None)
                return 200, check_bundles.canonical_json(doc).encode("utf-8")
            return default

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer(override=drop_readme) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            v1_row = next(r for r in report["cases"] if r["id"] == "example-v1")
            self.assertFalse(v1_row["passed"])
            self.assertIn("assembled file set", v1_row["reason"])

    def test_wrong_refusal_code_and_wrong_span_each_fail(self) -> None:
        def wrong_code(path: str, body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            if path == "/v1/assemble" and default[0] != 200:
                doc = json.loads(default[1])
                doc["code"] = "WRONG_CODE"
                doc["diagnostics"][0]["code"] = "WRONG_CODE"
                return default[0], check_bundles.canonical_json(doc).encode("utf-8")
            return default

        def wrong_span(path: str, body: str, default: tuple[int, bytes]) -> tuple[int, bytes]:
            if path == "/v1/assemble" and default[0] != 200:
                doc = json.loads(default[1])
                doc["diagnostics"][0]["byte_end"] += 1
                doc["diagnostics"][0]["end_column"] += 1
                return default[0], check_bundles.canonical_json(doc).encode("utf-8")
            return default

        for label, override_fn, substr in (
            ("wrong_code", wrong_code, "WRONG_CODE"),
            ("wrong_span", wrong_span, "span"),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp, FakeSomServer(
                override=override_fn
            ) as srv:
                out_dir = pathlib.Path(tmp) / "out"
                rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
                self.assertEqual(rc, 1)
                report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
                self.assertFalse(report["passed"])
                a1_row = next(r for r in report["cases"] if r["id"] == "A1")
                self.assertFalse(a1_row["passed"])
                self.assertIn(substr, a1_row["reason"])

    def test_refused_ports_non_loopback_and_existing_out_exit_2_before_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeSomServer() as srv:
            base = pathlib.Path(tmp)
            for idx, bad_url in enumerate(
                (
                    "http://127.0.0.1:8765",
                    "http://localhost:18765",
                    "http://192.0.2.1:19000",
                    "http://example.com:19000",
                )
            ):
                out_dir = base / f"refused-{idx}"
                rc, _, err = self._run_main(["--url", bad_url, "--out", str(out_dir)])
                self.assertEqual(rc, 2, msg=bad_url)
                self.assertIn("refused:", err)
                self.assertFalse(out_dir.exists())

            existing_out = base / "already_exists"
            existing_out.mkdir()
            rc, _, err = self._run_main(["--url", srv.url, "--out", str(existing_out)])
            self.assertEqual(rc, 2)
            self.assertIn("refused:", err)
            self.assertEqual(srv.request_count, 0)

    def test_legacy_bundle_differs_between_two_compiles_fails(self) -> None:
        seen_legacy_compile: set[str] = set()

        def nondet_legacy_compile(
            path: str, body: str, default: tuple[int, bytes]
        ) -> tuple[int, bytes]:
            if path == "/v1/compile":
                req = json.loads(body)
                if (
                    req.get("target") == "typescript"
                    and isinstance(req.get("prompt"), dict)
                    and req["prompt"].get("package") == "fake_legacy_in_scope"
                ):
                    if body in seen_legacy_compile:
                        return default[0], default[1] + b"\n"
                    seen_legacy_compile.add(body)
            return default

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer(
            override=nondet_legacy_compile
        ) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            leg_row = next(r for r in report["cases"] if r["id"] == "legacy100-0000")
            self.assertFalse(leg_row["passed"])
            self.assertIn("nondeterministic compile", leg_row["reason"])

    def test_name_case_typed_json_bundle_differs_from_cnl_bundle_fails(self) -> None:
        def tamper_tn01_json(
            path: str, body: str, default: tuple[int, bytes]
        ) -> tuple[int, bytes]:
            if path == "/v1/compile":
                req = json.loads(body)
                if (
                    req.get("format") == "som.prompt-json.v1"
                    and req.get("target") == "typescript"
                    and isinstance(req.get("prompt"), dict)
                    and req["prompt"].get("package")
                    == "name_case_tn01_cnl_field_uppercase"
                ):
                    return default[0], default[1] + b"\n"
            return default

        with tempfile.TemporaryDirectory() as tmp, FakeSomServer(
            override=tamper_tn01_json
        ) as srv:
            out_dir = pathlib.Path(tmp) / "out"
            rc, _, _ = self._run_main(["--url", srv.url, "--out", str(out_dir)])
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            tn01_row = next(
                r for r in report["cases"] if r["id"] == "TN01_CNL_FIELD_UPPERCASE"
            )
            self.assertFalse(tn01_row["passed"])
            self.assertIn("CNL and JSON compile bytes differ", tn01_row["reason"])

    def test_wrong_manifest_count_and_wrong_in_scope_count_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeSomServer() as srv:
            base = pathlib.Path(tmp)
            leg_run, inv_path, _ = create_tiny_legacy_run(base)

            # 1. Default expected_manifest_products=125 fails when tiny run produces 26 products
            out_default_125 = base / "out-default-125"
            rc_125, _, _ = self._run_main(
                [
                    "--url",
                    srv.url,
                    "--out",
                    str(out_default_125),
                    "--legacy-run",
                    str(leg_run),
                ],
                expected_legacy_in_scope=1,
                expected_manifest_products=None,
                inv_path=inv_path,
            )
            self.assertEqual(rc_125, 1)
            rep_125 = json.loads(
                (out_default_125 / "report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(rep_125["passed"])
            self.assertTrue(
                any(
                    "products/manifest.json product count is 26, expected 125" in p
                    for p in rep_125["problems"]
                ),
                msg=rep_125["problems"],
            )

            # 2. Lowered expected_manifest_products parameter (e.g. 25 != 26) also fails
            out_param_25 = base / "out-param-25"
            rc_25, _, _ = self._run_main(
                [
                    "--url",
                    srv.url,
                    "--out",
                    str(out_param_25),
                    "--legacy-run",
                    str(leg_run),
                ],
                expected_legacy_in_scope=1,
                expected_manifest_products=25,
                inv_path=inv_path,
            )
            self.assertEqual(rc_25, 1)
            rep_25 = json.loads(
                (out_param_25 / "report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(rep_25["passed"])
            self.assertTrue(
                any(
                    "products/manifest.json product count is 26, expected 25" in p
                    for p in rep_25["problems"]
                ),
                msg=rep_25["problems"],
            )

            # 3. Wrong in-scope count fails
            out_wrong_in_scope = base / "out-wrong-in-scope"
            rc_in, _, _ = self._run_main(
                [
                    "--url",
                    srv.url,
                    "--out",
                    str(out_wrong_in_scope),
                    "--legacy-run",
                    str(leg_run),
                ],
                expected_legacy_in_scope=100,
                expected_manifest_products=26,
                inv_path=inv_path,
            )
            self.assertEqual(rc_in, 1)
            rep_in = json.loads(
                (out_wrong_in_scope / "report.json").read_text(encoding="utf-8")
            )
            self.assertFalse(rep_in["passed"])
            self.assertTrue(
                any(
                    "legacy_in_scope job count is 1, expected 100" in p
                    for p in rep_in["problems"]
                ),
                msg=rep_in["problems"],
            )

    def test_missing_legacy_job_input_is_not_run_and_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeSomServer() as srv:
            base = pathlib.Path(tmp)
            leg_run, inv_path, in_prompt = create_tiny_legacy_run(base)
            in_sha = check_bundles.canonical_hash(in_prompt)
            (leg_run / "compile" / in_sha / "compile-json.request.json").unlink()
            (leg_run / "0000" / "compile-json.request.json").unlink()

            out_dir = base / "out"
            rc, _, _ = self._run_main(
                ["--url", srv.url, "--out", str(out_dir), "--legacy-run", str(leg_run)],
                expected_legacy_in_scope=1,
                expected_manifest_products=26,
                inv_path=inv_path,
            )
            self.assertEqual(rc, 1)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["passed"])
            self.assertEqual(report["not_run_count"], 1)
            leg_row = next(r for r in report["cases"] if r["id"] == "legacy100-0000")
            self.assertEqual(leg_row["status"], "not_run")
            self.assertFalse(leg_row["passed"])


if __name__ == "__main__":
    unittest.main()
