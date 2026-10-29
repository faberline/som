"""Synthetic saved bytes and reports only. No compiler or service is executed."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
CHECKS = ('owner', 'health', 'pointers', 'previous_compiler_inputs', 'canaries', 'owner_after')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else encoded(value))


def forbidden(*_args, **_kwargs):
    raise AssertionError('a process or network operation must not run in this fixture')


def modules():
    rc = types.SimpleNamespace(require=require, digest=digest,
        load=lambda path: json.loads(Path(path).read_bytes()), alive=forbidden, port_free=forbidden,
        identity=forbidden, PROTECTED_PORTS={8765, 18765},
        exact_json=lambda a, b: json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))
    rc.harness_port = lambda port: require(type(port) is int and 18919 <= port < 19000, 'wrong harness port')
    loaded = []
    with mock.patch.dict(sys.modules, {'release_common': rc}):
        for filename in ('prepare_compiler_inputs', 'run_compiler_rehearsal'):
            spec = importlib.util.spec_from_file_location('fixture_' + filename, HERE / (filename + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded.append(module)
    return rc, *loaded


class CompilerInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='som-release-inputs-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.rc, self.producer, self.runner = modules()

    def test_final_mac_receipt_and_independent_review_are_exactly_joined(self):
        revision, binary, audit, raw = '1' * 40, '2' * 64, '3' * 64, '4' * 64
        review_path = self.root / 'evidence/review.json'
        review = {'version': 'som.react-output-independent-acceptance-review.v1', 'findings': [],
                  'candidate_binary_sha256': binary, 'compiler_source_commit': revision,
                  'audit_sha256': audit, 'raw_run_sha256': raw}
        write(review_path, review)
        accepted = {'version': 'som.react-output-acceptance-evidence.v1',
                    'status': 'accepted_after_independent_raw_audit',
                    'candidate': {'source_commit': revision, 'binary_sha256': binary},
                    'baseline': {'binary_sha256': self.producer.BASELINE_PINS['som']},
                    'independent_audit': {'summary_sha256': '5' * 64, 'audit_sha256': audit},
                    'independent_review': {'path': 'evidence/review.json', 'sha256': digest(review_path)},
                    'report_sha256': {'run/run.json': raw}}
        receipt = self.root / 'receipt.json'
        write(receipt, accepted)
        with mock.patch.object(self.rc, 'REPO', self.root, create=True):
            self.assertEqual(self.producer.final_receipt(receipt, digest(receipt), revision), accepted)
            self.assertEqual(self.producer.final_review(accepted), review_path)
            with self.assertRaises(RuntimeError):
                self.producer.final_receipt(receipt, '0' * 64, revision)
            with self.assertRaises(RuntimeError):
                self.producer.final_receipt(receipt, digest(receipt), '9' * 40)
            review['candidate_binary_sha256'] = '9' * 64
            write(review_path, review)
            accepted['independent_review']['sha256'] = digest(review_path)
            with self.assertRaises(RuntimeError):
                self.producer.final_review(accepted)

    def test_final_mac_receipt_refuses_old_binary(self):
        accepted = {'version': 'som.react-output-acceptance-evidence.v1',
                    'status': 'accepted_after_independent_raw_audit',
                    'candidate': {'source_commit': '1' * 40,
                                  'binary_sha256': '59dab78a0f401a86e028c186285a888cfdf304b2d077e167d46b20d1131a3308'},
                    'baseline': {'binary_sha256': self.producer.BASELINE_PINS['som']},
                    'independent_audit': {'summary_sha256': '5' * 64, 'audit_sha256': '3' * 64}}
        receipt = self.root / 'old.json'
        write(receipt, accepted)
        with self.assertRaises(RuntimeError):
            self.producer.final_receipt(receipt, digest(receipt), '1' * 40)

    def test_protected_port_guard_only_reads_listener_metadata(self):
        with mock.patch.object(self.runner.subprocess, 'run', return_value=types.SimpleNamespace(
                returncode=1, stdout=b'', stderr=b'')) as command, \
                mock.patch.object(self.rc, 'port_free', side_effect=forbidden):
            result = self.runner.protected_ports_absent()
        self.assertEqual([r['port'] for r in result], [8765, 18765])
        self.assertEqual([c.args[0] for c in command.call_args_list], [
            ['/usr/sbin/lsof', '-nP', '-a', '-iTCP:8765', '-sTCP:LISTEN', '-Fp'],
            ['/usr/sbin/lsof', '-nP', '-a', '-iTCP:18765', '-sTCP:LISTEN', '-Fp']])
        for code, stdout, stderr in [(0, b'p123\n', b''), (1, b'', b'permission denied'), (2, b'', b'')]:
            with self.subTest(code=code, stderr=stderr), mock.patch.object(self.runner.subprocess, 'run',
                    return_value=types.SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)), self.assertRaises(RuntimeError):
                self.runner.protected_ports_absent()

    def test_cli_pretty_keeps_number_tokens_unicode_and_nested_types(self):
        raw = b'{"status":"ok","bundle":{"z":1e-07,"a":-0.0,"big":9007199254740993,"string":"1e-07","nested":[true,null,{},[]],"text":"\u00e9\ud83d\ude00\u0001"}}'
        expected = ('{\n  "a": -0.0,\n  "big": 9007199254740993,\n  "nested": [\n'
                    '    true,\n    null,\n    {},\n    []\n  ],\n  "string": "1e-07",\n'
                    '  "text": "é😀\\u0001",\n  "z": 1e-07\n}\n').encode()
        self.assertEqual(self.producer.cli_json_bytes(raw, 'bundle'), expected)
        self.assertEqual(self.producer.cli_json_bytes(b'{}'), b'{}\n')

    def calibration(self, root):
        rows = []
        for index in range(9):
            response = f'{{"status":"ok","bundle":{{"name":"case-{index}","value":-0.0}}}}'.encode()
            stdout = f'{{\n  "name": "case-{index}",\n  "value": -0.0\n}}\n'.encode()
            response_name, stdout_name = f'http-{index}.json', f'cli-{index}.stdout'
            write(root / 'run/react-target' / response_name, response)
            write(root / 'run/react-target' / stdout_name, stdout)
            rows.append({'http_status': 200, 'exit_code': 0, 'passed': True,
                'http_response_file': response_name, 'http_response_sha256': hashlib.sha256(response).hexdigest(),
                'stdout_file': stdout_name, 'stdout_sha256': hashlib.sha256(stdout).hexdigest()})
        rows += [{'http_status': 400, 'exit_code': 2, 'passed': True} for _ in range(13)]
        report = {'cli': {'passed': True, 'total': 22, 'passed_count': 22, 'rows': rows}}
        write(root / 'run/react-target/report.json', report)
        return report

    def test_all_nine_saved_cli_captures_must_match_independent_pretty_bytes(self):
        root = self.root / 'calibration'
        report = self.calibration(root)
        result = self.producer.calibrate(self.producer.Sources(root))
        self.assertEqual(result['captured_compile_cases'], 9)
        self.assertIs(result['passed'], True)
        changed = b'{\n  "name": "wrong",\n  "value": -0.0\n}\n'
        write(root / 'run/react-target/cli-8.stdout', changed)
        report['cli']['rows'][8]['stdout_sha256'] = hashlib.sha256(changed).hexdigest()
        write(root / 'run/react-target/report.json', report)
        with self.assertRaisesRegex(RuntimeError, 'calibration'):
            self.producer.calibrate(self.producer.Sources(root))

    def test_calibration_refuses_missing_success_or_failed_cli(self):
        for mutation in ('eight', 'exit'):
            with self.subTest(mutation=mutation):
                root = self.root / mutation
                report = self.calibration(root)
                if mutation == 'eight':
                    report['cli']['rows'][8]['http_status'] = 400
                else:
                    report['cli']['rows'][8]['exit_code'] = 1
                write(root / 'run/react-target/report.json', report)
                with self.assertRaises(RuntimeError):
                    self.producer.calibrate(self.producer.Sources(root))

    def test_sources_refuse_traversal_symlinks_duplicate_json_and_hash_drift(self):
        root = self.root / 'sources'
        write(root / 'data.json', b'{"one":1}')
        source = self.producer.Sources(root)
        original = source.read('data.json')
        self.assertEqual(source.files['data.json'], hashlib.sha256(original).hexdigest())
        for name in ('../outside', str(root / 'data.json')):
            with self.subTest(path=name), self.assertRaises(RuntimeError):
                source.read(name)
        (root / 'link').symlink_to(root / 'data.json')
        with self.assertRaises(RuntimeError):
            source.read('link')
        write(root / 'duplicate.json', b'{"same":1,"same":2}')
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            source.json('duplicate.json')
        write(root / 'data.json', b'{"one":2}')
        with self.assertRaises(RuntimeError):
            source.read('data.json', hashlib.sha256(original).hexdigest())
        with self.assertRaises(RuntimeError):
            source.read('data.json')
        self.assertEqual(source.files['data.json'], hashlib.sha256(original).hexdigest())

    def case_bytes(self, family='react', cnl=True):
        request = {'format': 'som.cnl.v1', 'source': 'Fake public CNL.\n'} if cnl else {
            'format': 'som.prompt-json.v1', 'prompt': {'version': 'fake.prompt.v1'}}
        if family != 'python':
            request['target'] = family
        bundle = {'version': 'fake.bundle.v1', 'prompt': {'value': 'é'}}
        if family != 'python':
            bundle['target'] = family
        return [encoded(request), encoded({'status': 'ok', 'bundle': bundle}),
                encoded({'bundle': bundle}), encoded({'status': 'ok', 'files': {'README.md': 'fake é\n'}})]

    def test_pinner_binds_raw_bytes_and_each_role_own_cli(self):
        for role, label in (('candidate', 'new'), ('previous', 'previous')):
            values = self.case_bytes('typescript')
            case = self.producer.Pinner(self.root / role).case(role, 'typescript', 'case', *values, {'kind': 'fixture'})
            self.assertEqual([step['kind'] for step in case['steps']], ['http', 'http', 'cli', 'cli'])
            self.assertEqual(Path(case['steps'][0]['request']['path']).read_bytes(), values[0])
            for step in case['steps']:
                self.assertEqual(digest(step['response']['path']), step['response']['sha256'])
                if step['kind'] == 'cli':
                    self.assertEqual(step['binaries'], [label])
                    self.assertEqual(step['inputs'][0]['path'], step['args'][1])
            self.assertEqual(case['steps'][-1]['args'][-2:], ['--target', 'typescript'])
            self.assertIn('not a historical CLI capture', case['steps'][2]['expectation_origin'])

    def test_pinner_refuses_wrong_target_or_unrelated_assembly_bundle_before_writes(self):
        for mutation in ('request', 'bundle-target', 'assembly'):
            with self.subTest(mutation=mutation):
                values = self.case_bytes()
                if mutation == 'request':
                    changed = json.loads(values[0]); changed['target'] = 'rust'; values[0] = encoded(changed)
                elif mutation == 'bundle-target':
                    changed = json.loads(values[1]); changed['bundle']['target'] = 'rust'; values[1] = encoded(changed)
                    values[2] = encoded({'bundle': changed['bundle']})
                else:
                    values[2] = encoded({'bundle': {'target': 'react', 'wrong': True}})
                destination = self.root / ('wrong-' + mutation)
                with self.assertRaises(RuntimeError):
                    self.producer.Pinner(destination).case('candidate', 'react', 'case', *values, {})
                self.assertFalse(destination.exists())

    def check_result(self, expected):
        return {'passed': True, 'expect_binary_sha256': expected,
                'checks': {name: {'passed': True} for name in CHECKS}}

    def test_independent_checks_require_exact_set_binary_and_true_statuses(self):
        expected = 'a' * 64
        path = self.root / 'check.json'
        write(path, self.check_result(expected))
        self.runner.verify_checks(path, expected)
        for mutation in ('missing', 'extra', 'binary', 'false', 'integer'):
            result = self.check_result(expected)
            if mutation == 'missing':
                del result['checks']['owner_after']
            elif mutation == 'extra':
                result['checks']['unknown'] = {'passed': True}
            elif mutation == 'binary':
                result['expect_binary_sha256'] = 'b' * 64
            else:
                result['checks']['owner']['passed'] = False if mutation == 'false' else 1
            write(path, result)
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                self.runner.verify_checks(path, expected)

    def rehearsal(self, name):
        directory = self.root / name
        config = {role: {'binary': {'sha256': char * 64}} for role, char in (('candidate', 'a'), ('previous', 'b'))}
        config['previous_canaries'] = {family: [{'name': family + '-1'}, {'name': family + '-2'}]
                                      for family in ('python', 'rust', 'typescript')}
        rows = []
        for index, scenario in enumerate(self.runner.SCENARIOS):
            root = directory / scenario
            pid = 5000 + index
            seed_pid, resumed_pid = 7000 + index, 6000 + index
            stale = {'pid': seed_pid, 'identity': {'binary_sha256': config['previous']['binary']['sha256']}}
            write(root / 'stand-in/stale-default.json', stale)
            seed = {'pid': seed_pid, 'old_pid_alive': False, 'exit_code': 0,
                    'argv': ['/fake/python', '-c', 'import os; print(os.getpid())'],
                    'receipt': 'stand-in/stale-default.json', 'default_sha256': digest(root / 'stand-in/stale-default.json')}
            for stream in ('stdout', 'stderr'):
                path = root / f'stand-in/seed.{stream}'
                write(path, f'{seed_pid}\n'.encode() if stream == 'stdout' else b'')
                seed[stream + '_file'] = f'stand-in/seed.{stream}'
                seed[stream + '_sha256'] = digest(path)
            resume_call = {'name': 'resume', 'exit_code': 0, 'wait_result': 0}
            for stream in ('stdout', 'stderr'):
                path = root / f'resume.{stream}'
                write(path, b'fake resume stream\n')
                resume_call[stream + '_sha256'] = digest(path)
            resumed_owner = {'pid': resumed_pid, 'binary_sha256': config['previous']['binary']['sha256']}
            fresh = {'pid': resumed_pid, 'identity': resumed_owner}
            snapshot = root / 'switch/0002-previous-default.json'
            write(snapshot, fresh)
            resume_result = {'passed': True, 'status': 'passed', 'old_pid': seed_pid, 'old_pid_alive': False,
                             'previous_binary_sha256': config['previous']['binary']['sha256'],
                             'stale_snapshot_sha256': seed['default_sha256'], 'default_sha256': digest(snapshot),
                             'owner': resumed_owner}
            write(root / 'resume/result.json', resume_result)
            resume_journal = [{'stage': 'stale_owner_absent', 'pid': seed_pid, 'alive': False, 'port': 18940 + index}]
            resume_journal += [{'stage': 'canary_passed', 'name': case['name'], 'label': 'resumed-previous-default'}
                               for cases in config['previous_canaries'].values() for case in cases]
            resume_journal += [{'stage': 'resume_verified', 'owner': resumed_owner, 'evidence_errors': []}]
            write(root / 'resume/journal.json', resume_journal)
            calls = []
            names = ('switch', 'check', 'restore', 'check-restored') if scenario == 'switch' else ('switch', 'check')
            for operation in names:
                call = {'name': operation, 'exit_code': 1 if operation == 'switch' and scenario != 'switch' else 0}
                call['wait_result'] = call['exit_code']
                for stream in ('stdout', 'stderr'):
                    path = root / (operation + '.' + stream)
                    write(path, (f'fake {scenario} {operation} {stream}\n').encode())
                    call[stream + '_sha256'] = digest(path)
                calls.append(call)
            journal = [{'stage': 'committed'}] if scenario == 'switch' else [
                {'stage': 'failed', 'error': f'injected failure at {scenario}'}, {'stage': 'rollback_verified'}]
            write(root / 'switch/journal.json', journal)
            expected = config['candidate' if scenario == 'switch' else 'previous']['binary']['sha256']
            write(root / 'check/result.json', self.check_result(expected))
            if scenario == 'switch':
                write(root / 'restore/journal.json', [{'stage': 'restore_verified'}])
                write(root / 'check-restored/result.json', self.check_result(config['previous']['binary']['sha256']))
            write(root / 'root/som-rs/runtime/default.json', {'pid': pid, 'identity': {'binary_sha256': config['previous']['binary']['sha256']}})
            rows.append({'name': scenario, 'passed': True, 'problems': [], 'port': 18940 + index,
                         'root': str(root / 'root'), 'calls': calls, 'stopped_pid': pid,
                         'offline_seed': seed, 'resume': resume_call})
        report = {'passed': True, 'profile': 'compiler-only', 'live_default_accessed': False, 'scenarios': rows}
        write(directory / 'rehearsal.json', report)
        return directory, config, report

    def verify(self, directory, config, alive=False):
        with mock.patch.object(self.rc, 'alive', return_value=alive) as owners, \
             mock.patch.object(self.rc, 'port_free') as ports, \
             mock.patch.object(self.rc, 'identity', side_effect=forbidden):
            result = self.runner.verify_report(directory, config)
        self.assertEqual(owners.call_count, 10)
        self.assertEqual([call.args[0] for call in ports.call_args_list], list(range(18940, 18945)))
        return result

    def test_complete_five_scenario_report_binds_streams_checks_and_stopped_owners(self):
        directory, config, report = self.rehearsal('valid')
        self.assertEqual(self.verify(directory, config), report)

    def test_missing_or_wrong_child_wait_refuses(self):
        for target, value in (('resume', None), ('switch', None), ('switch', False), ('switch', 8)):
            with self.subTest(target=target, value=value):
                directory, config, report = self.rehearsal(f'wait-{target}-{value}')
                row = report['scenarios'][0]
                call = row['resume'] if target == 'resume' else row['calls'][0]
                call['wait_result'] = value
                write(directory / 'rehearsal.json', report)
                with self.assertRaises(RuntimeError):
                    self.verify(directory, config)

    def test_resume_proof_mutations_refuse_even_when_switch_and_checks_pass(self):
        for mutation in ('exit', 'seed_zero', 'seed_command', 'seed_stream', 'stale_receipt',
                         'resume_stream', 'journal', 'failed_event', 'evidence_errors', 'result', 'snapshot', 'canaries'):
            with self.subTest(mutation=mutation):
                directory, config, report = self.rehearsal('resume-' + mutation)
                row = report['scenarios'][0]
                root = directory / 'switch'
                if mutation == 'exit': row['resume']['exit_code'] = 1
                elif mutation == 'seed_zero': row['offline_seed']['pid'] = 0
                elif mutation == 'seed_command': row['offline_seed']['argv'][-1] = 'other code'
                elif mutation == 'seed_stream': write(root / 'stand-in/seed.stdout', b'999\n')
                elif mutation == 'stale_receipt': write(root / 'stand-in/stale-default.json', b'{}')
                elif mutation == 'resume_stream': write(root / 'resume.stdout', b'changed')
                elif mutation == 'snapshot': write(root / 'switch/0002-previous-default.json', b'{}')
                elif mutation == 'result':
                    result = json.loads((root / 'resume/result.json').read_bytes())
                    result['passed'] = False
                    write(root / 'resume/result.json', result)
                else:
                    journal = json.loads((root / 'resume/journal.json').read_bytes())
                    if mutation == 'journal': journal[-1]['stage'] = 'resume_committed'
                    elif mutation == 'failed_event': journal.insert(0, {'stage': 'resume_failed'})
                    elif mutation == 'evidence_errors': journal[-1]['evidence_errors'] = ['lost raw bytes']
                    else: journal.pop(1)
                    write(root / 'resume/journal.json', journal)
                write(directory / 'rehearsal.json', report)
                with self.assertRaises(RuntimeError):
                    self.verify(directory, config)

    def test_scenario_set_calls_qa_root_and_stop_receipt_mutations_refuse(self):
        for mutation in ('missing', 'extra', 'order', 'calls', 'exit', 'root', 'pid', 'port'):
            directory, config, report = self.rehearsal('bad-' + mutation)
            if mutation == 'missing':
                report['scenarios'].pop()
            elif mutation == 'extra':
                report['scenarios'].append(copy.deepcopy(report['scenarios'][0]))
            elif mutation == 'order':
                report['scenarios'].reverse()
            else:
                row = report['scenarios'][1]
                if mutation == 'calls': row['calls'].pop()
                elif mutation == 'exit': row['calls'][0]['exit_code'] = 0
                elif mutation == 'root': row['root'] = str(self.root / 'outside')
                elif mutation == 'pid': row['stopped_pid'] += 100
                else: row['port'] = 8765
            write(directory / 'rehearsal.json', report)
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                self.verify(directory, config)

    def test_changed_journals_streams_binary_and_live_owner_refuse(self):
        for mutation in ('journal', 'injection', 'restore', 'stream', 'binary', 'alive'):
            directory, config, _ = self.rehearsal('proof-' + mutation)
            if mutation == 'journal':
                write(directory / 'after_stop/switch/journal.json', [{'stage': 'committed'}])
            elif mutation == 'injection':
                write(directory / 'after_stop/switch/journal.json', [{'stage': 'failed', 'error': 'other failure'}, {'stage': 'rollback_verified'}])
            elif mutation == 'restore':
                write(directory / 'switch/restore/journal.json', [{'stage': 'failed'}])
            elif mutation == 'stream':
                write(directory / 'switch/check.stdout', b'changed raw subprocess bytes\n')
            elif mutation == 'binary':
                write(directory / 'after_stop/root/som-rs/runtime/default.json', {'pid': 5001, 'identity': {'binary_sha256': 'a' * 64}})
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                self.verify(directory, config, alive=mutation == 'alive')

    def test_successful_final_stages_cannot_hide_earlier_cleanup_or_evidence_errors(self):
        locations = [(scenario, 'switch') for scenario in self.runner.SCENARIOS] + [('switch', 'restore')]
        for scenario, operation in locations:
            for failure in ('evidence_errors', 'OWNED_CLONE_CLEANUP_FAILED', 'ROLLBACK_FAILED_REQUIRES_OPERATOR'):
                with self.subTest(scenario=scenario, operation=operation, failure=failure):
                    directory, config, _ = self.rehearsal(f'hidden-{scenario}-{operation}-{failure}')
                    path = directory / scenario / operation / 'journal.json'
                    journal = json.loads(path.read_bytes())
                    event = ({'stage': 'evidence_observed', 'evidence_errors': [{'operation': 'save', 'error': 'fake OSError'}]}
                             if failure == 'evidence_errors' else {'stage': failure, 'error': 'fake cleanup failure'})
                    journal.insert(0, event)
                    write(path, journal)
                    # The committed/rollback_verified/restore_verified final event
                    # and all independent postchecks remain successful.
                    message = 'evidence write errors' if failure == 'evidence_errors' else 'cleanup or rollback failure'
                    with self.assertRaisesRegex(RuntimeError, message):
                        self.verify(directory, config)

    def test_every_final_previous_receipt_refuses_rollback_evidence_errors(self):
        for scenario in self.runner.SCENARIOS:
            with self.subTest(scenario=scenario):
                directory, config, _ = self.rehearsal('receipt-error-' + scenario)
                path = directory / scenario / 'root/som-rs/runtime/default.json'
                receipt = json.loads(path.read_bytes())
                receipt['rollback_evidence_errors'] = [{'operation': 'journal', 'error': 'fake OSError'}]
                write(path, receipt)
                with self.assertRaisesRegex(RuntimeError, 'restored receipt has evidence write errors'):
                    self.verify(directory, config)

    def test_empty_recorded_evidence_error_lists_preserve_complete_success(self):
        directory, config, report = self.rehearsal('empty-error-lists')
        for scenario in self.runner.SCENARIOS:
            for operation in (('switch', 'restore') if scenario == 'switch' else ('switch',)):
                path = directory / scenario / operation / 'journal.json'
                journal = json.loads(path.read_bytes())
                journal[-1]['evidence_errors'] = []
                write(path, journal)
            path = directory / scenario / 'root/som-rs/runtime/default.json'
            receipt = json.loads(path.read_bytes())
            receipt['rollback_evidence_errors'] = []
            write(path, receipt)
        self.assertEqual(self.verify(directory, config), report)


if __name__ == '__main__':
    unittest.main()
