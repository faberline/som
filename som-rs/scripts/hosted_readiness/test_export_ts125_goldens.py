"""Synthetic TS125 source fixtures. Run on GitHub. No candidate or tools needed."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("export_ts125_goldens", HERE / "export_ts125_goldens.py")
e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e)


class TS125Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.responses = self.root / "responses"
        self.responses.mkdir()
        self.inventory = {"version": "som.typescript-output-case-inventory.v1", **{k: [] for k in e.GROUPS}}
        self.report = {"cases": []}
        self.manifest, self.products = {"products": []}, {"products": [], "summary": {"no_sampling_complete": True, "all_passed": True, "total_products": 125, "passed_products": 125}}
        self.target = []
        self.trees = {"bundles/responses": {}, "bundles/products": {}}
        for i in range(125):
            group = "public_examples" if i < 5 else "version_positive_fixtures" if i < 9 else "coverage_cases" if i < 15 else "name_cases" if i < 25 else "legacy_in_scope"
            cid = f"case-{i}"
            # Preserve two named rows with the same prompt. No row deduplication.
            prompt = {"version": "som.prompt.v1", "value": i if i != 124 else 123}
            item = {"id": cid, "prompt_sha256": e.value_sha(prompt)}
            if group == "legacy_in_scope":
                item["inventory_index"] = i + 200
            if group != "name_cases":
                item["typescript"] = "in_scope"
            self.inventory[group].append(item)
            forms = ["cnl", "json"] if 15 <= i < 21 else ["json"] if 21 <= i < 25 else ["json", "cnl"]
            bundle = {"version": "som.dsl-bundle.v2", "target": "typescript", "prompt": prompt, "dsl": {"version": "fixture", "files": []}, "assets": {"backend": {"id": "fixture.backend", "sha256": "a" * 64}}}
            bundle["hashes"] = {"prompt_sha256": e.value_sha(prompt), "dsl_sha256": e.value_sha(bundle["dsl"]), "payload_sha256": e.value_sha(bundle)}
            for form in forms:
                for attempt in (1, 2):
                    self.store(f"bundle_checks__{cid}__compile_{form}_{attempt}.json", {"status": "ok", "bundle": bundle})
            files = {f"src/file{j}.ts": f"fixture secret sentinel {i}:{j}\n" for j in range(11)}
            for attempt in (1, 2):
                assembled_sha = self.store(f"bundle_checks__{cid}__assemble_{attempt}.json", {"status": "ok", "files": files})
            file_hashes = {k: e.sha(v.encode()) for k, v in files.items()}
            for name, digest in file_hashes.items():
                self.trees["bundles/products"][cid + "/" + name] = digest
            request_sha = e.sha((cid + ":first:" + forms[0]).encode())
            row = {"id": cid, "section": "bundle_checks", "forms": forms, "single_form": None if len(forms) == 2 else "only json form exists in target-examples.json name_cases", "request_sha256": request_sha, "http_status": 200, "response_sha256": assembled_sha, "response_file": f"responses/bundle_checks__{cid}__assemble_1.json"}
            if group == "legacy_in_scope":
                row["inventory_index"] = item["inventory_index"]
            self.report["cases"].append(row)
            self.manifest["products"].append({"id": cid, "product": str(self.root / "products" / cid), "prompt": prompt})
            self.products["products"].append({"id": cid, "version": prompt["version"], "passed": True, "files_sha256": e.value_sha(file_hashes), "failed_gates": [], "not_run_gates": [], "gate_statuses": {g: "passed" for g in e.GATES}})
            if i < 15:
                self.target.append({"row_index": len(self.target), "section": "in_scope", "id": cid + "-cnl-typescript", "form": "cnl", "http_status": 200, "request_sha256": e.sha((cid + ":cnl").encode()), "response_sha256": e.sha(self.raw(cid, "cnl"))})
        self.bundle = json.loads(self.raw("case-0", "json"))["bundle"]

    def store(self, name, value):
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
        (self.responses / name).write_bytes(raw)
        self.trees["bundles/responses"][name] = e.sha(raw)
        return e.sha(raw)

    def raw(self, cid, form, attempt=1):
        return (self.responses / f"bundle_checks__{cid}__compile_{form}_{attempt}.json").read_bytes()

    def rows(self):
        return e.case_rows(self.inventory, self.report, self.manifest, self.products, self.target, self.root, self.trees)[0]

    def test_01_complete_named_scope_retains_forms_order_duplicates_and_no_bodies(self):
        rows = self.rows()
        self.assertEqual([r["id"] for r in rows], [f"case-{i}" for i in range(125)])
        self.assertEqual(sum(len(r["compile"]) for r in rows), 246)
        self.assertEqual(sum(op["request_sha256"] is not None for r in rows for op in r["compile"]), 140)
        self.assertEqual(sum(r["product"]["file_count"] for r in rows), 1375)
        self.assertEqual(rows[-1]["prompt_sha256"], rows[-2]["prompt_sha256"])
        self.assertNotEqual(rows[-1]["id"], rows[-2]["id"])
        self.assertNotIn("fixture secret sentinel", json.dumps(rows))
        self.assertEqual(rows[15]["forms"], ["cnl", "json"])
        self.assertEqual(rows[21]["forms"], ["json"])

    def test_02_reordered_report_or_manifest_is_not_set_equivalence(self):
        for doc in [self.report["cases"], self.manifest["products"], self.products["products"]]:
            doc[0], doc[1] = doc[1], doc[0]
            with self.assertRaisesRegex(e.old.Refusal, "ordered identities"):
                self.rows()
            doc[0], doc[1] = doc[1], doc[0]

    def test_03_missing_or_renamed_legacy_row_blocks_scope(self):
        self.inventory["legacy_in_scope"].pop()
        with self.assertRaisesRegex(e.old.Refusal, "scope"):
            self.rows()

    def test_04_compile_response_same_values_different_bytes_loses_receipt_binding(self):
        path = self.responses / "bundle_checks__case-0__compile_json_1.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(e.old.Refusal, "receipt tree binding"):
            self.rows()

    def test_05_rehashed_tree_cannot_hide_invalid_bundle_canonical_hash(self):
        doc = json.loads(self.raw("case-0", "json"))
        doc["bundle"]["prompt"]["value"] = 999
        for n in (1, 2):
            self.store(f"bundle_checks__case-0__compile_json_{n}.json", doc)
        with self.assertRaisesRegex(e.old.Refusal, "canonical hash"):
            self.rows()

    def test_06_cnl_and_json_reply_association_cannot_differ(self):
        doc = json.loads(self.raw("case-1", "json"))
        for n in (1, 2):
            self.store(f"bundle_checks__case-0__compile_cnl_{n}.json", doc)
        self.target[0]["response_sha256"] = e.sha(self.raw("case-0", "cnl"))
        with self.assertRaisesRegex(e.old.Refusal, "CNL and JSON"):
            self.rows()

    def test_07_repeated_compile_or_assemble_must_match_raw_bytes(self):
        path = self.responses / "bundle_checks__case-0__compile_json_2.json"
        raw = path.read_bytes() + b" "
        path.write_bytes(raw)
        self.trees["bundles/responses"][path.name] = e.sha(raw)
        with self.assertRaisesRegex(e.old.Refusal, "repeat compile"):
            self.rows()

    def test_08_extra_product_file_is_not_an_approved_complete_map(self):
        self.trees["bundles/products"]["case-0/unrecorded.ts"] = "a" * 64
        with self.assertRaisesRegex(e.old.Refusal, "retained product"):
            self.rows()

    def test_09_mutated_product_bytes_or_aggregate_is_refused(self):
        self.products["products"][0]["files_sha256"] = "b" * 64
        with self.assertRaisesRegex(e.old.Refusal, "complete file hash map"):
            self.rows()

    def test_10_unrun_historical_gate_cannot_be_exported_as_passed(self):
        self.products["products"][0]["gate_statuses"]["parity"] = "unrun"
        with self.assertRaisesRegex(e.old.Refusal, "gates incomplete"):
            self.rows()

    def test_11_two_direct_request_authorities_must_agree(self):
        row = self.report["cases"][0]
        self.target.append({"row_index": 20, "section": "in_scope", "id": "case-0-json-typescript", "form": "json", "http_status": 200, "request_sha256": "f" * 64, "response_sha256": e.sha(self.raw("case-0", "json"))})
        with self.assertRaisesRegex(e.old.Refusal, "request hashes disagree"):
            self.rows()

    def test_12_compile_hash_is_never_relabelled_as_assembly_or_missing_form(self):
        rows = self.rows()
        self.assertIsNone(rows[0]["assemble"]["request_sha256"])
        self.assertIsNone(rows[25]["compile"][1]["request_sha256"])
        self.assertEqual(rows[25]["compile"][1]["request_hash_authority"], "unavailable")
        self.assertIsNone(rows[15]["compile"][1]["source_reconstructed_request_sha256"])
        self.assertIn("unrun", rows[15]["compile"][1]["source_reconstruction"])

    def test_13_target_history_preserves_repeated_ids_and_exact_statuses(self):
        rows = []
        for i in range(791):
            status = 200 if i < 190 else 400 if i < 202 else 422
            sha = self.store(f"history-{i}.json", {"status": "ok"} if status == 200 else {"status": "error", "code": "UNSUPPORTED_TARGET"})
            rows.append({"id": "repeated-cnl-typescript", "section": "in_scope", "http_status": status, "request_sha256": "c" * 64, "response_sha256": sha, "response_file": f"responses/history-{i}.json"})
        out = e.target_rows({"cases": rows}, self.root, {}, {})
        self.assertEqual(len(out), 791)
        self.assertEqual(out[-1]["row_index"], 790)
        self.assertEqual(out[0]["id"], out[1]["id"])
        self.assertTrue(all(r["endpoint"] == "/v1/compile" and r["format"] == "som.cnl.v1" for r in out))
        rows[-1]["http_status"] = 200
        with self.assertRaisesRegex(e.old.Refusal, "status counts"):
            e.target_rows({"cases": rows}, self.root, {}, {})

    def test_14_raw_negative_form_requires_matching_frozen_request_hash(self):
        source = {"form": "json", "body": '{"format":"som.prompt-json.v1","target":null}'}
        row = {"section": "negative_cases", "id": "TT1", "request_sha256": e.sha(source["body"].encode())}
        self.assertEqual(e.target_form(row, {}, {"TT1": source}), "json")
        row["request_sha256"] = "a" * 64
        with self.assertRaisesRegex(e.old.Refusal, "source association"):
            e.target_form(row, {}, {"TT1": source})

    def test_15_assemble_refusals_keep_real_assemble_request_hash_and_order(self):
        rows = []
        for i in range(1, 13):
            digest = self.store(f"refusal-{i}.json", {"status": "error", "code": "INVALID_BUNDLE"})
            rows.append({"id": f"A{i}", "section": "assemble_refusals", "http_status": 400, "request_sha256": e.sha(str(i).encode()), "response_sha256": digest, "response_file": f"responses/refusal-{i}.json", "expected": {"http_status": 400, "code": "INVALID_BUNDLE", "prompt_pointer": "/bundle"}})
        out = e.refusal_rows({"cases": rows}, self.root)
        self.assertEqual(out[-1]["id"], "A12")
        self.assertTrue(all(r["endpoint"] == "/v1/assemble" for r in out))
        rows[0], rows[1] = rows[1], rows[0]
        with self.assertRaisesRegex(e.old.Refusal, "ordered scope"):
            e.refusal_rows({"cases": rows}, self.root)

    def test_16_frozen_public_doc_requires_independent_accepted_source_manifest(self):
        repo = self.root / "repo"
        path = repo / e.INVENTORY
        path.parent.mkdir(parents=True)
        raw = b'{"version":"fixture"}'
        path.write_bytes(raw)
        class Inputs:
            def doc(self, role):
                return {"files": {e.INVENTORY: "a" * 64}}
        with patch.dict(e.SOURCE_PINS, {e.INVENTORY: e.sha(raw)}):
            with self.assertRaisesRegex(e.old.Refusal, "accepted source binding"):
                e.source_doc(Inputs(), repo, e.INVENTORY)

    def test_17_directory_receipt_covers_bytes_paths_extra_files_and_symlinks(self):
        root = self.root / "tree"
        root.mkdir()
        (root / "a").write_bytes(b'{"v":1}')
        original = e.tree_digest(e.tree_snapshot(root))
        (root / "a").write_bytes(b'{ "v": 1 }')
        self.assertNotEqual(e.tree_digest(e.tree_snapshot(root)), original)
        (root / "link").symlink_to(root / "a")
        with self.assertRaisesRegex(e.old.Refusal, "symlink"):
            e.tree_snapshot(root)

    def test_18_separate_raw_canary_request_must_match_the_accepted_case_bundle(self):
        documents = {"typescript_canaries": []}
        raws = {}
        bundlemap = {"case-0": self.bundle, "case-1": self.bundle}
        for ci, cid in enumerate(bundlemap):
            case = {"name": cid, "steps": []}
            for si, name in enumerate(["compile-json", "compile-cnl", "assemble"]):
                request = {"bundle": self.bundle} if name == "assemble" else {"format": e.FORMATS[name.removeprefix("compile-")], "target": "typescript", **({"prompt": self.bundle["prompt"]} if name == "compile-json" else {"source": "synthetic CNL"})}
                reply = {"files": {}, "status": "ok"} if name == "assemble" else {"bundle": self.bundle, "status": "ok"}
                step = {"name": name, "kind": "http", "path": "/v1/assemble" if name == "assemble" else "/v1/compile"}
                for side, value in [("request", request), ("response", reply)]:
                    raw = e.canonical(value)
                    role = f"accepted canary typescript_canaries:{ci}:{si}:{side}"
                    raws[role] = raw
                    step[side] = {"sha256": e.sha(raw)}
                case["steps"].append(step)
            documents["typescript_canaries"].append(case)
        class Inputs:
            def raw(self, role):
                return raws[role]
            def doc(self, role):
                return documents
        rows = e.canary_rows(Inputs(), bundlemap)
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r["raw_request_file_verified"] for r in rows))
        self.assertTrue(all(r["http_status"] is None for r in rows))
        bad = copy.deepcopy(self.bundle)
        bad["prompt"]["value"] = 12345
        raw = e.canonical({"bundle": bad})
        raws["accepted canary typescript_canaries:0:2:request"] = raw
        documents["typescript_canaries"][0]["steps"][2]["request"]["sha256"] = e.sha(raw)
        with self.assertRaisesRegex(e.old.Refusal, "assemble bundle association"):
            e.canary_rows(Inputs(), bundlemap)

    def test_19_old_negative_source_uses_one_exact_regular_archive_member(self):
        raw = b'{"cases":[]}'
        def archive_bytes(duplicate=False):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                for _ in range(2 if duplicate else 1):
                    member = tarfile.TarInfo(e.NEGATIVE_CASES)
                    member.size = len(raw)
                    archive.addfile(member, io.BytesIO(raw))
            return buffer.getvalue()
        archive = archive_bytes()
        class Inputs:
            def raw(self, role):
                return archive
            def doc(self, role):
                return {"files": {e.NEGATIVE_CASES: e.sha(raw)}}
        with patch.object(e, "BEFORE_NEGATIVE_SHA256", e.sha(raw)):
            self.assertEqual(e.before_negative_doc(Inputs()), {"cases": []})
            archive = archive_bytes(duplicate=True)
            with self.assertRaisesRegex(e.old.Refusal, "member differs"):
                e.before_negative_doc(Inputs())


if __name__ == "__main__":
    unittest.main()
