#!/usr/bin/env python3
"""Execute whole frozen product cohorts on GitHub with recorded input transport.

Preparation results describe fresh commands on saved candidate product bytes.
They never grant compiler acceptance or transfer product results to a new build.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import signal
import stat
import subprocess
import sys
import time
import traceback
from types import ModuleType
import uuid

SELF = 'som-rs/scripts/hosted_readiness/run_full_products.py'
CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
CARGO = 'som-rs/scripts/hosted_readiness/product_runtime/consume_cargo.py'
STAGES = ('python-quality', 'phase1', 'phase2-python', 'phase2-rust')
PENDING = ('fresh compile and assemble determinism and refusals', 'CLI forwarding and diagnostics',
           'old bundle refusal and exact rebase', 'recorded baseline compiler comparisons',
           'ordinary-mode installer file scope independent audit',
           'numbered native policy sealing and independent complete acceptance audit')
CALLBACK_ADAPTERS = {
    'typescript-regression': 'som-rs/scripts/hosted_readiness/typescript_regression_owner.py',
    'm6-react003': 'som-rs/scripts/react_output_acceptance/m6_react_callback.py',
}
NATIVE_QA_ENTRIES = {
    'som-core/scripts/verify_python_quality.py': 'entry.qa.python-quality',
    'som-core/scripts/verify_service_v3.py': 'entry.qa.legacy-service',
    'som-rs/behavior/check_behavior.py': 'entry.qa.behavior',
    'som-rs/behavior/phase2/check_phase2.py': 'entry.qa.phase2-python',
    'som-rs/tests/rust-output-v1/check_contract.py': 'entry.qa.rust-contract',
    'som-rs/tests/typescript-output-v1/check_contract.py': 'entry.qa.typescript-contract',
    'som-rs/scripts/hosted_readiness/typescript_regression_checker.py': 'entry.qa.typescript-wrapper',
    'som-rs/scripts/hosted_readiness/product_runtime/recorded_before_oracle.py': 'entry.qa.recorded-before',
    'som-rs/scripts/hosted_readiness/run_experimental_native_cnl.py': 'entry.qa.native-cnl',
}


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _native_controls(repo, helper_revision, candidate_revision):
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    require(Path(repo).absolute() == controls.root and helper_revision == controls.helper_revision
            == candidate_revision == controls.candidate_revision,
            'active full-product source identity differs')
    return controls


def load_context_module(repo, revision, qa_mode=None, candidate_revision=None):
    """Bootstrap only exact committed raw Python, including this entry point."""
    repo = Path(repo).absolute()
    require(re.fullmatch('[0-9a-f]{40}', revision), 'full helper source commit required')
    def git(*args):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo), *args],
                                       stderr=subprocess.DEVNULL)
    require(git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision, 'helper HEAD differs')
    def source(name):
        path = repo / name
        require(not any(item.is_symlink() for item in (path, *path.parents)), 'linked bootstrap source refused')
        entry = git('ls-tree', '-z', revision, '--', name)
        require(entry.startswith((b'100644 blob ', b'100755 blob ')) and entry.endswith(b'\t' + name.encode() + b'\0')
                and entry.count(b'\0') == 1, 'regular committed bootstrap source required')
        raw = git('cat-file', 'blob', revision + ':' + name)
        require(raw == path.read_bytes(), 'bootstrap source differs from committed bytes')
        return raw
    require(source(SELF) == Path(__file__).read_bytes(), 'entry point differs from helper commit')
    native_authority = None
    if qa_mode == 'native-qa-v1':
        require(revision == candidate_revision and revision != 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa',
                'native QA entry requires one new exact source candidate')
        path = 'som-rs/scripts/hosted_readiness/native_qa_authority.py'
        authority_raw = source(path)
        controls = _native_controls(repo, revision, candidate_revision)
        require(controls.role_sources['bootstrap.authority'] == path, 'fixed native authority seed source differs')
        authority = controls.load_module('bootstrap.authority')
        require(authority.__som_raw_sha256__ == digest(authority_raw), 'preloaded native authority source differs')
        tracked = git('ls-tree', '-r', '-z', '--name-only', revision)
        require(tracked.endswith(b'\0'), 'complete tracked source inventory required')
        paths = [name.decode('utf-8', 'strict') for name in tracked.split(b'\0') if name]
        require(paths == sorted(set(paths)), 'ordered complete tracked source inventory required')
        native_authority = authority.verify(source, mode='native-qa-v1', source_paths=paths,
            helper_revision=revision, candidate_revision=candidate_revision)
        require(native_authority.get('source_authority_active') is True
                and native_authority.get('mode') == 'native-qa-v1'
                and native_authority.get('helper_revision') == revision
                and native_authority.get('candidate_revision') == candidate_revision
                and native_authority.get('product_acceptance_passed') is False
                and native_authority.get('compiler_acceptance_passed') is False
                and native_authority.get('whole_acceptance_complete') is False, 'native QA entry source authority is pending or mismatched')
    elif qa_mode not in {None, 'historical', 'experimental-preparation'}:
        raise ValueError('unknown source authority mode')
    raw = source(CONTEXT)
    if qa_mode == 'native-qa-v1':
        require(controls.role_sources['runtime.context'] == CONTEXT, 'fixed native Context source differs')
        module = controls.load_module('runtime.context')
        require(module.__file__ == str(repo / CONTEXT) and module.__som_raw_sha256__ == digest(raw),
                'actual Core Context source differs from committed bytes')
        module.__som_native_source_authority__ = native_authority
        return module
    require('full_product_context' not in sys.modules, 'preloaded context module refused')
    module = ModuleType('full_product_context')
    module.__file__, module.__cached__, module.__package__ = str(repo / CONTEXT), None, ''
    module.__som_raw_sha256__ = digest(raw)
    module.__som_native_source_authority__ = native_authority
    sys.modules[module.__name__] = module
    exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    return module


def execute_tool(arguments):
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux execution only')
    context_path = Path(os.environ['SOM_FULL_PRODUCT_CONTEXT'])
    data = json.loads(context_path.read_bytes())
    work = Path(data['work_root']).absolute()
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(temporary in work.parents and work.is_dir() and '..' not in work.parts
            and not any(part.is_symlink() for part in (work, *work.parents)), 'owned checker evidence root required')
    module, ctx, sources, old_main, old_argv = None, None, None, None, None
    entry_error, legacy_finder, legacy_finder_owned, old_path = None, None, False, None
    tool = arguments[0] if arguments else ''
    proof = {'version': 'som.full-product-checker-postcheck.v1', 'tool': tool, 'pid': os.getpid(),
             'helper_revision': data['helper_revision'], 'candidate_revision': data['candidate_revision'],
             'stage': data['stage'], 'shard': data['shard'], 'entry_executed': False,
             'source_runtime_tools_inputs_unchanged': False, 'product_acceptance_passed': False}
    try:
        module = load_context_module(Path(data['repo']), data['helper_revision'], data.get('qa_mode'), data['candidate_revision'])
        ctx = module.Context(context_path)
        module._ACTIVE = ctx
        sources = ctx.sources
        sources.read(SELF)
        sources.read(CONTEXT)
        module.relative(tool)
        require(tool.endswith('.py'), 'fixed repository Python checker required')
        if data.get('qa_mode') == 'native-qa-v1':
            authority = ctx.qa_source_authority
            require(authority == module.__som_native_source_authority__
                    and authority['source_authority_active'] is True
                    and tool in authority['admitted_repository_python_sources'],
                    'entry itself must have exact sealed native source authority before read or execution')
            ctx.activate_python_paths(sys.executable)
        else:
            before_finders = tuple(sys.meta_path)
            legacy_finder = module.install_source_finder(sources)
            legacy_finder_owned = legacy_finder not in before_finders and legacy_finder in sys.meta_path
            require(legacy_finder_owned and getattr(legacy_finder, 'sources', None) is sources,
                    'new owned legacy raw source guard required')
        # Native Context owns its lease. The old modes keep their original raw Git finder.
        raw = sources.read(tool)
        proof['entry_sha256'] = digest(raw)
        if data.get('qa_mode') == 'native-qa-v1':
            controls = _native_controls(sources.root, data['helper_revision'], data['candidate_revision'])
            require(tool in NATIVE_QA_ENTRIES, 'repository checker has no fixed native entry role')
            role = NATIVE_QA_ENTRIES[tool]
            require(controls.role_sources[role] == tool, 'fixed native checker role source differs')
            proof['entry_executed'] = True
            try:
                code = controls.execute_entry(role, list(arguments[1:]))
                proof['entry_exit_code'] = code
                return code
            except SystemExit as result:
                code = result.code if isinstance(result.code, int) else (0 if result.code is None else 1)
                proof['entry_exit_code'] = code
                return code
        name = '__main__'
        target = ModuleType(name)
        target.__file__, target.__package__, target.__cached__ = str(sources.root / tool), '', None
        target.__som_raw_sha256__ = digest(raw)
        old_main, old_argv = sys.modules[name], sys.argv
        sys.modules[name], sys.argv = target, [target.__file__, *arguments[1:]]
        old_path = list(sys.path)
        sys.path.insert(0, str(sources.root / 'som-core/src'))
        sys.path.insert(0, str((sources.root / tool).parent))
        proof['entry_executed'] = True
        try:
            exec(compile(raw, target.__file__, 'exec', dont_inherit=True), target.__dict__)
            proof['entry_exit_code'] = 0
            return 0
        except SystemExit as result:
            code = result.code if isinstance(result.code, int) else (0 if result.code is None else 1)
            proof['entry_exit_code'] = code
            return code
    except BaseException as error:
        entry_error = error
        proof['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        try:
            require(ctx is not None and sources is not None, 'source/context admission did not complete')
            if legacy_finder is not None:
                require(legacy_finder in sys.meta_path, 'legacy raw source guard changed during checker execution')
            sources.verify()
            ctx.verify()
            proof.update(source_sha256=dict(sorted(sources.pins.items())), source_runtime_tools_inputs_unchanged=True)
        except BaseException as error:
            proof['postcheck_error'] = type(error).__name__ + ': ' + str(error)
            if entry_error is None: raise
        finally:
            try:
                save(work / 'checker-postchecks' / (str(os.getpid()) + '.json'), proof)
            finally:
                if legacy_finder_owned and legacy_finder in sys.meta_path: sys.meta_path.remove(legacy_finder)
                if old_path is not None: sys.path[:] = old_path
                if old_main is not None: sys.modules['__main__'], sys.argv = old_main, old_argv


@contextmanager
def environment(values):
    old = dict(os.environ)
    os.environ.clear()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical(value) + b'\n')


def scoped(ids, shard):
    require(type(shard['count']) is int and type(shard['index']) is int
            and 1 <= shard['count'] <= 32 and 0 <= shard['index'] < shard['count'], 'invalid fixed shard')
    require(len(ids) == len(set(ids)), 'duplicate named cohort slot')
    return [identity for index, identity in enumerate(ids) if index % shard['count'] == shard['index']]


def write_product(root, files):
    root.mkdir(parents=True, exist_ok=False)
    for name, body in files.items():
        from full_product_context import relative
        path = root / relative(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(body.encode())


def unchanged(root, files):
    return all((root / name).read_bytes() == body.encode() for name, body in files.items())


def python_quality(ctx):
    rows = ctx.python_rows()
    ids = [row['cohort'] + '/' + row['id'] for row in rows]
    wanted = set(scoped(ids, ctx.data['shard']))
    results = []
    for item, identity in zip(rows, ids):
        if identity not in wanted:
            continue
        record = {'id': identity, 'passed': False, 'files_map_sha256': item['golden']['files_map_sha256'],
                  'prompt_sha256': digest(canonical(item['prompt'])), 'gates': {}}
        root = ctx.work / 'quality-products' / f'{item["index"]:04d}'
        try:
            write_product(root, item['files'])
            # Parsing is the historical v1-v5 syntax gate. It is never a file
            # parity fallback: the complete file map was already hash-checked.
            for name, body in item['files'].items():
                if name.endswith('.py'):
                    ast.parse(body, filename=name)
            record['gates']['python_syntax'] = {'passed': True}
            if item['prompt']['version'] in {'som.prompt.v6', 'som.prompt.v7'}:
                python = ctx.prepare_python(root, item['files'])
                checker = 'som-core/scripts/verify_python_quality.py'
                ctx.sources.read(checker)
                ctx.command([python, '-B', ctx.repo / SELF, '--execute-tool', checker, '--', str(root),
                             '--evidence', str(ctx.work / 'quality-evidence' / f'{item["index"]:04d}')],
                            root, {**ctx.child_environment(), **ctx.cache['product_environments'][str(root)]['environment']},
                            'strict-own-project-python-quality', timeout=3600)
                record['gates']['strict_five_tool_quality'] = {'passed': True}
            else:
                record['strict_quality_scope'] = 'historical v1-v5 syntax; five-tool template is not applicable'
            record['gates']['all_original_files_unchanged'] = {'passed': unchanged(root, item['files'])}
            record['passed'] = all(gate['passed'] for gate in record['gates'].values())
        except Exception as error:
            record['error'] = str(error)
            record['traceback'] = traceback.format_exc()
        try:
            ctx.release_python(root, record['passed'])
        except Exception as error:
            record['passed'], record['cleanup_error'] = False, str(error)
        results.append(record)
        save(ctx.work / 'rows' / f'{len(results)-1:04d}.json', record)
        print(identity, 'PASS' if record['passed'] else 'FAIL', flush=True)
    return ids, results


def tool(ctx, script, args, label, timeout=20000):
    ctx.sources.read(script)
    return ctx.command([ctx.data['observer_python'], '-B', ctx.repo / SELF, '--execute-tool', script, '--', *args],
                       ctx.work, ctx.child_environment(), label, timeout=timeout, check=False)


def phase1(ctx):
    if ctx.data.get('qa_mode') == 'native-qa-v1':
        controls = _native_controls(ctx.repo, ctx.helper_revision, ctx.candidate_revision)
        require(controls.role_sources['full.phase1.products'] == 'som-rs/som-targets/qa/check_rust_products.py'
                and controls.role_sources['full.hosts'] == 'som-rs/behavior/hosts.py', 'fixed phase1 source roles differ')
        products = controls.load_module('full.phase1.products')
        hosts = controls.load_module('full.hosts')
    else:
        for path in ('som-rs/som-targets/qa', 'som-rs/behavior'):
            sys.path.insert(0, str(ctx.repo / path))
        import check_rust_products as products
        import hosts
    every = products.acceptance_cases()
    ids = [row['id'] for row in every]
    require(len(ids) == 115 and ctx.data['shard'] == {'index': 0, 'count': 1}, 'whole phase1 job required')
    rows = {identity: {'id': identity, 'passed': False, 'gates': {}} for identity in ids}
    for mode in ('python', 'faults', 'parity'):
        output = ctx.work / ('behavior-' + mode)
        command = tool(ctx, 'som-rs/behavior/check_behavior.py', [mode, '--qa', 'recorded-input-transport',
                       '--out', str(output), '--target-dir', str(ctx.work / 'behavior-target'),
                       '--jobs', '4', '--python', ctx.data['observer_python']], 'phase1-' + mode)
        report_path = output / ('report-' + mode + '.json')
        report = json.loads(report_path.read_bytes()) if report_path.is_file() else None
        passed = bool(command['exit_code'] == 0 and report and report['summary'].get('complete') is True
                      and report['summary']['passed'] == report['summary']['total'])
        for row in rows.values():
            row['gates']['whole_' + mode + '_suite'] = {'passed': passed,
                'report_sha256': digest(report_path.read_bytes()) if report_path.is_file() else None}
    cluster = ctx.work / 'phase1-cluster'
    cluster.mkdir()
    ports = hosts.Ports()
    with hosts.cluster(cluster, ports.take()) as pg:
        for case in every:
            row = rows[case['id']]
            try:
                files = ctx.recorded_files(case['prompt'], 'rust', case['id'])
                result = products.product_gates('recorded-input-transport', case, files,
                         ctx.work / 'phase1-products', pg, str(ctx.work / 'phase1-target'))
                row['gates']['full_product_gates'] = result
                product = ctx.work / 'phase1-products' / re.sub('[^a-z0-9]+', '_', case['id'].lower())
                row['gates']['whole_files_unchanged'] = {'passed': unchanged(product, files)}
                python_files = ctx.recorded_files(case['prompt'], 'python')
                test_env = {**ctx.child_environment(), 'PGHOST': pg.split(':')[0], 'PGPORT': pg.split(':')[1],
                            'PGUSER': 'postgres', 'PGDATABASE': 'postgres'}
                if case['id'].startswith('legacy100-'):
                    verifier = 'som-core/scripts/verify_service_v3.py'
                    heldout = 'som-core/data/service-regression-v4/heldout.jsonl'
                    ctx.sources.read(verifier)
                    ctx.sources.read(heldout)
                    saved_files = ctx.work / 'legacy-python-inputs' / (case['id'] + '.json')
                    save(saved_files, {'files': python_files})
                    result = ctx.command([ctx.data['observer_python'], '-B', ctx.repo / SELF,
                              '--execute-tool', verifier, '--', '--cases', ctx.repo / heldout,
                              '--heldout-index', str(int(case['id'].rsplit('-', 1)[1])), '--files-json', saved_files],
                              ctx.work, test_env, 'original-legacy100-python-verifier', timeout=600, check=False)
                    row['gates']['original_legacy100_python_tests_api_and_migrations'] = {
                        'passed': result['exit_code'] == 0, 'command_exit_code': result['exit_code']}
                else:
                    python_root = ctx.work / 'phase1-python-tests' / case['id']
                    write_product(python_root, python_files)
                    interpreter = ctx.prepare_python(python_root, python_files)
                    database = products.fresh_database(pg, 'som_test_python_' + uuid.uuid4().hex[:16])
                    test_env.update(TEST_DATABASE_URL=database.replace('postgresql://', 'postgresql+psycopg://'),
                                    DATABASE_URL=database.replace('postgresql://', 'postgresql+psycopg://'))
                    code, raw = ctx.capture([interpreter, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                                            python_root, test_env, 600)
                    found = re.search(r'Ran (\d+) tests? in ', raw)
                    row['gates']['generated_python_tests'] = {
                        'passed': code == 0 and bool(found and int(found[1]) > 0) and '(skipped=' not in raw,
                        'tests': int(found[1]) if found else None, 'command_exit_code': code}
                    row['gates']['python_test_files_unchanged'] = {'passed': unchanged(python_root, python_files)}
                    ctx.release_python(python_root, row['gates']['generated_python_tests']['passed'])
                row['passed'] = all(gate['passed'] for gate in row['gates'].values())
            except Exception as error:
                row['error'], row['traceback'] = str(error), traceback.format_exc()
            save(ctx.work / 'rows' / (re.sub('[^A-Za-z0-9]+', '_', case['id']) + '.json'), row)
            print(case['id'], 'PASS' if row['passed'] else 'FAIL', flush=True)
    return ids, list(rows.values())


def phase2_python(ctx):
    if ctx.data.get('qa_mode') == 'native-qa-v1':
        controls = _native_controls(ctx.repo, ctx.helper_revision, ctx.candidate_revision)
        require(controls.role_sources['full.phase2'] == 'som-rs/behavior/phase2/check_phase2.py',
                'fixed phase2 source role differs')
        checker = controls.load_module('full.phase2')
    else:
        sys.path.insert(0, str(ctx.repo / 'som-rs/behavior/phase2'))
        import check_phase2 as checker
    every = checker.cases()
    ids = [row['id'] for row in every]
    selected = scoped(ids, ctx.data['shard'])
    output = ctx.work / 'phase2-python'
    command, error = None, None
    try:
        command = tool(ctx, 'som-rs/behavior/phase2/check_phase2.py', ['run', '--language', 'python', '--out', str(output),
                       '--workers', '2', '--only', *selected], 'full-phase2-python-shard')
    except Exception as failure:
        error = {'error': str(failure), 'traceback': traceback.format_exc()}
    suite = sum(row.get('checker') == 'phase1_suite' for row in every if row['id'] in selected)
    worker_count = int(suite > 0) + min(2, len(selected) - suite)
    return ids, phase2_python_results(ctx, ids, selected, output, checker.key, worker_count, command, error)


def phase2_python_results(ctx, ids, selected, output, key, worker_count, command, error=None):
    """Keep every selected row and bind it to the completed shard process."""
    if ctx.data.get('qa_mode') == 'native-qa-v1':
        controls = _native_controls(ctx.repo, ctx.helper_revision, ctx.candidate_revision)
        require(controls.role_sources['runtime.context'] == CONTEXT, 'fixed phase2 result Context source differs')
        read = controls.load_module('runtime.context').read
    else:
        from full_product_context import read
    script = 'som-rs/behavior/phase2/check_phase2.py'
    command_gate = {'passed': bool(error is None and type(command) is dict
                    and type(command.get('exit_code')) is int and command['exit_code'] == 0
                    and command.get('wait_completed') is True
                    and command.get('owned_group_cleanup_passed') is True),
                    'command': command, 'failure': error}
    rows = []
    for identity in selected:
        result = {'id': identity, 'passed': False, 'source_row_sha256': None, 'case_result': None}
        try:
            raw = read(output / 'cases' / (key(identity) + '.json'))
            result['source_row_sha256'] = digest(raw)
            result['case_result'] = json.loads(raw)
            require(result['case_result'].get('id') == identity, 'phase2 named row identity differs')
            require(type(result['case_result'].get('passed')) is bool, 'phase2 row needs an explicit boolean result')
        except Exception as failure:
            result['row_error'] = str(failure)
        rows.append(result)
    summary_gate, postcheck_gate = {'passed': False}, {'passed': False}
    try:
        raw = read(output / 'hosted-shard-summary.json')
        summary_gate['raw_sha256'] = digest(raw)
        summary = json.loads(raw)
        summary_gate['worker_exit_codes'] = summary.get('worker_exit_codes')
        require(summary.get('version') == 'som.full-product-phase2-shard.v1'
                and summary.get('stage') == ctx.data['stage'] == 'phase2-python'
                and summary.get('helper_revision') == ctx.helper_revision
                and summary.get('candidate_revision') == ctx.candidate_revision
                and summary.get('shard') == ctx.data['shard']
                and summary.get('selected_ids') == selected
                and summary.get('whole_inventory_total') == len(ids)
                and summary.get('whole_inventory_sha256') == digest(canonical(ids))
                and summary.get('whole_inventory_complete') is False
                and summary.get('product_acceptance_passed') is False,
                'phase2 hosted shard scope or candidate identity differs')
        names = [key(identity) + '.json' for identity in selected]
        require(len(set(names)) == len(names)
                and {path.name for path in (output / 'cases').glob('*.json')
                     if not path.name.endswith('.outcome.json')} == set(names),
                'phase2 saved named case scope differs')
        expected = [{'id': row['id'], 'passed': bool(row.get('case_result')
                     and row['case_result'].get('passed') is True and 'row_error' not in row),
                     'raw_sha256': row['source_row_sha256']} for row in rows]
        require(summary.get('rows') == expected and summary.get('missing') == []
                and summary.get('shard_complete') is True, 'phase2 summary rows or raw hashes differ')
        codes = summary.get('worker_exit_codes')
        require(type(codes) is list and len(codes) == worker_count
                and all(type(code) is int and code == 0 for code in codes),
                'phase2 worker exits are missing or failed')
        summary_gate['passed'] = True
    except Exception as failure:
        summary_gate['error'] = str(failure)
    try:
        require(type(command) is dict and type(command.get('pid')) is int and command['pid'] > 0,
                'phase2 driver process identity is missing')
        raw = read(ctx.work / 'checker-postchecks' / (str(command['pid']) + '.json'))
        postcheck_gate['raw_sha256'] = digest(raw)
        proof = json.loads(raw)
        require(proof.get('version') == 'som.full-product-checker-postcheck.v1'
                and proof.get('tool') == script and proof.get('pid') == command['pid']
                and proof.get('helper_revision') == ctx.helper_revision
                and proof.get('candidate_revision') == ctx.candidate_revision
                and proof.get('stage') == ctx.data['stage'] and proof.get('shard') == ctx.data['shard']
                and proof.get('source_runtime_tools_inputs_unchanged') is True
                and proof.get('product_acceptance_passed') is False,
                'phase2 checker cleanup or source postcheck proof differs')
        pins = proof.get('source_sha256')
        require(type(pins) is dict and script in pins and SELF in pins and CONTEXT in pins
                and all(digest(ctx.sources.read(name)) == expected for name, expected in pins.items()),
                'phase2 checker postcheck source pins differ')
        postcheck_gate['passed'] = True
    except Exception as failure:
        postcheck_gate['error'] = str(failure)
    for row in rows:
        row['gates'] = {'driver_exit_wait_cleanup': command_gate, 'hosted_shard_summary': summary_gate,
                        'checker_postcheck': postcheck_gate}
        row['passed'] = bool(row.get('case_result') and row['case_result'].get('passed') is True
                             and 'row_error' not in row and all(gate['passed'] for gate in row['gates'].values()))
    return rows


def rust_preparation_without_python(ctx, case, fixture, output):
    """Execute independent Rust commands even when the paired Python input is absent."""
    import check_rust_products as products
    from business_database_checks import Mongo, Postgres
    from verify_related import run
    prompt = case['load_prompt']()
    files = ctx.recorded_files(prompt, 'rust', case['id'])
    root = output / 'unpaired-rust-products' / re.sub('[^A-Za-z0-9]+', '_', case['id'])
    write_product(root, files)
    target = output / 'unpaired-rust-target'
    name = 'som_test_prepare_' + uuid.uuid4().hex[:16]
    backend = (Mongo if prompt.get('capabilities', {}).get('database') == 'mongodb' else Postgres)(prompt, name, fixture)
    from full_product_context import rust_environment
    tools = ctx.rust_tools()
    env = {**os.environ, **ctx.child_environment(), **rust_environment(), **backend.env,
           'CARGO_TARGET_DIR': str(target)}
    backend.create(run)
    record = {'id': case['id'], 'passed': False, 'paired_python_input': 'missing',
              'gates': {}, 'storage_equality': 'unrun_missing_paired_python',
              'paired_behavior': 'unrun_missing_paired_python'}
    try:
        rs = sorted(name for name in files if name.endswith('.rs'))
        commands = [('rustfmt_check', [tools['rustfmt'], '--edition', '2024', '--config-path', '/dev/null', '--check', *rs]),
                    ('cargo_build', [tools['cargo'], 'build', '--locked', '--offline']),
                    ('cargo_clippy', [tools['cargo'], 'clippy', '--locked', '--offline', '--all-targets', '--', '-D', 'warnings']),
                    ('cargo_test_fresh_database', [tools['cargo'], 'test', '--locked', '--offline', '--', '--test-threads=1'])]
        for label, command in commands:
            try:
                code, raw = ctx.capture(command, root, env, 1800)
                result = {'passed': code == 0, 'exit_code': code}
                if label == 'cargo_test_fresh_database':
                    count_code, why = products.test_count(root, raw)
                    result.update(passed=code == count_code == 0, count_problem=why)
                record['gates'][label] = result
            except Exception as error:
                record['gates'][label] = {'passed': False, 'error': str(error)}
        record['gates']['frozen_files_and_lock_unchanged'] = {'passed': unchanged(root, files)}
        record['independent_rust_commands_passed'] = all(row['passed'] for row in record['gates'].values())
    except Exception as error:
        record['error'], record['traceback'] = str(error), traceback.format_exc()
    finally:
        backend.close(run)
    return record


def phase2_rust(ctx):
    if ctx.data.get('qa_mode') == 'native-qa-v1':
        controls = _native_controls(ctx.repo, ctx.helper_revision, ctx.candidate_revision)
        require(controls.role_sources['full.phase2'] == 'som-rs/behavior/phase2/check_phase2.py'
                and controls.role_sources['full.phase2-products'] == 'som-rs/som-targets/qa/check_phase2_products.py'
                and controls.role_sources['full.phase2-host'] == 'som-rs/behavior/phase2/phase2_host.py'
                and controls.role_sources['full.m3-fixture'] == 'som-core/scripts/m3_local_fixture.py'
                and controls.role_sources['full.hosts'] == 'som-rs/behavior/hosts.py', 'fixed phase2 Rust source roles differ')
        phase2 = controls.load_module('full.phase2')
        gates = controls.load_module('full.phase2-products')
        phase2_host = controls.load_module('full.phase2-host')
        M3Fixture = controls.load_module('full.m3-fixture').M3Fixture
        hosts = controls.load_module('full.hosts')
    else:
        for path in ('som-rs/som-targets/qa', 'som-rs/behavior', 'som-rs/behavior/phase2', 'som-core/scripts'):
            sys.path.insert(0, str(ctx.repo / path))
        import check_phase2 as phase2
        import check_phase2_products as gates
        import phase2_host
        from m3_local_fixture import M3Fixture
        import hosts
    every = phase2.cases()
    ids = [row['id'] for row in every]
    selected = set(scoped(ids, ctx.data['shard']))
    output = ctx.work / 'phase2-rust'
    for name in ('cases', 'work', 'fixtures', 'gates', 'gates-work', 'target'):
        (output / name).mkdir(parents=True, exist_ok=False)
    save(output / 'run.json', {'language': 'rust', 'qa': 'recorded-input-transport'})
    rows = []
    # The owning checker supplies this class. Its tests, count check, storage
    # comparison and behavior assertions remain the original source logic.
    phase2_host.RustProduct = gates.gated_product_class()
    cluster = ctx.work / 'phase2-suite-cluster'
    cluster.mkdir()
    ports = hosts.Ports()
    with hosts.cluster(cluster, ports.take()) as pg:
        host = hosts.Host('recorded-input-transport', pg, output / 'suite-products', output / 'suite-target',
                          ctx.data['observer_python'])
        host.ports = ports
        with M3Fixture(output / 'fixtures' / 'owned') as fixture:
            for case in every:
                if case['id'] not in selected:
                    continue
                record = {'id': case['id'], 'passed': False, 'fresh_bundle_checks': 'unrun_no_compiler_carrier'}
                try:
                    if case['phase2_expected']['result'] == 'refusal' and ctx.data.get('qa_mode') == 'native-qa-v1':
                        negative = ctx.current_refusal(case['id'], 'rust')
                        require(negative is not None and negative['product_executed'] is False,
                                'fresh exact named current Rust refusal missing')
                        record.update(passed=True, outcome_role='current-compiler-refusal-no-product',
                                      product_executed=False, current_refusal=negative,
                                      fresh_bundle_checks='source-verified-current-typed-compiler-refusal')
                        save(output / 'cases' / (phase2.key(case['id']) + '.json'), record)
                        rows.append(record)
                        save(output / 'gates' / (phase2.key(case['id']) + '.json'), record)
                        print(case['id'], 'PASS', flush=True)
                        continue
                    if case['phase2_expected']['result'] == 'refusal':
                        raise ValueError('fresh current refusal requires the same-candidate compiler carrier')
                    try:
                        prompt, python_files = phase2.load(case)
                    except Exception as missing:
                        record = rust_preparation_without_python(ctx, case, fixture, output)
                        record['required_paired_input_error'] = str(missing)
                        raise ValueError('paired schema and behavior remain blocked by the required Python input')
                    files = ctx.recorded_files(prompt, 'rust', case['id'])
                    if case['checker'] == 'phase1_suite':
                        product_gates = gates.products.product_gates('recorded-input-transport',
                              {'id': case['id'], 'prompt': prompt}, files, output / 'suite-gate-products', pg,
                              str(output / 'suite-gate-target'))
                        phase2.suite_case(output, case, host, 'rust')
                        record['product_gates'] = product_gates['gates']
                    else:
                        gate = gates.Gate(case, prompt, python_files, fixture,
                                          output / 'gates-work' / phase2.key(case['id']))
                        gates.Gate.current = gate
                        try:
                            phase2.fixture_case(output, case, fixture, 'rust', 'recorded-input-transport', output / 'target')
                        finally:
                            gates.Gate.current = None
                        gate.finish(output / 'work' / phase2.key(case['id']) / 'product', files)
                        record['product_gates'] = gate.gates
                    raw = (output / 'cases' / (phase2.key(case['id']) + '.json')).read_bytes()
                    behavior = json.loads(raw)
                    record['behavior'] = {'passed': behavior['passed'] is True, 'raw_sha256': digest(raw)}
                    record['passed'] = (record['behavior']['passed']
                                        and bool(record['product_gates'])
                                        and all(row['passed'] for row in record['product_gates'].values()))
                except Exception as error:
                    record['error'], record['traceback'] = str(error), traceback.format_exc()
                rows.append(record)
                save(output / 'gates' / (phase2.key(case['id']) + '.json'), record)
                print(case['id'], 'PASS' if record['passed'] else 'FAIL', flush=True)
    return ids, rows


def identity_record(ctx):
    rt = ctx.runtime()
    profile = ctx.sources.read('som-rs/contracts/native-tools-v1/product-rust-tools-v1.json')
    compile_raw = (Path(ctx.data['assembly_root']) / 'manifest.json').read_bytes()
    assembly_raw = (Path(ctx.data['assembly_root']) / 'assembly/manifest.json').read_bytes()
    compiled = json.loads(compile_raw)
    return {'helper_revision': ctx.helper_revision, 'candidate_revision': ctx.candidate_revision,
            'candidate_binary_sha256': compiled['initial_identities']['binary']['sha256'],
            'compile_manifest_sha256': digest(compile_raw), 'assembly_manifest_sha256': digest(assembly_raw),
            'runtime_identity': {key: value for key, value in rt['identity'].items() if key != 'reuse_receipt_sha256'},
            'rust_profile_sha256': digest(profile),
            'wheel_receipt_sha256': digest((Path(ctx.data['wheel_root']) / 'receipt.json').read_bytes()),
            'cargo_receipt_sha256': digest((Path(ctx.data['cargo_root']) / 'receipt.json').read_bytes()),
            'input_transport': ctx.data['transport'], 'qa_mode': ctx.data.get('qa_mode', 'historical'),
            'native_source_admission_sha256': digest(canonical(ctx.data['native_source_authority'])) if ctx.data.get('native_source_authority') else None}


def worker(context_path):
    data = json.loads(context_path.read_bytes())
    module = load_context_module(data['repo'], data['helper_revision'], data.get('qa_mode'), data['candidate_revision'])
    ctx = module.Context(context_path)
    module._ACTIVE = ctx
    if data.get('qa_mode') == 'native-qa-v1': ctx.activate_python_paths(sys.executable)
    if data.get('qa_mode') != 'native-qa-v1': module.install_source_finder(ctx.sources)
    ctx.sources.read(SELF)
    stage = data['stage']
    started = time.monotonic()
    report = {'version': 'som.full-product-preparation-shard.v1', 'stage': stage, 'shard': data['shard'],
              'product_acceptance_passed': False, 'whole_acceptance_complete': False,
              'unrun_acceptance_gates': list(PENDING), 'rows': [], 'shard_complete': False}
    try:
        report['identities'] = identity_record(ctx)
        report['own_runtime_reuse_receipt_sha256'] = ctx.runtime()['identity']['reuse_receipt_sha256']
        with environment(ctx.child_environment()):
            expected, rows = {'python-quality': python_quality, 'phase1': phase1,
                              'phase2-python': phase2_python, 'phase2-rust': phase2_rust}[stage](ctx)
        require([row['id'] for row in rows] == scoped(expected, data['shard']), 'shard case order or named scope differs')
        report.update(ordered_whole_cohort=expected, ordered_whole_cohort_sha256=digest(canonical(expected)), rows=rows,
                      shard_complete=True, all_selected_gates_passed=all(row['passed'] is True for row in rows))
    except BaseException as error:
        report['error'], report['traceback'] = str(error), traceback.format_exc()
    finally:
        try:
            ctx.verify()
            report['source_runtime_tools_unchanged'] = True
        except BaseException as error:
            report['source_runtime_tools_unchanged'] = False
            report['postcheck_error'] = str(error)
        report['elapsed_seconds'] = time.monotonic() - started
        report['bound_source_sha256'] = dict(sorted(ctx.sources.pins.items()))
        save(ctx.work / 'result.json', report)
    return 0 if (report['shard_complete'] and report.get('all_selected_gates_passed')
                 and report['source_runtime_tools_unchanged']) else 1


def m6_callback_input_bindings(args, baselines, protected):
    """Bind explicit locations. The M6 owner still admits their actual data."""
    pairs = getattr(args, 'm6_react_input', None) or []
    gate = getattr(args, 'callback_gate', None)
    if type(gate) is not str or gate != 'm6-react003':
        require(not pairs, 'M6 input locations require the fixed M6 callback gate')
        return {}
    required = {'baseline_profile', 'legacy_run', 'rust_baseline_run', 'qa_profile',
                'api_profile', 'api_closure_tar', 'collector_out', 'audit_out'}
    require(type(pairs) is list and len(pairs) == len(required),
            'all eight explicit M6 callback input locations required')
    values = {}
    for pair in pairs:
        require(type(pair) in {list, tuple} and len(pair) == 2,
                'M6 input binding requires exactly NAME VALUE')
        name, value = pair
        require(type(name) is str and name in required and name not in values,
                'unknown or duplicate M6 callback input name')
        require(type(value) is str and value and '\\' not in value
                and not any(ord(char) < 32 or ord(char) == 127 for char in value),
                'M6 callback input value must be a clean explicit string')
        values[name] = value
    profile = 'som-rs/scripts/hosted_readiness/inputs/recorded-before-profile-v1.json'
    require(values['baseline_profile'] == profile and set(baselines) == {profile},
            'M6 callback requires the exact recorded-before baseline binding')
    inputs = {}
    for name in ('legacy_run', 'rust_baseline_run', 'qa_profile', 'api_profile', 'api_closure_tar'):
        path = Path(values[name])
        require(path.is_absolute() and str(path) == values[name] and '..' not in path.parts
                and not any(part.is_symlink() for part in (path, *path.parents)),
                'M6 callback input path must be physical and absolute')
        mode = path.lstat().st_mode
        require(stat.S_ISDIR(mode) if name in {'legacy_run', 'rust_baseline_run'} else stat.S_ISREG(mode),
                'M6 callback input path has the wrong physical file type')
        inputs[name] = path
    local = args.repo.absolute() / '.local'
    outputs = []
    for name in ('collector_out', 'audit_out'):
        path = Path(values[name])
        require(path.is_absolute() and str(path) == values[name] and '..' not in path.parts
                and local in path.parents and path.parent.is_dir() and not path.exists()
                and not any(part.is_symlink() for part in (path, *path.parents)),
                'M6 callback output must be a new physical repo-local path')
        require(all(path != other and path not in other.parents and other not in path.parents
                    for other in [*protected[1:], *map(Path, baselines.values()), *inputs.values(), *outputs]),
                'M6 callback output overlaps protected input/output or another M6 path')
        outputs.append(path)
    return {'m6_react_inputs': values}


def callback_context_bindings(args, sources, admission, out):
    """Bind existing callback inputs. Their owning gate still admits all data."""
    pairs = getattr(args, 'baseline_input', None) or []
    gate = getattr(args, 'callback_gate', None)
    if type(gate) is str and gate == 'm6-react003':
        m6_pairs = getattr(args, 'm6_react_input', None)
        require(type(m6_pairs) is list and len(m6_pairs) == 8,
                'all eight explicit M6 callback input locations required')
    if not pairs and gate is None:
        return m6_callback_input_bindings(args, {}, [])
    require(args.mode == 'sealed-candidate-products' and getattr(args, 'prepare_context_only', False),
            'callback bindings require sealed prepare-context-only mode')
    require(type(gate) is str and gate in CALLBACK_ADAPTERS,
            'callback gate has no finalized fixed source adapter')
    require(type(pairs) is list and 1 <= len(pairs) <= 32,
            'one to 32 explicit baseline profile/root bindings required')
    names = ('assembly_root', 'wheel_root', 'runtime_root', 'rust_tools_root', 'cargo_root',
             'carrier_root', 'python_inputs_root', 'rust_inputs_root', 'current_companions_root',
             'build_provenance_root')
    protected = [args.repo.absolute(), out, args.artifact_inputs.absolute().parent,
                 *(getattr(args, name).absolute() for name in names)]
    require(all('..' not in path.parts and not any(part.is_symlink() for part in (path, *path.parents))
                for path in protected), 'callback protected input paths must be physical and clean')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    baselines, roots = {}, []
    for pair in pairs:
        require(type(pair) in {list, tuple} and len(pair) == 2,
                'baseline binding requires exactly PROFILE ROOT')
        profile, locator = pair
        require(type(profile) is str and profile.startswith('som-rs/scripts/hosted_readiness/')
                and profile.endswith('.json') and '\\' not in profile
                and all(part not in {'', '.', '..'} for part in profile.split('/'))
                and not any(ord(char) < 32 or ord(char) == 127 for char in profile),
                'baseline profile must be a clean tracked operational JSON source')
        require(profile not in baselines, 'duplicate baseline profile binding')
        # The admitted reader proves tracked source and exact physical Git bytes.
        # JSON shape is not profile/data completeness or product admission.
        raw = sources.read(profile)
        def unique(rows):
            value = {}
            for key, item in rows:
                require(key not in value, 'duplicate operational baseline JSON member')
                value[key] = item
            return value
        document = json.loads(raw, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite operational baseline JSON')))
        require(type(document) is dict, 'operational baseline profile must be a JSON object')
        require(type(locator) is str and locator and '\\' not in locator
                and not any(ord(char) < 32 or ord(char) == 127 for char in locator),
                'baseline root must be an explicit physical absolute path')
        root = Path(locator)
        require(root.is_absolute() and str(root) == locator and '..' not in root.parts and temporary in root.parents
                and not any(part.is_symlink() for part in (root, *root.parents))
                and stat.S_ISDIR(root.lstat().st_mode), 'baseline root must be a physical directory without traversal or links')
        require(all(root != other and root not in other.parents and other not in root.parents
                    for other in [*protected, *roots]), 'baseline root overlaps protected input/output or another baseline')
        baselines[profile] = str(root)
        roots.append(root)
    path = CALLBACK_ADAPTERS[gate]
    require(type(admission) is dict and path in admission.get('admitted_repository_python_sources', []),
            'fixed callback adapter is not in actual native source admission')
    adapter_raw = sources.read(path)
    return {'baseline_inputs': baselines,
            'gate_adapters': {gate: {'path': path, 'sha256': digest(adapter_raw)}},
            **m6_callback_input_bindings(args, baselines, protected)}


def prepare(args):
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux only')
    sealed = args.mode == 'sealed-candidate-products'
    if args.mode not in {'experimental-preparation', 'sealed-candidate-products'}:
        raise ValueError('official acceptance refuses the unsealed policy and requires separate complete independent acceptance')
    module, graph, graph_sources = None, None, None
    if sealed:
        require(args.helper_revision == args.candidate_revision and args.candidate_revision != 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa',
                'sealed products require one new final source candidate')
        require(all(getattr(args, name, None) is not None for name in
                    ('carrier_root', 'artifact_inputs', 'current_companions_root', 'python_inputs_root', 'rust_inputs_root', 'build_provenance_root')),
                'all fresh same-run carrier/input/current-companion paths are required')
        build_root = args.build_provenance_root.absolute()
        require(build_root == args.assembly_root.absolute().parent / 'som-current-build-provenance'
                and build_root.is_dir() and not any(path.is_symlink() for path in (build_root, *build_root.parents)),
                'original same-job build provenance must be the exact assembly artifact sibling')
        graph_path = 'som-rs/scripts/hosted_readiness/same_candidate_graph.py'
        graph_raw = subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(args.repo),
            'cat-file', 'blob', args.helper_revision + ':' + graph_path], stderr=subprocess.DEVNULL)
        require(graph_raw == (args.repo / graph_path).read_bytes(), 'graph source differs from exact helper commit')
        controls = _native_controls(args.repo, args.helper_revision, args.candidate_revision)
        require(controls.role_sources['full.graph'] == graph_path, 'fixed full-product Graph source differs')
        graph = controls.load_module('full.graph')
        # Formal source/HEAD/workflow binding precedes the native policy and Context loaders.
        graph_sources, _, graph_authority = graph._admitted_controller_sources(args.repo, args.helper_revision, args.candidate_revision)
        module = load_context_module(args.repo, args.helper_revision, 'native-qa-v1', args.candidate_revision)
        require(module.__som_native_source_authority__ == graph_authority, 'graph and Context source authority differ')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    out = args.out.absolute()
    require(temporary in out.parents and '..' not in out.parts and not out.exists(), 'new RUNNER_TEMP output required')
    require(args.stage in STAGES, 'named complete product cohort required')
    if args.stage == 'phase1':
        require((args.shard_index, args.shard_count) == (0, 1), 'phase1 keeps the whole 115-case parity report')
    callback_bindings = callback_context_bindings(args, graph_sources,
        module.__som_native_source_authority__ if sealed else None, out)
    out.mkdir(mode=0o700)
    for name in ('home', 'tmp', 'observer'):
        (out / name).mkdir()
    data = {'version': 'som.full-product-context.v1', 'repo': str(args.repo.absolute()),
            'helper_revision': args.helper_revision, 'candidate_revision': args.candidate_revision,
            'work_root': str(out), 'observer_python': str(out / 'observer/.venv/bin/python'),
            'stage': args.stage, 'shard': {'index': args.shard_index, 'count': args.shard_count},
            'transport': 'recorded-assembled-products',
            **{name: str(getattr(args, name).absolute()) for name in
               ('assembly_root', 'wheel_root', 'runtime_root', 'rust_tools_root', 'cargo_root')}}
    if callback_bindings:
        data.update(callback_bindings)
    if sealed:
        data.update(qa_mode='native-qa-v1', native_source_authority=module.__som_native_source_authority__,
                    source_workflow_binding=graph_sources.workflow_binding,
                    artifact_inputs=str(args.artifact_inputs.absolute()),
                    carrier_root=str(args.carrier_root.absolute()),
                    build_provenance_root=str(args.build_provenance_root.absolute()),
                    current_companions_root=str(args.current_companions_root.absolute()),
                    python_inputs_root=str(args.python_inputs_root.absolute()), rust_inputs_root=str(args.rust_inputs_root.absolute()))
        data['current_carrier_restore'] = graph.restore_current_carrier(graph_sources, args.artifact_inputs, args.assembly_root,
            args.carrier_root, out / 'candidate', args.helper_revision, args.candidate_revision)
    path = out / 'context.json'
    save(path, data)
    os.environ['SOM_FULL_PRODUCT_CONTEXT'] = str(path)
    if module is None: module = load_context_module(args.repo, args.helper_revision)
    ctx = module.Context(path)
    module._ACTIVE = ctx
    if data.get('qa_mode') != 'native-qa-v1': module.install_source_finder(ctx.sources)
    ctx.sources.read(SELF)
    ctx.python_rows()
    ctx.rust_rows()
    consumer = ctx.module('cargo_consumer', CARGO)
    consumer.prepare(ctx)
    ctx.prepare_python(out / 'observer', {})
    if getattr(args, 'prepare_context_only', False):
        require(sealed, 'callback context requires active sealed source mode')
        ctx.verify()
        save(out / 'context-prepared.json', {'version': 'som.sealed-callback-context-prepared.v1',
             'helper_revision': args.helper_revision, 'candidate_revision': args.candidate_revision,
             'context_sha256': digest(path.read_bytes()), 'product_acceptance_passed': False,
             'product_rows_run': False})
        return 0
    command = ctx.command([data['observer_python'], '-B', ctx.repo / SELF, '--worker-context', str(path)],
                          out, ctx.child_environment(), 'whole-frozen-product-cohort', timeout=20000, check=False)
    ctx.verify()
    return command['exit_code']


def join(args):
    """Require every named slot once, in contract order, on identical identities."""
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub receipt join only')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(args.out.is_absolute() and temporary in args.out.parents and not args.out.exists(),
            'new join output under RUNNER_TEMP required')
    grouped = {stage: [] for stage in STAGES}
    identities = None
    for path in args.receipts:
        raw = path.read_bytes()
        row = json.loads(raw)
        require(row.get('version') == 'som.full-product-preparation-shard.v1' and row.get('stage') in grouped,
                'unknown full-product shard receipt')
        require(row.get('product_acceptance_passed') is False and row.get('whole_acceptance_complete') is False,
                'preparation receipt makes an acceptance claim')
        require(row.get('shard_complete') is True and row.get('source_runtime_tools_unchanged') is True,
                'partial shard or missing source/tool postcheck')
        current = row['identities']
        require(identities is None or identities == current, 'mixed candidate, helper, tools, runtime or artifact')
        identities = current
        row['receipt_raw_sha256'] = digest(raw)
        grouped[row['stage']].append(row)
    repo = Path(__file__).absolute().parents[3]
    module = load_context_module(repo, identities['helper_revision'], identities.get('qa_mode'), identities['candidate_revision'])
    sources = module.SourceBytes(repo, identities['helper_revision'])
    for shards in grouped.values():
        for receipt in shards:
            pins = receipt['bound_source_sha256']
            require(pins.get(SELF) == digest(sources.read(SELF)) and pins.get(CONTEXT) == digest(sources.read(CONTEXT)),
                    'shard does not bind this exact driver and input/native adapter')
            for name, expected_sha in pins.items():
                require(digest(sources.read(name)) == expected_sha, 'shard helper source binding differs')
    golden_raw = sources.read('som-rs/scripts/hosted_readiness/inputs/public-golden-hashes-v1.json')
    require(digest(golden_raw) == module.GOLDENS_SHA256, 'fixed golden authority changed')
    goldens = json.loads(golden_raw)
    expected_scopes = {
        'python-quality': [name + '/' + row['id'] for name in ('python_public', 'python_legacy')
                           for row in goldens['cohorts'][name]],
        'phase1': [row['id'] for row in goldens['cohorts']['rust_phase1']],
        'phase2-python': [row['id'] for row in goldens['cohorts']['rust_phase2']],
        'phase2-rust': [row['id'] for row in goldens['cohorts']['rust_phase2']]}
    totals, complete, passed = {}, True, True
    for stage, shards in grouped.items():
        require(shards, 'required cohort has no shards: ' + stage)
        count = shards[0]['shard']['count']
        require(len(shards) == count and {row['shard']['index'] for row in shards} == set(range(count))
                and all(row['shard']['count'] == count for row in shards), 'missing or duplicate cohort shard')
        expected = shards[0]['ordered_whole_cohort']
        require(expected == expected_scopes[stage], 'joined IDs differ from the committed frozen authority')
        require(len(expected) == {'python-quality': 671, 'phase1': 115, 'phase2-python': 584, 'phase2-rust': 584}[stage],
                'required full cohort total differs')
        require(all(row['ordered_whole_cohort'] == expected
                    and row['ordered_whole_cohort_sha256'] == digest(canonical(expected)) for row in shards),
                'shards disagree on whole ordered cohort')
        joined = {}
        for row in shards:
            require([r['id'] for r in row['rows']] == scoped(expected, row['shard']), 'shard omits or adds a named slot')
            for result in row['rows']:
                require(result['id'] not in joined, 'cohort slot overlaps two shards')
                joined[result['id']] = result
        require(set(joined) == set(expected), 'joined cohort has missing or extra named rows')
        selected = [joined[identity] for identity in expected]
        good = sum(row['passed'] is True for row in selected)
        totals[stage] = {'total': len(expected), 'run': len(selected), 'passed': good,
                         'failed': [row['id'] for row in selected if row['passed'] is not True], 'rows': selected,
                         'shard_receipt_sha256': [row['receipt_raw_sha256'] for row in sorted(shards, key=lambda r:r['shard']['index'])]}
        passed = passed and good == len(expected)
    sources.verify()
    save(args.out, {'version': 'som.full-product-preparation-join.v1', 'identities': identities,
                    'whole_named_product_cohorts_joined': complete, 'all_preparation_gates_passed': passed,
                    'cohorts': totals, 'product_acceptance_passed': False, 'whole_acceptance_complete': False,
                    'unrun_acceptance_gates': list(PENDING)})
    return 0 if passed else 1


def native_python_bootstrap(arguments):
    """Enter a qualified Python payload after source admission, without site."""
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
            'native Python child requires -I -S -B before any project import')
    path = Path(os.environ['SOM_FULL_PRODUCT_CONTEXT']).absolute()
    data = json.loads(path.read_bytes())
    require(data.get('qa_mode') == 'native-qa-v1', 'native Python bootstrap cannot change historical modes')
    module = load_context_module(data['repo'], data['helper_revision'], 'native-qa-v1', data['candidate_revision'])
    ctx = module.Context(path); module._ACTIVE = ctx
    payload = ctx.activate_python_paths(sys.executable)
    require(arguments, 'Python child target is missing')
    proof = {'version': 'som.native-python-child-postcheck.v1', 'payload': payload,
             'original_python_arguments': list(arguments), 'actual_argv': list(sys.orig_argv),
             'helper_revision': ctx.helper_revision, 'candidate_revision': ctx.candidate_revision,
             'entry_executed': False, 'postchecks_passed': False, 'product_acceptance_passed': False}
    old_main, old_argv, old_path = sys.modules['__main__'], sys.argv, list(sys.path)
    primary = None
    try:
        # Product module lookup and script lookup start only after the payload
        # and repo raw import controls have passed. No site/.pth helper is used.
        sys.path.insert(0, str(Path.cwd()))
        if arguments[0] == '-m':
            require(len(arguments) >= 2 and not arguments[1].startswith('-'), 'Python module name required')
            sys.argv = [arguments[1], *arguments[2:]]
            proof['entry_executed'] = True
            runpy.run_module(arguments[1], run_name='__main__', alter_sys=True)
        else:
            if arguments[0] == '-c':
                require(len(arguments) >= 2, 'Python source argument required')
                raw, filename, rest = arguments[1].encode(), '<string>', arguments[2:]
            else:
                file = Path(arguments[0]).absolute()
                require(file.suffix == '.py' and (ctx.repo in file.parents or ctx.work in file.parents),
                        'Python script must be admitted repository or owned generated source')
                filename, rest = str(file), arguments[1:]
                if ctx.repo in file.parents:
                    name = file.relative_to(ctx.repo).as_posix()
                    require(name in ctx.qa_source_authority['admitted_repository_python_sources'],
                            'Python script is outside native repository source authority')
                    raw = ctx.sources.read(name)
                    controls = _native_controls(ctx.repo, ctx.helper_revision, ctx.candidate_revision)
                    require(name in NATIVE_QA_ENTRIES, 'repository Python script has no fixed native entry role')
                    role = NATIVE_QA_ENTRIES[name]
                    require(controls.role_sources[role] == name, 'fixed repository Python entry source differs')
                    # Product lookup paths are not a QA import search grant.
                    sys.path[:] = old_path
                    proof['entry_executed'] = True
                    code = controls.execute_entry(role, list(rest))
                    proof['exit_code'] = code
                    return code
                else:
                    raw = module.read(file)
                sys.path.insert(0, str(file.parent))
            target = ModuleType('__main__')
            target.__file__, target.__package__, target.__cached__ = filename, '', None
            target.__som_raw_sha256__ = digest(raw)
            sys.modules['__main__'], sys.argv = target, [filename if filename != '<string>' else '-c', *rest]
            proof['entry_executed'] = True
            exec(compile(raw, filename, 'exec', dont_inherit=True), target.__dict__)
        proof['exit_code'] = 0
        return 0
    except SystemExit as result:
        code = result.code if type(result.code) is int else (0 if result.code is None else 1)
        if code != 0: primary = result
        proof['exit_code'] = code
        return code
    except BaseException as error:
        primary = error
        proof['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        try:
            ctx.verify()
            proof['postchecks_passed'] = True
        except BaseException as error:
            proof['postcheck_error'] = type(error).__name__ + ': ' + str(error)
            if primary is None:
                primary = error
                raise
        finally:
            proof['source_sha256'] = dict(sorted(ctx.sources.pins.items()))
            try:
                save(ctx.work / 'python-startup-postchecks' / f'{os.getpid()}.json', proof)
            except BaseException:
                if primary is None: raise
            finally:
                sys.modules['__main__'], sys.argv, sys.path[:] = old_main, old_argv, old_path


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, lambda number, frame: (_ for _ in ()).throw(KeyboardInterrupt('owned runner interrupted')))
    if arguments[:1] == ['--native-python-bootstrap']:
        require(arguments[1:2] == ['--'], 'explicit native Python target separator required')
        return native_python_bootstrap(arguments[2:])
    if arguments[:1] == ['--execute-tool']:
        rest = arguments[1:]
        require(len(rest) >= 2 and rest[1] == '--', 'explicit checker argument separator required')
        return execute_tool([rest[0], *rest[2:]])
    if arguments[:1] == ['--worker-context']:
        require(len(arguments) == 2, 'fixed worker context required')
        return worker(Path(arguments[1]))
    if arguments[:1] == ['--join']:
        parser = argparse.ArgumentParser()
        parser.add_argument('--out', type=Path, required=True)
        parser.add_argument('receipts', type=Path, nargs='+')
        return join(parser.parse_args(arguments[1:]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('experimental-preparation', 'sealed-candidate-products', 'acceptance'), required=True)
    parser.add_argument('--stage', choices=STAGES, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--helper-revision', required=True)
    parser.add_argument('--candidate-revision', required=True)
    parser.add_argument('--prepare-context-only', action='store_true')
    parser.add_argument('--callback-gate', choices=tuple(CALLBACK_ADAPTERS))
    parser.add_argument('--baseline-input', nargs=2, action='append', metavar=('PROFILE', 'ROOT'))
    parser.add_argument('--m6-react-input', nargs=2, action='append', metavar=('NAME', 'VALUE'))
    for name in ('carrier-root', 'artifact-inputs', 'current-companions-root', 'python-inputs-root', 'rust-inputs-root', 'build-provenance-root'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    for name in ('assembly-root', 'wheel-root', 'runtime-root', 'rust-tools-root', 'cargo-root', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parsed = parser.parse_args(arguments)
    if parsed.callback_gate == 'm6-react003' and len(parsed.m6_react_input or []) != 8:
        parser.error('all eight explicit M6 callback input locations required')
    return prepare(parsed)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(1)
