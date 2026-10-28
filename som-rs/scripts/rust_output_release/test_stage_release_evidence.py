"""Check lossless regular bytes and explicit QA pointer sidecars. Run on GitHub."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('stage_release_evidence', Path(__file__).with_name('stage_release_evidence.py'))
staging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(staging)


class StagingTests(unittest.TestCase):
    def test_regular_bytes_and_exact_pointer_sidecars_preserve_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root, out = parent / 'raw', parent / 'staged'
            for family in ('runtime', 'compiler-assets'):
                pointer = root / 'run/rehearsal/switch/root/som-rs' / family / 'current'
                pointer.parent.mkdir(parents=True)
                pointer.symlink_to('/exact/saved/target')
            (root / 'summary.json').write_bytes(b'{"passed":false}\n')
            record = staging.stage(root, out)
            self.assertEqual(len(record['pointer_sidecars']), 2)
            self.assertEqual((out / 'summary.json').read_bytes(), b'{"passed":false}\n')
            pointer = root / 'run/rehearsal/switch/root/som-rs/runtime/current'
            self.assertTrue(pointer.is_symlink())
            sidecar = out / 'run/rehearsal/switch/root/som-rs/runtime/current.symlink.json'
            self.assertEqual(json.loads(sidecar.read_text())['target'], '/exact/saved/target')
            self.assertFalse(any(p.is_symlink() for p in out.rglob('*')))
            with self.assertRaises(ValueError):
                staging.stage(root, out)

    def test_other_symlinks_refused(self):
        for relative in ('run/other/current', 'run/rehearsal/unknown/root/som-rs/runtime/current'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                parent = Path(temporary).resolve()
                root = parent / 'raw'
                link = root / relative
                link.parent.mkdir(parents=True)
                link.symlink_to('/do/not/read')
                with self.assertRaises(ValueError):
                    staging.stage(root, parent / 'out')


if __name__ == '__main__':
    unittest.main()
