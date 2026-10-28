"""TypeScript collector fixtures. Run on GitHub, never on the local host."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("react_typescript_baseline_collector", HERE / "check_typescript_baseline.py")
collector = importlib.util.module_from_spec(SPEC)
# These fixtures exercise HTTP collection only. No acceptance helper or service runs.
with mock.patch.dict(sys.modules, {"check_bundles": types.ModuleType("check_bundles")}):
    with mock.patch.object(sys, "path", list(sys.path)):
        SPEC.loader.exec_module(collector)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


class TypeScriptBaselineCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="som-ts-baseline-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name)
        self.qa = "http://127.0.0.1:19001"
        self.baseline = "http://127.0.0.1:19002"
        self.prompt = {"version": "som.prompt.v1", "project": "fixture"}
        self.reply = {
            "status": "ok",
            "bundle": {
                "version": "som.dsl-bundle.v2",
                "target": "typescript",
                "prompt": self.prompt,
                "assets": {"backend": {"id": "backend", "sha256": "a" * 64},
                           "target_contract": {"id": "contract", "sha256": "b" * 64},
                           "catalog": {"id": "catalog", "sha256": "c" * 64}},
                "hashes": {"payload_sha256": "d" * 64, "prompt_sha256": "e" * 64},
                "dsl": {"files": [{"path": "src/main.ts", "blocks": []}]},
            },
        }
        other = copy.deepcopy(self.reply)
        other["bundle"]["assets"]["backend"]["sha256"] = "1" * 64
        other["bundle"]["assets"]["target_contract"]["sha256"] = "2" * 64
        other["bundle"]["hashes"]["payload_sha256"] = "3" * 64
        self.responses = {
            (self.qa, "/v1/compile"): (200, b" \n" + encoded(self.reply) + b"\n"),
            (self.baseline, "/v1/compile"): (200, encoded(other)),
            (self.qa, "/v1/assemble"): (200, encoded({"status": "ok", "files": {"src/main.ts": "// café\n"}})),
            (self.baseline, "/v1/assemble"): (200, encoded({"status": "ok", "files": {"src/main.ts": "// café\n"}})),
        }
        self.sent = []

    def case(self, form="json"):
        request = {"format": "som.prompt-json.v1", "prompt": self.prompt, "target": "typescript"}
        if form == "cnl":
            request = {"format": "som.cnl.v1", "source": "Project café.\n", "target": "typescript"}
        return {"id": "fixture-case", "group": "fixtures", "requests": [(form, encoded(request).decode())]}

    def post(self, url, route, body):
        self.sent.append((url, route, body.encode("utf-8")))
        value = self.responses[(url, route)]
        if isinstance(value, Exception):
            raise value
        return value

    def collect(self, case=None):
        with mock.patch.object(collector, "post", side_effect=self.post):
            return collector.check_case(self.qa, self.baseline, self.case() if case is None else case, self.out)

    def test_allowed_compile_differences_and_identical_files_keep_exact_raw_exchanges(self):
        case = self.case()
        row = self.collect(case)
        self.assertEqual(row["status"], "passed")
        self.assertIs(row["passed"], True)
        self.assertEqual(row["id"], case["id"])
        record = row["requests"][0]
        self.assertEqual(record["form"], "json")
        request = (self.out / record["request_file"]).read_bytes()
        self.assertEqual(request, case["requests"][0][1].encode())
        self.assertEqual(digest(request), record["body_sha256"])
        self.assertEqual([body for _, route, body in self.sent if route == "/v1/compile"], [request, request])
        for side, url in (("qa", self.qa), ("baseline", self.baseline)):
            for operation in ("compile", "assemble"):
                prefix = side if operation == "compile" else f"{side}_assemble"
                path = Path(record[f"{prefix}_response_file"])
                self.assertFalse(path.is_absolute())
                raw = (self.out / path).read_bytes()
                self.assertEqual(raw, self.responses[(url, f"/v1/{operation}")][1])
                self.assertEqual(record[f"{prefix}_sha256"], digest(raw))
                self.assertEqual(record[f"{prefix}_status"], 200)
                if operation == "assemble":
                    sent = next(body for sent_url, route, body in self.sent
                                if sent_url == url and route == "/v1/assemble")
                    self.assertEqual((self.out / record[f"{prefix}_request_file"]).read_bytes(), sent)
                    self.assertEqual(record[f"{prefix}_request_sha256"], digest(sent))
                    compiled = json.loads(self.responses[(url, "/v1/compile")][1])
                    self.assertEqual(json.loads(sent), {"bundle": compiled["bundle"]})
        expected_files = {"src/main.ts": digest("// café\n".encode())}
        self.assertEqual(record["files_sha256"], expected_files)
        self.assertEqual(record["baseline_files_sha256"], expected_files)
        self.assertEqual(set(record["differing_members"]),
                         {"assets.backend", "assets.target_contract", "hashes.payload_sha256"})

    def test_compile_change_outside_the_allowed_members_fails_and_retains_response(self):
        changed = copy.deepcopy(self.reply)
        changed["bundle"]["assets"]["catalog"]["sha256"] = "f" * 64
        raw = encoded(changed)
        self.responses[(self.baseline, "/v1/compile")] = (200, raw)
        row = self.collect()
        self.assertEqual(row["status"], "failed")
        self.assertIs(row["passed"], False)
        self.assertTrue(any("outside the allowed" in problem for problem in row["problems"]))
        self.assertEqual((self.out / row["requests"][0]["baseline_response_file"]).read_bytes(), raw)

    def test_different_assembled_file_bytes_fail(self):
        self.responses[(self.baseline, "/v1/assemble")] = (200, encoded({"status": "ok", "files": {"src/main.ts": "// cafe\n"}}))
        row = self.collect()
        self.assertEqual(row["status"], "failed")
        self.assertIs(row["passed"], False)
        self.assertTrue(any("product files differ" in problem for problem in row["problems"]))
        record = row["requests"][0]
        self.assertNotEqual(record["files_sha256"], record["baseline_files_sha256"])

    def test_assemble_failure_retains_the_exact_http_error(self):
        raw = b'{"status":"error","detail":"fixture refusal"}\n'
        self.responses[(self.baseline, "/v1/assemble")] = (422, raw)
        row = self.collect()
        self.assertEqual(row["status"], "failed")
        self.assertIs(row["passed"], False)
        record = row["requests"][0]
        self.assertEqual(record["baseline_assemble_status"], 422)
        self.assertEqual((self.out / record["baseline_assemble_response_file"]).read_bytes(), raw)
        self.assertNotIn("baseline_files_sha256", record)

    def test_non_json_compile_response_fails_without_losing_raw_bytes(self):
        raw = b"<html>fixture error</html>\xff"
        self.responses[(self.qa, "/v1/compile")] = (200, raw)
        row = self.collect()
        self.assertEqual(row["status"], "failed")
        self.assertIs(row["passed"], False)
        self.assertEqual((self.out / row["requests"][0]["qa_response_file"]).read_bytes(), raw)
        self.assertTrue(all(route == "/v1/compile" for _, route, _ in self.sent))

    def test_malformed_assemble_files_fail_with_raw_response(self):
        raw = encoded({"status": "ok", "files": {"src/main.ts": 7}})
        self.responses[(self.qa, "/v1/assemble")] = (200, raw)
        row = self.collect()
        self.assertEqual(row["status"], "failed")
        self.assertIs(row["passed"], False)
        self.assertEqual((self.out / row["requests"][0]["qa_assemble_response_file"]).read_bytes(), raw)

    def test_connection_failure_is_not_run_and_keeps_available_response(self):
        self.responses[(self.qa, "/v1/compile")] = OSError("fixture connection failure")
        row = self.collect()
        self.assertEqual(row["status"], "not_run")
        self.assertIs(row["passed"], False)
        record = row["requests"][0]
        self.assertIn("not_run_errors", record)
        self.assertNotIn("qa_status", record)
        self.assertTrue((self.out / record["baseline_response_file"]).is_file())

    def test_missing_case_source_has_explicit_false_passed(self):
        case = self.case()
        case["not_run_error"] = "fixture source is missing"
        row = self.collect(case)
        self.assertEqual(row["status"], "not_run")
        self.assertIs(row["passed"], False)
        self.assertEqual(row["requests"], [])
        self.assertEqual(self.sent, [])

    def test_empty_case_requests_cannot_pass(self):
        case = self.case()
        case["requests"] = []
        row = self.collect(case)
        self.assertEqual(row["status"], "not_run")
        self.assertIs(row["passed"], False)
        self.assertEqual(self.sent, [])

    def test_cnl_request_keeps_its_label_and_adds_the_derived_json_request(self):
        case = self.case("cnl")
        case["derive_json_from_cnl_bundle"] = True
        row = self.collect(case)
        self.assertIs(row["passed"], True)
        self.assertEqual([record["form"] for record in row["requests"]], ["cnl", "json"])
        source = json.loads((self.out / row["requests"][0]["request_file"]).read_bytes())
        derived = json.loads((self.out / row["requests"][1]["request_file"]).read_bytes())
        self.assertEqual(source, json.loads(case["requests"][0][1]))
        self.assertEqual(derived, {"format": "som.prompt-json.v1", "prompt": self.prompt, "target": "typescript"})


if __name__ == "__main__":
    unittest.main()
