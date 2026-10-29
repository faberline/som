"""Synthetic source restoration fixtures. Run only on a GitHub-hosted runner."""
import hashlib
import io
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')
import restore_inputs as r

def member(name, data=b'', kind='file', mode=0o644, target=''):
    item = tarfile.TarInfo(name)
    item.mode = mode
    if kind == 'dir':
        item.type = tarfile.DIRTYPE
    elif kind == 'symlink':
        item.type = tarfile.SYMTYPE
        item.linkname = target
    else:
        item.size = len(data)
    return item, data

def fixture(extra=()):
    root = r.ROOT
    entries = [member(root, kind='dir', mode=0o700)]
    for name in r.TREES:
        entries.append(member(root + '/' + name, kind='dir', mode=0o555 if name == 'npm-cache-new' else 0o755))
    for name in r.FILES:
        entries.append(member(root + '/' + name, ('bytes:' + name).encode()))
    entries.extend([
        member(root + '/probe/report.json', b'{"state":"synthetic"}'),
        member(root + '/probe/private-output', b'must not be restored'),
        member(root + '/npm-cache-new/content', b'cache bytes', mode=0o444),
        member(root + '/node/lib/node_modules/npm/bin/npm-cli.js', b'cli'),
        member(root + '/node/lib/node_modules/npm/bin/npm', kind='symlink', target='npm-cli.js'),
        *extra,
    ])
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as tar:
        for item, data in entries:
            tar.addfile(item, io.BytesIO(data) if item.isfile() else None)
    return out.getvalue()

class RestoreFixtures(unittest.TestCase):
    def scratch(self, raw):
        root = Path(tempfile.mkdtemp(prefix='som-react-restore-fixture-'))
        archive = root / 'input.tar'
        archive.write_bytes(raw)
        return archive, root / 'output'

    def test_exact_selected_files_modes_and_source_report(self):
        raw = fixture()
        archive, output = self.scratch(raw)
        r.restore(archive, output, hashlib.sha256(raw).hexdigest())
        self.assertEqual((output / 'producer-report.json').read_bytes(), b'{"state":"synthetic"}')
        self.assertFalse((output / 'probe').exists())
        self.assertEqual((output / 'npm-cache-new/content').read_bytes(), b'cache bytes')
        self.assertEqual(stat.S_IMODE((output / 'npm-cache-new').stat().st_mode), 0o555)
        self.assertEqual(stat.S_IMODE((output / 'npm-cache-new/content').stat().st_mode), 0o444)
        self.assertEqual((output / 'node/lib/node_modules/npm/bin/npm').read_bytes(), b'cli')

    def test_wrong_source_hash_refuses_before_output(self):
        archive, output = self.scratch(fixture())
        with self.assertRaises(r.Refusal):
            r.restore(archive, output, '0' * 64)
        self.assertFalse(output.exists())

    def test_duplicate_traversal_and_unsafe_link_refuse(self):
        extras = (
            member(r.ROOT + '/public-inputs.json', b'duplicate'),
            member(r.ROOT + '/../escape', b'x'),
            member(r.ROOT + '/node/lib/node_modules/npm/bin/bad', kind='symlink', target='../../../../../../escape'),
            member(r.ROOT + '/npm-cache-new/link', kind='symlink', target='content'),
        )
        for item in extras:
            raw = fixture([item])
            archive, output = self.scratch(raw)
            with self.subTest(item=item[0].name), self.assertRaises(r.Refusal):
                r.restore(archive, output, hashlib.sha256(raw).hexdigest())
            self.assertFalse(output.exists())

    def test_link_cannot_be_parent_of_other_entry(self):
        extras = [member(r.ROOT + '/node/lib/node_modules/npm/alias', kind='symlink', target='bin'),
                  member(r.ROOT + '/node/lib/node_modules/npm/alias/file', b'x')]
        raw = fixture(extras)
        archive, output = self.scratch(raw)
        with self.assertRaises(r.Refusal):
            r.restore(archive, output, hashlib.sha256(raw).hexdigest())
        self.assertFalse(output.exists())

if __name__ == '__main__':
    unittest.main()
