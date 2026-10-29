#!/usr/bin/env python3
"""Draft: run M6 collector and audit against one source-bound candidate.

This is not acceptance. The caller must supply the live owned services, inputs,
and checked transport. Result verification is intentionally unavailable until
launcher cleanup and cross-job restored-run identities have defined verifiers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import ModuleType

SELF = 'som-rs/scripts/react_output_acceptance/m6_react_callback.py'
GRAPH = 'som-rs/scripts/hosted_readiness/same_candidate_graph.py'
CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
COLLECTOR = 'som-rs/scripts/react_output_acceptance/run_acceptance.py'
AUDITOR = 'som-rs/scripts/react_output_acceptance/audit/audit130.py'
GATE = 'm6-react003'
# Fail closed until the graph supplies reviewed source, owner and restored-run joins.
GRAPH_INTEGRATION_REVIEWED = False
HEX = re.compile(r'[0-9a-f]{40}\Z')


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def physical(path, *, exists=True):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts and
            not any(p.is_symlink() for p in (path, *path.parents)), 'absolute physical path required')
    if exists:
        require(path.exists() and path.resolve() == path, 'physical path missing')
    return path


def read(path, maximum=128 * 1024**2):
    path = physical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum,
                'bounded single-link file required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        require(len(raw) <= maximum, 'file too large')
        return raw
    finally:
        os.close(fd)


def save(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'linked callback evidence refused')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha(read(path))
    return result


def git(repo, *args):
    return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo), *args],
                                   stderr=subprocess.DEVNULL, timeout=60)


def admit(repo, revision, candidate, context_path):
    require(HEX.fullmatch(revision or '') is not None and revision == candidate,
            'same full source and candidate revision required')
    require(git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision,
            'checkout HEAD differs')
    entry = git(repo, 'ls-tree', '-z', revision, '--', GRAPH)
    require(entry.startswith((b'100644 blob ', b'100755 blob ')) and
            entry.endswith(b'\t' + GRAPH.encode() + b'\0') and entry.count(b'\0') == 1,
            'regular graph Git source required')
    raw = git(repo, 'cat-file', 'blob', revision + ':' + GRAPH)
    require(raw == read(repo / GRAPH), 'graph source differs from Git')
    name = '_som_m6_callback_graph'
    require(name not in sys.modules, 'preloaded graph refused')
    graph = ModuleType(name)
    graph.__file__, graph.__cached__, graph.__package__ = str(repo / GRAPH), None, ''
    graph.__som_raw_sha256__ = sha(raw)
    sys.modules[name] = graph
    exec(compile(raw, graph.__file__, 'exec', dont_inherit=True), graph.__dict__)
    sources, _, authority = graph.admit_source(repo, revision, candidate)
    require(sources.read(SELF) == read(Path(__file__).absolute()), 'callback source differs from Git')
    module = graph.raw_module(sources, 'full_product_context', CONTEXT)
    ctx = module.Context(context_path)
    module._ACTIVE = ctx
    require(ctx.repo == repo and ctx.helper_revision == revision and ctx.candidate_revision == candidate
            and ctx.qa_mode == 'native-qa-v1' and ctx.qa_source_authority == authority,
            'context and source authority differ')
    return graph, sources, authority, module, ctx


def command_evidence(ctx, out, name, command):
    """Copy actual raw streams, including partial output after command failure."""
    record = dict(command)
    row = {'id': name, 'command': record, 'streams_complete': False, 'postcheck': None}
    streams = 0
    for kind in ('stdout', 'stderr'):
        if kind not in command:
            continue
        data = read(ctx.work / command[kind])
        require(sha(data) == command[kind + '_sha256'], 'command log changed')
        relative = 'logs/' + name + '.' + kind
        save(out / relative, data)
        row[kind] = relative
        streams += 1
    row['streams_complete'] = streams == 2
    if type(command.get('pid')) is int:
        path = ctx.work / 'checker-postchecks' / (str(command['pid']) + '.json')
        if path.is_file():
            post = read(path)
            relative = 'logs/' + name + '.postcheck.json'
            save(out / relative, post)
            row.update(postcheck=relative, postcheck_sha256=sha(post))
    return row


def run_command(ctx, module, repo, env, out, report, label, script, arguments, timeout):
    """Keep recorded failure data even if Context.command raises on timeout."""
    prior = len(ctx.cache.get('commands', []))
    try:
        command = ctx.command(module.tool_command(repo / script, arguments), repo, env,
                              'm6-react-' + label, timeout=timeout, check=False)
    except BaseException:
        rows = ctx.cache.get('commands', [])
        if len(rows) == prior + 1:
            report['rows'].append(command_evidence(ctx, out, label, rows[-1]))
        raise
    return command_evidence(ctx, out, label, command)


def validate_args(args, ctx, graph):
    repo = physical(args.repo)
    require(ctx.repo == repo and args.qa_mode == 'native-qa-v1', 'repository or mode differs')
    require(ctx.data.get('assembly_root') == str(physical(args.assembly_root)), 'assembly root differs')
    require(ctx.data.get('candidate_revision') == args.candidate_revision, 'candidate differs')
    # The graph's carrier restore and current assembly proof remain independent inputs.
    physical(args.carrier_root)
    compile_proof = graph.verify_compile_proof(args.assembly_root, args.candidate_revision)
    candidate = physical(args.candidate_dir)
    build = json.loads(read(candidate / 'build.json'))
    runtime = json.loads(read(candidate / 'runtime.json'))
    binary = read(candidate / 'som')
    require(build['tree_before']['head'] == args.candidate_revision and build['complete'] is True
            and build['binary_sha256'] == runtime['binary_sha256'] == sha(binary)
            and sha(binary) == compile_proof['binary_identity']['sha256']
            and runtime['port'] == args.port == 18978, 'candidate build, binary or port differs')
    require(args.baseline == 'http://127.0.0.1:18979', 'owned baseline port differs')
    for item in (args.legacy_run, args.rust_baseline_run, args.qa_profile, args.api_profile, args.api_closure_tar):
        physical(item)
    # Collector/auditor Reader currently requires this run to be under the repo.
    collector_out = physical(args.collector_out, exists=False)
    require(repo / '.local' in collector_out.parents and not collector_out.exists(),
            'fresh repo-local collector output required')
    out = physical(args.out, exists=False)
    temporary = physical(Path(os.environ['RUNNER_TEMP']))
    require(temporary in out.parents and not out.exists() and out.parent.is_dir(),
            'fresh RUNNER_TEMP callback output required')
    require(ctx.data.get('runtime_root') is not None, 'qualified runtime context missing')
    return compile_proof


def run(args):
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and
            os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted' and
            os.environ.get('RUNNER_OS') == 'Linux' and sys.platform == 'linux',
            'GitHub-hosted Linux runner required')
    repo = physical(args.repo)
    physical(args.product_context)
    graph, sources, authority, module, ctx = admit(repo, args.helper_revision, args.candidate_revision,
                                                   args.product_context)
    proof = validate_args(args, ctx, graph)
    require(GRAPH_INTEGRATION_REVIEWED, 'M6 graph integration is unreviewed; product run not admitted')
    out = args.out
    out.mkdir(mode=0o700)
    report = {'version': 'som.m6-react-graph-callback.v1', 'gate': GATE,
              'helper_revision': args.helper_revision, 'candidate_revision': args.candidate_revision,
              'source_admission': authority, 'compile_proof': proof,
              'collector_out': str(args.collector_out), 'rows': [],
              'product_acceptance_passed': False, 'complete': False,
              'all_required_passed': False, 'owned_waits_complete': False,
              'owned_cleanup_complete': False, 'raw_logs_complete': False,
              'remaining': ['source-bound candidate and baseline owner/cleanup join',
                            'sealed restored-run mapping and independent verifier',
                            'collector child source routing and reviewed React003 authority']}
    env = ctx.child_environment()
    for key in ('RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_ARCH', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
        if key in os.environ:
            env[key] = os.environ[key]
    env.pop('UV_NO_INSTALLER_METADATA', None)
    api_profile = json.loads(read(args.api_profile, 1024 * 1024))
    venv = physical(Path(api_profile['venv']))
    python = venv / 'bin/python'
    require(python.is_file(), 'live venv interpreter missing')
    collect_args = ['--candidate', str(args.candidate_dir), '--out', str(args.collector_out),
                    '--baseline', args.baseline, '--legacy-run', str(args.legacy_run),
                    '--rust-baseline-run', str(args.rust_baseline_run), '--python', str(python),
                    '--protected-default', 'absent', '--cleanup-products-runtime', '--record-raw-http',
                    '--qa-profile', str(args.qa_profile), '--api-profile', str(args.api_profile)]
    audit_out = repo / '.local' / ('m6-react-audit-' + out.name + '.json')
    physical(audit_out, exists=False)
    require(not audit_out.exists(), 'fresh audit output required')
    try:
        report['rows'].append(run_command(ctx, module, repo, env, out, report, 'collector', COLLECTOR,
                                          collect_args, timeout=18000))
        ctx.verify(); sources.verify()
        if args.collector_out.is_dir():
            audit_args = ['--repo', str(repo), '--run', str(args.collector_out), '--out', str(audit_out),
                          '--api-closure-tar', str(args.api_closure_tar)]
            report['rows'].append(run_command(ctx, module, repo, env, out, report, 'audit', AUDITOR,
                                              audit_args, timeout=7200))
            if audit_out.is_file():
                audit_raw = read(audit_out)
                save(out / 'audit.json', audit_raw)
                report['audit_sha256'] = sha(audit_raw)
            ctx.verify(); sources.verify()
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        report['bound_source_sha256'] = dict(sorted({**sources.pins, **ctx.sources.pins}.items()))
        report['saved_files_sha256'] = inventory(out)
        save(out / 'result.json', canonical(report) + b'\n')
    # Even a successful collector and auditor do not verify service cleanup or
    # prove that a restored raw run is the same run. The graph must provide both.
    return 1


def verify_result(root, source_reader, helper_revision, candidate_revision, gate=GATE, *, repo, proof_roots):
    """Fail closed until graph-owned service cleanup and sealed run join exist."""
    raise ValueError('M6 callback independent graph verifier is not implemented')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'assembly-root', 'carrier-root', 'product-context', 'out', 'candidate-dir',
                 'legacy-run', 'rust-baseline-run', 'qa-profile', 'api-profile', 'api-closure-tar',
                 'collector-out'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('helper-revision', 'candidate-revision', 'qa-mode', 'baseline'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--port', type=int, required=True)
    return run(parser.parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
