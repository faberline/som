"""GitHub-only fixtures for actual raw subprocess/CLI/product capture."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest

HERE = Path(__file__).absolute().parent


class RawCaptureTests(unittest.TestCase):
    def setUp(self):
        self.name = '_som_ts_raw_capture_fixture'; self.module = ModuleType(self.name)
        self.module.__file__ = str(HERE / 'typescript_regression_raw_capture.py')
        sys.modules[self.name] = self.module
        exec(compile(Path(self.module.__file__).read_bytes(), self.module.__file__, 'exec', dont_inherit=True),
             self.module.__dict__)
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.original_run = subprocess.run

    def tearDown(self):
        subprocess.run = self.original_run
        self.tmp.cleanup(); sys.modules.pop(self.name, None)

    def test_real_child_binary_streams_and_wait_are_retained(self):
        capture = self.module.RawRuns(self.root / 'commands'); capture.install()
        try:
            done = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
                'import sys;sys.stdout.buffer.write(bytes([255]));sys.stderr.buffer.write(bytes([254]))'],
                capture_output=True, check=True)
        finally:
            capture.close()
        self.assertEqual((done.stdout, done.stderr), (b'\xff', b'\xfe'))
        row = json.loads((self.root / 'commands/000000/result.json').read_bytes())
        self.assertTrue(row['wait_completed']); self.assertEqual(row['exit_code'], 0)
        self.assertGreater(row['pid'], 0); self.assertEqual(row['argv'], done.args)
        self.assertEqual((self.root / 'commands/000000/stdout.raw').read_bytes(), b'\xff')
        self.assertIs(subprocess.run, self.original_run)

    def test_real_timeout_retains_first_error_and_actual_terminal_wait(self):
        capture = self.module.RawRuns(self.root / 'commands'); capture.install()
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess.run([sys.executable, '-I', '-S', '-B', '-c', 'import time;time.sleep(30)'],
                               capture_output=True, timeout=0.01)
        finally:
            capture.close()
        row = json.loads((self.root / 'commands/000000/result.json').read_bytes())
        self.assertEqual(row['failure']['error'], 'TimeoutExpired')
        self.assertTrue(row['wait_completed']); self.assertNotEqual(row['exit_code'], 0)

    def test_text_api_preserves_decoded_result_and_original_raw(self):
        capture = self.module.RawRuns(self.root / 'commands'); capture.install()
        try:
            done = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
                'import sys;sys.stdout.buffer.write(b"a\\r\\nb\\r")'], capture_output=True, text=True)
        finally:
            capture.close()
        self.assertEqual(done.stdout, 'a\nb\n')
        self.assertEqual((self.root / 'commands/000000/stdout.raw').read_bytes(), b'a\r\nb\r')

    def test_cli_return_objects_and_every_byte_are_preserved(self):
        source = self.root / 'case.cnl'; source.write_bytes(b'original source')
        done = subprocess.CompletedProcess(['som', 'compile', str(source)], 2, b'\xff', b'\xfe')
        sent = [('/v1/compile', b'{"source":"original source"}')]
        target = SimpleNamespace(_run_cli_with_request_capture=lambda *args: (done, sent))
        rows = self.module.install_cli(target, self.root / 'cli')
        returned, requests = target._run_cli_with_request_capture('som', 'http://127.0.0.1:18978', source, 'typescript')
        self.assertIs(returned, done); self.assertIs(requests, sent)
        self.assertEqual((self.root / 'cli/000000/stderr.raw').read_bytes(), b'\xfe')
        self.assertEqual((self.root / 'cli/000000/request-000.raw').read_bytes(), sent[0][1])
        self.assertEqual(rows[0]['source_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_product_http_keeps_bytes_before_any_utf8_decode(self):
        target = SimpleNamespace(ts_suite=SimpleNamespace(http_send=lambda *args: (422, 'application/json', b'\xff\xfe')))
        rows = self.module.install_product_http(target, self.root / 'http')
        result = target.ts_suite.http_send('http://127.0.0.1:34567', 'POST', '/items', b'\xff', 'application/json')
        self.assertEqual(result, (422, 'application/json', b'\xff\xfe'))
        self.assertEqual((self.root / 'http/000000/request.raw').read_bytes(), b'\xff')
        self.assertEqual((self.root / 'http/000000/response.raw').read_bytes(), b'\xff\xfe')
        self.assertTrue(rows[0]['complete'])


if __name__ == '__main__':
    unittest.main()
