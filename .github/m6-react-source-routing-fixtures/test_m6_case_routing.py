"""Synthetic product child routing. Execute on GitHub-hosted runners only."""
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

common = ModuleType('common')
common.CONTRACT = Path('/tmp/fixture-contract')
common.REPO = Path('/tmp/fixture-repo')
with mock.patch.dict(sys.modules, {'common':common, 'check_contract':ModuleType('check_contract')}):
    import check_products as subject


class CaseRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'])
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)/'repo'; self.repo.mkdir()
        self.run = self.repo/'.local/run'; self.work = self.run/'products/case'; self.work.mkdir(parents=True)
        (self.run/'api-qa-evidence').mkdir()
        self.before = self.run/'api-qa-evidence/full-before.json'; self.before.write_text('{}')
        self.entry = 'som-rs/scripts/react_output_acceptance/m6_native_entry.py'
        self.target = 'som-rs/tests/react-output-v1/run_gates.py'
        self.recorder = 'som-rs/scripts/react_output_acceptance/record_api.py'
        self.calls=[]
        self.sources=SimpleNamespace(read=lambda path:self.calls.append(path) or b'fixture')
        self.ctx=SimpleNamespace(qa_mode='native-qa-v1',qa_source_authority={
            'admitted_repository_python_sources':[self.entry,self.target,self.recorder]},
            path=Path('/tmp/context.json'),helper_revision='a'*40,candidate_revision='a'*40)
        self.holder=SimpleNamespace(qa_mode='native-qa-v1',context=self.ctx,live={'python':'/tmp/venv/bin/python'},
                                    router=SimpleNamespace(sources=self.sources),api_profile=Path('/tmp/api.json'),
                                    full_before=self.before)

    def test_native_gate_records_logical_actual_argv_and_wait(self):
        logical=['/tmp/venv/bin/python',str(self.repo/self.target),'product','work']
        captured=[]
        def spawn(argv, **kwargs):
            captured.append(argv)
            Path(argv[argv.index('--postcheck')+1]).write_bytes(b'{}')
            return SimpleNamespace(pid=124,wait=lambda timeout:0)
        with mock.patch.object(subject,'REPO',self.repo), mock.patch.dict(sys.modules,{'_som_m6_checked_router':self.holder}), \
             mock.patch.object(subject.subprocess,'Popen',side_effect=spawn), \
             mock.patch.object(subject.os,'killpg',side_effect=ProcessLookupError):
            record=subject.gate_invocation(logical[1:],self.work/'gates.log',self.work/'gates-invocation.json',logical[0],self.before)
        self.assertEqual(record['argv'],logical)
        self.assertEqual(record['wait'],{'state':'completed','exit_code':0})
        self.assertEqual(record['source_routing']['actual_argv'],captured[0])
        self.assertEqual(captured[0][:5],[logical[0],'-I','-S','-B',str(self.repo/self.entry)])
        self.assertIn('--scope',captured[0])
        self.assertEqual(record['source_routing']['pid'],124)
        self.assertEqual(self.calls,[self.entry,self.target])

    def test_native_api_records_source_postcheck_and_original_argv(self):
        logical=['/tmp/venv/bin/python',str(self.repo/self.recorder),'case','product','work']
        def spawn(argv, **kwargs):
            Path(argv[argv.index('--postcheck')+1]).write_bytes(b'{}')
            return SimpleNamespace(pid=125,wait=lambda timeout:0)
        with mock.patch.object(subject,'REPO',self.repo), mock.patch.dict(sys.modules,{'_som_m6_checked_router':self.holder}), \
             mock.patch.object(subject.subprocess,'Popen',side_effect=spawn), \
             mock.patch.object(subject.os,'killpg',side_effect=ProcessLookupError):
            record=subject.api_invocation(logical[1:],self.work/'api.log',self.work/'api-invocation.json',logical[0],full_before=self.before)
        self.assertEqual(record['argv'],logical)
        self.assertEqual(record['wait'],{'state':'completed','exit_code':0})
        self.assertEqual(record['source_routing']['pid'],125)
        self.assertEqual(record['source_routing']['postcheck_sha256'],subject.retained_evidence.digest(self.work/'api-source.json'))

    def test_unadmitted_target_refuses_before_launch(self):
        self.ctx.qa_source_authority['admitted_repository_python_sources'].remove(self.target)
        with mock.patch.object(subject,'REPO',self.repo), mock.patch.dict(sys.modules,{'_som_m6_checked_router':self.holder}), \
             mock.patch.object(subject.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):
                subject.gate_invocation([str(self.repo/self.target)],self.work/'gates.log',self.work/'gates-invocation.json',
                                        '/tmp/venv/bin/python',self.before)
            spawn.assert_not_called()

    def test_signal_during_start_waits_owned_case_child_before_returning(self):
        class Child:
            pid=124
            def __init__(self): self.signals=[]
            def poll(self): return None
            def send_signal(self, value): self.signals.append(value)
            def wait(self, timeout): return 3
        child=Child()
        def spawn(argv, **kwargs):
            Path(argv[argv.index('--postcheck')+1]).write_bytes(b'{}')
            os.kill(os.getpid(), subject.signal.SIGTERM)
            return child
        with mock.patch.object(subject,'REPO',self.repo), mock.patch.dict(sys.modules,{'_som_m6_checked_router':self.holder}), \
             mock.patch.object(subject.subprocess,'Popen',side_effect=spawn), \
             mock.patch.object(subject.os,'killpg',side_effect=ProcessLookupError):
            with self.assertRaises(KeyboardInterrupt):
                subject.api_invocation([str(self.repo/self.recorder)],self.work/'api.log',self.work/'api-invocation.json',
                                       '/tmp/venv/bin/python',full_before=self.before)
        import json
        saved=json.loads((self.work/'api-invocation.json').read_bytes())
        self.assertEqual(saved['outcome'],'interrupted')
        self.assertEqual(saved['interruption_cleanup']['recorder_wait'],'completed')
        self.assertEqual(saved['interruption_cleanup']['owned_services'],'unverified')
        self.assertEqual(child.signals,[subject.signal.SIGINT])

    def test_missing_postcheck_is_unverified_even_after_zero_exit(self):
        with mock.patch.object(subject,'REPO',self.repo), mock.patch.dict(sys.modules,{'_som_m6_checked_router':self.holder}), \
             mock.patch.object(subject.subprocess,'Popen',return_value=SimpleNamespace(pid=124,wait=lambda timeout:0)), \
             mock.patch.object(subject.os,'killpg',side_effect=ProcessLookupError):
            record=subject.gate_invocation([str(self.repo/self.target)],self.work/'gates.log',self.work/'gates-invocation.json',
                                           '/tmp/venv/bin/python',self.before)
        self.assertEqual(record['outcome'],'unverified')
        self.assertEqual(record['failure'],'source-postcheck-missing')


if __name__ == '__main__': unittest.main()
