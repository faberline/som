#!/usr/bin/env python3
"""Unsealed draft source-bound M6 checker entry; GitHub Linux QA only.

Invoke with the qualified live venv Python and -I -S -B. This entry is stdlib
only until native and React source authorities and live inputs are admitted.
It loads checked Git bytes instead of running a working-tree script with runpy.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import traceback
from types import ModuleType
from types import FunctionType

SELF = 'som-rs/scripts/react_output_acceptance/m6_native_entry.py'
GRAPH = 'som-rs/scripts/hosted_readiness/same_candidate_graph.py'
CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
RETAINED = 'som-rs/scripts/react_output_acceptance/retained_evidence.py'
API_CONTEXT = 'som-rs/scripts/react_output_acceptance/linux_api_context.py'
ROUTER_NAME = '_som_m6_checked_router'
ROUTING_REVIEWED = True

PROVIDER = 'som-rs/scripts/react_output_acceptance/react_api_python_payload.py'
NATIVE_ENTRY_ROLE = 'entry.qa.m6-native'
NATIVE_TARGETS = {
    'som-rs/tests/react-output-v1/check_contract.py': 'entry.m6.react-contract',
    'som-rs/scripts/react_output_acceptance/check_target.py': 'entry.m6.react-target',
    'som-rs/tests/react-output-v1/check_python_controls.py': 'entry.m6.react-python-controls',
    'som-rs/scripts/typescript_output_acceptance/check_python_baseline.py': 'entry.m6.python-baseline',
    'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py': 'entry.m6.rust-baseline',
    'som-rs/tests/rust-output-v1/check_python_controls.py': 'entry.m6.rust-python-controls',
    'som-rs/scripts/typescript_output_acceptance/check_rt_diagnostics.py': 'entry.m6.rt-diagnostics',
    'som-rs/scripts/typescript_output_acceptance/check_target.py': 'entry.m6.ts-target',
    'som-rs/scripts/react_output_acceptance/check_typescript_baseline.py': 'entry.m6.ts-baseline',
    'som-rs/tests/typescript-output-v1/check_python_controls.py': 'entry.m6.ts-python-controls',
    'som-rs/scripts/react_output_acceptance/check_bundles.py': 'entry.m6.bundles',
    'som-rs/scripts/react_output_acceptance/check_products.py': 'entry.m6.products',
    'som-rs/scripts/react_output_acceptance/record_http.py': 'entry.m6.record-http',
    'som-rs/tests/react-output-v1/run_gates.py': 'entry.m6.product-gates',
    'som-rs/scripts/react_output_acceptance/record_api.py': 'entry.m6.record-api',
    'som-rs/tests/react-output-v1/run_api_contract.py': 'entry.m6.api-contract',
}


def _native_route():
    if '__som_native_active_controls__' in globals():
        return True
    try:
        from _som_native_active_controls_binding import controls as _probe
    except ModuleNotFoundError as error:
        need(error.name == '_som_native_active_controls_binding'
             and '_som_native_active_controls' not in sys.modules, 'native M6 entry binding is missing')
        return False
    raise ValueError('native M6 entry binding is missing')


def _native_context():
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    need(controls.role_sources.get(NATIVE_ENTRY_ROLE) == SELF and type(__file__) is str
         and __file__ == str(controls.root / SELF) and type(__som_raw_sha256__) is str
         and sha(read(controls.root / SELF)) == __som_raw_sha256__, 'actual fixed M6 entry source required')
    need(type(__som_native_context_module__) is ModuleType, 'actual fixed Context module required')
    namespace = ModuleType.__getattribute__(__som_native_context_module__, '__dict__')
    need(namespace.get('Context') is __som_native_Context__
         and namespace.get('SourceBytes') is __som_native_SourceBytes__
         and namespace.get('context') is __som_native_context_getter__
         and type(namespace.get('__file__')) is str
         and namespace['__file__'] == str(controls.root / CONTEXT), 'fixed Context exports differ')
    ctx = namespace.get('_ACTIVE')
    need(type(ctx) is __som_native_Context__, 'actual active native Context required')
    values = object.__getattribute__(ctx, '__dict__')
    need(type(values) is dict and type(values.get('repo')) is type(controls.root)
         and values['repo'] == controls.root and type(values.get('helper_revision')) is str
         and type(values.get('candidate_revision')) is str
         and values['helper_revision'] == values['candidate_revision'] == controls.helper_revision == controls.candidate_revision
         and type(values.get('qa_mode')) is str and values['qa_mode'] == 'native-qa-v1',
         'actual Context root/revisions/mode differ')
    need(type(values.get('path')) is type(controls.root) and values['path'].is_absolute()
         and type(values.get('raw')) is bytes and read(values['path']) == values['raw']
         and os.environ.get('SOM_FULL_PRODUCT_CONTEXT') == str(values['path']), 'actual Context path/raw differs')
    sources = values.get('sources')
    need(type(sources) is __som_native_SourceBytes__, 'actual SourceBytes class required')
    fields = object.__getattribute__(sources, '__dict__')
    need(type(fields) is dict and set(fields) == {'root', 'revision', 'pins'}
         and type(fields.get('root')) is type(controls.root)
         and fields['root'] == controls.root and type(fields.get('revision')) is str
         and fields['revision'] == controls.helper_revision and type(fields.get('pins')) is dict
         and all(type(path) is str and type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin)
                 for path, pin in fields['pins'].items()), 'actual SourceBytes owner differs')
    lease = values.get('qa_import_controls')
    need(type(lease) is type(controls), 'actual Context source lease required')
    lease.verify_installed()
    need(lease.root == controls.root and lease.helper_revision == controls.helper_revision
         and lease.candidate_revision == controls.candidate_revision, 'Context source lease differs')
    authority = values.get('qa_source_authority')
    need(type(authority) is dict and authority.get('source_authority_active') is True
         and type(authority.get('helper_revision')) is str and type(authority.get('candidate_revision')) is str
         and authority['helper_revision'] == authority['candidate_revision'] == controls.helper_revision
         and type(authority.get('admitted_repository_python_sources')) is list
         and all(type(path) is str for path in authority['admitted_repository_python_sources'])
         and all(authority.get(key) is False for key in ('product_acceptance_passed',
                  'compiler_acceptance_passed', 'whole_acceptance_complete')), 'actual native source authority required')
    need(__som_native_context_getter__() is ctx, 'actual fixed Context getter changed owner')
    return controls, sources, ctx, authority


def _native_bootstrap(repo, revision, candidate, context_path):
    controls, sources, ctx, authority = _native_context()
    need(sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == 1,
         'isolated no-site no-bytecode interpreter required')
    need(type(repo) is type(controls.root) and repo == controls.root and repo.is_absolute()
         and repo.resolve() == repo and not any(p.is_symlink() for p in (repo, *repo.parents)),
         'physical repository required')
    need(type(revision) is str and type(candidate) is str
         and re.fullmatch(r'[0-9a-f]{40}', revision or '') is not None and revision == candidate
         and revision == controls.helper_revision == controls.candidate_revision,
         'same exact helper and candidate required')
    need(type(context_path) is type(repo) and context_path == ctx.path, 'same actual Context path required')
    need(git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision,
         'checked HEAD differs')
    entry = git(repo, 'ls-tree', '-z', revision, '--', GRAPH)
    need(entry.startswith((b'100644 blob ', b'100755 blob ')) and entry.count(b'\0') == 1
         and entry.endswith(b'\t' + GRAPH.encode() + b'\0'), 'regular graph source required')
    raw = git(repo, 'cat-file', 'blob', revision + ':' + GRAPH)
    need(raw == read(repo / GRAPH) == sources.read(GRAPH), 'graph bytes differ')
    need(sources.read(SELF) == read(Path(__file__).absolute()), 'entry source differs')
    required = {SELF, GRAPH, CONTEXT, RETAINED, API_CONTEXT, PROVIDER, *NATIVE_TARGETS}
    need(required <= set(authority['admitted_repository_python_sources']),
         'required entry/bootstrap and finite target sources not admitted')
    for path in sorted(required):
        sources.read(path)
    need(ctx.repo == repo and ctx.qa_mode == 'native-qa-v1' and ctx.qa_source_authority is authority
         and ctx.helper_revision == revision == ctx.candidate_revision, 'native context differs')
    return controls, sources, ctx, authority


def _native_function(module, name, path, sources):
    need(type(module) is ModuleType, 'actual private source module required')
    namespace = ModuleType.__getattribute__(module, '__dict__')
    need(type(namespace.get('__file__')) is str and namespace['__file__'] == str(sources.root / path)
         and type(namespace.get('__som_raw_sha256__')) is str
         and namespace['__som_raw_sha256__'] == sha(sources.read(path)), 'private module source differs')
    function = namespace.get(name)
    need(type(function) is FunctionType and function.__globals__ is namespace
         and function.__code__.co_name == function.__code__.co_qualname == name
         and function.__code__.co_filename == str(sources.root / path), 'original source function required')
    return function


@contextmanager
def _native_live(ctx, controls, sources, repo, profile, before):
    need(type(profile) is type(repo) and type(before) is type(repo), 'actual profile/before path data required')
    proof = __som_native_Context__.python_payload(ctx, sys.executable)
    need(type(proof) is dict and type(proof.get('role')) is str and proof['role'] == 'react-api-qa'
         and type(proof.get('python')) is str and Path(sys.executable).absolute() == Path(proof['python'])
         and type(proof.get('site')) is str and type(proof.get('requirements_count')) is int
         and type(proof.get('wheel_count')) is int and proof['requirements_count'] == 39 and proof['wheel_count'] == 40
         and proof.get('pth_executed') is False and proof.get('product_acceptance_passed') is False,
         'actual admitted39/40 API Python payload required')
    site = Path(proof['site'])
    need(site.is_absolute() and site.is_dir() and not any(p.is_symlink() for p in (site, *site.parents)),
         'physical live site-packages required')
    need(type(__som_native_provider__) is ModuleType
         and controls.role_sources.get('m6.api.provider') == PROVIDER,
         'actual fixed native provider required')
    provider = ModuleType.__getattribute__(__som_native_provider__, '__dict__')
    need(provider.get('_private_helpers') is __som_native_provider_private_helpers__
         and provider.get('admit') is __som_native_provider_admit__
         and provider.get('_NativeOwner') is __som_native_NativeOwner__
         and type(provider.get('__file__')) is str and provider['__file__'] == str(repo / PROVIDER)
         and type(provider.get('__som_raw_sha256__')) is str
         and provider['__som_raw_sha256__'] == sha(sources.read(PROVIDER)), 'native provider source/exports differ')
    controls.verify_installed()
    # Existing provider scope owns private4. Never acquire or close a second group.
    with __som_native_provider_private_helpers__(ctx) as (api, live_verifier, owner):
        need(type(owner) is __som_native_NativeOwner__, 'actual provider owner class required')
        values = object.__getattribute__(owner, '__dict__')
        members = values.get('members') if type(values) is dict else None
        need(type(members) is dict and set(members) == {'retained', 'api', 'live', 'node'}
             and members['api'] is api and members['live'] is live_verifier,
             'actual private helper pointers required')
        validate = _native_function(api, 'validate_case', API_CONTEXT, sources)
        recheck = _native_function(api, 'recheck_case', API_CONTEXT, sources)
        live = validate(profile, repo, before)
        need(type(live) is dict and Path(sys.executable).absolute() == Path(live['python']),
             'live interpreter path differs')
        recheck(live)
        yield api, live, site, members['retained']


def _native_execute(script, arguments, executed):
    controls, sources, ctx, authority = _native_context()
    need(type(script) is str and type(arguments) is list and all(type(value) is str for value in arguments)
         and type(executed) is list, 'finite target argv data required')
    target = Path(script)
    if not target.is_absolute():
        target = ctx.repo / target
    need(target.is_absolute() and '..' not in target.parts and ctx.repo in target.parents,
         'repository checker required')
    relative = target.relative_to(ctx.repo).as_posix()
    need(relative in NATIVE_TARGETS, 'M6 target is outside exact16 entry table')
    need(relative in authority['admitted_repository_python_sources'], 'checker is not in sealed source authority')
    role = NATIVE_TARGETS[relative]
    need(controls.role_sources.get(role) == relative, 'finite M6 target role/source differs')
    raw = sources.read(relative)
    executed.append({'path': relative, 'sha256': sha(raw)})
    return controls.execute_entry(role, arguments)


def _native_case_postcheck(ctx, api, live, retained, sources, repo):
    sources.verify()
    need(ctx.qa_import_controls is not None, 'native raw import controls missing')
    ctx.qa_import_controls.verify_installed()
    need(read(ctx.path) == ctx.raw, 'product context changed')
    _native_function(retained, 'sealed_files', RETAINED, sources)
    need(sources.read(RETAINED) == read(repo / RETAINED), 'retained verifier changed')
    _native_function(api, 'recheck_case', API_CONTEXT, sources)(live)


def _native_run(args):
    need(type(args) is argparse.Namespace, 'parsed M6 CLI data required')
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and
         os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted' and sys.platform == 'linux',
         'GitHub hosted Linux required')
    need(type(args.scope) is str and args.scope in {'step', 'case'}, 'fixed M6 scope required')
    need(all(type(getattr(args, key)) is type(Path()) for key in
             ('repo', 'product_context', 'api_profile', 'full_before', 'postcheck'))
         and type(args.helper_revision) is str and type(args.candidate_revision) is str
         and type(args.script) is str and type(args.arguments) is list
         and all(type(value) is str for value in args.arguments), 'parsed M6 path/argv data required')
    controls, sources, ctx, authority = _native_bootstrap(args.repo, args.helper_revision,
        args.candidate_revision, args.product_context)
    need(ROUTING_REVIEWED, 'M6 routing remains unreviewed')
    need(ROUTER_NAME not in sys.modules, 'preloaded M6 router refused')
    lease, primary, trace, code = controls.borrow(), None, None, 1
    previous_signals, executed, secondary = {}, [], []
    report = {'version': 'som.m6-native-source-entry.v1', 'helper_revision': args.helper_revision,
              'candidate_revision': args.candidate_revision, 'logical_argv': [args.script, *args.arguments],
              'pid': os.getpid(), 'scope': args.scope, 'interpreter': str(Path(sys.executable).absolute()),
              'execution_scope': 'direct finite dispatch; nested wrapper manifests require their own source join',
              'entry_executed': False, 'entry_exit_code': None, 'source_postcheck': False,
              'product_acceptance_passed': False}
    def failure(phase, error):
        nonlocal primary, trace, code
        if phase != 'entry':
            report['source_postcheck'] = False
        if primary is None:
            primary, trace = error, error.__traceback__
            report['error'] = type(error).__name__ + ': ' + str(error)
            report['error_traceback'] = ''.join(traceback.format_exception(type(error), error, trace))
        elif error is not primary:
            secondary.append({'phase': phase, 'error': type(error).__name__ + ': ' + str(error),
                              'traceback': ''.join(traceback.format_exception(type(error), error, error.__traceback__))})
        code = 1
    def interrupted(number, _frame):
        raise KeyboardInterrupt('owned native child interrupted: ' + str(number))
    try:
        lease.verify_installed()
        with _native_live(ctx, controls, sources, ctx.repo, args.api_profile, args.full_before) as (api, live, site, retained):
            report.update(profile_sha256=live['context_sha256'], full_before_sha256=live['full_before_sha256'],
                          site_packages=str(site))
            for number in (signal.SIGTERM, signal.SIGHUP):
                previous_signals[number] = signal.signal(number, interrupted)
            try:
                report['entry_executed'] = True
                code = _native_execute(args.script, args.arguments, executed)
                report['entry_exit_code'] = code
            except BaseException as error:
                failure('entry', error)
            finally:
                for phase, action in (('source', sources.verify),
                    ('scope-postcheck', (lambda: __som_native_Context__.verify(ctx)) if args.scope == 'step' else
                     lambda: _native_case_postcheck(ctx, api, live, retained, sources, ctx.repo)),
                    ('case-recheck', lambda: _native_function(api, 'recheck_case', API_CONTEXT, sources)(live)),
                    ('import-controls', lease.verify_installed)):
                    try:
                        action()
                    except BaseException as error:
                        failure(phase, error)
            if primary is not None:
                raise primary.with_traceback(trace)
        report['source_postcheck'] = True
    except BaseException as error:
        failure('provider-scope', error)
        try:
            attached = BaseException.__getattribute__(error, '__som_native_payload_cleanup_secondary__')
        except AttributeError:
            attached = ()
        if type(attached) is tuple:
            for index, extra in enumerate(attached):
                if isinstance(extra, BaseException):
                    failure('provider-attached:' + str(index), extra)
    finally:
        def same_owner():
            current, current_sources, current_ctx, current_authority = _native_context()
            need(current is controls and current_sources is sources and current_ctx is ctx
                 and current_authority is authority, 'native M6 source owner changed')
        for phase, action in (('owner', same_owner), ('source-final', sources.verify),
                              ('controls-final', lease.verify_installed), ('borrow-close', lease.close)):
            try:
                action()
            except BaseException as error:
                failure(phase, error)
        for number, handler in previous_signals.items():
            try:
                signal.signal(number, handler)
            except BaseException as error:
                failure('signal-restore:' + str(number), error)
        report['executed'] = executed
        report['source_sha256'] = dict(sorted(sources.pins.items()))
        report['secondary_errors'] = secondary
        try:
            save(args.postcheck, report)
        except BaseException as error:
            failure('save-postcheck', error)
            # Keep the actual original primary on write failure; no fake receipt.
            if primary is not None:
                BaseException.__setattr__(primary, '__som_native_m6_entry_secondary__', tuple(secondary))
                raise primary.with_traceback(trace)
            raise
    return code


def need(value, message):
    if not value:
        raise ValueError(message)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def read(path, maximum=32 * 1024**2):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and
         not any(p.is_symlink() for p in (path, *path.parents)), 'physical source path required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum,
             'regular bounded source required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        need(len(raw) <= maximum, 'source exceeds limit')
        return raw
    finally:
        os.close(fd)


def save(path, value):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and not path.exists()
         and not any(p.is_symlink() for p in (path, *path.parents)), 'fresh physical evidence path required')
    with path.open('xb') as out:
        out.write(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n')


def git(repo, *args):
    return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo), *args],
                                   stderr=subprocess.DEVNULL, timeout=60)


def bootstrap(repo, revision, candidate, context_path):
    if _native_route():
        return _native_bootstrap(repo, revision, candidate, context_path)
    need(sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == 1,
         'isolated no-site no-bytecode interpreter required')
    need(re.fullmatch(r'[0-9a-f]{40}', revision or '') is not None and revision == candidate,
         'same exact helper and candidate required')
    need(git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision,
         'checked HEAD differs')
    entry = git(repo, 'ls-tree', '-z', revision, '--', GRAPH)
    need(entry.startswith((b'100644 blob ', b'100755 blob ')) and entry.count(b'\0') == 1
         and entry.endswith(b'\t' + GRAPH.encode() + b'\0'), 'regular graph source required')
    raw = git(repo, 'cat-file', 'blob', revision + ':' + GRAPH)
    need(raw == read(repo / GRAPH), 'graph bytes differ')
    graph = ModuleType('_som_m6_native_graph')
    graph.__file__, graph.__cached__, graph.__package__ = str(repo / GRAPH), None, ''
    graph.__som_raw_sha256__ = sha(raw)
    exec(compile(raw, graph.__file__, 'exec', dont_inherit=True), graph.__dict__)
    sources, _, authority = graph.admit_source(repo, revision, candidate)
    need(sources.read(GRAPH) == raw, 'source-bound graph differs')
    need(sources.read(SELF) == read(Path(__file__).absolute()), 'entry source differs')
    need(all(path in authority['admitted_repository_python_sources'] for path in
             (SELF, CONTEXT, RETAINED, API_CONTEXT)), 'required entry/bootstrap sources not admitted')
    module = graph.raw_module(sources, 'full_product_context', CONTEXT)
    ctx = module.Context(context_path)
    module._ACTIVE = ctx
    need(ctx.repo == repo and ctx.qa_mode == 'native-qa-v1' and ctx.qa_source_authority == authority
         and ctx.helper_revision == revision == ctx.candidate_revision, 'native context differs')
    return graph, sources, ctx, authority


def admit_live_inputs(graph, sources, repo, profile, before):
    """Bind React receipt and same-run live venv before using site-packages."""
    if _native_route():
        raise ValueError('legacy live-input loader is not native authority; actual provider scope required')
    need('retained_evidence' not in sys.modules and 'linux_api_context' not in sys.modules,
         'preloaded React source refused')
    graph.raw_module(sources, 'retained_evidence', RETAINED)
    api = graph.raw_module(sources, 'linux_api_context', API_CONTEXT)
    live = api.validate_case(profile, repo, before)
    api.recheck_case(live)
    # React entrypoints authenticate their own retained verifier before loading.
    # Remove only the exact modules we just installed so their checks can run.
    need(sys.modules.get('linux_api_context') is api, 'API context module changed')
    del sys.modules['linux_api_context']
    retained = sys.modules.pop('retained_evidence')
    need(getattr(retained, '__file__', None) == str(repo / RETAINED), 'retained module changed')
    need(Path(sys.executable).absolute() == Path(live['python']), 'live interpreter path differs')
    venv = Path(live['python']).parent.parent
    site = venv / 'lib/python3.12/site-packages'
    need(site.is_absolute() and site.is_dir() and not any(p.is_symlink() for p in (site, *site.parents)),
         'physical live site-packages required')
    # No .pth files or sitecustomize are executed. Full venv byte identity is
    # established before/after the run; this case rechecks selected live tools.
    sys.path.append(str(site))
    return live, site, api


class Router:
    def __init__(self, repo, sources, authority):
        self.repo, self.sources, self.authority = repo, sources, authority
        self.executed = []

    def execute(self, script, arguments):
        if _native_route():
            raise ValueError('native Router objects are not authority; fixed native dispatcher required')
        target = Path(script)
        if not target.is_absolute(): target = self.repo / target
        need(target.is_absolute() and '..' not in target.parts and self.repo in target.parents,
             'repository checker required')
        relative = target.relative_to(self.repo).as_posix()
        need(relative in self.authority['admitted_repository_python_sources'],
             'checker is not in sealed source authority')
        raw = self.sources.read(relative)
        self.executed.append({'path': relative, 'sha256': sha(raw)})
        prior_main, prior_argv, prior_path = sys.modules.get('__main__'), sys.argv, sys.path[:]
        target_module = ModuleType('__main__')
        target_module.__file__, target_module.__cached__, target_module.__package__ = str(target), None, None
        sys.modules['__main__'] = target_module
        sys.argv = [str(target), *arguments]
        sys.path.insert(0, str(target.parent))
        try:
            exec(compile(raw, str(target), 'exec', dont_inherit=True), target_module.__dict__)
            return 0
        except SystemExit as error:
            return error.code if type(error.code) is int else (0 if error.code is None else 1)
        finally:
            sys.argv = prior_argv
            sys.path[:] = prior_path
            if prior_main is None: sys.modules.pop('__main__', None)
            else: sys.modules['__main__'] = prior_main


def case_postcheck(ctx, api, live, graph, sources, repo):
    """Recheck import controls and live case identity without rescanning runtime."""
    if _native_route():
        raise ValueError('legacy Graph postcheck is not native authority; fixed native postcheck required')
    ctx.sources.verify()
    need(ctx.qa_import_controls is not None, 'native raw import controls missing')
    ctx.qa_import_controls.verify_installed()
    need(read(ctx.path) == ctx.raw, 'product context changed')
    retained = sys.modules.get('retained_evidence')
    if retained is None:
        retained = graph.raw_module(sources, 'retained_evidence', RETAINED)
    need(getattr(retained, '__file__', None) == str(repo / RETAINED)
         and callable(getattr(retained, 'sealed_files', None))
         and sources.read(RETAINED) == read(repo / RETAINED), 'retained verifier changed')
    api.recheck_case(live)


def run(args):
    if _native_route():
        return _native_run(args)
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and
         os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted' and sys.platform == 'linux',
         'GitHub hosted Linux required')
    repo = Path(args.repo)
    need(repo.is_absolute() and repo.resolve() == repo and not any(p.is_symlink() for p in (repo, *repo.parents)),
         'physical repository required')
    graph, sources, ctx, authority = bootstrap(repo, args.helper_revision, args.candidate_revision, args.product_context)
    live, site, api = admit_live_inputs(graph, sources, repo, args.api_profile, args.full_before)
    need(ROUTING_REVIEWED, 'M6 routing remains unreviewed')
    router = Router(repo, sources, authority)
    need(ROUTER_NAME not in sys.modules, 'preloaded M6 router refused')
    holder = ModuleType(ROUTER_NAME)
    holder.router = router
    holder.qa_mode = 'native-qa-v1'
    holder.context, holder.live = ctx, live
    holder.api_profile, holder.full_before = args.api_profile, args.full_before
    sys.modules[ROUTER_NAME] = holder
    report = {'version': 'som.m6-native-source-entry.v1', 'helper_revision': args.helper_revision,
              'candidate_revision': args.candidate_revision, 'logical_argv': [args.script, *args.arguments],
              'profile_sha256': live['context_sha256'], 'full_before_sha256': live['full_before_sha256'],
              'pid': os.getpid(), 'scope': args.scope, 'interpreter': str(Path(sys.executable).absolute()), 'site_packages': str(site),
              'entry_executed': False, 'entry_exit_code': None, 'source_postcheck': False,
              'product_acceptance_passed': False}
    code = 1
    previous_signals = {}
    def interrupted(number, _frame):
        raise KeyboardInterrupt('owned native child interrupted: ' + str(number))
    for number in (signal.SIGTERM, signal.SIGHUP):
        previous_signals[number] = signal.signal(number, interrupted)
    try:
        report['entry_executed'] = True
        code = router.execute(args.script, args.arguments)
        report['entry_exit_code'] = code
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        code = 1
    finally:
        try:
            sources.verify()
            if args.scope == 'step':
                ctx.verify()
            else:
                case_postcheck(ctx, api, live, graph, sources, repo)
            report['source_postcheck'] = True
        except BaseException as error:
            report['postcheck_error'] = type(error).__name__ + ': ' + str(error)
            code = 1
        report['executed'] = router.executed
        report['source_sha256'] = dict(sorted({**sources.pins, **ctx.sources.pins}.items()))
        save(args.postcheck, report)
        sys.modules.pop(ROUTER_NAME, None)
        for number, handler in previous_signals.items():
            signal.signal(number, handler)
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'product-context', 'api-profile', 'full-before', 'postcheck'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('helper-revision', 'candidate-revision', 'script'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--scope', choices=('step', 'case'), required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.arguments[:1] == ['--']: args.arguments = args.arguments[1:]
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
