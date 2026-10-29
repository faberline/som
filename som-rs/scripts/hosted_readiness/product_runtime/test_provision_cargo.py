"""Synthetic integrity checks. Run on GitHub; no real network, Cargo, or child programs."""
import hashlib
import io
import json
import os
import py_compile
import struct
import sys
from pathlib import Path
import tarfile
import tempfile
import unittest
from types import ModuleType
from unittest.mock import patch

SUBJECT = Path(__file__).with_name("provision_cargo.py")
c = ModuleType("provision_cargo")
c.__file__, c.__cached__ = str(SUBJECT), None
sys.modules[c.__name__] = c
exec(compile(SUBJECT.read_bytes(), str(SUBJECT), "exec", dont_inherit=True), c.__dict__)


def stale_cache(path, source, stale):
    """Create a valid timestamp cache whose body disagrees with the source bytes."""
    if len(stale) > len(source):
        raise AssertionError("fixture cache body must fit the source size")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(stale.ljust(len(source), b" "))
    os.utime(path, (1700000000, 1700000000))
    cache = Path(py_compile.compile(str(path), doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
    path.write_bytes(source)
    os.utime(path, (1700000000, 1700000000))
    if cache.read_bytes()[8:16] != struct.pack("<II", 1700000000, len(source)):
        raise AssertionError("fixture is not a valid matching timestamp/size cache")
    return cache


def canonical_sha(value):
    return c.digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


def archive(files, links=()):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w:gz") as tar:
        for name, body in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            tar.addfile(member, io.BytesIO(body))
        for name, target_name, kind in links:
            member = tarfile.TarInfo(name)
            member.type, member.linkname = kind, target_name
            tar.addfile(member)
    return target.getvalue()


class GitOnly:
    def __init__(self, repo, blobs):
        self.repo, self.blobs, self.report = repo, blobs, {}
        self.head = "1" * 40

    def run_bytes(self, argv, label, limit):
        args = [str(value) for value in argv]
        if "--no-replace-objects" not in args:
            raise AssertionError("replacement objects allowed")
        operation = args[5]
        if operation == "rev-parse":
            return (str(self.repo).encode() if args[-1] == "--show-toplevel" else self.head.encode()) + b"\n"
        if operation == "ls-tree":
            relative = args[-1]
            blob = self.blobs[relative]
            oid = hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest()
            return b"100644 blob " + oid.encode() + b"\t" + relative.encode() + b"\0"
        if operation == "cat-file":
            blob = next(value for value in self.blobs.values()
                        if hashlib.sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest() == args[-1])
            return str(len(blob)).encode() + b"\n" if args[-2] == "-s" else blob
        raise AssertionError("not Git metadata")


class FrozenCompileOracle:
    """Independent fixture pins. Mutable artifact reports cannot change these."""

    def __init__(self, pins):
        self.pins = dict(pins)

    def verify_response(self, target, identity, status, raw):
        from types import SimpleNamespace
        if target != "rust" or status != 200 or self.pins[(identity["phase"], identity["id"])] != raw:
            raise c.Refusal("independent frozen raw QA differs")
        return SimpleNamespace(report={"byte_compatibility_passed": True, "current_raw_sha256": c.digest(raw)})


class CargoClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.out = self.root / "out"
        self.out.mkdir()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.prefix = "tiny-1.0.0"
        self.bodies = {"Cargo.toml": b'[package]\nname="tiny"\nversion="1.0.0"\nlicense="MIT"\nlicense-file="LICENSE"\n',
                       "Cargo.toml.orig": b"original metadata", "src/lib.rs": b"pub fn real_source() {}\n", "LICENSE": b"MIT fixture notice"}
        blob = archive({self.prefix + "/" + name: body for name, body in self.bodies.items()})
        self.path = self.root / "tiny.crate"
        self.path.write_bytes(blob)
        self.record = ("tiny", "1.0.0", c.REGISTRY, c.digest(blob))

    def published(self):
        return c.inspect_crate(self.record, self.path, self.out)

    def vendor(self, bodies=None):
        bodies = dict(self.bodies) if bodies is None else bodies
        vendor = self.out / "vendor"
        directory = vendor / self.prefix
        for name, body in bodies.items():
            c.q.write_new(directory / name, body)
        c.q.write_new(directory / ".cargo-checksum.json", c.json_bytes(
            {"package": self.record[3], "files": {name: c.digest(body) for name, body in bodies.items()}}))
        return vendor


    def test_bootstrap_ignores_valid_matching_timestamp_cache(self):
        support = self.root / "bootstrap" / c.SUPPORT
        source = Path(c.q.__file__).read_bytes()
        cache = stale_cache(support, source, b"raise AssertionError('unchecked cached support executed')\n")
        with patch.dict(sys.modules):
            sys.modules.pop(c.SUPPORT_MODULE, None)
            module = c.bootstrap_support(support)
        self.assertEqual(module.__file__, str(support))
        self.assertIsNone(module.__cached__)
        self.assertEqual(module.EXPECTED, c.q.EXPECTED)
        self.assertTrue(cache.is_file())

    def test_bootstrap_rejects_dirty_support_before_compile(self):
        support = self.root / "bootstrap" / c.SUPPORT
        support.parent.mkdir(parents=True)
        support.write_bytes(b"raise AssertionError('dirty support executed')\n")
        with patch("builtins.compile", side_effect=AssertionError("dirty support reached compile")):
            with self.assertRaisesRegex(RuntimeError, "support SHA"):
                c.bootstrap_support(support)

    def test_bootstrap_rejects_linked_or_copied_support_paths(self):
        support = self.root / "bootstrap" / c.SUPPORT
        support.parent.mkdir(parents=True)
        target = self.root / "original-support.py"
        target.write_bytes(Path(c.q.__file__).read_bytes())
        support.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "linked"):
            c.bootstrap_support(support)
        support.unlink()
        os.link(target, support)
        with self.assertRaisesRegex(RuntimeError, "linked"):
            c.bootstrap_support(support)
        with self.assertRaisesRegex(RuntimeError, "fixed bootstrap"):
            c.bootstrap_support(target)

    def test_bootstrap_refuses_preloaded_unbound_module(self):
        support = self.root / "bootstrap" / c.SUPPORT
        support.parent.mkdir(parents=True)
        support.write_bytes(Path(c.q.__file__).read_bytes())
        with patch.dict(sys.modules, {c.SUPPORT_MODULE: ModuleType(c.SUPPORT_MODULE)}):
            with self.assertRaisesRegex(RuntimeError, "already loaded"):
                c.bootstrap_support(support)


    def test_bound_helper_ignores_valid_cache_and_uses_git_blob_bytes(self):
        source = b"VALUE = 'committed blob bytes'\n"
        path = self.repo / c.RAW
        stale_cache(path, source, b"VALUE = 'cache'\n")
        with patch.dict(sys.modules):
            sys.modules.pop("raw_digest_compat", None)
            module = c.load_bound("raw_digest_compat", path, source)
        self.assertEqual(module.VALUE, "committed blob bytes")
        self.assertIsNone(module.__cached__)

    def test_bound_helper_does_not_reread_mutable_source_after_binding(self):
        source = b"VALUE = 'Git blob authority'\n"
        path = self.repo / c.RAW
        c.q.write_new(path, b"raise AssertionError('mutable source executed')\n")
        with patch.dict(sys.modules):
            sys.modules.pop("raw_digest_compat", None)
            module = c.load_bound("raw_digest_compat", path, source)
        self.assertEqual(module.VALUE, "Git blob authority")
        with self.assertRaisesRegex(c.Refusal, "fixed bound helper"):
            c.load_bound("unreviewed_name", path, source)

    def test_bound_helper_refuses_preloaded_unbound_module(self):
        path = self.repo / c.RAW
        with patch.dict(sys.modules, {"raw_digest_compat": ModuleType("raw_digest_compat")}):
            with self.assertRaisesRegex(c.Refusal, "already loaded"):
                c.load_bound("raw_digest_compat", path, b"VALUE = 'Git blob authority'\n")

    def test_real_frozen_locks_have_the_complete_352_union(self):
        repo = Path(__file__).resolve().parents[4]
        blobs = {c.BASE + row[0] + "/" + leaf: (repo / c.BASE / row[0] / leaf).read_bytes()
                 for row in c.LOCKS.values() for leaf in ("Cargo.toml", "Cargo.lock")}
        union, counts = c.lock_union(blobs)
        self.assertEqual(counts, {"phase1": 203, "phase2": 334})
        self.assertEqual(len(union), 352)
        self.assertTrue(all(row[2] == c.REGISTRY and c.q.SHA.fullmatch(row[3]) for row in union.values()))
        blobs[c.BASE + "reference/Cargo.lock"] += b"\n"
        with self.assertRaisesRegex(c.Refusal, "frozen Cargo bytes"):
            c.lock_union(blobs)

    def test_lock_checksum_conflict_and_wrong_registry_are_refused(self):
        repo = Path(__file__).resolve().parents[4]
        original = {c.BASE + row[0] + "/" + leaf: (repo / c.BASE / row[0] / leaf).read_bytes()
                    for row in c.LOCKS.values() for leaf in ("Cargo.toml", "Cargo.lock")}
        shared = next(row for row in c.tomllib.loads(original[c.BASE + "reference/Cargo.lock"].decode())["package"]
                      if row.get("checksum") and any(other.get("name") == row["name"] and other.get("version") == row["version"]
                         for other in c.tomllib.loads(original[c.BASE + "reference-phase2/Cargo.lock"].decode())["package"]))
        for mutation in ("checksum", "registry"):
            with self.subTest(mutation=mutation):
                blobs, specs = dict(original), dict(c.LOCKS)
                path = c.BASE + "reference-phase2/Cargo.lock"
                blobs[path] = blobs[path].replace(shared["checksum"].encode(), b"0" * 64, 1) if mutation == "checksum" else blobs[path].replace(c.REGISTRY.encode(), b"registry+https://invalid.example/index", 1)
                old = specs["phase2"]
                specs["phase2"] = (old[0], c.digest(blobs[path]), old[2], old[3])
                with patch.object(c, "LOCKS", specs), self.assertRaises(c.Refusal):
                    c.lock_union(blobs)

    def test_git_binding_rejects_dirty_lock_or_helper(self):
        files = [c.SCRIPT, c.SUPPORT, c.GOLDENS, c.TS065, c.EXPERIMENT, c.RAW, c.BASE + "toolchain.json"]
        files += [c.BASE + item[0] + "/" + leaf for item in c.LOCKS.values() for leaf in ("Cargo.toml", "Cargo.lock")]
        blobs = {name: ("fixture " + name).encode() for name in files}
        for name, body in blobs.items():
            c.q.write_new(self.repo / name, body)
        with patch.object(c, "__file__", str(self.repo / c.SCRIPT)), patch.object(c.q, "__file__", str(self.repo / c.SUPPORT)):
            self.assertEqual(c.bind_sources(GitOnly(self.repo, blobs), self.repo, "1" * 40), blobs)
            for name in (c.SCRIPT, c.BASE + "reference/Cargo.lock"):
                path = self.repo / name
                path.write_bytes(blobs[name] + b" dirty")
                with self.assertRaisesRegex(c.Refusal, "working source differs"):
                    c.bind_sources(GitOnly(self.repo, blobs), self.repo, "1" * 40)
                path.write_bytes(blobs[name])

    def test_archive_hash_is_checked_before_parser_and_env_cannot_override(self):
        self.path.write_bytes(b"changed")
        with patch.dict(os.environ, {"CRATE_SHA256": c.digest(b"changed")}), patch.object(c.q, "checked_tar") as parser:
            with self.assertRaisesRegex(c.Refusal, "crate changed"):
                self.published()
        parser.assert_not_called()

    def test_full_archive_and_license_inventory_is_preserved(self):
        published = self.published()
        self.assertEqual(published["files"], {name: c.digest(body) for name, body in self.bodies.items()})
        self.assertEqual(published["license_expression"], "MIT")
        self.assertEqual((self.out / "licenses" / self.prefix / "LICENSE").read_bytes(), self.bodies["LICENSE"])

    def test_unsafe_archive_names_links_and_duplicates_are_refused(self):
        for index, (files, links) in enumerate([
            ({"../outside": b"x"}, ()),
            ({self.prefix + "/Cargo.toml": self.bodies["Cargo.toml"]}, ((self.prefix + "/src/lib.rs", "../outside", tarfile.SYMTYPE),)),
            ({self.prefix + "/Cargo.toml": self.bodies["Cargo.toml"]}, ((self.prefix + "/src/lib.rs", self.prefix + "/Cargo.toml", tarfile.LNKTYPE),)),
        ]):
            with self.subTest(index=index):
                blob = archive(files, links)
                path = self.root / f"unsafe-{index}.crate"
                path.write_bytes(blob)
                with self.assertRaises(c.Refusal):
                    c.inspect_crate((*self.record[:3], c.digest(blob)), path, self.out)

    def test_cargo_generated_full_checksum_and_publisher_bytes_are_required(self):
        published = self.published()
        vendor = self.vendor()
        changes = {}
        rows = c.verify_vendor(vendor, {self.record[:2]: self.record}, {self.record[:2]: published}, changes)
        self.assertEqual(len(rows), 1)
        self.assertEqual(changes[self.prefix]["publisher_files_not_vendored"], [])
        file = vendor / self.prefix / "src/lib.rs"
        file.write_bytes(b'compile_error!("stub");\n')
        with self.assertRaisesRegex(c.Refusal, "checksum metadata"):
            c.verify_vendor(vendor, {self.record[:2]: self.record}, {self.record[:2]: published}, {})

    def test_rehashed_stub_cannot_hide_behind_new_checksum_metadata(self):
        published = self.published()
        bodies = dict(self.bodies)
        bodies["src/lib.rs"] = b'compile_error!("stub");\n'
        vendor = self.vendor(bodies)
        changes = {}
        with self.assertRaisesRegex(c.Refusal, "transformed publisher"):
            c.verify_vendor(vendor, {self.record[:2]: self.record}, {self.record[:2]: published}, changes)
        self.assertIn("src/lib.rs", changes[self.prefix]["changed"])

    def test_missing_real_source_is_not_a_permitted_cargo_omission(self):
        published = self.published()
        vendor = self.vendor({name: body for name, body in self.bodies.items() if name != "src/lib.rs"})
        with self.assertRaisesRegex(c.Refusal, "omitted publisher source"):
            c.verify_vendor(vendor, {self.record[:2]: self.record}, {self.record[:2]: published}, {})

    def test_nested_git_metadata_omissions_keep_all_publisher_source_bytes(self):
        metadata = {"tests/test-data/.gitattributes": b"*.pem binary\n",
                    "tests/.gitignore": b"generated\n", "nested/.git/config": b"fixture metadata\n"}
        self.bodies.update(metadata)
        blob = archive({self.prefix + "/" + name: body for name, body in self.bodies.items()})
        self.path.write_bytes(blob)
        self.record = (*self.record[:3], c.digest(blob))
        published = self.published()
        vendor = self.vendor({name: body for name, body in self.bodies.items() if name not in metadata})
        changes = {}
        rows = c.verify_vendor(vendor, {self.record[:2]: self.record}, {self.record[:2]: published}, changes)
        self.assertEqual(rows[0]["publisher_files_not_vendored"], sorted(metadata))
        self.assertEqual(changes[self.prefix]["changed"], {})
        self.assertIn("Cargo.toml.orig", rows[0]["files"])

    def test_registry_backup_and_other_dotfile_omissions_are_refused(self):
        original = dict(self.bodies)
        for index, relative in enumerate(("Cargo.toml.orig", "src/lib.rs.rej", "tests/.gitkeep", "tests/.gitattributes.rs")):
            with self.subTest(relative=relative):
                self.out = self.root / f"omission-{index}"
                self.out.mkdir()
                self.bodies = {**original, relative: b"publisher byte that Cargo retains\n"}
                blob = archive({self.prefix + "/" + name: body for name, body in self.bodies.items()})
                self.path.write_bytes(blob)
                self.record = (*self.record[:3], c.digest(blob))
                published = self.published()
                vendor = self.vendor({name: body for name, body in self.bodies.items() if name != relative})
                with self.assertRaisesRegex(c.Refusal, "omitted publisher source"):
                    c.verify_vendor(vendor, {self.record[:2]: self.record}, {self.record[:2]: published}, {})

    def test_vendor_symlinks_and_unsafe_config_are_refused(self):
        vendor = self.vendor()
        (vendor / self.prefix / "alias").symlink_to(self.root / "outside-fixture")
        with self.assertRaisesRegex(c.Refusal, "symlink"):
            c.tree_files(vendor)
        root = self.root / "project"
        root.mkdir()
        (self.root / ".cargo").mkdir()
        (self.root / ".cargo/config.toml").write_text("[build]\nrustc-wrapper='unexpected'\n")
        with self.assertRaisesRegex(c.Refusal, "configuration"):
            c.reject_cargo_configs(root)

    def test_mock_download_checks_size_hash_and_public_redirect_host(self):
        class Response(io.BytesIO):
            def geturl(self):
                return "https://static.crates.io/crates/tiny/tiny-1.0.0.crate"
        for label, limit in (("good", c.MAX_CRATE), ("large", 2)):
            with patch.object(c, "build_opener") as opener, patch.object(c, "MAX_CRATE", limit):
                opener.return_value.open.return_value = Response(self.path.read_bytes())
                if label == "good":
                    self.assertEqual(c.fetch_crate(self.record, self.root / label), self.path.stat().st_size)
                else:
                    with self.assertRaisesRegex(c.Refusal, "limit"):
                        c.fetch_crate(self.record, self.root / label)
        for url in ("http://crates.io/x", "https://invalid.example/x", "https://token@crates.io/x"):
            with self.assertRaises(c.Refusal):
                c.public_url(url)

    def consumer_fixture(self):
        artifact = self.root / "assembly-input"
        artifact.mkdir()
        revision, products, roots, blobs, selected_data, pins = "1" * 40, [], {}, {}, {}, {}
        cohorts = {"rust_phase1": [], "rust_phase2": []}
        rust_keys = []
        for phase, count in (("phase1", 115), ("phase2", 582)):
            for index in range(count):
                identity = c.IDENTITIES[phase] if index == 0 else phase + "-fixture-" + str(index)
                row = {"phase": phase, "id": identity}
                products.append(row)
                rust_keys.append((phase, identity))
                cohorts["rust_" + phase].append({"id": identity, "files_map_sha256": "0" * 64})
                if index:
                    continue
                root = artifact / "assembly/observations" / phase / "product"
                roots[phase] = root
                text = {"Cargo.toml": "[package]\nname='som_app'\nversion='0.1.0'\n",
                        "Cargo.lock": "version = 4\n", "src/main.rs": "// accepted fixture text, never built\n"}
                for name, value in text.items():
                    c.q.write_new(root / name, value.encode())
                directory = c.LOCKS[phase][0]
                for name in ("Cargo.toml", "Cargo.lock"):
                    blobs[c.BASE + directory + "/" + name] = text[name].encode()
                expected = canonical_sha(text)
                cohorts["rust_" + phase][-1]["files_map_sha256"] = expected
                row.update(state="passed", rust_assembly_mode="normal-target-plan",
                           product_dir=root.relative_to(artifact).as_posix(),
                           expected_files_map_sha256=expected, actual_files_map_sha256=expected,
                           files=[{"path": name, "sha256": c.digest(text[name].encode()), "size": len(text[name].encode())}
                                  for name in sorted(text)])
                payload = {"target": "rust", "prompt": {"version": "som.prompt.v1"}, "dsl": {}, "assets": {}}
                bundle = {**payload, "hashes": {"payload_sha256": canonical_sha(payload)}}
                request = c.json_bytes({"bundle": bundle})
                response = c.json_bytes({"status": "ok", "files": text})
                for stem, body in (("request", request), ("response", response)):
                    relative = "assembly/observations/" + phase + "/" + stem + ".raw"
                    c.q.write_new(artifact / relative, body)
                    row[stem + "_path"], row[stem + "_sha256"] = relative, c.digest(body)
                selected_data[phase] = (bundle, row)
        for index in range(2):
            identity = "refusal-fixture-" + str(index)
            rust_keys.append(("phase2", identity))
            cohorts["rust_phase2"].append({"id": identity, "files_map_sha256": None})
        python_keys = [("public", "python-fixture-" + str(index), "none") for index in range(1342)]
        baseline = {"baselines": {"python": {"rows": [dict(zip(("source", "id", "variant"), key)) for key in python_keys]},
                                  "rust": {"rows": [{"phase": phase, "id": identity, "check": "compile_equal"} for phase, identity in rust_keys]}}}
        blobs[c.GOLDENS] = c.json_bytes({"version": "som.public-golden-hashes.v1", "cohorts": cohorts})
        blobs[c.TS065] = c.json_bytes(baseline)
        rows = [{"target": "python", "identity": dict(zip(("source", "id", "variant"), key)), "state": "passed", "index": index}
                for index, key in enumerate(python_keys)]
        for phase, identity in rust_keys:
            row = {"target": "rust", "identity": {"phase": phase, "id": identity}, "state": "passed",
                   "index": len(rows), "http_status": 200}
            if identity == c.IDENTITIES.get(phase):
                raw = c.json_bytes({"bundle": selected_data[phase][0]})
                row["current_response_sha256"] = c.digest(raw)
                pins[(phase, identity)] = raw
                check = {"target": "rust", "identity": row["identity"], "http_status": 200,
                         "byte_compatibility_passed": True, "product_acceptance": False,
                         "current_raw_sha256": c.digest(raw), "source_pin_record": {"commit": revision}}
                c.q.write_new(artifact / "rows" / f"{len(rows):04d}" / "response.raw", raw)
                c.q.write_new(artifact / "rows" / f"{len(rows):04d}" / "checker.json", c.json_bytes(check))
            rows.append(row)
        compile_receipt = {"tested_sha": revision, "state": "experimental-unsealed", "product_acceptance_passed": False,
                           "experiment_complete": True, "passed": 2041, "identity_stability_passed": True,
                           "cleanup": {"cleanup_passed": True}, "independent_source_record": {"commit": revision}, "rows": rows}
        assembly = {"version": "som.native-assembly-experiment.v1", "tested_sha": revision,
                    "state": "experimental-unsealed", "product_acceptance_passed": False,
                    "source_pin_record": {"commit": revision},
                    "input_receipts": {"goldens_raw_sha256": c.digest(blobs[c.GOLDENS]), "ts065_raw_sha256": c.digest(blobs[c.TS065])},
                    "products": {"rust": products}}
        c.q.write_new(artifact / "manifest.json", c.json_bytes(compile_receipt))
        c.q.write_new(artifact / "assembly/manifest.json", c.json_bytes(assembly))
        return artifact, roots, blobs, assembly, FrozenCompileOracle(pins)

    def test_complete_assembly_inventory_above_source_blob_limit_is_bound(self):
        artifact, roots, blobs, assembly, oracle = self.consumer_fixture()
        files = [{"path": f"generated/component_{index:03d}/source/structured_module_with_full_inventory_scope.rs",
                  "sha256": "a" * 64, "size": 128} for index in range(40)]
        assembly["observations"] = [{"index": index, "target": "python" if index < 1348 else "rust",
                                     "state": "passed", "files": files} for index in range(2045)]
        path = artifact / "assembly/manifest.json"
        raw = c.json_bytes(assembly)
        self.assertGreater(len(raw), 16 * 1024 * 1024)
        path.write_bytes(raw)
        # The exact whole receipt must reach the existing full product/raw proof checks.
        selected, receipts = c.select_products(artifact, roots, blobs, "1" * 40, canonical_sha, oracle)
        self.assertEqual(set(selected), {"phase1", "phase2"})
        self.assertEqual(receipts["assembly_manifest_sha256"], c.digest(raw))
        # The larger receipt allowance must not change ordinary source/blob reads.
        with self.assertRaisesRegex(c.Refusal, "oversized input"):
            c.safe_read(path)

    def test_assembly_receipt_above_its_own_limit_is_refused_before_read(self):
        artifact, roots, blobs, _, oracle = self.consumer_fixture()
        path = artifact / "assembly/manifest.json"
        # A sparse oversized file avoids allocating or parsing 64 MiB of fixture data.
        with path.open("wb") as stream:
            stream.truncate(c.MAX_ASSEMBLY_RECEIPT + 1)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("oversized receipt was read")):
            with self.assertRaisesRegex(c.Refusal, "oversized input"):
                c.select_products(artifact, roots, blobs, "1" * 40, canonical_sha, oracle)

    def test_assembly_receipt_symbolic_link_is_refused_before_read(self):
        artifact, roots, blobs, _, oracle = self.consumer_fixture()
        path = artifact / "assembly/manifest.json"
        target = self.root / "copied-assembly-manifest.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("linked receipt was read")):
            with self.assertRaisesRegex(c.Refusal, "symlink"):
                c.select_products(artifact, roots, blobs, "1" * 40, canonical_sha, oracle)

    def test_real_product_targets_full_maps_and_raw_receipts_are_bound(self):
        artifact, roots, blobs, _, oracle = self.consumer_fixture()
        selected, receipts = c.select_products(artifact, roots, blobs, "1" * 40, canonical_sha, oracle)
        self.assertEqual(set(selected), {"phase1", "phase2"})
        self.assertEqual(selected["phase1"]["id"], "example-v1")
        self.assertTrue(receipts["compile_manifest_sha256"])

    def test_product_changes_and_copied_targets_are_refused(self):
        artifact, roots, blobs, _, oracle = self.consumer_fixture()
        actual = roots["phase1"] / "src/main.rs"
        original = actual.read_bytes()
        actual.write_bytes(b"// substituted target\n")
        with self.assertRaisesRegex(c.Refusal, "complete generated text file map"):
            c.select_products(artifact, roots, blobs, "1" * 40, canonical_sha, oracle)
        actual.write_bytes(original)
        with self.assertRaisesRegex(c.Refusal, "explicit product root"):
            c.select_products(artifact, {**roots, "phase1": self.root}, blobs, "1" * 40, canonical_sha, oracle)

    def test_changed_candidate_or_raw_assembly_receipt_is_refused(self):
        artifact, roots, blobs, assembly, oracle = self.consumer_fixture()
        with self.assertRaisesRegex(c.Refusal, "candidate"):
            c.select_products(artifact, roots, blobs, "2" * 40, canonical_sha, oracle)
        response = artifact / assembly["products"]["rust"][0]["response_path"]
        response.write_bytes(response.read_bytes() + b" ")
        with self.assertRaisesRegex(c.Refusal, "assembly raw receipt"):
            c.select_products(artifact, roots, blobs, "1" * 40, canonical_sha, oracle)

    def test_rehashed_compile_and_checker_artifacts_cannot_replace_frozen_raw_qa(self):
        artifact, roots, blobs, assembly, oracle = self.consumer_fixture()
        manifest = c.parse((artifact / "manifest.json").read_bytes())
        row = next(row for row in manifest["rows"] if row.get("target") == "rust"
                   and row["identity"] == {"phase": "phase1", "id": "example-v1"})
        compile_path = artifact / "rows" / f"{row['index']:04d}" / "response.raw"
        body = c.parse(compile_path.read_bytes())
        body["bundle"]["dsl"]["changed"] = True
        body["bundle"]["hashes"]["payload_sha256"] = canonical_sha({key: value for key, value in body["bundle"].items() if key != "hashes"})
        raw = c.json_bytes(body)
        compile_path.write_bytes(raw)
        row["current_response_sha256"] = c.digest(raw)
        (artifact / "manifest.json").write_bytes(c.json_bytes(manifest))
        check_path = compile_path.with_name("checker.json")
        check = c.parse(check_path.read_bytes())
        check["current_raw_sha256"] = c.digest(raw)
        check_path.write_bytes(c.json_bytes(check))
        product = assembly["products"]["rust"][0]
        request = c.json_bytes({"bundle": body["bundle"]})
        (artifact / product["request_path"]).write_bytes(request)
        product["request_sha256"] = c.digest(request)
        (artifact / "assembly/manifest.json").write_bytes(c.json_bytes(assembly))
        with self.assertRaisesRegex(c.Refusal, "independent frozen raw QA"):
            c.select_products(artifact, roots, blobs, "1" * 40, canonical_sha, oracle)

    def test_duplicate_crate_archive_member_is_refused(self):
        target = io.BytesIO()
        with tarfile.open(fileobj=target, mode="w:gz") as tar:
            for _ in range(2):
                member = tarfile.TarInfo(self.prefix + "/Cargo.toml")
                body = self.bodies["Cargo.toml"]
                member.size = len(body)
                tar.addfile(member, io.BytesIO(body))
        self.path.write_bytes(target.getvalue())
        with self.assertRaisesRegex(c.Refusal, "duplicate"):
            c.inspect_crate((*self.record[:3], c.digest(target.getvalue())), self.path, self.out)

    def test_candidate_archive_preserves_file_mode_and_publisher_archives(self):
        vendor = self.vendor()
        for top in ("licenses", "downloads", "cargo-home"):
            (self.out / top).mkdir()
        executable = vendor / self.prefix / "helper.sh"
        executable.write_bytes(b"# fixture, never executed\n")
        executable.chmod(0o755)
        (self.out / "downloads/tiny.crate").write_bytes(self.path.read_bytes())
        (self.out / "cargo-home/config.toml").write_bytes(b"[net]\noffline=true\n")
        result = c.package_candidate(self.out)
        self.assertEqual(result, c.q.sha(self.out / "cargo-closure.tar.gz"))
        with tarfile.open(self.out / "cargo-closure.tar.gz", "r:gz") as archive:
            self.assertEqual(archive.getmember("vendor/" + self.prefix + "/helper.sh").mode, 0o755)
            self.assertEqual(archive.extractfile("downloads/tiny.crate").read(), self.path.read_bytes())

    def test_staging_refuses_changed_product_bytes(self):
        source = self.root / "main.rs"
        source.write_bytes(b"changed")
        item = {"source_files": {"src/main.rs": source},
                "files": [{"path": "src/main.rs", "sha256": c.digest(b"accepted"), "size": len(b"accepted")}]}
        with self.assertRaisesRegex(c.Refusal, "changed before staging"):
            c.stage_products({"phase1": item}, self.out)

    def test_missing_consumer_roots_stop_before_any_program(self):
        from types import SimpleNamespace
        args = SimpleNamespace(repo=self.repo, out=self.root / "new-output", assembly_root=self.root / "absent",
                               phase1_product_root=self.root / "absent-p1", phase2_product_root=self.root / "absent-p2")
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(self.root)}), patch.object(c.platform, "system", return_value="Linux"), patch.object(c.platform, "machine", return_value="x86_64"), patch.object(c.q, "Runner") as runner:
            with self.assertRaisesRegex(c.Refusal, "consumer inputs are missing"):
                c.provision(args)
        runner.assert_not_called()
        self.assertFalse(args.out.exists())


if __name__ == "__main__":
    unittest.main()
