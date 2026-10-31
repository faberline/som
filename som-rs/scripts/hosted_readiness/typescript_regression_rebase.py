"""Current TypeScript125 exact raw comparison, old refusal and narrow rebase.

The original TypeScript001/Rust005 gate is unchanged. This is the additional
TypeScript002/native source-owned proof against the explicit #65 before role.
"""
from __future__ import annotations
import traceback
import sys
import argparse
import copy
import hashlib
import json
from pathlib import Path
import urllib.error
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_rebase.py'
DIGESTS = BASE + 'typescript_native_digests.py'
BUNDLES = 'som-rs/scripts/typescript_output_acceptance/check_bundles.py'
CURRENT = 'http://127.0.0.1:18978'
BEFORE = 'http://127.0.0.1:18979'
VERSION = 'som.typescript002-native-refusal-rebase.v1'


def failure_record(error):
    return {'error': type(error).__name__, 'reason': str(error),
            'traceback': ''.join(traceback.format_exception(type(error), error, error.__traceback__))}


def remember_failure(state, role, error):
    record = {'role': role, **failure_record(error)}
    if state['primary'] is None:
        state.update(primary=error, traceback=error.__traceback__, failure=record)
        error.__som_typescript_failure_record__ = record
    else:
        state['secondary'].append(record)
    state['primary'].__som_typescript_secondary_failures__ = state['secondary']
    return record


def raise_primary(state):
    if state['primary'] is None:
        return
    try:
        sys.stderr.write(json.dumps({'typescript_primary_failure': state['failure'],
            'secondary_failures': state['secondary']}, ensure_ascii=False, sort_keys=True) + '\n')
    except BaseException as error:
        remember_failure(state, 'primary_traceback_stderr', error)
    raise state['primary'].with_traceback(state['traceback'])


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical(value) + b'\n')


def cases(ctx, legacy):
    require({SELF, DIGESTS, BUNDLES} <= set(ctx.qa_source_authority['admitted_repository_python_sources']),
            'complete current TypeScript rebase QA source admission required')
    checker = ctx.module('typescript_regression_original_bundles', BUNDLES)
    inventory = json.loads(ctx.sources.read('som-rs/contracts/typescript-output-v1/case-inventory.json'))
    examples = json.loads(ctx.sources.read('som-rs/contracts/typescript-output-v1/target-examples.json'))
    ordinary = checker.collect_in_scope_cases(inventory, examples)
    legacy_cases, errors = checker.collect_legacy_in_scope_cases(inventory, legacy, checker.EXPECTED_LEGACY_IN_SCOPE)
    require(not errors and len(ordinary) == 25 and len(legacy_cases) == 100,
            'all original25 TypeScript cases and100 legacy jobs required')
    result = ordinary + legacy_cases
    require(len({row['id'] for row in result}) == 125 and all(not row.get('file_problems')
            and not row.get('not_run_error') and row.get('requests') for row in result),
            'complete disjoint source-bound TypeScript125 requests required')
    return checker, result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class CaptureTransport:
    def __init__(self, root):
        self.root, self.rows = root, []
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def __call__(self, side, route, body):
        require(side in {'current', 'before'} and route in {'/v1/compile', '/v1/assemble'},
                'exact shared QA route required')
        url = (CURRENT if side == 'current' else BEFORE) + route
        index = len(self.rows); prefix = 'http/' + f'{index:06d}'
        request = Request(url, data=body, headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with self.opener.open(request, timeout=120) as response:
                status, raw = response.status, response.read(32 * 1024**2 + 1)
        except urllib.error.HTTPError as error:
            with error:
                status, raw = error.code, error.read(32 * 1024**2 + 1)
        require(len(raw) <= 32 * 1024**2, 'bounded complete QA response required')
        directory = self.root / prefix; directory.mkdir(parents=True)
        (directory / 'request.raw').write_bytes(body); (directory / 'response.raw').write_bytes(raw)
        row = {'index': index, 'side': side, 'url': url, 'method': 'POST', 'route': route,
               'status': status, 'request_path': prefix + '/request.raw', 'request_sha256': sha(body),
               'response_path': prefix + '/response.raw', 'response_sha256': sha(raw)}
        save(directory / 'result.json', row); self.rows.append(row)
        return status, raw


class ReplayTransport:
    def __init__(self, root, rows):
        self.root, self.rows, self.index = root, rows, 0

    def __call__(self, side, route, body):
        require(self.index < len(self.rows), 'missing original raw TypeScript QA exchange')
        row = self.rows[self.index]; index = self.index; self.index += 1
        prefix = 'http/' + f'{index:06d}'
        require(row['index'] == index and row['side'] == side and row['route'] == route
                and row['url'] == (CURRENT if side == 'current' else BEFORE) + route
                and row['method'] == 'POST' and row['request_path'] == prefix + '/request.raw'
                and row['response_path'] == prefix + '/response.raw', 'raw QA slot/owner/route differs')
        request = (self.root / row['request_path']).read_bytes()
        raw = (self.root / row['response_path']).read_bytes()
        require(request == body and sha(body) == row['request_sha256'] and sha(raw) == row['response_sha256']
                and json.loads((self.root / prefix / 'result.json').read_bytes()) == row,
                'complete original raw QA exchange differs')
        return row['status'], raw


def check_one(ctx, checker, item, exchange):
    digest = ctx.module('typescript_regression_native_digests', DIGESTS)
    result = {'id': item['id'], 'group': item['group'], 'forms': []}
    requests = list(item['requests'])
    for index, (form, text) in enumerate(requests):
        require(type(text) is str and form in {'json', 'cnl'}, 'original source request form required')
        body = text.encode('utf-8')
        old_status, old_raw = exchange('before', '/v1/compile', body)
        current_status, current_raw = exchange('current', '/v1/compile', body)
        require(old_status == current_status == 200, 'in-scope original and current TypeScript compile must both succeed')
        proof = digest.compare(ctx, 'typescript', old_raw, current_raw)
        before, current = json.loads(old_raw), json.loads(current_raw)
        old_bundle, current_bundle = before['bundle'], current['bundle']
        products = {}
        for side, bundle in (('before', old_bundle), ('current', current_bundle)):
            status, raw = exchange(side, '/v1/assemble', compact({'bundle': bundle}))
            value = json.loads(raw)
            require(status == 200 and value.get('status') == 'ok' and type(value.get('files')) is dict
                    and len(value['files']) == 11 and all(type(k) is str and type(v) is str for k, v in value['files'].items()),
                    'complete original/current eleven-file TypeScript assembly required')
            products[side] = value['files']
        require(products['current'] == products['before'], 'full TypeScript product file bytes differ from #65 before')
        old_request = compact({'bundle': old_bundle})
        status, raw = exchange('current', '/v1/assemble', old_request)
        errors = checker.check_refusal_response(status, raw, old_request.decode('utf-8'),
            {'http_status': 400, 'code': 'ASSET_HASH_MISMATCH', 'prompt_pointer': '/bundle/assets'})
        require(not errors, 'unchanged old TypeScript bundle refusal/span differs: ' + repr(errors))
        rebased, rebase_proof = digest.rebase(ctx, 'typescript', old_raw, current_raw)
        status, raw = exchange('current', '/v1/assemble', compact({'bundle': rebased}))
        document = json.loads(raw)
        require(status == 200 and document.get('status') == 'ok' and document.get('files') == products['before'],
                'native digest-only rebase changed complete TypeScript files')
        result['forms'].append({'index': index, 'form': form, 'request_sha256': sha(body),
            'digest_proof': proof, 'rebase_proof': rebase_proof,
            'files_map_sha256': sha(canonical(products['before'])), 'passed': True})
        if index == 0 and len(requests) == 1 and item.get('derive_json_from_cnl_bundle'):
            requests.append(('json', compact({'format': 'som.prompt-json.v1',
                'prompt': current_bundle['prompt'], 'target': 'typescript'}).decode('utf-8')))
    require(result['forms'], 'every named TypeScript case needs its full source forms')
    result['passed'] = True
    return result


def verify_capture(ctx, root, legacy):
    report = json.loads((root / 'result.json').read_bytes())
    require(report['version'] == VERSION and report['helper_revision'] == report['candidate_revision'] == ctx.helper_revision
            and report['product_acceptance_passed'] is False, 'current TypeScript rebase source identity differs')
    checker, expected = cases(ctx, legacy)
    require(len(report['rows']) == 125 and [row['id'] for row in report['rows']] == [item['id'] for item in expected],
            'complete ordered TypeScript125 rebase scope required')
    replay = ReplayTransport(root, report['http'])
    for item, row in zip(expected, report['rows']):
        require(check_one(ctx, checker, item, replay) == row, 'source-derived original TypeScript rebase assertions differ')
    require(replay.index == len(report['http']), 'extra original TypeScript raw QA exchange')
    expected_files = {'result.json'} | {f'http/{i:06d}/{name}' for i in range(replay.index)
        for name in ('request.raw', 'response.raw', 'result.json')}
    require({p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()} == expected_files,
            'TypeScript125 raw member universe differs')
    return {'version': 'som.typescript002-native-refusal-rebase-proof.v1',
            'named_cases': 125, 'raw_http_exchanges': replay.index,
            'original_result_sha256': sha((root / 'result.json').read_bytes()),
            'all_required_passed': True, 'product_acceptance_passed': False}


def main():
    import full_product_context as hosted
    ctx = hosted.context()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    require(ctx.qa_mode == 'native-qa-v1' and ctx.helper_revision == ctx.candidate_revision,
            'checked native Context required before current TypeScript125')
    checker, inputs = cases(ctx, args.legacy_run)
    require(ctx.work in args.out.absolute().parents and not args.out.exists(), 'new Context-owned TypeScript rebase output required')
    args.out.mkdir(parents=True)
    capture = CaptureTransport(args.out)
    report = {'version': VERSION, 'helper_revision': ctx.helper_revision, 'candidate_revision': ctx.candidate_revision,
              'rows': [], 'http': capture.rows, 'product_acceptance_passed': False}
    failure = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
    try:
        for item in inputs:
            try:
                report['rows'].append(check_one(ctx, checker, item, capture))
            except Exception as error:
                report['rows'].append({'id': item['id'], 'group': item['group'], 'passed': False,
                                      **remember_failure(failure, 'original_refusal_rebase_' + item['id'], error)})
    except BaseException as error:
        remember_failure(failure, 'original_refusal_rebase_interrupted', error)
    finally:
        if failure['primary'] is not None:
            report['primary_failure'] = failure['failure']
        report['secondary_failures'] = failure['secondary']
        try:
            save(args.out / 'result.json', report)
        except BaseException as error:
            remember_failure(failure, 'refusal_rebase_result_evidence', error)
        try:
            ctx.verify()
        except BaseException as error:
            remember_failure(failure, 'refusal_rebase_context_postcheck', error)
    raise_primary(failure)
    verify_capture(ctx, args.out, args.legacy_run)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
