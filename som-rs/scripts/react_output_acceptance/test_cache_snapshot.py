"""Tiny filesystem snapshots. Run on GitHub; no package command or product gate runs."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("react_cache_snapshot_fixture", HERE / "check_products.py")
collector = importlib.util.module_from_spec(SPEC)
common = types.ModuleType("common")
common.CONTRACT, common.REPO = HERE.parents[1] / "contracts/react-output-v1", HERE.parents[2]
# Import only the nonfrozen collector. Frozen helpers are not loaded or executed.
with mock.patch.dict(sys.modules, {"common": common, "check_contract": types.ModuleType("check_contract")}), \
     mock.patch.object(sys, "path", list(sys.path)):
    SPEC.loader.exec_module(collector)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class CacheSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()

    def snapshot(self, name):
        return collector.cache_snapshot(self.cache, self.root / name)

    def test_file_map_and_tree_hash_are_bound_to_names_and_raw_bytes(self):
        (self.cache / "z.bin").write_bytes(b"\x00\xff\n")
        (self.cache / "nested").mkdir()
        (self.cache / "nested/café.txt").write_text("café\n")
        (self.cache / "empty-directory").mkdir()
        record = self.snapshot("before.json")
        expected = {"nested/café.txt": sha("café\n".encode()), "z.bin": sha(b"\x00\xff\n")}
        lines = b"".join(f"{name}\0{expected[name]}\n".encode() for name in sorted(expected))
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["files"], expected)
        self.assertEqual(record["tree_sha256"], sha(lines))
        self.assertEqual((record["file_count"], record["symlink_count"]), (2, 0))
        self.assertEqual(json.loads((self.root / "before.json").read_bytes()), record)

    def test_changed_file_addition_and_deletion_change_the_proof(self):
        path = self.cache / "package"
        path.write_bytes(b"original")
        before = self.snapshot("before.json")
        path.write_bytes(b"changed")
        changed = self.snapshot("changed.json")
        self.assertNotEqual(before["files"]["package"], changed["files"]["package"])
        self.assertNotEqual(before["tree_sha256"], changed["tree_sha256"])
        path.rename(self.cache / "renamed")
        renamed = self.snapshot("renamed.json")
        self.assertEqual(list(renamed["files"].values()), list(changed["files"].values()))
        self.assertNotEqual(renamed["tree_sha256"], changed["tree_sha256"])
        (self.cache / "added").write_bytes(b"new file")
        self.assertEqual(self.snapshot("added.json")["file_count"], 2)
        (self.cache / "renamed").unlink()
        self.assertEqual(set(self.snapshot("deleted.json")["files"]), {"added"})

    def test_file_and_directory_symlinks_are_refused(self):
        external = self.root / "external"
        external.mkdir()
        (external / "data").write_bytes(b"outside")
        for name, target in (("file-link", external / "data"), ("directory-link", external)):
            with self.subTest(name=name):
                link = self.cache / name
                link.symlink_to(target)
                record = self.snapshot(name + ".json")
                self.assertEqual(record["status"], "failed")
                self.assertNotIn("tree_sha256", record)
                link.unlink()

    def test_root_symlink_missing_cache_and_special_file_are_refused(self):
        root_link = self.root / "cache-link"
        root_link.symlink_to(self.cache)
        self.assertEqual(collector.cache_snapshot(root_link, self.root / "link.json")["status"], "failed")
        self.assertEqual(collector.cache_snapshot(self.root / "missing", self.root / "missing.json")["status"], "failed")
        os.mkfifo(self.cache / "fifo")
        self.assertEqual(self.snapshot("fifo.json")["status"], "failed")

    def test_snapshot_receipt_is_never_overwritten(self):
        self.snapshot("preserve.json")
        original = (self.root / "preserve.json").read_bytes()
        (self.cache / "new").write_text("changed")
        with self.assertRaises(FileExistsError):
            self.snapshot("preserve.json")
        self.assertEqual((self.root / "preserve.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
