"""Unsealed draft: admit the React API Python payload, not product acceptance.

Only the independently reviewed native startup may load this module from its
source authority.  The reviewed caller must pass its real Context, never a
caller-provided verifier, source map, payload, or receipt.  React amendment 003
and the native source closure must both bind this source before use.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import sys
import threading
from types import ModuleType

SELF = 'som-rs/scripts/react_output_acceptance/react_api_python_payload.py'
RETAINED = 'som-rs/scripts/react_output_acceptance/retained_evidence.py'
API = 'som-rs/scripts/react_output_acceptance/linux_api_context.py'
LIVE = 'som-rs/scripts/react_output_acceptance/linux_api_live_inputs.py'
NODE = 'som-rs/tests/react-output-v1/linux_qa_profile.py'
CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
PRODUCER = 'som-rs/scripts/hosted_readiness/product_python/produce_wheels.py'
REUSE = 'som-rs/scripts/hosted_readiness/product_runtime/reuse_runtime.py'
QUALIFY = 'som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py'
NATIVE_AUTHORITY = 'som-rs/scripts/hosted_readiness/native_qa_authority.py'
# All repository Python that this admission path executes, including the
# trusted caller and Context.runtime's source-bound helpers. The provisioner
# and metadata probe are checked as recorded inputs; this path does not run them.
REPOSITORY_PYTHON_EXECUTION_CLOSURE = frozenset((
    SELF, CONTEXT, NATIVE_AUTHORITY, RETAINED, API, LIVE, NODE, PRODUCER, REUSE, QUALIFY,
))
PRODUCER_MODULE = 'som_react_api_verified_wheel_producer'
HEX = re.compile(r'[0-9a-f]{64}\Z')
LOCK = threading.RLock()


class PayloadError(ValueError):
    pass


def need(value, message):
    if not value:
        raise PayloadError(message)


def physical_directory(path):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'absolute path required')
    need(path.is_dir() and path.resolve(strict=True) == path
         and not any(part.is_symlink() for part in (path, *path.parents)),
         'physical directory required')
    return path


def load_source(ctx, relative, name):
    need(name not in sys.modules, 'preloaded verifier refused: ' + name)
    raw = ctx.sources.read(relative)
    need(type(raw) is bytes, 'bound source bytes required')
    module = ModuleType(name)
    module.__file__ = str(ctx.repo / relative)
    module.__cached__ = None
    module.__package__ = ''
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise
    need(sys.modules.get(name) is module, 'verifier identity changed')
    return module


class _PrivateModules:
    """Private verifier names; every other module uses the real registry."""
    def __init__(self, retained):
        self.private = {'retained_evidence': retained}
        self.names = frozenset(('retained_evidence', PRODUCER_MODULE))

    def __contains__(self, name):
        return name in self.private if name in self.names else name in sys.modules

    def __getitem__(self, name):
        return self.private[name] if name in self.names else sys.modules[name]

    def __setitem__(self, name, value):
        if name in self.names:
            self.private[name] = value
        else:
            sys.modules[name] = value

    def __delitem__(self, name):
        if name in self.names:
            del self.private[name]
        else:
            del sys.modules[name]

    def get(self, name, default=None):
        return self.private.get(name, default) if name in self.names else sys.modules.get(name, default)

    def pop(self, name, *default):
        return self.private.pop(name, *default) if name in self.names else sys.modules.pop(name, *default)


class _PrivateSys:
    def __init__(self, retained):
        self.modules = _PrivateModules(retained)

    def __getattr__(self, name):
        return getattr(sys, name)


@contextmanager
def _private_helpers(ctx):
    """Compile fresh authenticated modules without touching collector names."""
    modules = {}
    try:
        for relative, name in ((RETAINED, '_som_react_private_retained'),
                               (API, '_som_react_api_payload_context'),
                               (LIVE, '_som_react_api_payload_live')):
            modules[name] = load_source(ctx, relative, name)
        private_sys = _PrivateSys(modules['_som_react_private_retained'])
        modules['_som_react_api_payload_context'].sys = private_sys
        modules['_som_react_api_payload_live'].sys = private_sys
        yield modules['_som_react_api_payload_context'], modules['_som_react_api_payload_live']
    finally:
        for name, module in reversed(tuple(modules.items())):
            if sys.modules.get(name) is module:
                del sys.modules[name]


def _bind_live(live_verifier, ctx, repo, runtime):
    need(live_verifier._binding is None, 'preloaded live verifier state refused')
    binding, _sources, _raw_sources = live_verifier.bound_sources(repo, ctx.helper_revision)
    need(binding is live_verifier._binding and binding['reuse'] is None
         and binding['repo'] == repo and binding['revision'] == ctx.helper_revision,
         'fresh source-bound live verifier required')
    binding.update(reuse=runtime['reuse'], qualification=runtime['qualification'],
                   runtime_inventory=runtime['inventory'], tools=runtime['tools'], native=runtime['identity'])


class AdmittedPayload(tuple):
    """Verifier-created immutable value. Context must call admit itself."""
    __slots__ = ()

    def record(self):
        keys = ('version', 'role', 'python', 'site', 'base_python',
                'api_profile_sha256', 'api_authority_sha256', 'live_receipt_sha256',
                'runtime_receipt_sha256', 'closure_receipt_sha256',
                'venv_inventory_sha256', 'requirements_count', 'wheel_count',
                'source_revision', 'pth_executed', 'product_acceptance_passed')
        return dict(zip(keys, self, strict=True))


def admit(ctx, executable):
    """Rederive the live 39-pin payload before returning its sole site path.

    ctx.sources and ctx.qa_source_authority come only from the native reviewed
    startup. The caller must append the returned path as a plain sys.path item;
    site.addsitedir, .pth processing and sitecustomize are not admitted.
    """
    need(ctx.qa_mode == 'native-qa-v1' and ctx.qa_import_controls is not None,
         'active native source controls required')
    need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
         'isolated no-site no-bytecode startup required')
    authority = ctx.qa_source_authority
    need(isinstance(authority, dict) and authority.get('source_authority_active') is True,
         'active native source authority required')
    allowed = authority.get('admitted_repository_python_sources')
    need(isinstance(allowed, (set, frozenset, list, tuple))
         and REPOSITORY_PYTHON_EXECUTION_CLOSURE.issubset(allowed),
         'executed Python source absent from native source authority')
    repo = physical_directory(ctx.repo)
    need(ctx.helper_revision == ctx.candidate_revision == os.environ.get('GITHUB_SHA')
         and re.fullmatch(r'[0-9a-f]{40}', ctx.helper_revision or '') is not None,
         'exact reviewed checkout required')
    # Native authorization precedes Context.runtime() and every dynamic exec,
    # including the wheel producer compiled inside live.bound_sources().
    for source in sorted(REPOSITORY_PYTHON_EXECUTION_CLOSURE):
        need(type(ctx.sources.read(source)) is bytes, 'bound source bytes required')
    ctx.qa_import_controls.verify_installed()
    temp = physical_directory(Path(os.environ['RUNNER_TEMP']))
    profile_path = temp / 'som-react-api-context.json'
    venv = temp / 'som-react-python-closure/environments/react-api-qa/venv'
    selected = Path(executable)
    need(selected.is_absolute() and selected == venv / 'bin/python', 'exact API interpreter required')
    physical_directory(venv)
    physical_directory(venv / 'bin')
    site = physical_directory(venv / 'lib/python3.12/site-packages')
    runtime = ctx.runtime()
    runtime_root = physical_directory(Path(ctx.data['runtime_root']))
    need(temp in runtime_root.parents, 'qualified runtime must be under runner temp')
    base = Path(runtime['tools']['python'])
    need(base == runtime_root / 'install/python/bin/python3.12', 'qualified runtime identity differs')
    runtime['reuse'].verify_installed_tree(runtime_root, runtime['inventory'], runtime['qualification'])
    physical_directory(base.parent)
    need(base.is_file() and not base.is_symlink() and os.access(base, os.X_OK),
         'qualified runtime Python is not a physical executable')
    need(selected.is_symlink() and selected.resolve(strict=True) == base,
         'venv interpreter link differs')
    base_stat, selected_stat = base.stat(), selected.stat()
    need(stat.S_ISREG(base_stat.st_mode) and (base_stat.st_dev, base_stat.st_ino)
         == (selected_stat.st_dev, selected_stat.st_ino), 'interpreter inode differs')

    with LOCK:
        with _private_helpers(ctx) as (api, live_verifier):
            repo2, path, profile, auth, auth_sha, profile_sha, receipt_sha = api.inputs(profile_path, repo)
            need(repo2 == repo and path == profile_path and profile['venv'] == str(venv)
                 and profile['runtime_root'] == str(runtime_root), 'API profile runtime differs from native Context')
            need(all(auth['source_sha256'][name] == hashlib.sha256(ctx.sources.read(name)).hexdigest()
                     for name in (RETAINED, API, LIVE, NODE, PRODUCER, REUSE, QUALIFY)),
                 'native and React source authorities differ')
            # Context.runtime() already ran the exact source-bound
            # admitted_runtime(). Its fixed module names cannot be loaded a
            # second time in this process. Seed only the verifier's private
            # runtime cache with that same verified object, never with a
            # caller-supplied verifier or receipt. bound_sources authenticates
            # the complete React map and exact Git source before the reuse.
            _bind_live(live_verifier, ctx, repo, runtime)
            live = live_verifier.verify(profile)
            keys = {'status', 'python', 'pg_bin', 'live_receipt_sha256', 'runtime_receipt_sha256',
                    'closure_receipt_sha256', 'venv_inventory_sha256', 'requirements_count', 'wheel_count'}
            need(type(live) is dict and set(live) == keys and live['status'] == 'verified-live-inputs'
                 and live['python'] == str(selected) and live['pg_bin'] == str(runtime_root / 'install/postgresql/bin')
                 and type(live['requirements_count']) is int and live['requirements_count'] == 39
                 and type(live['wheel_count']) is int and live['wheel_count'] == 40
                 and live['live_receipt_sha256'] == receipt_sha
                 and live['runtime_receipt_sha256'] == runtime['identity']['reuse_receipt_sha256'],
                 'live verifier result differs')
            hashes = (profile_sha, auth_sha, receipt_sha, live['runtime_receipt_sha256'],
                      live['closure_receipt_sha256'], live['venv_inventory_sha256'])
            need(all(type(value) is str and HEX.fullmatch(value) for value in hashes), 'proof digest differs')
            # Recheck link and directory after the full wheel and source scan.
            need(physical_directory(site) == site and selected.resolve(strict=True) == base
                 and (selected.stat().st_dev, selected.stat().st_ino) == (base.stat().st_dev, base.stat().st_ino)
                 and (base.stat().st_dev, base.stat().st_ino) == (base_stat.st_dev, base_stat.st_ino),
                 'Python payload changed during admission')
            runtime['reuse'].verify_installed_tree(runtime_root, runtime['inventory'], runtime['qualification'])
            ctx.qa_import_controls.verify_installed()
            return AdmittedPayload(('som.native-python-startup-payload.v1', 'react-api-qa', str(selected),
                                    str(site), str(base), *hashes, 39, 40, ctx.helper_revision, False, False))


def collector_validate(ctx, profile_path):
    """Run the complete original API validator with a fresh private namespace.

    The trusted native collector supplies only its real Context and fixed
    profile path. No verifier, module, receipt, or runtime is supplied by it.
    The returned source-bound API module owns the context's recheck functions.
    """
    temp = physical_directory(Path(os.environ['RUNNER_TEMP']))
    path = temp / 'som-react-api-context.json'
    need(Path(profile_path) == path, 'exact API profile required')
    venv = temp / 'som-react-python-closure/environments/react-api-qa/venv'
    proof = admit(ctx, venv / 'bin/python').record()
    repo, runtime = physical_directory(ctx.repo), ctx.runtime()
    runtime_root = physical_directory(Path(ctx.data['runtime_root']))
    with LOCK:
        with _private_helpers(ctx) as (api, live):
            repo2, path2, profile, auth, auth_sha, profile_sha, receipt_sha = api.inputs(path, repo)
            need(repo2 == repo and path2 == path and profile['runtime_root'] == str(runtime_root)
                 and auth_sha == proof['api_authority_sha256'] and profile_sha == proof['api_profile_sha256']
                 and receipt_sha == proof['live_receipt_sha256'], 'collector profile or receipt differs')
            need(all(auth['source_sha256'][name] == hashlib.sha256(ctx.sources.read(name)).hexdigest()
                     for name in (RETAINED, API, LIVE, NODE, PRODUCER, REUSE, QUALIFY)),
                 'collector source authorities differ')
            node_name = '_som_react_api_payload_node'
            node = load_source(ctx, NODE, node_name)
            try:
                _bind_live(live, ctx, repo, runtime)
                result = api.verify(repo, path, profile, auth, auth_sha, profile_sha, receipt_sha, node, live)
                need(result['python'] == proof['python']
                     and result['runtime_receipt_sha256'] == proof['runtime_receipt_sha256']
                     and result['live_receipt_sha256'] == proof['live_receipt_sha256']
                     and result['venv_inventory_sha256'] == proof['venv_inventory_sha256'],
                     'collector live verification differs from admitted payload')
                runtime['reuse'].verify_installed_tree(runtime_root, runtime['inventory'], runtime['qualification'])
                ctx.qa_import_controls.verify_installed()
                return api, result
            finally:
                if sys.modules.get(node_name) is node:
                    del sys.modules[node_name]
