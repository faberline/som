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
        venv=top/'venv';venv.mkdir(); qa=top/'qa.json';qa.write_text('{}')
        api=top/'api.json';api.write_text(json.dumps({'version':'som.react-linux-api-profile.v1',
                                                       'node_profile':str(qa),'venv':str(venv)}))
        self.inputs={'baseline_profile':'som-rs/scripts/hosted_readiness/baseline.json',
                     'legacy_run':str(top/'legacy'),'rust_baseline_run':str(top/'rust'),
                     'qa_profile':str(qa),'api_profile':str(api),'api_closure_tar':str(self.closure),
                     'collector_out':str(self.original),'audit_out':str(self.repo/'.local/audit.json')}
        observer=str(top/'observer/bin/python');driver='som-rs/scripts/hosted_readiness/run_full_products.py'
        context={'repo':str(self.repo),'helper_revision':self.rev,'candidate_revision':self.rev,
                 'qa_mode':'native-qa-v1','observer_python':observer,'m6_react_inputs':self.inputs}
        (self.shared/'context.input.json').write_text(json.dumps(context))
        collect=s.collector_arguments(candidate_dir=self.candidate,collector_out=self.original,
                  baseline_url='http://127.0.0.1:18979',legacy_run=self.inputs['legacy_run'],
                  rust_baseline_run=self.inputs['rust_baseline_run'],python=venv/'bin/python',
                  qa_profile=self.inputs['qa_profile'],api_profile=self.inputs['api_profile'])
        audit_args=s.auditor_arguments(repo=self.repo,collector_out=self.original,
                       audit_out=Path(self.inputs['audit_out']),api_closure_tar=self.closure,candidate_dir=self.candidate)
        expected={'collector':collect,'audit':audit_args}
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
                 'command':{'argv':[observer,'-I','-S','-B',str(self.repo/driver),'--execute-tool',tool,'--',*expected[name]],
                            'cwd':str(self.repo),'pid':100+code,'exit_code':code,'wait_completed':True,
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
                     'collector_capture':self.copy,'input_paths':{**self.inputs,'candidate_dir':str(self.candidate)}}
        self.save()
        self.shared_proof={'owned_waits_complete':True,'owned_cleanup_complete':True,
                           'context_sha256':s.sha((self.shared/'context.input.json').read_bytes())}
        self.approved={'.local/approved.raw':{'sha256':s.sha(b'approved input'),'bytes':len(b'approved input'),
                                               'mode':self.input.stat().st_mode & 0o777}}
        self.derived_files={}
    def save(self):
        self.report['saved_files_sha256']={k:v for k,v in s.inventory(self.owner).items() if k!='result.json'}
        (self.owner/'result.json').write_text(json.dumps(self.report))
    @contextlib.contextmanager
    def derived(self,*args):
        files={'.local/approved.raw':{'sha256':s.sha(b'approved input'),'bytes':len(b'approved input')},
               **self.derived_files}
        class Bound:
            def __init__(_):_.files=files
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

    def test_command_argv_cwd_and_output_path_must_match_context(self):
        for operation in ('argv','cwd','output'):
            with self.subTest(operation=operation):
                old=json.loads(json.dumps(self.report))
                if operation=='argv':self.report['rows'][0]['command']['argv'][-1]='tampered'
                elif operation=='cwd':self.report['rows'][0]['command']['cwd']='/tmp/other'
                else:self.report['input_paths']['collector_out']=str(self.repo/'.local/other')
                self.save()
                with self.assertRaises(ValueError):self.check()
                self.report=old;self.save()

    def test_source_symlink_or_special_file_is_not_complete(self):
        import shutil
        for kind in ('symlink','fifo'):
            with self.subTest(kind=kind):
                item=self.original/kind
                if kind=='symlink':item.symlink_to('request.raw')
                else:os.mkfifo(item)
                shutil.rmtree(self.owner/'collector')
                self.report['collector_capture']=s.copy_capture(self.original,self.owner/'collector')
                self.save()
                with self.assertRaisesRegex(ValueError,'source tree differs'):
                    self.check()
                item.unlink()
                shutil.rmtree(self.owner/'collector')
                self.report['collector_capture']=s.copy_capture(self.original,self.owner/'collector')
                self.save()

    def test_derived_roles_are_not_relabelled_as_approved_manifest(self):
        path=self.repo/'.local/derived.raw';path.write_bytes(b'derived')
        path.chmod(0o644)
        self.derived_files['.local/derived.raw']={'sha256':s.sha(b'derived'),'bytes':7,'source':'later-capture-derived'}
        self.audit['files']['.local/derived.raw']=s.sha(b'derived')
        (self.owner/'audit.json').write_text(json.dumps(self.audit));self.report['audit_sha256']=s.sha((self.owner/'audit.json').read_bytes());self.save()
        self.assertTrue(self.check()['complete'])
        self.approved['.local/derived.raw']={'sha256':s.sha(b'derived'),'bytes':7,'mode':0o644}
        with self.assertRaisesRegex(ValueError, 'derived React input identity'):
            self.check()
        del self.approved['.local/derived.raw']
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'derived React input identity'):
            self.check()
        self.audit['totals']['unrun']=1
        (self.owner/'audit.json').write_text(json.dumps(self.audit));self.report['audit_sha256']=s.sha((self.owner/'audit.json').read_bytes());self.save()
        with self.assertRaises(ValueError):self.check()

if __name__=='__main__':unittest.main()
