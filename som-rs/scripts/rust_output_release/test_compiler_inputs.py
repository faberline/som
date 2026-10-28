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
        identity=forbidden, PROTECTED_PORTS={8765, 18765})
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
        rows = []
        for index, scenario in enumerate(self.runner.SCENARIOS):
            root = directory / scenario
            pid = 5000 + index
            calls = []
            names = ('switch', 'check', 'restore', 'check-restored') if scenario == 'switch' else ('switch', 'check')
            for operation in names:
                call = {'name': operation, 'exit_code': 1 if operation == 'switch' and scenario != 'switch' else 0}
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
                         'root': str(root / 'root'), 'calls': calls, 'stopped_pid': pid})
        report = {'passed': True, 'profile': 'compiler-only', 'live_default_accessed': False, 'scenarios': rows}
        write(directory / 'rehearsal.json', report)
        return directory, config, report

    def verify(self, directory, config, alive=False):
        with mock.patch.object(self.rc, 'alive', return_value=alive) as owners, \
             mock.patch.object(self.rc, 'port_free') as ports, \
             mock.patch.object(self.rc, 'identity', side_effect=forbidden):
            result = self.runner.verify_report(directory, config)
        self.assertEqual(owners.call_count, 5)
        self.assertEqual([call.args[0] for call in ports.call_args_list], list(range(18940, 18945)))
        return result

    def test_complete_five_scenario_report_binds_streams_checks_and_stopped_owners(self):
        directory, config, report = self.rehearsal('valid')
        self.assertEqual(self.verify(directory, config), report)

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


if __name__ == '__main__':
    unittest.main()
