#!/usr/bin/env python3
"""Capture all RS22 and coverage6 Python forms from the exact recorded candidate.

This is a current-input capture, not compiler, product or release acceptance.
No before response is used as a current response. Run only on GitHub Linux.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import traceback
from types import MappingProxyType, ModuleType
import urllib.error
import urllib.request

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'run_current_python_companions.py'
MODULES = {name: BASE + name + '.py' for name in (
    'export_goldens', 'raw_digest_compat', 'reconstruct_public_inputs',
    'map_rust_inputs', 'complete_rust_inputs', 'experimental_native_compile',
    'run_experimental_native_compile', 'dependency_candidate')}
MODULES['candidate_carrier'] = BASE + 'product_runtime/candidate_carrier.py'
RS_PACKET = BASE + 'evidence/rs22-fixed-before-capture-inputs.json'
RS_PACKET_SHA = '27bc7b3bd140d8855d3ac1a19b266940ebd85718d28946ccdfeb710fe668e294'
COVERAGE_PACKET = BASE + 'evidence/coverage6-fixed-before-capture-inputs.json'
COVERAGE_PACKET_SHA = '7b0b99ef462ab76656c7f21c93307943a4d93e4a270236eb9f2023359d30f260'
NEGATIVE = 'som-rs/contracts/rust-output-v1/negative-cases.json'
PHASE2 = 'som-rs/contracts/rust-output-v1/phase2/case-inventory.json'
POLICY = 'som-rs/contracts/compiler-v1/selection-policy.json'
BEFORE_BINARY = '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
CANDIDATE = 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'
BINARY_SHA = 'e84ac8f06e243d60d8fc64459d6a5d35cff64d84a85d3e48f3cae275be8d36f5'
PORT = 18978
VERSION = 'som.current-python-companions.v1'
_NATIVE_COMPANION_VARIANTS = {
    '__main__': 'companions.entry',
    '_som_graph_current_companion_data': 'companions.graph',
    '_som_full_current_capture': 'companions.context',
}


def _native_companion_prefix():
    require(type(__name__) is str and __name__ in _NATIVE_COMPANION_VARIANTS,
            'unlisted native companion namespace')
    return _NATIVE_COMPANION_VARIANTS[__name__]

REFUSALS = {'RS12_CNL_V3_FILTER_LIMIT': 'limit', 'RS13_JSON_V3_FILTER_OFFSET': 'offset'}
DIAGNOSTIC_SOURCES = {
    'som-rs/src/python_names.rs': '22ab53da0538580165f792d3c47c0080a12c7dd8c2d86c5881c793827df6beec',
    'som-rs/src/json_diagnostics.rs': '87c38ad0077de8bb5eb1ee9edf5d4ecce7b5fce8fffcb19a4fc190b472d870b6',
    'som-rs/src/strict_json.rs': '06a8947fcfb9d7ade5afd963f06da72696d3845d64a957f881eded0666f37161',
    'som-rs/src/main.rs': '042c9abb969d17fa3f28dba7dfcec459ee63123da4ba5e3fd1645b1104a454c5',
    'som-rs/src/lib.rs': '60ea28026fbcbc72ca623717b81a47236a59e59ffe9f9914591e54730d8c158f',
    POLICY: '6fc29cf4791de4e2c6ec716a03426cf66cb1bdd0c5659c30511b837da8552f56',
}
EVENT_SOURCES = {
    'som-rs/som-targets/src/main.rs': '6f521d6d7f3fa7b074de861da4ce5c15934d312d8ce77464f47cf0c3e9863fe2',
    'som-rs/som-targets/src/lib.rs': '812e0025e930869acb216527fe48461cc0fe694853d1c7be14b3d1e330fadba7',
    'som-rs/src/flow.rs': 'fc8b7ea7d591026aed9380a07641ca206391555e95df15b317b879e0e9a31b75',
    'som-rs/src/compiler.rs': '49c8309e102b1f7c686c8a975ed8b2c81d0133557c0443d8986474ca41cd9f3c',
}
# These are the separately audited fixed-before #57 repairs. They do not
# replace the older rs-products-001 hashes retained in each input packet row.
REPAIRED = {
    'RS07_CNL_FIELD_SELF': '898a13759574d701c3d273dce8177eed1b152362f278283b60bc3ab501a96916',
    'RS14_CNL_V5_FOREIGN_KEY_SELF': '4e265f07e09939cb7bf841e765d072e8ec9297f8caf1481b8803edc92a94279d',
    'RS16_CNL_V5_FIRST_REFUSAL': '1b451a6275813c61a76ddd1abcf91feac2fe8a1fbc6e987828a1dbea3342a72b',
}
FIXED_BEFORE_AUTHORITY = {
    'role': 'fresh fixed-before product-map authority; never a current response',
    'run_id': 40812392485, 'source_revision': 'd423911da37e459dbd56346f5d45f976aa6040ac',
    'binary_sha256': BEFORE_BINARY,
    'independent_actual_byte_audit_sha256': '10f382ef6a4a3d158c921326e665a9db060cb9e16a08f52266c787073e795f19',
    'summary_sha256': '4447fe6485351b7265e3e8927c1fe8024a8e32877d0822d90a5175d178a9e7d8',
    'historical_missing_wires_recovered': False,
}
MAX_RAW, MAX_SOURCE = 64 * 1024**2, 8 * 1024**2


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def ordered(value):
    if type(value) is dict:
        return {key: ordered(value[key]) for key in sorted(value)}
    if type(value) is list:
        return [ordered(item) for item in value]
    return value



def _native_controls(repo=None, revision=None, candidate=None):
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    if revision is not None:
        require(revision == controls.helper_revision == candidate == controls.candidate_revision,
                'active caller helper/candidate identity differs')
    if repo is not None:
        require(Path(repo).absolute() == controls.root, 'active caller repository differs')
    return controls


def _native_secondary(primary, phase, secondary):
    try:
        previous = BaseException.__getattribute__(primary, '__som_native_caller_secondary_errors__')
    except AttributeError:
        previous = ()
    BaseException.__setattr__(primary, '__som_native_caller_secondary_errors__',
                             (*previous, {'phase': phase, 'exception': secondary}))


@contextmanager
def _borrowed_native(sources):
    lease = _native_controls(sources.root, sources.revision, sources.revision).borrow()
    primary = None
    try:
        lease.verify_installed()
        yield lease
        sources.verify()
    except BaseException as error:
        primary = error
        raise
    finally:
        errors = []
        for phase, operation in (('source-postcheck', sources.verify),
                                 ('controls-postcheck', lease.verify_installed),
                                 ('borrow-close', lease.close)):
            try:
                operation()
            except BaseException as secondary:
                errors.append((phase, secondary))
        if errors:
            if primary is None:
                phase, primary = errors.pop(0)
                for phase, secondary in errors:
                    _native_secondary(primary, phase, secondary)
                raise primary
            for phase, secondary in errors:
                _native_secondary(primary, phase, secondary)


def plain_module(source_reader, name):
    """Execute only checked immutable bytes of a stdlib-only data helper."""
    require(name in {'export_goldens', 'raw_digest_compat'}, 'fixed plain helper required')
    raw = source_reader(MODULES[name])
    module = ModuleType('_som_current_companion_verify_' + name)
    module.__file__, module.__package__, module.__cached__ = MODULES[name], '', None
    exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    return module


def descriptor_ids(source_reader):
    tree = ast.parse(source_reader(MODULES['experimental_native_compile']))
    nodes = [row.value for row in tree.body if isinstance(row, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == 'IDS' for target in row.targets)]
    require(len(nodes) == 1, 'one fixed descriptor ID source table required')
    return ast.literal_eval(nodes[0])


def parse(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate JSON member')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))


def relative(name):
    require(type(name) is str and name and not name.startswith('/') and '\\' not in name
            and all(part not in {'', '.', '..'} for part in name.split('/'))
            and not any(ord(c) < 32 or ord(c) == 127 for c in name), 'safe relative file name required')
    return name


def read(path, maximum=MAX_RAW):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
            'linked or traversing input refused')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum,
                'bounded regular single-link file required')
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, 'input exceeds fixed bound')
    return raw


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def save(path, value):
    write(path, compact(value) + b'\n')


class Sources:
    def __init__(self, root, revision):
        self.root, self.revision = Path(root).absolute(), revision
        self.raw, self.pins = {}, {}
        require(re.fullmatch('[0-9a-f]{40}', revision), 'full helper revision required')
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision
                and self.git('rev-parse', '--show-toplevel').decode().strip() == str(self.root),
                'helper source HEAD or repository root differs')
        require(Path(__file__).absolute() == self.root / SELF, 'fixed capture source path required')
        for name in (SELF, *MODULES.values()):
            self.read(name)

    def git(self, *arguments):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *arguments],
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
            stderr=subprocess.DEVNULL, timeout=30)

    def read(self, name):
        relative(name)
        if name not in self.raw:
            entry = self.git('ls-tree', '-z', self.revision, '--', name)
            require(entry.count(b'\0') == 1 and entry.endswith(b'\t' + name.encode() + b'\0')
                    and entry.startswith((b'100644 blob ', b'100755 blob ')), 'regular committed source required')
            raw = self.git('cat-file', 'blob', self.revision + ':' + name)
            require(len(raw) <= MAX_SOURCE and raw == read(self.root / name, MAX_SOURCE),
                    'source differs from committed helper bytes')
            self.raw[name], self.pins[name] = raw, sha(raw)
        return self.raw[name]

    def verify(self):
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == self.revision,
                'helper source HEAD changed')
        for name, raw in self.raw.items():
            require(read(self.root / name, MAX_SOURCE) == raw, 'helper source changed during capture')


class BoundModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, sources):
        self.sources = sources
        self.raw = MappingProxyType({name: sources.read(path) for name, path in MODULES.items()})
        self.loaded = set()

    def find_spec(self, fullname, path=None, target=None):
        if fullname in MODULES:
            require(target is None, 'helper reload refused')
            return importlib.util.spec_from_loader(fullname, self, origin=str(self.sources.root / MODULES[fullname]))
        return None

    def create_module(self, spec):
        return ModuleType(spec.name)

    def exec_module(self, module):
        name = module.__name__
        require(name not in self.loaded, 'helper re-execution refused')
        module.__file__, module.__package__, module.__cached__ = str(self.sources.root / MODULES[name]), '', None
        self.loaded.add(name)
        module.__som_raw_sha256__ = sha(self.raw[name])
        exec(compile(self.raw[name], module.__file__, 'exec', dont_inherit=True), module.__dict__)

    @contextmanager
    def installed(self):
        require(not (set(MODULES) & set(sys.modules)), 'preloaded project helper refused')
        sys.meta_path.insert(0, self)
        try:
            yield {name: importlib.import_module(name) for name in MODULES}
            require(self.loaded == set(MODULES), 'loaded helper closure differs')
            self.sources.verify()
        finally:
            sys.meta_path.remove(self)
            for name in self.loaded:
                sys.modules.pop(name, None)


def packet(sources, name, expected):
    raw = sources.read(name)
    require(sha(raw) == expected, 'public source-derived request packet differs')
    return parse(raw)


def native_source_authority(source_reader, source_paths, helper_revision, candidate_revision):
    require(helper_revision == candidate_revision and candidate_revision != CANDIDATE
            and re.fullmatch('[0-9a-f]{40}', candidate_revision), 'new exact same-source candidate required')
    require(type(source_paths) in {list, tuple, set} and source_paths, 'complete tracked source paths required')
    path = BASE + 'native_qa_authority.py'
    raw = source_reader(path)
    controls = _native_controls(revision=helper_revision, candidate=candidate_revision)
    require(controls.role_sources['bootstrap.authority'] == path, 'fixed native authority source differs')
    module = controls.load_module('bootstrap.authority')
    require(module.__file__ == str(controls.root / path) and module.__som_raw_sha256__ == sha(raw),
            'actual native authority bytes differ')
    record = module.verify(source_reader, mode='native-qa-v1', source_paths=source_paths,
                           helper_revision=helper_revision, candidate_revision=candidate_revision)
    require(record.get('mode') == 'native-qa-v1' and record.get('source_authority_active') is True
            and record.get('helper_revision') == helper_revision and record.get('candidate_revision') == candidate_revision
            and record.get('product_acceptance_passed') is False and record.get('compiler_acceptance_passed') is False
            and record.get('whole_acceptance_complete') is False, 'native source authority remains pending or identity differs')
    return record


def source_paths(sources):
    names = sources.git('ls-tree', '-r', '-z', '--name-only', sources.revision).split(b'\0')
    values = [relative(name.decode('utf-8', 'strict')) for name in names if name]
    require(values == sorted(set(values)), 'complete ordered unique tracked source path set required')
    return values


def selected_current_identity(args, sources):
    mode = getattr(args, 'qa_mode', 'recorded-f59')
    if mode == 'recorded-f59':
        require(args.candidate_revision == CANDIDATE, 'recorded mode admits only original f59 candidate')
        return None
    require(mode == 'native-qa-v1', 'unknown current capture source mode')
    return native_source_authority(sources.read, source_paths(sources), args.helper_revision, args.candidate_revision)


def prepare_jobs(sources, proof_root, modules, candidate_revision=CANDIDATE):
    export, driver = modules['export_goldens'], modules['run_experimental_native_compile']
    rs = packet(sources, RS_PACKET, RS_PACKET_SHA)
    coverage = packet(sources, COVERAGE_PACKET, COVERAGE_PACKET_SHA)
    require(rs['source_revision'] == coverage['source_revision'] == CANDIDATE
            and rs['before_binary_sha256'] == coverage['before_binary_sha256'] == BEFORE_BINARY,
            'source packet roles differ')
    negative = parse(sources.read(NEGATIVE))['cases']
    native = {row['id']: row for row in negative if row['id'].startswith('RS')}
    inventory = parse(sources.read(PHASE2))['rows']
    require(len(rs['rows']) == len(native) == 22
            and [row['id'] for row in rs['rows']] == [row['id'] for row in inventory[-22:]],
            'whole ordered22 RS source scope required')
    proof = parse(read(proof_root / 'manifest.json'))
    require(proof['tested_sha'] == candidate_revision and proof['experiment_complete'] is True
            and proof['passed'] == 2041, 'complete original candidate compile proof required')
    rust_rows = [row for row in proof['rows'] if row['target'] == 'rust'
                 and row['identity']['phase'] == 'phase2' and row['identity']['id'] in native]
    rust = {row['identity']['id']: row for row in rust_rows}
    require(len(rust_rows) == len(rust) == 22 and set(rust) == set(native), 'candidate proof omits an RS input')
    jobs = []
    for row in rs['rows']:
        identity = row['id']
        require(row['native_form'] == native[identity]['form']
                and row['expected_current_compiler_refusal'] == (identity in REFUSALS)
                and row['historical_python_defect_repaired_issue57'] == (identity in REPAIRED),
                'RS source role or refusal identity differs')
        observed = rust[identity]
        raw = read(proof_root / 'rows' / f'{observed["index"]:04d}' / 'request.raw')
        require(sha(raw) == observed['expected_request_sha256'] == row['typed_rust_input']['raw_sha256'],
                'original typed Rust request differs')
        request = parse(raw)
        require(request['target'] == 'rust' and export.value_sha(request['prompt']) == row['prompt_sha256'],
                'RS frozen prompt differs')
        typed = driver.compact({key: value for key, value in request.items() if key != 'target'})
        forms = [('native', row['native_form'], row['native_python_request']),
                 ('typed', 'json', row['fresh_python_typed_request'])]
        for form, syntax, source in forms:
            raw = source['body'].encode()
            require(sha(raw) == source['raw_sha256'] and source['endpoint'] == '/v1/compile'
                    and source['method'] == 'POST', 'RS request wire bytes differ')
            require((raw == native[identity]['python_control_body'].encode()) if form == 'native' else raw == typed,
                    'RS native/typed source-derived request differs')
            body = parse(raw)
            require('target' not in body, 'RS Python companion must omit target')
            expected = None if identity in REFUSALS else REPAIRED.get(identity, row['old_rs_products001']['product_sha256'])
            jobs.append({'cohort': 'rs22', 'id': identity, 'form': form, 'syntax': syntax, 'target': None,
                         'raw': raw, 'request_sha256': sha(raw), 'prompt_sha256': row['prompt_sha256'],
                         'expected_status': 400 if identity in REFUSALS else 200,
                         'expected_files_map_sha256': expected,
                         'historical_old_rs001': row['old_rs_products001'],
                         'known_issue57_repair': identity in REPAIRED,
                         'file_authority_role': 'audited fixed-before #57 repair' if identity in REPAIRED else 'unchanged frozen oldrs001 map'})
    require(len(coverage['rows']) == 6, 'whole six coverage source rows required')
    index_source = coverage['frozen_index']
    index_raw = sources.read(index_source['path'])
    require(sha(index_raw) == index_source['raw_sha256'], 'coverage index differs')
    index = parse(index_raw)['cases']
    require([row['id'] for row in coverage['rows']] == [row['id'] for row in index], 'coverage named order differs')
    for row in coverage['rows']:
        prompt_raw, cnl_raw = (sources.read(row[key]['path']) for key in ('prompt_source', 'cnl_source'))
        require(sha(prompt_raw) == row['prompt_source']['raw_sha256']
                and sha(cnl_raw) == row['cnl_source']['raw_sha256'], 'coverage source bytes differ')
        prompt, cnl = parse(prompt_raw), cnl_raw.decode()
        require(export.value_sha(prompt) == row['prompt_sha256'], 'coverage frozen prompt differs')
        authority = row['historical_authority']
        authority_raw = sources.read(authority['path'])
        require(sha(authority_raw) == authority['raw_sha256'], 'coverage file authority differs')
        frozen = export.pointer(parse(authority_raw), authority['pointer'])
        require(frozen['id'] == row['id'] and frozen['python_files'] == authority['expected_file_sha256'],
                'whole coverage frozen file inventory differs')
        require([(r['form'], r['target']) for r in row['requests']]
                == [('json', None), ('json', 'python'), ('cnl', None), ('cnl', 'python')],
                'all four coverage forms required in fixed order')
        for source in row['requests']:
            syntax, target = source['form'], source['target']
            body = ({'format': 'som.prompt-json.v1', 'prompt': prompt} if syntax == 'json'
                    else {'format': 'som.cnl.v1', 'source': cnl})
            if target is not None:
                body['target'] = target
            raw = driver.compact(body)
            require(raw == source['body'].encode() and sha(raw) == source['raw_sha256']
                    and source['expected_http_status'] == source['assemble_expected_http_status'] == 200
                    and source['method'] == 'POST' and source['endpoint'] == '/v1/compile',
                    'coverage exact source-derived wire or status differs')
            jobs.append({'cohort': 'coverage6', 'id': row['id'], 'form': syntax + '-' + ('none' if target is None else target),
                         'syntax': syntax, 'target': target, 'raw': raw, 'request_sha256': sha(raw),
                         'prompt_sha256': row['prompt_sha256'], 'expected_status': 200,
                         'expected_file_sha256': authority['expected_file_sha256'],
                         'file_authority_role': 'unchanged public frozen complete per-file map'})
    require(Counter(row['cohort'] for row in jobs) == {'rs22': 44, 'coverage6': 24}
            and len({(row['cohort'], row['id'], row['form']) for row in jobs}) == 68
            and sum(row['expected_status'] == 200 for row in jobs) == 64, 'complete68-form input scope differs')
    return jobs


def location(text, start, end):
    def point(offset):
        before = text[:offset]
        return before.count('\n') + 1, offset - before.rfind('\n')
    line, column = point(start)
    end_line, end_column = point(end)
    return {'line': line, 'column': column, 'end_line': end_line, 'end_column': end_column,
            'byte_start': len(text[:start].encode()), 'byte_end': len(text[:end].encode())}


class RefusalOracle:
    def __init__(self, sources, modules):
        self.modules = modules
        for name, pin in DIAGNOSTIC_SOURCES.items():
            require(sha(sources.read(name)) == pin, 'independent compiler diagnostic source differs')
        self.policy = parse(sources.read(POLICY))['python_name_collisions']
        self.negative = {row['id']: row for row in parse(sources.read(NEGATIVE))['cases'] if row['id'] in REFUSALS}
        phase2 = {row['id']: row['phase2_expected'] for row in parse(sources.read(PHASE2))['rows'] if row['id'] in REFUSALS}
        require(set(self.negative) == set(phase2) == set(REFUSALS), 'whole current refusal scope differs')
        for identity, name in REFUSALS.items():
            detail = self.policy['error']['detail'].replace('<path>', 'query.filter_field')
            require(phase2[identity] == {'result': 'refusal', 'rule': 'python_name_collisions', 'code': 'INVALID_VALUE',
                    'detail': detail, 'pointer': '/query/filter_field', 'http_status': 400}, 'frozen collision rule differs')
            require(any(name in row['names'] and 'filter_field' in row['roles']
                        for row in self.policy['versions']['som.prompt.v3']['postgresql']), 'reserved collision name differs')

    def expected(self, job):
        identity, body = job['id'], parse(job['raw'])
        require(identity in REFUSALS and job['expected_status'] == 400, 'no refusal authority for this case')
        name = REFUSALS[identity]
        detail = self.policy['error']['detail'].replace('<path>', 'query.filter_field')
        if job['syntax'] == 'cnl':
            source, matches, offset = body['source'], [], 0
            for line in source.splitlines(keepends=True):
                if line.rstrip('\r\n') == 'Filter exactly on field ' + name + '.':
                    start = offset + len('Filter exactly on field ')
                    matches.append((start, start + len(name)))
                offset += len(line)
            require(len(matches) == 1, 'CNL collision token absent or ambiguous')
            span, pointer = location(source, *matches[0]), '/query/filter_field'
            allowed = self.negative['RS12_CNL_V3_FILTER_LIMIT']['expected']['allowed_form']
        else:
            require(body['prompt']['version'] == 'som.prompt.v3'
                    and body['prompt']['query']['filter_field'] == name, 'typed collision prompt differs')
            node = self.modules['raw_digest_compat']._Document(job['raw']).nodes[('prompt', 'query', 'filter_field')]
            require(node[0] == 'string' and node[3] == name, 'typed collision token differs')
            text = job['raw'].decode()
            start, end = (len(job['raw'][:offset].decode()) for offset in (node[1], node[2]))
            span, pointer = location(text, start, end), '/prompt/query/filter_field'
            allowed = self.negative['RS13_JSON_V3_FILTER_OFFSET']['expected']['allowed_form']
        diagnostic = {'code': 'INVALID_VALUE', **span, 'prompt_pointer': pointer, 'cause': detail, 'allowed_form': allowed}
        if job['form'] == 'native':
            require(job['raw'] == self.negative[identity]['python_control_body'].encode()
                    and diagnostic == self.negative[identity]['expected'], 'native complete frozen diagnostic differs')
        value = {'status': 'error', 'code': 'INVALID_VALUE', 'detail': detail, 'diagnostics': [diagnostic]}
        # Match Rust's recursively ordered JSON object encoding. The expected
        # object and positions come only from pinned sources plus request bytes.
        return compact(ordered(value)), value


def validate_bundle(raw, job, checker, export):
    value = parse(raw)
    require(type(value) is dict and set(value) == {'status', 'bundle'} and value['status'] == 'ok',
            'successful compile reply scope differs')
    bundle = value['bundle']
    require(type(bundle) is dict and set(bundle) == {'version', 'prompt', 'dsl', 'assets', 'hashes'}
            and bundle['version'] == 'som.dsl-bundle.v1', 'Python companion bundle scope differs')
    require(export.value_sha(bundle['prompt']) == job['prompt_sha256'], 'compiled companion prompt differs')
    require(set(bundle['hashes']) == {'prompt_sha256', 'dsl_sha256', 'payload_sha256'}, 'bundle hash fields differ')
    for field, body in (('prompt_sha256', bundle['prompt']), ('dsl_sha256', bundle['dsl']),
                        ('payload_sha256', {k: v for k, v in bundle.items() if k != 'hashes'})):
        require(bundle['hashes'][field] == export.value_sha(body), 'own bundle canonical hash differs')
    formatter = ('python_formatted_formatter' if bundle['prompt']['version'] in {'som.prompt.v6', 'som.prompt.v7'}
                 else 'python_render_only_formatter')
    require(bundle['assets']['backend'] == {'id': checker.IDS['python_backend'], 'sha256': checker.after['python_backend']}
            and bundle['assets']['formatter'] == {'id': checker.IDS[formatter], 'sha256': checker.after[formatter]},
            'companion metadata differs from independently derived source factories')
    return bundle


def product_files(raw, job, export):
    value = parse(raw)
    require(type(value) is dict and set(value) == {'status', 'files'} and value['status'] == 'ok'
            and type(value['files']) is dict and value['files'], 'successful assembly file map missing')
    files = value['files']
    for name, text in files.items():
        relative(name)
        require(type(text) is str and '\0' not in name and ':' not in name, 'unsafe companion product file')
    names = sorted(files)
    require(not any(other.startswith(name + '/') for i, name in enumerate(names) for other in names[i + 1:]),
            'product file/directory collision')
    hashes = {name: sha(text.encode('utf-8', 'strict')) for name, text in sorted(files.items())}
    if job['cohort'] == 'coverage6':
        require(hashes == job['expected_file_sha256'], 'complete frozen coverage file map differs')
    else:
        require(export.value_sha(files) == job['expected_files_map_sha256'], 'complete fixed-before RS file map differs')
    return files, hashes


def assemble(service, raw, driver):
    owner = service.ownership()
    request = urllib.request.Request(f'http://127.0.0.1:{service.port}/v1/assemble', data=raw,
                                    headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with service.opener.open(request, timeout=driver.TIMEOUT) as response:
            return response.status, response.read(MAX_RAW + 1), owner
    except urllib.error.HTTPError as error:
        return error.code, error.read(MAX_RAW + 1), owner


class CaptureStopped(BaseException):
    def __init__(self, records, cause):
        self.records, self.cause = records, cause


def capture_rows(jobs, service, oracle, checker, modules, out):
    driver, export = modules['run_experimental_native_compile'], modules['export_goldens']
    records, primary = [], {}
    for index, job in enumerate(jobs):
        directory = out / 'rows' / f'{index:04d}'
        directory.mkdir(parents=True)
        record = {k: v for k, v in job.items() if k != 'raw'}
        record.update(index=index, state='failed', product_acceptance_passed=False,
                      request_path=f'rows/{index:04d}/compile.request.raw')
        write(out / record['request_path'], job['raw'])
        try:
            status, raw, owner = service.call('/v1/compile', job['raw'])
            require(len(raw) <= MAX_RAW, 'compile response exceeds fixed bound')
            record.update(compile_status=status, compile_response_sha256=sha(raw), compile_ownership=owner,
                          compile_response_path=f'rows/{index:04d}/compile.response.raw')
            write(out / record['compile_response_path'], raw)
            require(status == job['expected_status'], 'current companion HTTP status differs')
            if status == 400:
                expected_raw, expected = oracle.expected(job)
                require(parse(raw) == expected and raw == expected_raw, 'complete source-derived current refusal differs')
                record.update(expected_refusal=expected, diagnostic_authority='fixed source policy and original request token',
                              refusal_verified=True, assemble_state='not_applicable_current_refusal')
            else:
                bundle = validate_bundle(raw, job, checker, export)
                assembly_request = driver.compact({'bundle': bundle})
                write(directory / 'assemble.request.raw', assembly_request)
                astatus, araw, aowner = assemble(service, assembly_request, driver)
                require(len(araw) <= MAX_RAW, 'assembly response exceeds fixed bound')
                record.update(assemble_status=astatus, assemble_request_sha256=sha(assembly_request),
                              assemble_response_sha256=sha(araw), assemble_ownership=aowner,
                              assemble_request_path=f'rows/{index:04d}/assemble.request.raw',
                              assemble_response_path=f'rows/{index:04d}/assemble.response.raw')
                write(out / record['assemble_response_path'], araw)
                require(astatus == 200, 'own-bundle companion assembly must return HTTP200')
                files, hashes = product_files(araw, job, export)
                for name, text in files.items():
                    write(directory / 'product' / name, text.encode('utf-8', 'strict'))
                current = {'compile_raw': raw, 'files': files}
                key = job['cohort'], job['id']
                require(key not in primary or primary[key] == current,
                        'named forms do not return identical current bundle and product bytes')
                primary[key] = current
                record.update(product_dir=f'rows/{index:04d}/product', file_sha256=hashes,
                              files_map_sha256=export.value_sha(files), bundle_hashes=bundle['hashes'],
                              historical_comparison=(None if job['cohort'] != 'rs22' else {
                                  'old_rs001_sha256': job['historical_old_rs001']['product_sha256'],
                                  'same_as_old_rs001': export.value_sha(files) == job['historical_old_rs001']['product_sha256'],
                                  'known_issue57_repair': job['known_issue57_repair']}))
            record['state'] = 'passed'
        except driver.DriverInterrupted as error:
            record.update(error=type(error).__name__, reason=str(error), state='interrupted')
            records.append(record)
            save(directory / 'record.json', record)
            raise CaptureStopped(records, error) from error
        except Exception as error:
            record.update(error=type(error).__name__, reason=str(error), traceback=traceback.format_exc())
        records.append(record)
        save(directory / 'record.json', record)
        print(job['id'], job['form'], record['state'], flush=True)
    return records


def output_inventory(root, exclude_manifest=True):
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink() and (path.is_dir() or path.is_file()), 'linked or special capture output')
        if path.is_file() and (not exclude_manifest or path != root / 'manifest.json'):
            result[path.relative_to(root).as_posix()] = sha(read(path))
    return result



def portable_identity(value):
    if type(value) is dict and {'sha256', 'size', 'mode'} <= set(value):
        return {key: value[key] for key in ('sha256', 'size', 'mode')}
    require(type(value) is dict, 'portable candidate identity must be an object')
    return {key: portable_identity(item) for key, item in value.items()}


def request_vector(jobs):
    return {row['cohort'] + '/' + row['id'] + '/' + row['form']: sha(row['raw']) for row in jobs}


def owner_identity(value):
    require(type(value) is dict and type(value.get('pid')) is int and value['pid'] > 1
            and value.get('pgrp') == value.get('session') == value['pid']
            and type(value.get('start_ticks')) is int and value['start_ticks'] > 0
            and value.get('state') in {'R', 'S', 'D', 'I'}
            and type(value.get('executable_inode')) is int and value['executable_inode'] > 0,
            'owned service process stamp incomplete')
    listener = value.get('listener')
    require(type(listener) is dict and set(listener) == {'table', 'address', 'inode'}
            and listener['table'] == 'tcp' and listener['address'] == '0100007F'
            and type(listener['inode']) is str and listener['inode'].isdecimal()
            and int(listener['inode']) > 0, 'owned exact IPv4 loopback socket missing')
    return {key: value[key] for key in ('pid', 'pgrp', 'session', 'start_ticks', 'executable_inode', 'listener')}



def save_owned_socket_proof(service, out):
    owner = service.ownership()
    proc = Path('/proc') / str(owner['pid'])
    refs = {'process_stat': 'owned-start.proc-stat.raw', 'tcp': 'owned-start.tcp.raw',
            'tcp6': 'owned-start.tcp6.raw', 'file_descriptors': 'owned-start.fd-links.json'}
    for key, source in (('process_stat', proc / 'stat'), ('tcp', proc / 'net/tcp'), ('tcp6', proc / 'net/tcp6')):
        write(out / refs[key], read(source, 4 * 1024**2))
    links = {}
    for path in (proc / 'fd').iterdir():
        try:
            links[path.name] = os.readlink(path)
        except FileNotFoundError:
            pass
    save(out / refs['file_descriptors'], links)
    executable = (proc / 'exe').stat()
    require((executable.st_dev, executable.st_ino) == (service.binary['device'], service.binary['inode']),
            'raw process executable identity differs')
    proof = {'owner': owner, 'raw_paths': refs, 'executable': {'device': executable.st_dev,
             'inode': executable.st_ino, 'target': os.readlink(proc / 'exe')}}
    require(owner_identity(owner) == owner_identity(service.ownership()), 'socket changed during raw process snapshot')
    verify_owned_socket_proof(out, proof, owner_identity(owner), service.argv[0])
    return proof


def verify_owned_socket_proof(root, proof, owner, binary_path):
    require(owner_identity(proof['owner']) == owner and proof['raw_paths'] == {
        'process_stat': 'owned-start.proc-stat.raw', 'tcp': 'owned-start.tcp.raw',
        'tcp6': 'owned-start.tcp6.raw', 'file_descriptors': 'owned-start.fd-links.json'},
        'raw owned process proof scope differs')
    raw = read(root / proof['raw_paths']['process_stat'])
    fields = raw.rsplit(b')', 1)[1].split()
    require(int(raw.split(b' ', 1)[0]) == owner['pid'] and fields[0] != b'Z'
            and int(fields[2]) == int(fields[3]) == owner['pid'] and int(fields[19]) == owner['start_ticks'],
            'raw owned PID, process group, session or start time differs')
    found = []
    for table in ('tcp', 'tcp6'):
        for line in read(root / proof['raw_paths'][table]).splitlines()[1:]:
            values = line.split()
            address, port = values[1].split(b':')
            if values[3] == b'0A' and int(port, 16) == PORT:
                found.append({'table': table, 'address': address.decode(), 'inode': values[9].decode()})
    links = parse(read(root / proof['raw_paths']['file_descriptors']))
    executable = proof['executable']
    require(found == [owner['listener']] and type(links) is dict
            and all(type(name) is str and name.isdecimal() and type(target) is str for name, target in links.items())
            and 'socket:[' + owner['listener']['inode'] + ']' in links.values()
            and executable['inode'] == owner['executable_inode'] and type(executable['device']) is int
            and executable['target'] == binary_path, 'raw socket FD or loaded executable proof differs')


def verify_process_report(report):
    owner = owner_identity(report['owned_start'])
    require(owner_identity(report['health']['owned_socket']) == owner, 'health socket owner differs')
    cleanup = report['cleanup']
    require(cleanup.get('owned_pid') == owner['pid'] and cleanup.get('cleanup_passed') is True
            and cleanup.get('signal_target') == 'owned new process group only'
            and type(cleanup.get('signals')) is list
            and cleanup['signals'] in ([], ['SIGTERM'], ['SIGTERM', 'SIGKILL'])
            and cleanup.get('remaining_owned_session_members') == []
            and cleanup.get('wait_completed') is True and type(cleanup.get('wait_exit_code')) is int
            and cleanup['wait_exit_code'] == cleanup.get('exit_code'), 'owned group cleanup or actual wait incomplete')
    require(report.get('protected_ports_before') == report.get('protected_ports_after')
            and set(report.get('protected_ports_before', {})) == {'8765', '18765'},
            'protected service listener snapshot changed')
    argv = report['launch']['argv']
    require(len(argv) == 6 and argv[1:3] == ['serve', '--assets'] and argv[-2:] == ['--port', str(PORT)],
            'capture may start only the fixed private port')
    return owner


def verify_saved_row(root, index, row, job, checker, export, oracle, owner):
    base = f'rows/{index:04d}'
    require(row.get('index') == index and row['state'] == 'passed'
            and row.get('product_acceptance_passed') is False
            and row['request_path'] == base + '/compile.request.raw'
            and row['compile_response_path'] == base + '/compile.response.raw'
            and read(root / row['request_path']) == job['raw']
            and sha(job['raw']) == row['request_sha256'] == job['request_sha256']
            and row['prompt_sha256'] == job['prompt_sha256'] and row['expected_status'] == job['expected_status']
            and row['compile_status'] == job['expected_status']
            and owner_identity(row['compile_ownership']) == owner,
            'current compile request, status or socket proof differs')
    raw = read(root / row['compile_response_path'])
    require(sha(raw) == row['compile_response_sha256'], 'current compile reply bytes differ')
    if job['expected_status'] == 400:
        expected_raw, expected_value = oracle.expected(job)
        require(raw == expected_raw and parse(raw) == expected_value
                and row.get('refusal_verified') is True and row.get('expected_refusal') == expected_value
                and row.get('assemble_state') == 'not_applicable_current_refusal'
                and not any(key.startswith('assemble_') and key != 'assemble_state' for key in row)
                and not ({'product_dir', 'file_sha256', 'files_map_sha256', 'bundle_hashes'} & set(row)),
                'current full refusal diagnostic or no-assembly proof differs')
        require(parse(read(root / base / 'record.json')) == row, 'saved named row record differs')
        return None, None
    bundle = validate_bundle(raw, job, checker, export)
    require(row.get('assemble_status') == 200 and row.get('assemble_request_path') == base + '/assemble.request.raw'
            and row.get('assemble_response_path') == base + '/assemble.response.raw'
            and row.get('product_dir') == base + '/product'
            and row.get('bundle_hashes') == bundle['hashes']
            and owner_identity(row['assemble_ownership']) == owner,
            'current assembly status, paths or socket proof differs')
    aq, ar = read(root / row['assemble_request_path']), read(root / row['assemble_response_path'])
    require(aq == compact({'bundle': bundle}) and sha(aq) == row['assemble_request_sha256']
            and sha(ar) == row['assemble_response_sha256'], 'current assembly must use its own exact current bundle')
    files, hashes = product_files(ar, job, export)
    require(hashes == row['file_sha256'] and export.value_sha(files) == row['files_map_sha256'],
            'current assembled product differs from frozen independent file authority')
    product = root / row['product_dir']
    require(output_inventory(product, exclude_manifest=False) == hashes
            and all(read(product / relative(name)) == text.encode('utf-8', 'strict') for name, text in files.items()),
            'current complete saved product file universe differs')
    require(parse(read(root / base / 'record.json')) == row, 'saved named row record differs')
    return bundle, files


def verify_event_sources(source_reader):
    require(all(sha(source_reader(name)) == pin for name, pin in EVENT_SOURCES.items()),
            'owning target service event source differs')


def verify_events(root, rows, export):
    raw = read(root / 'server.log')
    events = [parse(line) for line in raw.splitlines() if line.startswith(b'{')]
    require(events and events[0] == {'stage': 'listening', 'address': f'127.0.0.1:{PORT}',
                                    'purpose': 'som.rust-compiler.v1'}, 'owned listening server event differs')
    expected = []
    for row in rows:
        bundle = (parse(read(root / relative(row['compile_response_path'])))['bundle']
                  if row.get('compile_status') == 200 else None)
        expected.append({'stage': 'compile', 'http_status': row['compile_status'],
                         'bundle_bytes': len(export.canonical(bundle).encode()) if bundle is not None else None})
        if row.get('assemble_status') == 200:
            files = parse(read(root / relative(row['assemble_response_path'])))['files']
            expected.append({'stage': 'assemble', 'formatted_bytes': sum(len(text.encode()) for text in files.values())})
    require(len(events) == len(expected) + 1, 'whole server event universe differs')
    for event, wanted in zip(events[1:], expected):
        require(set(event) == set(wanted) | {'total_ms'} and type(event['total_ms']) in {int, float}
                and math.isfinite(event['total_ms']) and event['total_ms'] >= 0
                and {key: value for key, value in event.items() if key != 'total_ms'} == wanted,
                'ordered server status event or own reply byte count differs')
    counts = Counter(row['stage'] for row in events)
    require(counts == {'listening': 1, 'compile': 68, 'assemble': 64}, 'complete68 compile and64 own assembly events required')
    statuses = Counter(row['compile_status'] for row in rows)
    require(statuses == {200: 64, 400: 4}, 'whole68 raw HTTP status scope required')
    return {'counts': dict(counts), 'raw_log_sha256': sha(raw),
            'compile_statuses_from_rows': {str(key): value for key, value in sorted(statuses.items())},
            'source_role': 'pinned som-targets main.rs compile status events and Python assembly byte counts'}


def bind_current_proof_factory_sources(checker, proof, candidate_revision):
    require(proof.get('tested_sha') == candidate_revision and checker.repo.commit == candidate_revision,
            'current factory source candidate identity differs')
    expected = proof['independent_source_record']['source_sha256']
    require(expected and expected == proof['initial_identities']['source'], 'complete trusted current proof source vector required')
    for name, pin in sorted(expected.items()):
        relative(name)
        require(re.fullmatch('[0-9a-f]{64}', pin) and sha(checker.repo.read(name)) == pin,
                'current proof owning source is missing or changed: ' + name)
    record = checker.source_record()
    require(record['commit'] == candidate_revision and record['source_sha256'] == expected
            and record['after_descriptors'] == proof['independent_source_record']['after_descriptors'],
            'current producer must bind the complete trusted proof factory/source vector')
    return record


def run(args, sources, modules):
    driver, carrier, export = (modules[name] for name in ('run_experimental_native_compile', 'candidate_carrier', 'export_goldens'))
    qa_authority = getattr(args, '_native_source_authority', None)
    if getattr(args, 'qa_mode', 'recorded-f59') == 'native-qa-v1':
        require(qa_authority == selected_current_identity(args, sources), 'fresh native source authority required before capture')
    else:
        require(args.candidate_revision == CANDIDATE, 'recorded mode admits only original f59 candidate')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    out, restored = args.out.absolute(), args.restored_candidate.absolute()
    roots = [args.repo.absolute(), args.assembly_root.absolute(), args.carrier_root.absolute(), restored]
    require(temporary in out.parents and temporary in restored.parents and out != restored
            and not any(out == root or root in out.parents or out in root.parents for root in roots)
            and not out.exists() and not restored.exists(), 'new disjoint owned RUNNER_TEMP outputs required')
    require(all(restored != root and root not in restored.parents and restored not in root.parents
                for root in roots[:-1]), 'restored candidate must be outside input/source roots')
    out.mkdir(mode=0o700)
    report = {'version': VERSION, 'role': 'current-candidate-compiled-inputs', 'candidate_revision': args.candidate_revision,
              'qa_mode': getattr(args, 'qa_mode', 'recorded-f59'), 'native_source_authority': qa_authority,
              'helper_revision': args.helper_revision, 'expected_forms': 68, 'expected_positive_assemblies': 64,
              'capture_complete': False, 'product_acceptance_passed': False, 'whole_acceptance_complete': False,
              'before_role': FIXED_BEFORE_AUTHORITY, 'historical_missing_wires_recovered': False,
              'unrun': ['baseline full raw-byte native compatibility', 'full product and behavior gates',
                        'CLI forwarding', 'old bundle refusal/rebase', 'numbered policy and independent acceptance'],
              'rows': [], 'source_bindings': {}, 'started_monotonic': time.monotonic()}
    service, checker, initial, jobs = None, None, None, []
    with driver.cancellation_handlers() as cancellation:
        try:
            guard = modules['dependency_candidate']
            report['candidate_admission'] = guard.verify_candidate_inputs(args.repo, args.helper_revision, args.candidate_revision, args.assembly_root)
            jobs = prepare_jobs(sources, args.assembly_root, modules, args.candidate_revision)
            report['original_requests_before'] = request_vector(jobs)
            oracle = RefusalOracle(sources, modules)
            verify_event_sources(sources.read)
            proof = parse(read(args.assembly_root / 'manifest.json'))
            if qa_authority is not None:
                carrier_input = parse(read(args.carrier_root / 'carrier.json'))
                require(carrier_input.get('compiler_rebuilt') is False
                        and carrier_input.get('github', {}).get('GITHUB_RUN_ID') == os.environ['GITHUB_RUN_ID']
                        and carrier_input.get('github', {}).get('GITHUB_RUN_ATTEMPT') == os.environ['GITHUB_RUN_ATTEMPT'],
                        'new final capture requires the current-run original same-job carrier')
            record = carrier.restore(args.carrier_root, restored, args.assembly_root / 'manifest.json', args.candidate_revision)
            expected_binary = proof['initial_identities']['binary']['sha256']
            require((args.candidate_revision != CANDIDATE or expected_binary == BINARY_SHA)
                    and record['identity']['binary']['sha256'] == expected_binary, 'restored current proof binary identity differs')
            report['carrier'] = {k: v for k, v in record.items() if k in ('carrier_sha256', 'archive_sha256', 'identity')}
            write(out / 'carrier.input.json', read(args.carrier_root / 'carrier.json'))
            checker = modules['experimental_native_compile'].ExperimentalChecker(args.repo)
            require(checker.repo.commit == args.helper_revision
                    and dict(checker.after) == proof['independent_source_record']['after_descriptors'],
                    'current source-derived factories differ from recorded candidate')
            # Make the exact descriptor ID table explicit without admitting
            # response-derived IDs or changing the owning checker.
            checker.IDS = modules['experimental_native_compile'].IDS
            report['factory_source_record'] = (bind_current_proof_factory_sources(checker, proof, args.candidate_revision)
                if qa_authority is not None else checker.source_record())
            initial = {'source': driver.source_snapshot(checker), 'candidate': carrier.identities(restored),
                       'carrier_json_sha256': sha(read(args.carrier_root / 'carrier.json')),
                       'carrier_archive_sha256': sha(read(args.carrier_root / 'candidate.tar.gz', 2 * 1024**3))}
            report['initial_identities'] = initial
            report['protected_ports_before'] = {str(port): driver.listeners(port) for port in (8765, 18765)}
            report['owned_port_absent_before'] = not driver.listeners(PORT)
            require(report['owned_port_absent_before'], 'current companion port is occupied')
            service = driver.OwnedService(record['binary'], record['assets'], record['tools'], PORT, out)
            report['launch'] = {'argv': service.argv, 'environment': service.environment}
            report['owned_start'] = service.start()
            report['socket_proof'] = save_owned_socket_proof(service, out)
            status, raw, owner = service.call('/healthz')
            write(out / 'health.response.raw', raw)
            health = parse(raw)
            require(status == 200 and health['status'] == 'ok' and health['model_loaded'] is False
                    and health['blocks_loaded'] is True and health['backend_sha256'] == checker.after['python_backend']
                    and health['asset_manifest_sha256'] == proof['asset_manifest_canonical_sha256']
                    and health['formatter_sha256'] == record['identity']['tools']['SOM_RUFF']['sha256'],
                    'current owned health differs from source and candidate identities')
            report['health'] = {'status': status, 'raw_sha256': sha(raw), 'owned_socket': owner}
            report['rows'] = capture_rows(jobs, service, oracle, checker, modules, out)
        except BaseException as error:
            if isinstance(error, CaptureStopped):
                report['rows'], error = error.records, error.cause
            report.update(setup_error=type(error).__name__, reason=str(error), traceback=traceback.format_exc())
        finally:
            cancellation['cleanup'] = True
            report['interrupted_signal'] = cancellation['signal']
            report['cleanup'] = service.close() if service is not None else {'cleanup_passed': True, 'owned_pid': None}
            if service is not None and service.process is not None:
                try:
                    report['cleanup']['wait_exit_code'] = service.process.wait(timeout=1)
                    report['cleanup']['wait_completed'] = True
                except BaseException as error:
                    report['cleanup'].update(wait_completed=False, wait_error=type(error).__name__, cleanup_passed=False)
            report['owned_port_absent_after'] = not driver.listeners(PORT)
            report['protected_ports_after'] = {str(port): driver.listeners(port) for port in (8765, 18765)}
            try:
                sources.verify()
                if initial is not None:
                    final = {'source': driver.source_snapshot(checker), 'candidate': carrier.identities(restored),
                             'carrier_json_sha256': sha(read(args.carrier_root / 'carrier.json')),
                             'carrier_archive_sha256': sha(read(args.carrier_root / 'candidate.tar.gz', 2 * 1024**3))}
                    report['final_identities'] = final
                    report['identities_unchanged'] = initial == final
                    require(guard.verify_candidate_inputs(args.repo, args.helper_revision, args.candidate_revision, args.assembly_root)
                            == report['candidate_admission'], 'candidate source/artifact admission changed')
                if jobs:
                    report['original_requests_after'] = request_vector(prepare_jobs(sources, args.assembly_root, modules, args.candidate_revision))
                    require(report['original_requests_before'] == report['original_requests_after'], 'original request vector changed')
                if qa_authority is not None:
                    require(selected_current_identity(args, sources) == qa_authority, 'native source authority changed during capture')
                report['source_bindings_after'] = dict(sources.pins)
            except BaseException as error:
                report.update(identities_unchanged=False, postcheck_error=str(error))
            if len(report['rows']) < len(jobs):
                done = {(row['cohort'], row['id'], row['form']) for row in report['rows']}
                for index, job in enumerate(jobs):
                    if (job['cohort'], job['id'], job['form']) not in done:
                        report['rows'].append({k: v for k, v in job.items() if k != 'raw'} |
                            {'index': index, 'state': 'not_run', 'reason': 'capture interrupted or setup failed'})
            report['source_bindings'] = dict(sources.pins)
            report['elapsed_seconds'] = time.monotonic() - report.pop('started_monotonic')
    records = report['rows']
    report['actual_forms'] = len(records)
    report['passed_forms'] = sum(row['state'] == 'passed' for row in records)
    report['positive_assemblies'] = sum(row.get('assemble_status') == 200 for row in records)
    report['refusal_forms'] = sum(row.get('refusal_verified') is True for row in records)
    try:
        report['server_events'] = verify_events(out, records, export)
        verify_process_report(report)
        report['server_event_scope_complete'] = True
    except Exception as error:
        report['server_event_scope_complete'], report['server_event_error'] = False, str(error)
    report['saved_files'] = output_inventory(out)
    report['capture_complete'] = (len(records) == report['passed_forms'] == 68 and report['positive_assemblies'] == 64
        and report['refusal_forms'] == 4 and report.get('identities_unchanged') is True
        and report.get('source_bindings') == report.get('source_bindings_after')
        and report.get('original_requests_before') == report.get('original_requests_after')
        and len(report.get('original_requests_before', {})) == 68
        and report['cleanup']['cleanup_passed'] is True and report['cleanup'].get('wait_completed') is True
        and report['owned_port_absent_after'] is True
        and report['server_event_scope_complete'] is True and report['interrupted_signal'] is None
        and not any(key in report for key in ('setup_error', 'postcheck_error')))
    save(out / 'manifest.json', report)
    print(compact({key: report[key] for key in ('capture_complete', 'actual_forms', 'passed_forms',
                 'positive_assemblies', 'refusal_forms', 'product_acceptance_passed')}).decode())
    return 0 if report['capture_complete'] else 1


def verify_capture(root, source_reader, helper_revision, candidate_revision, admission, assembly_root,
                   qa_mode='recorded-f59', source_paths=None):
    """Re-read exact current captures; return all28 named current input slots."""
    root = Path(root).absolute()
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub capture consumer only')
    report = parse(read(root / 'manifest.json'))
    qa_authority = None
    if qa_mode == 'native-qa-v1':
        qa_authority = native_source_authority(source_reader, source_paths, helper_revision, candidate_revision)
        require(report.get('qa_mode') == qa_mode and report.get('native_source_authority') == qa_authority,
                'current capture sealed source authority differs')
    else:
        require(qa_mode == 'recorded-f59' and candidate_revision == CANDIDATE, 'unknown or mixed current capture mode')
    require(report.get('version') == VERSION and report.get('role') == 'current-candidate-compiled-inputs'
            and report.get('helper_revision') == helper_revision
            and report.get('candidate_revision') == candidate_revision
            and report.get('candidate_admission') == admission
            and report.get('capture_complete') is True and report.get('actual_forms') == report.get('passed_forms') == 68
            and report.get('positive_assemblies') == 64 and report.get('refusal_forms') == 4
            and report.get('product_acceptance_passed') is False and report.get('whole_acceptance_complete') is False
            and report.get('before_role') == FIXED_BEFORE_AUTHORITY
            and report.get('historical_missing_wires_recovered') is False,
            'complete actual same-helper current candidate capture required')
    proof = parse(read(Path(assembly_root) / 'manifest.json'))
    expected_binary = proof['initial_identities']['binary']['sha256']
    require(candidate_revision != CANDIDATE or expected_binary == BINARY_SHA, 'historical candidate binary identity differs')
    require(report['initial_identities'] == report['final_identities']
            and report['initial_identities']['candidate']['binary']['sha256'] == expected_binary
            and report['identities_unchanged'] is True and report['cleanup']['cleanup_passed'] is True
            and report['cleanup'].get('wait_completed') is True
            and report['owned_port_absent_before'] is report['owned_port_absent_after'] is True
            and report['source_bindings'] == report['source_bindings_after']
            and report['server_event_scope_complete'] is True and report['interrupted_signal'] is None,
            'current source, binary or owned-process proof incomplete')
    required_sources = {SELF, *MODULES.values(), RS_PACKET, COVERAGE_PACKET, NEGATIVE, PHASE2, *DIAGNOSTIC_SOURCES, *EVENT_SOURCES}
    require(required_sources <= set(report['source_bindings']) and all(sha(source_reader(name)) == pin
            for name, pin in report['source_bindings'].items()), 'current capture helper/source authority differs')
    require(output_inventory(root) == report['saved_files'], 'current capture saved file universe differs')
    rs = packet(type('Reader', (), {'read': staticmethod(source_reader)})(), RS_PACKET, RS_PACKET_SHA)
    coverage = packet(type('Reader', (), {'read': staticmethod(source_reader)})(), COVERAGE_PACKET, COVERAGE_PACKET_SHA)
    required_sources |= {coverage['frozen_index']['path']}
    required_sources |= {row[key]['path'] for row in coverage['rows']
                         for key in ('prompt_source', 'cnl_source', 'historical_authority')}
    require(required_sources <= set(report['source_bindings']), 'complete current input source pin vector missing')
    if qa_authority is None:
        recipe_ref = admission['recipe']
        recipe_raw = source_reader(recipe_ref['path'])
        require(sha(recipe_raw) == recipe_ref['sha256'], 'admitted factory source recipe differs')
        admitted_recipe = parse(recipe_raw)
        require(len(admitted_recipe['factory_source_pins']) == 139
                and len(admitted_recipe['compile_assembly_source_pins']) == 168
                and all(sha(source_reader(name)) == pin for name, pin in admitted_recipe['factory_source_pins'].items()),
                'complete139 admitted owning factory sources differ')
    else:
        require(admission.get('mode') == 'same-candidate' and admission.get('helper_revision') == helper_revision
                and admission.get('candidate_revision') == candidate_revision and admission.get('recipe') is None
                and admission.get('artifact') is None and admission.get('product_acceptance_passed') is False,
                'new final capture may not reuse historical dependency admission')
        factory_sources = proof['independent_source_record']['source_sha256']
        require(factory_sources and report['factory_source_record']['source_sha256'] == factory_sources
                and factory_sources == proof['initial_identities']['source']
                and all(sha(source_reader(name)) == pin for name, pin in factory_sources.items()),
                'whole same-candidate factory source authority differs')
    if qa_authority is not None:
        controls = _native_controls(revision=helper_revision, candidate=candidate_revision)
        prefix = _native_companion_prefix()
        export_role, digest_role = prefix + '.verify.export-goldens', prefix + '.verify.raw-digest-compat'
        require(controls.role_sources[export_role] == MODULES['export_goldens']
                and controls.role_sources[digest_role] == MODULES['raw_digest_compat'],
                'fixed current verifier helper sources differ')
        source_reader(MODULES['export_goldens']); source_reader(MODULES['raw_digest_compat'])
        export = controls.load_module(export_role)
        modules = {'raw_digest_compat': controls.load_module(digest_role)}
    else:
        export = plain_module(source_reader, 'export_goldens')
        modules = {'raw_digest_compat': plain_module(source_reader, 'raw_digest_compat')}
    oracle = RefusalOracle(type('Reader', (), {'read': staticmethod(source_reader)})(), modules)
    proof = parse(read(Path(assembly_root) / 'manifest.json'))
    require(proof['tested_sha'] == candidate_revision and proof['experiment_complete'] is True
            and report['factory_source_record']['after_descriptors'] == proof['independent_source_record']['after_descriptors'],
            'current companion metadata factory proof differs')
    checker = type('Factory', (), {'IDS': descriptor_ids(source_reader),
                                   'after': proof['independent_source_record']['after_descriptors']})()
    expected_jobs = prepare_jobs(type('Reader', (), {'read': staticmethod(source_reader)})(), Path(assembly_root),
        {'export_goldens': export, 'run_experimental_native_compile': type('Driver', (), {'compact': staticmethod(compact)})()}, candidate_revision)
    require(report['original_requests_before'] == report['original_requests_after'] == request_vector(expected_jobs),
            'original current request vector differs')
    verify_event_sources(source_reader)
    owner = verify_process_report(report)
    verify_owned_socket_proof(root, report['socket_proof'], owner, report['launch']['argv'][0])
    require(report['server_events'] == verify_events(root, report['rows'], export), 'saved server event proof differs')
    factory = report['factory_source_record']
    require(factory['commit'] == helper_revision and factory['host'] == 'x86_64-unknown-linux-gnu'
            and factory['source_sha256'] == report['initial_identities']['source']
            and factory['source_sha256']
            and all(sha(source_reader(name)) == pin for name, pin in factory['source_sha256'].items()),
            'whole independently derived factory source vector differs')
    candidate = portable_identity({key: proof['initial_identities'][key] for key in ('binary', 'assets', 'tools')})
    require(report['initial_identities']['candidate'] == report['carrier']['identity'] == candidate,
            'complete portable candidate identity differs from original proof')
    carrier_raw = read(root / 'carrier.input.json')
    carrier_record = parse(carrier_raw)
    require(sha(carrier_raw) == report['initial_identities']['carrier_json_sha256'] == report['carrier']['carrier_sha256']
            and carrier_record.get('version') == 'som.tested-candidate-carrier.v1'
            and carrier_record.get('candidate_revision') == candidate_revision
            and carrier_record.get('compile_manifest_sha256') == sha(read(Path(assembly_root) / 'manifest.json'))
            and carrier_record.get('tested_identities') == candidate
            and carrier_record.get('archive_sha256') == report['initial_identities']['carrier_archive_sha256']
            == report['carrier']['archive_sha256'] and carrier_record.get('product_acceptance_passed') is False
            and type(carrier_record.get('compiler_rebuilt')) is bool
            and carrier_record.get('origin_role') == ('new build compared byte-for-byte with the admitted old candidate'
                if carrier_record['compiler_rebuilt'] else 'original already tested candidate bytes'),
            'exact raw carrier provenance differs')
    if qa_authority is not None:
        require(carrier_record['compiler_rebuilt'] is False
                and carrier_record.get('github', {}).get('GITHUB_RUN_ID') == os.environ['GITHUB_RUN_ID']
                and carrier_record.get('github', {}).get('GITHUB_RUN_ATTEMPT') == os.environ['GITHUB_RUN_ATTEMPT'],
                'current source capture cannot promote a rebuilt or earlier-run carrier')
    health_raw = read(root / 'health.response.raw')
    health = parse(health_raw)
    require(report['health']['status'] == 200 and sha(health_raw) == report['health']['raw_sha256']
            and health['status'] == 'ok' and health['model_loaded'] is False and health['blocks_loaded'] is True
            and health['backend_sha256'] == checker.after['python_backend']
            and health['asset_manifest_sha256'] == proof['asset_manifest_canonical_sha256']
            and health['formatter_sha256'] == candidate['tools']['SOM_RUFF']['sha256'],
            'current raw health differs from owning source and candidate identities')
    expected = [('rs22', row['id'], form) for row in rs['rows'] for form in ('native', 'typed')]
    expected += [('coverage6', row['id'], form) for row in coverage['rows']
                 for form in ('json-none', 'json-python', 'cnl-none', 'cnl-python')]
    require([(row['cohort'], row['id'], row['form']) for row in report['rows']] == expected,
            'whole ordered68 current forms required')
    grouped = {}
    for index, (row, job) in enumerate(zip(report['rows'], expected_jobs)):
        require(all(row.get(key) == value for key, value in job.items() if key != 'raw'),
                'named current row source role differs')
        verify_saved_row(root, index, row, job, checker, export, oracle, owner)
        key = row['cohort'], row['id']
        grouped.setdefault(key, []).append(row)
    result = []
    for (cohort, identity), forms in grouped.items():
        prompts = {row['prompt_sha256'] for row in forms}
        require(len(prompts) == 1 and all(row['compile_status'] == row['expected_status'] for row in forms),
                'current companion prompt/status disagreement')
        selected = next(row for row in forms if row['form'] == ('typed' if cohort == 'rs22' else 'json-none'))
        value = {'cohort': cohort, 'id': identity, 'prompt_sha256': selected['prompt_sha256'],
                 'current_capture_manifest_sha256': sha(read(root / 'manifest.json')), 'forms': forms,
                 'candidate_revision': candidate_revision, 'helper_revision': helper_revision}
        if selected['compile_status'] == 400:
            require(identity in REFUSALS and all(row.get('refusal_verified') is True and 'product_dir' not in row for row in forms),
                    'current refusal may not provide a Python product')
            value.update(state='current-refusal', files=None, files_map_sha256=None)
        else:
            maps = []
            for row in forms:
                require(row['assemble_status'] == 200
                        and sha(read(root / relative(row['assemble_request_path']))) == row['assemble_request_sha256']
                        and sha(read(root / relative(row['assemble_response_path']))) == row['assemble_response_sha256'],
                        'current own-bundle assembly bytes differ')
                files = {name: read(root / relative(row['product_dir']) / relative(name)).decode('utf-8', 'strict')
                         for name in row['file_sha256']}
                require({name: sha(text.encode()) for name, text in files.items()} == row['file_sha256'],
                        'current complete Python product bytes differ')
                maps.append(files)
            require(all(files == maps[0] for files in maps)
                    and len({row['compile_response_sha256'] for row in forms}) == 1,
                    'named current forms disagree on complete bundle or file bytes')
            prompt = parse(read(root / relative(selected['compile_response_path'])))['bundle']['prompt']
            value.update(state='current-product', prompt=prompt, files=maps[0], files_map_sha256=selected['files_map_sha256'])
        result.append(value)
    require(len(result) == 28 and sum(row['state'] == 'current-product' for row in result) == 26,
            'whole28 named current input slots required')
    return result


def main():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux execution only')
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'assembly-root', 'carrier-root', 'restored-candidate', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--helper-revision', required=True)
    parser.add_argument('--candidate-revision', required=True)
    parser.add_argument('--qa-mode', choices=('recorded-f59', 'native-qa-v1'), default='recorded-f59')
    args = parser.parse_args()
    sources = Sources(args.repo, args.helper_revision)
    args._native_source_authority = selected_current_identity(args, sources)
    if args._native_source_authority is not None:
        with _borrowed_native(sources) as controls:
            require(_native_companion_prefix() == 'companions.entry', 'current capture needs its fixed entry namespace')
            raw = {name: sources.read(path) for name, path in MODULES.items()}
            roles = {name: 'companions.entry.capture.' + name.replace('_', '-') for name in MODULES}
            require(all(controls.role_sources[roles[name]] == path for name, path in MODULES.items()),
                    'fixed current capture helper source differs')
            modules = {}
            for name, path in MODULES.items():
                modules[name] = controls.load_module(roles[name])
                require(modules[name].__file__ == str(sources.root / path)
                        and modules[name].__som_raw_sha256__ == sha(raw[name]), 'current capture helper bytes differ')
            require(set(modules) == set(MODULES), 'loaded helper closure differs')
            return run(args, sources, modules)
    with BoundModules(sources).installed() as modules:
        return run(args, sources, modules)


if __name__ == '__main__':
    raise SystemExit(main())
