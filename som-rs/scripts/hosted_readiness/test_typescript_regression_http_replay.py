"""GitHub-only source fixtures for complete original HTTP replay admission."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest

PATH = Path(__file__).with_name('typescript_regression_http_replay.py')
NAME = '_som_typescript_regression_http_replay_fixture'
raw = PATH.read_bytes()
replay = ModuleType(NAME)
replay.__file__, replay.__package__, replay.__cached__ = str(PATH), '', None
replay.__som_raw_sha256__ = hashlib.sha256(raw).hexdigest()
sys.modules[NAME] = replay
exec(compile(raw, str(PATH), 'exec', dont_inherit=True), replay.__dict__)


class RawTapeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def tape(self, rows=None):
        records = []
        for index, (body, response, status, url) in enumerate(rows or [
                (b'{"prompt":{}}', b'{"status":"ok"}', 200, replay.CURRENT + '/v1/compile')]):
            directory = self.root / f'{index:06d}'
            directory.mkdir()
            (directory / 'request.raw').write_bytes(body)
            (directory / 'response.raw').write_bytes(response)
            row = {'index': index, 'url': url, 'method': 'POST', 'complete': True,
                   'status': status, 'request_sha256': replay.sha(body), 'response_sha256': replay.sha(response)}
            (directory / 'result.json').write_bytes(replay.canonical(row))
            records.append(row)
        document = {'version': 'som.typescript-regression-raw-http.v1', 'rows': records,
                    'product_acceptance_passed': False}
        (self.root / 'index.json').write_bytes(replay.canonical(document))
        (self.root / 'checker-postcheck.json').write_bytes(b'{}')
        return document

    def test_exact_raw_request_response_and_all_slots(self):
        self.tape()
        tape = replay.Tape(self.root)
        self.assertEqual(tape(replay.CURRENT, '/v1/compile', b'{"prompt":{}}'),
                         (200, b'{"status":"ok"}'))
        self.assertEqual(tape.finish()['raw_http_exchanges'], 1)

    def test_request_whitespace_is_not_normalized(self):
        self.tape()
        with self.assertRaisesRegex(ValueError, 'source-derived HTTP request'):
            replay.Tape(self.root)(replay.CURRENT, '/v1/compile', b'{ "prompt":{} }')

    def test_changed_raw_response_is_refused(self):
        self.tape()
        (self.root / '000000/response.raw').write_bytes(b'{"status":"changed"}')
        with self.assertRaisesRegex(ValueError, 'bytes differ'):
            replay.Tape(self.root)

    def test_missing_exchange_is_refused(self):
        self.tape()
        with self.assertRaisesRegex(ValueError, 'unconsumed'):
            replay.Tape(self.root).finish()

    def test_extra_file_is_refused(self):
        self.tape()
        (self.root / 'unlisted.raw').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'member universe'):
            replay.Tape(self.root)

    def test_symlink_is_refused_before_read(self):
        self.tape()
        path = self.root / '000000/response.raw'
        path.rename(self.root / 'original.raw')
        path.symlink_to('../original.raw')
        with self.assertRaisesRegex(ValueError, 'link/special'):
            replay.Tape(self.root)

    def test_protected_port_is_refused(self):
        self.tape(rows=[(b'{}', b'{}', 200, 'http://127.0.0.1:8765/v1/compile')])
        with self.assertRaisesRegex(ValueError, 'endpoint'):
            replay.Tape(self.root)

    def test_pending_exchange_is_refused(self):
        document = self.tape()
        document['rows'][0]['complete'] = False
        (self.root / 'index.json').write_bytes(replay.canonical(document))
        with self.assertRaisesRegex(ValueError, 'terminal state'):
            replay.Tape(self.root)

    def test_post_admission_byte_change_is_refused(self):
        self.tape()
        tape = replay.Tape(self.root)
        (self.root / '000000/request.raw').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'source-derived HTTP request'):
            tape(replay.CURRENT, '/v1/compile', b'{"prompt":{}}')

    def test_named_584_inventory_cannot_drop_a_case(self):
        expected = [{'id': f'case-{i}', 'prompt_sha256': replay.sha(replay.canonical({'version': 'v1'}))}
                    for i in range(584)]
        actual = [{'id': row['id'], 'prompt': {'version': 'v1'}, 'phase': 'phase2'} for row in expected]
        replay.named_inputs(actual, expected, phase='phase2', ordered=False)
        with self.assertRaisesRegex(ValueError, 'complete source-owned'):
            replay.named_inputs(actual[:-1], expected, phase='phase2', ordered=False)

    def test_native_hook_is_explicit_and_restored_on_failure(self):
        old_post = lambda *args: None
        old_context = lambda: 'original'
        module = SimpleNamespace(post_raw=old_post, native_digest_context=old_context)
        digest = object()
        ctx = SimpleNamespace(module=lambda key, path: digest)
        tape = object()
        with self.assertRaisesRegex(RuntimeError, 'first'):
            with replay.assertions(module, ctx, tape):
                self.assertIs(module.post_raw, tape)
                self.assertEqual(module.native_digest_context(), (ctx, digest))
                raise RuntimeError('first')
        self.assertIs(module.post_raw, old_post)
        self.assertIs(module.native_digest_context, old_context)

    def test_pending_authority_refuses_before_helper_or_input_read(self):
        reads = []
        ctx = SimpleNamespace(qa_mode='native-qa-v1', helper_revision='a' * 40,
            candidate_revision='a' * 40, qa_source_authority={'source_authority_active': False},
            sources=SimpleNamespace(read=lambda path: reads.append(path)))
        with self.assertRaisesRegex(ValueError, 'source admission'):
            replay.verify(ctx, self.root, {'helper_revision': 'a' * 40, 'candidate_revision': 'a' * 40})
        self.assertEqual(reads, [])

    def test_named_prompt_change_is_refused(self):
        expected = [{'id': 'source-owned', 'prompt_sha256': replay.sha(replay.canonical({'version': 'v1'}))}]
        actual = [{'id': 'source-owned', 'prompt': {'version': 'v2'}}]
        with self.assertRaisesRegex(ValueError, 'typed prompt differs'):
            replay.named_inputs(actual, expected)


if __name__ == '__main__':
    unittest.main()
