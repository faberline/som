#!/usr/bin/env python3
"""Unit tests for the optional React release canaries and gates (#138).

All HTTP replies, CLI calls, and runtime owners are fakes. These tests need
no compiler process, local service, or protected port.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_default  # noqa: E402
import prepare_inputs as pi  # noqa: E402
import release_common as rc  # noqa: E402
import switch  # noqa: E402

REACT_IDS = ['example-v1', 'example-v4']


def _target_sources() -> list[dict]:
    return [
        {
            'id': name,
            'prompt': {'version': version, 'package': f'react_canary_v{version}'},
            'cnl': f'Use prompt version {version}.\nUse package react_canary_v{version}.\n',
        }
        for name, version in [('example-v1', 1), ('example-v2', 2), ('example-v4', 4)]
    ] + [{'id': 'reject-version-unknown', 'rejection': True, 'prompt': {}, 'cnl': None}]


def _files(case_id: str) -> dict[str, str]:
    return {
        'package.json': f'{{"name":"{case_id}"}}\n',
        'src/App.tsx': f'// {case_id}\nexport const App = () => <main>Ready</main>;\n',
        'src/main.tsx': 'export { App } from "./App";\n',
    }


def _product(run: Path, case_id: str, files: dict[str, str]) -> Path:
    accepted = run / 'bundles/products' / case_id
    for name, content in files.items():
        path = accepted / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode())
    return accepted


def _bundle(prompt: dict, target: str = 'react') -> dict:
    return {
        'version': 'som.dsl-bundle.v2',
        'target': target,
        'prompt': prompt,
        'dsl': {'version': 'som.dsl.react.v1', 'files': []},
    }


def _cli_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode()


class FakeReactCandidate:
    def __init__(self, bundles: dict[str, dict], files: dict[str, dict[str, str]]) -> None:
        self.bundles = bundles
        self.files = files
        self.cnl_raw: bytes | None = None
        self.compile_stdout: bytes | None = None
        self.assemble_stdout: bytes | None = None
        self.http_calls: list[tuple[int, str, bytes]] = []
        self.cli_calls: list[list[str]] = []
        self.by_package = {bundle['prompt']['package']: name for name, bundle in bundles.items()}

    def case_id(self, payload: dict) -> str:
        if payload.get('format') == 'som.prompt-json.v1':
            return self.by_package[payload['prompt']['package']]
        for package, name in self.by_package.items():
            if f'Use package {package}.' in payload['source']:
                return name
        raise AssertionError(f'unknown CNL source: {payload}')

    def post_raw(self, port: int, path: str, body: bytes) -> tuple[int, bytes]:
        self.http_calls.append((port, path, body))
        payload = json.loads(body)
        if path == '/v1/compile':
            name = self.case_id(payload)
            if payload['format'] == 'som.cnl.v1' and self.cnl_raw is not None:
                return 200, self.cnl_raw
            return 200, pi.targets.dump({'status': 'ok', 'bundle': self.bundles[name]}).encode()
        if path == '/v1/assemble':
            name = self.by_package[payload['bundle']['prompt']['package']]
            return 200, pi.targets.dump({'status': 'ok', 'files': self.files[name]}).encode()
        raise AssertionError(f'unexpected HTTP path: {path}')

    def subprocess_run(self, argv: list[str], **kwargs):
        self.cli_calls.append(list(argv))
        if argv[1] == 'compile':
            name = self.case_id({'source': Path(argv[2]).read_text()})
            stdout = self.compile_stdout if self.compile_stdout is not None else _cli_bytes(self.bundles[name])
        elif argv[1] == 'assemble':
            bundle = json.loads(Path(argv[2]).read_bytes())
            name = self.by_package[bundle['prompt']['package']]
            reply = {'status': 'ok', 'files': self.files[name]}
            stdout = self.assemble_stdout if self.assemble_stdout is not None else _cli_bytes(reply)
        else:
            raise AssertionError(f'unexpected CLI command: {argv}')
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b'')


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


def _step_canary(pin, family: str, name: str) -> dict:
    prefix = f'{family}/{name}'
    request = pin.write(f'{prefix}/compile.request', b'{"target":"react"}')
    response = pin.write(f'{prefix}/compile.response', b'{"status":"ok"}')
    cnl = pin.write(f'{prefix}/source.cnl', b'Use prompt version 1.\n')
    bundle = pin.write(f'{prefix}/bundle.json', b'{"target":"react"}')
    compile_stdout = pin.write(f'{prefix}/cli-compile.stdout', b'{}\n')
    assemble_stdout = pin.write(f'{prefix}/cli-assemble.stdout', b'{}\n')
    return {
        'name': name,
        'family': family,
        'steps': [
            {'kind': 'http', 'name': 'compile-json', 'path': '/v1/compile',
             'request': request, 'response': response},
            {'kind': 'cli', 'name': 'cli-compile', 'args': ['compile', cnl['path']],
             'inputs': [cnl], 'binaries': ['new'], 'response': compile_stdout},
            {'kind': 'cli', 'name': 'cli-assemble', 'args': ['assemble', bundle['path']],
             'inputs': [bundle], 'binaries': ['new'], 'response': assemble_stdout},
        ],
    }


def _prerequisites(root: Path, *, with_react: bool = True) -> dict:
    binary = root / 'candidate/som'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b'fake-react-candidate')
    binary_sha = rc.digest(binary)
    prettier = root / 'prettier'
    prettier.mkdir()
    health = {
        'status': 'ok',
        'asset_manifest_sha256': 'a' * 64,
        'model_manifest_sha256': 'b' * 64,
        'blocks_sha256': 'c' * 64,
        'targets': ['python', 'rust'],
        'rust_target': {'formatter_sha256': 'd' * 64},
    }
    _write_json(root / 'candidate/runtime.json', {
        'port': 18960, 'binary_sha256': binary_sha, 'health': health,
        'som_ts_prettier': str(prettier),
    })
    _write_json(root / 'candidate/build.json', {'binary_sha256': binary_sha})
    _write_json(root / 'candidate/source.json', {'commit': '0' * 40})
    run = root / '.local/react-output/acceptance/run-001'
    run.mkdir(parents=True)
    audit = run.parent / 'audit-001.json'
    if with_react:
        identity = run / 'identity.json'
        _write_json(identity, {
            'passed': True, 'problems': [],
            'candidate': {'binary_sha256': binary_sha, 'binary_sha256_after': binary_sha},
        })
        _write_json(run / 'run.json', {
            'version': 'som.react-output-acceptance-run.v1',
            'passed': True, 'status': 'passed', 'failed': [], 'not_run': [], 'problems': [],
            'identity': str(identity), 'identity_sha256': rc.digest(identity), 'identity_passed': True,
            'steps': [{'step': f'gate-{index}', 'judgment': 'passed', 'passed': True} for index in range(12)],
        })
        _write_json(audit, {
            'version': 'som.react-output-audit-130.v1',
            'run': str(run.relative_to(root)),
            'totals': {'passed': 30, 'failed': 0, 'unrun': 0},
        })
    else:
        _write_json(run / 'summary.json', {
            'version': 'som.rust-output-acceptance-run.v1', 'status': 'passed', 'blockers': [],
            'candidate_after': {'binary_sha256': binary_sha}, 'header_sha256': 'e' * 64,
        })
        _write_json(audit, {
            'version': 'som.rust-output-audit-055.v1', 'run': str(run),
            'totals': {'failed': 0, 'unrun': 0},
        })
    legacy_binary = root / 'legacy/som'
    legacy_binary.parent.mkdir()
    legacy_binary.write_bytes(b'fake-legacy')
    for name in ('manifest', 'blocks'):
        _write_json(root / f'legacy/{name}.json', {})
    _write_json(root / 'legacy/default.json', {
        'command': [str(legacy_binary), 'serve', '--model', 'm', '--blocks',
                    str(root / 'legacy/blocks.json'), '--port', '8765'],
        'health': {'model_manifest_sha256': rc.digest(root / 'legacy/manifest.json'),
                   'blocks_sha256': rc.digest(root / 'legacy/blocks.json')},
    })
    pin = pi.Pinner(root / 'inputs')
    with mock.patch.object(rc, 'REPO', root):
        gates, header_sha = pi.gate_assertions(
            run, audit, binary_sha, react_run=run if with_react else None, react_cases=REACT_IDS,
        )
    config = {
        'version': rc.PREREQUISITES_VERSION, 'repo': str(root),
        'candidate': {name: pi.binding(root / 'candidate' / filename) for name, filename in
                      [('binary', 'som'), ('runtime', 'runtime.json'), ('build', 'build.json'), ('source', 'source.json')]},
        'gates': gates, 'rust_products_run': str(run),
        'legacy_default': pi.binding(root / 'legacy/default.json'),
        'legacy_binary': pi.binding(legacy_binary),
        'legacy_model_manifest': pi.binding(root / 'legacy/manifest.json'),
        'legacy_blocks': pi.binding(root / 'legacy/blocks.json'),
        'python_canaries': [_step_canary(pin, 'flow', 'smoke12'), _step_canary(pin, 'legacy', 'legacy100')],
        'rust_canaries': [_step_canary(pin, 'rust', 'example-v1'), _step_canary(pin, 'rust', 'example-v5')],
        'm2_canaries': [{'name': 'smoke12', 'prompt': pin.write('m2/prompt.json', b'{}'),
                         'expected_response': pin.write('m2/response.json', b'{}')}],
        'acceptance_summary_header_sha256': header_sha,
    }
    if with_react:
        config.update({
            'react_products_run': str(run),
            'typescript_formatter': {'env': 'SOM_TS_PRETTIER', 'path': str(prettier)},
            'react_canaries': [_step_canary(pin, 'react', name) for name in REACT_IDS],
        })
    return config


class TestReactCanaries(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.enterContext(mock.patch.dict(os.environ, {}, clear=False))
        os.environ.pop('SOM_TS_PRETTIER', None)
        self.enterContext(mock.patch.object(pi.targets, 'sources', side_effect=_target_sources))
        self.run = self.tmp / 'products-run'
        self.files = {name: _files(name) for name in REACT_IDS}
        for name, files in self.files.items():
            _product(self.run, name, files)
        self.sources = pi.react_sources(self.run, REACT_IDS)
        self.bundles = {case['id']: _bundle(case['prompt']) for case in self.sources}
        self.binary = self.tmp / 'som'
        self.binary.write_bytes(b'fake-binary')

    def pin_case(self, harness: FakeReactCandidate, name: str = 'inputs') -> dict:
        with (
            mock.patch.object(rc, 'post_raw', side_effect=harness.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness.subprocess_run),
        ):
            return pi.react_case(pi.Pinner(self.tmp / name), self.sources[0], 18960, self.binary)

    def test_pins_both_react_canaries_and_target_in_http_and_cli(self) -> None:
        harness = FakeReactCandidate(self.bundles, self.files)
        pin = pi.Pinner(self.tmp / 'inputs')
        with (
            mock.patch.object(rc, 'post_raw', side_effect=harness.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness.subprocess_run),
        ):
            cases = [pi.react_case(pin, case, 18960, self.binary) for case in self.sources]
        self.assertEqual([case['name'] for case in cases], REACT_IDS)
        for case in cases:
            self.assertEqual(case['family'], 'react')
            self.assertEqual(case['files'], len(self.files[case['name']]))
            self.assertEqual(case['accepted_product'], str(self.run / 'bundles/products' / case['name']))
            self.assertEqual([step['name'] for step in case['steps']],
                             ['compile-json', 'compile-cnl', 'assemble', 'cli-compile', 'cli-assemble'])
            directory = self.tmp / 'inputs/react' / case['name']
            for filename in ('source.cnl', 'compile-json.request', 'compile-json.response',
                             'compile-cnl.request', 'compile-cnl.response', 'assemble.request',
                             'assemble.response', 'bundle.json', 'cli-compile.stdout', 'cli-assemble.stdout'):
                self.assertTrue((directory / filename).is_file(), filename)
            self.assertEqual(json.loads((directory / 'assemble.response').read_bytes())['files'],
                             self.files[case['name']])
            self.assertEqual((directory / 'cli-compile.stdout').read_bytes(), _cli_bytes(self.bundles[case['name']]))
            for step in case['steps']:
                rc.bound(step['response'])
                if step['kind'] == 'http':
                    rc.bound(step['request'])
                for item in step.get('inputs', []):
                    rc.bound(item)
        compile_requests = [json.loads(body) for _, path, body in harness.http_calls if path == '/v1/compile']
        self.assertEqual(len(compile_requests), 4)
        self.assertEqual({body['target'] for body in compile_requests}, {'react'})
        compile_commands = [argv for argv in harness.cli_calls if argv[1] == 'compile']
        self.assertEqual(len(compile_commands), 2)
        for argv in compile_commands:
            self.assertEqual(argv[argv.index('--target') + 1], 'react')

    def test_sources_reject_unknown_rejected_duplicate_and_missing_products(self) -> None:
        cases = [
            (['example-v1', 'no-such-case'], 'unknown React canary'),
            (['example-v1', 'reject-version-unknown'], 'unknown React canary'),
            (['example-v1', 'example-v1'], 'repeated React canary'),
            (['example-v1', 'example-v2'], 'no accepted product'),
        ]
        for ids, message in cases:
            with self.subTest(ids=ids), self.assertRaisesRegex(RuntimeError, message):
                pi.react_sources(self.run, ids)

    def test_cnl_and_json_compile_must_have_equal_raw_bytes(self) -> None:
        reply = {'status': 'ok', 'bundle': self.bundles['example-v1']}
        different = copy.deepcopy(reply)
        different['bundle']['extra'] = True
        for name, response in [('different-json', pi.targets.dump(different).encode()),
                               ('same-json-whitespace', _cli_bytes(reply))]:
            harness = FakeReactCandidate(self.bundles, self.files)
            harness.cnl_raw = response
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'React CNL and prompt JSON compile differ'):
                self.pin_case(harness, name)

    def test_compile_bundle_must_name_react_target(self) -> None:
        bundles = copy.deepcopy(self.bundles)
        bundles['example-v1']['target'] = 'typescript'
        with self.assertRaisesRegex(RuntimeError, 'bundle target is not react'):
            self.pin_case(FakeReactCandidate(bundles, self.files))

    def test_assembled_files_must_match_accepted_file_set_and_bytes(self) -> None:
        for change in ('content', 'extra', 'missing'):
            files = copy.deepcopy(self.files)
            if change == 'content':
                files['example-v1']['src/App.tsx'] += '// changed\n'
            elif change == 'extra':
                files['example-v1']['src/extra.tsx'] = 'export const extra = true;\n'
            else:
                del files['example-v1']['src/main.tsx']
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, 'differs from the accepted product'):
                self.pin_case(FakeReactCandidate(self.bundles, files), change)

    def test_accepted_crlf_bytes_cannot_match_assembled_lf_text(self) -> None:
        path = self.sources[0]['accepted'] / 'src/App.tsx'
        path.write_bytes(self.files['example-v1']['src/App.tsx'].replace('\n', '\r\n').encode())
        with self.assertRaisesRegex(RuntimeError, 'differs from the accepted product'):
            self.pin_case(FakeReactCandidate(self.bundles, self.files))

    def test_cli_compile_and_assemble_require_exact_stdout_bytes(self) -> None:
        bundle = self.bundles['example-v1']
        assemble = {'status': 'ok', 'files': self.files['example-v1']}
        cases = [
            ('compile-different', 'compile_stdout', _cli_bytes({'target': 'react', 'different': True})),
            ('compile-whitespace', 'compile_stdout', rc.canon(bundle).encode()),
            ('assemble-different', 'assemble_stdout', _cli_bytes({'status': 'ok', 'files': {}})),
            ('assemble-whitespace', 'assemble_stdout', rc.canon(assemble).encode()),
        ]
        for name, attribute, stdout in cases:
            harness = FakeReactCandidate(self.bundles, self.files)
            setattr(harness, attribute, stdout)
            message = 'React CLI bundle differs' if attribute == 'compile_stdout' else 'React CLI assemble differs'
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, message):
                self.pin_case(harness, name)

    def test_prepare_inputs_main_writes_react_configuration_only_when_requested(self) -> None:
        for with_react in (True, False):
            with self.subTest(with_react=with_react):
                root = self.tmp / ('main-react' if with_react else 'main-rust')
                base = _prerequisites(root, with_react=with_react)
                run = Path(base['rust_products_run'])
                for name, files in self.files.items():
                    _product(run, name, files)
                manifest = root / 'som-rs/models/current/manifest.json'
                _write_json(manifest, {})
                switch_dir = root / 'switch-001'
                _write_json(switch_dir / '0001-prerequisites.json', {'canaries': [1, 2]})
                legacy = switch_dir / '0002-legacy-default.json'
                legacy.write_bytes(Path(base['legacy_default']['path']).read_bytes())
                health = json.loads(Path(base['candidate']['runtime']['path']).read_bytes())['health']
                previous = root / 'previous/som'
                previous.parent.mkdir()
                previous.write_bytes(b'fake-previous')
                _write_json(root / 'som-rs/runtime/default.json', {
                    'command': [str(previous), 'serve', '--assets', 'unused', '--port', '8765'],
                    'identity': {'binary_sha256': rc.digest(previous)},
                    'health': health, 'previous_default_sha256': rc.digest(legacy),
                })

                def python_case(pin, case, prev, new, port, prefix='python'):
                    family, name = ('flow', 'smoke12') if case == 1 else ('legacy', 'legacy100')
                    pinned = _step_canary(pin, family, name)
                    m2 = {'name': name, 'prompt': pin.write(f'm2/{name}/prompt', b'{}'),
                          'expected_response': pin.write(f'm2/{name}/response', b'{}')}
                    return pinned, m2

                harness = FakeReactCandidate(self.bundles, self.files)
                out = root / 'out'
                argv = ['prepare_inputs.py', '--candidate', str(root / 'candidate'),
                        '--acceptance', str(run), '--audit', base['gates']['audit']['path'],
                        '--out', str(out)]
                if with_react:
                    argv += ['--react-run', str(run)]
                with (
                    mock.patch.object(rc, 'REPO', root),
                    mock.patch.object(pi, 'SWITCH_001', switch_dir),
                    mock.patch.object(pi, 'rust_sources', side_effect=lambda r, ids: [{'id': name} for name in ids]),
                    mock.patch.object(pi, 'rust_case', side_effect=lambda pin, case, p, new: _step_canary(pin, 'rust', case['id'])),
                    mock.patch.object(pi, 'python_case', side_effect=python_case),
                    mock.patch.object(rc, 'post_raw', side_effect=harness.post_raw),
                    mock.patch.object(pi.subprocess, 'run', side_effect=harness.subprocess_run),
                    mock.patch.object(sys, 'argv', argv),
                    mock.patch('builtins.print') as printed,
                ):
                    pi.main()
                written = json.loads((out / 'prerequisites.json').read_bytes())
                counts = json.loads(printed.call_args.args[0])
                self.assertEqual(counts['python_steps'], 6)
                self.assertEqual(counts['rust_steps'], 6)
                self.assertEqual(counts['m2_cases'], 2)
                self.assertNotIn('typescript_steps', counts)
                if with_react:
                    self.assertEqual(written['react_products_run'], str(run))
                    self.assertEqual([case['name'] for case in written['react_canaries']], REACT_IDS)
                    self.assertEqual([case['family'] for case in written['react_canaries']], ['react', 'react'])
                    self.assertEqual(counts['react_steps'], 10)
                    self.assertIn('react_identity', written['gates'])
                    self.assertEqual(len(harness.http_calls), 6)
                    self.assertEqual(len(harness.cli_calls), 4)
                else:
                    self.assertNotIn('react_products_run', written)
                    self.assertNotIn('react_canaries', written)
                    self.assertNotIn('react_identity', written['gates'])
                    self.assertNotIn('react_steps', counts)
                    self.assertEqual(harness.http_calls, [])
                    self.assertEqual(harness.cli_calls, [])
                self.assertNotIn('typescript_formatter', written)
                self.assertNotIn('typescript_canaries', written)
                self.assertNotIn('SOM_TS_PRETTIER', os.environ)
                os.environ.pop('SOM_TS_PRETTIER', None)


class TestReactReleaseGates(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

    def test_react_run_and_identity_are_bound_with_repo_relative_audit_run(self) -> None:
        config = _prerequisites(self.tmp)
        with mock.patch.object(rc, 'REPO', self.tmp):
            runtime, _ = rc.check_prerequisites(config)
        self.assertEqual(runtime['port'], 18960)
        gates = config['gates']
        self.assertEqual(Path(gates['acceptance']['path']).name, 'run.json')
        self.assertEqual(gates['acceptance']['assertions']['/passed'], True)
        self.assertEqual(gates['acceptance']['assertions']['/not_run'], [])
        self.assertEqual(gates['audit']['assertions']['/totals/failed'], 0)
        self.assertEqual(gates['audit']['assertions']['/totals/unrun'], 0)
        self.assertEqual(gates['audit']['assertions']['/run'], '.local/react-output/acceptance/run-001')
        identity = gates['react_identity']
        self.assertEqual(Path(identity['path']).name, 'identity.json')
        self.assertEqual(identity['assertions']['/candidate/binary_sha256'], config['candidate']['binary']['sha256'])
        self.assertEqual(config['acceptance_summary_header_sha256'], identity['sha256'])

    def test_react_gate_refuses_no_run_or_fewer_than_two_canaries(self) -> None:
        config = _prerequisites(self.tmp)
        run = Path(config['react_products_run'])
        audit = Path(config['gates']['audit']['path'])
        for react_run, ids in [(None, REACT_IDS), (run, ['example-v1']), (run, [])]:
            with (
                self.subTest(react_run=react_run, ids=ids),
                mock.patch.object(rc, 'REPO', self.tmp),
                self.assertRaisesRegex(RuntimeError, 'React acceptance gate'),
            ):
                pi.gate_assertions(run, audit, config['candidate']['binary']['sha256'],
                                   react_run=react_run, react_cases=ids)

    def test_react_run_identity_and_audit_fail_closed_on_changed_claims(self) -> None:
        mutations = [
            ('run-version', 'acceptance', lambda doc: doc.update(version='som.react-output-acceptance-run.v0')),
            ('run-passed', 'acceptance', lambda doc: doc.update(passed=False)),
            ('run-passed-type', 'acceptance', lambda doc: doc.update(passed=1)),
            ('run-not-run', 'acceptance', lambda doc: doc.update(not_run=['gate-11'])),
            ('step-failed', 'acceptance', lambda doc: doc['steps'][4].update(judgment='failed')),
            ('step-not-run', 'acceptance', lambda doc: doc['steps'][4].update(judgment='not_run')),
            ('no-steps', 'acceptance', lambda doc: doc.update(steps=[])),
            ('run-identity-hash', 'acceptance', lambda doc: doc.update(identity_sha256='0' * 64)),
            ('identity-passed', 'react_identity', lambda doc: doc.update(passed=False)),
            ('identity-candidate-before', 'react_identity',
             lambda doc: doc['candidate'].update(binary_sha256='0' * 64)),
            ('identity-candidate-after', 'react_identity',
             lambda doc: doc['candidate'].update(binary_sha256_after='0' * 64)),
            ('audit-version', 'audit', lambda doc: doc.update(version='som.react-output-audit-130.v0')),
            ('audit-failed', 'audit', lambda doc: doc['totals'].update(failed=1)),
            ('audit-unrun', 'audit', lambda doc: doc['totals'].update(unrun=1)),
            ('audit-other-run', 'audit', lambda doc: doc.update(run='.local/react-output/acceptance/run-999')),
            ('audit-absolute-run', 'audit', lambda doc: doc.update(run=str(self.tmp / 'absolute-run'))),
        ]
        for name, gate, mutate in mutations:
            with self.subTest(name=name):
                root = self.tmp / name
                config = _prerequisites(root)
                document_path = Path(config['gates'][gate]['path'])
                document = json.loads(document_path.read_bytes())
                mutate(document)
                _write_json(document_path, document)
                run = Path(config['react_products_run'])
                if gate == 'react_identity':
                    run_doc = json.loads((run / 'run.json').read_bytes())
                    run_doc['identity_sha256'] = rc.digest(document_path)
                    _write_json(run / 'run.json', run_doc)
                with mock.patch.object(rc, 'REPO', root), self.assertRaises(RuntimeError):
                    config['gates'], _ = pi.gate_assertions(
                        run, Path(config['gates']['audit']['path']), config['candidate']['binary']['sha256'],
                        react_run=run, react_cases=REACT_IDS,
                    )
                    rc.check_prerequisites(config)

    def test_prerequisites_refuse_one_react_canary(self) -> None:
        config = _prerequisites(self.tmp)
        config['react_canaries'] = config['react_canaries'][:1]
        with mock.patch.object(rc, 'REPO', self.tmp), self.assertRaisesRegex(RuntimeError, 'need two React canaries'):
            rc.check_prerequisites(config)

    def test_prerequisites_bind_every_react_step_request_response_and_cli_input(self) -> None:
        for step_index, binding_name in [(0, 'request'), (0, 'response'), (1, 'response'),
                                         (1, 'inputs'), (2, 'response'), (2, 'inputs')]:
            with self.subTest(step=step_index, binding=binding_name):
                root = self.tmp / f'{step_index}-{binding_name}'
                config = _prerequisites(root)
                step = config['react_canaries'][0]['steps'][step_index]
                item = step[binding_name][0] if binding_name == 'inputs' else step[binding_name]
                Path(item['path']).write_bytes(b'tampered bytes')
                with mock.patch.object(rc, 'REPO', root), self.assertRaisesRegex(RuntimeError, 'bound input changed'):
                    rc.check_prerequisites(config)

    def test_configuration_without_react_keeps_existing_rust_gates(self) -> None:
        config = _prerequisites(self.tmp, with_react=False)
        self.assertNotIn('react_canaries', config)
        self.assertNotIn('react_products_run', config)
        self.assertNotIn('react_identity', config['gates'])
        self.assertEqual(Path(config['gates']['acceptance']['path']).name, 'summary.json')
        self.assertEqual(config['acceptance_summary_header_sha256'], 'e' * 64)
        with mock.patch.object(rc, 'REPO', self.tmp):
            runtime, _ = rc.check_prerequisites(config)
        self.assertEqual(runtime['port'], 18960)


class TestReactReleaseReplay(unittest.TestCase):
    def test_switch_and_default_checks_replay_react_only_on_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for selected in (None, 'after_pointer'):
                with self.subTest(inject=selected):
                    root = Path(temporary).resolve() / (selected or 'success')
                    config = _prerequisites(root)
                    runtime = rc.load(config['candidate']['runtime']['path'])
                    rustfmt = root / 'rustfmt'
                    rustfmt.write_bytes(b'fake-rustfmt')
                    runtime['health']['rust_target']['formatter_sha256'] = rc.digest(rustfmt)
                    _write_json(Path(config['candidate']['runtime']['path']), runtime)
                    config['candidate']['runtime'] = pi.binding(Path(config['candidate']['runtime']['path']))
                    _write_json(root / 'som-rs/contracts/rust-output-v1/toolchain.json',
                                {'rust': {'bin_dir': str(root)}})
                    pin = pi.Pinner(root / 'inputs')
                    config['typescript_canaries'] = [_step_canary(pin, 'typescript', 'example-v1')]
                    config['typescript_canaries'].append(_step_canary(pin, 'typescript', 'example-v5'))
                    previous = root / 'previous/som'
                    previous.parent.mkdir()
                    previous.write_bytes(b'fake-previous')
                    assets = root / 'som-rs/compiler-assets/versions/assets'
                    assets.mkdir(parents=True)
                    (assets.parent.parent / 'current').symlink_to(assets)
                    default_path = root / 'som-rs/runtime/default.json'
                    default_path.parent.mkdir(parents=True)
                    (default_path.parent / 'current').symlink_to(previous.parent)
                    command = [str(previous), 'serve', '--assets', str(assets), '--port', '18961']

                    def owner(pid, argv, port):
                        return {'pid': pid, 'argv': argv, 'port': port, 'start': 'fake-start',
                                'cwd': str(root), 'binary_sha256': rc.digest(argv[0])}

                    old = {'version': 'som.default-runtime.v2', 'accepted': True, 'pid': 101,
                           'command': command, 'identity': owner(101, command, 18961),
                           'health': runtime['health']}
                    _write_json(default_path, old)
                    for name, state in check_default.LEGACY_POINTERS.items():
                        path = root / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.symlink_to(state['target'])
                    calls = []

                    class FakeRelease:
                        def __init__(self, output, cwd):
                            self.owners, self.children, self.evidence_errors = {}, [], []

                        def save(self, name, data):
                            pass

                        def event(self, stage, **kwargs):
                            pass

                        def health(self, port, expected):
                            return expected

                        def start(self, argv, expected):
                            identity = owner(102, argv, int(argv[-1]))
                            self.owners[identity['port']] = identity
                            return identity

                        def stop_child(self, identity):
                            pass

                        def m2_canary(self, port, binaries, cases):
                            calls.append(('m2', [case['name'] for case in cases]))

                        def canary(self, port, binaries, cases, label):
                            calls.append((label, [case['family'] for case in cases]))

                    with (
                        mock.patch.object(rc, 'REPO', root),
                        mock.patch.object(rc, 'Release', FakeRelease),
                        mock.patch.object(rc, 'identity', side_effect=owner),
                        mock.patch.object(rc, 'assets_match'),
                        mock.patch.object(rc, 'free_harness_port', return_value=18962),
                        mock.patch.object(rc, 'terminate'),
                        mock.patch.object(rc, 'alive', return_value=False),
                        mock.patch.object(switch.signal, 'signal'),
                        mock.patch.dict(os.environ, {}, clear=False),
                    ):
                        os.environ.pop('SOM_TS_PRETTIER', None)
                        args = (config, root, 18961, root / 'switch-out', None, True, selected)
                        if selected:
                            with self.assertRaisesRegex(RuntimeError, 'injected failure at after_pointer'):
                                switch.execute(*args)
                        else:
                            switch.execute(*args)
                        candidate_families = ['flow', 'legacy', 'rust', 'rust',
                                              'typescript', 'typescript', 'react', 'react']
                        expected = [('current-default', ['flow', 'legacy']), ('m2', ['smoke12']),
                                    ('clone', ['flow', 'legacy']), ('clone', candidate_families),
                                    ('new-default', candidate_families)]
                        expected.append(('restored-default', ['flow', 'legacy']) if selected
                                        else ('new-default-after-pointer', candidate_families))
                        self.assertEqual(calls, expected)
                        switched = rc.load(default_path)
                        self.assertEqual(switched.get('rolled_back_default_sha256') is not None, bool(selected))
                        prerequisites = root / 'prerequisites.json'
                        _write_json(prerequisites, config)
                        for candidate in (True, False):
                            receipt = switched if candidate and not selected else old
                            if candidate and selected:
                                receipt = {**old, 'command': [config['candidate']['binary']['path'], *command[1:]]}
                                receipt['identity'] = owner(receipt['pid'], receipt['command'], 18961)
                            _write_json(default_path, receipt)
                            rc.atomic_link(default_path.parent / 'current',
                                           {'kind': 'symlink', 'target': str(Path(receipt['command'][0]).parent)})
                            calls.clear()
                            argv = ['check_default.py', '--prerequisites', str(prerequisites),
                                    '--root', str(root), '--port', '18961', '--expect-binary-sha256',
                                    rc.digest(receipt['command'][0]), '--output', str(root / f'check-{candidate}')]
                            with mock.patch.object(sys, 'argv', argv), mock.patch('builtins.print'):
                                self.assertEqual(check_default.main(), 0)
                            self.assertEqual(calls, [('post-check', candidate_families if candidate
                                                     else ['flow', 'legacy'])])


if __name__ == '__main__':
    unittest.main()
