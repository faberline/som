import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import node_role_transport as transport


class NodeRoleTransportTests(unittest.TestCase):
    def fixture(self, temp):
        run = temp / 'packet/run'
        root = run / 'roles/node_tools'
        data = {'LICENSE': (b'license', 0o644),
                'lib/node_modules/npm/bin/npm-cli.js': (b'npm', 0o755)}
        files = {'bin/node': {'sha256': '3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538'}}
        for name, (raw, mode) in data.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(mode)
            files[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw), 'mode': mode}
        profile = temp / 'profile.json'
        profile.write_text(json.dumps({'status': 'incomplete', 'product_acceptance': 'not_run',
                                       'roles': {'node_tools': {'files': files}}}))
        return run, profile, data

    def test_roundtrip_restores_paths_bytes_and_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp).resolve(); run, profile, data = self.fixture(temp)
            with patch.object(transport, 'PROFILE_SHA', hashlib.sha256(profile.read_bytes()).hexdigest()):
                transport.pack(profile, run)
                self.assertFalse((run / 'roles/node_tools').exists())
                self.assertTrue((temp / 'node-tools-source-backup/lib/node_modules/npm/bin/npm-cli.js').exists())
                target = temp / 'restored/run'; target.mkdir(parents=True)
                for name in ('node-tools-files.zip', 'node-tools-transport.json'):
                    shutil.copyfile(run / name, target / name)
                transport.restore(profile, target)
                for name, (raw, mode) in data.items():
                    path = target / 'roles/node_tools' / name
                    self.assertEqual(path.read_bytes(), raw)
                    self.assertEqual(path.stat().st_mode & 0o777, mode)

    def test_source_universe_and_destination_collisions_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp).resolve(); run, profile, _ = self.fixture(temp)
            with patch.object(transport, 'PROFILE_SHA', hashlib.sha256(profile.read_bytes()).hexdigest()):
                (run / 'roles/node_tools/extra').write_bytes(b'extra')
                with self.assertRaises(ValueError): transport.pack(profile, run)
                (run / 'roles/node_tools/extra').unlink()
                transport.pack(profile, run)
                (run / 'roles/node_tools').mkdir(parents=True)
                with self.assertRaises(ValueError): transport.restore(profile, run)

    def test_modified_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp).resolve(); run, profile, _ = self.fixture(temp)
            with patch.object(transport, 'PROFILE_SHA', hashlib.sha256(profile.read_bytes()).hexdigest()):
                transport.pack(profile, run)
                with (run / 'node-tools-files.zip').open('ab') as out: out.write(b'changed')
                with self.assertRaises(ValueError): transport.restore(profile, run)

    def test_unsafe_paths_are_refused(self):
        for name in ('/absolute', '../up', 'a/../b', 'a//b', 'a/./b'):
            self.assertFalse(transport.valid_name(name))


if __name__ == '__main__':
    unittest.main()
