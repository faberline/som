"""Unsealed draft: admit the React API Python payload, not product acceptance.

Only the independently reviewed native startup may load this module from its
source authority.  The reviewed caller must pass its real Context, never a
caller-provided verifier, source map, payload, or receipt.  React amendment 003
and the native source closure must both bind this source before use.
"""
from __future__ import annotations

import hashlib
import builtins
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import sys
import threading
from types import FunctionType, ModuleType

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
NATIVE_OWNER_PROFILE = 'som-rs/contracts/react-output-v1/m12-native-api-payload-owner-v1.json'
NATIVE_PRODUCER_SHA = '02d06583060b12601f3038709f2d2a9a0643d93cda7670220d592fd821ed580e'
NATIVE_CORE = 'som-rs/scripts/hosted_readiness/native_active_controls.py'
NATIVE_RUNTIME = 'som-rs/scripts/hosted_readiness/product_runtime/native_runtime_verifier.py'
NATIVE_REQUIRED = REPOSITORY_PYTHON_EXECUTION_CLOSURE | {NATIVE_CORE, NATIVE_RUNTIME}
NATIVE_MEMBERS = {
    'retained': ('m6.api.payload.retained', RETAINED, '_som_react_private_retained'),
    'api': ('m6.api.payload.api', API, '_som_react_api_payload_context'),
    'live': ('m6.api.payload.live', LIVE, '_som_react_api_payload_live'),
    'node': ('m6.api.payload.node', NODE, '_som_react_api_payload_node'),
}
_NATIVE_OWNER = None
_NATIVE_FAILURE = None


def _native_route():
    if '__som_native_active_controls__' in globals():
        return True
    need('_som_native_active_controls' not in sys.modules,
         'native payload has no fixed active-controls binding')
    return False


class _NativeOwner:
    """One source-owned lifetime, not a cache of successful data checks."""
    def __init__(self, ctx, controls, lease, group, members, profile_sha):
        self.ctx, self.controls, self.lease, self.group = ctx, controls, lease, group
        self.members, self.profile_sha = members, profile_sha
        self.anchor = (ctx, controls, lease, group, tuple(members.items()))
        self.producer, self.binding, self.runtime = None, None, None
        self.committed, self.failed = False, False
        # Install the journal before private-group construction or member reads.
        # A failed close is an attempted close. It is never replayed.
        self.phase = 'acquiring'
        self.cleanup = []
        self.cleanup_errors = []
        self.closed = False
        self.acquisition = (ctx, controls, lease)


def _native_owned_close(state, name, value, kind):
    need(type(value) is kind, 'actual provider-owned cleanup object required')
    close = type.__getattribute__(kind, '__dict__').get('close')
    need(type(close) is FunctionType and close.__closure__ is None,
         'source-owned public close function required')
    row = {'name': name, 'value': value, 'kind': kind, 'close': close,
           'signature': (close.__code__, close.__globals__, close.__defaults__,
                         close.__kwdefaults__, close.__builtins__),
           'attempted': False, 'closed': False, 'error': None, 'traceback': None}
    state.cleanup.append(row)


def _native_cleanup_owner(ctx):
    """Check ownership without invoking admission or the poisoned-use gate."""
    state = _NATIVE_OWNER
    need(type(ctx) is __som_native_Context__ and type(state) is _NativeOwner,
         'actual provider cleanup Context and owner required')
    values = object.__getattribute__(state, '__dict__')
    acquisition = values.get('acquisition')
    need(type(acquisition) is tuple and len(acquisition) == 3
         and acquisition[0] is ctx and values.get('ctx') is ctx
         and values.get('controls') is acquisition[1] and values.get('lease') is acquisition[2]
         and type(acquisition[1]) is type(acquisition[2])
         and type(values.get('cleanup')) is list and type(values.get('cleanup_errors')) is list
         and type(values.get('phase')) is str
         and values['phase'] in {'acquiring', 'live', 'closing', 'closed'},
         'provider cleanup acquisition journal differs')
    namespace = ModuleType.__getattribute__(__som_native_context_module__, '__dict__')
    need(namespace.get('_ACTIVE') is ctx, 'provider cleanup Context owner changed')
    rows = values['cleanup']
    need(1 <= len(rows) <= 2 and rows[0]['name'] == 'borrow'
         and rows[0]['value'] is acquisition[2]
         and (len(rows) == 1 or rows[1]['name'] == 'private4'
              and rows[1]['value'] is values.get('group')),
         'provider cleanup resource order differs')
    return state


def _native_release_owned(state):
    values = object.__getattribute__(state, '__dict__')
    values['phase'] = 'closing'
    errors = []
    for row in reversed(values['cleanup']):
        if row['attempted']:
            continue
        row['attempted'] = True
        try:
            value, kind, close, signature = row['value'], row['kind'], row['close'], row['signature']
            need(type(value) is kind and type(close) is FunctionType
                 and type(signature) is tuple and len(signature) == 5
                 and close.__code__ is signature[0] and close.__globals__ is signature[1]
                 and close.__defaults__ is signature[2] and close.__kwdefaults__ is signature[3]
                 and close.__builtins__ is signature[4] and close.__closure__ is None,
                 'held public cleanup invocation changed')
            # Call the held original, not a mutable late descriptor or callback.
            close(value)
            row['closed'] = True
        except BaseException as error:
            row['error'], row['traceback'] = error, error.__traceback__
            values['cleanup_errors'].append((row['name'], error, error.__traceback__))
            errors.append(error)
    values['phase'], values['closed'] = 'closed', True
    return errors


def _native_collector_finalize(ctx):
    """Release this provider's resources once, including partial/poisoned ones.

    The full-products owner holds this fixed source export before activation.
    It calls this only after its last complete Context/API/source verification.
    No admission, private member access or Context verification follows release.
    """
    if _NATIVE_OWNER is None:
        return {'acquired': False, 'private4_closed': False, 'borrow_closed': False}
    state = _native_cleanup_owner(ctx)
    errors = _native_release_owned(state)
    rows = object.__getattribute__(state, '__dict__')['cleanup']
    status = {'acquired': True, 'private4_closed': any(row['name'] == 'private4' and row['closed'] for row in rows),
              'borrow_closed': rows[0]['closed'], 'attempted': [row['name'] for row in rows if row['attempted']],
              'product_acceptance_passed': False}
    if errors:
        primary = errors[0]
        BaseException.__setattr__(primary, '__som_native_payload_cleanup_secondary__', tuple(errors[1:]))
        BaseException.__setattr__(primary, '__som_native_payload_cleanup_status__', status)
        raise primary.with_traceback(object.__getattribute__(state, '__dict__')['cleanup_errors'][-len(errors)][2])
    return status


def _native_controls(ctx):
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    if _NATIVE_FAILURE is not None:
        raise PayloadError('native payload owner is poisoned') from _NATIVE_FAILURE
    need(type(ctx) is __som_native_Context__, 'actual native Context class required')
    need(type(__som_native_context_module__) is ModuleType
         and ModuleType.__getattribute__(__som_native_context_module__, '__dict__').get('_ACTIVE') is ctx,
         'actual active native Context owner required')
    values = object.__getattribute__(ctx, '__dict__')
    need(type(values) is dict and values.get('qa_mode') == 'native-qa-v1'
         and type(values.get('sources')) is __som_native_SourceBytes__, 'actual native Context source owner required')
    need(type(values.get('repo')) is type(controls.root) and values['repo'] == controls.root
         and type(values.get('helper_revision')) is str and type(values.get('candidate_revision')) is str
         and values['helper_revision'] == values['candidate_revision'] == controls.helper_revision == controls.candidate_revision,
         'actual native Context root/revisions differ')
    source = object.__getattribute__(values['sources'], '__dict__')
    need(type(source) is dict and type(source.get('root')) is type(controls.root)
         and source['root'] == controls.root and type(source.get('revision')) is str
         and source['revision'] == controls.helper_revision and type(source.get('pins')) is dict
         and all(type(path) is str and type(pin) is str and HEX.fullmatch(pin)
                 for path, pin in source['pins'].items()),
         'actual native Context source reader differs')
    authority = values.get('qa_source_authority')
    need(type(authority) is dict and authority.get('source_authority_active') is True
         and type(authority.get('admitted_repository_python_sources')) is list
         and all(type(path) is str for path in authority['admitted_repository_python_sources'])
         and NATIVE_REQUIRED <= set(authority['admitted_repository_python_sources']),
         'complete actual native payload source authority required')
    return controls


def _native_profile(ctx, controls):
    import json
    raw = ctx.sources.read(NATIVE_OWNER_PROFILE)
    def pairs(rows):
        result = {}
        for key, value in rows:
            need(key not in result, 'duplicate native owner profile key')
            result[key] = value
        return result
    profile = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(PayloadError('nonfinite native owner profile')))
    need(type(profile) is dict and set(profile) == {'version', 'status', 'complete', 'pending',
         'lifetime', 'producer_role', 'producer_module', 'producer_sha256', 'source_sha256', 'product_acceptance_passed'},
         'exact native owner source profile required')
    need(profile['version'] == 'som.m12.native-react-payload-owner-source.v1'
         and profile['status'] == 'reviewed_source_only' and profile['complete'] is True
         and profile['pending'] == [] and profile['product_acceptance_passed'] is False,
         'native owner source profile remains draft or pending')
    need(profile['lifetime'] == 'one-exact-Core-Context-owner'
         and profile['producer_role'] == 'm6.api.producer' and profile['producer_module'] == PRODUCER_MODULE
         and profile['producer_sha256'] == NATIVE_PRODUCER_SHA,
         'fixed native owner lifetime/producer differs')
    pins = profile['source_sha256']
    need(type(pins) is dict and NATIVE_REQUIRED <= pins.keys()
         and all(type(path) is str and type(pin) is str and HEX.fullmatch(pin) for path, pin in pins.items()),
         'complete native owner source pins required')
    for path, pin in pins.items():
        need(hashlib.sha256(ctx.sources.read(path)).hexdigest() == pin, 'native owner source changed')
    need(pins[PRODUCER] == NATIVE_PRODUCER_SHA
         and controls.role_sources.get('m6.api.producer') == PRODUCER,
         'fixed separate native wheel producer source required')
    for role, path, name in NATIVE_MEMBERS.values():
        need(controls.role_sources.get(role) == path, 'fixed private member source differs')
    ctx.sources.verify()
    return profile, hashlib.sha256(raw).hexdigest()


def _native_current(ctx):
    controls = _native_controls(ctx)
    if _NATIVE_FAILURE is not None:
        raise PayloadError('native payload owner is poisoned') from _NATIVE_FAILURE
    state = _NATIVE_OWNER
    need(type(state) is _NativeOwner, 'actual native payload owner state required')
    values = object.__getattribute__(state, '__dict__')
    anchor = values.get('anchor')
    need(type(anchor) is tuple and len(anchor) == 5 and anchor[0] is ctx and anchor[1] is controls
         and values.get('ctx') is anchor[0] and values.get('controls') is anchor[1]
         and values.get('lease') is anchor[2] and values.get('group') is anchor[3]
         and type(values.get('committed')) is bool and values.get('failed') is False
         and values.get('phase') == 'live' and values.get('closed') is False,
         'native payload owner identity differs')
    lease, group = anchor[2], anchor[3]
    need(type(lease) is type(controls) and type(group) is __som_native_Group__,
         'actual borrowed lease and Core private group required')
    lease.verify_installed()
    need(lease.root == controls.root and lease.helper_revision == controls.helper_revision
         and lease.candidate_revision == controls.candidate_revision, 'native payload borrow owner differs')
    members = values.get('members')
    need(type(members) is dict and all(type(key) is str for key in members) and set(members) == set(NATIVE_MEMBERS)
         and type(anchor[4]) is tuple and len(anchor[4]) == 4
         and all(type(item) is tuple and len(item) == 2 and type(item[0]) is str for item in anchor[4])
         and {item[0] for item in anchor[4]} == set(NATIVE_MEMBERS),
         'exact native payload member set required')
    profile, pin = _native_profile(ctx, controls)
    need(type(values.get('profile_sha')) is str and values['profile_sha'] == pin,
         'native payload source authority changed')
    for item in anchor[4]:
        need(type(item) is tuple and len(item) == 2 and type(item[0]) is str and item[0] in NATIVE_MEMBERS,
             'actual native member anchor required')
        key, original = item
        role, path, name = NATIVE_MEMBERS[key]
        module = members.get(key)
        need(module is original and type(module) is ModuleType and group[key] is module
             and sys.modules.get(name) is module, 'native private member pointer differs')
        data = ModuleType.__getattribute__(module, '__dict__')
        need(data.get('__name__') == name and data.get('__file__') == str(controls.root / path)
             and data.get('__som_raw_sha256__') == profile['source_sha256'][path],
             'native private member source/namespace differs')
    producer = values.get('producer')
    if values['committed']:
        binding, runtime = values.get('binding'), values.get('runtime')
        need(type(producer) is ModuleType and type(binding) is dict and type(runtime) is tuple and len(runtime) == 5
             and binding.get('native_owner') is state and binding.get('producer') is producer
             and all(binding.get(key) is value for key, value in zip(
                 ('reuse', 'qualification', 'runtime_inventory', 'tools', 'native'), runtime, strict=True)),
             'committed native owner binding/runtime identity differs')
    if producer is not None:
        need(type(producer) is ModuleType and sys.modules.get(PRODUCER_MODULE) is producer,
             'native producer pointer differs')
        data = ModuleType.__getattribute__(producer, '__dict__')
        need(data.get('__name__') == PRODUCER_MODULE and data.get('__file__') == str(controls.root / PRODUCER)
             and data.get('__som_raw_sha256__') == NATIVE_PRODUCER_SHA, 'native producer source differs')
    controls.verify_installed()
    return state, profile


def _native_poison(state, primary, *, acquisition=False):
    global _NATIVE_FAILURE
    _NATIVE_FAILURE = primary
    errors = []
    status = {'group_acquired': False, 'private4_closed': False, 'borrow_closed': False,
              'producer_retained_until_outer_owner_cleanup': PRODUCER_MODULE in sys.modules,
              'product_acceptance_passed': False}
    if type(state) is _NativeOwner:
        values = object.__getattribute__(state, '__dict__')
        values['failed'] = True
        status['group_acquired'] = values.get('group') is not None
        if acquisition:
            errors.extend(_native_release_owned(state))
        for row in values['cleanup']:
            status[row['name'] + '_closed'] = row['closed']
    status['private_names_remaining'] = [name for _, _, name in NATIVE_MEMBERS.values() if name in sys.modules]
    BaseException.__setattr__(primary, '__som_native_payload_partial_rollback__', status)
    BaseException.__setattr__(primary, '__som_native_payload_cleanup_secondary__', tuple(errors))


def _native_acquire(ctx):
    global _NATIVE_OWNER
    controls = _native_controls(ctx)
    if _NATIVE_FAILURE is not None:
        raise PayloadError('native payload owner is poisoned') from _NATIVE_FAILURE
    if _NATIVE_OWNER is not None:
        return _native_current(ctx)[0]
    profile, profile_sha = _native_profile(ctx, controls)
    need(all(name not in sys.modules for _, _, name in NATIVE_MEMBERS.values())
         and PRODUCER_MODULE not in sys.modules, 'initial native private/producer namespace is preloaded')
    # Construct the source-owned journal before acquiring a resource.
    state = _NativeOwner(ctx, controls, None, None, {}, profile_sha)
    lease = controls.borrow()
    state.lease = lease
    state.anchor = (ctx, controls, lease, None, ())
    state.acquisition = (ctx, controls, lease)
    _NATIVE_OWNER = state
    # No operation that can load a private member precedes this ownership row.
    _native_owned_close(state, 'borrow', lease, type(controls))
    initial_members = state.members
    cleanup, cleanup_errors = state.cleanup, state.cleanup_errors
    borrow_row = cleanup[0]
    group_returned = False
    try:
        group = lease.load_private_group('m6.api.payload')
        group_returned = True
        state.group = group
        _native_owned_close(state, 'private4', group, __som_native_Group__)
        members = {key: group[key] for key in NATIVE_MEMBERS}
        state.members = members
        state.anchor = (ctx, controls, lease, group, tuple(members.items()))
        state.phase = 'live'
        _native_current(ctx)
        files = members['retained'].sealed_files(controls.root)
        need(type(files) is dict and files.get(NATIVE_OWNER_PROFILE) == profile_sha,
             'native owner profile lacks reviewed numbered React source binding')
        return state
    except BaseException as primary:
        _native_poison(state, primary, acquisition=True)
        # Core owns failed partial-group rollback. Retire only our fully closed
        # borrow-only acquisition; retain every unresolved resource journal.
        if (group_returned is False and _NATIVE_OWNER is state
                and _NATIVE_FAILURE is primary and type(state) is _NativeOwner):
            values = object.__getattribute__(state, '__dict__')
            status = BaseException.__getattribute__(primary, '__som_native_payload_partial_rollback__')
            acquisition = values.get('acquisition')
            if (type(values) is dict and values.get('ctx') is ctx
                    and values.get('controls') is controls and values.get('lease') is lease
                    and type(acquisition) is tuple and len(acquisition) == 3
                    and acquisition[0] is ctx and acquisition[1] is controls and acquisition[2] is lease
                    and values.get('group') is None
                    and values.get('members') is initial_members and type(initial_members) is dict
                    and len(initial_members) == 0
                    and values.get('producer') is None and values.get('binding') is None
                    and values.get('runtime') is None and values.get('committed') is False
                    and values.get('failed') is True and values.get('closed') is True
                    and type(values.get('phase')) is str and values['phase'] == 'closed'
                    and values.get('cleanup') is cleanup and type(cleanup) is list and len(cleanup) == 1
                    and cleanup[0] is borrow_row and type(borrow_row) is dict
                    and type(borrow_row.get('name')) is str and borrow_row['name'] == 'borrow'
                    and borrow_row.get('value') is lease and borrow_row.get('kind') is type(controls)
                    and borrow_row.get('attempted') is True and borrow_row.get('closed') is True
                    and borrow_row.get('error') is None and borrow_row.get('traceback') is None
                    and values.get('cleanup_errors') is cleanup_errors
                    and type(cleanup_errors) is list and len(cleanup_errors) == 0
                    and type(status) is dict and status.get('group_acquired') is False
                    and status.get('private4_closed') is False and status.get('borrow_closed') is True
                    and status.get('producer_retained_until_outer_owner_cleanup') is False
                    and type(status.get('private_names_remaining')) is list
                    and len(status['private_names_remaining']) == 0
                    and all(name not in sys.modules for _, _, name in NATIVE_MEMBERS.values())
                    and PRODUCER_MODULE not in sys.modules):
                _NATIVE_OWNER = None
        raise


@contextmanager
def _native_helpers(ctx):
    state = _native_acquire(ctx)
    was_committed = state.committed
    primary, trace, secondary = None, None, []
    try:
        yield state.members['api'], state.members['live'], state
    except BaseException as error:
        primary, trace = error, error.__traceback__
    finally:
        for action in (lambda: _native_current(ctx), lambda: _native_verify_sources(ctx)):
            try:
                action()
            except BaseException as error:
                if primary is None:
                    primary, trace = error, error.__traceback__
                else:
                    secondary.append(error)
        if primary is not None:
            _native_poison(state, primary, acquisition=not was_committed)
            previous = BaseException.__getattribute__(primary, '__som_native_payload_cleanup_secondary__')
            BaseException.__setattr__(primary, '__som_native_payload_cleanup_secondary__', (*secondary, *previous))
            raise primary.with_traceback(trace)
        try:
            need(type(state.producer) is ModuleType and type(state.binding) is dict and type(state.runtime) is tuple,
                 'native helper use did not complete its actual runtime handoff')
        except BaseException as error:
            _native_poison(state, error, acquisition=not was_committed)
            raise
        state.committed = True


def _native_verify_sources(ctx):
    _native_controls(ctx)
    ctx.sources.verify()


def _native_state_for_live(live, repo, revision):
    need(type(_NATIVE_OWNER) is _NativeOwner, 'native payload owner is not acquired')
    ctx = object.__getattribute__(_NATIVE_OWNER, '__dict__').get('ctx')
    state, profile = _native_current(ctx)
    need(type(live) is ModuleType and live is state.members['live']
         and type(repo) is type(state.controls.root) and repo == state.controls.root
         and type(revision) is str and revision == state.controls.helper_revision,
         'actual native live caller/source owner differs')
    return state, profile


def _native_accept_producer(live, producer):
    need(type(_NATIVE_OWNER) is _NativeOwner, 'actual native owner state required')
    ctx = object.__getattribute__(_NATIVE_OWNER, '__dict__').get('ctx')
    controls = _native_controls(ctx)
    state, _ = _native_state_for_live(live, controls.root, controls.helper_revision)
    need(type(producer) is ModuleType and sys.modules.get(PRODUCER_MODULE) is producer,
         'actual Core native producer required')
    data = ModuleType.__getattribute__(producer, '__dict__')
    need(data.get('__name__') == PRODUCER_MODULE and data.get('__file__') == str(state.controls.root / PRODUCER)
         and data.get('__som_raw_sha256__') == NATIVE_PRODUCER_SHA,
         'actual native producer source/namespace differs')
    need(state.producer is None or state.producer is producer, 'native producer ownership changed')
    state.producer = producer


def _native_bind_live(live, ctx, repo, runtime):
    state, _ = _native_current(ctx)
    need(live is state.members['live'] and repo == state.controls.root and type(runtime) is dict
         and runtime is ctx.runtime(), 'actual native runtime handoff required')
    binding, _, _ = live.bound_sources(repo, ctx.helper_revision)
    need(type(binding) is dict and binding is ModuleType.__getattribute__(live, '__dict__').get('_binding')
         and binding.get('native_owner') is state and type(binding.get('repo')) is type(repo) and binding['repo'] == repo
         and type(binding.get('revision')) is str and binding['revision'] == ctx.helper_revision
         and binding.get('producer') is state.producer,
         'actual native live binding owner differs')
    names = ('reuse', 'qualification', 'runtime_inventory', 'tools', 'native')
    values = tuple(runtime[key] for key in ('reuse', 'qualification', 'inventory', 'tools', 'identity'))
    if state.binding is None:
        need(all(binding.get(key) is None for key in names), 'initial native live runtime state is not fresh')
        binding.update(zip(names, values, strict=True))
        state.binding, state.runtime = binding, values
    else:
        need(state.binding is binding and type(state.runtime) is tuple and len(state.runtime) == len(names)
             and all(binding.get(key) is before is current for key, before, current in zip(names, state.runtime, values, strict=True)),
             'native live/runtime pointer identity changed')


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


def load_source(ctx, relative, name, *, scope=None):
    need(not _native_route(), 'native payload uses only fixed Core roles')
    need(name not in sys.modules, 'preloaded verifier refused: ' + name)
    raw = ctx.sources.read(relative)
    need(type(raw) is bytes, 'bound source bytes required')
    module = ModuleType(name)
    module.__file__ = str(ctx.repo / relative)
    module.__cached__ = None
    module.__package__ = ''
    if scope is not None:
        module.__dict__['__builtins__'] = scope.builtins
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


class _PrivateImports:
    """Resolve only our fixed helpers privately; delegate all other imports."""
    NAMES = frozenset(('retained_evidence', 'linux_api_context', 'linux_api_live_inputs', 'linux_qa_profile'))

    def __init__(self):
        self.modules = {}
        self.builtins = dict(vars(builtins))
        self.builtins['__import__'] = self.import_module

    def import_module(self, name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in self.NAMES:
            need(name in self.modules, 'private source import is not bound')
            return self.modules[name]
        return builtins.__import__(name, globals, locals, fromlist, level)


@contextmanager
def _private_helpers(ctx):
    """Compile fresh authenticated modules without touching collector names."""
    if _native_route():
        with _native_helpers(ctx) as result:
            yield result
        return
    modules = {}
    scope = _PrivateImports()
    try:
        for relative, name, alias in ((RETAINED, '_som_react_private_retained', 'retained_evidence'),
                                      (API, '_som_react_api_payload_context', 'linux_api_context'),
                                      (LIVE, '_som_react_api_payload_live', 'linux_api_live_inputs')):
            modules[name] = load_source(ctx, relative, name, scope=scope)
            scope.modules[alias] = modules[name]
        private_sys = _PrivateSys(modules['_som_react_private_retained'])
        modules['_som_react_api_payload_context'].sys = private_sys
        modules['_som_react_api_payload_live'].sys = private_sys
        yield modules['_som_react_api_payload_context'], modules['_som_react_api_payload_live'], scope
    finally:
        for name, module in reversed(tuple(modules.items())):
            if sys.modules.get(name) is module:
                del sys.modules[name]


def _bind_live(live_verifier, ctx, repo, runtime):
    if _native_route():
        return _native_bind_live(live_verifier, ctx, repo, runtime)
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
    if _native_route():
        _native_profile(ctx, _native_controls(ctx))
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
        with _private_helpers(ctx) as (api, live_verifier, _scope):
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
        with _private_helpers(ctx) as (api, live, scope):
            repo2, path2, profile, auth, auth_sha, profile_sha, receipt_sha = api.inputs(path, repo)
            need(repo2 == repo and path2 == path and profile['runtime_root'] == str(runtime_root)
                 and auth_sha == proof['api_authority_sha256'] and profile_sha == proof['api_profile_sha256']
                 and receipt_sha == proof['live_receipt_sha256'], 'collector profile or receipt differs')
            need(all(auth['source_sha256'][name] == hashlib.sha256(ctx.sources.read(name)).hexdigest()
                     for name in (RETAINED, API, LIVE, NODE, PRODUCER, REUSE, QUALIFY)),
                 'collector source authorities differ')
            node_name = '_som_react_api_payload_node'
            if _native_route():
                state, _ = _native_current(ctx)
                need(scope is state, 'collector native private owner differs')
                node = state.members['node']
            else:
                node = load_source(ctx, NODE, node_name, scope=scope)
                scope.modules['linux_qa_profile'] = node
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
                if not _native_route() and sys.modules.get(node_name) is node:
                    del sys.modules[node_name]
