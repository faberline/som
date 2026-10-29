"""Synthetic routing fixtures. Execute only on a GitHub-hosted runner."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.request

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import m6_native_entry as native
import record_http
import baseline_raw


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'])
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'; self.repo.mkdir()
        (self.repo / '.local').mkdir()
        self.script = self.repo / 'fake.py'
        self.script.write_text('import sys\nsys.modules["_m6_test_sink"].append((__name__, __file__, sys.argv[1:]))\nraise SystemExit(3)\n')
        self.source = self.script.read_bytes()
        class Sources:
            def read(inner, name):
                if name != 'fake.py': raise ValueError('unadmitted fixture source')
                return self.source
        self.sources = Sources()
        self.authority = {'admitted_repository_python_sources': ['fake.py']}
        self.router = native.Router(self.repo, self.sources, self.authority)

    def test_router_executes_checked_git_bytes_and_restores_main(self):
        sink = []
        before = sys.modules['__main__']
        with mock.patch.dict(sys.modules, {'_m6_test_sink': sink}):
            self.assertEqual(self.router.execute('fake.py', ['case']), 3)
        self.assertEqual(sink, [('__main__', str(self.script), ['case'])])
        self.assertIs(sys.modules['__main__'], before)
        self.assertEqual(self.router.executed, [{'path': 'fake.py', 'sha256': native.sha(self.source)}])

    def test_unadmitted_and_outside_checkers_fail_before_exec(self):
        with self.assertRaises(ValueError): self.router.execute('other.py', [])
        with self.assertRaises(ValueError): self.router.execute('/tmp/other.py', [])
        self.assertEqual(self.router.executed, [])

    def test_legacy_and_native_ports_are_disjoint_and_narrow(self):
        def request(port, route='/v1/compile', host='127.0.0.1'):
            return urllib.request.Request(f'http://{host}:{port}{route}', data=b'{}', method='POST')
        for port in (18917, 18919):
            self.assertEqual(record_http.request_identity(request(port))[1].split(':')[2].split('/')[0], str(port))
            with self.assertRaises(record_http.RecordingError): record_http.request_identity(request(port), (18978,18979))
        for port in (18978,18979):
            self.assertEqual(record_http.request_identity(request(port), (18978,18979))[0], 'POST')
            with self.assertRaises(record_http.RecordingError): record_http.request_identity(request(port))
        for bad in (request(8765), request(18978, '/admin'), request(18978, host='example.org')):
            with self.assertRaises(record_http.RecordingError): record_http.request_identity(bad, (18978,18979))
        with self.assertRaises(record_http.RecordingError): record_http.request_identity(request(18978), (18978,8765))
        self.assertEqual(baseline_raw.endpoint('http://127.0.0.1:18978', (18978,18979)), 'http://127.0.0.1:18978')
        with self.assertRaises(baseline_raw.BaselineRawError): baseline_raw.endpoint('http://127.0.0.1:18978')
        with self.assertRaises(baseline_raw.BaselineRawError): baseline_raw.endpoint('http://127.0.0.1:18919', (18978,18979))

    def test_native_recorder_routes_nested_source_bytes_and_retains_mode(self):
        output = self.repo / '.local' / 'http'
        holder = SimpleNamespace(qa_mode='native-qa-v1', router=self.router)
        sink = []
        with mock.patch.object(record_http, 'REPO', self.repo), \
             mock.patch.object(record_http, 'ALLOWED_SCRIPTS', frozenset({'fake.py'})), \
             mock.patch.dict(sys.modules, {'_som_m6_checked_router': holder, '_m6_test_sink': sink}):
            code = record_http.run_checker(output, self.script, ['from-recorder'], 'native-qa-v1')
        self.assertEqual(code, 3)
        self.assertEqual(sink, [('__main__', str(self.script), ['from-recorder'])])
        manifest = json.loads((output/'manifest.json').read_bytes())
        self.assertEqual(manifest['qa_mode'], 'native-qa-v1')
        self.assertEqual(manifest['ports'], [18978,18979])
        self.assertEqual(manifest['source_route'], 'checked-git-bytes')
        self.assertEqual(manifest['exit_code'], 3)

    def test_native_recorder_refuses_missing_router(self):
        with mock.patch.object(record_http, 'REPO', self.repo), \
             mock.patch.object(record_http, 'ALLOWED_SCRIPTS', frozenset({'fake.py'})), \
             mock.patch.dict(sys.modules, {'_som_m6_checked_router': None}):
            with self.assertRaises(record_http.RecordingError):
                record_http.run_checker(self.repo/'.local/http', self.script, [], 'native-qa-v1')
        self.assertFalse((self.repo/'.local/http').exists())

    def test_case_postcheck_refuses_replaced_raw_import_control(self):
        context_path = self.repo/'context.json'; context_path.write_bytes(b'{}')
        retained = self.repo/native.RETAINED; retained.parent.mkdir(parents=True,exist_ok=True)
        retained.write_bytes(b'fixture')
        tokens = ['installed']
        def verify_installed():
            if tokens != ['installed']:
                raise ValueError('raw import control replaced')
        ctx = SimpleNamespace(sources=SimpleNamespace(verify=lambda:None),
                              qa_import_controls=SimpleNamespace(verify_installed=verify_installed),
                              path=context_path,raw=b'{}')
        api = SimpleNamespace(recheck_case=lambda live:None)
        sources = SimpleNamespace(read=lambda name:b'fixture')
        module = SimpleNamespace(__file__=str(retained),sealed_files=lambda repo:{})
        with mock.patch.dict(sys.modules,{'retained_evidence':module}):
            native.case_postcheck(ctx,api,{},None,sources,self.repo)
            tokens.clear()
            with self.assertRaises(ValueError): native.case_postcheck(ctx,api,{},None,sources,self.repo)

    def test_enabled_entry_keeps_isolated_startup(self):
        self.assertTrue(native.ROUTING_REVIEWED)
        self.assertTrue(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)


if __name__ == '__main__': unittest.main()
