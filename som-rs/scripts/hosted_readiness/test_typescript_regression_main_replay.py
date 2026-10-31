"""GitHub-only fixtures for exact original-main HTTP replay guards."""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
import urllib.error
import urllib.request

NAME = '_som_typescript_original_main_replay_fixture'
PATH = Path(__file__).with_name('typescript_regression_main_replay.py')
raw = PATH.read_bytes()
replay = ModuleType(NAME)
replay.__file__, replay.__cached__, replay.__package__ = str(PATH), None, ''
replay.__som_raw_sha256__ = hashlib.sha256(raw).hexdigest()
sys.modules[NAME] = replay
exec(compile(raw, str(PATH), 'exec', dont_inherit=True), replay.__dict__)


class MainReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo, self.root, self.out = (self.base / name for name in ('repo', 'capture', 'audit'))
        for directory in (self.repo, self.root, self.out):
            directory.mkdir()
        self.ctx = SimpleNamespace(repo=self.repo, source_bytes={})

    def tape(self, status=200, body=b'{}', reply=b'{"status":"ok"}'):
        root = self.root / 'http'; root.mkdir()
        directory = root / '000000'; directory.mkdir()
        (directory / 'request.raw').write_bytes(body)
        (directory / 'response.raw').write_bytes(reply)
        row = {'index': 0, 'method': 'POST', 'url': replay.CURRENT + '/v1/compile',
               'status': status, 'request_sha256': replay.sha(body),
               'response_sha256': replay.sha(reply), 'complete': True,
               'response_headers': [['Content-Type', 'application/json']]}
        (directory / 'result.json').write_bytes(replay.canonical(row))
        (root / 'index.json').write_bytes(replay.canonical({
            'version': 'som.typescript-regression-raw-http.v1', 'rows': [row],
            'product_acceptance_passed': False}))
        (root / 'checker-postcheck.json').write_bytes(b'{}')
        (root / 'raw-commands').mkdir()
        (root / 'raw-commands/index.json').write_bytes(replay.canonical({
            'version': 'som.typescript-regression-raw-commands.v1', 'rows': [],
            'product_acceptance_passed': False}))
        return replay.HttpTape(root)

    def guards(self, tape=None, saved=None):
        return replay.MainGuards(self.ctx, self.root, {'saved_files': saved or {}}, self.out,
                                 tape or SimpleNamespace(open=lambda *args: None))

    def test_domain_obsolete_direct_driver_is_refused_with_matching_raw_command(self):
        # Standalone command-consumer data, not actual owner or native evidence.
        path = PATH.with_name('typescript_regression_domain.py'); source = path.read_bytes()
        domain = ModuleType('_som_domain_command_argv_data_fixture')
        domain.__file__, domain.__cached__, domain.__package__ = str(path), None, ''
        exec(compile(source, str(path), 'exec', dont_inherit=True), domain.__dict__)
        context = {'repo': str(self.repo), 'observer_python': '/runtime/python',
                   'helper_revision': 'a' * 40, 'candidate_revision': 'a' * 40}
        original = self.base / 'original-run'; root = self.root / 'domain-commands'
        script = domain.BASE + 'typescript_regression_checker.py'
        capture = {'context': context, 'candidate_revision': 'a' * 40, 'original_run': str(original),
                   'bound_source_sha256': {script: 'c' * 64}, 'rows': []}
        expected = []
        judgment = {'explicit_synthetic_judge': True}
        for index in range(1, 16):
            logical = [context['observer_python'], str(self.repo / script), '--fixture-index', str(index)]
            name = 'synthetic-command-' + str(index)
            step = {'name': name, 'argv': logical, 'cwd': self.repo,
                    'judge': lambda result, run: (True, judgment)}
            expected.append(step)
            argv = [context['observer_python'], '-I', '-S', '-B',
                    str(self.repo / (domain.BASE + 'native_source_bootstrap.py')),
                    '--repo', context['repo'], '--helper-revision', context['helper_revision'],
                    '--candidate-revision', context['candidate_revision'], '--entry-role', 'entry.full-products', '--',
                    '--execute-tool', script, '--', '--tool', script, '--capture-root',
                    str(original / 'raw-checkers' / f'{index:02d}'), '--', *logical[2:]]
            body = ('complete synthetic command stream ' + str(index)).encode()
            command = {'argv': argv, 'cwd': str(self.repo), 'exit_code': 0, 'wait_completed': True,
                       'owned_group_cleanup_passed': True, 'pid': 100 + index,
                       'stdout_sha256': hashlib.sha256(body).hexdigest(), 'stderr_sha256': hashlib.sha256(body).hexdigest()}
            row = {'index': index, 'step': name, 'script': script, 'logical_argv': logical, 'cwd': str(self.repo),
                   'command': command, 'passed': True, 'judgment': judgment}
            capture['rows'].append(row)
            directory = root / 'commands' / f'{index:02d}'; directory.mkdir(parents=True)
            for stream in ('stdout', 'stderr'): (directory / (stream + '.raw')).write_bytes(body)
            post = {'tool': script, 'pid': command['pid'], 'helper_revision': 'a' * 40,
                    'candidate_revision': 'a' * 40, 'entry_executed': True, 'entry_exit_code': 0,
                    'source_runtime_tools_inputs_unchanged': True, 'product_acceptance_passed': False}
            checked = {'tool': script, 'entry_executed': True, 'exit_code': 0,
                       'postcheck_complete': True, 'source_sha256': 'c' * 64}
            for member, value in [(directory / 'command.json', command), (directory / 'postcheck.json', post),
                    (root / 'capture/raw-checkers' / f'{index:02d}' / 'checker-postcheck.json', checked)]:
                member.parent.mkdir(parents=True, exist_ok=True); member.write_text(json.dumps(value))
        owner = SimpleNamespace(read=lambda member: member.read_bytes(), parse=json.loads)
        self.assertEqual(len(domain.command_proof(owner, root, capture, expected)), 15)
        row = capture['rows'][0]
        row['command']['argv'] = [context['observer_python'], '-I', '-S', '-B',
            str(self.repo / (domain.BASE + 'run_full_products.py')), '--execute-tool', script, '--',
            '--tool', script, '--capture-root', str(original / 'raw-checkers/01'), '--', *row['logical_argv'][2:]]
        (root / 'commands/01/command.json').write_text(json.dumps(row['command']))
        with self.assertRaisesRegex(ValueError, 'actual isolated qualified checker argv differs'):
            domain.command_proof(owner, root, capture, expected)

    def test_actual_urllib_uses_saved_success_bytes_without_socket(self):
        tape = self.tape()
        with self.guards(tape):
            request = urllib.request.Request(replay.CURRENT + '/v1/compile', data=b'{}')
            with urllib.request.urlopen(request) as reply:
                self.assertEqual(reply.status, 200)
                self.assertEqual(reply.read(), b'{"status":"ok"}')
        self.assertEqual(tape.finish()['http_exchanges'], 1)

    def test_actual_urllib_preserves_original_http_error_body(self):
        tape = self.tape(status=400, reply=b'{"code":"INVALID_VALUE"}')
        with self.guards(tape):
            request = urllib.request.Request(replay.CURRENT + '/v1/compile', data=b'{}')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(caught.exception.read(), b'{"code":"INVALID_VALUE"}')
        tape.finish()

    def test_changed_request_spacing_is_refused(self):
        tape = self.tape()
        request = urllib.request.Request(replay.CURRENT + '/v1/compile', data=b'{ }')
        with self.assertRaisesRegex(ValueError, 'source request/order'):
            tape.open(None, request)

    def test_source_main_cannot_run_a_real_child(self):
        original = subprocess.Popen
        sentinel = self.base / 'child-sentinel'
        with self.assertRaisesRegex(ValueError, 'native command forbidden'):
            with self.guards():
                subprocess.run([sys.executable, '-c', 'open(' + repr(str(sentinel)) + ',"w").write("bad")'])
        self.assertIs(subprocess.Popen, original)
        self.assertFalse(sentinel.exists())

    def test_live_socket_is_refused_and_restored(self):
        original = socket.socket
        with self.assertRaisesRegex(ValueError, 'live network forbidden'):
            with self.guards():
                socket.socket()
        self.assertIs(socket.socket, original)

    def test_original_primary_error_survives_guard_postcheck(self):
        original = subprocess.Popen
        with self.assertRaisesRegex(RuntimeError, 'original failure'):
            with self.guards() as guard:
                guard.process_attempts.append({'api': 'fixture'})
                raise RuntimeError('original failure')
        self.assertIs(subprocess.Popen, original)

    def test_source_write_outside_new_audit_root_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'writes outside'):
            with self.guards():
                (self.repo / 'changed.py').write_text('sentinel')
        self.assertFalse((self.repo / 'changed.py').exists())

    def test_complete_captured_input_is_read_but_mutation_is_refused(self):
        path = self.root / 'input.json'; path.write_bytes(b'{"input":1}')
        expected = {'input.json': {'sha256': replay.sha(path.read_bytes()), 'size': path.stat().st_size,
                                  'mode': path.stat().st_mode & 0o777}}
        with self.guards(saved=expected):
            self.assertEqual(path.read_bytes(), b'{"input":1}')
            (self.out / 'new-report.json').write_bytes(b'{}')
        path.write_bytes(b'{"input":2}')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            with self.guards(saved=expected):
                path.read_bytes()

    def test_pending_authority_stops_before_source_or_main(self):
        called = []
        ctx = SimpleNamespace(qa_mode='native-qa-v1', helper_revision='a' * 40,
            candidate_revision='a' * 40, qa_source_authority={'source_authority_active': False},
            module=lambda *args: called.append(args))
        with self.assertRaisesRegex(ValueError, 'source admission'):
            replay.verify(ctx, self.root, {}, out=self.out / 'new')
        self.assertEqual(called, [])

    def test_only_known_report_elapsed_time_can_change(self):
        class Spans:
            class _Document:
                def __init__(self, raw):
                    # The fixture pins the actual single known numeric token.
                    token = b'1.0' if b'1.0' in raw else b'9.0'
                    start = raw.index(token)
                    self.nodes = {('seconds',): ('scalar', start, start + len(token), None),
                        ('inputs', 'public_run'): ('string', raw.index(b'"/old/public"'),
                            raw.index(b'"/old/public"') + len(b'"/old/public"'), '/old/public'),
                        ('inputs', 'legacy_run'): ('string', raw.index(b'"/old/legacy"'),
                            raw.index(b'"/old/legacy"') + len(b'"/old/legacy"'), '/old/legacy')}
        first = b'{"seconds":1.0,"inputs":{"public_run":"/old/public","legacy_run":"/old/legacy"},"passed":true}'
        second = first.replace(b'1.0', b'9.0')
        mappings = [('/old/public', '<public>'), ('/old/legacy', '<legacy>')]
        self.assertEqual(replay.report_raw('python-replay', 'python-replay.json', first, mappings, Spans),
                         replay.report_raw('python-replay', 'python-replay.json', second, mappings, Spans))
        changed = second.replace(b'"passed":true', b'"passed":false')
        self.assertNotEqual(replay.report_raw('python-replay', 'python-replay.json', first, mappings, Spans),
                            replay.report_raw('python-replay', 'python-replay.json', changed, mappings, Spans))


class GuardOwnershipTests(unittest.TestCase):
    setUp = MainReplayTests.setUp
    guards = MainReplayTests.guards
    def test_replaced_guard_is_refused_without_clobbering_new_owner(self):
        original = subprocess.Popen
        replacement = lambda *args, **kwargs: None
        try:
            with self.assertRaisesRegex(ValueError, 'changed an owned guard'):
                with self.guards():
                    subprocess.Popen = replacement
            self.assertIs(subprocess.Popen, replacement)
        finally:
            subprocess.Popen = original

    def test_replaced_guard_keeps_original_failure_and_new_owner(self):
        original = subprocess.Popen
        replacement = lambda *args, **kwargs: None
        try:
            with self.assertRaisesRegex(RuntimeError, 'first source failure'):
                with self.guards() as guard:
                    subprocess.Popen = replacement
                    raise RuntimeError('first source failure')
            self.assertIs(subprocess.Popen, replacement)
            self.assertEqual(guard.cleanup_errors, [{'attribute': 'Popen',
                'reason': 'owned replay guard was replaced'}])
        finally:
            subprocess.Popen = original


if __name__ == '__main__':
    unittest.main()
