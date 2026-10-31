"""Replay the original full Python/Rust baseline assertions from saved HTTP.

This module executes no commands and opens no sockets. The caller must first
derive native source authority and check the complete input/capture inventories.
Only the named proof rows returned here replace old hash-only audit statements.
Other old attestations remain incomplete.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_http_replay.py'
PYTHON = 'som-rs/scripts/typescript_output_acceptance/check_python_baseline.py'
RUST = 'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py'
DIGESTS = BASE + 'typescript_native_digests.py'
INPUTS = BASE + 'typescript_regression_inputs.py'
GOLDENS = BASE + 'inputs/public-golden-hashes-v1.json'
PHASE2_INVENTORY = 'som-rs/contracts/rust-output-v1/phase2/case-inventory.json'
CURRENT = 'http://127.0.0.1:18978'
BEFORE = 'http://127.0.0.1:18979'
REFUSALS = ('RS12_CNL_V3_FILTER_LIMIT', 'RS13_JSON_V3_FILTER_OFFSET')


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate replay JSON member')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite replay JSON')))


def regular(path, limit=64 * 1024**2):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
            'physical raw replay file required')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
                'bounded regular raw replay file required')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    require(len(raw) == before.st_size and (before.st_dev, before.st_ino, before.st_size,
            before.st_mode, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mode, after.st_mtime_ns, after.st_ctime_ns),
            'raw replay file changed during read')
    return raw


class Tape:
    """A strict replacement for the original checker's post_raw function."""
    def __init__(self, root):
        self.root = Path(root).absolute()
        require(self.root.is_dir() and not any(p.is_symlink() for p in (self.root, *self.root.parents)),
                'physical checker HTTP capture required')
        self.index_raw = regular(self.root / 'index.json')
        document = parse(self.index_raw)
        require(document.get('version') == 'som.typescript-regression-raw-http.v1'
                and document.get('product_acceptance_passed') is False
                and type(document.get('rows')) is list, 'exact original HTTP tape required')
        self.rows, self.position = document['rows'], 0
        expected = {'index.json', 'checker-postcheck.json'} | {
            f'{i:06d}/{name}' for i in range(len(self.rows))
            for name in ('request.raw', 'response.raw', 'result.json')}
        actual = set()
        for path in self.root.rglob('*'):
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                    'HTTP tape link/special member refused')
            if stat.S_ISREG(info.st_mode):
                relative = path.relative_to(self.root).as_posix()
                if relative.split('/')[0] not in {'raw-commands', 'cli-raw', 'product-http'}:
                    actual.add(relative)
        require(actual == expected, 'complete HTTP tape member universe differs')
        for index, row in enumerate(self.rows):
            prefix = self.root / f'{index:06d}'
            require(type(row) is dict and row.get('index') == index and row.get('complete') is True
                    and row.get('method') == 'POST' and type(row.get('status')) is int
                    and 200 <= row['status'] <= 599
                    and row.get('url') in {base + route for base in (CURRENT, BEFORE)
                                         for route in ('/v1/compile', '/v1/assemble')}
                    and parse(regular(prefix / 'result.json')) == row,
                    'original HTTP tape row/endpoint/terminal state differs')
            require(sha(regular(prefix / 'request.raw')) == row.get('request_sha256')
                    and sha(regular(prefix / 'response.raw')) == row.get('response_sha256'),
                    'complete original HTTP tape bytes differ')

    def __call__(self, base_url, route, data, timeout=120.0):
        require(base_url in {CURRENT, BEFORE} and route in {'/v1/compile', '/v1/assemble'}
                and type(data) is bytes and self.position < len(self.rows),
                'missing or out-of-scope original HTTP replay slot')
        row = self.rows[self.position]
        prefix = self.root / f'{self.position:06d}'
        require(row['url'] == base_url + route and regular(prefix / 'request.raw') == data,
                'source-derived HTTP request/order differs from original capture')
        raw = regular(prefix / 'response.raw')
        require(sha(data) == row['request_sha256'] and sha(raw) == row['response_sha256'],
                'original HTTP tape changed after admission')
        self.position += 1
        return row['status'], raw

    def finish(self):
        require(self.position == len(self.rows) and regular(self.root / 'index.json') == self.index_raw,
                'unconsumed original HTTP exchange or changed tape index')
        return {'raw_http_exchanges': self.position, 'index_sha256': sha(self.index_raw)}


@contextmanager
def assertions(module, ctx, tape):
    """Supply checked data authority explicitly, without a runtime Context import."""
    require(callable(getattr(module, 'native_digest_context', None)),
            'actual native-only baseline adapter must be in the owning source')
    old_post, old_context = module.post_raw, module.native_digest_context
    digest = ctx.module('typescript_http_replay_native_digests', DIGESTS)
    module.post_raw, module.native_digest_context = tape, lambda: (ctx, digest)
    try:
        yield
    finally:
        module.post_raw, module.native_digest_context = old_post, old_context


def named_inputs(items, expected, *, phase=None, ordered=True, value_sha=None):
    require(len(items) == len(expected) and len({row['id'] for row in items}) == len(items)
            and {row['id'] for row in items} == {row['id'] for row in expected}
            and (not ordered or [row['id'] for row in items] == [row['id'] for row in expected]),
            'complete source-owned baseline input names/order differ')
    expected_by_name = {row['id']: row for row in expected}
    for item in items:
        golden = expected_by_name[item['id']]
        require(not item.get('not_run_reason') and (phase is None or item.get('phase') == phase),
                'source-owned baseline input is missing/not-run')
        prompt = item.get('prompt') or (item.get('body') or {}).get('prompt')
        require(type(prompt) is dict, 'complete source-owned typed prompt required')
        expected_prompt = golden.get('prompt_sha256')
        prompt_hash = (value_sha(prompt) if value_sha is not None else sha(canonical(prompt)))
        require(expected_prompt is None or prompt_hash == expected_prompt,
                'typed prompt differs from complete source-owned named input')
        expected_files = golden.get('files_map_sha256')
        if phase is not None:
            require(item.get('files_sha256') == expected_files,
                    'accepted full file-map binding differs for named baseline input')


def successful_report(report, rows):
    require(report.get('rows') == rows and report.get('status') == 'passed' and report.get('passed') is True
            and report.get('problems') == [] and report.get('total') == report.get('total_rows') == len(rows)
            and report.get('passed_count') == report.get('passed_rows') == len(rows)
            and report.get('failed_count') == report.get('failed_rows') == 0
            and report.get('not_run_count') == report.get('not_run_rows') == 0
            and all(row.get('passed') is True and row.get('status') == 'passed' for row in rows),
            'complete original baseline assertion rows or totals differ')


def python_baseline(ctx, root, goldens):
    module = ctx.module('typescript_http_replay_original_python', PYTHON)
    digest = ctx.module('typescript_http_replay_native_digests', DIGESTS)
    factory, _, _, _ = digest.authorities(ctx, 'python')
    inputs = root / 'inputs'
    public_root, legacy_root = inputs / 'public_run', inputs / 'legacy_run/run-005'
    public, public_meta = module.read_public_inputs(public_root)
    legacy, legacy_meta = module.read_legacy_inputs(legacy_root)
    named_inputs(public, goldens['python_public'], value_sha=factory.value_sha)
    named_inputs(legacy, goldens['python_legacy'], value_sha=factory.value_sha)
    require(len(public) == 127 and len(legacy) == 544 and module.VARIANTS == ('none', 'python')
            and public_meta['accepted_required'] == public_meta['records_count'] == 127
            and legacy_meta['accepted_required'] == legacy_meta['records_count'] == 544,
            'all671 original Python inputs and both variants required')
    tape = Tape(root / 'capture/raw-checkers/06')
    rows = []
    with assertions(module, ctx, tape):
        for item in public + legacy:
            for variant in module.VARIANTS:
                row, blobs = module.evaluate_variant(CURRENT, BEFORE, item, variant)
                require(row.get('passed') is True and row.get('status') == 'passed' and not blobs,
                        'original full Python baseline assertion refused')
                rows.append(row)
    proof = tape.finish()
    require(len(rows) == 1342, 'all671 times two original Python assertion rows required')
    report_raw = regular(root / 'capture/python-baseline/report.json')
    report = parse(report_raw)
    successful_report(report, rows)
    require(report.get('version') == 'som.typescript-output-python-baseline.v1'
            and report.get('qa') == report.get('qa_url') == CURRENT
            and report.get('baseline') == report.get('baseline_url') == BEFORE
            and report.get('public_result_sha256') == public_meta['result_sha256']
            and report.get('legacy_result_sha256') == legacy_meta['result_sha256']
            and report.get('expected_public') == 127 and report.get('expected_legacy') == 544
            and report.get('expected_rows') == report.get('expected_total') == 1342
            and report.get('row_count_matches') is True,
            'original Python report source-input/endpoint/count bindings differ')
    require(report.get('totals', {}).get('by_source') == {
                'public': {'expected_rows': 254, 'total': 254, 'passed': 254, 'failed': 0, 'not_run': 0},
                'legacy': {'expected_rows': 1088, 'total': 1088, 'passed': 1088, 'failed': 0, 'not_run': 0}}
            and report.get('totals', {}).get('by_variant') == {
                name: {'expected_rows': 671, 'total': 671, 'passed': 671, 'failed': 0, 'not_run': 0}
                for name in ('none', 'python')}, 'Python source/variant totals differ')
    return {**proof, 'target': 'python', 'input_count': 671, 'assertion_rows': 1342,
            'report_sha256': sha(report_raw), 'rederived_rows_sha256': sha(canonical(rows)),
            'replaces': ['ts.python_unchanged.0', 'rust.amendment005.2'],
            'product_acceptance_passed': False}


@contextmanager
def bounded_phase2_reader(module, ctx, root):
    """The old loader may read captured inputs or exact Git data, never .local."""
    phase2, original = module.phase2, module.phase2.read
    root, repo = root.absolute(), Path(ctx.repo).absolute()
    def checked(path):
        path = Path(path).absolute()
        require(not any(p.is_symlink() for p in (path, *path.parents)), 'physical phase2 data read required')
        if repo in path.parents:
            raw = ctx.sources.read(path.relative_to(repo).as_posix())
            require(regular(path) == raw, 'phase2 loader Git data differs')
        else:
            require(root in path.parents, 'phase2 loader attempted an undeclared external input')
            raw = regular(path)
        return parse(raw)
    phase2.read = checked
    old_caches = (module._LOADED_PROMPT_CACHE, module._STATIC_PROMPT_CACHE, module._LEGACY_INDEX_CACHE)
    module._LOADED_PROMPT_CACHE, module._STATIC_PROMPT_CACHE, module._LEGACY_INDEX_CACHE = {}, None, None
    try:
        yield
    finally:
        phase2.read = original
        module._LOADED_PROMPT_CACHE, module._STATIC_PROMPT_CACHE, module._LEGACY_INDEX_CACHE = old_caches


def rust_baseline(ctx, root, goldens):
    module = ctx.module('typescript_http_replay_original_rust', RUST)
    ctx.module('typescript_http_replay_input_adapter', INPUTS).configure(ctx, module)
    run = root / 'inputs/rust_run'
    require((run / 'phase2/cases').is_dir(), 'complete restored phase2 records required; no implicit fallback')
    with bounded_phase2_reader(module, ctx, root / 'inputs'):
        phase1, phase1_sha, problems1 = module.load_phase1_cases(run)
        phase2, record_hashes, problems2 = module.load_phase2_cases(run)
    require(not problems1 and not problems2 and len(phase1) == 115 and len(phase2) == 584,
            'all115 plus584 original Rust inputs required')
    # The original record loader owns iteration order. The source inventory owns
    # the complete name set; the strict tape then proves that actual loader order.
    named_inputs(phase1, goldens['rust_phase1'], phase='phase1', ordered=False, value_sha=module.canonical_sha256)
    named_inputs(phase2, goldens['rust_phase2'], phase='phase2', ordered=False, value_sha=module.canonical_sha256)
    inventory = parse(ctx.sources.read(PHASE2_INVENTORY))['rows']
    require({item['id'] for item in phase2} == {item['id'] for item in inventory}
            and len(record_hashes) == 584, 'all584 named phase2 source records required')
    actual_refusals = [item['id'] for item in phase1 + phase2 if item['is_refusal']]
    require(sorted(actual_refusals) == list(REFUSALS), 'only the two original compiler refusals are admitted')
    tape = Tape(root / 'capture/raw-checkers/11')
    rows, refusals, response_files = [], [], {}
    with assertions(module, ctx, tape):
        for item in phase1 + phase2:
            case_rows, blobs, refused = module.evaluate_case(CURRENT, BEFORE, item)
            require(len(case_rows) == len(blobs), 'complete original Rust assertion/blob pairing required')
            if refused:
                refusals.append(item['id'])
            for row, (check_name, raw_blobs) in zip(case_rows, blobs):
                require(row.get('passed') is True and row.get('status') == 'passed',
                        'original full Rust baseline/refusal/rebase assertion refused')
                if raw_blobs:
                    prefix = '_'.join(module._safe_filename(value) for value in
                                      (row['phase'], row['id'], check_name))
                    saved = {}
                    for suffix, raw in sorted(raw_blobs.items()):
                        relative = f'responses/{prefix}.{suffix}'
                        require(relative not in response_files and regular(root / 'capture/rust-baseline' / relative) == raw,
                                'complete original native Rust raw response binding differs')
                        response_files[relative] = sha(raw)
                        saved[suffix] = relative
                    row['saved_responses'] = saved
                rows.append(row)
    proof = tape.finish()
    require(len(rows) == 2093 and sorted(refusals) == list(REFUSALS),
            'all697 product checks and two exact compiler refusals required')
    responses = root / 'capture/rust-baseline/responses'
    actual = {path.relative_to(root / 'capture/rust-baseline').as_posix()
              for path in responses.rglob('*') if path.is_file()}
    require(actual == set(response_files), 'Rust successful/failed raw response universe differs')
    report_raw = regular(root / 'capture/rust-baseline/report.json')
    report = parse(report_raw)
    successful_report(report, rows)
    record_digest = sha(''.join(f'{cid}\0{pin}\n' for cid, pin in sorted(record_hashes.items())).encode())
    require(report.get('version') == 'som.typescript-output-rust-baseline.v1'
            and report.get('qa') == report.get('qa_url') == CURRENT
            and report.get('baseline') == report.get('baseline_url') == BEFORE
            and report.get('rust_products_sha256') == phase1_sha
            and report.get('phase2_cases_sha256') == record_digest
            and report.get('records_sha256') == {'rust_products_json': phase1_sha,
                'phase2_cases_digest': record_digest, 'phase2_case_files': record_hashes}
            and report.get('refusal_cases') == report.get('rs12_rs13_cases') == list(REFUSALS),
            'original Rust report input/endpoint/refusal bindings differ')
    require(report.get('totals') == {
        'phase1_cases': 115, 'phase2_cases': 584, 'total_cases': 699,
        'refusal_cases': 2, 'total_rows': 2093, 'passed': 2093, 'failed': 0, 'not_run': 0,
        'by_check': {name: {'total': count, 'passed': count, 'failed': 0, 'not_run': 0}
                     for name, count in (('compile_equal', 699), ('baseline_bundle_refused', 697), ('rebased_files', 697))},
        'by_phase': {'phase1': {'cases': 115, 'total_rows': 345, 'passed': 345, 'failed': 0, 'not_run': 0},
                     'phase2': {'cases': 584, 'total_rows': 1748, 'passed': 1748, 'failed': 0, 'not_run': 0}}},
        'complete Rust check/phase totals differ')
    for row in tape.rows:
        if row['status'] == 400 and row['url'].endswith('/v1/compile'):
            raw = regular(tape.root / f"{row['index']:06d}" / 'response.raw')
            require(parse(raw).get('code') == 'INVALID_VALUE', 'the two raw compiler refusal codes differ')
    return {**proof, 'target': 'rust', 'input_count': 699, 'product_count': 697,
            'compiler_refusals': list(REFUSALS), 'assertion_rows': 2093,
            'report_sha256': sha(report_raw), 'rederived_rows_sha256': sha(canonical(rows)),
            'raw_response_files_sha256': sha(canonical(response_files)),
            'replaces': ['rust.amendment005.4', 'rust.amendment005.6'],
            'partial_support_for': ['rust.phase2.refusals_amendment_004'],
            'product_acceptance_passed': False}


def verify(ctx, root, capture):
    require(ctx.qa_mode == 'native-qa-v1' and ctx.helper_revision == ctx.candidate_revision
            and ctx.qa_source_authority.get('source_authority_active') is True
            and {SELF, PYTHON, RUST, DIGESTS, INPUTS} <=
                set(ctx.qa_source_authority['admitted_repository_python_sources']),
            'exact native replay/helper source admission required')
    require(capture.get('helper_revision') == capture.get('candidate_revision') == ctx.helper_revision,
            'original capture/replay source identity differs')
    ctx.sources.read(SELF)
    goldens = parse(ctx.sources.read(GOLDENS))['cohorts']
    require({key: len(goldens[key]) for key in
            ('python_public', 'python_legacy', 'rust_phase1', 'rust_phase2')} ==
            {'python_public': 127, 'python_legacy': 544, 'rust_phase1': 115, 'rust_phase2': 584},
            'complete original baseline source inventory required')
    root = Path(root).absolute()
    python = python_baseline(ctx, root, goldens)
    rust = rust_baseline(ctx, root, goldens)
    replacements = {name: proof for proof in (python, rust) for name in proof['replaces']}
    return {'version': 'som.typescript-regression-original-baseline-http-proof.v1',
            'helper_revision': ctx.helper_revision, 'candidate_revision': ctx.candidate_revision,
            'python': python, 'rust': rust, 'replacements': replacements,
            'only_named_replacements_complete': True, 'whole_domain_complete': False,
            'product_acceptance_passed': False}
