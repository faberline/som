"""Analyze the exact retained Python response fixtures as data only.

This is not an operational Mac comparator or a native authority interface.
The original strict failures stay separate from prospective data facts.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

import python_backend_source_data as backend_source
import raw_digest_compat as raw_compat

FIXTURE_MANIFEST_SHA = 'a72505622f3bb6335a0d4c60e1a0c4e1185c01ddceee0738d83cbc2db625e8f8'
VECTORS = 'som-rs/contracts/compiler-v1/canonical-vectors.json'
VECTORS_SHA = 'dd09b41f49702f70098574f638e10ea33646366a77dfa486539f324d6f9ff83f'
HISTORICAL_VECTOR_FLOW_SHA = '8df232e307baa785c696869f89f4f44b3a6eec2a621dfea0e5c054165fa483f6'
CANONICAL_REGION_SHA = 'ae240afdfe50a0da4bae400f2d83747c0db19a95f5e8bd7417e4484bccbf39de'
CANONICAL_SOURCES = {
    'src/flow.rs': ('som-rs/src/flow.rs', 'fc8b7ea7d591026aed9380a07641ca206391555e95df15b317b879e0e9a31b75'),
    'Cargo.toml': ('som-rs/Cargo.toml', '6c673ba87bf98308042b8229727850956d90c7d24c03c8ea17b042c874d546b1'),
    'Cargo.lock': ('som-rs/Cargo.lock', 'c7468fc0e5c51fc9031369b0dc292e47214ca5b7233dfb85216748107cc2493c'),
}
RETAINED_CANDIDATE = '9763e9b86f36a99229b70d8c571734e9a740e81c'
HEX = re.compile(r'[0-9a-f]{64}\Z')
FORMS = ('omitted', 'python')
SIDES = ('before', 'candidate')
PHASES = ('compile', 'assemble')
PARTS = ('request.raw', 'response.raw', 'transport.json')
PAIR_KEYS = frozenset(phase + '_' + part.replace('.raw', '').replace('.json', '')
                      for phase in PHASES for part in PARTS)
VECTOR_IDS = ('integer', 'float', 'negative-zero', 'null', 'bool', 'tiny',
              'negative-tiny', 'unicode', 'escaped', 'map-order', 'typed-order')


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (list, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _finite_float(text):
    value = float(text)
    require(math.isfinite(value), 'nonfinite JSON number')
    return value


def _parse(raw):
    require(type(raw) is bytes, 'immutable raw JSON bytes required')
    raw_compat.mask_raw(raw, [], 'before')
    return json.loads(raw.decode('utf-8', 'strict'), parse_float=_finite_float)


def _read_regular(root, relative):
    require(type(root) is type(Path()) and root.is_absolute(), 'absolute physical input root required')
    require(type(relative) is str and relative and not relative.startswith('/')
            and '\\' not in relative and all(p not in ('', '.', '..') for p in relative.split('/')),
            'relative fixture member path required')
    path = root / relative
    for item in (path, *path.parents):
        require(not item.is_symlink(), 'fixture path has a symbolic link')
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size <= raw_compat.MAX_BYTES,
            'bounded regular fixture file required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(raw_compat.MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    end = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_mtime_ns, s.st_ctime_ns)
    require(identity(before) == identity(opened) == identity(after) == identity(end)
            and len(data) == before.st_size, 'fixture changed during its read')
    return data


def _authenticate_fixture(root):
    manifest_raw = _read_regular(root, 'proposal-manifest.json')
    require(sha(manifest_raw) == FIXTURE_MANIFEST_SHA, 'frozen fixture manifest fingerprint differs')
    manifest = _parse(manifest_raw)
    members = manifest['members']
    expected = {'DATA_PROOF.json'} | {f'{form}/{side}/{phase}/{part}'
        for form in FORMS for side in SIDES for phase in PHASES for part in PARTS}
    require(type(members) is dict and set(members) == expected, 'complete25 fixture member set differs')
    observed = set()
    for parent, dirs, files in os.walk(root, followlinks=False):
        parent = Path(parent)
        for name in dirs:
            path = parent / name
            require(not path.is_symlink() and stat.S_ISDIR(path.lstat().st_mode),
                    'fixture directory is not physical')
        for name in files:
            observed.add((parent / name).relative_to(root).as_posix())
    require(observed == expected | {'proposal-manifest.json'}, 'missing or extra fixture member')
    data = {}
    for name in sorted(expected):
        raw = _read_regular(root, name)
        require(type(members[name]) is dict and len(raw) == members[name]['bytes']
                and sha(raw) == members[name]['sha256'], 'frozen fixture member fingerprint differs')
        data[name] = raw
    return _freeze(data)


def _derive_sources(repository):
    before = backend_source.read_before_backend(repository)
    current = backend_source.read_current_backend(repository)
    require(before['revision'] == backend_source.BEFORE and len(before['entries']) == 27
            and len(current['entries']) == 29, 'complete independent backend source vectors required')
    return before, current


def _qualify_canonical(repository, sources):
    raw = _read_regular(repository, VECTORS)
    require(sha(raw) == VECTORS_SHA, 'frozen canonical vector bytes differ')
    vectors = _parse(raw)
    require(vectors['version'] == 'som.canonical-json.vectors.v1'
            and tuple(row['id'] for row in vectors['vectors']) == VECTOR_IDS,
            'complete canonical vector inventory differs')
    require(vectors['source_sha256'] == HISTORICAL_VECTOR_FLOW_SHA,
            'historical canonical vector source provenance differs')
    # The historical vector's whole file differs from both retained owning files.
    # Their complete canonical region and resolved dependencies are reviewed data.
    for source in sources:
        entries = {row['logical']: row for row in source['entries']}
        for logical, (relative, expected) in CANONICAL_SOURCES.items():
            require(logical in entries and entries[logical]['path'] == relative
                    and entries[logical]['sha256'] == expected,
                    'canonical algorithm source provenance differs')
    physical = {}
    for logical, (relative, expected) in CANONICAL_SOURCES.items():
        physical[logical] = _read_regular(repository, relative)
        require(sha(physical[logical]) == expected,
                'canonical physical source provenance differs')
    flow = physical['src/flow.rs']
    start_marker = b'pub fn canonical(value: &Value) -> String {'
    end_marker = b'\nfn capability('
    require(flow.count(start_marker) == flow.count(end_marker) == 1,
            'complete reviewed canonical source region is not unique')
    start = flow.index(start_marker)
    end = flow.index(end_marker, start)
    require(sha(flow[start:end]) == CANONICAL_REGION_SHA,
            'complete reviewed canonical source region differs')
    records = []
    for row in vectors['vectors']:
        actual = canonical(_parse(row['input_json'].encode('utf-8')))
        require(actual == row['canonical'] and sha(actual.encode()) == row['sha256'],
                'new canonical implementation failed a complete frozen vector')
        records.append({'id': row['id'], 'sha256': row['sha256']})
    return records


def _pair_from_members(data, form):
    require(type(form) is str and form in FORMS, 'known fixture form required')
    pair = {'form': form}
    for side in SIDES:
        pair[side] = {phase + '_' + part.replace('.raw', '').replace('.json', ''):
            data[f'{form}/{side}/{phase}/{part}'] for phase in PHASES for part in PARTS}
    return pair


def _transport(raw, response, phase):
    value = _parse(raw)
    require(type(value) is dict and set(value) == {'bytes', 'headers', 'method', 'owned_pid',
            'port', 'read_complete', 'route', 'sha256', 'status'}, 'transport member set differs')
    require(value['method'] == 'POST' and value['route'] == '/v1/' + phase, 'transport method or route differs')
    require(type(value['status']) is int and value['status'] == 200, 'transport status differs')
    require(value['read_complete'] is True, 'transport read is incomplete')
    require(type(value['bytes']) is int and value['bytes'] == len(response)
            and type(value['sha256']) is str and value['sha256'] == sha(response),
            'transport response length or fingerprint differs')
    require(type(value['owned_pid']) is int and value['owned_pid'] > 0
            and type(value['port']) is int and 1 <= value['port'] <= 65535,
            'retained transport PID or port differs')
    headers = value['headers']
    require(type(headers) is list and len(headers) <= 100 and all(type(row) is list
            and len(row) == 2 and all(type(part) is str for part in row) for row in headers),
            'transport headers differ')
    lengths = [v for k, v in headers if k.lower() == 'content-length']
    require(len(lengths) == 1 and lengths[0].isdigit() and int(lengths[0]) == len(response),
            'transport content length differs')
    return value


def _request(raw, form):
    value = _parse(raw)
    require(type(value) is dict and set(value) == ({'format', 'prompt'} if form == 'omitted'
            else {'format', 'prompt', 'target'}), 'compile request form differs')
    require(value['format'] == 'som.prompt-json.v1' and type(value['prompt']) is dict
            and (form == 'omitted' or value['target'] == 'python'), 'Python request format or target differs')
    version = value['prompt'].get('version')
    require(version not in {'som.prompt.v6', 'som.prompt.v7'},
            'formatted v6/v7 requires separately reviewed real native formatter authority')
    require(version in {'som.prompt.v1', 'som.prompt.v2', 'som.prompt.v3', 'som.prompt.v4', 'som.prompt.v5'},
            'known render-only Python prompt version required')
    return value


def _compile_bundle(raw, prompt, source):
    value = _parse(raw)
    require(type(value) is dict and set(value) == {'bundle', 'status'} and value['status'] == 'ok',
            'complete successful compile envelope required')
    bundle = value['bundle']
    require(type(bundle) is dict and set(bundle) == {'assets', 'dsl', 'hashes', 'prompt', 'version'}
            and bundle['version'] == 'som.dsl-bundle.v1', 'complete original bundle member set differs')
    require(canonical(bundle['prompt']) == canonical(prompt), 'returned prompt differs from its own request')
    hashes = bundle['hashes']
    require(type(hashes) is dict and set(hashes) == {'prompt_sha256', 'dsl_sha256', 'payload_sha256'}
            and all(type(v) is str and HEX.fullmatch(v) for v in hashes.values()), 'complete bundle hash set differs')
    require(hashes['prompt_sha256'] == value_sha(bundle['prompt']), 'complete prompt hash differs')
    require(hashes['dsl_sha256'] == value_sha(bundle['dsl']), 'complete DSL hash differs')
    payload = {key: item for key, item in bundle.items() if key != 'hashes'}
    require(hashes['payload_sha256'] == value_sha(payload), 'complete payload hash differs')
    assets = bundle['assets']
    require(type(assets) is dict and set(assets) == {'backend', 'block_library', 'catalog',
            'compiler_contract', 'formatter', 'grammar', 'literal_policy', 'selection_policy',
            'semantic_contracts', 'serializer'}, 'complete Python asset set differs')
    backend = assets['backend']
    require(type(backend) is dict and set(backend) == {'id', 'sha256'}
            and backend['id'] == 'som.compiler-backend.v1' and backend['sha256'] == source['backend_sha256'],
            'backend descriptor differs from its own complete source vector')
    formatter = assets['formatter']
    require(type(formatter) is dict and set(formatter) == {'id', 'sha256'}
            and formatter['id'] == 'som.compiler-render-only.v1'
            and type(formatter['sha256']) is str and HEX.fullmatch(formatter['sha256']),
            'render-only formatter descriptor differs')
    return bundle


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _own_assemble_request(raw, bundle):
    value = _parse(raw)
    require(type(value) is dict and set(value) == {'bundle'}
            and canonical(value['bundle']) == canonical(bundle), 'assembly request is not its complete own returned bundle')
    require(raw == compact({'bundle': bundle}), 'own assembly request original compact spelling differs')


def _compare_pair_data(pair, sources):
    """Private data primitive. It is not an authentication or native acceptance API."""
    require(type(pair) is dict and set(pair) == {'form', 'before', 'candidate'}
            and type(pair['form']) is str and pair['form'] in FORMS, 'named data pair shape differs')
    require(type(sources) is tuple and len(sources) == 2
            and all(type(value) is MappingProxyType for value in sources), 'derived source data pair required')
    for side in SIDES:
        require(type(pair[side]) is dict and set(pair[side]) == PAIR_KEYS
                and all(type(value) is bytes for value in pair[side].values()), 'complete raw data side differs')
    old, new = pair['before'], pair['candidate']
    require(old['compile_request'] == new['compile_request'], 'same-form compile request bytes differ')
    request = _request(old['compile_request'], pair['form'])
    transports, bundles = [], []
    for side, source in zip((old, new), sources):
        compile_transport = _transport(side['compile_transport'], side['compile_response'], 'compile')
        assemble_transport = _transport(side['assemble_transport'], side['assemble_response'], 'assemble')
        require((compile_transport['port'], compile_transport['owned_pid'])
                == (assemble_transport['port'], assemble_transport['owned_pid']),
                'same-role retained transport observations differ')
        transports.append(compile_transport)
        bundle = _compile_bundle(side['compile_response'], request['prompt'], source)
        _own_assemble_request(side['assemble_request'], bundle)
        bundles.append(bundle)
    require(transports[0]['port'] != transports[1]['port'], 'before and candidate endpoints are the same')
    require(bundles[0]['assets']['formatter'] == bundles[1]['assets']['formatter'],
            'v1-v5 formatter bytes must remain exact')
    pins = [('/bundle/assets/backend/sha256', sources[0]['backend_sha256'], sources[1]['backend_sha256']),
            ('/bundle/hashes/payload_sha256', bundles[0]['hashes']['payload_sha256'], bundles[1]['hashes']['payload_sha256'])]
    raw_compat.verify_raw(old['compile_response'], new['compile_response'], pins)
    raw_compat.verify_raw(old['assemble_request'], new['assemble_request'], pins)
    require(old['assemble_response'] == new['assemble_response'], 'complete assembly response bytes differ')
    assembled = _parse(old['assemble_response'])
    require(type(assembled) is dict and set(assembled) == {'files', 'status'} and assembled['status'] == 'ok'
            and type(assembled['files']) is dict and assembled['files']
            and all(type(k) is str and type(v) is str for k, v in assembled['files'].items()),
            'complete successful assembly file map required')
    return _freeze({'form': pair['form'], 'compile_request_sha256': sha(old['compile_request']),
        'before_compile_response_sha256': sha(old['compile_response']),
        'candidate_compile_response_sha256': sha(new['compile_response']),
        'before_assemble_request_sha256': sha(old['assemble_request']),
        'candidate_assemble_request_sha256': sha(new['assemble_request']),
        'assemble_request_hashes_differ': sha(old['assemble_request']) != sha(new['assemble_request']),
        'complete_assemble_response_sha256': sha(old['assemble_response']),
        'generated_file_sha256': {p: sha(value.encode('utf-8')) for p, value in assembled['files'].items()},
        'independently_derived_digest_pins': pins,
        'strict_compile_byte_equal': old['compile_response'] == new['compile_response'],
        'strict_assemble_byte_equal': True, 'prospective_data_comparison_only': True,
        'retained_before_pid': transports[0]['owned_pid'], 'retained_candidate_pid': transports[1]['owned_pid'],
        'live_process_ownership_verified': False, 'native_authority': False,
        'SourceSeal': False, 'product_acceptance_passed': False})


def analyze_retained_fixture_pair(repository, fixture_root):
    """Return immutable facts for the exact two-form historical fixture set only."""
    data = _authenticate_fixture(fixture_root)
    sources = _derive_sources(repository)
    canonical_records = _qualify_canonical(repository, sources)
    pairs = [_compare_pair_data(_pair_from_members(data, form), sources) for form in FORMS]
    require(all(row['strict_compile_byte_equal'] is False and row['strict_assemble_byte_equal'] is True
                for row in pairs), 'original strict diagnostic result was relabelled')
    return _freeze({'version': 'som.python-raw-response-data-facts.v1', 'data_only': True,
        'before_source_revision': sources[0]['revision'], 'selected_current_source_revision': sources[1]['revision'],
        'retained_candidate_origin_revision': RETAINED_CANDIDATE,
        'retained_data_is_fresh_current_candidate_proof': False,
        'canonical_vectors_revalidated': canonical_records,
        'canonical_source_provenance': {
            'historical_vector_origin_flow_sha256': HISTORICAL_VECTOR_FLOW_SHA,
            'actual_source_file_sha256_by_side': {side: {row['logical']: row['sha256']
                for row in source['entries'] if row['logical'] in CANONICAL_SOURCES}
                for side, source in zip(SIDES, sources)},
            'complete_canonical_region_sha256': CANONICAL_REGION_SHA,
            'actual_Rust_build_features_or_runtime_caller_verified': False,
            'historical_Rust_code_executed': False},
        'pairs': pairs,
        'original_strict_compile_failures': 2, 'original_strict_assemble_passes': 2,
        'formatter_exception_applied': False, 'native_authority': False, 'SourceSeal': False,
        'full671_or12_stage_acceptance': False, 'product_acceptance_passed': False})


# Complete frozen primitive definitions; source and AST provenance is in the packet.
class Refusal(ValueError):
    pass

def require(condition, reason):
    if not condition:
        raise Refusal(reason)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    """Frozen Python primitive rule used by the accepted baseline's qa.sha."""
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        require(math.isfinite(value), "nonfinite JSON number")
        text = repr(value)
        if "e" in text:
            first, exponent = text.split("e", 1)
            sign = "-" if exponent.startswith("-") else "+" if exponent.startswith("+") else ""
            digits = exponent.lstrip("+-")
            text = first + "e" + sign + (digits.zfill(2) if sign == "-" else digits)
        return text
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) is list:
        return "[" + ",".join(canonical(item) for item in value) + "]"
    require(type(value) is dict and all(type(key) is str for key in value), "unsupported JSON value")
    return "{" + ",".join(canonical(key) + ":" + canonical(value[key]) for key in sorted(value)) + "}"

def value_sha(value):
    return sha(canonical(value).encode("utf-8", "strict"))
