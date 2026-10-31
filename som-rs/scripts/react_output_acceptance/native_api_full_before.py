"""Source-owned native collector records. This module grants no acceptance.

The Core loads this stable role only from the one live collector scope.
The provider owns private4. This writer never constructs a Context or group.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from types import FunctionType, ModuleType

SELF = 'som-rs/scripts/react_output_acceptance/native_api_full_before.py'
PROVIDER = 'som-rs/scripts/react_output_acceptance/react_api_python_payload.py'
API = 'som-rs/scripts/react_output_acceptance/linux_api_context.py'
SESSION = 'som-rs/scripts/react_output_acceptance/m6_native_entry.py'


def _need(value, message):
    if not value:
        raise ValueError(message)


def _native_record(label):
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    _need(type(label) is str and label in ('before', 'after'),
          'fixed collector record phase required')
    _need(type(__som_native_collector_session__) is ModuleType,
          'actual stable collector module required')
    scope_values = ModuleType.__getattribute__(__som_native_collector_session__, '__dict__')
    _need(type(scope_values.get('_NATIVE_COLLECTOR')) is __som_native_CollectorScope__
          and scope_values.get('_native_collector_writer_origin') is __som_native_collector_writer_origin__
          and scope_values.get('__file__') == str(controls.root / SESSION),
          'fixed collector origin export differs')
    ctx, out = __som_native_collector_writer_origin__(label)
    _need(type(ctx) is __som_native_Context__
          and ModuleType.__getattribute__(__som_native_context_module__, '__dict__').get('_ACTIVE') is ctx,
          'actual writer Context required')
    namespace = ModuleType.__getattribute__(__som_native_provider__, '__dict__')
    _need(namespace.get('collector_validate') is __som_native_provider_collector_validate__
          and namespace.get('__file__') == str(controls.root / PROVIDER)
          and namespace.get('__som_raw_sha256__') == hashlib.sha256(ctx.sources.read(PROVIDER)).hexdigest(),
          'fixed provider validator source differs')
    _need(type(out) is type(controls.root) and out.is_absolute()
          and controls.root / '.local' in out.parents
          and out.is_dir() and not any(p.is_symlink() for p in (out, *out.parents)),
          'original physical repository collector C required')
    profile = Path(os.environ['RUNNER_TEMP']) / 'som-react-api-context.json'
    api, result = __som_native_provider_collector_validate__(ctx, profile)
    _need(type(api) is ModuleType, 'actual provider API module required')
    api_values = ModuleType.__getattribute__(api, '__dict__')
    full_record = api_values.get('full_record')
    _need(api_values.get('__file__') == str(controls.root / API)
          and api_values.get('__som_raw_sha256__') == hashlib.sha256(ctx.sources.read(API)).hexdigest()
          and type(full_record) is FunctionType and full_record.__globals__ is api_values,
          'actual source-owned full_record required')
    # Public Core checks the original API Function/CodeType before invocation.
    # Neither a record nor an expected hash is supplied by the collector.
    controls.verify_installed()
    record = full_record(result)
    raw = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2,
                      allow_nan=False) + '\n').encode('utf-8')
    _need(type(record) is dict and 0 < len(raw) <= 1024 * 1024,
          'bounded actual full-validation record required')
    root = out / 'api-qa-evidence'
    if label == 'before':
        root.mkdir(mode=0o700, exist_ok=False)
    _need(root.is_dir() and not root.is_symlink() and stat.S_IMODE(root.stat().st_mode) == 0o700,
          'owned full-validation evidence directory required')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    fd = None
    primary, trace, secondary = None, None, []
    try:
        _need(os.fstat(directory).st_ino == root.stat().st_ino,
              'full-validation directory identity changed')
        fd = os.open('full-' + label + '.json',
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory)
        identity = os.fstat(fd)
        _need(stat.S_ISREG(identity.st_mode) and stat.S_IMODE(identity.st_mode) == 0o600
              and identity.st_nlink == 1 and identity.st_uid == os.geteuid(),
              'exclusive owned0600 full-validation file required')
        position = 0
        while position < len(raw):
            count = os.write(fd, raw[position:])
            _need(count > 0, 'full-validation write made no progress')
            position += count
        os.fsync(fd)
        named = os.stat('full-' + label + '.json', dir_fd=directory, follow_symlinks=False)
        _need((identity.st_dev, identity.st_ino) == (named.st_dev, named.st_ino)
              and named.st_size == len(raw), 'full-validation output identity changed')
        ctx.sources.verify()
        controls.verify_installed()
    except BaseException as error:
        primary, trace = error, error.__traceback__
    finally:
        for owned in (fd, directory):
            if owned is not None:
                try:
                    os.close(owned)
                except BaseException as error:
                    if primary is None:
                        primary, trace = error, error.__traceback__
                    else:
                        secondary.append(error)
    if primary is not None:
        BaseException.__setattr__(primary, '__som_native_collector_secondary__', tuple(secondary))
        raise primary.with_traceback(trace)
    return {'path': str(root / ('full-' + label + '.json')),
            'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
            'actual_provider_full_record': True, 'product_acceptance_passed': False}


def _native_write_after():
    return _native_record('after')


# First Core load runs the real full validator before validate_case can read C.
_NATIVE_BEFORE = _native_record('before')
