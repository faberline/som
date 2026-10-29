"""Unsealed draft: admit the React API Python payload, not product acceptance.

Only the independently reviewed native startup may load this module from its
source authority.  The reviewed caller must pass its real Context, never a
caller-provided verifier, source map, payload, or receipt.  React amendment 003
and the native source closure must both bind this source before use.
"""
from __future__ import annotations

import hashlib
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
    ctx.qa_import_controls.verify_installed()
    authority = ctx.qa_source_authority
    need(isinstance(authority, dict) and authority.get('source_authority_active') is True,
         'active native source authority required')
    allowed = authority.get('admitted_repository_python_sources')
    need(isinstance(allowed, (set, frozenset, list, tuple))
         and all(path in allowed for path in (SELF, RETAINED, API, LIVE)),
         'provider or verifier absent from native source authority')
    repo = physical_directory(ctx.repo)
    need(ctx.helper_revision == ctx.candidate_revision == os.environ.get('GITHUB_SHA')
         and re.fullmatch(r'[0-9a-f]{40}', ctx.helper_revision or '') is not None,
         'exact reviewed checkout required')
    ctx.sources.read(SELF)
    temp = physical_directory(Path(os.environ['RUNNER_TEMP']))
    profile_path = temp / 'som-react-api-context.json'
    venv = temp / 'som-react-python-closure/environments/react-api-qa/venv'
    selected = Path(executable)
    need(selected.is_absolute() and selected == venv / 'bin/python', 'exact API interpreter required')
    physical_directory(venv)
    physical_directory(venv / 'bin')
    site = physical_directory(venv / 'lib/python3.12/site-packages')
    runtime = ctx.runtime()
    base = Path(runtime['tools']['python'])
    expected_base = temp / 'som-product-runtime-qualification/install/python/bin/python3.12'
    need(base == expected_base and Path(ctx.data['runtime_root']) == expected_base.parents[3],
         'qualified runtime identity differs')
    physical_directory(base.parent)
    need(base.is_file() and not base.is_symlink() and os.access(base, os.X_OK),
         'qualified runtime Python is not a physical executable')
    need(selected.is_symlink() and selected.resolve(strict=True) == base,
         'venv interpreter link differs')
    base_stat, selected_stat = base.stat(), selected.stat()
    need(stat.S_ISREG(base_stat.st_mode) and (base_stat.st_dev, base_stat.st_ino)
         == (selected_stat.st_dev, selected_stat.st_ino), 'interpreter inode differs')

    with LOCK:
        modules = {}
        need(PRODUCER_MODULE not in sys.modules, 'preloaded wheel producer refused')
        try:
            modules['retained_evidence'] = load_source(ctx, RETAINED, 'retained_evidence')
            modules['_som_react_api_payload_context'] = load_source(ctx, API, '_som_react_api_payload_context')
            api = modules['_som_react_api_payload_context']
            repo2, path, profile, auth, auth_sha, profile_sha, receipt_sha = api.inputs(profile_path, repo)
            need(repo2 == repo and path == profile_path and profile['venv'] == str(venv)
                 and profile['runtime_root'] == str(expected_base.parents[3]), 'API profile differs')
            need(auth['source_sha256'][RETAINED] == hashlib.sha256(ctx.sources.read(RETAINED)).hexdigest()
                 and auth['source_sha256'][API] == hashlib.sha256(ctx.sources.read(API)).hexdigest()
                 and auth['source_sha256'][LIVE] == hashlib.sha256(ctx.sources.read(LIVE)).hexdigest(),
                 'native and React source authorities differ')
            modules['_som_react_api_payload_live'] = load_source(ctx, LIVE, '_som_react_api_payload_live')
            live = modules['_som_react_api_payload_live'].verify(profile)
            keys = {'status', 'python', 'pg_bin', 'live_receipt_sha256', 'runtime_receipt_sha256',
                    'closure_receipt_sha256', 'venv_inventory_sha256', 'requirements_count', 'wheel_count'}
            need(type(live) is dict and set(live) == keys and live['status'] == 'verified-live-inputs'
                 and live['python'] == str(selected) and live['pg_bin'] == str(expected_base.parents[3] / 'install/postgresql/bin')
                 and type(live['requirements_count']) is int and live['requirements_count'] == 39
                 and type(live['wheel_count']) is int and live['wheel_count'] == 40
                 and live['live_receipt_sha256'] == receipt_sha,
                 'live verifier result differs')
            hashes = (profile_sha, auth_sha, receipt_sha, live['runtime_receipt_sha256'],
                      live['closure_receipt_sha256'], live['venv_inventory_sha256'])
            need(all(type(value) is str and HEX.fullmatch(value) for value in hashes), 'proof digest differs')
            # Recheck link and directory after the full wheel and source scan.
            need(physical_directory(site) == site and selected.resolve(strict=True) == base
                 and (selected.stat().st_dev, selected.stat().st_ino) == (base.stat().st_dev, base.stat().st_ino)
                 and (base.stat().st_dev, base.stat().st_ino) == (base_stat.st_dev, base_stat.st_ino),
                 'Python payload changed during admission')
            ctx.qa_import_controls.verify_installed()
            return AdmittedPayload(('som.native-python-startup-payload.v1', 'react-api-qa', str(selected),
                                    str(site), str(base), *hashes, 39, 40, ctx.helper_revision, False, False))
        finally:
            live_module = modules.get('_som_react_api_payload_live')
            binding = getattr(live_module, '_binding', None)
            producer = binding.get('producer') if isinstance(binding, dict) else None
            if producer is not None and sys.modules.get(PRODUCER_MODULE) is producer:
                del sys.modules[PRODUCER_MODULE]
            for name, module in reversed(tuple(modules.items())):
                if sys.modules.get(name) is module:
                    del sys.modules[name]
