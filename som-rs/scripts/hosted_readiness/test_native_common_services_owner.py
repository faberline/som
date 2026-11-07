"""GitHub-only ordinary Services class ownership protocol.

Real Core and the complete proposed self-contained Services source execute.
The entry retains actual selected native-loader helper ASTs. Its Source/Git,
service driver, Context/runtime/admission and callback are explicit doubles.
No real server, listener, native Context or product gate is qualified.
"""
from __future__ import annotations
import ast,copy,hashlib,os
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest import mock
if os.environ.get('GITHUB_ACTIONS')!='true':raise RuntimeError('GitHub tests only')
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
path=Path(__file__).absolute().with_name('test_native_active_controls.py')
protocol=ModuleType('_som_common_services_core_protocol');protocol.__file__=str(path)
protocol.__cached__,protocol.__package__=None,''
exec(compile(path.read_bytes(),str(path),'exec',dont_inherit=True),protocol.__dict__)
BASE='som-rs/scripts/hosted_readiness/'
COMMON=BASE+'run_current_candidate_gate.py'
SERVICES=BASE+'current_gate_services.py'
CALLBACK='som-rs/scripts/react_output_acceptance/m6_react_callback.py'
ENTRY='entry.current-gate'
ENTRY_DOUBLE='from pathlib import Path\nfrom types import ModuleType\nimport hashlib,sys\nBASE=\'som-rs/scripts/hosted_readiness/\'\nSERVICES_SOURCE=BASE+\'current_gate_services.py\'\nclass Sources:\n    # Explicit Source/Git constructor double. No real admission is granted.\n    def __init__(self,root,revision):self.root,self.revision=root,revision\n    def read(self,path):return (self.root/path).read_bytes()\nclass Driver:\n    # No socket or service process is opened by this protocol driver.\n    def listeners(self,port):return []\n\ndef require(value, message):\n    if not value:\n        raise ValueError(message)\n\ndef sha(raw):\n    return hashlib.sha256(raw).hexdigest()\n\ndef _native_controls(sources=None):\n    from _som_native_active_controls_binding import controls\n    controls.verify_installed()\n    if sources is not None:\n        require(sources.root == controls.root and sources.revision == controls.helper_revision\n                == controls.candidate_revision, \'active shared controller source identity differs\')\n    return controls\n\ndef _native_service_class(sources):\n    """Load the completed ordinary source owner before using its actual class."""\n    require(type(sources) is Sources, \'actual current-gate source owner required\')\n    controls = _native_controls(sources)\n    raw = sources.read(SERVICES_SOURCE)\n    require(controls.role_sources.get(\'common.services\') == SERVICES_SOURCE,\n            \'fixed ordinary current-gate services source differs\')\n    module = controls.load_module(\'common.services\')\n    require(type(module) is ModuleType and module.__name__ == \'_som_current_gate_services\'\n            and sys.modules.get(module.__name__) is module\n            and module.__file__ == str(sources.root / SERVICES_SOURCE) and module.__cached__ is None\n            and module.__som_raw_sha256__ == sha(raw), \'actual ordinary services source/module differs\')\n    cls = module.Services\n    require(type(cls) is type and type.__getattribute__(cls, \'__module__\') == module.__name__,\n            \'actual ordinary source-owned Services class required\')\n    controls.verify_installed()\n    return cls\n\ndef main():\n    controls=_native_controls()\n    sources=Sources(controls.root,controls.helper_revision)\n    cls=_native_service_class(sources)\n    ordinary=sys.modules[\'_som_current_gate_services\']\n    assert cls is ordinary.Services\n    callback=controls.load_module(\'common.runtime.owner.m6-react003\')\n    manager=cls(None,Driver(),None,None,None,None,Path(sys.argv[1]))\n    try:\n        assert callback.accept(manager) is True\n        # Callback fixed-export class is available before this entry completes.\n        controls.verify_installed()\n    finally:\n        manager.close_all()\n    callback.LAST=manager\n    mro=controls.load_module(\'fixture.mro-consumer\')\n    assert mro.Refusal.__bases__[0] is sys.modules[\'_som_services_mro_provider\'].OriginalBase\n    controls.verify_installed()\n    return 0\nif __name__==\'__main__\':raise SystemExit(main())\n'
CALLBACK_DOUBLE="""LAST=None
def accept(manager):return type(manager) is __som_native_Services__
"""
MRO_SOURCE="""class OriginalBase(ValueError):
    pass
class AlternateBase(ValueError):
    pass
class Refusal(OriginalBase):
    pass
"""
class Transfer(protocol.ProtocolTransfer):
    def verify_entry_source(self,path):self.fixture.case.assertEqual(path,COMMON)
class ServiceFixture(protocol.Fixture):
    def __init__(self,case):
        with mock.patch.object(protocol,'ProtocolTransfer',Transfer):super().__init__(case)
        self.trusted=self.seeds['_som_native_trusted_bootstrap_entry']['module']._trusted_run
        self.output=self.root/'services-output'
    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role!='m6.api.producer':return row
        super().add_role(ENTRY,COMMON,ENTRY_DOUBLE,kind='entry',module='__main__',
            bindings={'__som_native_active_controls__':{'kind':'active-controls'}},
            callers=[{'role':'bootstrap.entry','function':'_trusted_run'}])
        super().add_role('common.services',SERVICES,(protocol.REPO/SERVICES).read_bytes(),
            module='_som_current_gate_services',callers=[{'role':ENTRY,'function':'_native_service_class'}])
        super().add_role('common.runtime.owner.m6-react003',CALLBACK,CALLBACK_DOUBLE,
            module='_som_full_current_gate_owner_m6_react003',
            bindings=copy.deepcopy({'__som_native_Services__':{'kind':'role-export','role':'common.services','attribute':'Services'}}),
            callers=[{'role':ENTRY,'function':'main'}])
        super().add_role('fixture.mro-provider',BASE+'synthetic_services_mro_provider.py',MRO_SOURCE,
            module='_som_services_mro_provider')
        super().add_role('fixture.mro-consumer',BASE+'synthetic_services_mro_consumer.py','VALUE=1\n',
            module='_som_services_mro_consumer',bindings={'Refusal':{'kind':'role-export','role':'fixture.mro-provider','attribute':'Refusal'}},
            callers=[{'role':ENTRY,'function':'main'}])
        return row
    def execute(self):return self.trusted(self.controls,ENTRY,[str(self.output)])

class OrdinaryServicesOwnerTests(unittest.TestCase):
    def test_actual_entry_return_then_verify_and_owner_close_keep_complete_class_owner(self):
        f=ServiceFixture(self);self.assertEqual(f.execute(),0)
        self.assertNotIn(ENTRY,f.domain.records)
        record=f.domain.records['common.services'];module=record.module
        self.assertEqual(record.fixed_bindings,{})
        callback=f.domain.records['common.runtime.owner.m6-react003'].module
        self.assertIs(callback.__som_native_Services__,module.Services)
        self.assertIs(type(callback.LAST),module.Services)
        self.assertTrue(callback.LAST.closed);self.assertEqual(callback.LAST.receipt['errors'],[])
        self.assertIn(('Services',),record.fixed_export_classes)
        for name in ('save','read','verify_recorded_python_startup','recorded_python_environment'):
            function=getattr(module,name);self.assertIs(function.__globals__,module.__dict__)
            self.assertTrue(any(function.__code__ is code for code in record.codes.values()))
        f.controls.verify_installed()
        f.finish()
        self.assertNotIn('_som_current_gate_services',sys.modules)
        self.assertNotIn('_som_full_current_gate_owner_m6_react003',sys.modules)

    def test_fixed_actual_Services_export_replacement_refuses_before_foreign_hook(self):
        f=ServiceFixture(self);f.execute();module=f.domain.records['common.services'].module
        actual=module.Services;calls=[]
        class Foreign:
            def __new__(cls,*args):calls.append(True);return super().__new__(cls)
        module.Services=Foreign
        try:
            with self.assertRaisesRegex(ValueError,'fixed exported class identity changed'):f.controls.verify_installed()
            self.assertEqual(calls,[])
        finally:module.Services=actual
        f.controls.verify_installed()

    def test_actual_ordinary_Services_method_mutation_refuses_before_method_hook(self):
        f=ServiceFixture(self);f.execute();cls=f.domain.records['common.services'].module.Services
        actual=cls.start_current;calls=[]
        def foreign(*args):calls.append(True);raise AssertionError('foreign service method ran')
        cls.start_current=foreign
        try:
            with self.assertRaisesRegex(ValueError,'fixed exported class namespace changed'):f.controls.verify_installed()
            self.assertEqual(calls,[])
        finally:cls.start_current=actual
        f.controls.verify_installed()

    def test_actual_ordinary_Services_added_constructor_hook_refuses_before_hook(self):
        f=ServiceFixture(self);f.execute();cls=f.domain.records['common.services'].module.Services;calls=[]
        def foreign(owner,*args):calls.append(True);return object.__new__(owner)
        cls.__new__=foreign
        try:
            with self.assertRaisesRegex(ValueError,'fixed exported class namespace changed'):f.controls.verify_installed()
            self.assertEqual(calls,[])
        finally:del cls.__new__
        f.controls.verify_installed()

    def test_ordinary_fixed_class_MRO_change_after_entry_removal_refuses(self):
        f=ServiceFixture(self);f.execute();module=f.domain.records['fixture.mro-provider'].module
        original=module.Refusal.__bases__;module.Refusal.__bases__=(module.AlternateBase,)
        try:
            with self.assertRaisesRegex(ValueError,'fixed exported class MRO changed'):f.controls.verify_installed()
        finally:module.Refusal.__bases__=original
        f.controls.verify_installed()

    def test_complete_original_Services_and_all_selected_helper_ASTs_stay_exact(self):
        original=ast.parse((protocol.REPO/COMMON).read_bytes());ordinary=ast.parse((protocol.REPO/SERVICES).read_bytes())
        by_name={}
        for node in original.body:
            if isinstance(node,(ast.FunctionDef,ast.ClassDef)):by_name[node.name]=node
            elif isinstance(node,ast.Assign):
                for target in node.targets:
                    if isinstance(target,ast.Name):by_name[target.id]=node
        names=[]
        for node in ordinary.body:
            if isinstance(node,(ast.FunctionDef,ast.ClassDef)):name=node.name
            elif isinstance(node,ast.Assign):name=node.targets[0].id
            else:continue
            names.append(name)
            self.assertEqual(ast.dump(node,include_attributes=False),ast.dump(by_name[name],include_attributes=False))
        self.assertIn('Services',names);self.assertEqual(len(names),35)
        self.assertNotIn('main',names);self.assertNotIn('admitted_sources',names)

if __name__=='__main__':unittest.main()
