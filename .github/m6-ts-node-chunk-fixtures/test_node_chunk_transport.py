import hashlib
from pathlib import Path
import tempfile
import unittest

import node_chunk_transport as transport


class NodeChunkTransportTests(unittest.TestCase):
    def make_node(self, root, data=b'0123456789abcdef'):
        path = root / 'roles/node_tools/bin/node'
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        path.chmod(0o755)
        return path

    def test_roundtrip_preserves_bytes_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); source = self.make_node(root)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            record = transport.split(root, expected_sha=digest, expected_bytes=16, chunk_bytes=7)
            self.assertEqual([r['size'] for r in record['chunks']], [7, 7, 2])
            self.assertFalse(source.exists())
            transport.restore(root, expected_sha=digest, expected_bytes=16)
            self.assertEqual(source.read_bytes(), b'0123456789abcdef')
            self.assertEqual(source.stat().st_mode & 0o777, 0o755)

    def test_modified_chunk_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); source = self.make_node(root)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            transport.split(root, expected_sha=digest, expected_bytes=16, chunk_bytes=7)
            (root / 'node-binary-chunks/000.part').write_bytes(b'changed')
            with self.assertRaises(ValueError): transport.restore(root, expected_sha=digest, expected_bytes=16)

    def test_missing_or_extra_chunk_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); source = self.make_node(root)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            transport.split(root, expected_sha=digest, expected_bytes=16, chunk_bytes=7)
            (root / 'node-binary-chunks/extra').write_bytes(b'x')
            with self.assertRaises(ValueError): transport.restore(root, expected_sha=digest, expected_bytes=16)

    def test_existing_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); source = self.make_node(root)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            transport.split(root, expected_sha=digest, expected_bytes=16, chunk_bytes=7)
            source.write_bytes(b'preserve')
            with self.assertRaises(ValueError): transport.restore(root, expected_sha=digest, expected_bytes=16)
            self.assertEqual(source.read_bytes(), b'preserve')


if __name__ == '__main__':
    unittest.main()
