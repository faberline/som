import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import materialize_roles as m


class MaterializeTests(unittest.TestCase):
    def test_copy_verifies_bytes_and_normalizes_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); src = root / 'source'; dst = root / 'out'
            src.write_bytes(b'approved'); src.chmod(0o644)
            identity = {'sha256': hashlib.sha256(b'approved').hexdigest(), 'size': 8, 'mode': 0o444}
            m.copy_verified(src, dst, identity, source_mode=0o644)
            self.assertEqual(dst.read_bytes(), b'approved')
            self.assertEqual(dst.stat().st_mode & 0o777, 0o444)

    def test_copy_refuses_changed_hash_length_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); src = root / 'source'; src.write_bytes(b'changed'); src.chmod(0o644)
            good = {'sha256': hashlib.sha256(b'expected').hexdigest(), 'size': 8, 'mode': 0o644}
            with self.assertRaises(ValueError): m.copy_verified(src, root / 'wrong-hash', good, source_mode=0o644)
            with self.assertRaises(ValueError): m.copy_verified(src, root / 'wrong-mode', good, source_mode=0o444)
            with self.assertRaises(ValueError): m.copy_verified(src, root / 'wrong-size', dict(good, size=2), source_mode=0o644)

    def test_tree_refuses_symlinks_and_hardlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); original = root / 'original'; original.write_bytes(b'a')
            alias = root / 'alias'; alias.symlink_to(original)
            with self.assertRaises(ValueError): m.regular_tree(root)
            alias.unlink(); os.link(original, alias)
            with self.assertRaises(ValueError): m.regular_tree(root)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); src = root / 'source'; dst = root / 'out'
            src.write_bytes(b'x'); src.chmod(0o644); dst.write_bytes(b'preserve')
            with self.assertRaises(FileExistsError):
                m.copy_verified(src, dst, {'sha256': hashlib.sha256(b'x').hexdigest(), 'size': 1, 'mode': 0o644}, source_mode=0o644)
            self.assertEqual(dst.read_bytes(), b'preserve')


if __name__ == '__main__':
    unittest.main()
