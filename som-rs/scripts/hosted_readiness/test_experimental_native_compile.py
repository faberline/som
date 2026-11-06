"""Synthetic compile exchanges and read-only source fixtures. Run on GitHub."""
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import marshal
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("experimental_native_compile", HERE / "experimental_native_compile.py")
e = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = e
SPEC.loader.exec_module(e)
REPO = HERE.parents[2]


def h(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def canonical_hash(value):
    # Independent fixture serialization. No checker hash helper creates goldens.
    return h(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))


def wire(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


class FixtureRepository:
    """Real committed factory text as data; all compile exchanges are synthetic."""
    def __init__(self, overrides=None):
        self.commit = "a" * 40
        self.overrides = {} if overrides is None else overrides
        self.pins = {}

    def read(self, path):
        raw = self.overrides[path] if path in self.overrides else (REPO / path).read_bytes()
        self.pins[path] = h(raw)
        return raw

    def text(self, path):
        return self.read(path).decode()

    def paths(self):
        return sorted({p.relative_to(REPO).as_posix() for p in (REPO / "som-rs/evidence").glob("*.json")} | set(self.overrides))


class CompileTests(unittest.TestCase):
    def setUp(self):
        self.baseline = json.loads((REPO / e.BASELINE).read_bytes())
        self.overrides = {}
        self.after, _receipts = e.after_pins(FixtureRepository())

    def test_raw_helper_bootstrap_ignores_matching_pyc_and_preloaded_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / e.RAW_COMPAT
            path.parent.mkdir(parents=True)
            raw = (REPO / e.RAW_COMPAT).read_bytes()
            path.write_bytes(raw)
            os.utime(path, (1700000000, 1700000000))
            cache = Path(importlib.util.cache_from_source(str(path)))
            cache.parent.mkdir()
            stale = compile("raise AssertionError('unchecked cache ran')", str(path), "exec")
            cache.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, 1700000000, len(raw)) + marshal.dumps(stale))
            poison = ModuleType("raw_digest_compat")
            poison.pointer_path = lambda _value: (_ for _ in ()).throw(AssertionError("preloaded helper ran"))
            with patch.dict(sys.modules, {"raw_digest_compat": poison}):
                module = e.bootstrap_raw_compat(path)
            self.assertEqual(module.pointer_path("/bundle/hashes"), ("bundle", "hashes"))
            self.assertIsNone(module.__cached__)
            self.assertIsNot(module, poison)
            self.assertTrue(cache.is_file())

    def test_dirty_raw_helper_is_refused_before_compile(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / e.RAW_COMPAT
            path.parent.mkdir(parents=True)
            path.write_bytes(b"raise AssertionError('dirty helper ran')\n")
            with patch("builtins.compile", side_effect=AssertionError("dirty bytes reached compile")):
                with self.assertRaisesRegex(RuntimeError, "SHA differs"):
                    e.bootstrap_raw_compat(path)

    def test_raw_helper_bootstrap_refuses_links_and_wrong_source_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            target = base / "helper.py"
            target.write_bytes((REPO / e.RAW_COMPAT).read_bytes())
            path = base / e.RAW_COMPAT
            path.parent.mkdir(parents=True)
            path.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "linked"):
                e.bootstrap_raw_compat(path)
            path.unlink()
            os.link(target, path)
            with self.assertRaisesRegex(RuntimeError, "linked"):
                e.bootstrap_raw_compat(path)
            with self.assertRaisesRegex(RuntimeError, "fixed raw compatibility source"):
                e.bootstrap_raw_compat(target)

    def exchange(self, target="python", version="som.prompt.v1", refusal=False):
        rows = self.baseline["baselines"][target]["rows"]
        row = next(row for row in rows if (target != "rust" or row["check"] == "compile_equal") and (row["qa_compile_status"] if target == "python" else row["qa_status"]) == (400 if refusal else 200))
        fields = ("source", "id", "variant") if target == "python" else ("phase", "id")
        identity = {key: row[key] for key in fields}
        if refusal:
            old = {"error": {"code": "UNSUPPORTED_TARGET", "literal": "台灣", "sha256": "1" * 64}}
        else:
            formatter = "python_formatted_formatter" if version in {"som.prompt.v6", "som.prompt.v7"} else "python_render_only_formatter"
            names = {"backend": target + "_backend", "formatter": formatter if target == "python" else "rust_formatter"}
            if target == "rust":
                names["target_contract"] = "rust_target_contract"
            assets = {key: {"id": e.IDS[name], "sha256": e.BEFORE[name]} for key, name in names.items()}
            assets.update({"catalog": {"id": "fixture.catalog", "sha256": h("catalog")}, "semantic_contracts": [{"id": "one", "sha256": h("one")}, {"id": "two", "sha256": h("two")}]})
            bundle = {"version": "som.dsl-bundle.v2" if target == "rust" else "som.dsl-bundle.v1"}
            if target == "rust":
                bundle["target"] = "rust"
            bundle.update({"prompt": {"version": version, "literal": "台灣", "order": [2, 1]}, "dsl": {"literal": "a\n\"b", "numbers": [1, 1.0, -0.0, 0.00001, None, False]}, "assets": assets})
            bundle["hashes"] = {"prompt_sha256": canonical_hash(bundle["prompt"]), "dsl_sha256": canonical_hash(bundle["dsl"]), "payload_sha256": canonical_hash(bundle)}
            old = {"status": "ok", "bundle": bundle}
        frozen = h(wire(old))
        if target == "python":
            row["qa_compile_response_sha256"] = row["baseline_compile_response_sha256"] = frozen
        else:
            row["qa_response_sha256"] = row["response_sha256"] = frozen
            row["baseline_response_sha256"] = frozen if refusal else h("OLDER_CONTROL_MUST_NOT_REPLACE_QA")
        self.overrides[e.BASELINE] = wire(self.baseline)
        current = copy.deepcopy(old)
        if not refusal:
            for asset, name in names.items():
                current["bundle"]["assets"][asset]["sha256"] = self.after[name]
            self.rehash(current)
        return identity, old, current

    @staticmethod
    def rehash(response):
        bundle = response["bundle"]
        bundle["hashes"]["prompt_sha256"] = canonical_hash(bundle["prompt"])
        bundle["hashes"]["dsl_sha256"] = canonical_hash(bundle["dsl"])
        bundle["hashes"]["payload_sha256"] = canonical_hash({key: value for key, value in bundle.items() if key != "hashes"})

    def checker(self):
        with patch.object(e, "Repository", side_effect=lambda root: FixtureRepository(self.overrides)), patch.object(e, "BASELINE_SHA256", h(self.overrides[e.BASELINE])), patch.object(e.platform, "system", return_value="Linux"), patch.object(e.platform, "machine", return_value="x86_64"):
            return e.ExperimentalChecker(REPO)

    def test_python_render_only_and_formatted_versions_preserve_raw_bytes(self):
        for version in ["som.prompt.v1", "som.prompt.v5", "som.prompt.v6", "som.prompt.v7"]:
            with self.subTest(version=version):
                identity, old, current = self.exchange(version=version)
                checked = self.checker().verify_response("python", identity, 200, wire(current))
                self.assertEqual(checked.original_bundle, old["bundle"])
                self.assertTrue(checked.report["byte_compatibility_passed"])
                self.assertFalse(checked.report["product_acceptance"])
                self.assertEqual(checked.report["state"], "experimental-unsealed")
                leaves = checked.report["admitted_digest_leaves"]
                self.assertEqual(len(leaves), 3 if version in {"som.prompt.v6", "som.prompt.v7"} else 2)
                self.assertEqual(checked.report["accepted_QA_raw_sha256"], h(wire(old)))

    def test_rust_uses_latest_qa_hash_and_preserves_older_control_separately(self):
        identity, old, current = self.exchange("rust")
        checked = self.checker().verify_response("rust", identity, 200, wire(current))
        self.assertEqual(checked.original_bundle, old["bundle"])
        self.assertEqual(len(checked.report["admitted_digest_leaves"]), 4)
        self.assertEqual(checked.report["accepted_QA_raw_sha256"], h(wire(old)))
        self.assertNotEqual(checked.report["accepted_QA_raw_sha256"], h("OLDER_CONTROL_MUST_NOT_REPLACE_QA"))
        self.assertEqual(checked.report["older_comparison_control_raw_sha256"], h("OLDER_CONTROL_MUST_NOT_REPLACE_QA"))

    def test_status_and_known_identity_are_exact(self):
        identity, _old, current = self.exchange()
        checker = self.checker()
        for status in [201, 400, True]:
            with self.assertRaises(e.Refusal):
                checker.verify_response("python", identity, status, wire(current))
        with self.assertRaises(e.Refusal):
            checker.verify_response("python", {**identity, "id": "UNKNOWN_CASE"}, 200, wire(current))
        with self.assertRaises(e.Refusal):
            checker.verify_response("python", {**identity, "expected_after": "1" * 64}, 200, wire(current))

    def test_refusals_admit_no_digest_exception(self):
        identity, old, _current = self.exchange("rust", refusal=True)
        checker = self.checker()
        checked = checker.verify_response("rust", identity, 400, wire(old))
        self.assertIsNone(checked.original_bundle)
        self.assertEqual(checked.report["admitted_digest_leaves"], [])
        changed = copy.deepcopy(old)
        changed["error"]["sha256"] = "2" * 64
        with self.assertRaises(e.raw_compat.RawDigestError):
            checker.verify_response("rust", identity, 400, wire(changed))
        with self.assertRaises(e.Refusal):
            checker.verify_response("rust", identity, 200, wire(old))

    def test_unlisted_semantic_changes_fail_after_valid_hash_recomputation(self):
        identity, _old, current = self.exchange("rust")
        checker = self.checker()
        for change in ["prompt", "dsl", "other_asset", "other_id", "list_order"]:
            with self.subTest(change=change):
                modified = copy.deepcopy(current)
                bundle = modified["bundle"]
                if change == "prompt": bundle["prompt"]["literal"] = "changed"
                if change == "dsl": bundle["dsl"]["literal"] = "changed"
                if change == "other_asset": bundle["assets"]["catalog"]["sha256"] = h("different")
                if change == "other_id": bundle["assets"]["catalog"]["id"] = "different"
                if change == "list_order": bundle["assets"]["semantic_contracts"].reverse()
                self.rehash(modified)
                with self.assertRaises(e.raw_compat.RawDigestError):
                    checker.verify_response("rust", identity, 200, wire(modified))

    def test_whitespace_order_spelling_and_escaped_digest_fail_raw_scope(self):
        identity, _old, current = self.exchange()
        checker, raw = self.checker(), wire(current)
        backend = self.after["python_backend"].encode()
        escaped = ("\\u%04x" % backend[0]).encode() + backend[1:]
        candidates = [raw + b" ", wire(dict(reversed(list(current.items())))), raw.replace(backend, escaped, 1), raw.replace(b'"backend"', b'"b\\u0061ckend"', 1)]
        for modified in candidates:
            with self.assertRaises(e.raw_compat.RawDigestError):
                checker.verify_response("python", identity, 200, modified)

    def test_payload_hash_is_verified_before_rebasing(self):
        identity, _old, current = self.exchange()
        current["bundle"]["hashes"]["payload_sha256"] = "0" * 64
        with self.assertRaisesRegex(e.Refusal, "canonical"):
            self.checker().verify_response("python", identity, 200, wire(current))

    def test_forged_current_descriptor_is_not_an_after_pin(self):
        identity, _old, current = self.exchange()
        current["bundle"]["assets"]["backend"]["sha256"] = h("caller chosen digest")
        self.rehash(current)
        with patch.dict(os.environ, {"SOM_EXPECTED_BACKEND_SHA256": h("caller chosen digest"), "SOM_TS_NODE": "/caller/node"}):
            with self.assertRaisesRegex(e.Refusal, "independent source factory"):
                self.checker().verify_response("python", identity, 200, wire(current))

    def test_descriptor_ids_and_render_only_formatter_cannot_change(self):
        identity, _old, current = self.exchange()
        checker = self.checker()
        for asset, field in [("backend", "id"), ("formatter", "sha256")]:
            modified = copy.deepcopy(current)
            modified["bundle"]["assets"][asset][field] = "different" if field == "id" else h("different")
            self.rehash(modified)
            with self.assertRaises(e.Refusal):
                checker.verify_response("python", identity, 200, wire(modified))

    def test_typescript_canaries_do_not_grant_full_inventory_scope(self):
        self.exchange()
        with self.assertRaisesRegex(e.Refusal, "not established"):
            self.checker().verify_response("typescript", {"id": "example-v1"}, 200, b"{}")

    def test_exact_baseline_raw_pin_cannot_be_replaced_by_a_caller(self):
        self.exchange()
        with patch.object(e, "Repository", side_effect=lambda root: FixtureRepository(self.overrides)), patch.object(e, "BASELINE_SHA256", "0" * 64), patch.object(e.platform, "system", return_value="Linux"), patch.object(e.platform, "machine", return_value="x86_64"):
            with self.assertRaisesRegex(e.Refusal, "baseline raw pin"):
                e.ExperimentalChecker(REPO)

    def test_malformed_json_and_decoded_duplicate_keys_fail(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":1,"\\u0078":2}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":}', b'{} trailing', b'\xff']:
            with self.assertRaises((e.Refusal, e.raw_compat.RawDigestError, ValueError)):
                e.parse(raw)

    def test_frozen_numeric_and_unicode_vectors(self):
        vectors = json.loads((REPO / "som-rs/contracts/compiler-v1/canonical-vectors.json").read_bytes())["vectors"]
        for item in vectors:
            self.assertEqual(e.canonical(e.parse(item["input_json"].encode())), item["canonical"])
            self.assertEqual(h(item["canonical"]), item["sha256"])

    def test_complete_backend_vector_tracks_source_and_refuses_factory_edits(self):
        repo = FixtureRepository()
        before = e.backend(repo, "python")
        path = "som-rs/src/main.rs"
        changed = FixtureRepository({path: (REPO / path).read_bytes() + b"\n// synthetic source change\n"})
        self.assertNotEqual(before, e.backend(changed, "python"))
        self.assertEqual(changed.pins[path], h(changed.overrides[path]))
        factory = (REPO / e.COMPILER).read_bytes().replace(b'("src/lib.rs", include_str!("lib.rs")),', b'', 1)
        with self.assertRaisesRegex(e.Refusal, "factory changed"):
            e.backend(FixtureRepository({e.COMPILER: factory}), "python")

    def test_linux_formatters_come_from_artifact_and_profile_rules(self):
        self.assertEqual(self.after["rust_formatter"], "dbb30c6037c51a2bacb94d46e2d6435d9487531c06cf82330fdee0bda507db0d")
        self.assertEqual(self.after["typescript_formatter"], "6070ceb6d781121f6175b7c3992f3aa58bd56f6d871e08f2ff426020bb30f50b")
        self.assertEqual(self.after["python_render_only_formatter"], e.BEFORE["python_render_only_formatter"])
        manifest = json.loads((REPO / e.ARTIFACTS).read_bytes())
        next(item for item in manifest["tools"] if item["tool"] == "ruff")["executable_sha256"] = "1" * 64
        with self.assertRaisesRegex(e.Refusal, "Ruff source pin"):
            e.after_pins(FixtureRepository({e.ARTIFACTS: wire(manifest)}))

    def test_profile_and_backend_ids_are_bound_to_owning_factories(self):
        profile = "som-rs/contracts/python-quality-v1/pyproject.toml"
        with self.assertRaisesRegex(e.Refusal, "formatter profile changed"):
            e.after_pins(FixtureRepository({profile: (REPO / profile).read_bytes() + b"\n"}))
        source = (REPO / e.RUST).read_bytes().replace(b'pub const BACKEND_ID: &str = "som.rust-backend.v1";', b'pub const BACKEND_ID: &str = "changed.backend.v1";', 1)
        with self.assertRaisesRegex(e.Refusal, "backend descriptor ID"):
            e.backend(FixtureRepository({e.RUST: source}), "rust")

    def test_active_receipt_files_and_source_constant_must_agree(self):
        receipt_path = sorted((REPO / "som-rs/evidence").glob("rust-output-contract-amendment-*.json"))[-1]
        receipt = json.loads(receipt_path.read_bytes())
        member = next(iter(receipt["files"]))
        with self.assertRaisesRegex(e.Refusal, "receipt file differs"):
            e.target_contract(FixtureRepository({member: (REPO / member).read_bytes() + b" "}), "rust")
        source = (REPO / e.RUST).read_bytes().replace(receipt["target_contract"]["sha256"].encode(), b"0" * 64, 1)
        with self.assertRaisesRegex(e.Refusal, "owning target constant"):
            e.target_contract(FixtureRepository({e.RUST: source}), "rust")
        future = copy.deepcopy(receipt)
        future["status"] = "draft"
        with self.assertRaisesRegex(e.Refusal, "unsealed"):
            e.target_contract(FixtureRepository({"som-rs/evidence/rust-output-contract-amendment-999.json": wire(future)}), "rust")

    def test_current_source_record_has_no_host_paths_or_acceptance_claim(self):
        identity, _old, current = self.exchange()
        report = self.checker().verify_response("python", identity, 200, wire(current)).report
        record = report["source_pin_record"]
        self.assertEqual(record["commit"], "a" * 40)
        self.assertEqual(record["host"], e.HOST)
        self.assertTrue(all(not path.startswith("/") for path in record["source_sha256"]))
        self.assertEqual(report["remaining"]["old_bundle_rejection"], "unrun")
        self.assertNotIn(str(REPO), json.dumps(report))

    def test_repository_rejects_uncommitted_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            file = root / "source.txt"
            file.write_bytes(b"changed")
            def git(args, **_kwargs):
                return b"a" * 40 + b"\n" if "rev-parse" in args else b"committed"
            with patch.object(e.subprocess, "check_output", side_effect=git):
                repo = e.Repository(root)
                with self.assertRaisesRegex(e.Refusal, "committed source"):
                    repo.read("source.txt")

    def test_cli_failure_remains_experimental_and_never_accepts_product(self):
        self.exchange()
        checker = self.checker()
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "response.json"
            raw.write_bytes(b"{}")
            with patch.object(e, "ExperimentalChecker", return_value=checker), contextlib.redirect_stdout(io.StringIO()) as output:
                code = e.main(["--repo", str(REPO), "--target", "typescript", "--identity", '{"id":"example-v1"}', "--http-status", "200", "--response", str(raw)])
            report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(report["state"], "experimental-unsealed")
        self.assertFalse(report["byte_compatibility_passed"])
        self.assertFalse(report["product_acceptance"])


if __name__ == "__main__":
    unittest.main()
