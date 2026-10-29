"""GitHub-only actual-Core schema capture row-state protocol.

Real Core, actual successor schema and completed ordinary RawCapture data
classes run. Source/admission/Context/runtime/process identity and SQL are
explicit source/data doubles. No real PostgreSQL process or product passes.
"""
from __future__ import annotations
import builtins,json,os
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest import mock
if os.environ.get('GITHUB_ACTIONS')!='true':raise RuntimeError('GitHub tests only')
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
path=Path(__file__).absolute().with_name('test_native_active_controls.py')
protocol=ModuleType('_som_ts_schema_rows_core_protocol');protocol.__file__=str(path)
protocol.__package__,protocol.__cached__='',None
exec(compile(path.read_bytes(),str(path),'exec',dont_inherit=True),protocol.__dict__)
BASE='som-rs/scripts/hosted_readiness/'
SCHEMA=BASE+'typescript_regression_schema_capture.py'
RAW=BASE+'typescript_regression_raw_capture.py'
TARGET='som-rs/som-targets/qa/check_rust_products.py'
CALLER_SOURCE="""import sys
rust_products=None
__som_native_process_roles__={4321:'postgres'}
def __som_native_verify_process__(pid):
    from _som_native_active_controls_binding import controls
    assert pid==4321
    controls.verify_installed()
def run(controls,root):
    global rust_products
    rust_products=controls.load_module('fixture.rust-products')
    capture=controls.load_module('ctx.typescript-regression-schema-capture')
    original_schema,original_psycopg=rust_products.schema,rust_products.psycopg
    try:
        rows=capture.install(sys.modules[__name__],root)
        controls.verify_installed()
        result=rust_products.schema('postgresql://postgres@127.0.0.1:15432/fixture',('alembic_version',))
        controls.verify_installed()
        return rows,result
    finally:
        rust_products.schema,rust_products.psycopg=original_schema,original_psycopg
        controls.verify_installed()
"""
SQL_DOUBLE="""from _som_native_active_controls_binding import controls
import builtins
CALLS=[]
class Cursor:
    def fetchall(self):return [['field',7,True,None]]
class Connection:
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,params=None):
        CALLS.append([sql,params])
        controls.verify_installed()
        if getattr(builtins,'__som_schema_query_failure__',None) is not None:
            raise builtins.__som_schema_query_failure__
        return Cursor()
class Psycopg:
    def connect(self,url):return Connection()
psycopg=Psycopg()
def schema(url,excluded):
    controls.verify_installed()
    with psycopg.connect(url)as connection:
        rows=connection.execute('SELECT source_fixture',('argument',)).fetchall()
    controls.verify_installed()
    return {'tables':['fixture'],'rows':rows,'excluded':list(excluded)}
"""
class SchemaFixture(protocol.Fixture):
    def __init__(self,case):
        super().__init__(case,caller=CALLER_SOURCE)
        self.out=self.root/'capture'
    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role!='m6.api.producer':return row
        self.rows['caller']['bindings']={'__som_native_active_controls__':{'kind':'active-controls'}}
        super().add_role('ctx.typescript-regression-raw-capture',RAW,(protocol.REPO/RAW).read_bytes(),
            module='_som_full_typescript_regression_raw_capture',
            callers=[{'role':'ctx.typescript-regression-schema-capture','function':'_native_row_class'}])
        super().add_role('ctx.typescript-regression-schema-capture',SCHEMA,(protocol.REPO/SCHEMA).read_bytes(),
            module='_som_full_typescript_regression_schema_capture',
            bindings={'__som_native_active_controls__':{'kind':'active-controls'},
                '__som_native_CaptureRows__':{'kind':'role-export','role':'ctx.typescript-regression-raw-capture','attribute':'CaptureRows'}},
            callers=[{'role':'caller','function':'run'}])
        super().add_role('fixture.rust-products',TARGET,SQL_DOUBLE,module='_som_schema_original_SQL_double',
            bindings={'__som_native_active_controls__':{'kind':'active-controls'}},
            callers=[{'role':'caller','function':'run'}])
        return row
    def execute(self):return self.caller().run(self.controls,self.out)


def legacy_module():
    path=protocol.REPO/SCHEMA
    module=ModuleType('_som_schema_legacy_protocol');module.__file__=str(path)
    module.__package__,module.__cached__='',None
    exec(compile(path.read_bytes(),str(path),'exec',dont_inherit=True),module.__dict__)
    return module

class SchemaMutableCoreTests(unittest.TestCase):
    def test_actual_schema_active_and_rows_mutate_between_Core_checks_and_owned_restore(self):
        f=SchemaFixture(self);rows,result=f.execute()
        self.assertIs(type(rows),list);self.assertEqual(len(rows),1)
        row=rows[0];self.assertTrue(row['complete']);self.assertEqual(row['schema'],result)
        self.assertEqual(row['queries'][0]['sql'],'SELECT source_fixture')
        self.assertEqual(row['queries'][0]['params'],{'type':'tuple','value':[{'type':'str','value':'argument'}]})
        self.assertEqual(row['pg_owner_pid'],4321)
        self.assertEqual(json.loads((f.out/'000000/result.json').read_bytes()),row)
        provider=f.domain.records['ctx.typescript-regression-raw-capture']
        self.assertIn(('CaptureRows',),provider.fixed_export_classes)
        f.controls.verify_installed();f.finish()
        self.assertNotIn('_som_full_typescript_regression_schema_capture',sys.modules)

    def test_original_query_error_and_writer_failure_keep_first_object_and_traceback(self):
        f=SchemaFixture(self);first=RuntimeError('original query refused');later=OSError('schema writer refused');original=Path.open
        def opened(path,*args,**kwargs):
            if path==f.out/'000000/result.json':raise later
            return original(path,*args,**kwargs)
        with mock.patch.object(builtins,'__som_schema_query_failure__',first,create=True),mock.patch.object(Path,'open',new=opened):
            with self.assertRaises(RuntimeError)as caught:f.execute()
        self.assertIs(caught.exception,first)
        self.assertIn('execute',first.__som_typescript_failure_record__['traceback'])
        self.assertEqual(first.__som_typescript_secondary_failures__[-1]['role'],'schema_result_evidence')
        f.controls.verify_installed();f.finish()

    def test_writer_failure_after_original_query_success_is_not_passed(self):
        f=SchemaFixture(self);first=OSError('first schema writer failure');original=Path.open
        def opened(path,*args,**kwargs):
            if path==f.out/'000000/result.json':raise first
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'open',new=opened):
            with self.assertRaises(OSError)as caught:f.execute()
        self.assertIs(caught.exception,first)
        self.assertEqual(first.__som_typescript_failure_record__['role'],'schema_result_evidence')
        f.controls.verify_installed();f.finish()

    def test_removed_actual_native_binding_never_enters_legacy_fallback(self):
        f=SchemaFixture(self)
        module=f.domain.load_known('ctx.typescript-regression-schema-capture',f.domain.prepare('ctx.typescript-regression-schema-capture'),None)
        actual=module.__dict__.pop('__som_native_active_controls__')
        try:
            with self.assertRaisesRegex(ValueError,'fixed active-controls binding was replaced'):f.execute()
            self.assertFalse(f.out.exists())
        finally:module.__dict__['__som_native_active_controls__']=actual
        f.controls.verify_installed();f.finish()

    def test_normal_legacy_probe_falls_back_only_for_exact_missing_reserved_module(self):
        module=legacy_module();self.assertIsNone(module._native_row_class())
        original=module._native_row_class.__builtins__['__import__']
        other=ModuleNotFoundError('required different dependency',name='different_dependency')
        def imported(name,*args,**kwargs):
            if name=='_som_native_active_controls_binding':raise other
            return original(name,*args,**kwargs)
        # A fresh test-only builtins map preserves all normal imports.
        source=(protocol.REPO/SCHEMA).read_bytes();fresh=ModuleType('_som_schema_legacy_other_import');fresh.__file__=str(protocol.REPO/SCHEMA)
        fresh.__dict__['__builtins__']={**module._native_row_class.__builtins__,'__import__':imported}
        exec(compile(source,fresh.__file__,'exec',dont_inherit=True),fresh.__dict__)
        with self.assertRaises(ModuleNotFoundError)as caught:fresh._native_row_class()
        self.assertIs(caught.exception,other)

    def test_present_fake_reserved_module_without_native_injection_refuses_before_hook(self):
        module=legacy_module();calls=[]
        class Foreign:
            def verify_installed(self):calls.append(True);raise AssertionError('foreign control hook')
        foreign=ModuleType('_som_native_active_controls_binding');foreign.controls=Foreign()
        with mock.patch.dict(sys.modules,{'_som_native_active_controls_binding':foreign}):
            with self.assertRaisesRegex(ValueError,'actual fixed native schema control binding required'):module._native_row_class()
        self.assertEqual(calls,[])

if __name__=='__main__':unittest.main()
