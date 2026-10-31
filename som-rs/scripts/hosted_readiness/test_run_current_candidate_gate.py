"""Integrity fixtures for GitHub only. No fixture starts a native service."""
from __future__ import annotations

import ast
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub fixtures only')
path = Path(__file__).with_name('run_current_candidate_gate.py')
p = ModuleType('_current_candidate_gate_fixture_subject')
p.__file__, p.__cached__ = str(path), None
exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), p.__dict__)


def full_identity(path):
    value, info = p.identity(path), Path(path).stat()
    return {**value, 'device': info.st_dev, 'inode': info.st_ino}


def full_tree(root):
    return {name: full_identity(Path(root) / name) for name in p.tree(root)}


class GateIntegrity(unittest.TestCase):
    def file(self, root, name, body=b'\x7fELFfixture', mode=0o755):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body); path.chmod(mode)
        return path

    def baseline(self, root):
        root.mkdir()
        self.file(root, 'som')
        self.file(root, 'assets/manifest.json', b'{}', 0o644)
        for name in ('ruff', 'rustfmt', 'node'): self.file(root, 'native/' + name)
        self.file(root, 'native/prettier/index.cjs', b'// immutable', 0o644)
        def file(name): return {'path': name, **p.identity(root / name)}
        def directory(name): return {'path': name, 'files': p.tree(root / name)}
        raw = b'{"status":"ok","model_loaded":false}'
        value = {'version': 'som.linux-qa-immutable-baseline.v1', 'role': 'historical-immutable-baseline',
            'host': 'x86_64-unknown-linux-gnu', 'source_revision': '1' * 40, 'source_sha256': '2' * 64,
            'binary': file('som'), 'assets': directory('assets'),
            'tools': {'SOM_RUFF': file('native/ruff'), 'SOM_RUSTFMT': file('native/rustfmt'),
                      'SOM_TS_NODE': file('native/node'), 'SOM_TS_PRETTIER': directory('native/prettier')},
            'health': {'raw_sha256': p.sha(raw), 'body': json.loads(raw)}}
        return value

    def test_plain_baseline_full_bytes_and_exact_tools_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'baseline'; profile = self.baseline(root)
            binary, assets, tools, expected = p.profile_inputs(profile, root)
            self.assertEqual(binary, root / 'som'); self.assertEqual(assets, root / 'assets')
            self.assertEqual(set(tools), p.TOOLS); self.assertEqual(expected, p.tree(root))

    def test_Linux_baseline_rejects_MachO_despite_matching_profile_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'baseline'; profile = self.baseline(root)
            (root / 'som').write_bytes(b'\xcf\xfa\xed\xfeMachO')
            profile['binary'].update(p.identity(root / 'som'))
            with self.assertRaisesRegex(ValueError, 'ELF'): p.profile_inputs(profile, root)

    def test_baseline_refuses_wrong_host_source_role_or_tool_universe(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'baseline'; original = self.baseline(root)
            for change in [lambda v: v.update(host='aarch64-apple-darwin'), lambda v: v.update(role='current'),
                           lambda v: v['tools'].pop('SOM_RUSTFMT')]:
                value = copy.deepcopy(original); change(value)
                with self.assertRaises(ValueError): p.profile_inputs(value, root)

    def test_baseline_omitted_extra_tampered_or_aliased_file_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'baseline'; profile = self.baseline(root)
            (root / 'extra').write_bytes(b'unlisted')
            with self.assertRaises(ValueError): p.profile_inputs(profile, root)
            (root / 'extra').unlink(); (root / 'assets/manifest.json').write_bytes(b'changed')
            with self.assertRaises(ValueError): p.profile_inputs(profile, root)
            (root / 'assets/manifest.json').write_bytes(b'{}')
            os.link(root / 'som', root / 'hardlink')
            with self.assertRaises(ValueError): p.profile_inputs(profile, root)

    def test_guarded_raw_reader_rejects_symlink_hardlink_and_size(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.file(root, 'raw', b'12345', 0o644)
            (root / 'link').symlink_to('raw')
            with self.assertRaises(ValueError): p.read(root / 'link')
            with self.assertRaises(ValueError): p.read(root / 'raw', 4)
            os.link(root / 'raw', root / 'hard')
            with self.assertRaises(ValueError): p.read(root / 'raw')

    def test_copy_rechecks_original_mode_and_complete_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); original = self.file(root, 'source')
            wanted = p.identity(original); p.copy_checked(original, root / 'copied', wanted)
            self.assertEqual(p.identity(root / 'copied'), wanted)
            original.chmod(0o644)
            with self.assertRaises(ValueError): p.copy_checked(original, root / 'bad', wanted)

    def test_real_adapter_must_exist_in_exact_admission_with_the_right_hash(self):
        raw = b'def run_gate(): pass\n'
        sources = SimpleNamespace(read=lambda path: raw)
        admission = {'admitted_repository_python_sources': ['owner.py']}
        data = {'gate_adapters': {'m6-react003': {'path': 'owner.py', 'sha256': p.sha(raw)}}}
        self.assertEqual(p.adapter_spec(data, 'm6-react003', admission, sources)['path'], 'owner.py')
        for value in ({}, {'path': '../owner.py', 'sha256': p.sha(raw)}, {'path': 'missing.py', 'sha256': p.sha(raw)},
                      {'path': 'owner.py', 'sha256': '0' * 64}):
            with self.assertRaises(ValueError): p.adapter_spec({'gate_adapters': {'m6-react003': value}}, 'm6-react003', admission, sources)

    def test_owner_verifier_api_cannot_be_a_missing_placeholder(self):
        def run_gate(ctx, candidate_dir, services, out, gate): pass
        def verify_result(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots): pass
        p.owner_api(SimpleNamespace(run_gate=run_gate, verify_result=verify_result))
        with self.assertRaises(ValueError): p.owner_api(SimpleNamespace(run_gate=run_gate))
        with self.assertRaises(ValueError): p.owner_api(SimpleNamespace(run_gate=lambda: None, verify_result=verify_result))

    def test_complete_four_roots_refuse_callback_overlap_or_missing_role(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); saved = root / 'saved'; saved.mkdir(); repo = root / 'repo'; repo.mkdir()
            values = {}
            for name in p.ROLES:
                values[name] = root / name; values[name].mkdir()
            self.assertEqual(set(p.roots_checked(values, saved, repo)), p.ROLES)
            with self.assertRaises(ValueError): p.roots_checked({k: v for k, v in values.items() if k != 'carrier_root'}, saved, repo)
            with self.assertRaises(ValueError): p.roots_checked({**values, 'carrier_root': saved}, saved, repo)

    def test_build_provenance_must_be_the_real_assembly_sibling(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); assembly = root / '_temp/som-unsealed-native-compile'; assembly.mkdir(parents=True)
            provenance = assembly.parent / 'som-current-build-provenance'; provenance.mkdir()
            self.assertEqual(p.provenance_root(assembly), provenance)
            wrong = root / 'other'; wrong.mkdir()
            with self.assertRaises(ValueError): p.provenance_root(wrong)

    def test_preloaded_helper_is_refused_before_reader_body(self):
        name = '_current_gate_unbound_fixture'
        with mock.patch.dict(sys.modules, {name: ModuleType(name)}):
            with self.assertRaisesRegex(ValueError, 'preloaded'):
                p.raw_module(SimpleNamespace(read=mock.Mock(side_effect=AssertionError('must not read'))), name, 'helper.py')

    def test_reserved_or_occupied_ports_never_start_a_service(self):
        with tempfile.TemporaryDirectory() as d:
            driver = SimpleNamespace(listeners=lambda port: [{'occupied': True}] if port == 18978 else [],
                                     OwnedService=mock.Mock(side_effect=AssertionError('must not launch')))
            with self.assertRaisesRegex(ValueError, 'occupied'):
                p.Services(None, driver, None, None, None, None, Path(d) / 'services')
            driver.OwnedService.assert_not_called()
        for port in (8765, 18765, 18979):
            with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
                with self.assertRaisesRegex(ValueError, '18978'):
                    p.main(['--gate','m6-react003','--repo','/unused','--helper-revision','1'*40,
                            '--candidate-revision','1'*40,'--product-context','/unused','--assembly-root','/unused',
                            '--carrier-root','/unused','--out','/unused','--qa-mode','native-qa-v1','--port',str(port)])

    def git_fixture(self, root, authority_body):
        repo = root / 'repo'; repo.mkdir()
        def write(name, raw):
            file = repo / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(raw)
        write(p.SELF, path.read_bytes()); write(p.AUTHORITY, authority_body)
        marker = root / 'context-body-executed'
        write(p.CONTEXT, ('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("BAD")\n').encode())
        command = ['/usr/bin/git', '--no-replace-objects', '-C', str(repo)]
        subprocess.check_call([*command, 'init', '--quiet'])
        subprocess.check_call([*command, 'add', '--all'])
        subprocess.check_call([*command, '-c','user.name=Source Fixture','-c','user.email=fixture@example.invalid',
                               '-c','commit.gpgsign=false','commit','--quiet','--no-verify','-m','fixture'])
        revision = subprocess.check_output([*command, 'rev-parse','HEAD']).decode().strip()
        return repo, revision, marker

    def test_actual_Git_pending_authority_stops_before_Context_body(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); repo, revision, marker = self.git_fixture(root,
                b'def verify(*args, **kwargs):\n    raise ValueError("pending numbered authority")\n')
            # The source/Git checks are real. Only the active loader is a
            # protocol double; it returns this exact committed pending body.
            bound = p.Sources(repo, revision)
            raw = bound.read(p.AUTHORITY)
            self.assertEqual(raw, (repo / p.AUTHORITY).read_bytes())
            authority = ModuleType('_committed_pending_authority_protocol_fixture')
            authority.__file__, authority.__som_raw_sha256__ = str(repo / p.AUTHORITY), p.sha(raw)
            exec(compile(raw, authority.__file__, 'exec', dont_inherit=True), authority.__dict__)
            committed_verify = authority.verify
            authority.verify = mock.Mock(wraps=committed_verify)
            lease = SimpleNamespace(verify_installed=mock.Mock(), close=mock.Mock(),
                load_module=mock.Mock(return_value=authority))
            controls = SimpleNamespace(borrow=mock.Mock(return_value=lease))
            with mock.patch.object(p, '_native_controls', return_value=controls) as acquire, \
                 mock.patch.object(p, 'raw_module', side_effect=AssertionError('legacy raw loader must not execute')):
                with self.assertRaisesRegex(ValueError, 'pending numbered'):
                    with p.admitted_sources(repo, revision, install=False):
                        self.fail('pending authority must not yield')
            acquire.assert_called_once()
            sources = acquire.call_args.args[0]
            self.assertEqual(sources.root, repo)
            self.assertEqual(sources.revision, revision)
            controls.borrow.assert_called_once_with()
            lease.load_module.assert_called_once_with('bootstrap.authority')
            authority.verify.assert_called_once_with(sources.read, 'native-qa-v1', sources.paths(), revision, revision)
            self.assertEqual(lease.verify_installed.call_count, 2)
            lease.close.assert_called_once_with()
            self.assertFalse(marker.exists())

    def test_actual_Git_source_change_cannot_reuse_a_committed_body(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); repo, revision, _ = self.git_fixture(root, b'VALUE = 1\n')
            sources = p.Sources(repo, revision)
            (repo / p.AUTHORITY).write_bytes(b'VALUE = 2\n')
            with self.assertRaisesRegex(ValueError, 'raw bytes'): sources.read(p.AUTHORITY)

    def test_actual_main_missing_owner_refuses_before_Context_body_or_native_process(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            admission = {'source_authority_active':True,'product_acceptance_passed':False,
                         'compiler_acceptance_passed':False,'whole_acceptance_complete':False,
                         'admitted_repository_python_sources':[]}
            repo,revision,marker = self.git_fixture(root, ('def verify(*args, **kwargs):\n    return ' + repr(admission) + '\n').encode())
            roots = {name:root/'inputs'/name for name in p.ROLES}
            roots['assembly_root'] = root/'inputs/_temp/som-unsealed-native-compile'
            for directory in roots.values():directory.mkdir(parents=True)
            provenance = roots['assembly_root'].parent/'som-current-build-provenance';provenance.mkdir()
            resources = {}
            for name in ('wheel_root','runtime_root','rust_tools_root','cargo_root'):
                resources[name]=str(root/name);Path(resources[name]).mkdir()
            context=root/'context.json'
            context.write_bytes(p.canonical({'repo':str(repo),'helper_revision':revision,'candidate_revision':revision,
                'qa_mode':'native-qa-v1','native_source_authority':admission,'build_provenance_root':str(provenance),
                **{name:str(value) for name,value in roots.items()},**resources,'gate_adapters':{}}))
            # Keep real Git/source checks. Only the active loader is a
            # finite protocol double for this missing-owner refusal fixture.
            bound = p.Sources(repo, revision)
            authority_raw = bound.read(p.AUTHORITY)
            self.assertEqual(authority_raw, (repo / p.AUTHORITY).read_bytes())
            authority = ModuleType('_actual_missing_owner_authority_protocol_fixture')
            authority.__file__ = str(repo / p.AUTHORITY)
            authority.__som_raw_sha256__ = p.sha(authority_raw)
            exec(compile(authority_raw, authority.__file__, 'exec', dont_inherit=True), authority.__dict__)
            committed_verify = authority.verify
            authority.verify = mock.Mock(wraps=committed_verify)
            lease = SimpleNamespace(verify_installed=mock.Mock(), close=mock.Mock(),
                load_module=mock.Mock(return_value=authority))
            controls = SimpleNamespace(borrow=mock.Mock(return_value=lease))
            real_popen=subprocess.Popen
            def git_only(argv,*args,**kwargs):
                if argv[0]!='/usr/bin/git':raise AssertionError('no native child')
                return real_popen(argv,*args,**kwargs)
            with mock.patch.dict(os.environ,{'RUNNER_TEMP':str(root)}), \
                 mock.patch.object(p, '_native_controls', return_value=controls) as acquire, \
                 mock.patch.object(p, 'raw_module', side_effect=AssertionError('legacy raw loader must not execute')), \
                 mock.patch.object(p.subprocess,'Popen',side_effect=git_only):
                with self.assertRaisesRegex(ValueError,'actual explicit owner'):
                    p.main(['--gate','m6-react003','--repo',str(repo),'--helper-revision',revision,
                        '--candidate-revision',revision,'--product-context',str(context),
                        '--assembly-root',str(roots['assembly_root']),'--carrier-root',str(roots['carrier_root']),
                        '--out',str(root/'output'),'--qa-mode','native-qa-v1','--port','18978'])
            acquire.assert_called_once()
            actual_sources = acquire.call_args.args[0]
            self.assertEqual(actual_sources.root, repo)
            self.assertEqual(actual_sources.revision, revision)
            controls.borrow.assert_called_once_with()
            lease.load_module.assert_called_once_with('bootstrap.authority')
            authority.verify.assert_called_once_with(actual_sources.read, 'native-qa-v1', actual_sources.paths(), revision, revision)
            self.assertEqual(lease.verify_installed.call_count, 2)
            lease.close.assert_called_once_with()
            self.assertFalse(marker.exists())

    def test_manager_stops_and_waits_both_owned_groups_without_rechecking_a_stopped_peer(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);profile=self.baseline(root/'inputs')
            binary,assets,tools,_=p.profile_inputs(profile,root/'inputs')
            driver=SimpleNamespace(listeners=lambda port:[],file_identity=full_identity,tree_identity=full_tree)
            manager=p.Services(None,driver,None,None,None,None,root/'services')
            waits=[]
            class Process:
                def __init__(self,pid):self.pid,self.code=pid,None
                def poll(self):return self.code
                def wait(self,timeout):waits.append(self.pid);return self.code
            class Service:
                def __init__(self,pid):self.process=Process(pid)
                def close(self):
                    self.process.code=-15
                    return {'owned_pid':self.process.pid,'exit_code':-15,'cleanup_passed':True,
                            'signal_target':'owned new process group only','signals':['SIGTERM'],
                            'remaining_owned_session_members':[]}
            initial={'binary':full_identity(binary),'assets':full_tree(assets),'tools':{
                key:full_tree(value) if key=='SOM_TS_PRETTIER' else full_identity(value) for key,value in tools.items()}}
            for role,pid in [('current',7),('baseline',8)]:
                directory=manager.out/role;directory.mkdir();(directory/'server.log').write_bytes(b'full raw log')
                manager.items[role]={'service':Service(pid),'record':{'role':role,'initial_identities':initial},
                                     'binary':binary,'assets':assets,'tools':tools}
            def check(item):
                self.assertIsNone(item['service'].process.poll())
            manager.verify_item=check
            manager.close_all();manager.close_all()
            self.assertEqual(waits,[8,7]);self.assertEqual(manager.receipt['errors'],[])
            for role in ('current','baseline'):
                value=json.loads((manager.out/role/'cleanup.json').read_bytes())
                self.assertTrue(value['wait_completed']);self.assertEqual(value['wait_exit_code'],-15)


class SavedServiceIntegrity(unittest.TestCase):
    # This subclass uses the real raw service verifier, with tiny retained data.
    file = GateIntegrity.file
    def fixture(self, root):
        candidate, restored = root / 'candidate', root / 'restored-candidate'
        candidate.mkdir(); restored.mkdir()
        binary = self.file(candidate, 'som')
        self.file(candidate, 'assets/manifest.json', b'{}', 0o644)
        tools = {}
        for role, name in {'SOM_RUFF':'native/bin/ruff','SOM_RUSTFMT':'compiler-rustfmt','SOM_TS_NODE':'native/bin/node'}.items():
            tools[role] = self.file(restored, name)
        self.file(restored, 'native/prettier/index.cjs', b'// exact', 0o644)
        tools['SOM_TS_PRETTIER'] = restored / 'native/prettier'
        initial = {'binary': full_identity(binary), 'assets': full_tree(candidate / 'assets'),
            'tools': {key: full_tree(value) if key == 'SOM_TS_PRETTIER' else full_identity(value) for key, value in tools.items()}}
        directory = root / 'services/current'; directory.mkdir(parents=True)
        inode = initial['binary']['inode']; owner = {'pid':7,'pgrp':7,'session':7,'start_ticks':9,
            'state':'S','executable_inode':inode,'listener':{'table':'tcp','address':'0100007F','inode':'55'}}
        fields = ['S','0','7','7',*(['0']*15),'9']
        (directory / 'start.proc-stat.raw').write_bytes(('7 (som) ' + ' '.join(fields)).encode())
        (directory / 'start.tcp.raw').write_text('header\n0: 0100007F:4A22 00000000:0000 0A 0 0 0 0 0 55\n')
        (directory / 'start.tcp6.raw').write_text('header\n')
        p.save(directory / 'start.fd-links.json', {'3':'socket:[55]'})
        proc = {'owner':owner,'executable':{'device':initial['binary']['device'],'inode':inode},
                'raw_paths':['start.proc-stat.raw','start.tcp.raw','start.tcp6.raw','start.fd-links.json']}
        p.save(directory / 'start.json', proc)
        raw = b'{"status":"ok"}'; (directory / 'health.response.raw').write_bytes(raw)
        env = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','RUST_BACKTRACE':'1',
               'TMPDIR':str(directory / 'tmp'),**{key:str(value) for key,value in tools.items()}}
        record = {'role':'current','port':18978,'argv':[str(binary),'serve','--assets',str(candidate / 'assets'),'--port','18978'],
            'environment':env,'started_at':'2026-10-29T00:00:00+00:00','initial_identities':initial,'extra':{'source_sha256':'a'*64},
            'owned_start':owner,'proc_proof':proc,'health':{'status':200,'raw_sha256':p.sha(raw),'owned_socket':owner,'body':json.loads(raw)},
            'health_checks':[]}
        p.save(directory / 'started.json',record)
        runtime = {'version':'som.rust-output-candidate-runtime.v1','accepted':False,'pid':7,'argv':record['argv'],'port':18978,
            'binary_sha256':initial['binary']['sha256'],'source_sha256':'a'*64,'started_at':record['started_at'],
            'health':json.loads(raw),'som_ts_prettier':str(tools['SOM_TS_PRETTIER']),
            'prettier_tree_sha256':p.tree_digest(p.tree(tools['SOM_TS_PRETTIER']))}
        p.save(directory / 'runtime.json',runtime); p.save(candidate / 'runtime.json',runtime)
        p.save(candidate / 'layout.json',{'original_candidate_dir':str(candidate),'original_restored_carrier_root':str(restored)})
        check = {'index':0,'status':200,'raw_sha256':p.sha(raw),'owned_socket':owner}
        checked = directory / 'health-checks/0000'; checked.mkdir(parents=True)
        (checked / 'response.raw').write_bytes(raw); p.save(checked / 'record.json',check); record['health_checks'].append(check)
        cleanup = {'owned_pid':7,'cleanup_passed':True,'wait_completed':True,'wait_exit_code':-15,'exit_code':-15,
                   'remaining_owned_session_members':[],'signal_target':'owned new process group only','signals':['SIGTERM']}
        p.save(directory / 'cleanup.json',cleanup)
        log = p.canonical({'stage':'listening','address':'127.0.0.1:18978','purpose':'som.rust-compiler.v1'}) + b'\n'
        (directory / 'server.log').write_bytes(log)
        record.update(cleanup=cleanup,final_identities=copy.deepcopy(initial),port_absent_after=True,server_log_sha256=p.sha(log))
        p.save(directory / 'result.json',record)
        protected = {str(port):[] for port in p.PROTECTED}
        p.save(root / 'services/protected-before.json',protected); p.save(root / 'services/protected-after.json',protected)
        value = {'version':'som.current-candidate-services.v1','services':{'current':record},
                 'protected_before':protected,'protected_after':protected,'errors':[]}
        trusted = SimpleNamespace(proof=SimpleNamespace(read=lambda name:raw),carrier={'identity':p.portable(initial)})
        layout = {'candidate_dir':str(candidate),'source_sha256':'a'*64,'binary_sha256':initial['binary']['sha256']}
        return root / 'services', value, trusted, layout, SimpleNamespace()

    def sync_record(self, data):
        root, value = data[:2]; record = value['services']['current']
        initial = {key:item for key,item in record.items() if key not in {'cleanup','final_identities','port_absent_after','server_log_sha256'}}
        initial['health_checks'] = []
        (root/'current/started.json').write_bytes(p.canonical(initial))
        (root/'current/result.json').write_bytes(p.canonical(record))
        runtime = json.loads((root/'current/runtime.json').read_bytes())
        runtime['health'],runtime['argv'] = record['health']['body'],record['argv']
        for target in (root/'current/runtime.json',Path(data[3]['candidate_dir'])/'runtime.json'):
            target.write_bytes(p.canonical(runtime))

    def test_full_retained_service_owner_health_wait_and_layout_join_passes(self):
        with tempfile.TemporaryDirectory() as d: p.verify_services(*self.fixture(Path(d)))

    def test_actual_proc_start_or_fd_socket_substitution_is_refused(self):
        for name, raw in [('start.proc-stat.raw',b'8 (som) S 0 7 7'),('start.fd-links.json',b'{"3":"socket:[999]"}')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                data = self.fixture(Path(d)); (data[0] / 'current' / name).write_bytes(raw)
                with self.assertRaises((ValueError,IndexError)): p.verify_services(*data)

    def test_rehashed_health_cannot_replace_the_trusted_typed_reply(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.fixture(Path(d)); root,value = data[:2]; record=value['services']['current']
            raw=b'{"status":"foreign"}'; (root/'current/health.response.raw').write_bytes(raw)
            record['health'].update(raw_sha256=p.sha(raw),body=json.loads(raw))
            for check in record['health_checks']:
                check['raw_sha256']=p.sha(raw); directory=root/'current/health-checks'/f"{check['index']:04d}"
                (directory/'response.raw').write_bytes(raw);(directory/'record.json').write_bytes(p.canonical(check))
            self.sync_record(data)
            with self.assertRaises(ValueError): p.verify_services(*data)

    def test_terminal_wait_or_remaining_owned_members_cannot_be_flags_only(self):
        for field,value in [('wait_completed',False),('wait_exit_code',None),('owned_pid',8),('remaining_owned_session_members',[{'pid':8}])]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as d:
                data=self.fixture(Path(d)); cleanup=data[1]['services']['current']['cleanup']; cleanup[field]=value
                (data[0]/'current/cleanup.json').write_bytes(p.canonical(cleanup))
                (data[0]/'current/result.json').write_bytes(p.canonical(data[1]['services']['current']))
                with self.assertRaises(ValueError):p.verify_services(*data)

    def test_foreign_candidate_tool_or_launch_environment_is_refused(self):
        for change in ('tool','argv','environment'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                data=self.fixture(Path(d)); record=data[1]['services']['current']
                if change=='tool': record['initial_identities']['tools']['SOM_RUFF']['sha256']='b'*64;record['final_identities']=copy.deepcopy(record['initial_identities'])
                elif change=='argv':record['argv'][-1]='8765'
                else:record['environment']['SOM_RUFF']='/foreign/ruff'
                self.sync_record(data)
                with self.assertRaises(ValueError):p.verify_services(*data)

    def test_missing_post_gate_health_and_changed_protected_listener_are_refused(self):
        for change in ('health','protected'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                data=self.fixture(Path(d));record=data[1]['services']['current']
                if change=='health':record['health_checks']=[];(data[0]/'current/result.json').write_bytes(p.canonical(record))
                else:data[1]['protected_after']['8765']=[{'inode':'foreign'}]
                with self.assertRaises(ValueError):p.verify_services(*data)


class CleanupFailureIntegrity(unittest.TestCase):
    baseline = GateIntegrity.baseline
    file = GateIntegrity.file

    def fixture(self, root, *, fail_close=False, fail_wait=False):
        profile = self.baseline(root / 'inputs')
        binary, assets, tools, _ = p.profile_inputs(profile, root / 'inputs')
        driver = SimpleNamespace(listeners=lambda port: [], file_identity=full_identity, tree_identity=full_tree)
        manager = p.Services(None, driver, None, None, None, None, root / 'services')
        calls = []
        class Process:
            def __init__(self, pid): self.pid, self.code = pid, None
            def poll(self): return self.code
            def wait(self, timeout):
                calls.append(('wait', self.pid))
                if self.pid == 8 and fail_wait: raise TimeoutError('baseline-wait-failure')
                return -15
        class Service:
            def __init__(self, pid): self.process = Process(pid)
            def close(self):
                calls.append(('close', self.process.pid))
                if self.process.pid == 8 and fail_close: raise OSError('baseline-close-failure')
                self.process.code = -15
                return {'owned_pid': self.process.pid, 'exit_code': -15, 'cleanup_passed': True,
                        'signal_target': 'owned new process group only', 'signals': ['SIGTERM'],
                        'remaining_owned_session_members': []}
        initial = {'binary': full_identity(binary), 'assets': full_tree(assets), 'tools': {
            key: full_tree(value) if key == 'SOM_TS_PRETTIER' else full_identity(value) for key, value in tools.items()}}
        for role, pid in [('current', 7), ('baseline', 8)]:
            directory = manager.out / role; directory.mkdir()
            (directory / 'server.log').write_bytes(b'complete raw server log')
            manager.items[role] = {'service': Service(pid), 'record': {'role': role, 'initial_identities': initial},
                                   'binary': binary, 'assets': assets, 'tools': tools}
        manager.verify_item = lambda item: self.assertIsNone(item['service'].process.poll())
        return manager, calls

    def assert_both_attempted(self, manager, calls):
        self.assertEqual(calls, [('close', 8), ('wait', 8), ('close', 7), ('wait', 7)])
        self.assertTrue(manager.closed)
        self.assertTrue(manager.receipt['errors'])
        current = json.loads((manager.out / 'current/cleanup.json').read_bytes())
        self.assertTrue(current['wait_completed']); self.assertEqual(current['wait_exit_code'], -15)
        self.assertEqual(json.loads((manager.out / 'manifest.json').read_bytes())['errors'], manager.receipt['errors'])

    def test_first_role_close_failure_still_waits_and_closes_the_other_group(self):
        with tempfile.TemporaryDirectory() as d:
            manager, calls = self.fixture(Path(d), fail_close=True)
            with self.assertRaisesRegex(ValueError, 'baseline-close-failure'): manager.close_all()
            self.assert_both_attempted(manager, calls)
            self.assertEqual(manager.receipt['errors'][0]['stage'], 'close')
            self.assertEqual(manager.items['baseline']['record']['cleanup_failures'][0]['reason'], 'baseline-close-failure')

    def test_first_role_wait_failure_cannot_skip_any_later_group(self):
        with tempfile.TemporaryDirectory() as d:
            manager, calls = self.fixture(Path(d), fail_wait=True)
            with self.assertRaisesRegex(ValueError, 'baseline-wait-failure'): manager.close_all()
            self.assert_both_attempted(manager, calls)
            self.assertFalse(manager.items['baseline']['record']['cleanup']['wait_completed'])

    def test_first_role_listener_failure_still_closes_and_waits_every_role(self):
        with tempfile.TemporaryDirectory() as d:
            manager, calls = self.fixture(Path(d))
            def listeners(port):
                if port == 18979: raise OSError('baseline-listener-read')
                return []
            manager.driver.listeners = listeners
            with self.assertRaisesRegex(ValueError, 'baseline-listener-read'): manager.close_all()
            self.assert_both_attempted(manager, calls)

    def test_first_role_receipt_write_failure_still_attempts_the_other_group(self):
        for name in ('cleanup.json', 'result.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                manager, calls = self.fixture(Path(d)); actual = p.save
                def save(path, value):
                    if Path(path) == manager.out / 'baseline' / name: raise OSError('baseline-ledger-write')
                    return actual(path, value)
                with mock.patch.object(p, 'save', side_effect=save), self.assertRaisesRegex(ValueError, 'baseline-ledger-write'):
                    manager.close_all()
                self.assert_both_attempted(manager, calls)

    def test_first_role_raw_log_read_failure_still_attempts_the_other_group(self):
        with tempfile.TemporaryDirectory() as d:
            manager, calls = self.fixture(Path(d)); actual = p.read
            def read(path, *args, **kwargs):
                if Path(path) == manager.out / 'baseline/server.log': raise OSError('baseline-log-read')
                return actual(path, *args, **kwargs)
            with mock.patch.object(p, 'read', side_effect=read), self.assertRaisesRegex(ValueError, 'baseline-log-read'):
                manager.close_all()
            self.assert_both_attempted(manager, calls)

    def test_owner_error_remains_primary_when_real_manager_cleanup_also_fails(self):
        with tempfile.TemporaryDirectory() as d:
            manager, calls = self.fixture(Path(d), fail_close=True)
            original = ValueError('owner-primary-failure')
            report = {'complete': False, 'all_required_passed': False}; cancellation = {'signal': None, 'cleanup': False}
            with self.assertRaises(ValueError) as caught:
                with p.owned_cleanup(manager, cancellation, report): raise original
            self.assertIs(caught.exception, original)
            self.assertEqual(report['primary_failure'], {'error': 'ValueError', 'reason': 'owner-primary-failure'})
            self.assertIn('baseline-close-failure', report['cleanup_failures'][0]['reason'])
            self.assertFalse(report['all_required_passed']); self.assertTrue(cancellation['cleanup'])
            self.assert_both_attempted(manager, calls)

    def test_cancellation_and_its_actual_signal_remain_primary_after_cleanup_failure(self):
        class Cancelled(BaseException): pass
        original = Cancelled('actual-owned-cancellation')
        manager = SimpleNamespace(close_all=mock.Mock(side_effect=OSError('cleanup-secondary')))
        cancellation = {'signal': 15, 'cleanup': False}; report = {'complete': False, 'all_required_passed': False}
        with self.assertRaises(Cancelled) as caught:
            with p.owned_cleanup(manager, cancellation, report): raise original
        self.assertIs(caught.exception, original); self.assertEqual(report['interrupted_signal'], 15)
        self.assertEqual(report['primary_failure']['reason'], 'actual-owned-cancellation')
        self.assertEqual(report['cleanup_failures'][0]['reason'], 'cleanup-secondary')
        self.assertFalse(report['all_required_passed']); manager.close_all.assert_called_once()

    def test_cleanup_only_failure_stays_failed_and_is_not_suppressed(self):
        original = OSError('cleanup-only-failure')
        manager = SimpleNamespace(close_all=mock.Mock(side_effect=original))
        report = {'complete': False, 'all_required_passed': False}
        with self.assertRaises(OSError) as caught:
            with p.owned_cleanup(manager, {'signal': None, 'cleanup': False}, report): pass
        self.assertIs(caught.exception, original)
        self.assertEqual(report['primary_failure']['reason'], 'cleanup-only-failure')
        self.assertFalse(report['complete']); self.assertFalse(report['all_required_passed'])


class HistoricalRoleIntegrity(unittest.TestCase):
    baseline = GateIntegrity.baseline
    file = GateIntegrity.file

    def test_f59_and_current_baseline_profiles_refuse_before_any_native_start(self):
        for revision in (p.PREPARATION_SOURCE, '1' * 40):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as d:
                root = Path(d); profile = self.baseline(root / 'inputs'); profile['source_revision'] = revision
                profile_path = p.BASE + 'profiles/synthetic-baseline.json'
                ctx = SimpleNamespace(candidate_revision='1' * 40,
                    data={'baseline_inputs': {profile_path: str(root / 'inputs')}},
                    sources=SimpleNamespace(read=lambda name: p.canonical(profile)))
                driver = SimpleNamespace(listeners=lambda port: [], OwnedService=mock.Mock(side_effect=AssertionError('must not launch')))
                manager = p.Services(ctx, driver, None, None, None, None, root / 'services')
                with self.assertRaisesRegex(ValueError, 'cannot relabel'): manager.start_baseline(profile_path)
                driver.OwnedService.assert_not_called(); self.assertEqual(manager.items, {})

    def fixture(self, root):
        data = SavedServiceIntegrity().fixture(root)
        services, value, trusted, layout, _ = data
        candidate = Path(layout['candidate_dir']); restored = root / 'restored-candidate'
        retained = services / 'baseline-inputs'; retained.mkdir()
        p.copy_checked(candidate / 'som', retained / 'som', p.identity(candidate / 'som'))
        for prefix, origin in [('assets', candidate / 'assets'), ('', restored)]:
            for name, wanted in p.tree(origin).items():
                p.copy_checked(origin / name, retained / (prefix + '/' + name if prefix else name), wanted)
        def file(name): return {'path': name, **p.identity(retained / name)}
        def directory(name): return {'path': name, 'files': p.tree(retained / name)}
        raw = (services / 'current/health.response.raw').read_bytes()
        profile = {'version': 'som.linux-qa-immutable-baseline.v1', 'role': 'historical-immutable-baseline',
            'host': 'x86_64-unknown-linux-gnu', 'source_revision': '3' * 40, 'source_sha256': 'a' * 64,
            'binary': file('som'), 'assets': directory('assets'), 'tools': {
                'SOM_RUFF': file('native/bin/ruff'), 'SOM_RUSTFMT': file('compiler-rustfmt'),
                'SOM_TS_NODE': file('native/bin/node'), 'SOM_TS_PRETTIER': directory('native/prettier')},
            'health': {'raw_sha256': p.sha(raw), 'body': json.loads(raw)}}
        profile_path = p.BASE + 'profiles/synthetic-baseline.json'
        baseline = services / 'baseline'; shutil.copytree(services / 'current', baseline)
        record = copy.deepcopy(value['services']['current'])
        record.update(role='baseline', port=18979,
            argv=[str(retained / 'som'), 'serve', '--assets', str(retained / 'assets'), '--port', '18979'],
            extra={'source_sha256': profile['source_sha256'], 'profile_path': profile_path, 'profile': profile,
                   'profile_sha256': p.sha(p.canonical(profile)), 'input_root': str(retained), 'retained_root': 'baseline-inputs'})
        record['environment']['TMPDIR'] = str(baseline / 'tmp')
        for role, item in profile['tools'].items(): record['environment'][role] = str(retained / item['path'])
        tcp = (baseline / 'start.tcp.raw').read_bytes().replace(b':4A22', b':4A23')
        (baseline / 'start.tcp.raw').write_bytes(tcp)
        log = (baseline / 'server.log').read_bytes().replace(b'18978', b'18979')
        (baseline / 'server.log').write_bytes(log); record['server_log_sha256'] = p.sha(log)
        runtime = json.loads((baseline / 'runtime.json').read_bytes())
        runtime.update(argv=record['argv'], port=18979, som_ts_prettier=record['environment']['SOM_TS_PRETTIER'])
        (baseline / 'runtime.json').write_bytes(p.canonical(runtime))
        value['services']['baseline'] = record
        sources = SimpleNamespace(revision='1' * 40, read=lambda name: p.canonical(profile))
        def sync():
            record['extra']['profile_sha256'] = p.sha(p.canonical(profile))
            (services / 'baseline-profile.raw').write_bytes(p.canonical(profile))
            started = {key: item for key, item in record.items() if key not in {'cleanup', 'final_identities', 'port_absent_after', 'server_log_sha256'}}
            started['health_checks'] = []
            (baseline / 'started.json').write_bytes(p.canonical(started))
            (baseline / 'result.json').write_bytes(p.canonical(record))
        sync()
        return (services, value, trusted, layout, sources), profile, sync

    def test_distinct_synthetic_saved_baseline_shape_reaches_the_independent_guard(self):
        with tempfile.TemporaryDirectory() as d:
            data, _, _ = self.fixture(Path(d)); p.verify_services(*data)

    def test_saved_verifier_rejects_rehashed_f59_and_current_historical_roles(self):
        for revision in (p.PREPARATION_SOURCE, '1' * 40):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as d:
                data, profile, sync = self.fixture(Path(d))
                p.verify_services(*data)  # Valid synthetic control, not an actual before comparator.
                profile['source_revision'] = revision; sync()
                with self.assertRaisesRegex(ValueError, 'cannot relabel'): p.verify_services(*data)



class CleanupSignalIntegrity(unittest.TestCase):
    baseline = GateIntegrity.baseline
    file = GateIntegrity.file
    fixture = CleanupFailureIntegrity.fixture

    @contextmanager
    def actual_cancellation(self):
        # Compile only the actual owning driver's two declarations. No project imports.
        driver_path = Path(__file__).with_name('run_experimental_native_compile.py')
        source = ast.parse(driver_path.read_bytes(), filename=str(driver_path))
        wanted = {'DriverInterrupted', 'cancellation_handlers'}
        nodes = [node for node in source.body
                 if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in wanted]
        self.assertEqual({node.name for node in nodes}, wanted)
        module = ModuleType('_actual_driver_cleanup_signal_fixture')
        module.__dict__.update(contextmanager=contextmanager, signal=signal)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(driver_path), 'exec', dont_inherit=True), module.__dict__)
        handlers, old_handlers = {}, {}
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            old_handlers[number] = object()
        def install(number, handler):
            handlers[number] = handler
        with mock.patch.object(signal, 'getsignal', side_effect=lambda number: old_handlers[number]), \
             mock.patch.object(signal, 'signal', side_effect=install):
            with module.cancellation_handlers() as cancellation:
                self.assertIsNone(cancellation['signal']); self.assertFalse(cancellation['cleanup'])
                yield cancellation, handlers
            self.assertEqual(handlers, old_handlers)

    def interrupt_during_close(self, manager, cancellation, handlers):
        original_close = manager.items['baseline']['service'].close
        def close():
            self.assertTrue(cancellation['cleanup'])
            self.assertIsNone(cancellation['signal'])
            # This is the actual source-bound handler, not a direct state assignment.
            self.assertIsNone(handlers[signal.SIGTERM](int(signal.SIGTERM), None))
            self.assertIsNone(handlers[signal.SIGHUP](int(signal.SIGHUP), None))
            self.assertEqual(cancellation['signal'], int(signal.SIGTERM))
            self.assertIs(type(cancellation['signal']), int)
            self.assertFalse(cancellation['raised'])
            return original_close()
        manager.items['baseline']['service'].close = close

    def assert_all_roles_attempted(self, manager, calls):
        self.assertTrue(manager.closed)
        self.assertEqual(calls, [('close', 8), ('wait', 8), ('close', 7), ('wait', 7)])
        self.assertTrue(manager.items['current']['record']['cleanup']['wait_completed'])

    def test_first_signal_during_successful_cleanup_is_retained_and_gate_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            manager, calls = self.fixture(Path(d))
            report = {'complete': False, 'all_required_passed': False}
            with self.actual_cancellation() as (cancellation, handlers):
                self.interrupt_during_close(manager, cancellation, handlers)
                with self.assertRaisesRegex(ValueError, 'owned gate was interrupted'):
                    with p.owned_cleanup(manager, cancellation, report): pass
                    # The unchanged main refusal must still run after successful cleanup.
                    p.require(cancellation['signal'] is None, 'owned gate was interrupted')
            self.assertEqual(report['interrupted_signal'], signal.SIGTERM)
            self.assertNotIn('cleanup_failures', report)
            self.assertFalse(report['complete']); self.assertFalse(report['all_required_passed'])
            self.assert_all_roles_attempted(manager, calls)

    def test_first_signal_during_failed_cleanup_keeps_cleanup_primary_and_signal(self):
        with tempfile.TemporaryDirectory() as d:
            manager, calls = self.fixture(Path(d), fail_close=True)
            report = {'complete': False, 'all_required_passed': False}
            with self.actual_cancellation() as (cancellation, handlers):
                self.interrupt_during_close(manager, cancellation, handlers)
                with self.assertRaisesRegex(ValueError, 'baseline-close-failure') as caught:
                    with p.owned_cleanup(manager, cancellation, report): pass
            self.assertEqual(report['interrupted_signal'], signal.SIGTERM)
            self.assertEqual(report['primary_failure'], p.failure_record(caught.exception))
            self.assertEqual(report['cleanup_failures'], [p.failure_record(caught.exception)])
            self.assertEqual(report['owned_cleanup_receipt'], manager.receipt)
            self.assertFalse(report['complete']); self.assertFalse(report['all_required_passed'])
            self.assert_all_roles_attempted(manager, calls)

    def test_owner_primary_and_first_cleanup_signal_survive_successful_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            manager, calls = self.fixture(Path(d))
            original = OSError('owner-before-first-cleanup-signal')
            report = {'complete': False, 'all_required_passed': False}
            with self.actual_cancellation() as (cancellation, handlers):
                self.interrupt_during_close(manager, cancellation, handlers)
                with self.assertRaises(OSError) as caught:
                    with p.owned_cleanup(manager, cancellation, report): raise original
            self.assertIs(caught.exception, original)
            self.assertEqual(report['primary_failure'], p.failure_record(original))
            self.assertEqual(report['interrupted_signal'], signal.SIGTERM)
            self.assertNotIn('cleanup_failures', report)
            self.assertFalse(report['complete']); self.assertFalse(report['all_required_passed'])
            self.assert_all_roles_attempted(manager, calls)

    def test_owner_primary_cleanup_secondary_and_first_cleanup_signal_stay_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            manager, calls = self.fixture(Path(d), fail_close=True)
            original = OSError('owner-before-first-cleanup-signal')
            report = {'complete': False, 'all_required_passed': False}
            with self.actual_cancellation() as (cancellation, handlers):
                self.interrupt_during_close(manager, cancellation, handlers)
                with self.assertRaises(OSError) as caught:
                    with p.owned_cleanup(manager, cancellation, report): raise original
            self.assertIs(caught.exception, original)
            self.assertEqual(report['primary_failure'], p.failure_record(original))
            self.assertEqual(report['interrupted_signal'], signal.SIGTERM)
            self.assertEqual(len(report['cleanup_failures']), 1)
            self.assertIn('baseline-close-failure', report['cleanup_failures'][0]['reason'])
            self.assertEqual(report['owned_cleanup_receipt'], manager.receipt)
            self.assertFalse(report['complete']); self.assertFalse(report['all_required_passed'])
            self.assert_all_roles_attempted(manager, calls)




class RecordedFreshBootstrapIntegrity(unittest.TestCase):
    """Finite source protocol fixtures. No real native Context or service pass."""
    revision = '1' * 40
    token = 'fixture-actions-authentication-never-save'

    def frame(self, temporary):
        ref = 'refs/heads/codex/native-recorded-fixture'
        return {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'Linux',
            'RUNNER_TEMP': str(temporary), 'GITHUB_SHA': self.revision, 'GITHUB_RUN_ID': '123',
            'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_JOB': 'required-callbacks',
            'GITHUB_REPOSITORY': 'faberline/som', 'GITHUB_REPOSITORY_ID': '1390374878',
            'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_API_URL': 'https://api.github.com',
            'GITHUB_WORKFLOW_SHA': self.revision, 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': ref,
            'GITHUB_WORKFLOW_REF': 'faberline/som/.github/workflows/rust-hosted-readiness.yml@' + ref,
            'RUNNER_NAME': 'Synthetic Protocol Runner', p.RECORDED_TOKEN: self.token}

    def test_complete_checked_frame_keeps_authentication_only_in_actual_environment(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); child = self.frame(root)
            child.update(M6_INPUTS_KEYRING='fixture-key-must-not-forward', PYTHONPATH='/ambient/site', HOME='/ambient/home')
            actual, public = p.recorded_python_environment(child, context=root / 'context.json',
                directory=root / 'baseline', revision=self.revision)
            self.assertEqual(actual[p.RECORDED_TOKEN], self.token)
            self.assertEqual(public[p.RECORDED_TOKEN], p.RECORDED_TOKEN_PUBLIC)
            self.assertEqual(set(actual), set(public))
            self.assertEqual(set(public), {'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SOM_FULL_PRODUCT_CONTEXT',
                                          p.RECORDED_TOKEN, *p.RECORDED_FRAME})
            self.assertNotEqual(id(actual), id(public))
            raw = p.canonical(public)
            for forbidden in (self.token, p.sha(self.token.encode()), 'fixture-key-must-not-forward', '/ambient/site'):
                self.assertNotIn(forbidden.encode(), raw)
            public['RUNNER_NAME'] = 'changed public map'
            self.assertEqual(actual['RUNNER_NAME'], child['RUNNER_NAME'])

    def test_missing_or_unsafe_existing_authentication_cannot_be_synthesized(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for value in (None, '', 'bad\nvalue', 'bad\rvalue', 'bad\0value', p.RECORDED_TOKEN_PUBLIC):
                child = self.frame(root)
                if value is None: child.pop(p.RECORDED_TOKEN)
                else: child[p.RECORDED_TOKEN] = value
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'existing Actions authentication'):
                    p.recorded_python_environment(child, context=root / 'context.json',
                        directory=root / 'baseline', revision=self.revision)

    def test_every_fresh_bootstrap_frame_key_is_required(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for key in p.RECORDED_FRAME:
                child = self.frame(root); child.pop(key)
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'complete child environment'):
                    p.recorded_python_environment(child, context=root / 'context.json',
                        directory=root / 'baseline', revision=self.revision)

    def test_public_environment_refuses_real_auth_extra_keys_and_wrong_hosted_frame(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, original = p.recorded_python_environment(self.frame(root), context=root / 'context.json',
                directory=root / 'baseline', revision=self.revision)
            changes = [(p.RECORDED_TOKEN, self.token), ('EXTRA', 'unlisted'), ('GITHUB_REPOSITORY_ID', '9'),
                ('GITHUB_REPOSITORY', 'foreign/som'), ('GITHUB_API_URL', 'https://foreign.invalid'),
                ('GITHUB_WORKFLOW_SHA', '2' * 40), ('GITHUB_EVENT_NAME', 'pull_request'),
                ('GITHUB_REF', 'refs/heads/different'), ('RUNNER_NAME', ' padded '),
                ('GITHUB_JOB', 'invalid job'), ('GITHUB_RUN_ID', '0'), ('RUNNER_TEMP', '/foreign')]
            for key, value in changes:
                changed = {**original, key: value}
                with self.subTest(key=key), self.assertRaises(ValueError):
                    p.validate_recorded_python_environment(changed, context=root / 'context.json',
                        directory=root / 'baseline', revision=self.revision)

    def test_failed_service_start_keeps_original_error_and_public_record_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); shared = root / 'shared'; shared.mkdir(); out = shared / 'services'; out.mkdir()
            inputs = root / 'inputs'; inputs.mkdir(); (inputs / 'data').write_bytes(b'opaque recorded data fixture')
            python = root / 'python3.12'; python.write_bytes(b'\x7fELFqualified fixture'); python.chmod(0o755)
            context = root / 'context.json'; raw = b'{"native_protocol_fixture":true}'
            context.write_bytes(raw); (shared / 'context.input.json').write_bytes(raw)
            primary = RuntimeError('specific owned-start failure')
            made = []
            class Service:
                def __init__(self, binary, assets, tools, port, directory):
                    self.process = None; self.environment = {}; made.append(self)
                def start(self): raise primary
            oracle = SimpleNamespace(prepare_data=lambda *args: ({}, {}, {'profile_sha256': p.sha(b'{}'),
                'named_slots': 4572}, lambda: None))
            ctx = SimpleNamespace(repo=root / 'repo', helper_revision=self.revision, candidate_revision=self.revision,
                path=context, raw=raw, sources=SimpleNamespace(read=lambda name: ('source:' + name).encode()),
                module=lambda key, path: oracle, runtime=lambda: {'tools': {'python': str(python)},
                    'identity': {'python_sha256': p.identity(python)['sha256']}},
                child_environment=lambda: self.frame(root))
            manager = object.__new__(p.Services)
            manager.ctx, manager.out, manager.closed, manager.items = ctx, out, False, {}
            manager.driver = SimpleNamespace(OwnedService=Service, file_identity=full_identity,
                tree_identity=full_tree, utc=lambda: 'synthetic-time')
            with mock.patch.dict(os.environ, {'SOM_FULL_PRODUCT_CONTEXT': str(context)}):
                with self.assertRaises(RuntimeError) as caught:
                    manager._start_recorded('profile.json', b'{}', inputs)
            self.assertIs(caught.exception, primary)
            self.assertIs(manager.items['baseline']['service'], made[0])
            self.assertEqual(made[0].environment[p.RECORDED_TOKEN], self.token)
            record = manager.items['baseline']['record']
            self.assertIsNot(record['environment'], made[0].environment)
            self.assertEqual(record['environment'][p.RECORDED_TOKEN], p.RECORDED_TOKEN_PUBLIC)
            for forbidden in (self.token, p.sha(self.token.encode())):
                self.assertNotIn(forbidden.encode(), p.canonical(record))
            self.assertEqual(record['requested_argv'][4], str(ctx.repo / p.ORACLE))
            self.assertNotIn('cleanup', record)
            self.assertEqual((out / 'baseline-profile.raw').read_bytes(), b'{}')

    def startup_fixture(self, root):
        directory = root / 'shared/services/baseline'; directory.mkdir(parents=True)
        revision = self.revision; repo = root / 'repo'; context = root / 'context.json'
        data = {'repo': str(repo), 'helper_revision': revision, 'candidate_revision': revision, 'qa_mode': 'native-qa-v1',
                'runtime_root': str(root / 'runtime'), 'work_root': str(root / 'work')}
        context_raw = p.canonical(data); context.write_bytes(context_raw)
        (root / 'shared/context.input.json').write_bytes(context_raw)
        python = root / 'runtime/install/python/bin/python3.12'; python.parent.mkdir(parents=True)
        python.write_bytes(b'\x7fELFqualified fixture'); python.chmod(0o755)
        digest = p.identity(python)['sha256']
        requested, actual = p.recorded_python_arguments(python, repo, revision, revision, 'profile.json',
            root / 'inputs', directory / 'oracle', digest)
        startup = {'version': 'som.native-python-child-startup.v1', 'pid': 123, 'actual_argv': actual,
            'original_requested_argv': requested, 'cwd': str(directory), 'helper_revision': revision,
            'candidate_revision': revision, 'product_acceptance_passed': False}
        source_bytes = {p.CONTEXT: b'context source', p.ORACLE: b'oracle source',
                        p.NATIVE_BOOTSTRAP: b'initial bootstrap source', p.FULL_DRIVER: b'entry driver source'}
        post = {'version': 'som.native-python-child-postcheck.v1', 'actual_argv': actual,
            'original_python_arguments': requested[4:], 'helper_revision': revision, 'candidate_revision': revision,
            'entry_executed': True, 'postchecks_passed': True, 'exit_code': 0, 'product_acceptance_passed': False,
            'payload': {'version': 'som.native-python-startup-payload.v1', 'role': 'qualified-base-no-site',
                'python': str(python), 'site': None, 'pth_executed': False, 'product_acceptance_passed': False},
            # Real Context reads these. Bootstrap/driver pins are separate below.
            'source_sha256': {name: p.sha(source_bytes[name]) for name in (p.CONTEXT, p.ORACLE)}}
        startup_raw, post_raw = p.canonical(startup), p.canonical(post)
        startup_source = root / 'work/python-child-startups/77-123.json'
        post_source = root / 'work/python-startup-postchecks/123.json'
        startup_source.parent.mkdir(parents=True); post_source.parent.mkdir(parents=True)
        startup_source.write_bytes(startup_raw); post_source.write_bytes(post_raw)
        (directory / 'native-python-startup.json').write_bytes(startup_raw)
        (directory / 'native-python-postcheck.json').write_bytes(post_raw)
        (directory / 'start.proc-stat.raw').write_bytes(b'123 (fixture) S 77 123 123')
        _, environment = p.recorded_python_environment(self.frame(root), context=context,
            directory=directory, revision=revision)
        record = {'requested_argv': requested, 'argv': actual, 'environment': environment,
            'owned_start': {'pid': 123}, 'cleanup': {'exit_code': 0, 'wait_exit_code': 0},
            'native_python_postcheck': {'source': str(post_source), 'sha256': p.sha(post_raw)},
            'extra': {'profile_path': 'profile.json', 'input_root': str(root / 'inputs'),
                'native_bootstrap_source': p.NATIVE_BOOTSTRAP,
                'native_bootstrap_source_sha256': p.sha(source_bytes[p.NATIVE_BOOTSTRAP]),
                'native_entry_driver_source': p.FULL_DRIVER,
                'native_entry_driver_source_sha256': p.sha(source_bytes[p.FULL_DRIVER]),
                'native_startup_source': str(startup_source), 'native_startup_sha256': p.sha(startup_raw)}}
        sources = SimpleNamespace(root=repo, revision=revision, read=source_bytes.__getitem__)
        return directory, record, sources, data

    def test_retained_startup_and_terminal_receipts_keep_exact_new_source_chain(self):
        with tempfile.TemporaryDirectory() as d:
            directory, record, sources, data = self.startup_fixture(Path(d))
            p.verify_recorded_python_startup(directory, record, sources, data)
            self.assertEqual(record['argv'][4], str(sources.root / p.NATIVE_BOOTSTRAP))
            self.assertNotIn(str(sources.root / p.FULL_DRIVER), record['argv'])
            self.assertEqual(record['extra']['native_entry_driver_source_sha256'], p.sha(sources.read(p.FULL_DRIVER)))

    def test_initial_bootstrap_and_entry_driver_cannot_borrow_each_others_pin(self):
        for change in ('bootstrap-path', 'bootstrap-sha', 'driver-path', 'driver-sha'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                directory, record, sources, data = self.startup_fixture(Path(d))
                if change == 'bootstrap-path': record['extra']['native_bootstrap_source'] = p.FULL_DRIVER
                elif change == 'bootstrap-sha': record['extra']['native_bootstrap_source_sha256'] = p.sha(sources.read(p.FULL_DRIVER))
                elif change == 'driver-path': record['extra']['native_entry_driver_source'] = p.NATIVE_BOOTSTRAP
                else: record['extra']['native_entry_driver_source_sha256'] = p.sha(sources.read(p.NATIVE_BOOTSTRAP))
                with self.assertRaisesRegex(ValueError, 'initial bootstrap, entry driver'):
                    p.verify_recorded_python_startup(directory, record, sources, data)

    def test_old_driver_first_saved_capture_cannot_be_relabelled_as_new_bootstrap(self):
        with tempfile.TemporaryDirectory() as d:
            directory, record, sources, data = self.startup_fixture(Path(d))
            record['argv'] = [record['argv'][0], '-I', '-S', '-B', str(sources.root / p.FULL_DRIVER),
                              '--native-python-bootstrap', '--', *record['requested_argv'][4:]]
            with self.assertRaisesRegex(ValueError, 'initial bootstrap, entry driver'):
                p.verify_recorded_python_startup(directory, record, sources, data)



    def test_saved_public_frame_must_match_immutable_context_and_same_run_catalog(self):
        with tempfile.TemporaryDirectory() as d:
            temporary = Path(d)
            directory, record, sources, data = self.startup_fixture(temporary)
            services = directory.parent; shared = services.parent
            profile = p.BASE + 'inputs/recorded-before-profile-v1.json'
            profile_raw = b'{"synthetic_source_profile":true}'
            inputs = temporary / 'inputs'; inputs.mkdir(); (inputs / 'opaque-data').write_bytes(b'fixture input')
            python = temporary / 'runtime/install/python/bin/python3.12'
            catalog = {'repository': 'faberline/som', 'run_id': 123, 'run_attempt': 2,
                       'helper_revision': self.revision, 'candidate_revision': self.revision}
            data.update(artifact_inputs=catalog, baseline_inputs={profile: str(inputs)})
            context_raw = p.canonical(data)
            Path(record['environment']['SOM_FULL_PRODUCT_CONTEXT']).write_bytes(context_raw)
            (shared / 'context.input.json').write_bytes(context_raw)
            (shared / 'artifact-inputs.json').write_bytes(p.canonical(catalog))
            (services / 'baseline-profile.raw').write_bytes(profile_raw)
            source_read = sources.read
            sources.read = lambda name: profile_raw if name == profile else source_read(name)
            record['extra'].update(profile_path=profile, profile_sha256=p.sha(profile_raw), profile=p.parse(profile_raw),
                                   input_root=str(inputs), oracle_out='oracle')
            requested, actual = p.recorded_python_arguments(python, sources.root, self.revision, self.revision,
                profile, inputs, directory / 'oracle', p.identity(python)['sha256'])
            record.update(requested_argv=requested, argv=actual, port=p.PORTS['baseline'])
            identities = {'binary': full_identity(python), 'assets': full_tree(inputs), 'tools': {}}
            record.update(initial_identities=identities, final_identities=copy.deepcopy(identities))
            runtime = {'argv': actual, 'port': p.PORTS['baseline'], 'pid': 123,
                       'binary_sha256': p.identity(python)['sha256']}
            (directory / 'runtime.json').write_bytes(p.canonical(runtime))
            oracle_dir = directory / 'oracle'; oracle_dir.mkdir()
            proof_raw = b'{"synthetic_data_proof":true}'; (oracle_dir / 'data-proof.json').write_bytes(proof_raw)
            record['extra']['data_proof_sha256'] = p.sha(proof_raw)
            oracle = SimpleNamespace(verify_recorded_run=lambda *args, **kwargs: {'profile_sha256': p.sha(profile_raw)})
            @contextmanager
            def borrowed(unused):
                yield SimpleNamespace(role_sources={'common.recorded-service': p.ORACLE},
                                      load_module=lambda role: oracle)
            # External runtime/data/cleanup proofs are explicit finite stubs here.
            # This fixture tests the new public-frame join, not those native gates.
            with mock.patch.dict(os.environ, {name: self.frame(temporary)[name] for name in p.RECORDED_FRAME}), \
                 mock.patch.object(p, 'qualified_recorded_python', return_value=python), \
                 mock.patch.object(p, 'verify_recorded_python_startup') as startup, \
                 mock.patch.object(p, 'verify_service_cleanup') as cleanup, \
                 mock.patch.object(p, '_borrowed_controls', side_effect=borrowed):
                p.verify_recorded_service(services, directory, record, {'pid': 123}, sources)
                startup.assert_called_once(); cleanup.assert_called_once()
                startup.reset_mock(); cleanup.reset_mock()
                for changed in ({**catalog, 'run_id': 999}, {**catalog, 'run_attempt': 9},
                                {**catalog, 'candidate_revision': '2' * 40}):
                    (shared / 'artifact-inputs.json').write_bytes(p.canonical(changed))
                    with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'immutable Context/same-run'):
                        p.verify_recorded_service(services, directory, record, {'pid': 123}, sources)
                    startup.assert_not_called(); cleanup.assert_not_called()
                (shared / 'artifact-inputs.json').write_bytes(p.canonical(catalog))
                original_environment = record['environment']
                alternate_ref = 'refs/heads/codex/another-valid-saved-branch'
                changes = [
                    {'GITHUB_JOB': 'another_valid_job'},
                    {'RUNNER_NAME': 'Another Valid Synthetic Runner'},
                    {'GITHUB_EVENT_NAME': 'push'},
                    {'GITHUB_REF': alternate_ref,
                     'GITHUB_WORKFLOW_REF': 'faberline/som/.github/workflows/rust-hosted-readiness.yml@' + alternate_ref},
                ]
                for change in changes:
                    record['environment'] = {**original_environment, **change}
                    # Establish that this valid-shaped substitution reached the live-frame join.
                    p.validate_recorded_python_environment(record['environment'],
                        context=record['environment']['SOM_FULL_PRODUCT_CONTEXT'],
                        directory=directory, revision=self.revision)
                    with self.subTest(saved_change=change), self.assertRaisesRegex(ValueError, 'immutable Context/same-run'):
                        p.verify_recorded_service(services, directory, record, {'pid': 123}, sources)
                    startup.assert_not_called(); cleanup.assert_not_called()
                record['environment'] = original_environment
                for name in p.RECORDED_FRAME:
                    with self.subTest(actual_field=name), mock.patch.dict(os.environ, {name: 'different-actual-frame'}):
                        with self.assertRaisesRegex(ValueError, 'immutable Context/same-run'):
                            p.verify_recorded_service(services, directory, record, {'pid': 123}, sources)
                        startup.assert_not_called(); cleanup.assert_not_called()


if __name__ == '__main__': unittest.main(verbosity=2)
