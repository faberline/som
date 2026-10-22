#!/usr/bin/env python3
"""Unit tests for TypeScript release canaries and TS gates in rust_output_release (#121)."""
from __future__ import annotations

import copy
import io
import json
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


def _fake_files(case_id: str) -> dict[str, str]:
    return {
        'package.json': f'{{"name": "{case_id}"}}\n',
        'src/app.ts': f'// {case_id} app\nexport const id = {json.dumps(case_id)};\n',
        'test/api.test.ts': f'// {case_id} test\n',
    }


def _write_product_dir(root: Path, case_id: str, files: dict[str, str] | None = None) -> Path:
    product_dir = root / 'bundles/products' / case_id
    product_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in (files or _fake_files(case_id)).items():
        target = product_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return product_dir


def _fake_bundle(prompt: dict, target: str = 'typescript', extra: dict | None = None) -> dict:
    return {
        'version': 'som.dsl-bundle.v2',
        'target': target,
        'prompt': prompt,
        'dsl': {'version': 'som.dsl.typescript.v1', 'files': []},
        **(extra or {}),
    }


class FakeCandidateHarness:
    """Fake HTTP and CLI responder for a candidate on a harness port."""

    def __init__(
        self,
        bundle_by_id: dict[str, dict],
        files_by_id: dict[str, dict[str, str]],
        *,
        cnl_override_by_id: dict[str, dict] | None = None,
        cli_compile_stdout: bytes | None = None,
        cli_assemble_stdout: bytes | None = None,
    ) -> None:
        self.bundle_by_id = bundle_by_id
        self.files_by_id = files_by_id
        self.cnl_override_by_id = cnl_override_by_id or {}
        self.cli_compile_stdout = cli_compile_stdout
        self.cli_assemble_stdout = cli_assemble_stdout
        self.http_calls: list[tuple[int, str, bytes]] = []
        self.cli_calls: list[list[str]] = []
        self._by_package = {
            bundle['prompt']['package']: case_id
            for case_id, bundle in bundle_by_id.items()
        }

    def _case_id_for_compile(self, payload: dict) -> str:
        if payload.get('format') == 'som.prompt-json.v1':
            pkg = payload['prompt']['package']
            return self._by_package[pkg]
        source = payload['source']
        for case_id, bundle in self.bundle_by_id.items():
            pkg = bundle['prompt']['package']
            if f'Use package {pkg}.' in source:
                return case_id
        return next(iter(self.bundle_by_id))

    def post_raw(self, port: int, path: str, body: bytes) -> tuple[int, bytes]:
        self.http_calls.append((port, path, body))
        payload = json.loads(body.decode())
        if path == '/v1/compile':
            case_id = self._case_id_for_compile(payload)
            if payload.get('format') == 'som.cnl.v1' and case_id in self.cnl_override_by_id:
                bundle = self.cnl_override_by_id[case_id]
            else:
                bundle = self.bundle_by_id[case_id]
            reply = {'status': 'ok', 'bundle': bundle}
            return 200, pi.targets.dump(reply).encode()
        if path == '/v1/assemble':
            pkg = payload['bundle']['prompt']['package']
            case_id = self._by_package[pkg]
            reply = {'status': 'ok', 'files': self.files_by_id[case_id]}
            return 200, pi.targets.dump(reply).encode()
        raise AssertionError(f'unexpected HTTP path: {path}')

    def subprocess_run(self, argv: list[str], **kwargs):
        self.cli_calls.append(list(argv))
        cmd = argv[1]
        if cmd == 'compile':
            if self.cli_compile_stdout is not None:
                stdout = self.cli_compile_stdout
            else:
                cnl_text = Path(argv[2]).read_text()
                case_id = self._case_id_for_compile({'format': 'som.cnl.v1', 'source': cnl_text})
                stdout = rc.canon(self.bundle_by_id[case_id]).encode()
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b'')
        if cmd == 'assemble':
            if self.cli_assemble_stdout is not None:
                stdout = self.cli_assemble_stdout
            else:
                bundle = json.loads(Path(argv[2]).read_text())
                case_id = self._by_package[bundle['prompt']['package']]
                reply = {'status': 'ok', 'files': self.files_by_id[case_id]}
                stdout = pi.targets.dump(reply).encode()
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b'')
        raise AssertionError(f'unexpected CLI command: {argv}')


def _make_base_prerequisites(tmp: Path, *, with_ts: bool = True, ts_count: int = 2) -> dict:
    """Create a complete bound prerequisites tree in tmp without touching any real service or port."""
    binary = tmp / 'candidate/som'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b'fake-candidate-binary')
    bin_sha = rc.digest(binary)

    health = {
        'status': 'ok',
        'asset_manifest_sha256': 'a' * 64,
        'model_manifest_sha256': 'b' * 64,
        'blocks_sha256': 'c' * 64,
        'targets': ['python', 'rust'],
        'rust_target': {'formatter_sha256': 'd' * 64},
    }
    runtime_path = tmp / 'candidate/runtime.json'
    runtime_path.write_text(json.dumps({'port': 18960, 'binary_sha256': bin_sha, 'health': health}))
    build_path = tmp / 'candidate/build.json'
    build_path.write_text(json.dumps({'binary_sha256': bin_sha}))
    source_path = tmp / 'candidate/source.json'
    source_path.write_text(json.dumps({'commit': '0' * 40}))

    run_dir = tmp / '.local/typescript-output/acceptance/run-003'
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / 'summary.json'
    summary = {
        'version': pi.TS_RUN if with_ts else 'som.rust-output-acceptance-run.v1',
        'status': 'passed',
        'blockers': [],
        'candidate_after': {'binary_sha256': bin_sha},
        'header_sha256': 'e' * 64,
    }
    summary_path.write_text(json.dumps(summary))

    audit_path = tmp / '.local/typescript-output/acceptance/audit-003.json'
    if with_ts:
        audit_doc = {
            'version': 'som.typescript-output-audit-065.v3',
            'run': str(run_dir.relative_to(tmp)),
            'totals': {'passed': 34, 'attested': 39, 'post_acceptance': 2, 'unrun': 0, 'failed': 0, 'rows': 75},
        }
    else:
        audit_doc = {
            'version': 'som.rust-output-audit-055.v1',
            'run': str(run_dir),
            'totals': {'failed': 0, 'unrun': 0},
        }
    audit_path.write_text(json.dumps(audit_doc))

    legacy_bin = tmp / 'legacy/som'
    legacy_bin.parent.mkdir(parents=True, exist_ok=True)
    legacy_bin.write_bytes(b'fake-legacy-binary')
    legacy_manifest = tmp / 'legacy/manifest.json'
    legacy_manifest.write_bytes(b'{}')
    legacy_blocks = tmp / 'legacy/blocks.json'
    legacy_blocks.write_bytes(b'{}')
    legacy_default = tmp / 'legacy/default.json'
    legacy_default.write_text(json.dumps({
        'command': [str(legacy_bin), 'serve', '--port', '8765'],
        'health': {
            'model_manifest_sha256': rc.digest(legacy_manifest),
            'blocks_sha256': rc.digest(legacy_blocks),
        },
    }))

    pin = pi.Pinner(tmp / 'out/inputs')

    def _make_step_canary(prefix: str, name: str, family: str) -> dict:
        req = pin.write(f'{prefix}/{name}/compile-json.request', b'{"target":"' + family.encode() + b'"}')
        resp = pin.write(f'{prefix}/{name}/compile-json.response', b'{"status":"ok"}')
        cli_in = pin.write(f'{prefix}/{name}/source.cnl', b'Use prompt version 1.\n')
        cli_out = pin.write(f'{prefix}/{name}/cli-compile.stdout', b'{"status":"ok"}')
        return {
            'name': name,
            'family': family,
            'steps': [
                {'kind': 'http', 'name': 'compile-json', 'path': '/v1/compile', 'request': req, 'response': resp},
                {'kind': 'cli', 'name': 'cli-compile', 'args': ['compile', cli_in['path']], 'inputs': [cli_in],
                 'binaries': ['new'], 'response': cli_out},
            ],
        }

    py_flow = _make_step_canary('python', 'smoke12', 'flow')
    py_legacy = _make_step_canary('python', 'legacy100', 'legacy')
    rust_v1 = _make_step_canary('rust', 'example-v1', 'rust')
    rust_v5 = _make_step_canary('rust', 'example-v5', 'rust')

    m2_prompt = pin.write('m2/smoke12/prompt', b'{}')
    m2_resp = pin.write('m2/smoke12/expected_response', b'{"status":"ok"}')

    with mock.patch.object(rc, 'REPO', tmp):
        gates, header_sha256 = pi.gate_assertions(
            run_dir,
            audit_path,
            bin_sha,
            run_dir if with_ts else None,
            ['example-v1', 'example-v5'][:ts_count] if with_ts or ts_count < 2 else ['example-v1', 'example-v5'],
        )

    config = {
        'version': rc.PREREQUISITES_VERSION,
        'repo': str(tmp),
        'base_controller': {'path': str(rc.BASE_PATH), 'sha256': rc.BASE_SHA256},
        'candidate': {
            'binary': pi.binding(binary),
            'runtime': pi.binding(runtime_path),
            'build': pi.binding(build_path),
            'source': pi.binding(source_path),
        },
        'gates': gates,
        'rust_products_run': str(run_dir),
        **({'typescript_products_run': str(run_dir)} if with_ts else {}),
        'legacy_default': pi.binding(legacy_default),
        'legacy_binary': pi.binding(legacy_bin),
        'legacy_model_manifest': pi.binding(legacy_manifest),
        'legacy_blocks': pi.binding(legacy_blocks),
        'python_canaries': [py_flow, py_legacy],
        'rust_canaries': [rust_v1, rust_v5],
        **(
            {
                'typescript_canaries': [
                    _make_step_canary('typescript', f'example-v{idx + 1}', 'typescript')
                    for idx in range(ts_count)
                ]
            }
            if with_ts
            else {}
        ),
        'm2_canaries': [{'name': 'smoke12', 'prompt': m2_prompt, 'expected_response': m2_resp}],
        'acceptance_summary_header_sha256': header_sha256,
    }
    return config


class TestTypeScriptCanaries(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.ts_run = self.tmp / 'ts-run'
        self.files_by_id = {
            'example-v1': _fake_files('example-v1'),
            'example-v5': _fake_files('example-v5'),
        }
        for case_id, files in self.files_by_id.items():
            _write_product_dir(self.ts_run, case_id, files)
        self.sources = pi.ts_sources(self.ts_run, ['example-v1', 'example-v5'])
        self.bundle_by_id = {
            case['id']: _fake_bundle(case['prompt'], target='typescript')
            for case in self.sources
        }
        self.candidate_bin = self.tmp / 'som'
        self.candidate_bin.write_bytes(b'fake-som')
        self.port = 18960

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pass_path_pins_files_and_sends_typescript_target(self) -> None:
        pin = pi.Pinner(self.tmp / 'inputs')
        harness = FakeCandidateHarness(self.bundle_by_id, self.files_by_id)
        with (
            mock.patch.object(rc, 'post_raw', side_effect=harness.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness.subprocess_run),
        ):
            pinned = [pi.ts_case(pin, case, self.port, self.candidate_bin) for case in self.sources]

        self.assertEqual([c['name'] for c in pinned], ['example-v1', 'example-v5'])
        for item in pinned:
            self.assertEqual(item['family'], 'typescript')
            self.assertEqual(item['files'], 3)
            self.assertEqual(
                [s['name'] for s in item['steps']],
                ['compile-json', 'compile-cnl', 'assemble', 'cli-compile', 'cli-assemble'],
            )
            case_dir = self.tmp / 'inputs/typescript' / item['name']
            for filename in (
                'source.cnl',
                'compile-json.request',
                'compile-json.response',
                'compile-cnl.request',
                'compile-cnl.response',
                'assemble.request',
                'assemble.response',
                'bundle.json',
                'cli-compile.stdout',
                'cli-assemble.stdout',
            ):
                self.assertTrue((case_dir / filename).is_file(), f'missing {case_dir / filename}')

            json_req = (case_dir / 'compile-json.request').read_text()
            cnl_req = (case_dir / 'compile-cnl.request').read_text()
            self.assertIn('"target":"typescript"', json_req)
            self.assertIn('"target":"typescript"', cnl_req)
            self.assertEqual(json.loads(json_req)['target'], 'typescript')
            self.assertEqual(json.loads(cnl_req)['target'], 'typescript')

        compile_cli_calls = [c for c in harness.cli_calls if c[1] == 'compile']
        self.assertEqual(len(compile_cli_calls), 2)
        for call in compile_cli_calls:
            self.assertIn('--target', call)
            self.assertEqual(call[call.index('--target') + 1], 'typescript')

    def test_ts_sources_refuses_unknown_rejected_missing_and_repeated_ids(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'repeated TypeScript canary'):
            pi.ts_sources(self.ts_run, ['example-v1', 'example-v1'])

        with self.assertRaisesRegex(RuntimeError, 'unknown TypeScript canary no-such-case'):
            pi.ts_sources(self.ts_run, ['example-v1', 'no-such-case'])

        with self.assertRaisesRegex(RuntimeError, 'unknown TypeScript canary reject-version-unknown'):
            pi.ts_sources(self.ts_run, ['example-v1', 'reject-version-unknown'])

        with self.assertRaisesRegex(RuntimeError, 'example-v2: no accepted product'):
            pi.ts_sources(self.ts_run, ['example-v1', 'example-v2'])

    def test_cnl_and_json_replies_differ_fails(self) -> None:
        pin = pi.Pinner(self.tmp / 'inputs-diff')
        different_bundle = _fake_bundle(
            self.sources[0]['prompt'],
            target='typescript',
            extra={'hashes': {'payload_sha256': 'f' * 64}},
        )
        harness = FakeCandidateHarness(
            self.bundle_by_id,
            self.files_by_id,
            cnl_override_by_id={'example-v1': different_bundle},
        )
        with (
            mock.patch.object(rc, 'post_raw', side_effect=harness.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness.subprocess_run),
        ):
            with self.assertRaisesRegex(RuntimeError, 'TypeScript CNL and prompt JSON compile differ'):
                pi.ts_case(pin, self.sources[0], self.port, self.candidate_bin)

    def test_assembled_file_differs_or_extra_or_missing_file_fails(self) -> None:
        # 1. Content of an assembled file differs from the accepted product.
        bad_content_files = copy.deepcopy(self.files_by_id)
        bad_content_files['example-v1']['src/app.ts'] = '// modified content\n'
        harness = FakeCandidateHarness(self.bundle_by_id, bad_content_files)
        with (
            mock.patch.object(rc, 'post_raw', side_effect=harness.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness.subprocess_run),
        ):
            with self.assertRaisesRegex(RuntimeError, 'example-v1 src/app.ts differs from the accepted product'):
                pi.ts_case(pi.Pinner(self.tmp / 'inputs-content'), self.sources[0], self.port, self.candidate_bin)

        # 2. Extra file in assembled output.
        extra_files = copy.deepcopy(self.files_by_id)
        extra_files['example-v1']['src/extra.ts'] = '// unexpected file\n'
        harness = FakeCandidateHarness(self.bundle_by_id, extra_files)
        with (
            mock.patch.object(rc, 'post_raw', side_effect=harness.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness.subprocess_run),
        ):
            with self.assertRaisesRegex(RuntimeError, 'example-v1 file set differs from the accepted product'):
                pi.ts_case(pi.Pinner(self.tmp / 'inputs-extra'), self.sources[0], self.port, self.candidate_bin)

        # 3. Missing file in assembled output.
        missing_files = copy.deepcopy(self.files_by_id)
        del missing_files['example-v1']['test/api.test.ts']
        harness = FakeCandidateHarness(self.bundle_by_id, missing_files)
        with (
            mock.patch.object(rc, 'post_raw', side_effect=harness.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness.subprocess_run),
        ):
            with self.assertRaisesRegex(RuntimeError, 'example-v1 file set differs from the accepted product'):
                pi.ts_case(pi.Pinner(self.tmp / 'inputs-missing'), self.sources[0], self.port, self.candidate_bin)

    def test_bundle_target_not_typescript_fails(self) -> None:
        wrong_target_bundles = {
            **self.bundle_by_id,
            'example-v1': _fake_bundle(self.sources[0]['prompt'], target='rust'),
        }
        harness = FakeCandidateHarness(wrong_target_bundles, self.files_by_id)
        with (
            mock.patch.object(rc, 'post_raw', side_effect=harness.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness.subprocess_run),
        ):
            with self.assertRaisesRegex(RuntimeError, 'bundle target is not typescript'):
                pi.ts_case(pi.Pinner(self.tmp / 'inputs-target'), self.sources[0], self.port, self.candidate_bin)

    def test_cli_prints_different_bytes_fails(self) -> None:
        # 1. CLI compile stdout differs from compiled bundle.
        harness_compile = FakeCandidateHarness(
            self.bundle_by_id,
            self.files_by_id,
            cli_compile_stdout=b'{"version":"som.dsl-bundle.v2","target":"typescript","different":true}\n',
        )
        with (
            mock.patch.object(rc, 'post_raw', side_effect=harness_compile.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness_compile.subprocess_run),
        ):
            with self.assertRaisesRegex(RuntimeError, 'TypeScript CLI bundle differs'):
                pi.ts_case(pi.Pinner(self.tmp / 'inputs-cli-compile'), self.sources[0], self.port, self.candidate_bin)

        # 2. CLI assemble stdout differs from assembled files response.
        harness_assemble = FakeCandidateHarness(
            self.bundle_by_id,
            self.files_by_id,
            cli_assemble_stdout=b'{"status":"ok","files":{"package.json":"different"}}\n',
        )
        with (
            mock.patch.object(rc, 'post_raw', side_effect=harness_assemble.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness_assemble.subprocess_run),
        ):
            with self.assertRaisesRegex(RuntimeError, 'TypeScript CLI assemble differs'):
                pi.ts_case(pi.Pinner(self.tmp / 'inputs-cli-assemble'), self.sources[0], self.port, self.candidate_bin)

    def test_ts_gate_assertions_pass_and_failures(self) -> None:
        config = _make_base_prerequisites(self.tmp, with_ts=True, ts_count=2)
        with mock.patch.object(rc, 'REPO', self.tmp):
            runtime, _ = rc.check_prerequisites(config)
        self.assertEqual(runtime['port'], 18960)
        self.assertEqual(config['acceptance_summary_header_sha256'], 'e' * 64)
        self.assertEqual(
            config['gates']['audit']['assertions'],
            {
                '/totals/failed': 0,
                '/totals/unrun': 0,
                '/run': '.local/typescript-output/acceptance/run-003',
            },
        )

        audit_path = Path(config['gates']['audit']['path'])
        base_audit = json.loads(audit_path.read_text())

        # totals.failed = 1 fails
        failed_audit = copy.deepcopy(base_audit)
        failed_audit['totals']['failed'] = 1
        audit_path.write_text(json.dumps(failed_audit))
        config['gates']['audit']['sha256'] = rc.digest(audit_path)
        with mock.patch.object(rc, 'REPO', self.tmp):
            with self.assertRaisesRegex(RuntimeError, 'gate failed: /totals/failed'):
                rc.check_prerequisites(config)

        # totals.unrun = 1 fails
        unrun_audit = copy.deepcopy(base_audit)
        unrun_audit['totals']['unrun'] = 1
        audit_path.write_text(json.dumps(unrun_audit))
        config['gates']['audit']['sha256'] = rc.digest(audit_path)
        with mock.patch.object(rc, 'REPO', self.tmp):
            with self.assertRaisesRegex(RuntimeError, 'gate failed: /totals/unrun'):
                rc.check_prerequisites(config)

        # wrong /run fails
        wrong_run_audit = copy.deepcopy(base_audit)
        wrong_run_audit['run'] = '.local/typescript-output/acceptance/run-999'
        audit_path.write_text(json.dumps(wrong_run_audit))
        config['gates']['audit']['sha256'] = rc.digest(audit_path)
        with mock.patch.object(rc, 'REPO', self.tmp):
            with self.assertRaisesRegex(RuntimeError, 'gate failed: /run'):
                rc.check_prerequisites(config)

        # TS acceptance gate without --ts-run or with < 2 TS cases is refused.
        run_dir = self.tmp / '.local/typescript-output/acceptance/run-003'
        with mock.patch.object(rc, 'REPO', self.tmp):
            with self.assertRaisesRegex(RuntimeError, 'TypeScript acceptance gate'):
                pi.gate_assertions(run_dir, audit_path, config['candidate']['binary']['sha256'], None, ['example-v1', 'example-v5'])
            with self.assertRaisesRegex(RuntimeError, 'TypeScript acceptance gate'):
                pi.gate_assertions(run_dir, audit_path, config['candidate']['binary']['sha256'], run_dir, ['example-v1'])

    def test_check_prerequisites_one_ts_canary_refused_and_without_ts_canaries(self) -> None:
        one_ts_dir = self.tmp / 'one-ts'
        one_ts_dir.mkdir()
        config_one = _make_base_prerequisites(one_ts_dir, with_ts=False)
        config_one['typescript_canaries'] = [config_one['rust_canaries'][0]]
        with mock.patch.object(rc, 'REPO', one_ts_dir):
            with self.assertRaisesRegex(RuntimeError, 'need two TypeScript canaries'):
                rc.check_prerequisites(config_one)

        # A tampered step in typescript_canaries is caught by bound() in check_prerequisites.
        tampered_dir = self.tmp / 'tampered-ts'
        tampered_dir.mkdir()
        config_tampered = _make_base_prerequisites(tampered_dir, with_ts=True, ts_count=2)
        resp_path = Path(config_tampered['typescript_canaries'][0]['steps'][0]['response']['path'])
        resp_path.write_bytes(b'{"status":"tampered"}')
        with mock.patch.object(rc, 'REPO', tampered_dir):
            with self.assertRaisesRegex(RuntimeError, 'bound input changed'):
                rc.check_prerequisites(config_tampered)

        # A config without typescript_canaries behaves as before.
        no_ts_dir = self.tmp / 'no-ts'
        no_ts_dir.mkdir()
        config_no_ts = _make_base_prerequisites(no_ts_dir, with_ts=False)
        self.assertNotIn('typescript_canaries', config_no_ts)
        self.assertNotIn('typescript_products_run', config_no_ts)
        with mock.patch.object(rc, 'REPO', no_ts_dir):
            runtime, _ = rc.check_prerequisites(config_no_ts)
        self.assertEqual(runtime['port'], 18960)

    def test_switch_and_check_default_replay_typescript_canaries(self) -> None:
        config = _make_base_prerequisites(self.tmp, with_ts=True, ts_count=2)
        root = self.tmp
        port = 18961
        assets_dir = root / 'som-rs/compiler-assets/versions' / ('a' * 64)
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / 'manifest.json').write_text(json.dumps({'files': {}}))
        manifest_sha = rc.sha_bytes(rc.canon({'files': {}}).encode())
        prev_bin = root / 'som-rs/runtime/versions/prev/som'
        prev_bin.parent.mkdir(parents=True, exist_ok=True)
        prev_bin.write_bytes(b'prev-binary')
        (root / 'som-rs/runtime/current').symlink_to(str(prev_bin.parent))
        (root / 'som-rs/compiler-assets').mkdir(parents=True, exist_ok=True)
        (root / 'som-rs/compiler-assets/current').symlink_to(str(assets_dir))

        health = json.loads(Path(config['candidate']['runtime']['path']).read_text())['health']
        health['asset_manifest_sha256'] = manifest_sha
        Path(config['candidate']['runtime']['path']).write_text(
            json.dumps({
                'port': 18960,
                'binary_sha256': config['candidate']['binary']['sha256'],
                'health': health,
            })
        )
        config['candidate']['runtime']['sha256'] = rc.digest(config['candidate']['runtime']['path'])

        receipt_owner = {'pid': 99999, 'argv': [str(prev_bin), 'serve', '--assets', str(assets_dir), '--port', str(port)], 'start': 't0', 'cwd': str(root)}
        default_receipt = {
            'version': 'som.default-runtime.v2',
            'accepted': True,
            'pid': 99999,
            'command': receipt_owner['argv'],
            'health': health,
            'identity': {**receipt_owner, 'binary_sha256': rc.digest(prev_bin)},
        }
        (root / 'som-rs/runtime/default.json').write_text(json.dumps(default_receipt, indent=2) + '\n')

        canary_calls: list[tuple[str, list[str]]] = []

        class FakeRelease:
            def __init__(self, output, cwd):
                self.output = output
                self.children = []
                self.owners = {}
                self.recovering = False
                self.evidence_errors = []

            def save(self, name, data):
                pass

            def event(self, stage, **kwargs):
                pass

            def health(self, p, h):
                return h

            def start(self, argv, h):
                owner = {'pid': 99998, 'argv': argv, 'port': int(argv[-1]), 'start': 't1', 'cwd': str(root)}
                self.owners[owner['port']] = owner
                return owner

            def stop_child(self, owner):
                pass

            def m2_canary(self, p, clients, cases):
                pass

            def canary(self, p, binaries, cases, label):
                canary_calls.append((label, [c['family'] for c in cases]))

        with (
            mock.patch.object(rc, 'REPO', root),
            mock.patch.object(rc, 'Release', FakeRelease),
            mock.patch.object(rc, 'identity', side_effect=lambda pid, argv, p: (
                default_receipt['identity'] if pid == 99999 else {'pid': pid, 'argv': argv, 'port': p, 'start': 't1', 'cwd': str(root)}
            )),
            mock.patch.object(rc, 'free_harness_port', return_value=18962),
            mock.patch.object(rc, 'terminate'),
        ):
            switch.execute(config, root, port, self.tmp / 'switch-out', None, True, None)

        self.assertEqual(
            canary_calls,
            [
                ('current-default', ['flow', 'legacy']),
                ('clone', ['flow', 'legacy']),
                ('clone', ['flow', 'legacy', 'rust', 'rust', 'typescript', 'typescript']),
                ('new-default', ['flow', 'legacy', 'rust', 'rust', 'typescript', 'typescript']),
                ('new-default-after-pointer', ['flow', 'legacy', 'rust', 'rust', 'typescript', 'typescript']),
            ],
        )

        # Also verify check_default replays typescript_canaries on the candidate, and Python-only on previous.
        for rel, state in check_default.LEGACY_POINTERS.items():
            link = root / rel
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(state['target'])
        prereq_file = self.tmp / 'prereq.json'
        prereq_file.write_text(json.dumps(config))
        toolchain_file = root / 'som-rs/contracts/rust-output-v1/toolchain.json'
        toolchain_file.parent.mkdir(parents=True, exist_ok=True)
        fake_rustfmt = self.tmp / 'rustfmt'
        fake_rustfmt.write_bytes(b'fake-rustfmt')
        health['rust_target']['formatter_sha256'] = rc.digest(fake_rustfmt)
        toolchain_file.write_text(json.dumps({'rust': {'bin_dir': str(self.tmp)}}))
        Path(config['candidate']['runtime']['path']).write_text(
            json.dumps({
                'port': 18960,
                'binary_sha256': config['candidate']['binary']['sha256'],
                'health': health,
            })
        )
        config['candidate']['runtime']['sha256'] = rc.digest(config['candidate']['runtime']['path'])
        prereq_file.write_text(json.dumps(config))

        committed_receipt = json.loads((root / 'som-rs/runtime/default.json').read_text())
        committed_receipt['health'] = health
        committed_receipt['identity']['binary_sha256'] = rc.digest(committed_receipt['command'][0])
        (root / 'som-rs/runtime/default.json').write_text(json.dumps(committed_receipt, indent=2) + '\n')

        canary_calls.clear()
        with (
            mock.patch.object(rc, 'REPO', root),
            mock.patch.object(rc, 'Release', FakeRelease),
            mock.patch.object(rc, 'identity', return_value=committed_receipt['identity']),
            mock.patch.object(
                sys,
                'argv',
                [
                    'check_default.py',
                    '--prerequisites', str(prereq_file),
                    '--root', str(root),
                    '--port', str(port),
                    '--expect-binary-sha256', config['candidate']['binary']['sha256'],
                    '--output', str(self.tmp / 'check-candidate'),
                ],
            ),
            mock.patch('builtins.print'),
        ):
            code = check_default.main()
        self.assertEqual(code, 0)
        self.assertEqual(
            canary_calls,
            [('post-check', ['flow', 'legacy', 'rust', 'rust', 'typescript', 'typescript'])],
        )

    def test_prepare_inputs_main_with_and_without_ts_run(self) -> None:
        root = self.tmp / 'main-repo'
        root.mkdir()
        candidate_dir = root / 'candidate'
        candidate_dir.mkdir()
        cand_bin = candidate_dir / 'som'
        cand_bin.write_bytes(b'cand-bin')
        bin_sha = rc.digest(cand_bin)

        manifest_file = root / 'som-rs/models/current/manifest.json'
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_bytes(b'{}')
        blocks_file = root / 'blocks.json'
        blocks_file.write_bytes(b'{}')

        health = {
            'status': 'ok',
            'asset_manifest_sha256': 'a' * 64,
            'model_manifest_sha256': rc.digest(manifest_file),
            'blocks_sha256': rc.digest(blocks_file),
        }
        (candidate_dir / 'runtime.json').write_text(
            json.dumps({'port': 18960, 'binary_sha256': bin_sha, 'health': health})
        )
        (candidate_dir / 'build.json').write_text(json.dumps({'binary_sha256': bin_sha}))
        (candidate_dir / 'source.json').write_text(json.dumps({'commit': '0' * 40}))

        switch_dir = root / 'switch-001'
        switch_dir.mkdir()
        legacy_default_path = switch_dir / '0002-legacy-default.json'
        legacy_bin = root / 'legacy-som'
        legacy_bin.write_bytes(b'legacy-bin')
        legacy_default_path.write_text(
            json.dumps({
                'command': [str(legacy_bin), 'serve', '--model', 'm', '--blocks', str(blocks_file), '--port', '8765'],
                'health': health,
            })
        )
        (switch_dir / '0001-prerequisites.json').write_text(json.dumps({'canaries': []}))

        default_json = root / 'som-rs/runtime/default.json'
        default_json.parent.mkdir(parents=True, exist_ok=True)
        default_json.write_text(
            json.dumps({
                'command': [str(cand_bin), 'serve', '--assets', 'a', '--port', '8765'],
                'identity': {'binary_sha256': bin_sha},
                'health': health,
                'previous_default_sha256': rc.digest(legacy_default_path),
            })
        )

        run_dir = root / '.local/typescript-output/acceptance/run-003'
        for name in ('example_v1', 'example_v5'):
            prod = run_dir / 'products' / name
            prod.mkdir(parents=True, exist_ok=True)
            for rel, content in self.files_by_id[name.replace('_', '-')].items():
                target = prod / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        for case_id, files in self.files_by_id.items():
            _write_product_dir(run_dir, case_id, files)

        (run_dir / 'summary.json').write_text(
            json.dumps({
                'version': pi.TS_RUN,
                'status': 'passed',
                'blockers': [],
                'candidate_after': {'binary_sha256': bin_sha},
                'header_sha256': '1' * 64,
            })
        )
        audit_path = root / '.local/typescript-output/acceptance/audit-003.json'
        audit_path.write_text(
            json.dumps({
                'version': 'som.typescript-output-audit-065.v3',
                'run': '.local/typescript-output/acceptance/run-003',
                'totals': {'failed': 0, 'unrun': 0, 'passed': 34},
            })
        )

        def _fake_py_case(pin, case, prev, new, port, prefix='python'):
            name, fam = ('smoke12', 'flow') if case == 1 else ('legacy100', 'legacy')
            req = pin.write(f'{prefix}/{name}.request', b'{}')
            resp = pin.write(f'{prefix}/{name}.response', b'{"status":"ok"}')
            pinned = {
                'name': name,
                'family': fam,
                'steps': [{'kind': 'http', 'name': 'generate', 'path': '/v1/generate', 'request': req, 'response': resp}],
            }
            m2 = {
                'name': f'm2-{name}',
                'prompt': pin.write(f'm2/{name}.p', b'{}'),
                'expected_response': pin.write(f'm2/{name}.r', b'{}'),
            }
            return pinned, m2

        def _fake_rust_case(pin, case, port, new):
            req = pin.write(f'rust/{case["id"]}/compile-json.request', b'{}')
            resp = pin.write(f'rust/{case["id"]}/compile-json.response', b'{"status":"ok"}')
            return {
                'name': case['id'],
                'family': 'rust',
                'steps': [{'kind': 'http', 'name': 'compile-json', 'path': '/v1/compile', 'request': req, 'response': resp}],
            }

        harness = FakeCandidateHarness(self.bundle_by_id, self.files_by_id)
        out_with_ts = root / 'out-with-ts'

        with (
            mock.patch.object(rc, 'REPO', root),
            mock.patch.object(pi, 'SWITCH_001', switch_dir),
            mock.patch.object(rc, 'post_raw', side_effect=harness.post_raw),
            mock.patch.object(pi.subprocess, 'run', side_effect=harness.subprocess_run),
            mock.patch.object(pi, 'rust_case', side_effect=_fake_rust_case),
            mock.patch.object(pi, 'python_case', side_effect=_fake_py_case),
            mock.patch('builtins.print'),
        ):
            (switch_dir / '0001-prerequisites.json').write_text(json.dumps({'canaries': [1, 2]}))
            if True:
                # 1. With --ts-run: writes typescript_canaries and typescript_products_run.
                with mock.patch.object(
                    sys,
                    'argv',
                    [
                        'prepare_inputs.py',
                        '--candidate', str(candidate_dir),
                        '--acceptance', str(run_dir),
                        '--audit', str(audit_path),
                        '--out', str(out_with_ts),
                        '--ts-run', str(run_dir),
                    ],
                ):
                    pi.main()
                written = json.loads((out_with_ts / 'prerequisites.json').read_text())
                self.assertEqual(written['typescript_products_run'], str(run_dir))
                self.assertEqual([c['name'] for c in written['typescript_canaries']], ['example-v1', 'example-v5'])
                self.assertEqual([c['family'] for c in written['typescript_canaries']], ['typescript', 'typescript'])

                # 2. TS acceptance run without --ts-run is refused in main().
                with mock.patch.object(
                    sys,
                    'argv',
                    [
                        'prepare_inputs.py',
                        '--candidate', str(candidate_dir),
                        '--acceptance', str(run_dir),
                        '--audit', str(audit_path),
                        '--out', str(root / 'out-refused-no-ts'),
                    ],
                ):
                    with self.assertRaisesRegex(RuntimeError, 'TypeScript acceptance gate'):
                        pi.main()

                # 3. Rust acceptance run without --ts-run omits both TS keys.
                (run_dir / 'summary.json').write_text(
                    json.dumps({
                        'version': 'som.rust-output-acceptance-run.v1',
                        'status': 'passed',
                        'blockers': [],
                        'candidate_after': {'binary_sha256': bin_sha},
                        'header_sha256': '1' * 64,
                    })
                )
                audit_path.write_text(
                    json.dumps({
                        'version': 'som.rust-output-audit-055.v1',
                        'run': str(run_dir),
                        'totals': {'failed': 0, 'unrun': 0},
                    })
                )
                out_no_ts = root / 'out-no-ts'
                with mock.patch.object(
                    sys,
                    'argv',
                    [
                        'prepare_inputs.py',
                        '--candidate', str(candidate_dir),
                        '--acceptance', str(run_dir),
                        '--audit', str(audit_path),
                        '--out', str(out_no_ts),
                    ],
                ):
                    pi.main()
                written_no_ts = json.loads((out_no_ts / 'prerequisites.json').read_text())
                self.assertNotIn('typescript_canaries', written_no_ts)
                self.assertNotIn('typescript_products_run', written_no_ts)


if __name__ == '__main__':
    unittest.main()
