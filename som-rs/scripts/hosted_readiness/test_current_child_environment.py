"""Check retained run identity at the native child boundary on GitHub only."""
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('Run these fixtures on GitHub only')

path = Path(__file__).with_name('product_runtime') / 'full_product_context.py'
subject = ModuleType('_current_child_environment_subject')
subject.__file__ = str(path)
exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), subject.__dict__)


class NativeChildEnvironment(unittest.TestCase):
    def context(self, mode='native-qa-v1'):
        return SimpleNamespace(
            runtime=lambda: {'tools': {'python': Path('/runtime/python'), 'uv': Path('/runtime/uv')}},
            rust_tools=lambda: {name: Path('/rust/bin') / name for name in ('cargo', 'rustc', 'rustfmt')},
            data={'runtime_root': '/runtime', 'observer_python': '/observer/bin/python'},
            work=Path('/work'), repo=Path('/repo'), path=Path('/work/context.json'), qa_mode=mode,
            helper_revision='1' * 40, candidate_revision='1' * 40)

    def environment(self, **changes):
        return {'RUNNER_TEMP': '/runner', 'GITHUB_RUN_ID': '40832270009',
                'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_REPOSITORY': 'faberline/som',
                'GITHUB_SHA': '1' * 40, 'GITHUB_JOB': 'full-products', **changes}

    def test_exact_run_identity_is_retained_without_keyring(self):
        with mock.patch.dict(os.environ, self.environment(M6_INPUTS_KEYRING='must-not-propagate'), clear=True):
            result = subject.Context.child_environment(self.context())
        for key in ('GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_REPOSITORY', 'GITHUB_JOB'):
            self.assertEqual(result[key], self.environment()[key])
        self.assertNotIn('M6_INPUTS_KEYRING', result)

    def test_native_missing_or_malformed_run_identity_is_refused(self):
        for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
            for value in (None, '', '0', '01', '-1', '1x'):
                with self.subTest(key=key, value=value):
                    environment = self.environment()
                    if value is None:
                        environment.pop(key)
                    else:
                        environment[key] = value
                    with mock.patch.dict(os.environ, environment, clear=True):
                        with self.assertRaises(subject.Refusal):
                            subject.Context.child_environment(self.context())

    def test_other_repository_is_refused(self):
        with mock.patch.dict(os.environ, self.environment(GITHUB_REPOSITORY='other/repo'), clear=True):
            with self.assertRaises(subject.Refusal):
                subject.Context.child_environment(self.context())

    def test_source_must_be_the_actual_same_candidate(self):
        for value in (None, '', 'short', '2' * 40):
            with self.subTest(value=value):
                environment = self.environment()
                if value is None:
                    environment.pop('GITHUB_SHA')
                else:
                    environment['GITHUB_SHA'] = value
                with mock.patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(subject.Refusal):
                        subject.Context.child_environment(self.context())
        context = self.context()
        context.candidate_revision = '2' * 40
        with mock.patch.dict(os.environ, self.environment(), clear=True):
            with self.assertRaises(subject.Refusal):
                subject.Context.child_environment(context)

    def test_missing_or_invalid_job_identity_is_refused(self):
        for value in (None, '', '../job', 'job with spaces', 'job\nother'):
            with self.subTest(value=value):
                environment = self.environment()
                if value is None:
                    environment.pop('GITHUB_JOB')
                else:
                    environment['GITHUB_JOB'] = value
                with mock.patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(subject.Refusal):
                        subject.Context.child_environment(self.context())

    def test_preparation_keeps_its_previous_environment_contract(self):
        with mock.patch.dict(os.environ, {'RUNNER_TEMP': '/runner'}, clear=True):
            result = subject.Context.child_environment(self.context('experimental-preparation'))
        self.assertNotIn('GITHUB_RUN_ID', result)
        self.assertNotIn('GITHUB_RUN_ATTEMPT', result)
        self.assertNotIn('GITHUB_REPOSITORY', result)
        self.assertNotIn('GITHUB_SHA', result)
        self.assertNotIn('GITHUB_JOB', result)


if __name__ == '__main__':
    unittest.main()
