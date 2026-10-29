"""Native startup source fixtures. Execute only on GitHub with Python3.12.

Synthetic wheel data tests payload rules. It is not a runtime qualification.
Real child processes test no-site startup and actual argv retention.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import csv
import io
import json
import marshal
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock
import zipfile

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub fixtures only')

HERE = Path(__file__).absolute().parent
def subject(name, path):
    module = ModuleType(name)
    module.__file__, module.__cached__, module.__package__ = str(path), None, ''
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module

c = subject('_native_startup_context', HERE / 'product_runtime/full_product_context.py')
w = subject('_native_startup_wheels', HERE / 'product_python/produce_wheels.py')


class NativeStartup(unittest.TestCase):
    @contextmanager
    def payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheels = root / 'wheels'; wheels.mkdir()
            prefix = 'startupfixture-0.1.dist-info'
            files = {'startupfixture/__init__.py': b'VALUE = 42\n',
                prefix + '/METADATA': b'Metadata-Version: 2.3\nName: startupfixture\nVersion: 0.1\nLicense-Expression: MIT\n\nPublic source fixture.\n',
                prefix + '/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
                prefix + '/licenses/LICENSE': b'Public synthetic fixture license.\n'}
            record = io.StringIO(newline=''); writer = csv.writer(record, lineterminator='\n')
            for name, raw in sorted(files.items()):
                writer.writerow([name, 'sha256=' + base64.urlsafe_b64encode(bytes.fromhex(w.sha(raw))).rstrip(b'=').decode(), str(len(raw))])
            writer.writerow([prefix + '/RECORD', '', ''])
            files[prefix + '/RECORD'] = record.getvalue().encode()
            wheel = wheels / 'startupfixture-0.1-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                for name, raw in files.items(): archive.writestr(name, raw)
            notices = root / 'notices'; notices.mkdir()
            audit = w.audit_wheel(wheel, 'startupfixture', '0.1', notices)
            closure = {'wheels': wheels, 'audits': {'startupfixture': audit}}
            assets = w.installer_assets((HERE / 'product_python/uv099-installer-authority.json').read_bytes())
            venv = root / '.venv'
            expected, contents, origins, dirs, proof = w.installed_scope(
                venv, closure, assets, Path(sys.executable).resolve(), 'startupfixture')
            venv.mkdir()
            for name in sorted(dirs, key=lambda value: len(Path(value).parts)):
                (venv / name).mkdir(parents=True, exist_ok=True)
            for name, value in expected.items():
                path = venv / name
                if value['type'] == 'symlink': path.symlink_to(value['target'])
                else: path.write_bytes(contents[name])
            site = venv / 'lib/python3.12/site-packages'
            # Ordinary installer metadata remains present. The source-payload
            # guard records it and leaves its independent whole-scope audit open.
            (site / prefix / 'INSTALLER').write_bytes(b'uv')
            (site / prefix / 'REQUESTED').write_bytes(b'')
            (site / prefix / 'direct_url.json').write_bytes(b'{"url":"fixture.whl"}')
            result = SimpleNamespace(root=root, venv=venv, site=site, expected=expected, origins=origins,
                dirs=dirs, prefix=prefix, wheel=wheel, closure=closure, assets=assets)
            yield result

    def check(self, fixture):
        return c.ordinary_python_payload(fixture.venv, w.installed_inventory(fixture.venv, fixture.root),
            fixture.expected, fixture.origins, fixture.dirs, [fixture.prefix])

    def child_probe(self, fixture):
        # This is a test-only source/payload harness. It exercises the real path
        # activation method after a real payload check. It is not Native.verify.
        data = fixture.root / 'expected.json'
        data.write_text(json.dumps({'expected': fixture.expected, 'origins': fixture.origins,
                                   'dirs': sorted(fixture.dirs), 'prefix': fixture.prefix}))
        program = r'''
import json, os, pathlib, sys
from types import ModuleType, SimpleNamespace
path, wheel_source, venv, data = map(pathlib.Path, sys.argv[1:])
def load(name, path):
    m=ModuleType(name);m.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec',dont_inherit=True),m.__dict__)
    return m
c=load('context_fixture',path);w=load('wheel_fixture',wheel_source)
spec=json.loads(data.read_bytes())
proof=c.ordinary_python_payload(venv,w.installed_inventory(venv,venv.parent),spec['expected'],spec['origins'],set(spec['dirs']),[spec['prefix']])
proof['python']=str(venv/'bin/python')
ctx=SimpleNamespace(qa_mode='native-qa-v1',qa_source_authority={'source_authority_active':True},
    python_payload=lambda executable:proof,cache={})
c.Context.activate_python_paths(ctx,sys.executable)
import startupfixture
print(json.dumps({'value':startupfixture.VALUE,'uv_hook_executed':'_virtualenv' in sys.modules,
    'customize_executed':'sitecustomize' in sys.modules,'isolated':sys.flags.isolated,
    'no_site':sys.flags.no_site,'metadata_audit':proof['ordinary_installer_scope_independent_audit']}))
'''
        return subprocess.run([str(fixture.venv / 'bin/python'), '-I', '-S', '-B', '-c', program,
            str(HERE / 'product_runtime/full_product_context.py'), str(HERE / 'product_python/produce_wheels.py'),
            str(fixture.venv), str(data)], capture_output=True, text=True, check=False)

    def context(self, mode='native-qa-v1'):
        return SimpleNamespace(qa_mode=mode, repo=HERE.parents[2], helper_revision='1' * 40, candidate_revision='1' * 40,
            sources=SimpleNamespace(root=HERE.parents[2], revision='1' * 40,
                read=lambda name: (HERE.parents[2] / name).read_bytes()),
            python_payload=lambda executable: {'site': None})

    def bootstrap_prefix(self, python, ctx):
        return [python, '-I', '-S', '-B', str(ctx.repo / 'som-rs/scripts/hosted_readiness/native_source_bootstrap.py'),
            '--repo', str(ctx.repo), '--helper-revision', ctx.helper_revision,
            '--candidate-revision', ctx.candidate_revision, '--entry-role', 'entry.full-products', '--']

    def test_real_wheel_and_uv_payload_accept_ordinary_metadata_without_audit_promotion(self):
        with self.payload() as f:
            proof = self.check(f)
            self.assertFalse(proof['pth_executed'])
            self.assertFalse(proof['product_acceptance_passed'])
            self.assertEqual(proof['ordinary_installer_scope_independent_audit'], 'unrun')
            self.assertTrue((f.site / f.prefix / 'INSTALLER').is_file())

    def test_changed_payload_cannot_pass_by_rehashing_installed_snapshot(self):
        with self.payload() as f:
            (f.site / 'startupfixture/__init__.py').write_bytes(b'VALUE = 99\n')
            with self.assertRaises(c.Refusal): self.check(f)

    def test_unknown_pth_customize_code_or_pyc_fails_before_site_activation(self):
        for name in ('unknown.pth', 'sitecustomize.py', 'usercustomize.py', 'extra.py', 'extra.pyc'):
            with self.subTest(name=name), self.payload() as f:
                (f.site / name).write_bytes(b'unproven startup payload')
                with self.assertRaises(c.Refusal): self.check(f)

    def test_changed_uv_hook_is_not_authorized_by_a_stable_inventory(self):
        with self.payload() as f:
            (f.site / '_virtualenv.pth').write_bytes(b'import hostile_hook')
            with self.assertRaises(c.Refusal): self.check(f)

    def test_missing_payload_or_extra_directory_fails(self):
        for change in ('missing', 'directory'):
            with self.subTest(change=change), self.payload() as f:
                if change == 'missing': (f.site / 'startupfixture/__init__.py').unlink()
                else: (f.site / 'unproven_directory').mkdir()
                with self.assertRaises(c.Refusal): self.check(f)

    def test_installer_metadata_is_data_only_and_remains_separate(self):
        with self.payload() as f:
            (f.site / f.prefix / 'RECORD').write_text('ordinary installer serialized metadata\n')
            self.assertEqual(self.check(f)['ordinary_installer_scope_independent_audit'], 'unrun')
            (f.site / f.prefix / 'INSTALLER').chmod(0o755)
            with self.assertRaises(c.Refusal): self.check(f)

    def test_installed_symlink_cannot_replace_source_payload(self):
        with self.payload() as f:
            path=f.site/'startupfixture/__init__.py';path.unlink();path.symlink_to('../_virtualenv.py')
            with self.assertRaises(c.Refusal): self.check(f)

    def test_real_isolated_child_imports_dependency_after_check_without_uv_hook(self):
        with self.payload() as f:
            result = self.child_probe(f)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value['value'], 42)
            self.assertTrue(value['isolated']); self.assertTrue(value['no_site'])
            self.assertFalse(value['uv_hook_executed']);self.assertFalse(value['customize_executed'])
            self.assertEqual(value['metadata_audit'], 'unrun')

    def test_real_sitecustomize_control_runs_but_isolated_rejects_before_hook(self):
        with self.payload() as f:
            marker = f.root / 'site-hook-marker'
            (f.site/'sitecustomize.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("BAD")\n')
            # Both actual children receive the same hook input. The isolated child
            # must ignore PYTHONPATH and refuse the extra payload before execution.
            # The ordinary control must prove which startup module actually ran.
            with mock.patch.dict(os.environ, {'PYTHONPATH': str(f.site)}):
                ordinary=subprocess.run([str(f.venv/'bin/python'),'-B','-c',
                    'import sys; print(sys.modules["sitecustomize"].__file__)'],capture_output=True,check=False)
                self.assertEqual(ordinary.returncode,0);self.assertEqual(marker.read_text(),'BAD');marker.unlink()
                self.assertEqual(Path(ordinary.stdout.decode().strip()), f.site/'sitecustomize.py')
                result=self.child_probe(f)
                self.assertNotEqual(result.returncode,0);self.assertFalse(marker.exists())

    def test_real_matching_timestamp_pyc_is_not_consumed_before_payload_rejection(self):
        import importlib.util
        with self.payload() as f:
            source=f.site/'startupfixture/__init__.py';marker=f.root/'pyc-marker'
            cache=Path(importlib.util.cache_from_source(str(source)));cache.parent.mkdir()
            code=compile('from pathlib import Path\nPath('+repr(str(marker))+').write_text("BAD")\nVALUE=99\n',str(source),'exec')
            info=source.stat()
            cache.write_bytes(importlib.util.MAGIC_NUMBER+struct.pack('<III',0,int(info.st_mtime),info.st_size)+marshal.dumps(code))
            ordinary=subprocess.run([str(f.venv/'bin/python'),'-B','-c','import startupfixture'],capture_output=True,check=False)
            self.assertEqual(ordinary.returncode,0);self.assertEqual(marker.read_text(),'BAD');marker.unlink()
            result=self.child_probe(f)
            self.assertNotEqual(result.returncode,0);self.assertFalse(marker.exists())

    def test_native_driver_and_generic_argv_have_real_isolation_flags(self):
        ctx=self.context();python=str(Path(sys.executable).absolute());driver=str(ctx.repo/'som-rs/scripts/hosted_readiness/run_full_products.py')
        direct=c.Context.python_argv(ctx,[python,'-B',driver,'--worker-context','context.json'])
        self.assertEqual(direct,[*self.bootstrap_prefix(python,ctx),'--worker-context','context.json'])
        generic=c.Context.python_argv(ctx,[python,'-m','unittest','discover'])
        self.assertEqual(generic,[*self.bootstrap_prefix(python,ctx),'--native-python-bootstrap','--','-m','unittest','discover'])
        self.assertEqual(c.Context.python_argv(ctx,generic),generic)

    def test_historical_and_preparation_commands_remain_exact(self):
        argv=['/old/python','-B','old.py','argument']
        for mode in ('historical','experimental-preparation'):
            self.assertEqual(c.Context.python_argv(self.context(mode),argv),argv)

    def test_unknown_or_unsupported_python_startup_is_not_silently_enabled(self):
        ctx=self.context()
        with self.assertRaises(c.Refusal):c.Context.python_argv(ctx,['python3','-m','unittest'])
        with self.assertRaises(c.Refusal):c.Context.python_argv(ctx,[str(Path(sys.executable).absolute()),'-X','unknown'])

    def test_source_authority_must_precede_explicit_package_path(self):
        called=[]
        ctx=SimpleNamespace(qa_mode='native-qa-v1',qa_source_authority=None,
            python_payload=lambda executable:called.append(executable),cache={})
        with self.assertRaises(c.Refusal):c.Context.activate_python_paths(ctx,sys.executable)
        self.assertEqual(called,[])

    def test_external_venv_requires_specific_payload_proof_without_observer_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);work=root/'work';work.mkdir();venv=root/'external/.venv';(venv/'bin').mkdir(parents=True)
            python=venv/'bin/python';python.symlink_to(Path(sys.executable).resolve())
            runtime={'tools':{'python':Path(sys.executable).resolve()},'inventory':{},'qualification':{},
                     'reuse':SimpleNamespace(verify_installed_tree=lambda *args:None)}
            ctx=SimpleNamespace(qa_mode='native-qa-v1',qa_import_controls=SimpleNamespace(verify_installed=lambda:None),
                runtime=lambda:runtime,data={'runtime_root':str(root/'runtime')},work=work)
            with self.assertRaisesRegex(c.Refusal,'own exact source-bound wheel/uv payload proof'):
                c.Context.python_payload(ctx,python)

    def test_explicit_existing_isolation_is_kept_and_version_uses_no_site(self):
        ctx=self.context();python=str(Path(sys.executable).absolute())
        got=c.Context.python_argv(ctx,[python,'-I','-S','-B','-m','unittest'])
        self.assertEqual(got[1:4],['-I','-S','-B'])
        self.assertEqual(got[-2:],['-m','unittest'])
        self.assertEqual(c.Context.python_argv(ctx,[python,'--version']),[python,'-I','-S','-B','--version'])

    def test_non_python_native_tools_do_not_get_a_python_bootstrap(self):
        argv=['/qualified/bin/node','tool.mjs','argument']
        self.assertEqual(c.Context.python_argv(self.context(),argv),argv)

    def test_actual_popen_args_are_retained_and_guard_has_owned_teardown(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);python=str(Path(sys.executable).absolute());requested=[python,'-c','print(7)']
            actual=[python,'-I','-S','-B','-c','print(7)']
            ctx=SimpleNamespace(python_argv=lambda argv,cwd=None:actual if list(argv)==requested else list(argv),
                work=root,helper_revision='1'*40,candidate_revision='1'*40)
            original=subprocess.Popen
            controls=c.NativePythonControls(ctx)
            try:
                child=subprocess.Popen(requested,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
                self.assertIsInstance(child,subprocess.Popen)
                out,err=child.communicate(timeout=20)
                self.assertEqual(child.returncode,0,err);self.assertEqual(out,b'7\n')
                self.assertEqual(child.args,actual)
                path,proof=child.__som_native_startup_receipt__
                self.assertEqual(proof['actual_argv'],actual);self.assertEqual(proof['original_requested_argv'],requested)
                self.assertEqual(json.loads(path.read_bytes()),proof)
                controls.verify_installed()
            finally:controls.close()
            self.assertIs(subprocess.Popen,original)

    def test_borrowed_python_lease_cannot_remove_active_context_controls(self):
        ctx=SimpleNamespace(path=Path('/same/context.json'),raw=b'same',python_argv=lambda argv,cwd=None:list(argv))
        original=subprocess.Popen
        owner=c.install_python_controls(ctx)
        try:
            borrowed=c.install_python_controls(ctx)
            self.assertTrue(owner.owns_controls);self.assertFalse(borrowed.owns_controls)
            borrowed.close();owner.verify_installed()
            self.assertIs(subprocess.Popen,owner.controls.wrapper)
        finally:owner.close()
        self.assertIs(subprocess.Popen,original)

    def test_another_context_cannot_borrow_existing_python_guard(self):
        ctx=SimpleNamespace(path=Path('/one/context.json'),raw=b'one',python_argv=lambda argv,cwd=None:list(argv))
        owner=c.install_python_controls(ctx)
        try:
            other=SimpleNamespace(path=Path('/other/context.json'),raw=b'other')
            with self.assertRaises(c.Refusal):c.install_python_controls(other)
            owner.verify_installed()
        finally:owner.close()

    def test_python_shell_or_executable_override_is_refused_before_process(self):
        with tempfile.TemporaryDirectory() as d:
            ctx=SimpleNamespace(python_argv=lambda argv,cwd=None:[str(Path(sys.executable).absolute()),'-I','-S','-B','-c','pass'],
                work=Path(d),helper_revision='1'*40,candidate_revision='1'*40)
            controls=c.NativePythonControls(ctx)
            try:
                for kwargs in ({'shell':True},{'executable':'/bin/false'}):
                    with self.subTest(kwargs=kwargs),self.assertRaises(c.Refusal):
                        subprocess.Popen([str(Path(sys.executable).absolute()),'-c','pass'],**kwargs)
            finally:controls.close()

    def test_product_owner_uses_actual_bootstrap_tail_and_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);directory=root/'python-child-startups';directory.mkdir();path=directory/'1-7.json'
            requested=['/owned/.venv/bin/python','-m','uvicorn','app.app:app','--host','127.0.0.1','--port','18001']
            actual=[*self.bootstrap_prefix(requested[0],self.context()),'--native-python-bootstrap','--',*requested[1:]]
            proof={'pid':7,'actual_argv':actual,'original_requested_argv':requested,'helper_revision':'1'*40,'candidate_revision':'1'*40}
            path.write_bytes(c.canonical(proof))
            process=SimpleNamespace(pid=7,args=actual,__som_native_startup_receipt__=(path,proof))
            ctx=SimpleNamespace(qa_mode='native-qa-v1',work=root,helper_revision='1'*40,candidate_revision='1'*40,
                python_argv=lambda argv:actual)
            self.assertTrue(c.Context.python_is_product_process(ctx,process,requested[0],'app'))
            self.assertEqual(process.args,actual)
            with self.assertRaises(c.Refusal):c.Context.python_is_product_process(ctx,process,requested[0],'foreign')
            process.args=[*actual[:-1],'8765']
            with self.assertRaises(c.Refusal):c.Context.python_is_product_process(ctx,process,requested[0],'app')

    def test_exact_source_bootstrap_prefix_is_idempotent_and_reads_both_actual_sources(self):
        ctx=self.context();python=str(Path(sys.executable).absolute());reads=[]
        read=ctx.sources.read
        ctx.sources.read=lambda name:(reads.append(name),read(name))[1]
        argv=[*self.bootstrap_prefix(python,ctx),'--native-python-bootstrap','--','-c','pass']
        self.assertEqual(c.Context.python_argv(ctx,argv),argv)
        self.assertEqual(reads,['som-rs/scripts/hosted_readiness/run_full_products.py',
            'som-rs/scripts/hosted_readiness/native_source_bootstrap.py'])

    def test_wrong_bootstrap_source_role_or_parameters_refuse_without_rewrapping(self):
        ctx=self.context();python=str(Path(sys.executable).absolute())
        good=[*self.bootstrap_prefix(python,ctx),'--worker-context','context.json']
        for index,value in ((4,'/foreign/native_source_bootstrap.py'),(4,'/foreign/other_bootstrap.py'),(6,'/foreign/repo'),
                (8,'2'*40),(10,'2'*40),(12,'entry.foreign'),(13,'--wrong-separator'),(1,'-B')):
            with self.subTest(index=index,value=value):
                bad=[*good];bad[index]=value
                with self.assertRaisesRegex(c.Refusal,'bootstrap prefix differs'):
                    c.Context.python_argv(ctx,bad)
        with self.assertRaisesRegex(c.Refusal,'bootstrap prefix differs'):
            c.Context.python_argv(ctx,self.bootstrap_prefix(python,ctx))

    def test_direct_native_tool_and_old_bootstrap_tails_keep_their_driver_semantics(self):
        ctx=self.context();python=str(Path(sys.executable).absolute())
        driver=str(ctx.repo/'som-rs/scripts/hosted_readiness/run_full_products.py')
        for tail in (['--execute-tool','checker.py','--','argument'],
                     ['--native-python-bootstrap','--','-m','uvicorn','app.app:app']):
            with self.subTest(tail=tail):
                self.assertEqual(c.Context.python_argv(ctx,[python,'-I','-S','-B',driver,*tail]),
                    [*self.bootstrap_prefix(python,ctx),*tail])

    def test_bootstrap_requires_actual_matching_context_source_before_launch(self):
        python=str(Path(sys.executable).absolute())
        for field,value in (('helper_revision','short'),('candidate_revision','2'*40)):
            ctx=self.context();setattr(ctx,field,value)
            with self.subTest(field=field),self.assertRaisesRegex(c.Refusal,'one exact Context source'):
                c.Context.python_argv(ctx,[python,'-c','pass'])
        ctx=self.context();ctx.sources.revision='2'*40
        with self.assertRaisesRegex(c.Refusal,'one exact Context source'):
            c.Context.python_argv(ctx,[python,'-c','pass'])


    def test_already_isolated_bootstrap_and_native_entry_overrides_are_refused_before_start(self):
        python=str(Path(sys.executable).absolute());calls=[]
        class ForbiddenStart:
            def __init__(child,*args,**kwargs):
                calls.append((args,kwargs));raise AssertionError('original Popen reached')
        with tempfile.TemporaryDirectory() as d:
            ctx=SimpleNamespace(python_argv=lambda argv,cwd=None:list(argv),work=Path(d),
                                helper_revision='1'*40,candidate_revision='1'*40)
            controls=c.NativePythonControls(ctx)
            try:
                controls.original=ForbiddenStart
                for tail in (['--native-python-bootstrap','--','-c','pass'],['--execute-tool','phase2'],['--worker-context','context.json']):
                    argv=[python,'-I','-S','-B','/repo/run_full_products.py',*tail]
                    for kwargs in ({'shell':True},{'executable':'/bin/false'}):
                        with self.subTest(tail=tail,kwargs=kwargs),self.assertRaisesRegex(c.Refusal,'shell or executable'):
                            subprocess.Popen(argv,**kwargs)
                self.assertEqual(calls,[])
            finally:
                controls.original=controls.wrapper.__mro__[1];controls.close()

    def test_python_positional_popen_options_are_refused_before_start(self):
        python=str(Path(sys.executable).absolute())
        ctx=SimpleNamespace(python_argv=lambda argv,cwd=None:list(argv))
        controls=c.NativePythonControls(ctx)
        try:
            argv=[python,'-I','-S','-B','/repo/run_full_products.py','--native-python-bootstrap','--','-c','pass']
            for options in ((-1,),(-1,'/bin/false'),(-1,None,None,None,None,None,True,True)):
                with self.subTest(options=options),self.assertRaisesRegex(c.Refusal,'keyword Popen options'):
                    subprocess.Popen(argv,*options)
        finally:controls.close()

    def test_nonpython_ordinary_popen_options_keep_original_semantics(self):
        ctx=SimpleNamespace(python_argv=lambda argv,cwd=None:list(argv))
        controls=c.NativePythonControls(ctx)
        try:
            child=subprocess.Popen(['/bin/sh','-c','printf native-control'], -1, stdout=subprocess.PIPE)
            out,_=child.communicate(timeout=20)
            self.assertEqual(child.returncode,0);self.assertEqual(out,b'native-control')
        finally:controls.close()

    @contextmanager
    def receipt_failure(self, *, signal_error=None, wait_errors=()):
        """Use one real short child; inject only the receipt/signal/wait failure."""
        with tempfile.TemporaryDirectory() as d:
            python=str(Path(sys.executable).absolute());requested=[python,'-c','import time;time.sleep(.15)']
            actual=[python,'-I','-S','-B',*requested[1:]]
            ctx=SimpleNamespace(python_argv=lambda argv,cwd=None:actual,work=Path(d),
                                helper_revision='1'*40,candidate_revision='1'*40)
            original=subprocess.Popen;wait=original.wait;canonical=c.canonical
            primary=OSError('original receipt failure');children=[];waits=[];signals=[]
            def fail_receipt(value):
                if value.get('version')=='som.native-python-child-startup.v1':raise primary
                return canonical(value)
            def tracked_wait(child,*args,**kwargs):
                children.append(child);waits.append((args,kwargs))
                if len(waits)<=len(wait_errors):raise wait_errors[len(waits)-1]
                return wait(child,*args,**kwargs)
            def failed_signal(pid,sig):
                signals.append((pid,sig))
                raise signal_error or ProcessLookupError('fast exit before signal')
            controls=c.NativePythonControls(ctx)
            try:
                with mock.patch.object(c,'canonical',side_effect=fail_receipt), \
                     mock.patch.object(original,'wait',tracked_wait), \
                     mock.patch.object(original,'poll',return_value=None), \
                     mock.patch.object(c.os,'killpg',side_effect=failed_signal):
                    with self.assertRaises(OSError) as caught:
                        subprocess.Popen(requested,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,start_new_session=True)
                self.assertIs(caught.exception,primary)
                proof=primary.__som_native_startup_failure__
                self.assertIs(proof,controls.startup_failures[-1])
                self.assertIs(primary.__som_native_startup_child__,children[-1])
                saved=json.loads(Path(proof['failure_evidence_path']).read_bytes())
                self.assertEqual(saved,proof)
                yield SimpleNamespace(proof=proof,children=children,waits=waits,signals=signals,primary=primary)
            finally:
                controls.close()
                for child in {child.pid:child for child in children}.values():
                    # Fixture cleanup is separate from the failed subject record.
                    wait(child,timeout=20)

    def test_receipt_failure_fast_exit_signal_error_still_reaps_real_child_and_keeps_primary(self):
        with self.receipt_failure() as f:
            self.assertEqual(len(f.waits),1);self.assertTrue(f.proof['wait_completed'])
            self.assertEqual(f.proof['exit_code'],0);self.assertTrue(f.proof['owned_child_reaped'])
            self.assertEqual([row['stage'] for row in f.proof['attempts']],['poll','SIGTERM','wait-after-stop'])
            self.assertEqual(f.proof['attempts'][1]['error']['type'],'ProcessLookupError')
            self.assertEqual(f.proof['primary_error']['message'],'original receipt failure')
            with self.assertRaises(ChildProcessError):os.waitpid(f.proof['pid'],os.WNOHANG)

    def test_receipt_failure_timeout_and_escalation_signal_error_always_attempt_terminal_wait(self):
        with self.receipt_failure(signal_error=PermissionError('signal refused'),
                wait_errors=(subprocess.TimeoutExpired('owned-child',.001),)) as f:
            self.assertEqual(len(f.waits),2);self.assertTrue(f.proof['wait_completed'])
            self.assertEqual([sig for _,sig in f.signals],[c.signal.SIGTERM,c.signal.SIGKILL])
            self.assertEqual([row['stage'] for row in f.proof['attempts']],
                ['poll','SIGTERM','wait-after-stop','SIGKILL','wait-after-escalation'])
            self.assertEqual(f.proof['attempts'][2]['error']['type'],'TimeoutExpired')
            self.assertEqual(f.proof['attempts'][3]['error']['type'],'PermissionError')
            self.assertEqual(f.proof['primary_error']['type'],'OSError')

    def test_receipt_failure_unreaped_child_stays_failed_with_primary_and_secondary_errors(self):
        with self.receipt_failure(wait_errors=(OSError('first wait failure'),OSError('last wait failure'))) as f:
            self.assertEqual(len(f.waits),2);self.assertFalse(f.proof['wait_completed'])
            self.assertFalse(f.proof['cleanup_passed']);self.assertFalse(f.proof['owned_child_reaped'])
            self.assertIsNone(f.proof['exit_code'])
            self.assertEqual(f.proof['primary_error']['message'],'original receipt failure')
            self.assertEqual(f.proof['attempts'][-1]['error']['message'],'last wait failure')

    def test_receipt_failure_stops_and_waits_real_owned_child_positive_control(self):
        with tempfile.TemporaryDirectory() as d:
            python=str(Path(sys.executable).absolute());requested=[python,'-c','import time;time.sleep(20)']
            actual=[python,'-I','-S','-B',*requested[1:]]
            ctx=SimpleNamespace(python_argv=lambda argv,cwd=None:actual,work=Path(d),
                helper_revision='1'*40,candidate_revision='1'*40)
            canonical=c.canonical;primary=OSError('receipt positive control')
            def failure(value):
                if value.get('version')=='som.native-python-child-startup.v1':raise primary
                return canonical(value)
            controls=c.NativePythonControls(ctx)
            try:
                with mock.patch.object(c,'canonical',side_effect=failure),self.assertRaises(OSError) as caught:
                    subprocess.Popen(requested,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,start_new_session=True)
                self.assertIs(caught.exception,primary)
                proof=primary.__som_native_startup_failure__
                self.assertTrue(proof['wait_completed']);self.assertTrue(proof['owned_child_reaped'])
                self.assertIs(type(proof['exit_code']),int)
                self.assertNotEqual(proof['exit_code'],0)
                with self.assertRaises(ChildProcessError):os.waitpid(proof['pid'],os.WNOHANG)
            finally:controls.close()

    def test_failed_cleanup_evidence_write_cannot_replace_receipt_error_or_skip_wait(self):
        with tempfile.TemporaryDirectory() as d:
            python=str(Path(sys.executable).absolute());requested=[python,'-c','import time;time.sleep(.1)']
            actual=[python,'-I','-S','-B',*requested[1:]]
            ctx=SimpleNamespace(python_argv=lambda argv,cwd=None:actual,work=Path(d),
                helper_revision='1'*40,candidate_revision='1'*40)
            primary=OSError('original receipt write');secondary=OSError('failure evidence write')
            def failure(value):
                if value['version']=='som.native-python-child-startup.v1':raise primary
                raise secondary
            controls=c.NativePythonControls(ctx)
            try:
                with mock.patch.object(c,'canonical',side_effect=failure),self.assertRaises(OSError) as caught:
                    subprocess.Popen(requested,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,start_new_session=True)
                self.assertIs(caught.exception,primary)
                proof=primary.__som_native_startup_failure__
                self.assertTrue(proof['wait_completed'])
                self.assertEqual(proof['evidence_write_error']['message'],'failure evidence write')
                with self.assertRaises(ChildProcessError):os.waitpid(proof['pid'],os.WNOHANG)
            finally:controls.close()

    @contextmanager
    def command_protocol(self, mode='native-qa-v1', rewrite=None):
        """Real Popen/waits with explicit source/payload doubles; not admission."""
        with tempfile.TemporaryDirectory() as directory:
            ctx=self.context(mode);ctx.work=Path(directory);ctx.cache={};ctx.command_lock=threading.RLock()
            ctx.module=lambda key,path:w
            calls=[]
            def route(argv,cwd=None):
                calls.append(list(argv))
                return list(argv) if rewrite is None else rewrite(list(argv),cwd)
            ctx.python_argv=route
            original=subprocess.Popen
            controls=c.NativePythonControls(ctx) if mode=='native-qa-v1' else None
            ctx.python_controls=c.NativePythonLease(controls,False) if controls is not None else None
            try:
                yield SimpleNamespace(ctx=ctx,root=ctx.work,calls=calls,controls=controls,original=original)
            finally:
                if controls is not None:controls.close()
                self.assertIs(subprocess.Popen,original)

    def test_command_keeps_first_request_and_records_real_guard_argv_and_receipt(self):
        python=str(Path(sys.executable).absolute());requested=[python,'-c','print(17)']
        actual=[python,'-I','-S','-B',*requested[1:]]
        with self.command_protocol(rewrite=lambda argv,cwd:actual) as f:
            result=c.Context.command(f.ctx,requested,f.root,dict(os.environ),'first-request',timeout=20)
            self.assertEqual(f.calls,[requested,requested])
            self.assertEqual(result['original_requested_argv'],requested)
            self.assertEqual(result['expected_argv'],actual);self.assertEqual(result['argv'],actual)
            self.assertEqual(result['exit_code'],0);self.assertTrue(result['wait_completed'])
            self.assertTrue(result['owned_group_cleanup_passed'])
            self.assertEqual((f.root/result['stdout']).read_bytes(),b'17\n')
            path=f.root/'python-child-startups'/f'{os.getpid()}-{result["pid"]}.json'
            proof=json.loads(path.read_bytes())
            self.assertEqual(proof['pid'],result['pid']);self.assertEqual(proof['actual_argv'],result['argv'])
            self.assertEqual(proof['original_requested_argv'],requested)
            with self.assertRaises(ChildProcessError):os.waitpid(result['pid'],os.WNOHANG)

    def test_command_missing_foreign_or_removed_guard_refuses_before_any_python_start(self):
        python=str(Path(sys.executable).absolute());calls=[]
        def forbidden(*args,**kwargs):
            calls.append((args,kwargs));raise AssertionError('Popen must not be called')
        class ForeignLease:
            def verify_installed(self):
                calls.append('foreign lease');raise AssertionError('foreign lease must not run')
        with self.command_protocol() as f:
            lease=f.ctx.python_controls
            for invalid in (None,ForeignLease()):
                f.ctx.python_controls=invalid
                with self.subTest(invalid=type(invalid).__name__),self.assertRaisesRegex(c.Refusal,'active guard lease'):
                    c.Context.command(f.ctx,[python,'-c','pass'],f.root,dict(os.environ),'missing-guard',timeout=20)
            f.ctx.python_controls=lease
            with mock.patch.object(subprocess,'Popen',side_effect=forbidden):
                with self.assertRaisesRegex(c.Refusal,'startup guard changed'):
                    c.Context.command(f.ctx,[python,'-c','pass'],f.root,dict(os.environ),'removed-guard',timeout=20)
            self.assertEqual(calls,[]);self.assertNotIn('commands',f.ctx.cache)

    def test_idempotent_direct_and_command_python_launches_have_real_startup_proofs(self):
        python=str(Path(sys.executable).absolute());actual=[python,'-I','-S','-B','-c','print(23)']
        with self.command_protocol() as f:
            child=subprocess.Popen(actual,cwd=f.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            out,err=child.communicate(timeout=20)
            self.assertEqual(child.returncode,0,err);self.assertEqual(out,b'23\n')
            path,proof=child.__som_native_startup_receipt__
            self.assertEqual(proof['pid'],child.pid);self.assertEqual(proof['actual_argv'],child.args)
            self.assertEqual(proof['original_requested_argv'],actual);self.assertEqual(json.loads(path.read_bytes()),proof)
            result=c.Context.command(f.ctx,actual,f.root,dict(os.environ),'idempotent',timeout=20)
            self.assertEqual(result['argv'],actual);self.assertEqual(result['original_requested_argv'],actual)
            proof=json.loads((f.root/'python-child-startups'/f'{os.getpid()}-{result["pid"]}.json').read_bytes())
            self.assertEqual(proof['actual_argv'],actual);self.assertEqual(proof['original_requested_argv'],actual)
            self.assertTrue(result['wait_completed']);self.assertTrue(result['owned_group_cleanup_passed'])

    def test_command_second_rewrite_mismatch_reaps_actual_child_and_keeps_both_vectors(self):
        python=str(Path(sys.executable).absolute());requested=[python,'-c','print(31)']
        expected=[python,'-I','-S','-B',*requested[1:]]
        actual=[python,'-I','-S','-B','-c','import time;time.sleep(20)'];calls=[]
        def differing(argv,cwd):
            calls.append(argv)
            return expected if len(calls)==1 else actual
        with self.command_protocol(rewrite=differing) as f:
            with self.assertRaisesRegex(c.Refusal,'command actual argv differs'):
                c.Context.command(f.ctx,requested,f.root,dict(os.environ),'mismatch',timeout=20)
            result=f.ctx.cache['commands'][-1]
            self.assertEqual(result['original_requested_argv'],requested)
            self.assertEqual(result['expected_argv'],expected);self.assertEqual(result['argv'],actual)
            self.assertTrue(result['wait_completed']);self.assertTrue(result['owned_group_cleanup_passed'])
            self.assertNotEqual(result['exit_code'],0)
            proof=json.loads((f.root/'python-child-startups'/f'{os.getpid()}-{result["pid"]}.json').read_bytes())
            self.assertEqual(proof['actual_argv'],actual);self.assertEqual(proof['original_requested_argv'],requested)
            with self.assertRaises(ChildProcessError):os.waitpid(result['pid'],os.WNOHANG)

    def test_guard_wrong_actual_vector_records_real_args_and_reaps_without_mutating_args(self):
        python=str(Path(sys.executable).absolute());requested=[python,'-c','print(37)']
        expected=[python,'-I','-S','-B',*requested[1:]];actual=[python,'-I','-S','-B','-c','pass']
        with self.command_protocol(rewrite=lambda argv,cwd:expected) as f:
            original=f.original
            class DifferentActual(original):
                def __init__(child,argv,**keywords):
                    original.__init__(child,actual,**keywords)
            f.controls.original=DifferentActual
            try:
                with self.assertRaisesRegex(c.Refusal,'Popen actual argv differs') as caught:
                    c.Context.command(f.ctx,requested,f.root,dict(os.environ),'wrong-real-vector',timeout=20)
                child=caught.exception.__som_native_startup_child__
                proof=caught.exception.__som_native_startup_failure__
                self.assertEqual(child.args,actual);self.assertEqual(proof['actual_argv'],actual)
                self.assertEqual(proof['original_requested_argv'],requested)
                self.assertTrue(proof['wait_completed']);self.assertTrue(proof['owned_child_reaped'])
                self.assertIsNone(f.ctx.cache['commands'][-1]['argv'])
                with self.assertRaises(ChildProcessError):os.waitpid(child.pid,os.WNOHANG)
            finally:
                f.controls.original=original

    def test_command_receipt_failure_preserves_first_error_and_actual_terminal_wait(self):
        python=str(Path(sys.executable).absolute());requested=[python,'-c','import time;time.sleep(20)']
        actual=[python,'-I','-S','-B',*requested[1:]];primary=OSError('command actual receipt failure')
        canonical=c.canonical
        def failure(value):
            if value.get('version')=='som.native-python-child-startup.v1':raise primary
            return canonical(value)
        with self.command_protocol(rewrite=lambda argv,cwd:actual) as f:
            with mock.patch.object(c,'canonical',side_effect=failure),self.assertRaises(OSError) as caught:
                c.Context.command(f.ctx,requested,f.root,dict(os.environ),'receipt-failure',timeout=20)
            self.assertIs(caught.exception,primary)
            child=primary.__som_native_startup_child__;proof=primary.__som_native_startup_failure__
            self.assertEqual(proof['actual_argv'],child.args);self.assertEqual(proof['original_requested_argv'],requested)
            self.assertTrue(proof['wait_completed']);self.assertTrue(proof['owned_child_reaped'])
            self.assertNotEqual(proof['exit_code'],0)
            result=f.ctx.cache['commands'][-1]
            self.assertIsNone(result['argv']);self.assertEqual(result['expected_argv'],actual)
            self.assertFalse(result['wait_completed'])
            self.assertFalse(result.get('owned_group_cleanup_passed',False))
            with self.assertRaises(ChildProcessError):os.waitpid(child.pid,os.WNOHANG)

    def test_real_version_nonpython_and_historical_commands_keep_their_original_semantics(self):
        python=str(Path(sys.executable).absolute())
        with self.command_protocol() as f:
            f.ctx.python_argv=lambda argv,cwd=None:c.Context.python_argv(f.ctx,argv,cwd)
            result=c.Context.command(f.ctx,[python,'--version'],f.root,dict(os.environ),'version',timeout=20)
            self.assertEqual(result['argv'],[python,'-I','-S','-B','--version'])
            proof=json.loads((f.root/'python-child-startups'/f'{os.getpid()}-{result["pid"]}.json').read_bytes())
            self.assertEqual(proof['original_requested_argv'],[python,'--version'])
            self.assertEqual(proof['actual_argv'],result['argv'])
        with self.command_protocol() as f:
            argv=['/bin/sh','-c','printf unchanged-nonpython']
            result=c.Context.command(f.ctx,argv,f.root,dict(os.environ),'nonpython',timeout=20)
            self.assertEqual(result['argv'],argv);self.assertNotIn('original_requested_argv',result)
            self.assertNotIn('expected_argv',result);self.assertFalse((f.root/'python-child-startups').exists())
        with self.command_protocol(mode='historical') as f:
            argv=[python,'-c','print("historical")']
            result=c.Context.command(f.ctx,argv,f.root,dict(os.environ),'historical',timeout=20)
            self.assertEqual(result['argv'],argv);self.assertNotIn('original_requested_argv',result)
            self.assertNotIn('expected_argv',result);self.assertFalse((f.root/'python-child-startups').exists())
            self.assertEqual((f.root/result['stdout']).read_bytes(),b'historical\n')

    @contextmanager
    def api_provider_fixture(self):
        """Synthetic owner interface only. It does not qualify the real M6 venv."""
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo=root/'repo';repo.mkdir();work=root/'work';work.mkdir()
            base=root/'qualified/python3.12';base.parent.mkdir();base.write_bytes(b'fixture interpreter bytes')
            venv=root/'som-react-python-closure/environments/react-api-qa/venv'
            (venv/'bin').mkdir(parents=True);python=venv/'bin/python';python.symlink_to(base)
            site=venv/'lib/python3.12/site-packages';site.mkdir(parents=True)
            source=b'# synthetic bridge owner fixture\n';calls=[];loads=[]
            class AdmittedPayload(tuple):
                def record(self):return dict(self[0])
            proof={'version':'som.native-python-startup-payload.v1','role':'react-api-qa','python':str(python),
                'site':str(site),'base_python':str(base),'requirements_count':39,'wheel_count':40,
                'source_revision':'1'*40,'pth_executed':False,'product_acceptance_passed':False,
                **{key:'2'*64 for key in ('api_profile_sha256','api_authority_sha256','live_receipt_sha256',
                    'runtime_receipt_sha256','closure_receipt_sha256','venv_inventory_sha256')}}
            provider=SimpleNamespace(AdmittedPayload=AdmittedPayload,__som_raw_sha256__=c.digest(source),
                __file__=str(repo/c.REACT_API_PAYLOAD))
            def admit(ctx,executable):calls.append((ctx,executable));return AdmittedPayload((tuple(proof.items()),))
            provider.admit=admit
            def module(key,path):loads.append((key,path));return provider
            runtime={'tools':{'python':base},'inventory':{},'qualification':{},
                'reuse':SimpleNamespace(verify_installed_tree=lambda *args:None)}
            ctx=SimpleNamespace(qa_mode='native-qa-v1',qa_import_controls=SimpleNamespace(verify_installed=lambda:None),
                qa_source_authority={'source_authority_active':True,'admitted_repository_python_sources':{c.REACT_API_PAYLOAD}},
                sources=SimpleNamespace(read=lambda path:source),repo=repo,work=work,module=module,runtime=lambda:runtime,
                data={'runtime_root':str(root/'qualified')},helper_revision='1'*40,candidate_revision='1'*40,
                command_lock=threading.Lock(),cache={})
            ctx.react_api_python_payload=lambda python,base,record=True:c.Context.react_api_python_payload(ctx,python,base,record=record)
            with mock.patch.dict(os.environ,{'RUNNER_TEMP':str(root)}):
                yield SimpleNamespace(ctx=ctx,python=python,base=base,proof=proof,provider=provider,
                    calls=calls,loads=loads,root=root,source=source,runtime=runtime)

    def test_exact_api_bridge_calls_admitted_owner_itself_and_preserves_false_acceptance(self):
        with self.api_provider_fixture() as f:
            proof=c.Context.python_payload(f.ctx,f.python)
            self.assertEqual(f.calls,[(f.ctx,f.python)])
            self.assertEqual(f.loads,[('react_api_python_payload',c.REACT_API_PAYLOAD)])
            self.assertEqual(proof['provider_source_sha256'],c.digest(f.source))
            self.assertFalse(proof['pth_executed']);self.assertFalse(proof['product_acceptance_passed'])
            self.assertEqual(proof['ordinary_installer_scope_independent_audit'],'unrun')
            self.assertEqual(proof['base_python_inode']['inode'],f.base.stat().st_ino)

    def test_api_bridge_refuses_provider_outside_authority_before_load_or_admit(self):
        with self.api_provider_fixture() as f:
            f.ctx.qa_source_authority['admitted_repository_python_sources']=set()
            with self.assertRaisesRegex(c.Refusal,'outside sealed native source authority'):
                c.Context.python_payload(f.ctx,f.python)
            self.assertEqual(f.calls,[]);self.assertEqual(f.loads,[])

    def test_api_bridge_refuses_supplied_plain_record_or_foreign_immutable_type(self):
        with self.api_provider_fixture() as f:
            for result in (f.proof,tuple(f.proof.items())):
                f.provider.admit=lambda ctx,executable:result
                with self.subTest(result=type(result).__name__),self.assertRaisesRegex(c.Refusal,'exact immutable AdmittedPayload'):
                    c.Context.python_payload(f.ctx,f.python)

    def test_api_bridge_rejects_bad_schema_roles_hashes_counts_source_hooks_or_acceptance(self):
        changes=({'role':'observer'},{'wheel_count':39},{'requirements_count':True},{'site':'/arbitrary/site'},
                 {'source_revision':'3'*40},{'pth_executed':True},{'product_acceptance_passed':True},
                 {'closure_receipt_sha256':'not-a-digest'},{'extra':'caller-proof'})
        for change in changes:
            with self.subTest(change=change),self.api_provider_fixture() as f:
                f.proof.update(change)
                with self.assertRaisesRegex(c.Refusal,'record schema or exact source identity'):
                    c.Context.python_payload(f.ctx,f.python)

    def test_api_bridge_rejects_changed_raw_module_and_base_bytes_during_admission(self):
        with self.api_provider_fixture() as f:
            f.provider.__som_raw_sha256__='3'*64
            with self.assertRaisesRegex(c.Refusal,'raw source module differs'):c.Context.python_payload(f.ctx,f.python)
            self.assertEqual(f.calls,[])
        with self.api_provider_fixture() as f:
            original=f.provider.admit
            def changed(ctx,python):
                value=original(ctx,python);f.base.write_bytes(b'changed interpreter');return value
            f.provider.admit=changed
            with self.assertRaisesRegex(c.Refusal,'changed during admission'):c.Context.python_payload(f.ctx,f.python)

    def test_api_bridge_does_not_hide_actual_provider_runtime_root_refusal(self):
        with self.api_provider_fixture() as f:
            refusal=ValueError('qualified runtime identity differs: actual owner provider root pending')
            def failed(ctx,python):raise refusal
            f.provider.admit=failed
            with self.assertRaises(ValueError) as caught:c.Context.python_payload(f.ctx,f.python)
            self.assertIs(caught.exception,refusal)

    def test_api_bridge_cannot_select_base_outside_actual_context_runtime(self):
        with self.api_provider_fixture() as f:
            alternate=f.root/'different/python3.12';alternate.parent.mkdir();alternate.write_bytes(f.base.read_bytes())
            with self.assertRaisesRegex(c.Refusal,'actual qualified Context interpreter'):
                c.Context.react_api_python_payload(f.ctx,f.python,alternate)
            self.assertEqual(f.calls,[])


if __name__ == '__main__':
    unittest.main()
