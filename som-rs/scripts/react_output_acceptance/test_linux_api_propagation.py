"""Synthetic profile propagation and child waits on GitHub-hosted runners."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import importlib.util
from types import ModuleType
from types import SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import run_acceptance
with mock.patch.dict(sys.modules, {'check_contract': ModuleType('check_contract')}):
    import check_products
recorder_spec = importlib.util.spec_from_file_location('fixture_record_api', Path(__file__).with_name('record_api.py'))
record_api = importlib.util.module_from_spec(recorder_spec)
recorder_spec.loader.exec_module(record_api)


class PropagationFixtures(unittest.TestCase):
    def test_each_entrypoint_binds_retained_source_before_execution(self):
        source = Path(__file__).with_name('retained_evidence.py').read_bytes()
        for load in (run_acceptance.load_pinned_retained, check_products.load_pinned_retained, record_api.load_pinned_retained):
            with self.subTest(entrypoint=load.__module__), tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
                repo = Path(directory).resolve()
                path = repo / 'som-rs/scripts/react_output_acceptance/retained_evidence.py'
                path.parent.mkdir(parents=True)
                path.write_bytes(source)
                with mock.patch.dict(sys.modules):
                    sys.modules.pop('retained_evidence', None)
                    loaded = load(repo)
                    self.assertEqual(loaded.__file__, str(path))
                path.write_bytes(b'raise AssertionError("tampered source ran")\n')
                with mock.patch.dict(sys.modules):
                    sys.modules.pop('retained_evidence', None)
                    with self.assertRaises(ValueError):
                        load(repo)
                path.write_bytes(source)
                with mock.patch.dict(sys.modules, {'retained_evidence': ModuleType('retained_evidence')}):
                    with self.assertRaises(ValueError):
                        load(repo)

    def test_main_retains_before_and_after_around_all_steps(self):
        for final_failure in (False, True):
            with self.subTest(final_failure=final_failure), tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
                root = Path(directory).resolve()
                candidate, out, node_profile, api_profile = (root / name for name in ('candidate', 'run', 'node.json', 'api.json'))
                candidate.mkdir()
                (candidate / 'build.json').write_text('{}')
                (candidate / 'runtime.json').write_text('{}')
                node_profile.write_bytes(b'node')
                api_profile.write_bytes(b'api')
                node_sha = run_acceptance.sha_file(node_profile)
                node = SimpleNamespace(__file__=str(root / 'som-rs/tests/react-output-v1/linux_qa_profile.py'))
                context = {'node_profile_path': node_profile, 'profile_sha256': node_sha,
                           'context_sha256': 'a' * 64, 'python': str(root / 'venv/bin/python')}
                order = []
                def full_record(_context):
                    order.append('full-before' if 'begin' not in order else 'full-after')
                    return {'source': 'fixture'}
                def recheck(_context):
                    order.append('full-recheck')
                    if final_failure:
                        raise ValueError('fixture changed')
                module = SimpleNamespace(validate=lambda *_args, **_kwargs: context, full_record=full_record, recheck=recheck)
                def begin(api_root, _context, _module):
                    order.append('begin')
                    (api_root / 'retained.json').write_text('{}')
                    return {}, {}
                def finish(api_root, _context, _module, _state, _runtime):
                    order.append('finish')
                    (api_root / 'retained.json').write_text('{"finished":true}')
                    return True
                evidence = SimpleNamespace(begin=begin, finish=finish)
                recorder = SimpleNamespace(load_linux_sources=lambda _repo, **_kwargs: (module, object(), evidence))
                snapshot = {'candidate_binary_sha256': 'a' * 64, 'candidate_healthz': {}, 'baseline_healthz': {},
                            'port_8765_healthz': {}, 'git_head': 'b' * 40, 'react_output_acceptance_files': {}}
                def node_begin(api_root, _repo, _profile, _module):
                    api_root.mkdir()
                    (api_root / 'identity.json').write_text('{}')
                    return {'profile': {'before': {'sha256': node_sha}}}, {}
                def run_step(item, output, _repo):
                    order.append('step-' + item['step'])
                    row = {'index': item['index'], 'step': item['step'], 'judgment': 'passed', 'reason': '',
                           'exit_code': 0, 'seconds': 0, 'report': None, 'report_sha256': None}
                    (output / 'receipts' / f"{item['index']:02d}-{item['step']}.json").write_text(json.dumps(row))
                    return row
                valid = ({}, {}, 'http://127.0.0.1:18919', 18919, 'http://127.0.0.1:18918', 18918)
                with mock.patch.object(run_acceptance, 'REPO', root), \
                     mock.patch.object(run_acceptance, 'LINUX_QA_CLI', True), \
                     mock.patch.object(run_acceptance, 'validate_qa_profile', return_value={'path': str(node_profile), 'sha256': node_sha}), \
                     mock.patch.object(run_acceptance, 'load_api_recorder', return_value=recorder), \
                     mock.patch.dict(sys.modules, {'linux_qa_profile': node}), \
                     mock.patch.object(run_acceptance.retained_evidence, 'begin', side_effect=node_begin), \
                     mock.patch.object(run_acceptance.retained_evidence, 'finish', return_value=True), \
                     mock.patch.object(run_acceptance, 'validate_preconditions', return_value=valid), \
                     mock.patch.object(run_acceptance, 'capture_identity_snapshot', return_value=snapshot), \
                     mock.patch.object(run_acceptance, 'evaluate_identity', return_value={'passed': True, 'problems': []}), \
                     mock.patch.object(run_acceptance, 'find_latest_contract_receipt', return_value=root / 'receipt.json'), \
                     mock.patch.object(run_acceptance, 'run_single_step', side_effect=run_step):
                    code = run_acceptance.main(['--candidate', str(candidate), '--out', str(out),
                                                '--qa-profile', str(node_profile), '--api-profile', str(api_profile)])
                self.assertEqual(code, 1 if final_failure else 0)
                self.assertLess(order.index('begin'), order.index('step-contract'))
                self.assertLess(order.index('step-products'), order.index('full-recheck'))
                self.assertLess(order.index('full-recheck'), order.index('finish'))
                self.assertTrue((out / 'api-qa-evidence/full-before.json').is_file())
                self.assertEqual((out / 'api-qa-evidence/full-after.json').is_file(), not final_failure)
                self.assertEqual(json.loads((out / 'identity.json').read_bytes())['api_qa_profile']['recheck_passed'], not final_failure)

    def test_unreviewed_api_bootstrap_refuses_activation(self):
        with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
            with self.assertRaises(ValueError):
                check_products.load_api_recorder(Path(directory))
            with self.assertRaises(ValueError):
                run_acceptance.load_api_recorder(Path(directory))

    def test_profiles_change_only_last_of_twelve_steps(self):
        with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
            root = Path(directory)
            options = {'python_bin': '/qa/python', 'candidate_dir': root / 'candidate',
                       'candidate_url': 'http://127.0.0.1:18919', 'baseline_url': 'http://127.0.0.1:18918',
                       'legacy_run': '/qa/legacy', 'out_dir': root / 'run', 'contract_receipt': 'receipt.json'}
            old = run_acceptance.build_steps(**options)
            new = run_acceptance.build_steps(**options, qa_profile='/qa/node-profile', api_profile='/qa/api-profile')
            self.assertEqual(len(old), 12)
            self.assertEqual(old[:-1], new[:-1])
            self.assertEqual(new[-1]['argv'], old[-1]['argv'] +
                             ['--qa-profile', '/qa/node-profile', '--api-profile', '/qa/api-profile'])

    def test_api_child_exit_is_a_real_wait_result(self):
        with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
            root = Path(directory)
            row = check_products.api_invocation(['-c', 'raise SystemExit(7)'], root / 'api.log',
                                                root / 'api-invocation.json', sys.executable)
            self.assertEqual(row['outcome'], 'exited')
            self.assertEqual(row['exit_code'], 7)
            self.assertEqual(row['wait'], {'state': 'completed', 'exit_code': 7})

    def test_unknown_wait_cannot_be_completed(self):
        with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
            root = Path(directory)
            with mock.patch.object(check_products.subprocess, 'Popen', side_effect=OSError('unknown')):
                row = check_products.api_invocation(['-c', 'pass'], root / 'api.log',
                                                    root / 'api-invocation.json', sys.executable)
            self.assertEqual(row['outcome'], 'process-error')
            self.assertEqual(row['wait'], {'state': 'unverified', 'exit_code': None})

    def test_timeout_interrupts_only_the_owned_recorder_and_keeps_failure(self):
        with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
            root = Path(directory)
            # The child owns its nested service. SIGINT triggers its real finally,
            # which stops and waits that service before the recorder exits.
            code = ("import pathlib,subprocess,sys,time\n"
                    "marker=pathlib.Path(sys.argv[1])\n"
                    "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
                    "try:\n"
                    " marker.write_text(str(p.pid))\n"
                    " time.sleep(30)\n"
                    "finally:\n"
                    " p.terminate(); p.wait(timeout=5); marker.write_text('stopped')\n")
            marker = root / 'child-state'
            row = check_products.api_invocation(['-c', code, str(marker)], root / 'api.log',
                                                root / 'api-invocation.json', sys.executable, timeout=1, grace=10)
            self.assertEqual(row['outcome'], 'timed-out')
            self.assertEqual(row['failure'], 'timeout')
            self.assertEqual(row['timeout_cleanup']['method'], 'interrupt-recorder')
            self.assertEqual(row['timeout_cleanup']['recorder_wait'], 'completed')
            self.assertEqual(row['timeout_cleanup']['owned_services'], 'requires-raw-proof')
            self.assertEqual(marker.read_text(), 'stopped')


if __name__ == '__main__':
    unittest.main()
