"""Synthetic routing audit checks. Execute on GitHub-hosted runners only."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import audit130


class Reader:
    def __init__(self, repo): self.repo = repo
    def path(self, value, base=None):
        path = Path(value)
        if not path.is_absolute(): path = (base or self.repo)/path
        if '..' in path.parts or not path.is_relative_to(self.repo): raise audit130.EvidenceError('unsafe')
        return path
    def bytes(self, value): return self.path(value).read_bytes()
    def json(self, value): return json.loads(self.bytes(value))
    def hash(self, value): return audit130.sha(self.bytes(value))


class RoutingAuditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']); self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)/'repo'; self.repo.mkdir()
        self.run = self.repo/'.local/run'; (self.run/'source-routing').mkdir(parents=True)
        (self.run/'api-qa-evidence').mkdir()
        self.reader = Reader(self.repo)
        self.audit = SimpleNamespace(repo=self.repo, run=self.run, reader=self.reader, api_qa_profile={})
        self.rev = 'a'*40
        self.entry = 'som-rs/scripts/react_output_acceptance/m6_native_entry.py'
        self.wrapper = 'som-rs/scripts/react_output_acceptance/record_http.py'
        self.nested = 'som-rs/scripts/typescript_output_acceptance/check_python_baseline.py'
        self.paths = [self.entry, self.wrapper, self.nested,
            'som-rs/scripts/hosted_readiness/same_candidate_graph.py',
            'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py',
            'som-rs/scripts/react_output_acceptance/retained_evidence.py',
            'som-rs/scripts/react_output_acceptance/linux_api_context.py']
        self.pins = {}
        for name in self.paths:
            path=self.repo/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(name.encode())
            self.pins[name]=self.reader.hash(name)
        self.context_path = self.run/'source-routing/context.json'
        self.write(self.context_path,{'repo':str(self.repo),'qa_mode':'native-qa-v1',
                                     'helper_revision':self.rev,'candidate_revision':self.rev})
        self.write(self.run/'identity.json',{'git_head':{'before':self.rev}})
        (self.run/'api-qa-evidence/full-before.json').write_bytes(b'{}')
        self.original_context = '/tmp/som-test-context.json'
        self.python = '/tmp/venv/bin/python'
        self.audit.api_qa_profile = {'python': self.python}
        self.profile_path = '/tmp/api-profile.json'
        self.post_path = self.run/'source-routing/01-python-baseline.json'
        self.logical = [self.python,str(self.repo/self.wrapper),'--out','/tmp/unused','--qa-mode','native-qa-v1',
                        '--',self.nested,'--flag']
        self.run_doc = {'native_source_context':{'path':'source-routing/context.json',
                        'original_path':self.original_context,'sha256':self.reader.hash(self.context_path)},
                        'api_qa_profile':{'path':self.profile_path,'sha256':'b'*64}}
        self.post = {'version':'som.m6-native-source-entry.v1','pid':123,
            'helper_revision':self.rev,'candidate_revision':self.rev,'logical_argv':[self.wrapper,*self.logical[2:]],
            'scope':'step','interpreter':self.python,'site_packages':'/tmp/venv/lib/python3.12/site-packages',
            'profile_sha256':'b'*64,'full_before_sha256':self.reader.hash(self.run/'api-qa-evidence/full-before.json'),
            'entry_executed':True,'entry_exit_code':0,'source_postcheck':True,'product_acceptance_passed':False,
            'executed':[{'path':x,'sha256':self.pins[x]} for x in (self.wrapper,self.nested)],
            'source_sha256':copy.deepcopy(self.pins)}
        self.actual = [self.python,'-I','-S','-B',str(self.repo/self.entry),'--repo',str(self.repo),
            '--product-context',self.original_context,'--helper-revision',self.rev,'--candidate-revision',self.rev,
            '--api-profile',self.profile_path,'--full-before',str(self.run/'api-qa-evidence/full-before.json'),
            '--postcheck',str(self.post_path),'--script',self.wrapper,'--scope','step','--',*self.logical[2:]]
        self.receipt={'argv':self.logical,'exit_code':0,'source_routing':{'actual_argv':self.actual,'pid':123,
            'wait':{'state':'completed','exit_code':0},'postcheck':str(self.post_path)}}
        self.store()

    def write(self, path, value): path.write_text(json.dumps(value,sort_keys=True))
    def store(self):
        self.write(self.post_path,self.post)
        self.receipt['source_routing']['postcheck_sha256']=self.reader.hash(self.post_path)
    def check(self): audit130.Audit.native_child_routing(self.audit,1,'python-baseline',self.receipt,self.run_doc)

    def test_verified_logical_and_actual_route(self): self.check()

    def test_refuses_wrong_wait_missing_source_and_nested_binding(self):
        original = copy.deepcopy(self.post)
        for mutation in ('wait','source','nested','argv'):
            with self.subTest(mutation=mutation):
                self.post=copy.deepcopy(original); self.receipt['source_routing']['actual_argv']=self.actual[:]
                self.receipt['source_routing']['wait']={'state':'completed','exit_code':0}
                if mutation=='wait': self.receipt['source_routing']['wait']['exit_code']=False
                if mutation=='source': del self.post['source_sha256'][self.entry]
                if mutation=='nested': self.post['executed'].pop()
                if mutation=='argv': self.receipt['source_routing']['actual_argv'][1]='-B'
                self.store()
                with self.assertRaises(audit130.EvidenceError): self.check()

    def test_case_route_checks_actual_launcher_wait_and_source(self):
        case = self.run/'products/case'; case.mkdir(parents=True)
        script = 'som-rs/tests/react-output-v1/run_gates.py'
        target = self.repo/script; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(b'gate')
        pins = dict(self.pins); pins[script] = self.reader.hash(script)
        post_path = case/'gates-source.json'
        logical = [self.python,str(target),'product','work','--qa-profile','/tmp/node.json']
        post = dict(self.post,scope='case',logical_argv=[script,*logical[2:]],
                    executed=[{'path':script,'sha256':pins[script]}],source_sha256=pins)
        self.write(post_path,post)
        actual = [self.python,'-I','-S','-B',str(self.repo/self.entry),'--repo',str(self.repo),
            '--product-context',self.original_context,'--helper-revision',self.rev,'--candidate-revision',self.rev,
            '--api-profile',self.profile_path,'--full-before',str(self.run/'api-qa-evidence/full-before.json'),
            '--postcheck',str(post_path),'--script',script,'--scope','case','--',*logical[2:]]
        invocation={'argv':logical,'exit_code':0,'wait':{'state':'completed','exit_code':0},
                    'source_routing':{'actual_argv':actual,'pid':123,'owned_group_absent':True,'postcheck':str(post_path),
                                      'postcheck_sha256':self.reader.hash(post_path)}}
        self.write(self.run/'run.json',self.run_doc)
        audit130.Audit.native_case_routing(self.audit,case,invocation,script,'gates')
        self.audit._native_case_routes_checked=set()
        invocation['wait']['exit_code']=False
        with self.assertRaises(audit130.EvidenceError):
            audit130.Audit.native_case_routing(self.audit,case,invocation,script,'gates')
        invocation['wait']['exit_code']=0
        self.audit.api_qa_profile['python']='/tmp/other-python'
        with self.assertRaises(audit130.EvidenceError):
            audit130.Audit.native_case_routing(self.audit,case,invocation,script,'gates')

    def test_step_rejects_interpreter_not_in_verified_profile(self):
        self.audit.api_qa_profile['python']='/tmp/other-python'
        with self.assertRaises(audit130.EvidenceError): self.check()

    def test_changed_source_bytes_refused(self):
        (self.repo/self.nested).write_bytes(b'tampered')
        with self.assertRaises(audit130.EvidenceError): self.check()


if __name__ == '__main__': unittest.main()
