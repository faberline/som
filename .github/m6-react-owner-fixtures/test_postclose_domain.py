"""Synthetic boundary tests. They do not establish real React acceptance."""
import contextlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')
import m6_react_callback as s


class DomainBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'])
        self.addCleanup(self.temp.cleanup)
        top=Path(self.temp.name).resolve(); self.repo=top/'repo'; self.repo.mkdir()
        self.original=self.repo/'.local/run'; self.original.mkdir(parents=True)
        (self.original/'request.raw').write_bytes(b'original raw')
        self.input=self.repo/'.local/approved.raw'; self.input.write_bytes(b'approved input')
        self.shared=top/'shared'; self.owner=self.shared/'owner'; self.owner.mkdir(parents=True)
        self.candidate=self.shared/'candidate'; self.candidate.mkdir()
        self.binary=b'actual synthetic candidate'; (self.candidate/'som').write_bytes(self.binary)
        (self.candidate/'build.json').write_text(json.dumps({'binary_sha256':s.sha(self.binary)}))
        runtime={'pid':4321,'binary_sha256':s.sha(self.binary)}
        (self.candidate/'runtime.json').write_text(json.dumps(runtime))
        sd=self.shared/'services/current'; sd.mkdir(parents=True); (sd/'runtime.json').write_text(json.dumps(runtime))
        self.closure=top/'closure.tar'; self.closure.write_bytes(b'closure')
        self.copy=s.copy_capture(self.original,self.owner/'collector')
        self.source=b'synthetic source'; self.rev='a'*40
        self.rows=[]
        for name,tool,code in [('collector',s.COLLECTOR,0),('audit',s.AUDITOR,1)]:
            log=self.owner/'logs'; log.mkdir(exist_ok=True)
            for kind in ('stdout','stderr'): (log/(name+'.'+kind)).write_bytes((name+kind).encode())
            post={'version':'som.full-product-checker-postcheck.v1','tool':tool,'pid':100+code,
                  'helper_revision':self.rev,'candidate_revision':self.rev,'entry_executed':True,
                  'entry_exit_code':code,'source_runtime_tools_inputs_unchanged':True,
                  'product_acceptance_passed':False,'source_sha256':{tool:s.sha(self.source)}}
            postpath=log/(name+'.postcheck.json');postpath.write_text(json.dumps(post))
            row={'id':name,'streams_complete':True,'capture_errors':[],
                 'stdout':'logs/'+name+'.stdout','stderr':'logs/'+name+'.stderr',
                 'postcheck':'logs/'+name+'.postcheck.json','postcheck_sha256':s.sha(postpath.read_bytes()),
                 'command':{'argv':['synthetic'], 'pid':100+code,'exit_code':code,'wait_completed':True,
                            'owned_group_cleanup_passed':True,'stdout':'raw-out','stderr':'raw-err',
                            'stdout_sha256':s.sha((log/(name+'.stdout')).read_bytes()),
                            'stderr_sha256':s.sha((log/(name+'.stderr')).read_bytes())}}
            self.rows.append(row)
        files={'.local/run/request.raw':s.sha(b'original raw'),'.local/approved.raw':s.sha(b'approved input'),
               'source.py':s.sha(self.source)}
        for name in ('som','build.json','runtime.json'):
            files['@owned-candidate/'+name]=s.sha((self.candidate/name).read_bytes())
        self.audit={'version':'som.react-output-audit-130.v1','run':'.local/run','rows':[{'id':'fixture','status':'passed'}],
                    'totals':{'passed':1,'failed':0,'unrun':0},'missing_files':[],'files':files,
                    'owned_candidate_process_binding':'unverified'}
        (self.owner/'audit.json').write_text(json.dumps(self.audit))
        self.report={'version':'som.m6-react-graph-callback.v1','gate':s.GATE,'helper_revision':self.rev,
                     'candidate_revision':self.rev,'product_acceptance_passed':False,
                     'owned_waits_complete':False,'owned_cleanup_complete':False,'rows':self.rows,
                     'bound_source_sha256':{s.SELF:s.sha(self.source)},'audit_sha256':s.sha((self.owner/'audit.json').read_bytes()),
                     'collector_capture':self.copy,'input_paths':{'collector_out':str(self.original),
                     'candidate_dir':str(self.candidate),'api_closure_tar':str(self.closure)}}
        self.save()
        self.shared_proof={'owned_waits_complete':True,'owned_cleanup_complete':True}
        self.approved={'.local/approved.raw':{'sha256':s.sha(b'approved input'),'bytes':len(b'approved input'),
                                               'mode':self.input.stat().st_mode & 0o777}}
    def save(self):
        self.report['saved_files_sha256']={k:v for k,v in s.inventory(self.owner).items() if k!='result.json'}
        (self.owner/'result.json').write_text(json.dumps(self.report))
    @contextlib.contextmanager
    def derived(self,*args):
        class Bound:
            def read(_,path):return Path(path).read_bytes()
        yield Bound()
    @contextlib.contextmanager
    def auditor(self,*args):
        class Audit:
            def run_audit(_, *args):return self.audit
        yield Audit()
    def check(self):
        with mock.patch.object(s,'approved_manifest',return_value=self.approved), \
             mock.patch.object(s,'source_bound_derived_inputs',self.derived), \
             mock.patch.object(s,'source_bound_auditor',self.auditor):
            return s.verify_react_raw_domain(self.owner,lambda _:self.source,self.rev,self.rev,s.GATE,
                                              repo=self.repo,proof_roots={},shared=self.shared_proof)
    def test_complete_synthetic_domain_and_identity(self):
        proof=self.check();self.assertTrue(proof['complete']);self.assertEqual(proof['candidate_pid'],4321)
        self.assertEqual(proof['candidate_binary_sha256'],s.sha(self.binary))
        self.assertFalse(proof['product_acceptance_passed'])
    def test_changed_original_or_retained_raw_fails(self):
        (self.original/'request.raw').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.check()
        (self.original/'request.raw').write_bytes(b'original raw')
        (self.owner/'collector/request.raw').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.check()
    def test_shared_wait_or_candidate_runtime_mismatch_fails(self):
        self.shared_proof['owned_waits_complete']=False
        with self.assertRaises(ValueError):self.check()
        self.shared_proof['owned_waits_complete']=True
        (self.shared/'services/current/runtime.json').write_text('{}')
        with self.assertRaises(ValueError):self.check()
    def test_unbound_input_or_audit_failure_is_not_accepted(self):
        self.approved.clear()
        with self.assertRaises(ValueError):self.check()
        self.audit['totals']['unrun']=1
        (self.owner/'audit.json').write_text(json.dumps(self.audit));self.report['audit_sha256']=s.sha((self.owner/'audit.json').read_bytes());self.save()
        with self.assertRaises(ValueError):self.check()

if __name__=='__main__':unittest.main()
