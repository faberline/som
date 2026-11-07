"""Synthetic CNL companion checks. Execute only on GitHub runners."""
import hashlib
import importlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

if os.environ.get("GITHUB_ACTIONS") != "true":
    raise RuntimeError("GitHub tests only")

SUBJECT = Path(__file__).with_name("run_experimental_native_cnl.py")
p = ModuleType("run_experimental_native_cnl")
p.__file__, p.__cached__, p.__package__ = str(SUBJECT), None, ""
sys.modules[p.__name__] = p
exec(compile(SUBJECT.read_bytes(), str(SUBJECT), "exec", dont_inherit=True), p.__dict__)
REPO = SUBJECT.parents[3]
REVISION = "1" * 40


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class FixtureSources(p.Sources):
    def __init__(self, root, bodies=None):
        self.fixture = {path: b"VALUE = 'bound-source'\n" for path in [p.SELF, *p.MODULES.values()]}
        self.fixture.update(bodies or {})
        for name, raw in self.fixture.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        super().__init__(root, REVISION)

    def git(self, *args):
        if args[:2] == ("rev-parse", "--verify"):
            return (REVISION + "\n").encode()
        if args == ("rev-parse", "--show-toplevel"):
            return (str(self.root) + "\n").encode()
        if args[0] == "ls-tree":
            name = args[-1]
            oid = hashlib.sha1(self.fixture[name]).hexdigest()
            return ("100644 blob " + oid + "\t" + name + "\0").encode()
        if args[:2] == ("cat-file", "-s"):
            raw = next(raw for raw in self.fixture.values() if hashlib.sha1(raw).hexdigest() == args[-1])
            return (str(len(raw)) + "\n").encode()
        if args[:2] == ("cat-file", "blob"):
            return next(raw for raw in self.fixture.values() if hashlib.sha1(raw).hexdigest() == args[-1])
        raise AssertionError(args)


class ByteBindingTests(unittest.TestCase):
    def setUp(self):
        namespace = patch.dict(sys.modules)
        namespace.start()
        self.addCleanup(namespace.stop)
        for name in p.MODULES:
            sys.modules.pop(name, None)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_valid_matching_timestamp_pyc_cannot_supply_helper_code(self):
        sources = FixtureSources(self.root)
        path = self.root / p.MODULES["export_goldens"]
        raw = path.read_bytes()
        cache = Path(importlib.util.cache_from_source(str(path)))
        cache.parent.mkdir()
        stamp = int(path.stat().st_mtime)
        malicious = compile("raise RuntimeError('cached code executed')", str(path), "exec")
        cache.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, stamp, len(raw)) + marshal.dumps(malicious))
        self.assertEqual(cache.read_bytes()[8:16], struct.pack("<II", stamp, len(raw)))
        loader = p.BoundModules(sources)
        with loader.installed():
            for name in p.MODULES:
                module = importlib.import_module(name)
                self.assertEqual(module.VALUE, "bound-source")
                self.assertIsNone(module.__cached__)
                self.assertIs(module.__spec__.loader, loader)
        self.assertFalse(set(p.MODULES) & set(sys.modules))

    def test_loaded_namespace_is_refused_before_any_helper_exec(self):
        sources = FixtureSources(self.root)
        with patch.dict(sys.modules, {"export_goldens": ModuleType("export_goldens")}):
            with self.assertRaisesRegex(p.Refusal, "already loaded"):
                with p.BoundModules(sources).installed():
                    self.fail("preloaded helper namespace entered")

    def test_bound_source_mutation_is_refused(self):
        sources = FixtureSources(self.root)
        (self.root / p.MODULES["export_goldens"]).write_bytes(b"VALUE = 'dirty-source'\n")
        with self.assertRaisesRegex(p.Refusal, "source changed"):
            sources.verify()

    def test_helper_exception_does_not_leave_cached_modules(self):
        sources = FixtureSources(self.root, {p.MODULES["export_goldens"]: b"raise RuntimeError('fixture failure')\n"})
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with p.BoundModules(sources).installed():
                importlib.import_module("export_goldens")
        self.assertNotIn("export_goldens", sys.modules)

    def test_regular_reads_refuse_symlinks_and_hardlinks(self):
        original = self.root / "original"
        original.write_bytes(b"fixture")
        linked = self.root / "linked"
        linked.symlink_to(original)
        with self.assertRaisesRegex(p.Refusal, "linked"):
            p.regular_bytes(linked)
        linked.unlink()
        os.link(original, linked)
        with self.assertRaisesRegex(p.Refusal, "linked"):
            p.regular_bytes(original)

    def test_size_limit_is_checked_before_read(self):
        path = self.root / "large"
        with path.open("wb") as stream:
            stream.truncate(p.MAX_RAW + 1)
        with patch.object(Path, "open", side_effect=AssertionError("oversized file read")):
            with self.assertRaisesRegex(p.Refusal, "oversized"):
                p.regular_bytes(path)


class HelperTests(unittest.TestCase):
    def setUp(self):
        namespace = patch.dict(sys.modules)
        namespace.start()
        self.addCleanup(namespace.stop)
        for name in p.MODULES:
            sys.modules.pop(name, None)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.sources = FixtureSources(self.root / "source")
        # Use the real bound helper bytes only. No helper startup import uses sys.path.
        self.sources.fixture = {path: (REPO / path).read_bytes() for path in [p.SELF, *p.MODULES.values()]}
        for name, raw in self.sources.fixture.items():
            (self.sources.root / name).write_bytes(raw)
        self.sources.raw.clear()
        self.sources.pins.clear()
        self.loader = p.BoundModules(self.sources)
        self.context = self.loader.installed()
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.driver = importlib.import_module("run_experimental_native_compile")
        importlib.import_module("complete_rust_inputs")

    def oracle(self, replace_policy=None):
        raw = {name: (REPO / name).read_bytes() for name in (p.POLICY, p.NEGATIVE, p.PHASE2)}
        if replace_policy:
            replace_policy(raw)
        checker = SimpleNamespace(repo=SimpleNamespace(read=raw.__getitem__))
        return p.RefusalOracle(checker, self.driver)

    def native(self, identity):
        rows = json.loads((REPO / p.NEGATIVE).read_bytes())["cases"]
        row = next(row for row in rows if row["id"] == identity)
        return {"target": "rust", "identity": {"phase": "phase2", "id": identity}, "form": row["form"], "raw": row["body"].encode()}

    def test_original_RS12_CNL_refusal_matches_every_frozen_field(self):
        job = self.native("RS12_CNL_V3_FILTER_LIMIT")
        status, raw, diagnostic = self.oracle().expected(job)
        self.assertEqual(status, 400)
        self.assertEqual(diagnostic["byte_start"], 376)
        self.assertEqual(diagnostic["byte_end"], 381)
        self.assertEqual(json.loads(raw)["diagnostics"], [diagnostic])
        self.assertEqual(raw, self.driver.compact(self.driver.sorted_objects(json.loads(raw))))

    def test_original_RS13_JSON_refusal_uses_its_raw_token_span(self):
        status, _, diagnostic = self.oracle().expected(self.native("RS13_JSON_V3_FILTER_OFFSET"))
        self.assertEqual(status, 400)
        self.assertEqual((diagnostic["byte_start"], diagnostic["byte_end"]), (456, 464))
        self.assertEqual(diagnostic["prompt_pointer"], "/prompt/query/filter_field")

    def test_RS13_CNL_companion_locates_source_not_envelope(self):
        job = self.native("RS13_JSON_V3_FILTER_OFFSET")
        source = '台灣 line\nFilter exactly on field offset.\n'
        job.update(form="cnl", raw=compact({"format": "som.cnl.v1", "source": source, "target": "rust"}))
        status, _, diagnostic = self.oracle().expected(job)
        before = source[:source.index("offset")]
        self.assertEqual(status, 400)
        self.assertEqual(diagnostic["byte_start"], len(before.encode()))
        self.assertEqual((diagnostic["line"], diagnostic["column"]), (2, 25))
        self.assertEqual(diagnostic["prompt_pointer"], "/query/filter_field")
        self.assertIn("Rename it", diagnostic["allowed_form"])

    def test_original_native_request_reencoding_is_refused(self):
        job = self.native("RS13_JSON_V3_FILTER_OFFSET")
        job["raw"] = json.dumps(json.loads(job["raw"]), indent=2).encode()
        with self.assertRaisesRegex(p.Refusal, "native original request bytes"):
            self.oracle().expected(job)

    def test_missing_or_duplicate_CNL_location_is_refused(self):
        job = self.native("RS13_JSON_V3_FILTER_OFFSET")
        for source in ("No filter here.\n", "Filter exactly on field offset.\n" * 2):
            with self.subTest(source=source):
                job.update(form="cnl", raw=compact({"format": "som.cnl.v1", "source": source, "target": "rust"}))
                with self.assertRaisesRegex(p.Refusal, "absent or ambiguous"):
                    self.oracle().expected(job)

    def test_old422_scope_rule_cannot_replace_current400(self):
        def change(raw):
            value = json.loads(raw[p.PHASE2])
            row = next(row for row in value["rows"] if row["id"] == "RS12_CNL_V3_FILTER_LIMIT")
            row["phase2_expected"]["http_status"] = 422
            raw[p.PHASE2] = compact(value)
        with self.assertRaisesRegex(p.Refusal, "current phase2 collision rule"):
            self.oracle(change)

    def test_only_two_frozen_collisions_have_oracle_authority(self):
        job = self.native("RS13_JSON_V3_FILTER_OFFSET")
        job["identity"]["id"] = "unrelated-case"
        with self.assertRaisesRegex(p.Refusal, "no authority"):
            self.oracle().expected(job)

    def plan_fixture(self):
        typed, contents = [], {}
        by_cohort = {"python_public": {}, "python_legacy": {}}
        for source, count in (("public", 127), ("legacy", 544)):
            cohort = "python_" + source
            for index in range(count):
                identity = f"{source}-named-{index:04d}"
                cnl = f"Synthetic named CNL input {identity}.\n"
                raw = compact({"format": "som.cnl.v1", "source": cnl})
                path = f"{cohort}/{index:04d}/compile-cnl.request.raw"
                contents[path] = raw
                by_cohort[cohort][identity] = {"cnl": cnl, "locators": {"compile-cnl": {"path": path, "raw_sha256": p.digest(raw)}}}
                for variant in ("none", "python"):
                    typed.append({"target": "python", "identity": {"source": source, "id": identity, "variant": variant}})
        negatives = [row for row in json.loads((REPO / p.NEGATIVE).read_bytes())["cases"] if row["id"].startswith("RS")]
        completed = {"cohorts": {"rust_phase1": [], "rust_phase2": []}}
        rust = self.root / "paired"
        for phase, count in (("phase1", 115), ("phase2", 584)):
            cohort = "rust_" + phase
            for index in range(count):
                native = negatives[index - 562] if phase == "phase2" and index >= 562 else None
                identity = native["id"] if native else f"{phase}-named-{index:04d}"
                prompt = (json.loads(native["body"])["prompt"] if native and native["form"] == "json"
                          else {"version": "som.prompt.v1", "case": identity})
                cnl = (json.loads(native["body"])["source"] if native and native["form"] == "cnl"
                       else f"Synthetic named Rust CNL {identity}.\n")
                cnl_raw = native["body"].encode() if native and native["form"] == "cnl" else compact({"format": "som.cnl.v1", "source": cnl, "target": "rust"})
                json_raw = native["body"].encode() if native and native["form"] == "json" else compact({"format": "som.prompt-json.v1", "prompt": prompt, "target": "rust"})
                directory = rust / cohort / f"{index:04d}"
                directory.mkdir(parents=True)
                requests = {}
                for form, raw in (("json", json_raw), ("cnl", cnl_raw)):
                    path = directory / (form + ".request.raw")
                    path.write_bytes(raw)
                    requests[form] = {"path": path.relative_to(rust).as_posix(), "raw_sha256": p.digest(raw)}
                row = {"id": identity, "expected_prompt_sha256": self.driver.value_sha(prompt), "cnl_source_sha256": p.digest(cnl.encode()), "requests": requests}
                if native:
                    row["native_form"] = native["form"]
                completed["cohorts"][cohort].append(row)
                typed.append({"target": "rust", "identity": {"phase": phase, "id": identity}})
        def read(path, wanted):
            raw = contents[path]
            self.assertEqual(p.digest(raw), wanted)
            return raw
        reused = SimpleNamespace(by_cohort=by_cohort, read=read)
        proof = SimpleNamespace(reference=lambda target, identity: {"path": "typed/current.raw", "sha256": "a" * 64,
                                                                   "status": 400 if target == "rust" and identity["id"] in p.REFUSALS else 200})
        return typed, reused, completed, rust, proof

    def test_complete2050_named_companion_slots_and_native_bodies_are_preserved(self):
        inputs = self.plan_fixture()
        jobs = p.plan_jobs(self.driver, *inputs)
        self.assertEqual(len(jobs), 2050)
        self.assertEqual(dict(p.Counter(row["group"] for row in jobs)), p.EXPECTED)
        self.assertEqual([row["identity"] for row in jobs[:2041]], [row["identity"] for row in inputs[0]])
        self.assertEqual(jobs[0]["identity"]["variant"], "none")
        self.assertEqual(jobs[1]["identity"]["variant"], "python")
        self.assertNotIn("target", json.loads(jobs[0]["raw"]))
        self.assertEqual(json.loads(jobs[1]["raw"])["target"], "python")
        negatives = {row["id"]: row for row in json.loads((REPO / p.NEGATIVE).read_bytes())["cases"] if row["id"].startswith("RS")}
        originals = [row for row in jobs if row["group"] == "rust_native_json"]
        self.assertEqual(len(originals), 9)
        for row in originals:
            self.assertEqual(row["raw"], negatives[row["identity"]["id"]]["body"].encode())
        native_cnl = [row for row in jobs if row["group"] == "rust_cnl" and row["input"]["native_form"] == "cnl"]
        self.assertEqual(len(native_cnl), 13)
        for row in native_cnl:
            self.assertEqual(row["raw"], negatives[row["identity"]["id"]]["body"].encode())

    def test_missing_native_slot_cannot_reduce_complete_scope(self):
        inputs = list(self.plan_fixture())
        inputs[2]["cohorts"]["rust_phase2"].pop()
        with self.assertRaisesRegex(p.Refusal, "paired input identities"):
            p.plan_jobs(self.driver, *inputs)

    def test_complete_manifest_cannot_self_authorize_changed_CNL_bytes(self):
        inputs = list(self.plan_fixture())
        row = inputs[2]["cohorts"]["rust_phase1"][0]
        path = inputs[3] / row["requests"]["cnl"]["path"]
        raw = compact({"format": "som.cnl.v1", "source": "changed source", "target": "rust"})
        path.write_bytes(raw)
        row["requests"]["cnl"]["raw_sha256"] = p.digest(raw)
        with self.assertRaisesRegex(p.Refusal, "CNL source envelope"):
            p.plan_jobs(self.driver, *inputs)

    def test_full_typed_proof_revalidates_every_request_and_reply(self):
        root = self.root / "typed"
        root.mkdir()
        jobs, rows, calls = [], [], []
        counts = {"python": 1342, "rust": 699}
        for index in range(2041):
            target = "python" if index < 1342 else "rust"
            identity = ({"source": "public", "id": f"slot-{index}", "variant": "none"} if target == "python"
                        else {"phase": "phase2", "id": f"slot-{index}"})
            request, response = compact({"slot": index}), compact({"current": index})
            job = {"target": target, "identity": identity, "raw": request, "expected_request_sha256": p.digest(request), "expected_http_status": 200}
            row = {"index": index, "target": target, "identity": identity, "state": "passed", "expected_request_sha256": p.digest(request),
                   "current_response_sha256": p.digest(response), "http_status": 200}
            directory = root / "rows" / f"{index:04d}"
            directory.mkdir(parents=True)
            for name, raw in (("request.raw", request), ("response.raw", response), ("result.json", compact(row)),
                              ("checker.json", compact({"current_raw_sha256": p.digest(response)}))):
                (directory / name).write_bytes(raw)
            jobs.append(job);rows.append(row)
        receipt = {"tested_sha": REVISION, "state": "experimental-unsealed", "policy_state": "unsealed", "product_acceptance_passed": False,
                   "experiment_complete": True, "identity_stability_passed": True, "cleanup": {"cleanup_passed": True}, "inputs": {"fixture": "bound"},
                   "initial_identities": {}, "final_identities": {}, "actual_rows": counts, "passed": 2041, "rows": rows}
        (root / "manifest.json").write_bytes(compact(receipt))
        def verify(target, identity, status, raw):
            calls.append((target, identity, status, raw))
            return SimpleNamespace(report={"byte_compatibility_passed": True})
        checker = SimpleNamespace(verify_response=verify)
        proof = p.TypedProof(root, jobs, checker, self.driver, REVISION, {"fixture": "bound"})
        self.assertEqual(len(calls), 2041)
        self.assertEqual(len(proof.rows), 2041)
        self.assertEqual(proof.reference("rust", jobs[-1]["identity"])["index"], 2040)
        proof.verify()
        path = root / "rows/0000/response.raw"
        path.write_bytes(b"modified but self-authorized receipt flag remains true")
        with self.assertRaisesRegex(p.Refusal, "typed proof bytes changed"):
            proof.verify()

    def test_success_requires_exact_raw_bytes_not_JSON_equivalence(self):
        typed, current = b'{"bundle":{}}', b'{ "bundle": {} }'
        job = {"group": "python_cnl", "target": "python", "identity": {"source": "public", "id": "row", "variant": "none"},
               "form": "cnl", "raw": b'{}', "request_sha256": p.digest(b'{}'),
               "typed_reference": {"path": "response.raw", "sha256": p.digest(typed), "status": 200}}
        proof = SimpleNamespace(read=lambda path: typed)
        service = SimpleNamespace(call=lambda route, raw: (200, current, {"pid": 1}))
        out = self.root / "result"
        out.mkdir()
        rows = p.run_rows([job], proof, None, service, out, self.driver)
        self.assertEqual(rows[0]["state"], "failed")
        self.assertIn("exact same-candidate typed bytes", rows[0]["reason"])
        self.assertEqual((out / "rows/0000/response.raw").read_bytes(), current)

    def test_transport_failure_preserves_named_result(self):
        job = {"group": "python_cnl", "target": "python", "identity": {"source": "legacy", "id": "duplicate-named-slot", "variant": "python"},
               "form": "cnl", "raw": b'{}', "request_sha256": p.digest(b'{}')}
        def fail(*args):
            raise TimeoutError("synthetic bounded transport failure")
        out = self.root / "result"
        out.mkdir()
        rows = p.run_rows([job], None, None, SimpleNamespace(call=fail), out, self.driver)
        self.assertEqual(rows[0]["state"], "failed_transport_or_ownership")
        self.assertEqual(rows[0]["identity"], job["identity"])
        self.assertTrue((out / "rows/0000/result.json").is_file())

    def test_unavailable_service_records_unrun_without_network(self):
        job = {"group": "rust_cnl", "target": "rust", "identity": {"phase": "phase2", "id": "row"},
               "form": "cnl", "raw": b'{}', "request_sha256": p.digest(b'{}')}
        out = self.root / "result"
        out.mkdir()
        rows = p.run_rows([job], None, None, None, out, self.driver)
        self.assertEqual(rows[0]["state"], "unrun")
        self.assertIsNone(rows[0]["http_status"])

    def test_interrupt_is_retained_and_cleanup_failure_cannot_pass(self):
        job = {"group": "rust_cnl", "target": "rust", "identity": {"phase": "phase2", "id": "row"},
               "form": "cnl", "raw": b'{}', "request_sha256": p.digest(b'{}')}
        def interrupt(*args):
            raise InterruptedError("synthetic interrupt")
        out = self.root / "result"
        out.mkdir()
        with self.assertRaises(self.driver.RowsStopped) as caught:
            p.run_rows([job], None, None, SimpleNamespace(call=interrupt), out, self.driver)
        self.assertEqual(caught.exception.records[0]["state"], "interrupted")
        self.assertFalse(p.close_service(SimpleNamespace(close=interrupt))["cleanup_passed"])


class CarrierIdentityBindingTests(unittest.TestCase):
    def identities(self):
        file = {'sha256': '1' * 64, 'size': 7, 'mode': 493, 'device': 1, 'inode': 10}
        prior = {'binary': dict(file), 'assets': {'manifest.json': dict(file)}, 'tools': {'SOM_RUFF': dict(file)}}
        current = {'binary': {**file, 'inode': 20}, 'assets': {'manifest.json': {**file, 'inode': 21}},
                   'tools': {'SOM_RUFF': {**file, 'inode': 22}}}
        return current, prior

    def carrier(self, root, prior):
        proof = b'{"proof":"previously verified whole typed scope"}'
        archive = b'archive bytes independently restored by parent carrier validator'
        pin = p.digest(proof)
        (root / 'compile-manifest.json').write_bytes(proof)
        (root / 'candidate.tar.gz').write_bytes(archive)
        value = {'version': 'som.tested-candidate-carrier.v1', 'candidate_revision': REVISION,
                 'compile_manifest_sha256': pin, 'archive_sha256': p.digest(archive),
                 'tested_identities': p.portable_identity(prior), 'compiler_rebuilt': False,
                 'origin_role': 'original already tested candidate bytes'}
        (root / 'carrier.json').write_bytes(compact(value))
        return value, pin

    def test_original_same_job_requires_same_inode(self):
        current, prior = self.identities()
        with self.assertRaises(p.Refusal): p.typed_identity_binding(current, prior, None, '0' * 64, REVISION)
        self.assertEqual(p.typed_identity_binding(prior, prior, None, '0' * 64, REVISION),
                         {'mode': 'original-same-job-identities'})

    def test_explicit_same_candidate_carrier_allows_inode_relocation_only(self):
        current, prior = self.identities()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); value, pin = self.carrier(root, prior)
            report = p.typed_identity_binding(current, prior, root, pin, REVISION)
            self.assertEqual(report['mode'], 'verified-same-candidate-portable-carrier-identities')
            self.assertFalse(report['compiler_rebuilt'])

    def test_carrier_refuses_different_candidate(self):
        current, prior = self.identities()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); value, pin = self.carrier(root, prior)
            with self.assertRaises(p.Refusal): p.typed_identity_binding(current, prior, root, pin, '2' * 40)

    def test_carrier_refuses_changed_tool_mode_or_bytes(self):
        current, prior = self.identities()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); value, pin = self.carrier(root, prior)
            current['tools']['SOM_RUFF']['mode'] = 420
            with self.assertRaises(p.Refusal): p.typed_identity_binding(current, prior, root, pin, REVISION)
            current['tools']['SOM_RUFF']['mode'] = 493
            current['tools']['SOM_RUFF']['sha256'] = '2' * 64
            with self.assertRaises(p.Refusal): p.typed_identity_binding(current, prior, root, pin, REVISION)

    def test_carrier_refuses_changed_archive_or_compile_proof(self):
        current, prior = self.identities()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); value, pin = self.carrier(root, prior)
            (root / 'candidate.tar.gz').write_bytes(b'changed archive')
            with self.assertRaises(p.Refusal): p.typed_identity_binding(current, prior, root, pin, REVISION)
            value, pin = self.carrier(root, prior)
            (root / 'compile-manifest.json').write_bytes(b'changed proof')
            with self.assertRaises(p.Refusal): p.typed_identity_binding(current, prior, root, pin, REVISION)

    def test_carrier_refuses_false_rebuild_role(self):
        current, prior = self.identities()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); value, pin = self.carrier(root, prior)
            value['compiler_rebuilt'] = True
            (root / 'carrier.json').write_bytes(compact(value))
            with self.assertRaises(p.Refusal): p.typed_identity_binding(current, prior, root, pin, REVISION)


if __name__ == "__main__":
    unittest.main()
