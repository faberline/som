"""Run the unchanged named Rust/TS receipt commands under sealed native QA."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import ModuleType

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'run_same_candidate_contracts.py'
GRAPH = BASE + 'same_candidate_graph.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
CASES = [('rust', 'som-rs/tests/rust-output-v1/check_contract.py',
          ['--receipt', 'som-rs/evidence/rust-output-contract-amendment-008.json', '--host']),
         ('typescript', 'som-rs/tests/typescript-output-v1/check_contract.py',
          ['--receipt', 'som-rs/evidence/typescript-output-contract-amendment-004.json'])]


def require(value, message):
    if not value: raise ValueError(message)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def read(path):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'linked callback data refused')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 128 * 1024**2, 'bounded regular callback data required')
    return path.read_bytes()


def save(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream: stream.write(raw)


def inventory(root):
    return {path.relative_to(root).as_posix(): sha(read(path)) for path in root.rglob('*') if path.is_file()}


def verify_result(root, source_reader, helper_revision, candidate_revision, gate='named-contract-acceptance', *, repo, proof_roots):
    root = Path(root)
    data = json.loads(read(root / 'context.json'))
    require(set(proof_roots) == {'assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root'}
            and data['version'] == 'som.full-product-context.v1' and data['qa_mode'] == 'native-qa-v1'
            and data['helper_revision'] == helper_revision == data['candidate_revision'] == candidate_revision
            and data['repo'] == str(repo)
            and all(data[name] == str(value) for name, value in proof_roots.items()),
            'named receipt Context differs from graph-chosen source and proof roots')
    report = json.loads(read(root / 'result.json'))
    require(gate == report['gate'] == 'named-contract-acceptance' and helper_revision == candidate_revision
            and report['helper_revision'] == helper_revision and report['candidate_revision'] == candidate_revision
            and report['product_acceptance_passed'] is False, 'named receipt callback identity differs')
    require(all(report.get(key) is True for key in ('complete', 'all_required_passed', 'owned_waits_complete',
            'owned_cleanup_complete', 'raw_logs_complete')), 'named receipt callback is partial or failed')
    observed = inventory(root); observed.pop('result.json')
    require(observed == report['saved_files_sha256'], 'named receipt raw file universe differs')
    require([row['id'] for row in report['rows']] == [row[0] for row in CASES], 'named receipt scope or order differs')
    require(SELF in report['bound_source_sha256'] and all(sha(source_reader(name)) == pin
            for name, pin in report['bound_source_sha256'].items()), 'named receipt source changed')
    for row, (identity, script, arguments) in zip(report['rows'], CASES):
        command = row['command']
        raw_command = json.loads(read(root / row['command_record']))
        require(raw_command == command, 'named receipt command differs from its original raw record')
        require(row['passed'] is True and command['exit_code'] == 0 and command['wait_completed'] is True
                and command['owned_group_cleanup_passed'] is True, 'named receipt process did not pass')
        expected = [data['observer_python'], '-B', str(Path(repo) / (BASE + 'run_full_products.py')),
                    '--execute-tool', script, '--', *arguments, '--qa-mode', 'native-qa-v1']
        require(command['argv'] == expected and command['cwd'] == str(repo),
                'original named receipt complete command or native mode differs')
        for kind in ('stdout', 'stderr'):
            require(sha(read(root / row[kind])) == command[kind + '_sha256'], 'named receipt raw stream differs')
        post = json.loads(read(root / row['postcheck']))
        require(post['version'] == 'som.full-product-checker-postcheck.v1'
                and post['tool'] == script and post['helper_revision'] == helper_revision
                and post['candidate_revision'] == candidate_revision and post['pid'] == command['pid']
                and post['source_runtime_tools_inputs_unchanged'] is True and post['entry_executed'] is True
                and post['entry_exit_code'] == 0 and post['product_acceptance_passed'] is False
                and {script, BASE + 'run_full_products.py', CONTEXT} <= set(post['source_sha256'])
                and all(sha(source_reader(name)) == pin for name, pin in post['source_sha256'].items()),
                'named receipt entry or source/runtime postcheck differs')
    return report


def main():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux callback only')
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'product-context', 'out'): parser.add_argument('--' + name, type=Path, required=True)
    for name in ('helper-revision', 'candidate-revision'): parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    data = json.loads(read(args.product_context))
    require(data.get('qa_mode') == 'native-qa-v1' and data['helper_revision'] == args.helper_revision
            and data['candidate_revision'] == args.candidate_revision == args.helper_revision, 'sealed current context required')
    graph_raw = subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(args.repo),
        'cat-file', 'blob', args.helper_revision + ':' + GRAPH], stderr=subprocess.DEVNULL)
    require(graph_raw == read(args.repo / GRAPH), 'graph bootstrap differs from current Git')
    graph = ModuleType('_som_named_contract_source_graph')
    graph.__file__, graph.__cached__, graph.__package__ = str(args.repo / GRAPH), None, ''
    graph.__som_raw_sha256__ = sha(graph_raw)
    exec(compile(graph_raw, graph.__file__, 'exec', dont_inherit=True), graph.__dict__)
    sources, _, authority = graph.admit_source(args.repo, args.helper_revision, args.candidate_revision)
    sources.read(SELF)
    module = graph.raw_module(sources, 'full_product_context', CONTEXT)
    ctx = module.Context(args.product_context); module._ACTIVE = ctx
    require(ctx.qa_source_authority == authority, 'context and initial native authority differ')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    out = args.out.absolute()
    require(temporary in out.parents and not out.exists(), 'new callback output required')
    out.mkdir(mode=0o700)
    save(out / 'context.json', read(args.product_context))
    report = {'version': 'som.same-candidate-named-contracts.v1', 'gate': 'named-contract-acceptance',
              'helper_revision': args.helper_revision, 'candidate_revision': args.candidate_revision,
              'source_admission': authority, 'rows': [], 'product_acceptance_passed': False,
              'complete': False, 'all_required_passed': False, 'owned_waits_complete': False,
              'owned_cleanup_complete': False, 'raw_logs_complete': False}
    try:
        for index, (identity, script, arguments) in enumerate(CASES):
            command = ctx.command(module.tool_command(ctx.repo / script, arguments), ctx.repo, ctx.child_environment(),
                                  'named-receipt-' + identity, timeout=3600, check=False)
            row = {'id': identity, 'command': command, 'passed': command['exit_code'] == 0}
            original_command = Path(command['stdout']).with_suffix('.json')
            row['command_record'] = 'rows/' + str(index) + '.command.json'
            save(out / row['command_record'], read(ctx.work / original_command))
            for kind in ('stdout', 'stderr'):
                path = 'rows/' + str(index) + '.' + kind
                save(out / path, read(ctx.work / command[kind])); row[kind] = path
            post = 'rows/' + str(index) + '.postcheck.json'
            save(out / post, read(ctx.work / 'checker-postchecks' / (str(command['pid']) + '.json')))
            row['postcheck'] = post; report['rows'].append(row)
            require(row['passed'], 'original named receipt checker failed: ' + identity)
        ctx.verify(); sources.verify()
        report.update(complete=True, all_required_passed=True, owned_waits_complete=True,
                      owned_cleanup_complete=True, raw_logs_complete=True)
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        # Retain complete original process records and first failures, including commands
        # that raised before returning a row to this wrapper.
        for directory in ('environment-commands', 'checker-postchecks', 'product-environments'):
            source_root = ctx.work / directory
            if source_root.exists():
                for path in sorted(source_root.rglob('*')):
                    require(not path.is_symlink(), 'linked named receipt evidence refused')
                    if path.is_file(): save(out / 'original-context-evidence' / directory / path.relative_to(source_root), read(path))
        report['bound_source_sha256'] = dict(sources.pins)
        report['saved_files_sha256'] = inventory(out)
        save(out / 'result.json', graph.canonical(report) + b'\n')
    proof_roots = {name: Path(data[name]) for name in ('assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root')}
    verify_result(out, sources.read, args.helper_revision, args.candidate_revision, repo=args.repo, proof_roots=proof_roots)
    return 0


if __name__ == '__main__': raise SystemExit(main())
