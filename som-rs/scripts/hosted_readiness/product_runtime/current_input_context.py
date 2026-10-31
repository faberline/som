"""Use current own-candidate Python inputs without replacing historical records."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

CAPTURE = 'som-rs/scripts/hosted_readiness/run_current_python_companions.py'
CHECKER = 'som-rs/scripts/hosted_readiness/experimental_native_compile.py'
TYPED_DRIVER = 'som-rs/scripts/hosted_readiness/run_experimental_native_compile.py'
TYPED_DRIVER_SHA256 = 'bb3653edc9021b1476d659276fbf9a549ba33de594fad9dac3713ac368b37d87'
INVENTORY = 'som-rs/contracts/rust-output-v1/phase2/case-inventory.json'
REFUSALS = {'RS12_CNL_V3_FILTER_LIMIT', 'RS13_JSON_V3_FILTER_OFFSET'}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'linked current input refused')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 128 * 1024**2,
                'bounded single-link current input required')
        raw = stream.read(128 * 1024**2 + 1)
    require(len(raw) == info.st_size, 'current input changed length')
    return raw


def rows(ctx, refresh=False):
    require(ctx.data.get('qa_mode') == 'native-qa-v1' and ctx.helper_revision == ctx.candidate_revision,
            'current input context requires sealed same-candidate native mode')
    if not refresh and 'current_companion_rows' in ctx.cache:
        return ctx.cache['current_companion_rows']
    raw = ctx.sources.git('ls-tree', '-r', '-z', '--name-only', ctx.helper_revision)
    require(raw.endswith(b'\0'), 'complete source path inventory required')
    paths = set(raw[:-1].decode('utf-8', 'strict').split('\0'))
    capture = ctx.module('current_capture', CAPTURE)
    root = Path(ctx.data['current_companions_root'])
    result = capture.verify_capture(root, ctx.sources.read, ctx.helper_revision, ctx.candidate_revision,
        ctx.candidate_admission, Path(ctx.data['assembly_root']), qa_mode='native-qa-v1', source_paths=paths)
    require(len(result) == 28 and len({(r['cohort'], r['id']) for r in result}) == 28
            and sum(r['state'] == 'current-product' for r in result) == 26
            and {(r['cohort'], r['id']) for r in result if r['state'] == 'current-refusal'}
                == {('rs22', identity) for identity in REFUSALS}, 'complete current28 input scope differs')
    require(all(r['helper_revision'] == ctx.helper_revision and r['candidate_revision'] == ctx.candidate_revision
                for r in result), 'current input candidate differs')
    ctx.cache['current_companion_rows'] = result
    return result


def lookup_files(ctx, prompt, identity=None):
    capture = ctx.module('current_capture', CAPTURE)
    prompt_sha = capture.sha(capture.plain_module(ctx.sources.read, 'export_goldens').canonical(prompt).encode())
    matches = [row for row in rows(ctx) if row['prompt_sha256'] == prompt_sha
               and (identity is None or row['id'] == identity)]
    if not matches:
        return None
    require(all(row['state'] == 'current-product' and type(row.get('files')) is dict for row in matches),
            'current compiler refusal cannot supply product files')
    require(all(row['files'] == matches[0]['files'] for row in matches), 'ambiguous current own-product bytes')
    return {'files': dict(matches[0]['files']), 'source_role': 'same-candidate-current-own-bundle-assembly',
            'rows': [{'cohort': row['cohort'], 'id': row['id'],
                      'manifest_sha256': row['current_capture_manifest_sha256']} for row in matches]}


def phase2_input(ctx, expected):
    if expected['family'] != 'rs':
        return None
    matches = [row for row in rows(ctx) if row['cohort'] == 'rs22' and row['id'] == expected['id']]
    require(len(matches) == 1 and matches[0]['prompt_sha256'] == expected['prompt_sha256'],
            'named native current prompt input missing')
    row = matches[0]
    if row['state'] == 'current-refusal':
        require(row['id'] in REFUSALS and expected['phase2_expected']['result'] == 'refusal'
                and row['files'] is None and row['files_map_sha256'] is None, 'negative input has product bytes')
        return {'prompt': ctx.native_prompt(expected), 'files': None, 'product_sha256': None,
                'source_role': 'same-candidate-current-compiler-refusal-no-product',
                'historical_product_sha256': expected['product_sha256']}
    capture = ctx.module('current_capture', CAPTURE)
    wanted = capture.REPAIRED.get(row['id'], expected['product_sha256'])
    require(row['files_map_sha256'] == wanted, 'current native product differs from its exact admitted source role')
    return {'prompt': row['prompt'], 'files': dict(row['files']), 'product_sha256': row['files_map_sha256'],
            'source_role': ('same-candidate-repaired-current-map' if row['id'] in capture.REPAIRED
                            else 'same-candidate-current-own-bundle-assembly'),
            'historical_product_sha256': expected['product_sha256']}


def typed_process_proof(ctx, proof, record):
    """Check retained identities against raw source; do not claim a new OS wait."""
    require(ctx.helper_revision == ctx.candidate_revision == proof.get('tested_sha'),
            'typed proof must use the current helper and candidate')
    initial, final = proof.get('initial_identities'), proof.get('final_identities')
    require(type(initial) is dict and set(initial) == {'source', 'binary', 'assets', 'tools'}
            and initial == final and proof.get('identity_stability_passed') is True,
            'typed proof source, binary, assets or tools changed')
    independent = proof.get('independent_source_record')
    require(type(independent) is dict and independent.get('commit') == ctx.candidate_revision
            and type(initial['source']) is dict and initial['source']
            and independent.get('source_sha256') == initial['source'],
            'typed proof complete source vectors differ')
    require(initial['source'].get(TYPED_DRIVER) == TYPED_DRIVER_SHA256,
            'typed proof wait/poll/reap driver source pin differs')
    for path, expected in initial['source'].items():
        require(type(path) is str and path and not Path(path).is_absolute() and '..' not in Path(path).parts
                and type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected) is not None,
                'typed proof source path or hash differs')
        require(sha(ctx.sources.read(path)) == expected, 'typed proof raw current Git source changed: ' + path)
    require(CHECKER in initial['source'], 'typed proof checker source pin missing')
    identity_fields = {'sha256', 'size', 'mode', 'device', 'inode'}
    def file_identity(value):
        require(type(value) is dict and set(value) == identity_fields
                and type(value['sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None
                and all(type(value[key]) is int and value[key] >= 0 for key in ('size', 'mode', 'device', 'inode'))
                and value['inode'] > 0, 'typed proof file identity incomplete')
    file_identity(initial['binary'])
    require(type(initial['assets']) is dict and initial['assets']
            and type(initial['tools']) is dict
            and set(initial['tools']) == {'SOM_RUFF', 'SOM_RUSTFMT', 'SOM_TS_NODE', 'SOM_TS_PRETTIER'},
            'typed proof asset or tool vector incomplete')
    for value in initial['assets'].values():
        file_identity(value)
    for name, value in initial['tools'].items():
        if name == 'SOM_TS_PRETTIER':
            require(type(value) is dict and value, 'typed proof prettier tree missing')
            for member in value.values():
                file_identity(member)
        else:
            file_identity(value)
    def owner(value):
        require(type(value) is dict
                and set(value) == {'pid', 'state', 'pgrp', 'session', 'start_ticks', 'listener', 'executable_inode'}
                and all(type(value[key]) is int and value[key] > 0
                        for key in ('pid', 'pgrp', 'session', 'start_ticks', 'executable_inode'))
                and value['pid'] == value['pgrp'] == value['session']
                and type(value['state']) is str and len(value['state']) == 1 and value['state'] != 'Z'
                and value['executable_inode'] == initial['binary']['inode'],
                'typed proof owned process identity incomplete')
        listener = value['listener']
        require(type(listener) is dict and set(listener) == {'table', 'address', 'inode'}
                and listener['table'] == 'tcp' and listener['address'] == '0100007F'
                and type(listener['inode']) is str and listener['inode'].isdecimal()
                and int(listener['inode']) > 0, 'typed proof exact owned loopback TCP socket missing')
        return {key: value[key] for key in value if key != 'state'}
    start = owner(proof.get('owned_start'))
    require(owner(record.get('owned_socket')) == start, 'typed row socket differs from owned start')
    cleanup = proof.get('cleanup')
    require(type(cleanup) is dict and type(cleanup.get('owned_pid')) is int
            and cleanup['owned_pid'] == start['pid'] and cleanup.get('cleanup_passed') is True
            and type(cleanup.get('exit_code')) is int
            and cleanup.get('signal_target') == 'owned new process group only'
            and type(cleanup.get('signals')) is list
            and cleanup['signals'] in ([], ['SIGTERM'], ['SIGTERM', 'SIGKILL'])
            and cleanup.get('remaining_owned_session_members') == [],
            'typed proof owned terminal cleanup incomplete')
    return {'verification_role': 'source-based-retained-typed-process-proof',
            'driver_source_path': TYPED_DRIVER, 'driver_source_sha256': TYPED_DRIVER_SHA256,
            'source_vector_sha256': sha(json.dumps(initial['source'], sort_keys=True, separators=(',', ':')).encode()),
            'source_file_count': len(initial['source']), 'binary_sha256': initial['binary']['sha256'],
            'owned_start': proof['owned_start'], 'row_owned_socket': record['owned_socket'],
            'cleanup': cleanup, 'wait_authority': 'checked OwnedService.close wait/poll/reap source and retained terminal cleanup'}


def refusal(ctx, identity, language):
    require(language in {'python', 'rust'}, 'named refusal language required')
    if identity not in REFUSALS:
        return None
    matches = [row for row in rows(ctx) if row['cohort'] == 'rs22' and row['id'] == identity]
    require(len(matches) == 1 and matches[0]['state'] == 'current-refusal', 'current named refusal unavailable')
    row = matches[0]
    forms = row['forms']
    require(len(forms) == 2 and {item['form'] for item in forms} == {'native', 'typed'}
            and all(item['compile_status'] == 400 and item['refusal_verified'] is True
                    and 'product_dir' not in item for item in forms), 'exact native and typed Python refusals required')
    result = {'id': identity, 'language': language, 'outcome_role': 'current-compiler-refusal-no-product',
              'compiler_status': 400, 'code': 'INVALID_VALUE', 'product_executed': False,
              'current_capture_manifest_sha256': row['current_capture_manifest_sha256'],
              'python_forms': forms, 'helper_revision': ctx.helper_revision,
              'candidate_revision': ctx.candidate_revision, 'product_acceptance_passed': False}
    if language == 'rust':
        root = Path(ctx.data['assembly_root'])
        proof = json.loads(read(root / 'manifest.json'))
        require(proof['tested_sha'] == ctx.candidate_revision and proof['experiment_complete'] is True
                and proof['passed'] == 2041 and proof['cleanup']['cleanup_passed'] is True,
                'complete fresh current typed proof required for Rust refusal')
        candidates = [item for item in proof['rows'] if item['target'] == 'rust'
                      and item['identity'] == {'phase': 'phase2', 'id': identity}]
        require(len(candidates) == 1, 'Rust refusal typed proof identity differs')
        record = candidates[0]
        process_proof = typed_process_proof(ctx, proof, record)
        require(record['state'] == 'passed' and record['http_status'] == 400, 'Rust refusal proof did not pass')
        directory = root / 'rows' / f"{record['index']:04d}"
        request, response = read(directory / 'request.raw'), read(directory / 'response.raw')
        require(sha(request) == record['expected_request_sha256'] and sha(response) == record['current_response_sha256'],
                'Rust refusal request or reply changed')
        checker_module = ctx.module('current_refusal_checker', CHECKER)
        checker = checker_module.ExperimentalChecker(ctx.repo)
        fresh_sources = checker.source_record()
        require(fresh_sources['commit'] == ctx.candidate_revision
                and all(proof['initial_identities']['source'].get(path) == expected
                        for path, expected in fresh_sources['source_sha256'].items()),
                'typed proof omits independently derived checker source authority')
        body = json.loads(request)
        require(body.get('format') == 'som.prompt-json.v1' and body.get('target') == 'rust'
                and checker_module.value_sha(body['prompt']) == row['prompt_sha256'],
                'Rust refusal typed request names another prompt or target')
        baseline = json.loads(ctx.sources.read(checker_module.BASELINE))
        require(sha(ctx.sources.read(checker_module.BASELINE)) == checker_module.BASELINE_SHA256,
                'Rust refusal accepted request source differs')
        frozen = [r for r in baseline['baselines']['rust']['rows'] if r['check'] == 'compile_equal'
                  and r['phase'] == 'phase2' and r['id'] == identity]
        require(len(frozen) == 1 and sha(request) == frozen[0]['qa_request_sha256'],
                'Rust refusal full request differs from frozen accepted bytes')
        verified = checker.verify_response('rust', record['identity'], 400, response)
        require(verified.report['byte_compatibility_passed'] is True and json.loads(response)['code'] == 'INVALID_VALUE',
                'complete frozen Rust compiler refusal bytes differ')
        result['rust_typed'] = {'compile_row_index': record['index'], 'request_sha256': sha(request),
                                'response_sha256': sha(response), 'checker': verified.report}
        result['typed_process_proof'] = process_proof
    return result
