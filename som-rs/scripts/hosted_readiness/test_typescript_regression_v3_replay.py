"""GitHub-only source-command, actual assertion failure and owned-state fixtures."""
from __future__ import annotations
import argparse
import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock
import urllib.parse

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub fixtures only')
HERE=Path(__file__).absolute().parent
REPO=HERE.parents[2]


def load(name):
    path=HERE/(name+'.py');module=ModuleType('_som_ts_v3_replay_'+name)
    module.__file__,module.__package__,module.__cached__=str(path),'',None
    exec(compile(path.read_bytes(),str(path),'exec',dont_inherit=True),module.__dict__)
    return module


def original_function(relative,name,namespace):
    path=REPO/relative
    selected=[node for node in ast.parse(path.read_bytes()).body if isinstance(node,ast.FunctionDef) and node.name==name]
    if len(selected)!=1:raise AssertionError('one original source function required')
    body=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*selected],type_ignores=[])
    ast.fix_missing_locations(body);exec(compile(body,str(path),'exec',dont_inherit=True),namespace)
    return namespace[name]


class SourceCommandTests(unittest.TestCase):
    def setUp(self):
        self.command=load('typescript_regression_product_commands')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        for name in ('src/main.ts','src/app.ts','test/api.test.ts','extra.ts','node_modules/ignore.ts'):
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixed source')
        self.expected=self.command.source_command_vectors((REPO/self.command.CHECKER).read_bytes(),self.root,
            '/qualified/node','/qualified/npm-cli.js')

    def test_exact_vectors_come_from_all_original_source_assignments(self):
        self.assertEqual(self.expected['install'],['/qualified/node','/qualified/npm-cli.js','ci','--offline',
            '--ignore-scripts','--no-audit','--no-fund'])
        self.assertEqual(self.expected['typecheck'],['/qualified/node','node_modules/typescript/bin/tsc','-p','tsconfig.json'])
        self.assertEqual(self.expected['test'],['/qualified/node','--test','--test-concurrency=1','test/api.test.ts'])
        self.assertEqual(self.expected['startup'],['/qualified/node','src/main.ts'])
        self.assertEqual(self.expected['format_check'],['/qualified/node','node_modules/prettier/bin/prettier.cjs',
            '--check','--no-config','--no-editorconfig','src/main.ts','src/app.ts','test/api.test.ts','extra.ts'])
        self.assertEqual(self.expected['schema_equivalence'][:3],['/qualified/node','--input-type=module','-e'])

    def test_consistently_changed_gate_and_raw_bin_true_refuse(self):
        gate={'command':['/bin/true']};rows=[{'cwd':str(self.root),'argv':['/bin/true']}]
        with self.assertRaisesRegex(ValueError,'independent original source command'):
            self.command.command_records(rows,str(self.root),'typecheck',gate,self.expected)

    def test_missing_original_required_argument_refuses(self):
        changed=self.expected['typecheck'][:-2];gate={'command':changed}
        with self.assertRaisesRegex(ValueError,'independent original source command'):
            self.command.command_records([{'cwd':str(self.root),'argv':changed}],str(self.root),'typecheck',gate,self.expected)

    def test_wrong_qualified_actual_executable_refuses(self):
        row={'cwd':str(self.root),'argv':['/other/node',*self.expected['typecheck'][1:]]}
        with self.assertRaisesRegex(ValueError,'actual command count/argv'):
            self.command.command_records([row],str(self.root),'typecheck',{'command':self.expected['typecheck']},self.expected)

    def test_exact_source_vector_and_actual_row_join(self):
        row={'cwd':str(self.root),'argv':self.expected['typecheck']}
        self.assertEqual(self.command.command_records([row],str(self.root),'typecheck',
            {'command':self.expected['typecheck']},self.expected),[row])


class OwnedReplayStateTests(unittest.TestCase):
    def setUp(self):
        self.checker=load('typescript_regression_checker');self.proof=load('typescript_regression_product_proof')
        self.path_before=list(sys.path);self.addCleanup(lambda:sys.path.__setitem__(slice(None),self.path_before))
        for name in ('som_behavior_base_suite','_som_typescript_product_proof_suite'):
            if name in sys.modules:self.fail('fresh isolated source fixture process required')
        self.ctx=SimpleNamespace(repo=REPO,sources=SimpleNamespace(read=lambda path:(REPO/path).read_bytes()))

    def descriptor(self):
        before={'backend':{'sha256':'b'*64},'target_contract':{'sha256':'c'*64},'formatter':{'sha256':'d'*64}}
        target=SimpleNamespace(compute_expected_ts_specific_descriptors=lambda:json.loads(json.dumps(before)))
        original=target.compute_expected_ts_specific_descriptors
        expected={'backend':'b'*64,'target_contract':'e'*64,'formatter':'f'*64}
        ctx=SimpleNamespace(qa_source_authority={'admitted_repository_python_sources':[self.checker.DIGESTS]},
            module=lambda *args:SimpleNamespace(authorities=lambda *args:(None,None,None,expected)))
        return ctx,target,original

    def test_actual_descriptor_adapter_success_restores_exact_original(self):
        ctx,target,original=self.descriptor();self.checker.native_bundle_expectations(ctx,target)
        self.assertEqual(target.compute_expected_ts_specific_descriptors()['formatter']['sha256'],'f'*64)
        self.checker.close_native_bundle_expectations(target)
        self.assertIs(target.compute_expected_ts_specific_descriptors,original)

    def test_actual_descriptor_adapter_failure_still_restores_exact_original(self):
        ctx,target,original=self.descriptor();first=RuntimeError('original bundle assertion')
        with self.assertRaises(RuntimeError) as caught:
            try:
                self.checker.native_bundle_expectations(ctx,target);raise first
            finally:self.checker.close_native_bundle_expectations(target)
        self.assertIs(caught.exception,first);self.assertIs(target.compute_expected_ts_specific_descriptors,original)

    def test_descriptor_cleanup_preserves_a_later_owner(self):
        ctx,target,original=self.descriptor();self.checker.native_bundle_expectations(ctx,target)
        replacement=lambda:{'new':'owner'};target.compute_expected_ts_specific_descriptors=replacement
        with self.assertRaisesRegex(ValueError,'later owner'):self.checker.close_native_bundle_expectations(target)
        self.assertIs(target.compute_expected_ts_specific_descriptors,replacement)

    def test_actual_suite_load_and_close_restore_only_its_added_path(self):
        expected=str(REPO/'som-rs/behavior');sys.path[:]=[value for value in sys.path if value!=expected]
        before=list(sys.path);source=self.proof.SourceModules(self.ctx)
        try:source.suite();self.assertIn(expected,sys.path)
        finally:source.close()
        self.assertEqual(sys.path,before)

    def test_actual_suite_failure_and_close_restore_path(self):
        expected=str(REPO/'som-rs/behavior');sys.path[:]=[value for value in sys.path if value!=expected]
        before=list(sys.path)
        def read(path):
            raw=(REPO/path).read_bytes()
            return raw+b'\nraise RuntimeError("injected after actual suite body")\n' if path==self.proof.SUITE else raw
        source=self.proof.SourceModules(SimpleNamespace(repo=REPO,sources=SimpleNamespace(read=read)))
        try:
            with self.assertRaisesRegex(RuntimeError,'after actual suite body'):source.suite()
        finally:source.close()
        self.assertEqual(sys.path,before)

    def test_preexisting_suite_path_and_later_unrelated_entry_are_preserved(self):
        expected=str(REPO/'som-rs/behavior');sys.path.insert(0,expected)
        source=self.proof.SourceModules(self.ctx);later='fixture-later-owner-search-path'
        try:source.suite();sys.path.append(later)
        finally:source.close()
        self.assertIs(sys.path[0],expected);self.assertIn(later,sys.path)

    def test_suite_close_refuses_foreign_path_replacement_without_clobbering_it(self):
        expected=str(REPO/'som-rs/behavior');sys.path[:]=[value for value in sys.path if value!=expected]
        source=self.proof.SourceModules(self.ctx);source.suite();owned=source.owned_path_entries[0][1]
        replacement=expected.encode().decode();self.assertIsNot(replacement,owned)
        sys.path[next(i for i,value in enumerate(sys.path) if value is owned)]=replacement
        with self.assertRaisesRegex(ValueError,'module/path ownership'):source.close()
        self.assertTrue(any(value is replacement for value in sys.path))
        self.assertNotIn('som_behavior_base_suite',sys.modules)


class OriginalFailureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()

    def test_actual_nested_cluster_popen_failure_keeps_original_without_inventing_wait(self):
        nested=load('typescript_regression_nested_rust');first=OSError('actual Popen refused before child assignment')
        pg=self.root/'pg';pg.mkdir();target=SimpleNamespace()
        stamps=SimpleNamespace(port_listeners=lambda port:[],process_stamp=lambda pid:self.fail('no child exists'))
        hosts=SimpleNamespace(Ports=lambda:SimpleNamespace(take=lambda:34567))
        ctx=SimpleNamespace(qa_mode='native-qa-v1',qa_source_authority={'admitted_repository_python_sources':
            [nested.SELF,nested.WRAPPER,nested.RUST,nested.HOST,nested.STAMP]},work=self.root,
            verify_postgres=lambda:pg,child_environment=lambda:{},command=lambda *args,**kwargs:{'exit_code':0},
            module=lambda key,path:stamps if path==nested.STAMP else hosts)
        psycopg=ModuleType('psycopg');psycopg.OperationalError=RuntimeError
        with mock.patch.dict(sys.modules,{'psycopg':psycopg}),mock.patch.object(nested.subprocess,'Popen',side_effect=first):
            nested.install(ctx,target,self.root/'capture')
            with self.assertRaises(OSError) as caught:target.cluster_gates(['--no-gates'])
        self.assertIs(caught.exception,first)
        record=json.loads((self.root/'typescript-regression/nested-rust-cluster/pg-cleanup.json').read_bytes())
        self.assertFalse(record['child_started']);self.assertFalse(record['wait_attempted'])
        self.assertFalse(record['wait_completed']);self.assertIn('Popen',record['primary_failure']['traceback'])

    def test_actual_schema_query_error_survives_evidence_writer_refusal(self):
        schema=load('typescript_regression_schema_capture');first=RuntimeError('original SQL query refused');later=OSError('schema writer refused')
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def execute(self,*args):raise first
        rust=SimpleNamespace(psycopg=SimpleNamespace(connect=lambda url:Connection()))
        def original(url,excluded):
            with rust.psycopg.connect(url) as connection:return list(connection.execute('SELECT actual query'))
        rust.schema=original
        checker=SimpleNamespace(rust_products=rust,__som_native_process_roles__={123:'postgres'},
            __som_native_verify_process__=lambda pid:{'pid':pid})
        rows=schema.install(checker,self.root/'schema');oldopen=Path.open
        def opened(path,*args,**kwargs):
            if path.name=='result.json':raise later
            return oldopen(path,*args,**kwargs)
        with mock.patch.object(Path,'open',new=opened):
            with self.assertRaises(RuntimeError) as caught:rust.schema('postgresql://postgres@127.0.0.1:34567/db',())
        self.assertIs(caught.exception,first);self.assertFalse(rows[0]['complete'])
        self.assertIn('execute',first.__som_typescript_failure_record__['traceback'])
        self.assertEqual(first.__som_typescript_secondary_failures__[0]['reason'],str(later))

    def test_actual_original_cli_main_error_survives_replay_stream_write_failure(self):
        replay=load('typescript_regression_cli_rust_proof');first=RuntimeError('actual original CLI check failure');later=OSError('CLI stream refused')
        cases=self.root/'cases';cases.mkdir();(cases/'index.json').write_text(json.dumps({'cases':[{'id':'fixture','cnl':'case.cnl'}]}));(cases/'case.cnl').write_text('actual input')
        def check(*args,**kwargs):raise first
        module=SimpleNamespace(check=check,_run_cli_with_request_capture=lambda *args:None,post=lambda *args:None,
            subprocess=SimpleNamespace(),NO_PROXY_OPENER=object(),tempfile=tempfile)
        namespace={'argparse':argparse,'pathlib':__import__('pathlib'),'urllib':__import__('urllib.parse'),
            'json':json,'tempfile':tempfile,'CASES':cases,'FORBIDDEN_PORTS':{8765,18765},'check':check}
        actual=original_function(replay.CLI,'main',namespace)
        def main(argv):
            namespace.update({name:getattr(module,name) for name in ('check','tempfile')});return actual(argv)
        module.main=main
        directory=self.root/'capture/raw-checkers/03';directory.mkdir(parents=True);(directory/'checker-postcheck.json').write_text('{"cli_rows":[]}')
        out=self.root/'audit';out.mkdir();capture={'services':{'current':{'candidate_dir':str(self.root/'candidate')}}}
        ctx=SimpleNamespace(module=lambda key,path:module if path==replay.CLI else
            SimpleNamespace(Tape=lambda root:SimpleNamespace()) if path==replay.HTTP else SimpleNamespace(commands=lambda root:[]))
        write=Path.write_bytes
        def refused(path,raw):
            if path.name=='cli-replay.stdout.raw':raise later
            return write(path,raw)
        with mock.patch.object(Path,'write_bytes',new=refused):
            with self.assertRaises(RuntimeError) as caught:replay.cli(ctx,self.root,capture,out)
        self.assertIs(caught.exception,first)
        self.assertIn('check_cli.py',first.__som_typescript_failure_record__['traceback'])
        self.assertEqual(first.__som_typescript_secondary_failures__[-1]['reason'],str(later))
        self.assertTrue(any(path.name.startswith('som-cli-') for path in out.iterdir()))

    def test_actual_original_main_error_remains_primary_after_stream_write_failure(self):
        replay=load('typescript_regression_main_replay');first=RuntimeError('original validate main POST failure');later=OSError('main stdout refused')
        path='som-rs/tests/rust-output-v1/validate_cases.py';raw=(REPO/path).read_bytes()
        capture_root=self.root/'capture-root';capture_root.mkdir();cases=capture_root/'cases';cases.mkdir()
        for name,body in {'index.json':json.dumps({'cases':[{'id':'fixture','prompt':'prompt.json','cnl':'case.cnl'}]}).encode(),
                          'prompt.json':b'{}','case.cnl':b'actual CNL'}.items():(cases/name).write_bytes(body)
        def post(*args):raise first
        namespace={'argparse':argparse,'json':json,'CASES':cases,'post':post}
        module=SimpleNamespace(main=original_function(path,'main',namespace))
        needed={replay.SELF,replay.OWNER,replay.COLLECTOR,replay.CHECKER,replay.DIGESTS,replay.INPUTS,replay.RAW_JSON,path}
        digest=SimpleNamespace(authorities=lambda *args:None)
        ctx=SimpleNamespace(repo=REPO,helper_revision='a'*40,candidate_revision='a'*40,qa_mode='native-qa-v1',cache={},
            qa_source_authority={'source_authority_active':True,'admitted_repository_python_sources':list(needed)},
            sources=SimpleNamespace(read=lambda value:raw if value==path else b'fixture'),
            data={'typescript_regression_inputs':{'roots':{},'siblings':{}}})
        ctx.module=lambda key,value:module if value==path else digest if value==replay.DIGESTS else SimpleNamespace()
        run=self.root/'original';step={'name':'validate-cases','argv':['python',str(REPO/path),'--base-url',replay.CURRENT,'--out',str(run/'validate-cases.json')]}
        capture={'helper_revision':'a'*40,'candidate_revision':'a'*40,'original_run':str(run),'input_roots':{},
            'rows':[{'step':'validate-cases','script':path,'logical_argv':[str(value) for value in step['argv']]}],
            'saved_files':{file.relative_to(capture_root).as_posix():{'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
                'size':file.stat().st_size,'mode':file.stat().st_mode&0o777} for file in cases.iterdir()}}
        out=self.root/'audit';write=Path.write_bytes
        def refused(member,body):
            if member.name=='validate-cases.stdout.raw':raise later
            return write(member,body)
        with mock.patch.dict(os.environ,{'RUNNER_TEMP':str(self.root)}),mock.patch.object(Path,'write_bytes',new=refused), \
             mock.patch.object(replay,'SPECS',(('validate-cases',1,path,'validate-cases.json',False),)), \
             mock.patch.object(replay,'source_snapshot',return_value={path:raw}), \
             mock.patch.object(replay,'arguments',return_value={'validate-cases':step}), \
             mock.patch.object(replay,'HttpTape',return_value=SimpleNamespace()):
            with self.assertRaises(RuntimeError) as caught:replay.verify(ctx,capture_root,capture,out=out)
        self.assertIs(caught.exception,first)
        report=json.loads((out/'first-failure.json').read_bytes())
        self.assertEqual(report['reason'],str(first));self.assertIn('validate_cases.py',report['traceback'])
        self.assertTrue(any(row['reason']==str(later) for row in first.__som_typescript_secondary_failures__))


class SupplementalObligationTests(unittest.TestCase):
    def test_native_formatter_replay_does_not_clear_rust_or_ts_strict_live_rows(self):
        native=load('typescript_native_digests');raw=load('raw_digest_compat');domain=load('typescript_regression_domain')
        def value_sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        for target in ('rust','typescript'):
            before={'backend':'1'*64,'target_contract':'2'*64,'formatter':'3'*64};after={**before,'formatter':'4'*64}
            ids={target+'_'+name:'fixture.'+name for name in before}
            factory=SimpleNamespace(IDS=ids,value_sha=value_sha)
            native.authorities=lambda ctx,name:(factory,raw,before,after)
            def document(pins):
                bundle={'target':target,'prompt':{'version':'som.prompt.v6'},'dsl':{},
                    'assets':{name:{'id':ids[target+'_'+name],'sha256':pin} for name,pin in pins.items()}}
                bundle['hashes']={'prompt_sha256':value_sha(bundle['prompt']),'dsl_sha256':value_sha(bundle['dsl']),
                                  'payload_sha256':value_sha(bundle)}
                return json.dumps({'status':'ok','bundle':bundle},separators=(',',':')).encode()
            native.compare(object(),target,document(before),document(after))
            for role in ('recorded-before-supplemental','live-before-profile'):
                rows={row['id']:row for row in domain.required_unrun_obligations(role)}
                self.assertEqual(rows['ts.amendment002.live_before']['status'],'unrun')
                self.assertEqual(rows['rust.amendment006.live_before']['status'],'unrun')

    def test_native_python_backend_replay_does_not_clear_strict_whole_response_row(self):
        native=load('typescript_native_digests');raw=load('raw_digest_compat');domain=load('typescript_regression_domain')
        def value_sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        before={'backend':'1'*64,'formatted_formatter':'2'*64,'render_only_formatter':'3'*64};after={**before,'backend':'4'*64}
        ids={'python_'+name:'fixture.'+name for name in before};factory=SimpleNamespace(IDS=ids,value_sha=value_sha)
        native.authorities=lambda ctx,name:(factory,raw,before,after)
        def document(pins):
            bundle={'target':'python','prompt':{'version':'som.prompt.v6'},'dsl':{},'assets':{
                'backend':{'id':ids['python_backend'],'sha256':pins['backend']},
                'formatter':{'id':ids['python_formatted_formatter'],'sha256':pins['formatted_formatter']}}}
            bundle['hashes']={'prompt_sha256':value_sha(bundle['prompt']),'dsl_sha256':value_sha(bundle['dsl']),
                              'payload_sha256':value_sha(bundle)}
            return json.dumps({'status':'ok','bundle':bundle},separators=(',',':')).encode()
        native.compare(object(),'python',document(before),document(after))
        for role in ('recorded-before-supplemental','live-before-profile'):
            rows={row['id']:row for row in domain.required_unrun_obligations(role)}
            self.assertEqual(rows['ts.python_unchanged.strict_live_before']['status'],'unrun')

    def test_actual_recipe_main_keeps_first_assertion_and_continues_all125_before_writer_failure(self):
        recipe=load('typescript_regression_rebase');first=RuntimeError('first original refusal assertion');later=OSError('recipe result refused')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();out=root/'out';calls=[];saved=[]
            ctx=SimpleNamespace(qa_mode='native-qa-v1',helper_revision='a'*40,candidate_revision='a'*40,work=root,verify=lambda:None)
            hosted=ModuleType('full_product_context');hosted.context=lambda:ctx
            cases=[{'id':str(index),'group':'fixture'} for index in range(125)]
            def check(ctx,checker,item,capture):
                calls.append(item['id'])
                if item['id']=='0':raise first
                return {'id':item['id'],'passed':True}
            def save(path,report):saved.append(report);raise later
            with mock.patch.dict(sys.modules,{'full_product_context':hosted}), \
                 mock.patch.object(sys,'argv',['recipe','--legacy-run',str(root/'legacy'),'--out',str(out)]), \
                 mock.patch.object(recipe,'cases',return_value=(object(),cases)), \
                 mock.patch.object(recipe,'check_one',side_effect=check),mock.patch.object(recipe,'save',side_effect=save), \
                 mock.patch.object(recipe,'verify_capture',side_effect=AssertionError('failed rows cannot pass')):
                with self.assertRaises(RuntimeError) as caught:recipe.main()
            self.assertIs(caught.exception,first);self.assertEqual(len(calls),125)
            self.assertEqual(saved[0]['primary_failure']['reason'],str(first))
            self.assertIn('check',saved[0]['primary_failure']['traceback'])
            self.assertEqual(first.__som_typescript_secondary_failures__[-1]['reason'],str(later))


if __name__=='__main__':unittest.main()
