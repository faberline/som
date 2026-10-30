#!/usr/bin/env python3
"""Protocol fixtures; execute only as a GitHub-hosted runner test."""
from __future__ import annotations

import copy
import hashlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

if not (os.environ.get('GITHUB_ACTIONS') == 'true'
        and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
        and os.environ.get('GITHUB_REPOSITORY') == 'faberline/som'):
    raise RuntimeError('execute these fixtures only on a GitHub-hosted runner')

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('m6_hosted_canary_fixture_subject', HERE / 'run_hosted_canaries.py')
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Handler(BaseHTTPRequestHandler):
    status = 200
    body = b'expected-http'

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *_args):
        pass


class HostedCanaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.files = {}

        def file(name, raw):
            path = self.workspace / name
            path.write_bytes(raw)
            return {'path': str(path), 'sha256': sha(raw)}

        cli = (b'#!/usr/bin/env python3\nimport os,sys\n'
               b"sys.stdout.write('wrong' if os.environ.get('M6_FAKE_CLI_WRONG') else 'expected-cli')\n"
               b"sys.exit(7 if os.environ.get('M6_FAKE_CLI_EXIT') else 0)\n")
        binaries = {}
        for role in ('candidate', 'previous'):
            binary = file(role + '-som', cli)
            Path(binary['path']).chmod(0o700)
            binaries[role] = {'binary': binary, **{key: file(role + '-' + key, key.encode())
                               for key in ('build', 'runtime', 'source')}}
        gate = file('accepted-gate', b'fixture')
        self.config = {'version': subject.rc.COMPILER_PREREQUISITES_VERSION, 'profile': 'compiler-only',
                       'candidate': binaries['candidate'], 'previous': binaries['previous'],
                       'gates': {'accepted_issue130': {**gate, 'assertions': {'/passed': True}}},
                       'typescript_formatter': {'tree_sha256': 'formatter-fixture'},
                       'previous_canaries': {}}
        expected_cases = {'candidate': {}, 'previous': {}}
        for role, families in subject.FAMILIES.items():
            for family in families:
                actual, wanted = [], []
                for number in range(2):
                    name = role + '-' + family + '-' + str(number)
                    request = file(name + '-request', b'input')
                    response = file(name + '-response', b'expected-http')
                    payload = file(name + '-input', b'input')
                    cli_response = file(name + '-stdout', b'expected-cli')
                    binary_role = 'new' if role == 'candidate' else 'previous'
                    source = {'fixture': name}
                    steps = [
                        {'kind': 'http', 'name': 'compile', 'path': '/v1/compile',
                         'request': request, 'response': response},
                        {'kind': 'http', 'name': 'assemble', 'path': '/v1/assemble',
                         'request': request, 'response': response},
                        {'kind': 'cli', 'name': 'cli-assemble', 'args': ['assemble', payload['path']],
                         'inputs': [payload], 'response': cli_response, 'binaries': [binary_role],
                         'expectation_origin': 'fixture'},
                    ]
                    actual.append({'name': name, 'family': family, 'source': source, 'steps': steps})
                    wanted.append({'name': name, 'family': family, 'source': source, 'steps': [
                        {'kind': 'http', 'name': 'compile', 'path': '/v1/compile',
                         'request_sha256': request['sha256'], 'response_sha256': response['sha256']},
                        {'kind': 'http', 'name': 'assemble', 'path': '/v1/assemble',
                         'request_sha256': request['sha256'], 'response_sha256': response['sha256']},
                        {'kind': 'cli', 'name': 'cli-assemble', 'args': ['assemble', 'sha256:' + payload['sha256']],
                         'inputs_sha256': [payload['sha256']], 'response_sha256': cli_response['sha256'],
                         'binaries': [binary_role], 'expectation_origin': 'fixture'},
                    ]})
                expected_cases[role][family] = wanted
                if role == 'previous':
                    self.config['previous_canaries'][family] = actual
                else:
                    self.config[family + '_canaries'] = actual
        self.expected = {'version': 'som.m6.runner-canary-expected.v1',
                         'accepted_receipt_sha256': gate['sha256'], 'cases': expected_cases,
                         'compiler_hashes': {role: {key: binaries[role][key]['sha256']
                                             for key in ('binary', 'build', 'runtime', 'source')}
                                             for role in binaries},
                         'gate_hashes': {'accepted_issue130': gate['sha256']},
                         'gate_assertions': {'accepted_issue130': {'/passed': True}},
                         'formatter_tree_sha256': 'formatter-fixture'}
        self.guard = mock.patch.object(subject.rc, 'check_prerequisites', return_value=None)
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.formatter = mock.patch.object(subject.rc, 'apply_formatter_env', return_value=None)
        self.formatter.start()
        self.addCleanup(self.formatter.stop)
        self.server = None
        for port in subject.rc.HARNESS_PORTS:
            try:
                self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
                break
            except OSError:
                continue
        self.assertIsNotNone(self.server, 'no fake harness port')
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port
        Handler.status, Handler.body = 200, b'expected-http'
        self.addCleanup(setattr, Handler, 'status', 200)
        self.addCleanup(setattr, Handler, 'body', b'expected-http')
        self.output = self.workspace / 'out'
        self.output.mkdir()
        self.prior = copy.deepcopy(self.config)
        self.current_path = self.workspace / 'current-prerequisites.json'
        self.prior_path = self.workspace / 'prior-prerequisites.json'
        self.archive_path = self.workspace / 'synthetic-sealed-archive'
        self.archive_path.write_bytes(b'synthetic sealed archive')
        self.runner = {'run_id': '1', 'run_attempt': '1', 'source_commit': 'a' * 40}

    def run_role(self, role='candidate'):
        self.current_path.write_text(json.dumps(self.config))
        self.prior_path.write_text(json.dumps(self.prior))
        anchor = {'prior_path': self.prior_path, 'prior_sha256': sha(self.prior_path.read_bytes()),
                  'archive_path': self.archive_path, 'archive_sha256': sha(self.archive_path.read_bytes())}
        return subject.run_cases(self.current_path, anchor, self.workspace, role, self.port, self.output, self.runner)

    def test_candidate_runs_all_eight_cases_and_retains_raw(self):
        receipt = self.run_role()
        self.assertTrue(receipt['passed'])
        self.assertEqual(len(receipt['expected_case_names']), 8)
        self.assertFalse(receipt['production_host_proven'])
        self.assertFalse(receipt['production_acceptance_passed'])
        self.assertFalse(receipt['check_default_replaced'])
        self.assertEqual(receipt['per_exchange_status_or_exit'], 'not separately recorded by Release')
        self.assertEqual(len(receipt['raw_files']), 57)
        self.assertTrue(any(row['name'].endswith('-response.raw') for row in receipt['raw_files']))
        self.assertTrue(any(row['name'].endswith('-cli-stdout.raw') for row in receipt['raw_files']))
        self.assertTrue(any(row['name'].endswith('-cli-stderr.raw') for row in receipt['raw_files']))
        self.assertTrue(any(row['name'].endswith('-cli-argv.json') for row in receipt['raw_files']))
        for row in receipt['raw_files']:
            self.assertEqual(sha((self.output / row['name']).read_bytes()), row['sha256'])

    def test_previous_runs_all_six_cases(self):
        self.assertEqual(len(self.run_role('previous')['expected_case_names']), 6)

    def test_missing_case_refused(self):
        self.config['react_canaries'].pop()
        with self.assertRaises(ValueError):
            self.run_role()

    def test_reordered_case_refused(self):
        self.config['rust_canaries'].reverse()
        with self.assertRaises(ValueError):
            self.run_role()

    def test_wrong_binary_hash_refused(self):
        self.config['candidate']['binary']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.run_role()

    def test_changed_request_bytes_refused(self):
        path = Path(self.config['react_canaries'][0]['steps'][0]['request']['path'])
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.run_role()

    def test_changed_gate_assertion_refused(self):
        self.config['gates']['accepted_issue130']['assertions']['/passed'] = False
        with self.assertRaises(ValueError):
            self.run_role()

    def test_unknown_role_refused(self):
        with self.assertRaises(ValueError):
            self.run_role('other')

    def test_protected_port_refused(self):
        self.port = 8765
        with self.assertRaises(Exception):
            self.run_role()

    def test_http_status_failure_is_not_success(self):
        Handler.status = 503
        with self.assertRaises(Exception):
            self.run_role()
        self.assertFalse(json.loads((self.output / 'hosted-canary-result.json').read_bytes())['passed'])

    def test_http_body_failure_is_not_success(self):
        Handler.body = b'wrong'
        with self.assertRaises(Exception):
            self.run_role()
        self.assertFalse(json.loads((self.output / 'hosted-canary-result.json').read_bytes())['passed'])

    def test_cli_exit_failure_is_not_success(self):
        with mock.patch.dict(os.environ, {'M6_FAKE_CLI_EXIT': '1'}):
            with self.assertRaises(Exception):
                self.run_role()
        self.assertFalse(json.loads((self.output / 'hosted-canary-result.json').read_bytes())['passed'])

    def test_cli_stdout_failure_is_not_success(self):
        with mock.patch.dict(os.environ, {'M6_FAKE_CLI_WRONG': '1'}):
            with self.assertRaises(Exception):
                self.run_role()
        self.assertFalse(json.loads((self.output / 'hosted-canary-result.json').read_bytes())['passed'])

    def test_http_timeout_is_not_success(self):
        with mock.patch.object(subject.rc, 'post_raw', side_effect=socket.timeout('fixture')):
            with self.assertRaises(socket.timeout):
                self.run_role()
        self.assertFalse(json.loads((self.output / 'hosted-canary-result.json').read_bytes())['passed'])

    def test_cli_timeout_is_not_success(self):
        with mock.patch.object(subject.rc.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired('fixture-cli', 120)):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_role()
        self.assertFalse(json.loads((self.output / 'hosted-canary-result.json').read_bytes())['passed'])

    def test_input_mutation_during_canary_is_not_success(self):
        path = Path(self.config['react_canaries'][0]['steps'][0]['request']['path'])
        def mutate(_runner, _port, _binaries, _cases, _label):
            path.write_bytes(b'changed-after-precheck')
        with mock.patch.object(subject.rc.Release, 'canary', autospec=True, side_effect=mutate):
            with self.assertRaises(ValueError):
                self.run_role()
        self.assertFalse(json.loads((self.output / 'hosted-canary-result.json').read_bytes())['passed'])

    def test_symlink_bound_file_is_refused(self):
        binding = self.config['react_canaries'][0]['steps'][0]['request']
        original = Path(binding['path'])
        alias = self.workspace / 'alias'
        alias.symlink_to(original)
        binding['path'] = str(alias)
        with self.assertRaises(ValueError):
            self.run_role()

    def test_non_runner_environment_is_refused(self):
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}, clear=True):
            with self.assertRaises(ValueError):
                subject.runner_identity()

    def test_canary_refuses_inherited_credentials(self):
        with mock.patch.dict(os.environ, {'M6_INPUTS_KEYRING': 'synthetic-keyring'}):
            with self.assertRaises(ValueError):
                self.run_role()
        receipt = json.loads((self.output / 'hosted-canary-result.json').read_bytes())
        self.assertFalse(receipt['passed'])
        self.assertEqual(receipt['first_error_phase'], 'preflight')

    def test_synthetic_inventory_projection_is_complete(self):
        self.assertTrue(subject.same(subject.inventory(self.config), self.expected))
        self.assertEqual(sum(len(cases) for cases in self.expected['cases']['candidate'].values()), 8)
        self.assertEqual(sum(len(cases) for cases in self.expected['cases']['previous'].values()), 6)

    def test_independent_prior_projection_mismatch_is_refused(self):
        self.prior['react_canaries'][0]['steps'][0]['response']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.run_role()

    def test_prelude_failure_records_failure_receipt(self):
        self.config['react_canaries'].clear()
        with self.assertRaises(ValueError):
            self.run_role()
        receipt = json.loads((self.output / 'hosted-canary-result.json').read_bytes())
        self.assertFalse(receipt['passed'])
        self.assertEqual(receipt['first_error_phase'], 'preflight')
        self.assertEqual(receipt['first_error_type'], 'ValueError')

    def test_failure_raw_hash_and_receipt_are_attempted(self):
        Handler.status = 503
        with self.assertRaises(RuntimeError):
            self.run_role()
        receipt = json.loads((self.output / 'hosted-canary-result.json').read_bytes())
        self.assertFalse(receipt['passed'])
        self.assertEqual(receipt['first_error_phase'], 'canary')
        self.assertTrue(receipt['raw_files'])
        for row in receipt['raw_files']:
            self.assertEqual(sha((self.output / row['name']).read_bytes()), row['sha256'])

    def test_receipt_failure_does_not_mask_first_error(self):
        first = RuntimeError('synthetic first error')
        actual_open = Path.open
        def failing_open(path, *args, **kwargs):
            if path.name == 'hosted-canary-result.json':
                raise OSError('synthetic receipt failure')
            return actual_open(path, *args, **kwargs)
        with mock.patch.object(subject.rc, 'post_raw', side_effect=first), \
             mock.patch.object(Path, 'open', failing_open):
            with self.assertRaises(RuntimeError) as raised:
                self.run_role()
        self.assertIs(raised.exception, first)
        self.assertTrue(any('evidence operation failed' in n for n in getattr(first, '__notes__', [])))

    def test_raw_hash_failure_does_not_mask_first_error(self):
        first = RuntimeError('synthetic first error')
        actual_digest = subject.rc.digest
        def failing_digest(path):
            if str(path).endswith('-request.raw'):
                raise OSError('synthetic raw hash failure')
            return actual_digest(path)
        with mock.patch.object(subject.rc, 'post_raw', side_effect=first), \
             mock.patch.object(subject.rc, 'digest', side_effect=failing_digest):
            with self.assertRaises(RuntimeError) as raised:
                self.run_role()
        self.assertIs(raised.exception, first)
        receipt = json.loads((self.output / 'hosted-canary-result.json').read_bytes())
        self.assertFalse(receipt['passed'])
        self.assertEqual(receipt['evidence_failures'][0]['phase'], 'raw_hash')

    def test_raw_hash_failure_after_canaries_is_not_success(self):
        actual_digest = subject.rc.digest
        def failing_digest(path):
            if str(path).endswith('-request.raw'):
                raise OSError('synthetic raw hash failure')
            return actual_digest(path)
        with mock.patch.object(subject.rc, 'digest', side_effect=failing_digest):
            with self.assertRaises(OSError):
                self.run_role()
        receipt = json.loads((self.output / 'hosted-canary-result.json').read_bytes())
        self.assertFalse(receipt['passed'])
        self.assertEqual(receipt['evidence_failures'][0]['phase'], 'raw_hash')

    def test_partial_receipt_write_fails_and_is_not_valid_json(self):
        def partial_write(_receipt, stream, **_kwargs):
            stream.write('{"passed":')
            raise OSError('synthetic partial write')
        with mock.patch.object(subject.json, 'dump', side_effect=partial_write):
            with self.assertRaises(OSError):
                self.run_role()
        with self.assertRaises(json.JSONDecodeError):
            json.loads((self.output / 'hosted-canary-result.json').read_bytes())

    def test_receipt_sync_failure_fails_even_if_json_is_complete(self):
        with mock.patch.object(subject, 'sync_receipt', side_effect=OSError('synthetic sync failure')):
            with self.assertRaises(OSError):
                self.run_role()
        # This may be valid JSON with passed=true. The failing runner step is
        # authoritative; the separate completion verifier must reject it.
        receipt = json.loads((self.output / 'hosted-canary-result.json').read_bytes())
        self.assertTrue(receipt['passed'])

    def test_existing_receipt_is_not_overwritten_or_tested(self):
        receipt = self.output / 'hosted-canary-result.json'
        receipt.write_bytes(b'existing')
        with mock.patch.object(subject.rc.Release, 'canary') as canary:
            with self.assertRaises(ValueError):
                self.run_role()
        canary.assert_not_called()
        self.assertEqual(receipt.read_bytes(), b'existing')

    def test_prior_mutation_during_canary_is_not_success(self):
        def mutate(_runner, _port, _binaries, _cases, _label):
            self.prior_path.write_text('{}')
        with mock.patch.object(subject.rc.Release, 'canary', autospec=True, side_effect=mutate):
            with self.assertRaises(ValueError):
                self.run_role()
        self.assertFalse(json.loads((self.output / 'hosted-canary-result.json').read_bytes())['passed'])

    def test_failed_canary_still_postchecks_both_inputs(self):
        first = RuntimeError('synthetic primary')
        def mutate_then_fail(*_args):
            self.prior_path.write_text('{}')
            raise first
        with mock.patch.object(subject.rc, 'post_raw', side_effect=mutate_then_fail):
            with self.assertRaises(RuntimeError) as raised:
                self.run_role()
        self.assertIs(raised.exception, first)
        receipt = json.loads((self.output / 'hosted-canary-result.json').read_bytes())
        self.assertFalse(receipt['passed'])
        self.assertFalse(receipt['inputs_postchecked'])
        self.assertEqual(receipt['first_error_phase'], 'canary')
        self.assertIn('inputs_postcheck', [item['phase'] for item in receipt['evidence_failures']])

    def test_self_comparison_is_refused(self):
        self.current_path.write_text(json.dumps(self.config))
        anchor = {'prior_path': self.current_path, 'prior_sha256': sha(self.current_path.read_bytes()),
                  'archive_path': self.archive_path, 'archive_sha256': sha(self.archive_path.read_bytes())}
        with self.assertRaises(ValueError):
            subject.run_cases(self.current_path, anchor, self.workspace, 'candidate', self.port,
                              self.output, self.runner)
        receipt = json.loads((self.output / 'hosted-canary-result.json').read_bytes())
        self.assertFalse(receipt['passed'])

    def test_public_error_output_does_not_contain_error_text(self):
        stream = io.StringIO()
        with mock.patch.object(subject, 'main', side_effect=ValueError('synthetic private case name')), \
             mock.patch.object(subject.sys, 'stderr', stream):
            self.assertEqual(subject.entry(), 1)
        self.assertEqual(stream.getvalue(), 'hosted canary failed\n')

    def test_runner_event_and_checkout_must_match(self):
        event = self.workspace / 'event.json'
        repo = {'id': subject.REPOSITORY_ID, 'full_name': 'faberline/som'}
        event.write_text(json.dumps({'repository': repo, 'pull_request': {
            'head': {'sha': 'a' * 40, 'repo': repo}, 'base': {'repo': repo}}}))
        env = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'macOS',
               'GITHUB_REPOSITORY': 'faberline/som', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
               'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_EVENT_PATH': str(event)}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(subject.subprocess, 'check_output', return_value='a' * 40 + '\n'):
            self.assertEqual(subject.runner_identity()['source_commit'], 'a' * 40)
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(subject.subprocess, 'check_output', return_value='b' * 40 + '\n'):
            with self.assertRaises(ValueError):
                subject.runner_identity()

    def test_fork_pull_request_is_refused(self):
        event = self.workspace / 'fork-event.json'
        repo = {'id': subject.REPOSITORY_ID, 'full_name': 'faberline/som'}
        fork = {'id': 999, 'full_name': 'attacker/fork'}
        event.write_text(json.dumps({'repository': repo, 'pull_request': {
            'head': {'sha': 'a' * 40, 'repo': fork}, 'base': {'repo': repo}}}))
        env = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'macOS',
               'GITHUB_REPOSITORY': 'faberline/som', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
               'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_EVENT_PATH': str(event)}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(subject.subprocess, 'check_output') as git:
            with self.assertRaises(ValueError):
                subject.runner_identity()
        git.assert_not_called()

    def test_runner_temp_audit_binding_is_allowed_only_when_declared(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'audit.json'
            path.write_bytes(b'audit')
            binding = {'path': str(path), 'sha256': sha(b'audit')}
            with self.assertRaises(ValueError):
                subject.pin_binding(binding, (self.workspace,))
            subject.pin_binding(binding, (self.workspace, path.parent))


if __name__ == '__main__':
    unittest.main()
