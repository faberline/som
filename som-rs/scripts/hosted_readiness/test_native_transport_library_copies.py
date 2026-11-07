"""GitHub-only exact-copy transport import protocol fixtures.

Actual Core/static_edges and all six whole source modules execute on synthetic
physical roots. Authority transfer is an explicit double. No Context factory,
helper CLI, crypto, transport restore, service or product runs.
"""
from __future__ import annotations
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub tests only')
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
HERE=Path(__file__).absolute().parent
REPO=HERE.parents[2]
PINS={'.github/m6-ts-role-transport/bind_restored_inputs.py': {'bytes': 6573,
                                                          'sha256': 'e6cafae98632108067435f3dd0f50f46a944073a95dd19ec7d91c2c4b4e65639'},
 '.github/m6-ts-role-transport/materialize_roles.py': {'bytes': 6407,
                                                       'sha256': 'd749e4e9c3c867d8c994a7697364f47d3e59edbf3980b9d489e867b50006f230'},
 '.github/m6-ts-role-transport/node_role_transport.py': {'bytes': 8545,
                                                         'sha256': 'a302f81ca1b7d014da0734880dea15de913a4afc3ba7e843447a091bf15747ce'},
 '.github/m6-ts-role-transport/verify_restored_roles.py': {'bytes': 3038,
                                                           'sha256': 'dd4aa2468e76fac98734eff9e256955cdd11a33d30c5e1902e970dda4923ad87'},
 'som-rs/scripts/hosted_readiness/native_active_controls.py': {'bytes': 81177,
                                                               'sha256': 'eead8e03186f8f55812566a11d5aa837fb5cdb542d2c3de9435da9bad6bb5b32'},
 'som-rs/scripts/hosted_readiness/native_qa_authority.py': {'bytes': 31631,
                                                            'sha256': '024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1'},
 'som-rs/scripts/hosted_readiness/role_inventory.py': {'bytes': 1663,
                                                       'sha256': '1d03b9bfc1d02f7d25501bae560780d27634829dbdb34e30482b7e2a4ff10e53'},
 'som-rs/scripts/hosted_readiness/test_native_active_controls.py': {'bytes': 61533,
                                                                    'sha256': 'f76335be9269618bcec26750895202a896bbe759cfc6ea490d7b8dffddc99e0d'},
 'som-rs/scripts/hosted_readiness/verify_restored_roles.py': {'bytes': 3038,
                                                              'sha256': 'dd4aa2468e76fac98734eff9e256955cdd11a33d30c5e1902e970dda4923ad87'}}
RAW={}
revision=os.environ['HELPER_REVISION']
assert subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(REPO),'rev-parse','HEAD']).decode().strip()==revision
for relative,wanted in PINS.items():
    path=REPO/relative
    assert path.is_file() and path.stat().st_nlink==1 and not any(p.is_symlink() for p in (path,*path.parents))
    body=path.read_bytes()
    assert {'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}==wanted
    assert body==subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(REPO),'cat-file','blob',revision+':'+relative])
    RAW[relative]=body
protocol_path=HERE/'test_native_active_controls.py'
protocol=ModuleType('_som_transport_library_protocol_fixture')
protocol.__file__,protocol.__cached__,protocol.__package__=str(protocol_path),None,''
exec(compile(RAW['som-rs/scripts/hosted_readiness/test_native_active_controls.py'],str(protocol_path),'exec',dont_inherit=True),protocol.__dict__)
ROLES={'test.transport.bind-inputs': {'bindings': {},
                                'callers': [{'function': 'run', 'role': 'caller'}],
                                'group': None,
                                'is_package': False,
                                'kind': 'module',
                                'module': '_som_transport_definition_bind_inputs',
                                'package': '',
                                'source': '.github/m6-ts-role-transport/bind_restored_inputs.py'},
 'test.transport.materialize': {'bindings': {},
                                'callers': [{'function': 'run', 'role': 'caller'}],
                                'group': None,
                                'is_package': False,
                                'kind': 'module',
                                'module': '_som_transport_definition_materialize',
                                'package': '',
                                'source': '.github/m6-ts-role-transport/materialize_roles.py'},
 'test.transport.node-role': {'bindings': {},
                              'callers': [{'function': 'run', 'role': 'caller'}],
                              'group': None,
                              'is_package': False,
                              'kind': 'module',
                              'module': '_som_transport_definition_node_role',
                              'package': '',
                              'source': '.github/m6-ts-role-transport/node_role_transport.py'},
 'test.transport.original-verifier': {'bindings': {},
                                      'callers': [{'function': 'run', 'role': 'caller'}],
                                      'group': None,
                                      'is_package': False,
                                      'kind': 'module',
                                      'module': '_som_transport_definition_original_verifier',
                                      'package': '',
                                      'source': '.github/m6-ts-role-transport/verify_restored_roles.py'},
 'ts.transport.library.role-inventory': {'bindings': {},
                                         'callers': [{'function': 'run', 'role': 'caller'},
                                                     {'function': '<module>',
                                                      'role': 'ts.transport.library.verify-restored-roles'},
                                                     {'function': '<module>',
                                                      'role': 'test.transport.materialize'},
                                                     {'function': '<module>',
                                                      'role': 'test.transport.node-role'},
                                                     {'function': '<module>',
                                                      'role': 'test.transport.original-verifier'}],
                                         'group': None,
                                         'is_package': False,
                                         'kind': 'module',
                                         'module': '_som_ts_transport_library_role_inventory',
                                         'package': '',
                                         'source': 'som-rs/scripts/hosted_readiness/role_inventory.py'},
 'ts.transport.library.verify-restored-roles': {'bindings': {},
                                                'callers': [{'function': 'run', 'role': 'caller'},
                                                            {'function': '<module>',
                                                             'role': 'test.transport.bind-inputs'}],
                                                'group': None,
                                                'is_package': False,
                                                'kind': 'module',
                                                'module': '_som_ts_transport_library_verify_restored_roles',
                                                'package': '',
                                                'source': 'som-rs/scripts/hosted_readiness/verify_restored_roles.py'}}
IMPORTS=[{'caller': 'test.transport.materialize',
  'from_roles': {},
  'fromlist': ['inventory'],
  'level': 0,
  'name': 'role_inventory',
  'result': 'ts.transport.library.role-inventory'},
 {'caller': 'test.transport.node-role',
  'from_roles': {},
  'fromlist': ['inventory'],
  'level': 0,
  'name': 'role_inventory',
  'result': 'ts.transport.library.role-inventory'},
 {'caller': 'test.transport.original-verifier',
  'from_roles': {},
  'fromlist': ['inventory'],
  'level': 0,
  'name': 'role_inventory',
  'result': 'ts.transport.library.role-inventory'},
 {'caller': 'test.transport.bind-inputs',
  'from_roles': {},
  'fromlist': [],
  'level': 0,
  'name': 'verify_restored_roles',
  'result': 'ts.transport.library.verify-restored-roles'},
 {'caller': 'ts.transport.library.verify-restored-roles',
  'from_roles': {},
  'fromlist': ['inventory'],
  'level': 0,
  'name': 'role_inventory',
  'result': 'ts.transport.library.role-inventory'}]
EXPECTED_EDGES=[{'caller': '.github/m6-ts-role-transport/bind_restored_inputs.py',
  'import': 'verify_restored_roles',
  'source': 'som-rs/scripts/hosted_readiness/verify_restored_roles.py'},
 {'caller': '.github/m6-ts-role-transport/materialize_roles.py',
  'import': 'role_inventory',
  'source': 'som-rs/scripts/hosted_readiness/role_inventory.py'},
 {'caller': '.github/m6-ts-role-transport/node_role_transport.py',
  'import': 'role_inventory',
  'source': 'som-rs/scripts/hosted_readiness/role_inventory.py'},
 {'caller': '.github/m6-ts-role-transport/verify_restored_roles.py',
  'import': 'role_inventory',
  'source': 'som-rs/scripts/hosted_readiness/role_inventory.py'},
 {'caller': 'som-rs/scripts/hosted_readiness/verify_restored_roles.py',
  'import': 'role_inventory',
  'source': 'som-rs/scripts/hosted_readiness/role_inventory.py'}]
SEARCH_ROOTS=['som-rs/som-targets/qa',
 'som-rs/behavior',
 'som-rs/behavior/phase2',
 'som-rs/behavior/phase2/adapted',
 'som-rs/tests/rust-output-v1',
 'som-rs/tests/typescript-output-v1',
 'som-rs/tests/react-output-v1',
 'som-rs/scripts/compiler_acceptance',
 'som-rs/scripts/rust_output_acceptance',
 'som-rs/scripts/typescript_output_acceptance',
 'som-rs/scripts/react_output_acceptance',
 'som-core/scripts',
 'som-core/src',
 'som-rs/scripts/hosted_readiness',
 'som-rs/scripts/hosted_readiness/product_runtime',
 'som-rs/scripts/hosted_readiness/product_python']
INV='ts.transport.library.role-inventory'
VER='ts.transport.library.verify-restored-roles'


class TransportFixture(protocol.Fixture):
    def __init__(self,case):
        choices={'materialize':'test.transport.materialize','node':'test.transport.node-role',
                 'original-verifier':'test.transport.original-verifier','bind':'test.transport.bind-inputs',
                 'inventory':INV,'verifier':VER}
        caller='def run(controls,choice="materialize"):\n'
        for key,role in choices.items():
            caller+='    if choice=='+repr(key)+': return controls.load_module('+repr(role)+')\n'
        caller+='    raise ValueError("unknown fixed fixture choice")\n'
        super().__init__(case,caller)
        self.policy['imports']=copy.deepcopy(IMPORTS)
        self.edges=self.core.static_edges(self.raw.__getitem__,[r['source'] for r in ROLES.values()],
                                          [r['source'] for r in ROLES.values()],SEARCH_ROOTS)
        self.policy['static_edges']=self.edges

    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role!='m6.api.producer':return row
        for target,data in ROLES.items():
            super().add_role(target,data['source'],RAW[data['source']],module=data['module'],
                             callers=copy.deepcopy(data['callers']),bindings=copy.deepcopy(data['bindings']))
        return row


@contextmanager
def active(case):
    # Global bare-name shadows are foreign. Fixed imports must preserve them.
    inv_shadow,ver_shadow=ModuleType('role_inventory'),ModuleType('verify_restored_roles')
    with mock.patch.dict(sys.modules,{'role_inventory':inv_shadow,'verify_restored_roles':ver_shadow}):
        fixture=TransportFixture(case)
        try:
            yield fixture,inv_shadow,ver_shadow
        finally:
            try:fixture.finish()
            finally:fixture.stack.close()


class NativeTransportLibraryTests(unittest.TestCase):
    def test_actual_static_edges_use_unchanged16roots_and_truthful_canonical_paths(self):
        with active(self) as (f,_,__):
            self.assertEqual(len(SEARCH_ROOTS),16)
            self.assertEqual(f.core.sha(f.core.canonical(SEARCH_ROOTS)),
                '3621fc0bcd55eb0f8a9b216a79b5197c1146fd4ea1bbec8c00addeb795debf2c')
            self.assertFalse(any(root.startswith('.github') for root in SEARCH_ROOTS))
            self.assertEqual(f.edges,EXPECTED_EDGES)
            self.assertEqual(len(f.edges),5)
            self.assertTrue(all(edge['source'].startswith('som-rs/scripts/hosted_readiness/') for edge in f.edges))
            self.assertEqual(len([edge for edge in f.edges if edge['caller'].startswith('.github/')]),4)

    def test_real_Core_executes_all4_helpers_without_main_and_keeps_exact_function_identities(self):
        with active(self) as (f,inv_shadow,ver_shadow):
            modules={choice:f.caller().run(f.controls,choice) for choice in
                     ('materialize','node','original-verifier','bind','inventory','verifier')}
            inventory,verifier=modules['inventory'],modules['verifier']
            for role,data in ROLES.items():
                record=f.domain.records[role];module=record.module
                self.assertIs(type(module),ModuleType)
                self.assertIs(sys.modules[data['module']],module)
                self.assertEqual(module.__file__,str(f.root/data['source']))
                self.assertEqual(module.__som_raw_sha256__,PINS[data['source']]['sha256'])
                self.assertIsNone(module.__cached__)
                self.assertNotEqual(module.__name__,'__main__')
                if role.startswith('test.transport.'):
                    self.assertEqual(module.main.__code__.co_filename,str(f.root/data['source']))
                    self.assertIs(module.main.__globals__,record.namespace)
                    self.assertTrue(any(module.main.__code__ is code for code in record.codes.values()))
            for role,name in ((INV,'inventory'),(INV,'digest'),(VER,'verify')):
                record=f.domain.records[role];function=getattr(record.module,name)
                self.assertIs(function.__globals__,record.namespace)
                self.assertEqual(function.__code__.co_filename,str(f.root/ROLES[role]['source']))
                self.assertTrue(any(function.__code__ is code for code in record.codes.values()))
            self.assertIs(modules['materialize'].inventory,inventory.inventory)
            self.assertIs(modules['node'].inventory,inventory.inventory)
            self.assertIs(modules['original-verifier'].inventory,inventory.inventory)
            self.assertIs(verifier.inventory,inventory.inventory)
            self.assertIs(modules['bind'].verifier,verifier)
            self.assertIs(sys.modules['role_inventory'],inv_shadow)
            self.assertIs(sys.modules['verify_restored_roles'],ver_shadow)
            f.finish()
            self.assertTrue(f.domain.closed)
            self.assertTrue(all(data['module'] not in sys.modules for data in ROLES.values()))
            self.assertIs(sys.modules['role_inventory'],inv_shadow)

    def test_original_verifier_and_canonical_library_do_not_collapse_source_or_namespace(self):
        with active(self) as (f,_,__):
            old=f.caller().run(f.controls,'original-verifier')
            new=f.caller().run(f.controls,'verifier')
            self.assertIsNot(old,new)
            self.assertIsNot(old.verify,new.verify)
            self.assertIsNot(old.verify.__code__,new.verify.__code__)
            self.assertIsNot(old.verify.__globals__,new.verify.__globals__)
            self.assertEqual(RAW[ROLES['test.transport.original-verifier']['source']],RAW[ROLES[VER]['source']])
            self.assertNotEqual(old.__file__,new.__file__)
            self.assertIs(old.inventory,new.inventory)

    def test_actual_inventory_rechecks_regular_bytes_internal_alias_and_external_refusal(self):
        with active(self) as (f,_,__):
            module=f.caller().run(f.controls,'inventory')
            root=f.root/'small-role';root.mkdir()
            data=root/'payload';data.write_bytes(b'exact small inventory');data.chmod(0o644)
            (root/'alias').symlink_to('payload')
            rows=module.inventory(root)
            self.assertEqual(rows['payload'],{'sha256':hashlib.sha256(data.read_bytes()).hexdigest(),
                'size':len(data.read_bytes()),'mode':0o644})
            self.assertEqual(rows['alias']['type'],'internal-alias')
            self.assertEqual(rows['alias']['target'],'payload')
            self.assertEqual(rows['alias']['target_sha256'],rows['payload']['sha256'])
            outside=f.root/'outside';outside.write_bytes(b'outside')
            (root/'escape').symlink_to('../outside')
            with self.assertRaisesRegex(ValueError,'only internal file aliases permitted'):
                module.inventory(root)

    def test_wrong_fixed_import_role_refuses_before_any_library_body(self):
        with active(self) as (f,_,__):
            rule=next(row for row in f.policy['imports'] if row['caller']=='test.transport.materialize')
            rule['result']='test.transport.original-verifier'
            with self.assertRaisesRegex(ValueError,'fixed caller-to-target role is not admitted'):
                f.run()
            self.assertNotIn(INV,f.domain.records)
            self.assertNotIn(VER,f.domain.records)
            self.assertNotIn('test.transport.original-verifier',f.domain.records)

    def test_wrong_importer_origin_refuses_before_inventory_body(self):
        with active(self) as (f,_,__):
            f.rows[INV]['callers']=[{'role':'test.transport.materialize','function':'foreign_origin'}]
            with self.assertRaisesRegex(ValueError,'fixed caller-to-target role is not admitted'):
                f.run()
            self.assertNotIn(INV,f.domain.records)
            self.assertNotIn(ROLES[INV]['module'],sys.modules)


if __name__=='__main__':
    unittest.main()
