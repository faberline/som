"""Synthetic process-argv fixtures. Execute only on GitHub-hosted runners."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import run_acceptance as collector


class CollectorRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'])
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)/'repo'; self.repo.mkdir()
        self.out = self.repo/'.local/run'; (self.out/'logs').mkdir(parents=True); (self.out/'receipts').mkdir()
        (self.out/'api-qa-evidence').mkdir()
        (self.out/'api-qa-evidence/full-before.json').write_text('{}')
        self.target = 'fake_checker.py'; (self.repo/self.target).write_text('# fixture')
        self.result = self.out/'result.json'; self.result.write_text('{"passed":true}')
        self.step = {'index':1,'step':'fake','argv':['/live/bin/python',self.target,'--flag'],
                     'report':self.result,'kind':'report'}
        self.ctx = SimpleNamespace(qa_mode='native-qa-v1',
            qa_source_authority={'admitted_repository_python_sources':[collector.NATIVE_ENTRY,self.target]},
            sources=SimpleNamespace(read=lambda name: b'bound'), path=Path('/tmp/context.json'),
            helper_revision='a'*40,candidate_revision='a'*40)
        self.module = SimpleNamespace(context=lambda:self.ctx)

    def test_native_argv_wait_and_source_postcheck_are_recorded(self):
        calls = []
        def spawn(argv, **kwargs):
            calls.append(argv)
            Path(argv[argv.index('--postcheck')+1]).write_text('{}')
            return SimpleNamespace(pid=123,returncode=0,wait=lambda:0)
        with mock.patch.object(collector, 'NATIVE_ROUTING_REVIEWED', True), \
             mock.patch.dict(sys.modules, {'full_product_context':self.module}), \
             mock.patch.object(collector.subprocess,'Popen',side_effect=spawn):
            result = collector.run_single_step(self.step,self.out,self.repo,Path('/tmp/api.json'))
        self.assertEqual(result['argv'],self.step['argv'])
        route = result['source_routing']
        self.assertEqual(route['actual_argv'][:5], ['/live/bin/python','-I','-S','-B',str(self.repo/collector.NATIVE_ENTRY)])
        self.assertEqual(route['wait'],{'state':'completed','exit_code':0})
        self.assertEqual(route['pid'],123)
        self.assertEqual(route['postcheck_sha256'],collector.sha_bytes(b'{}'))
        self.assertEqual(result['judgment'],'passed')
        self.assertEqual(calls,[route['actual_argv']])

    def test_missing_source_authority_refuses_before_child_start(self):
        self.ctx.qa_source_authority['admitted_repository_python_sources'].remove(self.target)
        with mock.patch.object(collector, 'NATIVE_ROUTING_REVIEWED', True), \
             mock.patch.dict(sys.modules, {'full_product_context':self.module}), \
             mock.patch.object(collector.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):
                collector.run_single_step(self.step,self.out,self.repo,Path('/tmp/api.json'))
            spawn.assert_not_called()

    def test_legacy_child_argv_is_unchanged(self):
        with mock.patch.object(collector.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            result = collector.run_single_step(self.step,self.out,self.repo)
        self.assertEqual(run.call_args.args[0],self.step['argv'])
        self.assertNotIn('source_routing',result)
        self.assertEqual(result['judgment'],'passed')

    def test_native_http_wrapper_has_explicit_mode_legacy_does_not(self):
        common = dict(python_bin='/py',candidate_dir=Path('/candidate'),candidate_url='http://127.0.0.1:18978',
                      baseline_url='http://127.0.0.1:18979',legacy_run='/legacy',out_dir=self.out,
                      contract_receipt='/receipt',record_raw_http=True)
        native = collector.build_steps(**common,qa_profile='/qa-profile',api_profile='/api-profile')
        legacy = collector.build_steps(**common)
        names = {'react-python-controls','python-baseline','rust-baseline','rust-python-controls','ts-python-controls'}
        for steps,flag in ((native,True),(legacy,False)):
            for step in steps:
                if step['step'] in names:
                    self.assertEqual('--qa-mode' in step['argv'],flag)
                    if flag: self.assertEqual(step['argv'][step['argv'].index('--qa-mode')+1],'native-qa-v1')


if __name__ == '__main__': unittest.main()
