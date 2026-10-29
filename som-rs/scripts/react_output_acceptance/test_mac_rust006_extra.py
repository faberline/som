"""GitHub-only process, raw protocol and port fixtures for the draft collector."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.request

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import record_rust006_http as recorder
import mac_rust006_extra as extra
import mac_rust006_cluster as cluster


class RawProtocolTests(unittest.TestCase):
    def test_only_candidate_loopback_and_frozen_routes(self):
        for url in ('http://127.0.0.1:18919/v1/compile', 'http://localhost:18919/v1/assemble'):
            request = urllib.request.Request(url, data=b'{}', method='POST')
            self.assertEqual(recorder.request_identity(request)[2], b'{}')
        for url in ('http://127.0.0.1:8765/v1/compile', 'http://127.0.0.1:18765/v1/compile',
                    'http://127.0.0.1:18917/v1/compile', 'http://example.com:18919/v1/compile',
                    'http://127.0.0.1:18919/other', 'http://127.0.0.1:18919/v1/compile?x=1'):
            with self.subTest(url=url), self.assertRaises(recorder.RecordingError):
                recorder.request_identity(urllib.request.Request(url, data=b'{}', method='POST'))

    def test_record_preserves_raw_request_and_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'calls').mkdir()
            owner = recorder.Recorder(out)
            call = recorder.Call(owner, ('POST', 'http://127.0.0.1:18919/v1/compile', b'original'), 1)
            call.record['status'] = 400
            call.append(b'{"code":"ASSET_HASH_MISMATCH"}')
            call.finish(complete=True)
            row = json.loads((out / 'calls/000001.json').read_bytes())
            self.assertEqual((out / row['request_file']).read_bytes(), b'original')
            self.assertEqual((out / row['response_file']).read_bytes(), b'{"code":"ASSET_HASH_MISMATCH"}')
            self.assertEqual(row['status'], 400)
            self.assertTrue(row['complete'])


class WaitEvidenceTests(unittest.TestCase):
    def test_actual_child_waits_and_keeps_failure_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'logs').mkdir()
            row = extra.record_command(out, 1, 'bad', [sys.executable, '-c',
                'import sys; print("out"); print("err",file=sys.stderr); sys.exit(7)'], out, dict(os.environ), 10)
            self.assertEqual(row['state'], 'completed')
            self.assertEqual(row['wait_result'], 7)
            self.assertEqual(row['exit_code'], 7)
            self.assertEqual(row['cleanup'], 'requires-independent-raw-audit')
            self.assertEqual((out / row['stdout']).read_bytes(), b'out\n')
            self.assertEqual((out / row['stderr']).read_bytes(), b'err\n')

    def test_timeout_is_never_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'logs').mkdir()
            row = extra.record_command(out, 1, 'slow', [sys.executable, '-c', 'import time; time.sleep(30)'],
                                       out, dict(os.environ), 0.5)
            self.assertEqual(row['state'], 'timeout')
            self.assertIsNone(row['wait_result'])
            self.assertEqual(row['cleanup'], 'unverified')
            self.assertIsInstance(row['cleanup_wait_result'], int)


if __name__ == '__main__':
    unittest.main(verbosity=2)
