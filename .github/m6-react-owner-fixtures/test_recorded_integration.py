"""Synthetic interface and source binding; never a product acceptance."""
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')
import m6_react_callback as s

class OracleIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']);self.addCleanup(self.tmp.cleanup)
        self.shared=Path(self.tmp.name).resolve();self.root=self.shared/'owner';self.root.mkdir()
        self.inputs=self.shared/'data';self.inputs.mkdir();self.run=self.shared/'repo'/'run';self.run.mkdir(parents=True)
        self.oracle=self.shared/'services/baseline/oracle';self.oracle.mkdir(parents=True)
        self.profile=b'synthetic profile';self.rev='a'*40
        self.context={'baseline_inputs':{s.ORACLE_PROFILE:str(self.inputs)}}
        self.manifest={'services':{'baseline':{'extra':{'profile_path':s.ORACLE_PROFILE,'input_root':str(self.inputs),'oracle_out':'oracle'}}}}
        self.write(self.root/'result.json',{'input_paths':{'baseline_profile':s.ORACLE_PROFILE}})
        self.write(self.shared/'context.input.json',self.context);self.write(self.shared/'services/manifest.json',self.manifest)
        self.write(self.shared/'services/baseline/runtime.json',{'pid':1234})
        self.write(self.oracle/'table.json',[]);self.write(self.oracle/'result.json',{'calls':[]})
        self.proof={'version':'som.recorded-before-run-verification.v1','profile_sha256':s.sha(self.profile),
            'helper_revision':self.rev,'candidate_revision':self.rev,'table_sha256':s.sha((self.oracle/'table.json').read_bytes()),
            'table':[],'calls':[],'result_sha256':s.sha((self.oracle/'result.json').read_bytes()),
            'runtime_named_coverage':'owner_verifier_required','product_acceptance_passed':False}
    def write(self,path,obj):path.write_text(json.dumps(obj))
    def reader(self,path):
        if path==s.ORACLE_PROFILE:return self.profile
        if path==s.ORACLE:return ('def verify_recorded_run(*args, **kwargs):\n return '+repr(self.proof)+'\n').encode()
        raise AssertionError(path)
    def shared_proof(self):return {'context_sha256':s.sha((self.shared/'context.input.json').read_bytes()),
                                  'services_manifest_sha256':s.sha((self.shared/'services/manifest.json').read_bytes())}
    def check(self):
        with mock.patch.object(s,'join_recorded_named_rows',return_value={'complete':True}) as join:
            value=s.verify_recorded_named_scope(self.root,self.reader,self.rev,self.rev,repo=self.run.parent,original=self.run,shared=self.shared_proof())
            join.assert_called_once_with(self.run,self.oracle,[],[],expected_counts={'react_python':2684,'rust006':699,'typescript125':492})
            return value
    def test_source_bound_join_and_no_module_leak(self):
        self.assertTrue(self.check()['complete']);self.assertNotIn('_som_m6_react_recorded_before_verifier',sys.modules)
    def test_manifest_context_input_mismatch_rejected(self):
        self.manifest['services']['baseline']['extra']['input_root']=str(self.shared)
        self.write(self.shared/'services/manifest.json',self.manifest)
        with self.assertRaisesRegex(ValueError,'service input binding'):self.check()
    def test_profile_proof_mismatch_rejected(self):
        self.proof['profile_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'oracle proof differs'):self.check()
    def test_changed_table_and_wrong_runtime_identity_rejected(self):
        self.proof['table']=[{'fake':True}]
        with self.assertRaisesRegex(ValueError,'oracle proof differs'):self.check()
        self.proof['table']=[];self.proof['helper_revision']='b'*40
        with self.assertRaisesRegex(ValueError,'oracle proof differs'):self.check()
    def test_preloaded_verifier_rejected(self):
        with mock.patch.dict(sys.modules,{'_som_m6_react_recorded_before_verifier':object()}):
            with self.assertRaisesRegex(ValueError,'preloaded'):self.check()
    def test_actual_coordinator_verifier_source_loads(self):
        raw=(Path(os.environ['GITHUB_WORKSPACE'])/'.github/m6-react-owner-fixtures/recorded_before_oracle.py').read_bytes()
        module=type(sys)('_synthetic_oracle_source_load');module.__file__=str(self.run.parent/s.ORACLE)
        exec(compile(raw,module.__file__,'exec',dont_inherit=True),module.__dict__)
        self.assertTrue(callable(module.verify_recorded_run))
