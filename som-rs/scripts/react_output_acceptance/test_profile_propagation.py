"""Synthetic profile propagation fixtures. Run only on GitHub-hosted runners."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

sys.path.insert(0, str(Path(__file__).absolute().parent))
import run_acceptance
import check_products


def scratch():
    return Path(tempfile.mkdtemp(prefix='som-react-profile-fixture-')).resolve()


class ProfilePropagationFixtures(unittest.TestCase):
    def test_build_steps_changes_only_products_and_keeps_raw_capture(self):
        root = scratch()
        profile = root / 'profile.json'
        options = {'python_bin': '/synthetic/python', 'candidate_dir': root / 'candidate',
                   'candidate_url': 'http://127.0.0.1:18919', 'baseline_url': 'http://127.0.0.1:18917',
                   'legacy_run': '/synthetic/legacy', 'out_dir': root / 'run',
                   'contract_receipt': 'receipt.json', 'record_raw_http': True,
                   'cleanup_products_runtime': True}
        old = run_acceptance.build_steps(**options)
        new = run_acceptance.build_steps(**options, qa_profile=str(profile))
        self.assertEqual(len(old), 12)
        self.assertEqual(old[:-1], new[:-1])
        self.assertEqual(new[-1]['step'], 'products')
        self.assertEqual(new[-1]['argv'], old[-1]['argv'] + ['--qa-profile', str(profile)])
        self.assertEqual(new[-1]['requires_manifest'], old[-1]['requires_manifest'])

    def test_cli_defaults_and_profile_identity_refuse_links(self):
        root = scratch()
        arguments = ['--candidate', str(root / 'candidate'), '--out', str(root / 'out')]
        self.assertIsNone(run_acceptance.parse_args(arguments).qa_profile)
        profile = root / 'profile.json'
        profile.write_bytes(b'{"synthetic":true}\n')
        self.assertEqual(run_acceptance.qa_profile_snapshot(profile),
                         {'path': str(profile), 'sha256': hashlib.sha256(profile.read_bytes()).hexdigest()})
        self.assertEqual(run_acceptance.parse_args([*arguments, '--qa-profile', str(profile)]).qa_profile, profile)
        link = root / 'linked-profile'
        link.symlink_to(profile)
        for invalid in (link, root / 'missing', Path('relative.json')):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                run_acceptance.qa_profile_snapshot(invalid)

    def test_gate_argv_keeps_default_cache_and_profile_separate(self):
        root = scratch()
        product, work, cache, profile = (root / item for item in ('product', 'work', 'cache', 'profile'))
        old = check_products.gate_argv(product, work, cache)
        new = check_products.gate_argv(product, work, cache, profile)
        self.assertEqual(old[-2:], ['--cache', str(cache)])
        self.assertEqual(new[-2:], ['--qa-profile', str(profile)])
        self.assertNotIn('--cache', new)
        self.assertEqual(old[:-2], new[:-2])

    def test_all_129_cases_receive_same_validated_profile_and_cache(self):
        root = scratch()
        cache, profile, manifest, out = (root / item for item in ('cache', 'profile', 'manifest.json', 'out'))
        cache.mkdir()
        profile.write_text('synthetic profile')
        manifest.write_text(json.dumps({'products': [{'id': f'case-{i:03d}'} for i in range(129)]}))
        validator = mock.Mock(return_value={'cache': cache, 'profile_path': profile, 'profile_sha256': 'a' * 64})
        recheck = mock.Mock()
        module = SimpleNamespace(validate_profile=validator, recheck=recheck)
        observed = []
        def fake_case(url, entry, out_arg, cache_arg, api, profile_arg):
            observed.append((entry['id'], cache_arg, profile_arg))
            return {'id': entry['id'], 'passed': True, 'problems': []}
        argv = ['check_products.py', '--url', 'http://127.0.0.1:18919', '--manifest', str(manifest),
                '--out', str(out), '--qa-profile', str(profile)]
        with mock.patch.object(sys, 'argv', argv), mock.patch.dict(sys.modules, {'linux_qa_profile': module}), \
             mock.patch.object(check_products, 'check_case', side_effect=fake_case), contextlib.redirect_stdout(io.StringIO()):
            code = check_products.main()
        self.assertEqual(code, 0)
        validator.assert_called_once_with(profile)
        self.assertEqual(observed, [(f'case-{i:03d}', cache, profile) for i in range(129)])
        report = json.loads((out / 'report.json').read_text())
        self.assertEqual(report['case_count'], 129)
        self.assertTrue(report['complete'])
        self.assertEqual(report['vendored_cache'], str(cache))
        self.assertEqual(report['qa_profile'], {'path': str(profile), 'sha256': 'a' * 64})
        self.assertEqual(report['qa_profile_final_recheck'], {'passed': True, 'failure_reason': None})
        recheck.assert_called_once()

    def test_final_qa_recheck_failure_blocks_pass_and_legacy_mode_stays_unchanged(self):
        for mode in ('linux-failure', 'legacy'):
            with self.subTest(mode=mode):
                root = scratch()
                cache, profile, manifest, out = (root / item for item in ('cache', 'profile', 'manifest.json', 'out'))
                cache.mkdir()
                profile.write_text('synthetic profile')
                manifest.write_text(json.dumps({'products': [{'id': f'case-{i:03d}'} for i in range(129)]}))
                validator = mock.Mock(return_value={'cache': cache, 'profile_path': profile, 'profile_sha256': 'a' * 64})
                recheck = mock.Mock(side_effect=ValueError('cache changed'))
                module = SimpleNamespace(validate_profile=validator, recheck=recheck)
                calls = []
                def fake_case(url, entry, out_arg, cache_arg, api, *rest):
                    calls.append((entry['id'], cache_arg, rest))
                    return {'id': entry['id'], 'passed': True, 'problems': []}
                argv = ['check_products.py', '--url', 'http://127.0.0.1:18919', '--manifest', str(manifest), '--out', str(out)]
                argv += ['--qa-profile', str(profile)] if mode == 'linux-failure' else ['--cache', str(cache)]
                with mock.patch.object(sys, 'argv', argv), mock.patch.dict(sys.modules, {'linux_qa_profile': module}), \
                     mock.patch.object(check_products, 'check_case', side_effect=fake_case), contextlib.redirect_stdout(io.StringIO()):
                    code = check_products.main()
                report = json.loads((out / 'report.json').read_text())
                self.assertEqual(len(calls), 129)
                if mode == 'linux-failure':
                    self.assertEqual(code, 1)
                    self.assertFalse(report['passed'])
                    self.assertEqual(report['qa_profile_final_recheck'],
                                     {'passed': False, 'failure_reason': 'ValueError: cache changed'})
                    self.assertTrue(all(rest == (profile,) for _, _, rest in calls))
                    recheck.assert_called_once()
                else:
                    self.assertEqual(code, 0)
                    self.assertTrue(report['passed'])
                    self.assertNotIn('qa_profile', report)
                    self.assertNotIn('qa_profile_final_recheck', report)
                    self.assertTrue(all(rest == () for _, _, rest in calls))
                    validator.assert_not_called()
                    recheck.assert_not_called()

    def test_unsealed_or_conflicting_profile_refuses_before_case_work(self):
        root = scratch()
        profile, manifest, out = (root / item for item in ('profile', 'manifest', 'out'))
        profile.write_text('synthetic')
        manifest.write_text('{}')
        validator = mock.Mock(side_effect=ValueError('Linux QA profile authority is not sealed'))
        module = SimpleNamespace(validate_profile=validator)
        base = ['check_products.py', '--url', 'http://127.0.0.1:18919', '--manifest', str(manifest),
                '--out', str(out), '--qa-profile', str(profile)]
        with mock.patch.object(sys, 'argv', base), mock.patch.dict(sys.modules, {'linux_qa_profile': module}), \
             mock.patch.object(check_products, 'check_case') as case, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(check_products.main(), 2)
            case.assert_not_called()
        self.assertFalse(out.exists())
        with mock.patch.object(sys, 'argv', [*base, '--cache', str(root / 'other-cache')]), \
             mock.patch.dict(sys.modules, {'linux_qa_profile': module}), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                check_products.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(out.exists())

    def test_acceptance_refuses_unsealed_profile_before_output_or_any_step(self):
        root = scratch()
        candidate, out, profile = root / 'candidate', root / 'out', root / 'profile'
        candidate.mkdir()
        profile.write_bytes(b'unsealed profile')
        validator = mock.Mock(side_effect=ValueError('Linux QA profile authority is not sealed'))
        module = SimpleNamespace(validate_profile=validator)
        with mock.patch.object(run_acceptance, 'REPO', root), \
             mock.patch.dict(sys.modules, {'linux_qa_profile': module}), \
             mock.patch.object(run_acceptance, 'validate_preconditions') as preconditions, \
             mock.patch.object(run_acceptance, 'run_single_step') as step, contextlib.redirect_stderr(io.StringIO()):
            code = run_acceptance.main(['--candidate', str(candidate), '--out', str(out), '--qa-profile', str(profile)])
        self.assertEqual(code, 2)
        validator.assert_called_once_with(profile)
        preconditions.assert_not_called()
        step.assert_not_called()
        self.assertFalse(out.exists())

    def test_acceptance_refuses_profile_changed_during_validation(self):
        root = scratch()
        candidate, out, profile = root / 'candidate', root / 'out', root / 'profile'
        candidate.mkdir()
        profile.write_bytes(b'first profile')
        before = hashlib.sha256(profile.read_bytes()).hexdigest()
        def validate(path):
            path.write_bytes(b'changed profile')
            return {'profile_path': path, 'profile_sha256': before}
        module = SimpleNamespace(validate_profile=validate)
        with mock.patch.object(run_acceptance, 'REPO', root), \
             mock.patch.dict(sys.modules, {'linux_qa_profile': module}), \
             mock.patch.object(run_acceptance, 'run_single_step') as step, contextlib.redirect_stderr(io.StringIO()):
            code = run_acceptance.main(['--candidate', str(candidate), '--out', str(out), '--qa-profile', str(profile)])
        self.assertEqual(code, 2)
        step.assert_not_called()
        self.assertFalse(out.exists())

    def test_acceptance_fails_if_profile_changes_during_run(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                root = scratch()
                candidate, out, profile = root / 'candidate', root / 'out', root / 'profile'
                candidate.mkdir()
                (candidate / 'build.json').write_text('{}')
                (candidate / 'runtime.json').write_text('{}')
                profile.write_bytes(b'original profile')
                snapshot = {'candidate_binary_sha256': 'a' * 64, 'candidate_healthz': {}, 'baseline_healthz': {},
                            'port_8765_healthz': {}, 'git_head': 'b' * 40, 'react_output_acceptance_files': {}}
                calls, seen_steps = [], []
                def capture(**kwargs):
                    calls.append(1)
                    if changed and len(calls) == 2:
                        profile.write_bytes(b'changed profile')
                    return snapshot
                def fake_step(step, out_arg, repo):
                    seen_steps.append(step)
                    receipt = {'index': step['index'], 'step': step['step'], 'judgment': 'passed',
                               'reason': '', 'exit_code': 0, 'seconds': 0, 'report': None, 'report_sha256': None}
                    path = out_arg / 'receipts' / f"{step['index']:02d}-{step['step']}.json"
                    path.write_text(json.dumps(receipt))
                    return receipt
                validated = ({}, {}, 'http://127.0.0.1:18919', 18919, 'http://127.0.0.1:18917', 18917)
                profile_sha = hashlib.sha256(profile.read_bytes()).hexdigest()
                module = SimpleNamespace(validate_profile=mock.Mock(return_value={'profile_path': profile,
                                                                                  'profile_sha256': profile_sha}))
                with mock.patch.object(run_acceptance, 'REPO', root), \
                     mock.patch.dict(sys.modules, {'linux_qa_profile': module}), \
                     mock.patch.object(run_acceptance, 'validate_preconditions', return_value=validated), \
                     mock.patch.object(run_acceptance, 'capture_identity_snapshot', side_effect=capture), \
                     mock.patch.object(run_acceptance, 'evaluate_identity', return_value={'passed': True, 'problems': []}), \
                     mock.patch.object(run_acceptance, 'find_latest_contract_receipt', return_value=root / 'receipt.json'), \
                     mock.patch.object(run_acceptance, 'run_single_step', side_effect=fake_step):
                    code = run_acceptance.main(['--candidate', str(candidate), '--out', str(out), '--qa-profile', str(profile)])
                summary = json.loads((out / 'run.json').read_text())
                identity = json.loads((out / 'identity.json').read_text())
                self.assertEqual(len(seen_steps), 12)
                self.assertEqual(seen_steps[-1]['argv'][-2:], ['--qa-profile', str(profile)])
                self.assertEqual(identity['qa_profile']['unchanged'], not changed)
                self.assertEqual(summary['identity_passed'], not changed)
                self.assertEqual(code, 1 if changed else 0)
                self.assertEqual(summary['status'], 'failed' if changed else 'passed')


if __name__ == '__main__':
    unittest.main()
