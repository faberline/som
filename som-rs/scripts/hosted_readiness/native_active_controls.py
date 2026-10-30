"""Native finite-role loaders. Entry source and all executable bytes stay bound.

Only the opaque source-bootstrap handoff may construct an active owner. This is
a loader-origin control, not blanket immutability of every QA data namespace.
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import threading
from types import CellType, CodeType, FunctionType, GetSetDescriptorType, MappingProxyType, MethodType, ModuleType

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'native_active_controls.py'
BOOTSTRAP = BASE + 'native_source_bootstrap.py'
POLICY = BASE + 'native_active_roles.json'
SCHEMA = BASE + 'native_active_roles.schema.json'
SCHEMA_SHA256 = '29711f2ac4a3a8f1afccbf21625238ff28f63214fde1f2bf9f2615cf40ca7942'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
CORE_MODULE = '_som_native_active_controls'
ENTRY_MODULE = '_som_native_trusted_bootstrap_entry'
SEED_NAMES = MappingProxyType({
    'bootstrap.graph': '_som_bootstrap_same_candidate_graph',
    'bootstrap.authority': '_som_same_candidate_native_authority',
    'bootstrap.core': CORE_MODULE,
    'bootstrap.entry': ENTRY_MODULE,
})
PRIVATE = MappingProxyType({
    'retained': ('m6.api.payload.retained', 'som-rs/scripts/react_output_acceptance/retained_evidence.py', '_som_react_private_retained'),
    'api': ('m6.api.payload.api', 'som-rs/scripts/react_output_acceptance/linux_api_context.py', '_som_react_api_payload_context'),
    'live': ('m6.api.payload.live', 'som-rs/scripts/react_output_acceptance/linux_api_live_inputs.py', '_som_react_api_payload_live'),
    'node': ('m6.api.payload.node', 'som-rs/tests/react-output-v1/linux_qa_profile.py', '_som_react_api_payload_node'),
})
_KEY = object()
_REAL_IMPORT = builtins.__import__
CONTEXT_BINDING = '_som_native_active_context_binding'
ROLE_BINDING = '_som_native_active_controls_binding'
# These actual objects are captured while the checked bootstrap executes this
# module body. Later sys.modules aliases cannot manufacture this handoff.
_ENTRY_SEED = sys.modules.get(ENTRY_MODULE)
_INITIAL_MAIN = sys.modules.get('__main__')
_OWNER_TYPE = vars(_INITIAL_MAIN).get('_BootstrapLease') if type(_INITIAL_MAIN) is ModuleType else None
_TRANSFER_TYPE = vars(_ENTRY_SEED).get('_ActiveTransfer') if type(_ENTRY_SEED) is ModuleType else None
_OWNER = sys._getframe(1).f_locals.get('self')
_ACTIVATE = vars(_OWNER_TYPE).get('activate') if type(_OWNER_TYPE) is type else None
_TRANSFER_VERIFY = vars(_TRANSFER_TYPE).get('verify_factory_call') if type(_TRANSFER_TYPE) is type else None


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(rows):
        result = {}
        for name, value in rows:
            require(name not in result, 'duplicate active policy member')
            result[name] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite active policy')))


def relative(path):
    require(type(path) is str and path and not path.startswith('/') and '\\' not in path
            and all(part not in {'', '.', '..'} for part in path.split('/'))
            and not any(ord(char) < 32 or ord(char) == 127 for char in path), 'unsafe active source path')
    return path


def freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(freeze(item) for item in value)
    return value


def equal(left, right):
    return type(left) is type(right) and canonical(left) == canonical(right)


def validate(value, rule, schema):
    """Validate the finite frozen schema subset; never resolve external refs."""
    if '$ref' in rule:
        ref = rule['$ref']
        require(ref.startswith('#/$defs/') and ref.count('/') == 2, 'external active schema reference')
        return validate(value, schema['$defs'][ref.rsplit('/', 1)[1]], schema)
    if 'oneOf' in rule:
        passed = 0
        for option in rule['oneOf']:
            try:
                validate(value, option, schema)
            except ValueError:
                continue
            passed += 1
        require(passed == 1, 'active policy binding variant differs')
        return
    if 'const' in rule:
        require(equal(value, rule['const']), 'active policy constant differs')
    if 'enum' in rule:
        require(any(equal(value, allowed) for allowed in rule['enum']), 'active policy enum differs')
    if 'type' in rule:
        expected = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'integer': int}[rule['type']]
        require(type(value) is expected, 'active policy field type differs')
    if type(value) is dict:
        require(set(rule.get('required', ())) <= set(value), 'active policy field missing')
        properties = rule.get('properties', {})
        for key, item in value.items():
            require(type(key) is str, 'active policy key must be text')
            if 'propertyNames' in rule:
                validate(key, rule['propertyNames'], schema)
            if key in properties:
                validate(item, properties[key], schema)
            else:
                additional = rule.get('additionalProperties', True)
                require(additional is not False, 'unknown active policy field')
                if type(additional) is dict:
                    validate(item, additional, schema)
    elif type(value) is list:
        require(len(value) >= rule.get('minItems', 0) and len(value) <= rule.get('maxItems', 10**6), 'active policy array bound')
        if rule.get('uniqueItems'):
            require(len({canonical(item) for item in value}) == len(value), 'duplicate active policy array row')
        for item in value:
            if 'items' in rule:
                validate(item, rule['items'], schema)
    elif type(value) is str:
        require(len(value) >= rule.get('minLength', 0)
                and ('pattern' not in rule or re.fullmatch(rule['pattern'], value)), 'active policy text differs')
    elif type(value) is int:
        require(value >= rule.get('minimum', -2**63), 'active policy integer bound')


def code_tree(code):
    result = [code]
    for value in code.co_consts:
        if type(value) is CodeType:
            result.extend(code_tree(value))
    return tuple(result)


def select_nodes(raw, selector):
    tree = ast.parse(raw)
    require(any(isinstance(node, ast.ImportFrom) and node.module == '__future__'
                and any(alias.name == 'annotations' for alias in node.names) for node in tree.body),
            'fragment source must retain original future annotations')
    wanted = selector['names']
    selected = []
    found = []
    kind = selector['kind']
    def visit(body, scope):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = '.'.join((*scope, node.name))
                if ((kind == 'top-level-functions' and not scope and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
                        or kind == 'qualified-function' and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))) and name in wanted:
                    selected.append(node); found.append(name)
                visit(node.body, (*scope, node.name))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = ['.'.join((*scope, target.id)) for target in targets if isinstance(target, ast.Name)]
                if kind in {'top-level-assignments', 'qualified-assignments'} and (not scope or kind == 'qualified-assignments'):
                    hits = [name for name in names if name in wanted]
                    if hits:
                        require(len(hits) == len(names) == len(targets) == 1, 'fragment assignment target changed')
                        selected.append(node); found.extend(hits)
    visit(tree.body, ())
    require(len(found) == len(wanted) and set(found) == set(wanted), 'fragment selector missing or ambiguous')
    original = ast.Module(body=selected, type_ignores=[])
    require(sha(ast.dump(original, include_attributes=False).encode()) == selector['ast_sha256'],
            'exact original fragment AST differs')
    result = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                             *selected], type_ignores=[])
    ast.fix_missing_locations(result)
    return result


def static_edges(reader, roots, paths, search_roots):
    paths = set(paths)
    pending, seen, edges = set(roots), set(), set()
    def candidates(parts, local=None):
        result = set()
        for prefix in (local,) if local is not None else search_roots:
            stem = prefix + ('/' + '/'.join(parts) if parts else '')
            for path in (stem + '.py', stem + '/__init__.py'):
                if path in paths:
                    result.add(path)
                    for count in range(1, len(parts)):
                        parent = prefix + '/' + '/'.join(parts[:count]) + '/__init__.py'
                        if parent in paths:
                            result.add(parent)
        return result
    while pending:
        path = min(pending); pending.remove(path)
        if path in seen:
            continue
        seen.add(path)
        for node in ast.walk(ast.parse(reader(path), filename=path)):
            imports = []
            if isinstance(node, ast.Import):
                imports = [(alias.name, alias.name.split('.'), None) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                local = None
                if node.level:
                    parts = path.split('/')[:-1]
                    require(node.level <= len(parts), 'active relative import escapes source')
                    local = '/'.join(parts[:len(parts) - node.level + 1])
                parts = node.module.split('.') if node.module else []
                imports = [('.' * node.level + (node.module or ''), parts, local)]
                imports.extend(('.' * node.level + '.'.join(parts + [alias.name]), parts + [alias.name], local)
                               for alias in node.names if alias.name != '*')
            for spelling, parts, local in imports:
                for target in candidates(parts, local):
                    edges.add((path, spelling, target)); pending.add(target)
    return [{'caller': owner, 'import': spelling, 'source': target} for owner, spelling, target in sorted(edges)]


class _Policy:
    def __init__(self, transfer):
        raw, schema_raw = transfer.read(POLICY), transfer.read(SCHEMA)
        require(sha(schema_raw) == SCHEMA_SHA256, 'fixed active role schema changed')
        schema, value = parse(schema_raw), parse(raw)
        validate(value, schema, schema)
        require(value['complete'] is True and value['pending'] == [], 'active finite role policy remains pending')
        design = value['design']
        for key, expected in (
                ('source_paths', '42e096d840170e2f8d00ec6bfc704670cf8c39a3a4ecbfb95138faff6f51ad2a'),
                ('edges', '1a6fc76e38703ebe7894f39a8cf6effc6538a5d62a7b70f8a73f65b21aec2910'),
                ('search_roots', '3621fc0bcd55eb0f8a9b216a79b5197c1146fd4ea1bbec8c00addeb795debf2c')):
            require(sha(canonical(design[key])) == expected, 'exact117/327/16 design inventory differs')
        sources, roles = value['sources'], value['roles']
        require(set(design['source_paths']) <= set(sources) and {SELF, BOOTSTRAP} <= set(sources),
                'complete design/core source map missing')
        require(set(sources) <= set(transfer.admission['admitted_repository_python_sources']), 'role source outside actual native admission')
        for path, record in sources.items():
            relative(path)
            require(path.endswith('.py') and sha(transfer.read(path)) == record['sha256']
                    and transfer.mode(path) == record['mode'], 'exact active source map changed')
        actual_edges = static_edges(transfer.read, sources, transfer.source_paths, design['search_roots'])
        require(actual_edges == value['static_edges']
                and all(edge['source'] in sources for edge in actual_edges), 'complete actual static source closure differs')
        names = {}
        for role, row in roles.items():
            require(row['source'] in sources and role in row['closure'] and set(row['closure']) <= set(roles),
                    'unknown role source/closure')
            require((row['kind'] == 'fragment') == ('selector' in row), 'fragment selector role differs')
            require(row['kind'] == 'entry' or row['module'] not in names, 'active role module namespace collision')
            if row['kind'] != 'entry':
                names[row['module']] = role
            if row['kind'] == 'entry':
                require(row['module'] == '__main__' and row['package'] == '' and row['is_package'] is False,
                        'fixed entry module shape differs')
            else:
                require(row['module'] != '__main__', 'nonentry role cannot replace main')
            for caller in row['callers']:
                require(caller['role'] in roles, 'unknown actual caller role')
            for binding in row['bindings'].values():
                dependency = binding.get('role') if binding['kind'] in {'role-module', 'role-export'} else binding.get('type_role')
                require(dependency is None or dependency in row['closure'], 'binding role missing from checked target closure')
                if binding['kind'] == 'source-constant':
                    require(binding['source'] in sources, 'binding constant source outside checked source map')
            if row['kind'] == 'seed':
                require(not row['bindings'], 'admission seed cannot gain a retrospective active binding')
        for role, name in SEED_NAMES.items():
            require(role in roles and roles[role]['kind'] == 'seed' and roles[role]['module'] == name,
                    'actual bootstrap seed role missing')
        require({role for role, row in roles.items() if row['kind'] == 'seed'} == set(SEED_NAMES),
                'unlisted admission seed role')
        for key, (role, path, name) in PRIVATE.items():
            row = roles.get(role)
            require(row is not None and row['kind'] == 'private' and row['source'] == path
                    and row['module'] == name and row['group'] == 'm6.api.payload', 'fixed private group member differs')
        require(set(row['group'] for row in roles.values()) <= {None, 'm6.api.payload'}, 'unknown private group')
        require('m6.api.producer' in roles and roles['m6.api.producer']['kind'] == 'module',
                'fixed separate API producer role missing')
        for caller in value['groups']['m6.api.payload']['callers']:
            require(caller['role'] in roles, 'unknown private group caller role')
        for key, role in value['context_keys'].items():
            require(role in roles and roles[role]['kind'] in {'module', 'seed'}, 'unknown Context key role')
        for bridge in value['bridges']:
            require(bridge['role'] in roles, 'unknown bridge role')
            if bridge['kind'] == 'context-module':
                require(roles[bridge['role']]['source'] == CONTEXT and bridge['function'] == 'Context.module',
                        'unlisted transparent Context bridge')
        for rule in value['imports']:
            require(rule['caller'] in roles and rule['result'] in roles
                    and set(rule['from_roles'].values()) <= set(roles), 'unknown fixed import role')
            caller = roles[rule['caller']]
            calls = []
            for node in ast.walk(ast.parse(transfer.read(caller['source']))):
                if isinstance(node, ast.Import):
                    calls.extend((alias.name, 0, []) for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    calls.append((node.module or '', node.level, [alias.name for alias in node.names]))
            require((rule['name'], rule['level'], rule['fromlist']) in calls,
                    'fixed import rule is not an actual original source import')
            targets = {rule['result'], *rule['from_roles'].values()}
            require(targets <= set(caller['closure']), 'fixed import target missing from caller closure')
            spellings = {'.' * rule['level'] + rule['name']}
            spellings.update('.' * rule['level'] + '.'.join(filter(None, (rule['name'], name)))
                             for name in rule['fromlist'] if name != '*')
            possible = {edge['source'] for edge in actual_edges
                        if edge['caller'] == caller['source'] and edge['import'] in spellings}
            require({roles[target]['source'] for target in targets} <= possible,
                    'fixed import target differs from source-derived import resolution')
        self.data, self.sha256 = freeze(value), sha(raw)


class _Record:
    def __init__(self, role, row, module, code):
        self.role, self.row, self.module, self.namespace = role, row, module, module.__dict__
        self.codes = {id(item): item for item in code_tree(code)}
        self.executing, self.thread = True, threading.get_ident()
        self.failure = None
        self.owned_exports = {}
        self.typed_classes = {}
        self.fixed_bindings = {}
        self.fixed_export_classes = {}
        self.injections, self.safe_builtins = {}, None
        self.key, self.name, self.entry_exit = role, module.__name__, 0


class _PrivateModules:
    __slots__ = ('_domain', '_members')
    def __init__(self, domain, members):
        self._domain, self._members = domain, members
    def _aliases(self):
        result = {'retained_evidence': self._members['retained']}
        producer = self._domain.records.get('m6.api.producer')
        if producer is not None:
            result['som_react_api_verified_wheel_producer'] = producer.module
        return result
    def __getitem__(self, name):
        if name in {'retained_evidence', 'som_react_api_verified_wheel_producer'}:
            return self._aliases()[name]
        return sys.modules[name]
    def get(self, name, default=None):
        try:
            return self[name]
        except KeyError:
            return default
    def __contains__(self, name):
        return name in self._aliases() if name in {'retained_evidence', 'som_react_api_verified_wheel_producer'} else name in sys.modules
    def __setitem__(self, name, value):
        raise ValueError('private source module aliases are read-only; use the fixed role loader')
    def __delitem__(self, name):
        raise ValueError('private source module aliases are read-only')
    def __iter__(self):
        return iter(set(sys.modules) | set(self._aliases()))
    def __len__(self):
        return len(set(sys.modules) | set(self._aliases()))
    def keys(self): return tuple(self)
    def values(self): return tuple(self[name] for name in self)
    def items(self): return tuple((name, self[name]) for name in self)


class _PrivateSys:
    __slots__ = ('modules',)
    def __init__(self, domain, members):
        object.__setattr__(self, 'modules', _PrivateModules(domain, members))
    def __getattr__(self, name):
        return getattr(sys, name)
    def __setattr__(self, name, value):
        raise ValueError('private sys view is read-only')


class _Builtins:
    """Freeze the importer, while preserving real source-owned builtin guards."""
    __slots__ = ('_importer',)
    def __init__(self, importer):
        object.__setattr__(self, '_importer', importer)
    def __getitem__(self, name):
        return self._importer if name == '__import__' else builtins.__dict__[name]
    def __iter__(self):
        return iter(builtins.__dict__)
    def __len__(self):
        return len(builtins.__dict__)
    def keys(self):
        return builtins.__dict__.keys()
    def __setattr__(self, name, value):
        raise ValueError('source importer binding is read-only')


class _Group:
    def __init__(self, domain, members, roles):
        self._domain, self._members, self._roles, self._closed = domain, MappingProxyType(members), roles, False
    def __getitem__(self, name):
        require(not self._closed, 'private group is closed')
        return self._members[name]
    def __iter__(self):
        return iter(self._members)
    def __len__(self):
        return len(self._members)
    def close(self):
        if not self._closed:
            self._domain.remove_roles(self._roles)
            self._closed = True
            self._domain.group = None
    def __enter__(self):
        return self
    def __exit__(self, kind, primary, traceback):
        try:
            self.close()
        except BaseException as secondary:
            if primary is None:
                raise
            self._domain.transfer.secondary(primary, 'private-group-close', secondary)
        return False


class _Fragment:
    __slots__ = ('_domain', '_record', '_closed')
    def __init__(self, domain, record):
        self._domain, self._record, self._closed = domain, record, False
    def __getattr__(self, name):
        require(not self._closed, 'fragment scope is closed')
        self._domain.check_exports(self._record)
        return getattr(self._record.module, name)
    def close(self):
        if not self._closed:
            self._domain.remove_roles((self._record.key,))
            self._closed = True
    def __enter__(self): return self
    def __exit__(self, kind, primary, traceback):
        try:
            self.close()
        except BaseException as secondary:
            if primary is None:
                raise
            self._domain.transfer.secondary(primary, 'fragment-scope-close', secondary)
        return False


class _Domain:
    def __init__(self, key, transfer, policy):
        require(key is _KEY, 'opaque source bootstrap handoff required')
        self.transfer, self.policy = transfer, policy.data
        self.root, self.revision = transfer.root, transfer.revision
        self.records, self.codes, self.bindings = {}, {}, {}
        self.lock, self.condition = threading.RLock(), threading.Condition(threading.RLock())
        self.closed, self.group = False, None
        self.entry_depth, self.suspended_mains = 0, []
        self.fragment_counter = 0
        self.owned_names, self.package_changes = [], []
        self.finder_lease = self.finder = self.spec = None
        self.finder_methods, self.manual_adapter = [], None
        self.import_frames = self._import_frames()
        self.std_codes = self._stdlib_codes()
        for role, name in SEED_NAMES.items():
            old = transfer.seed(name)
            row = self.policy['roles'][role]
            require(old['path'] == row['source'] and old['globals'] is old['module'].__dict__, 'opaque seed record differs')
            self.enroll(role, row, old['module'], old['code']).executing = False
        try:
            self.install()
        except BaseException as primary:
            try:
                self.close()
            except BaseException as secondary:
                self.transfer.secondary(primary, 'active-install-rollback', secondary)
            raise

    def _stdlib_codes(self):
        result = {}
        for module in tuple(sys.modules.values()):
            if type(module) is not ModuleType:
                continue
            namespace = module.__dict__
            origin = namespace.get('__file__')
            if origin is not None and (type(origin) is not str or not Path(origin).absolute().is_relative_to(Path(sys.base_prefix).absolute())):
                continue
            if origin is None and module.__name__ not in sys.builtin_module_names and module.__name__ not in {'importlib._bootstrap', 'importlib._bootstrap_external'}:
                continue
            for value in tuple(namespace.values()):
                if type(value) is FunctionType and value.__globals__ is namespace:
                    for code in code_tree(value.__code__):
                        result[id(code)] = (code, namespace)
        return result

    def _import_frames(self):
        result = {}
        for name in ('importlib', 'importlib._bootstrap', 'importlib._bootstrap_external'):
            module = sys.modules[name]
            for value in vars(module).values():
                if type(value) is FunctionType and value.__globals__ is vars(module):
                    for code in code_tree(value.__code__):
                        result[id(code)] = (code, vars(module))
        return result

    def enroll(self, role, row, module, code):
        require(role not in self.records, 'role already owns a module namespace')
        record = _Record(role, row, module, code)
        self.records[role] = record
        for identity, actual in record.codes.items():
            self.codes.setdefault(identity, []).append((actual, record))
        return record

    def record_for_frame(self, frame):
        matches = [record for actual, record in self.codes.get(id(frame.f_code), ())
                   if actual is frame.f_code and record.namespace is frame.f_globals]
        require(len(matches) == 1, 'actual caller CodeType/globals are not registered')
        return matches[0]

    def origin(self, frame, requested=None):
        skipped_bridge = False
        while frame is not None:
            machinery = self.import_frames.get(id(frame.f_code))
            if machinery is not None and machinery[0] is frame.f_code and machinery[1] is frame.f_globals:
                frame = frame.f_back
                continue
            record = self.record_for_frame(frame)  # Never skip an unknown foreign frame.
            bridge = [row for row in self.policy['bridges'] if row['role'] == record.role
                      and row['function'] == frame.f_code.co_qualname]
            if bridge:
                require(not skipped_bridge and len(bridge) == 1 and requested is not None, 'unlisted bridge chain')
                rule = bridge[0]
                if rule['kind'] == 'context-module':
                    path, key = frame.f_locals.get('path'), frame.f_locals.get('key')
                    require(type(key) is str and self.policy['context_keys'].get(key) == requested, 'Context key/role differs')
                    require(type(path) is str or type(path) is type(Path()), 'foreign Context path object')
                    text = str(path)
                    wanted = self.policy['roles'][requested]['source']
                    require(text in {wanted, str(self.root / wanted)}, 'Context source path differs from fixed role')
                else:
                    row = self.policy['roles'][requested]
                    require(row['kind'] == 'fragment', 'fragment wrapper used for another role')
                    path = frame.f_locals.get(rule.get('source_local', ''))
                    names = frame.f_locals.get(rule.get('names_local', ''))
                    require(type(path) is str and path == row['source'] and type(names) in {list, tuple, set, frozenset}
                            and all(type(name) is str for name in names)
                            and set(names) == set(row['selector']['names']), 'source fragment wrapper arguments differ')
                skipped_bridge = True
                frame = frame.f_back
                continue
            self.check_exports(record)
            return record, frame
        raise ValueError('no actual originating caller')

    def authorize(self, row, frame, role):
        caller, origin = self.origin(frame, role)
        require(any(rule['role'] == caller.role and rule['function'] == origin.f_code.co_qualname
                    for rule in row['callers']), 'fixed caller-to-target role is not admitted')
        return caller, origin

    def callable_origin(self, function):
        matches = [record for actual, record in self.codes.get(id(function.__code__), ())
                   if actual is function.__code__ and record.namespace is function.__globals__]
        return matches[0] if len(matches) == 1 else None

    def check_function(self, function):
        if type(function) is MethodType:
            function = function.__func__
        require(type(function) is FunctionType and self.source_callable(function),
                'foreign callable code/globals cannot become a source export')
        owner = self.callable_origin(function)
        if owner is not None and owner.safe_builtins is not None:
            require(function.__builtins__ is owner.safe_builtins, 'source function lost its actual protected importer')
        state, references = self.callable_state(function)
        previous = self.bindings.get(id(function))
        require(previous is None or previous[0] is function and previous[1] == state,
                'actual source callable invocation bindings changed')
        if previous is None:
            self.bindings[id(function)] = (function, state, references)

    def source_callable(self, function, seen=None):
        if type(function) is not FunctionType:
            return False
        if self.callable_origin(function) is not None:
            return True
        standard = self.std_codes.get(id(function.__code__))
        if standard is None or standard[0] is not function.__code__ or standard[1] is not function.__globals__:
            return False
        seen = set() if seen is None else seen
        if id(function) in seen:
            return False
        seen.add(id(function))
        for cell in function.__closure__ or ():
            try:
                if self.source_callable(cell.cell_contents, seen):
                    return True
            except ValueError:
                continue
        return False

    def callable_state(self, function):
        seen, references = set(), []
        def state(value):
            kind = type(value)
            if any(kind is accepted for accepted in (str, bytes, int, float, bool, type(None))):
                return ('atom', id(kind), value)
            identity = id(value)
            if identity in seen:
                return ('reference', id(kind), identity)
            seen.add(identity); references.append(value)
            if kind is tuple or kind is list:
                return ('sequence', id(kind), identity, tuple(state(item) for item in value))
            if kind is dict:
                return ('dict', identity, tuple((state(key), state(item)) for key, item in value.items()))
            if kind is set or kind is frozenset:
                return ('set', id(kind), identity, frozenset(state(item) for item in value))
            if kind is CellType:
                try:
                    return ('cell', identity, state(value.cell_contents))
                except ValueError:
                    return ('cell', identity, 'empty')
            if kind is MethodType:
                return ('method', identity, state(value.__func__), state(value.__self__))
            if kind is FunctionType:
                standard = self.std_codes.get(id(value.__code__))
                require(self.callable_origin(value) is not None or standard is not None
                        and standard[0] is value.__code__ and standard[1] is value.__globals__,
                        'unregistered callback in actual invocation binding')
                return ('function', identity, id(value.__code__), id(value.__globals__),
                        state(value.__defaults__), state(value.__kwdefaults__), state(value.__closure__))
            require(not callable(value) or kind is type or kind is type(importlib.abc.MetaPathFinder),
                    'foreign callable object in invocation binding')
            return ('opaque', id(kind), identity)
        return state(function), tuple(references)

    def exported_functions(self, namespace):
        result = {}
        for name, value in tuple(namespace.items()):
            require(type(name) is str, 'source module namespace key type differs')
            if type(value) is FunctionType and self.source_callable(value):
                result[(name,)] = value
            elif type(value) is type or type(value) is type(importlib.abc.MetaPathFinder):
                attributes = type.__getattribute__(value, '__dict__')
                for key, item in attributes.items():
                    if type(item) is classmethod or type(item) is staticmethod:
                        item = item.__func__
                    if type(item) is FunctionType and self.source_callable(item):
                        result[(name, key)] = item
        return result

    def export_at(self, namespace, path):
        value = namespace.get(path[0])
        if len(path) == 2:
            require(type(value) is type or type(value) is type(importlib.abc.MetaPathFinder), 'source class export changed type')
            value = type.__getattribute__(value, '__dict__').get(path[1])
            if type(value) is classmethod or type(value) is staticmethod:
                value = value.__func__
        return value

    def typed_class_at(self, record, name):
        """Read a declared class path without module or metaclass hooks."""
        parts = name.split('.')
        require(all(type(key) is str for key in record.namespace), 'typed source namespace key type differs')
        value = record.namespace.get(parts[0], _KEY)
        for part in parts[1:]:
            require(type(value) is type, 'typed class path has a foreign owner')
            attributes = type.__getattribute__(value, '__dict__')
            require(all(type(key) is str for key in attributes), 'typed class namespace key type differs')
            value = attributes.get(part, _KEY)
        require(type(value) is type, 'typed local requires an ordinary source-owned class')
        return value

    def typed_class_members(self, owner, attributes):
        """Only source functions or exact generated descriptors may run here."""
        require(all(type(name) is str for name in attributes), 'typed class namespace key type differs')
        if owner is object:  # This built-in base cannot acquire new Python hooks.
            return
        for name, value in attributes.items():
            kind = type(value)
            if kind is FunctionType:
                self.check_function(value)
            elif kind is classmethod or kind is staticmethod:
                self.check_function(object.__getattribute__(value, '__func__'))
            elif kind is property:
                for slot in ('fget', 'fset', 'fdel'):
                    function = object.__getattribute__(value, slot)
                    if function is not None:
                        self.check_function(function)
            elif kind is GetSetDescriptorType and name in {'__dict__', '__weakref__'}:
                require(object.__getattribute__(value, '__objclass__') is owner,
                        'typed class generated descriptor has a foreign owner')
            else:
                # Do not invoke a descriptor or inspect attributes through its
                # metaclass. An unregistered callable/descriptor cannot enter
                # the typed instance through a class member.
                require(type(kind) is type, 'foreign typed member metaclass')
                ancestry = type.__getattribute__(kind, '__mro__')
                require(not callable(value) and not any(
                    slot in type.__getattribute__(base, '__dict__')
                    for base in ancestry for slot in ('__get__', '__set__', '__delete__')),
                    'foreign callable or descriptor in typed class namespace')

    def capture_typed_classes(self, record):
        """Pin only classes used by local-instance recipes, before export."""
        names = {binding['type_name'] for row in self.policy['roles'].values()
                 for binding in row['bindings'].values()
                 if binding['kind'] == 'local-instance' and binding['type_role'] == record.role}
        for name in sorted(names):
            if name in record.typed_classes:
                continue
            original = self.typed_class_at(record, name)
            attributes = type.__getattribute__(original, '__dict__')
            module_name = attributes.get('__module__')
            require(type(module_name) is str and module_name == record.name,
                    'typed class did not originate in its actual source role')
            mro = type.__getattribute__(original, '__mro__')
            require(type(mro) is tuple and mro and mro[-1] is object
                    and all(type(base) is type for base in mro), 'typed class MRO has a foreign owner')
            namespaces = []
            for base in mro:
                attributes = type.__getattribute__(base, '__dict__')
                self.typed_class_members(base, attributes)
                namespaces.append((base, dict(attributes)))
            record.typed_classes[name] = (original, mro, tuple(namespaces))

    def verify_typed_classes(self):
        """Reject changed typed classes before binding or fragment export use."""
        for record in tuple(self.records.values()):
            for name, (original, expected_mro, namespaces) in tuple(record.typed_classes.items()):
                require(self.typed_class_at(record, name) is original,
                        'source-owned typed class export changed')
                mro = type.__getattribute__(original, '__mro__')
                require(type(mro) is tuple and len(mro) == len(expected_mro)
                        and all(actual is expected for actual, expected in zip(mro, expected_mro)),
                        'source-owned typed class MRO changed')
                for base, expected in namespaces:
                    attributes = type.__getattribute__(base, '__dict__')
                    require(all(type(key) is str for key in attributes)
                            and len(attributes) == len(expected)
                            and all(key in attributes and attributes[key] is value
                                    for key, value in expected.items()),
                            'source-owned typed class namespace changed')
                    self.typed_class_members(base, attributes)

    def fixed_role_value(self, target, attributes):
        """Read only an actual role module and its declared namespace exports."""
        require(type(target) is _Record and self.records.get(target.key) is target
                and target.failure is None, 'fixed binding lost its original target record')
        module = target.module
        require(type(module) is ModuleType and module.__dict__ is target.namespace
                and sys.modules.get(target.name) is module,
                'fixed binding target module ownership changed')
        value = module
        for name in attributes:
            if type(value) is ModuleType:
                namespace = ModuleType.__getattribute__(value, '__dict__')
            else:
                require(type(value) is type, 'fixed role export path has a foreign owner')
                namespace = type.__getattribute__(value, '__dict__')
            require(all(type(key) is str for key in namespace), 'fixed role export namespace key type differs')
            require(name in namespace, 'fixed role export is absent')
            value = namespace[name]
        return value

    def capture_fixed_export_classes(self, record):
        """Pin only classes named by policy-declared role-export bindings."""
        exports = {tuple(binding['attribute'].split('.')) for row in self.policy['roles'].values()
                   for binding in row['bindings'].values()
                   if binding['kind'] == 'role-export' and binding['role'] == record.role}
        builtin_types = tuple(value for value in builtins.__dict__.values() if type(value) is type)
        for attributes in sorted(exports):
            if attributes in record.fixed_export_classes:
                continue
            original = self.fixed_role_value(record, attributes)
            kind = type(original)
            require(type(kind) is type, 'fixed export value has a foreign metaclass')
            ancestry = type.__getattribute__(kind, '__mro__')
            if not any(base is type for base in ancestry):
                continue
            require(kind is type, 'fixed exported class must have the ordinary metaclass')
            mro = type.__getattribute__(original, '__mro__')
            require(type(mro) is tuple and mro and mro[-1] is object
                    and all(type(base) is type for base in mro), 'fixed exported class MRO has a foreign owner')
            namespaces = []
            for base in mro:
                namespace = type.__getattribute__(base, '__dict__')
                require(all(type(key) is str for key in namespace), 'fixed exported class namespace key type differs')
                builtin_base = any(base is value for value in builtin_types)
                if not builtin_base:
                    self.typed_class_members(base, namespace)
                # Immutable builtin bases may have C descriptors. Pin their
                # namespace identities without treating them as Python hooks.
                namespaces.append((base, dict(namespace), builtin_base))
            record.fixed_export_classes[attributes] = (original, mro, tuple(namespaces))

    def verify_fixed_export_classes(self):
        """Check each declared class and its base state before consumer use."""
        for record in tuple(self.records.values()):
            for attributes, (original, expected_mro, namespaces) in tuple(record.fixed_export_classes.items()):
                require(self.fixed_role_value(record, attributes) is original,
                        'fixed exported class identity changed')
                require(type(original) is type, 'fixed exported class metaclass changed')
                mro = type.__getattribute__(original, '__mro__')
                require(type(mro) is tuple and len(mro) == len(expected_mro)
                        and all(actual is expected for actual, expected in zip(mro, expected_mro)),
                        'fixed exported class MRO changed')
                for base, expected, builtin_base in namespaces:
                    require(type(base) is type, 'fixed exported class base metaclass changed')
                    namespace = type.__getattribute__(base, '__dict__')
                    require(all(type(key) is str for key in namespace)
                            and len(namespace) == len(expected)
                            and all(key in namespace and namespace[key] is value
                                    for key, value in expected.items()),
                            'fixed exported class namespace changed')
                    if not builtin_base:
                        self.typed_class_members(base, namespace)

    def verify_fixed_bindings(self, record):
        self.verify_fixed_export_classes()
        if not record.fixed_bindings:
            return
        require(all(type(name) is str for name in record.namespace), 'fixed binding namespace key type differs')
        for name, (target, attributes, original) in record.fixed_bindings.items():
            require(record.namespace.get(name, _KEY) is original,
                    'declared fixed role binding was replaced')
            require(self.fixed_role_value(target, attributes) is original,
                    'declared fixed role target export changed')

    def check_exports(self, record):
        suspended = record.row['kind'] == 'entry' and any(module is record.module for module in self.suspended_mains)
        require(type(record.module) is ModuleType and record.module.__dict__ is record.namespace
                and (sys.modules.get(record.name) is record.module or suspended), 'owned source module namespace changed')
        for name, value in record.injections.items():
            require(record.namespace.get(name) is value and type(value) is _Lease and value._domain is self,
                    'fixed active-controls binding was replaced')
        if not record.executing:
            self.capture_fixed_export_classes(record)
        self.verify_fixed_bindings(record)
        if record.executing:
            return
        self.capture_typed_classes(record)
        self.verify_typed_classes()
        for name, original in tuple(record.owned_exports.items()):
            current = self.export_at(record.namespace, name)
            self.check_function(current)
        for name, value in self.exported_functions(record.namespace).items():
            self.check_function(value)
            record.owned_exports.setdefault(name, value)

    def prepare(self, role):
        row = self.policy['roles'][role]
        selected = {name: self.policy['roles'][name] for name in row['closure']}
        paths = {record['source'] for record in selected.values()}
        pending, seen = {row['source']}, set()
        while pending:
            source = pending.pop()
            if source in seen:
                continue
            seen.add(source)
            pending.update(edge['source'] for edge in self.policy['static_edges'] if edge['caller'] == source)
        require(seen <= paths, 'target transitive source closure is missing before exec')
        compiled = {}
        for name, record in selected.items():
            raw = self.transfer.read(record['source'])
            require(sha(raw) == self.policy['sources'][record['source']]['sha256'], 'role source bytes changed')
            source = select_nodes(raw, record['selector']) if record['kind'] == 'fragment' else raw
            compiled[name] = compile(source, str(self.root / record['source']), 'exec', dont_inherit=True)
        return compiled

    def binding(self, rule, frame, prepared):
        kind = rule['kind']
        if kind == 'active-controls':
            return _Lease(_KEY, self, False)
        if kind == 'stdlib':
            require(rule['module'].split('.')[0] in sys.stdlib_module_names, 'nonstdlib binding')
            value = _REAL_IMPORT(rule['module'], fromlist=['*'])
            origin = getattr(value, '__file__', None)
            require(origin is None or Path(origin).absolute().is_relative_to(Path(sys.base_prefix).absolute()),
                    'stdlib binding origin differs')
            if 'attribute' in rule:
                for name in rule['attribute'].split('.'):
                    value = getattr(value, name)
            return value
        if kind in {'role-module', 'role-export'}:
            module = self.load_known(rule['role'], prepared, frame)
            target = self.records[rule['role']]
            require(target.module is module, 'fixed binding target record differs')
            attributes = tuple(rule['attribute'].split('.')) if kind == 'role-export' else ()
            value = self.fixed_role_value(target, attributes)
            if attributes and type(value) is type:
                require(attributes in target.fixed_export_classes
                        and target.fixed_export_classes[attributes][0] is value,
                        'original fixed exported class pin is missing')
            return value
        if kind == 'source-constant':
            require(rule['source'] in self.policy['sources'], 'constant source outside policy')
            nodes = ast.parse(self.transfer.read(rule['source'])).body
            values = [node.value for node in nodes if isinstance(node, ast.Assign)
                      and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == rule['name']]
            values += [node.value for node in nodes if isinstance(node, ast.AnnAssign)
                       and isinstance(node.target, ast.Name) and node.target.id == rule['name']]
            require(len(values) == 1, 'exact source constant missing or ambiguous')
            node = values[0]
            if isinstance(node, ast.Call):
                require(isinstance(node.func, ast.Name) and node.func.id == 'frozenset'
                        and len(node.args) == 1 and not node.keywords, 'unlisted constant expression')
                return frozenset(ast.literal_eval(node.args[0]))
            return ast.literal_eval(node)
        require(frame is not None, 'typed local binding requires an actual originating frame')
        value = frame.f_locals.get(rule['local'])
        if kind == 'local-data':
            self.typed(value, rule['type'])
            return value
        require(kind == 'local-instance', 'unknown fixed binding kind')
        module = self.load_known(rule['type_role'], prepared, frame)
        owner = self.records[rule['type_role']]
        require(owner.module is module and rule['type_name'] in owner.typed_classes,
                'original source-owned typed class pin is missing')
        self.verify_typed_classes()
        expected = owner.typed_classes[rule['type_name']][0]
        require(type(value) is expected, 'local object is not the exact source-owned class')
        data = object.__getattribute__(value, '__dict__')
        require(type(data) is dict and all(type(key) is str for key in data)
                and set(data) == set(rule['fields']), 'source-owned local object fields differ')
        for name, expected_type in rule['fields'].items():
            self.typed(data[name], expected_type)
        return value

    def typed(self, value, kind):
        if kind == 'path':
            require(type(value) is type(Path()) and value.is_absolute() and '..' not in value.parts, 'typed local physical locator required')
        elif kind == 'json':
            seen = set()
            def json_data(item):
                if any(type(item) is scalar for scalar in (str, int, bool, type(None))):
                    return
                require(id(item) not in seen, 'cyclic local JSON data')
                seen.add(id(item))
                if type(item) is list:
                    for child in item: json_data(child)
                else:
                    require(type(item) is dict and all(type(key) is str for key in item), 'foreign local JSON object')
                    for child in item.values(): json_data(child)
                seen.remove(id(item))
            json_data(value)
        else:
            require(type(value) is {'int': int, 'str': str, 'bool': bool}[kind], 'typed local scalar differs')

    def import_for(self, record):
        def checked_import(name, globals=None, locals=None, fromlist=(), level=0):
            frame = sys._getframe(1)
            require(frame.f_globals is record.namespace and self.record_for_frame(frame) is record,
                    'foreign importer CodeType/globals refused')
            require(frame.f_builtins is record.safe_builtins, 'actual importer binding changed')
            names = list(fromlist or ())
            if name in {CONTEXT_BINDING, ROLE_BINDING}:
                self.check_guard()
                require(level == 0 and names == ['controls'], 'reserved active binding import shape differs')
                if name == CONTEXT_BINDING:
                    require(record.row['source'] == CONTEXT
                            and frame.f_code.co_qualname == 'Context._native_source_controls',
                            'reserved active Context binding has a foreign caller')
                else:
                    require(record.row['kind'] != 'seed' and record.row['source'] != CONTEXT,
                            'reserved general role binding cannot replace a seed or Context getter')
                self.check_exports(record)
                handle = record.injections.get('__som_native_active_controls__')
                require(type(handle) is _Lease and handle._domain is self and not handle._closed
                        and record.namespace.get('__som_native_active_controls__') is handle,
                        'actual fixed Context control binding was replaced')
                view = ModuleType(name)
                view.controls = handle
                return view
            matches = [rule for rule in self.policy['imports'] if rule['caller'] == record.role
                       and rule['name'] == name and rule['level'] == level and list(rule['fromlist']) == names]
            if matches:
                require(len(matches) == 1, 'ambiguous fixed source import')
                rule = matches[0]; prepared = self.prepare(rule['result'])
                self.authorize(self.policy['roles'][rule['result']], frame, rule['result'])
                result = self.load_known(rule['result'], prepared, frame)
                for attribute, role in rule['from_roles'].items():
                    self.authorize(self.policy['roles'][role], frame, role)
                    target = self.load_known(role, self.prepare(role), frame)
                    old = vars(result).get(attribute, _KEY)
                    require(old is _KEY or old is target, 'package attribute collision')
                    if old is _KEY:
                        setattr(result, attribute, target); self.package_changes.append((result, attribute, target))
                return result
            if record.row['group'] == 'm6.api.payload':
                if level == 0 and name == 'sys' and record.role in {'m6.api.payload.api', 'm6.api.payload.live'}:
                    return _PrivateSys(self, self.group_members())
                aliases = {'retained_evidence': 'retained', 'linux_api_context': 'api',
                           'linux_api_live_inputs': 'live', 'linux_qa_profile': 'node'}
                if level == 0 and name in aliases:
                    role = PRIVATE[aliases[name]][0]
                    self.authorize(self.policy['roles'][role], frame, role)
                    return self.load_known(role, self.prepare(role), frame)
            return _REAL_IMPORT(name, globals, locals, fromlist, level)
        return checked_import

    def load_known(self, role, prepared, frame, *, supplied=None, fresh=False):
        require(role in prepared or role in SEED_NAMES, 'role missing from checked execution closure')
        row = self.policy['roles'][role]
        require(row['source'] in self.finder.allowed, 'role is outside original finder admission')
        with self.lock:
            key, name = role, row['module']
            if fresh:
                require(row['kind'] == 'fragment' and supplied is None, 'fresh instance is only a fixed fragment')
                self.fragment_counter += 1
                key = role + '#' + str(self.fragment_counter)
                name += '__instance_' + str(self.fragment_counter)
            existing = self.records.get(key)
            if existing is not None:
                require(existing.failure is None, 'prior source execution failed')
                require(not existing.executing or existing.thread == threading.get_ident(), 'concurrent partial module refused')
                self.check_exports(existing)
                return existing.module
            require(row['kind'] != 'seed', 'actual opaque seed record missing')
            require(supplied is not None or name not in sys.modules, 'preloaded source module refused')
            module = supplied if supplied is not None else ModuleType(name)
            require(type(module) is ModuleType and module.__name__ == name, 'source module identity differs')
            module.__file__, module.__package__, module.__cached__ = str(self.root / row['source']), row['package'], None
            if supplied is None and row['kind'] != 'entry':
                spec = importlib.machinery.ModuleSpec(name, self.finder, origin=module.__file__, is_package=row['is_package'])
                spec.has_location = True
                module.__spec__, module.__loader__ = spec, self.finder
                if row['is_package']:
                    module.__path__ = [str((self.root / row['source']).parent)]
                    spec.submodule_search_locations = module.__path__
            module.__som_raw_sha256__ = self.policy['sources'][row['source']]['sha256']
            injections, fixed_bindings = {}, {}
            for binding_name, binding in row['bindings'].items():
                require(binding['kind'] != 'active-controls' or row['kind'] != 'seed'
                        and binding_name == '__som_native_active_controls__', 'unlisted control binding')
                module.__dict__[binding_name] = self.binding(binding, frame, prepared)
                if binding['kind'] == 'active-controls':
                    injections[binding_name] = module.__dict__[binding_name]
                elif binding['kind'] in {'role-module', 'role-export'}:
                    attributes = tuple(binding['attribute'].split('.')) if binding['kind'] == 'role-export' else ()
                    fixed_bindings[binding_name] = (self.records[binding['role']], attributes, module.__dict__[binding_name])
            code = prepared[role]
            if fresh:
                record = _Record(role, row, module, code)
                record.key = key
                self.records[key] = record
                for identity, actual in record.codes.items():
                    self.codes.setdefault(identity, []).append((actual, record))
            else:
                record = self.enroll(role, row, module, code)
            # The exact same code below and exact globals are now registered.
            record.injections, record.fixed_bindings = injections, fixed_bindings
            record.safe_builtins = _Builtins(self.import_for(record))
            module.__builtins__ = record.safe_builtins
            sys.modules[name] = module
            self.owned_names.append((name, module, key))
        try:
            self.verify_fixed_bindings(record)
            try:
                exec(code, record.namespace)
            except SystemExit as terminal:
                if row['kind'] != 'entry' or not (terminal.code is None or type(terminal.code) is int):
                    raise
                record.entry_exit = 0 if terminal.code is None else terminal.code
            record.executing = False
            self.check_exports(record)
            return module
        except BaseException as primary:
            record.failure, record.executing = primary, False
            try:
                self.remove_roles((key,))
            except BaseException as secondary:
                self.transfer.secondary(primary, 'role-exec-cleanup', secondary)
            raise

    def group_members(self):
        return {name: self.records[role].module for name, (role, _, _) in PRIVATE.items() if role in self.records}

    def load(self, role, frame, fragment=False):
        self.check_guard()
        require(type(role) is str and role in self.policy['roles'], 'unlisted fixed role')
        row = self.policy['roles'][role]
        require(row['kind'] not in {'entry', 'private'} and (row['kind'] == 'fragment') == fragment,
                'wrong finite role API')
        require(row['kind'] != 'seed' or role == 'bootstrap.authority',
                'admission-only seed is unavailable as a retrospective QA controller')
        _, origin = self.authorize(row, frame, role)
        prepared = self.prepare(role)
        module = self.load_known(role, prepared, origin, fresh=fragment)
        if fragment:
            records = [record for record in self.records.values() if record.module is module]
            require(len(records) == 1, 'actual fresh fragment record missing')
            return _Fragment(self, records[0])
        return module

    def load_group(self, name, frame):
        self.check_guard()
        require(name == 'm6.api.payload' and self.group is None, 'unknown or active private group')
        self.authorize(self.policy['groups'][name], frame, PRIVATE['api'][0])
        roles = tuple(PRIVATE[key][0] for key in PRIVATE)
        prepared = {}
        for role in roles:
            prepared.update(self.prepare(role))
            require(self.policy['roles'][role]['module'] not in sys.modules, 'private group namespace collision')
        members = {}
        try:
            for key, (role, _, _) in PRIVATE.items():
                members[key] = self.load_known(role, prepared, frame)
            self.group = _Group(self, members, roles)
            return self.group
        except BaseException as primary:
            try:
                self.remove_roles(roles)
            except BaseException as secondary:
                self.transfer.secondary(primary, 'private-group-exec-cleanup', secondary)
            raise

    def execute_entry(self, role, argv, frame):
        self.check_guard()
        require(type(role) is str and role in self.policy['roles'] and self.policy['roles'][role]['kind'] == 'entry',
                'unlisted native entry role')
        require(role not in self.records, 'active entry role cannot enter itself again')
        require(type(argv) in {list, tuple} and len(argv) <= 4096
                and all(type(value) is str and '\0' not in value and len(value) <= 1024**2 for value in argv),
                'native entry argv must be bounded text data')
        caller, origin = self.authorize(self.policy['roles'][role], frame, role)
        if self.entry_depth == 0:
            require(caller.role == 'bootstrap.entry' and origin.f_code.co_qualname == '_trusted_run',
                    'only the fresh ledger-owned bootstrap entry may initially launch QA')
            self.transfer.verify_entry_source(self.policy['roles'][role]['source'])
        prepared = self.prepare(role)
        old_main, old_argv = sys.modules['__main__'], sys.argv
        if self.entry_depth == 0:
            require(self.transfer.initial_main is old_main, 'initial trusted main changed before entry')
        else:
            require(any(record.module is old_main and record.row['kind'] == 'entry' for record in self.records.values()),
                    'nested main is not an actual registered entry')
            self.suspended_mains.append(old_main)
        sys.argv = [str(self.root / self.policy['roles'][role]['source']), *argv]
        del sys.modules['__main__']
        self.entry_depth += 1
        primary = None
        try:
            self.load_known(role, prepared, origin)
            return self.records[role].entry_exit
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                self.remove_roles((role,))
            except BaseException as secondary:
                if primary is None:
                    raise
                self.transfer.secondary(primary, 'entry-close', secondary)
            finally:
                sys.modules['__main__'], sys.argv = old_main, old_argv
                self.entry_depth -= 1
                if self.suspended_mains and self.suspended_mains[-1] is old_main:
                    self.suspended_mains.pop()

    def install(self):
        authority = self.transfer.authority
        require(not any(type(finder) is authority.RawQaFinder for finder in sys.meta_path),
                'an existing finder without this opaque active ledger cannot be adopted')
        lease = authority.install_raw_finder(self.transfer.sources.read, self.root, self.transfer.admission)
        self.finder_lease, self.finder, self.spec = lease, lease.controls, importlib.util.spec_from_file_location
        self.meta_path = tuple(sys.meta_path)
        self.old_find, self.old_exec = self.finder.find_spec, self.finder.exec_module
        self.finder_state = (self.finder.reader, self.finder.root, frozenset(self.finder.allowed),
                             canonical(self.finder.rules), self.finder.original_spec, self.finder.raw_spec)
        domain = self
        def find_spec(finder, fullname, path=None, target=None):
            domain.check_guard()
            require(type(fullname) is str and (path is None or type(path) in {list, tuple}
                    and all(type(value) is str for value in path)), 'foreign source import resolver arguments')
            spec = domain.old_find(fullname, path, target)  # Keep original resolver/refusals.
            if spec is None:
                return None
            matches = [role for role, row in domain.policy['roles'].items()
                       if row['module'] == fullname and row['source'] == Path(spec.origin).relative_to(domain.root).as_posix()]
            require(target is None and len(matches) == 1, 'automatic source import has no fixed role')
            role = matches[0]
            domain.authorize(domain.policy['roles'][role], sys._getframe(1), role)
            domain.prepare(role)
            return spec
        def exec_module(finder, module):
            domain.check_guard()
            require(module.__spec__.loader is finder, 'actual source finder loader differs')
            origin, raw = finder.pending.pop(module.__name__)
            matches = [role for role, row in domain.policy['roles'].items()
                       if row['module'] == module.__name__ and domain.root / row['source'] == origin]
            require(len(matches) == 1 and raw == domain.transfer.read(domain.policy['roles'][matches[0]]['source']),
                    'actual automatic source pending bytes differ')
            role = matches[0]
            _, frame = domain.authorize(domain.policy['roles'][role], sys._getframe(1), role)
            domain.load_known(role, domain.prepare(role), frame, supplied=module)
        def manual_exec(loader, module):
            domain.check_guard()
            require(type(loader) is authority.BoundManualSourceLoader and type(module) is ModuleType
                    and type(loader.path) is str and type(loader.module_name) is str,
                    'actual original manual loader/module type required')
            matches = [role for role, row in domain.policy['roles'].items()
                       if row['module'] == loader.module_name and row['source'] == loader.path]
            require(len(matches) == 1, 'original common spec target has no fixed role')
            role = matches[0]
            caller, frame = domain.authorize(domain.policy['roles'][role], sys._getframe(1), role)
            require(module.__name__ == loader.module_name and module.__spec__.loader is loader
                    and loader.reader is domain.finder.reader and loader.reader(loader.path) == loader.raw
                    and loader.root == domain.root, 'original manual loader/spec/source validation differs')
            require(caller.row['source'] in authority.MANUAL_SOURCE_CALLERS
                    and loader.path == authority.MANUAL_SOURCE_PATH and loader.module_name == authority.MANUAL_SOURCE_MODULE,
                    'only original two common-to-Rust-builder specs are available')
            module.__som_manual_raw_source__ = loader.path
            domain.load_known(role, domain.prepare(role), frame, supplied=module)
        self.finder.find_spec, self.finder.exec_module = MethodType(find_spec, self.finder), MethodType(exec_module, self.finder)
        self.finder_methods = [('find_spec', self.finder.find_spec), ('exec_module', self.finder.exec_module)]
        self.finder_bindings = [self.callable_state(value.__func__) for _, value in self.finder_methods]
        self.manual_adapter = manual_exec
        self.transfer.install_manual_adapter(manual_exec, self)

    def check_guard(self):
        require(not self.closed and self.finder is not None, 'active source owner is closed')
        self.transfer.verify_sources()
        require(len(sys.meta_path) == len(self.meta_path)
                and all(left is right for left, right in zip(sys.meta_path, self.meta_path))
                and importlib.util.spec_from_file_location is self.spec, 'actual finder/spec ownership changed')
        self.finder.verify_installed()
        reader, root, allowed, rules, original, raw_spec = self.finder_state
        require(self.finder.reader is reader and self.finder.root == root
                and type(self.finder.allowed) is set and frozenset(self.finder.allowed) == allowed
                and type(self.finder.rules) is list and canonical(self.finder.rules) == rules
                and self.finder.original_spec is original and self.finder.raw_spec is raw_spec,
                'original finder source admission or manual rules changed')
        for (name, value), (expected, _) in zip(self.finder_methods, self.finder_bindings):
            require(getattr(self.finder, name) is value, 'owned finder adapter changed')
            current, _ = self.callable_state(value.__func__)
            require(current == expected, 'owned finder adapter invocation bindings changed')
        self.transfer.verify_manual_adapter(self.manual_adapter, self)

    def verify(self):
        self.check_guard()
        self.transfer.verify_sources()
        for record in tuple(self.records.values()):
            self.check_exports(record)

    def remove_roles(self, roles):
        errors = []
        for name, module, role in reversed(tuple(self.owned_names)):
            if role not in roles:
                continue
            if sys.modules.get(name) is module:
                del sys.modules[name]
            else:
                errors.append(ValueError('foreign module replacement kept during owned cleanup'))
            self.records.pop(role, None)
            self.owned_names.remove((name, module, role))
            for identity in tuple(self.codes):
                self.codes[identity] = [(code, record) for code, record in self.codes[identity] if record.key != role]
                if not self.codes[identity]:
                    del self.codes[identity]
        if errors:
            for error in errors[1:]: self.transfer.secondary(errors[0], 'owned-module-cleanup', error)
            raise errors[0]

    def close(self):
        if self.closed:
            return
        errors = []
        for action in (self.verify, self.restore_packages, lambda: self.remove_roles(tuple(self.records)),
                       lambda: self.transfer.restore_manual_adapter(self.manual_adapter, self)):
            try:
                action()
            except BaseException as error:
                errors.append(error)
        for name, installed in reversed(self.finder_methods):
            if getattr(self.finder, name, None) is installed:
                delattr(self.finder, name)
            else:
                errors.append(ValueError('foreign finder method kept during cleanup'))
        if self.finder_lease is not None:
            try:
                self.finder_lease.close()
            except BaseException as error:
                errors.append(error)
        self.closed = True
        if errors:
            for error in errors[1:]: self.transfer.secondary(errors[0], 'active-owner-close', error)
            raise errors[0]

    def restore_packages(self):
        errors = []
        for module, name, target in reversed(self.package_changes):
            if vars(module).get(name) is target:
                delattr(module, name)
            else:
                errors.append(ValueError('foreign package attribute kept during owned cleanup'))
        self.package_changes.clear()
        if errors:
            for error in errors[1:]: self.transfer.secondary(errors[0], 'owned-package-cleanup', error)
            raise errors[0]


class _Lease:
    __slots__ = ('_domain', '_owns', '_closed')
    def __init__(self, key, domain, owns):
        require(key is _KEY, 'opaque active owner required')
        self._domain, self._owns, self._closed = domain, owns, False
    @property
    def root(self): return self._domain.root
    @property
    def helper_revision(self): return self._domain.revision
    @property
    def candidate_revision(self): return self._domain.revision
    @property
    def context_keys(self): return self._domain.policy['context_keys']
    @property
    def role_sources(self): return MappingProxyType({role: row['source'] for role, row in self._domain.policy['roles'].items()})
    def verify_installed(self):
        require(not self._closed, 'active source lease is closed')
        self._domain.verify()
    def borrow(self):
        require(not self._closed and not self._domain.closed, 'active source owner is closed')
        return _Lease(_KEY, self._domain, False)
    def load_module(self, role):
        require(not self._closed, 'active source lease is closed')
        return self._domain.load(role, sys._getframe(1))
    def load_fragment(self, role):
        require(not self._closed, 'active source lease is closed')
        return self._domain.load(role, sys._getframe(1), fragment=True)
    def load_private_group(self, role):
        require(not self._closed, 'active source lease is closed')
        return self._domain.load_group(role, sys._getframe(1))
    def execute_entry(self, role, argv):
        require(not self._closed, 'active source lease is closed')
        return self._domain.execute_entry(role, argv, sys._getframe(1))
    def close(self):
        if not self._closed:
            if self._owns:
                self._domain.close()
            self._closed = True
    def __enter__(self):
        self.verify_installed()
        return self
    def __exit__(self, kind, primary, traceback):
        try:
            self.close()
        except BaseException as secondary:
            if primary is None:
                raise
            self._domain.transfer.secondary(primary, 'active-lease-close', secondary)
        return False


def _activate_from_bootstrap(transfer):
    """Private fixed bridge. No path, bytes, namespace or ledger dict parameter."""
    frame = sys._getframe(1)
    require(type(_ACTIVATE) is FunctionType and frame.f_code is _ACTIVATE.__code__
            and frame.f_globals is _ACTIVATE.__globals__ is vars(_INITIAL_MAIN)
            and type(_OWNER) is _OWNER_TYPE and frame.f_locals.get('self') is _OWNER
            and sys.modules.get(ENTRY_MODULE) is _ENTRY_SEED
            and type(_TRANSFER_VERIFY) is FunctionType and type(transfer) is _TRANSFER_TYPE
            and vars(_TRANSFER_TYPE).get('verify_factory_call') is _TRANSFER_VERIFY
            and object.__getattribute__(transfer, '_owner') is _OWNER,
            'actual fresh trusted bootstrap handoff type required')
    _TRANSFER_VERIFY(transfer, frame, sys.modules[CORE_MODULE])
    transfer.verify_sources()
    transfer.sources.verify()
    result = transfer.authority.verify(transfer.sources.read, mode='native-qa-v1',
        source_paths=transfer.source_paths, helper_revision=transfer.revision, candidate_revision=transfer.revision)
    require(equal(result, transfer.admission) and result['source_authority_active'] is True
            and result['product_acceptance_passed'] is False and result['compiler_acceptance_passed'] is False
            and result['whole_acceptance_complete'] is False, 'actual native source authority changed before activation')
    policy = _Policy(transfer)
    transfer.verify_operational_policy(POLICY, SCHEMA)
    domain = _Domain(_KEY, transfer, policy)
    return _Lease(_KEY, domain, True)
