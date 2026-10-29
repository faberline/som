#!/usr/bin/env python3
"""Unsealed draft source-bound M6 checker entry; GitHub Linux QA only.

Invoke with the qualified live venv Python and -I -S -B. This entry is stdlib
only until native and React source authorities and live inputs are admitted.
It loads checked Git bytes instead of running a working-tree script with runpy.
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

SELF = 'som-rs/scripts/react_output_acceptance/m6_native_entry.py'
GRAPH = 'som-rs/scripts/hosted_readiness/same_candidate_graph.py'
CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
RETAINED = 'som-rs/scripts/react_output_acceptance/retained_evidence.py'
API_CONTEXT = 'som-rs/scripts/react_output_acceptance/linux_api_context.py'
ROUTER_NAME = '_som_m6_checked_router'
ROUTING_REVIEWED = False


def need(value, message):
    if not value:
        raise ValueError(message)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def read(path, maximum=32 * 1024**2):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and
         not any(p.is_symlink() for p in (path, *path.parents)), 'physical source path required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum,
             'regular bounded source required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        need(len(raw) <= maximum, 'source exceeds limit')
        return raw
    finally:
        os.close(fd)


def save(path, value):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and not path.exists()
         and not any(p.is_symlink() for p in (path, *path.parents)), 'fresh physical evidence path required')
    with path.open('xb') as out:
        out.write(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n')


def git(repo, *args):
    return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo), *args],
                                   stderr=subprocess.DEVNULL, timeout=60)


def bootstrap(repo, revision, candidate, context_path):
    need(sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == 1,
         'isolated no-site no-bytecode interpreter required')
    need(re.fullmatch(r'[0-9a-f]{40}', revision or '') is not None and revision == candidate,
         'same exact helper and candidate required')
    need(git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision,
         'checked HEAD differs')
    entry = git(repo, 'ls-tree', '-z', revision, '--', GRAPH)
    need(entry.startswith((b'100644 blob ', b'100755 blob ')) and entry.count(b'\0') == 1
         and entry.endswith(b'\t' + GRAPH.encode() + b'\0'), 'regular graph source required')
    raw = git(repo, 'cat-file', 'blob', revision + ':' + GRAPH)
    need(raw == read(repo / GRAPH), 'graph bytes differ')
    graph = ModuleType('_som_m6_native_graph')
    graph.__file__, graph.__cached__, graph.__package__ = str(repo / GRAPH), None, ''
    graph.__som_raw_sha256__ = sha(raw)
    exec(compile(raw, graph.__file__, 'exec', dont_inherit=True), graph.__dict__)
    sources, _, authority = graph.admit_source(repo, revision, candidate)
    need(sources.read(GRAPH) == raw, 'source-bound graph differs')
    need(sources.read(SELF) == read(Path(__file__).absolute()), 'entry source differs')
    need(all(path in authority['admitted_repository_python_sources'] for path in
             (SELF, CONTEXT, RETAINED, API_CONTEXT)), 'required entry/bootstrap sources not admitted')
    module = graph.raw_module(sources, 'full_product_context', CONTEXT)
    ctx = module.Context(context_path)
    module._ACTIVE = ctx
    need(ctx.repo == repo and ctx.qa_mode == 'native-qa-v1' and ctx.qa_source_authority == authority
         and ctx.helper_revision == revision == ctx.candidate_revision, 'native context differs')
    return graph, sources, ctx, authority


def admit_live_inputs(graph, sources, repo, profile, before):
    """Bind React receipt and same-run live venv before using site-packages."""
    need('retained_evidence' not in sys.modules and 'linux_api_context' not in sys.modules,
         'preloaded React source refused')
    graph.raw_module(sources, 'retained_evidence', RETAINED)
    api = graph.raw_module(sources, 'linux_api_context', API_CONTEXT)
    live = api.validate_case(profile, repo, before)
    api.recheck_case(live)
    # React entrypoints authenticate their own retained verifier before loading.
    # Remove only the exact modules we just installed so their checks can run.
    need(sys.modules.get('linux_api_context') is api, 'API context module changed')
    del sys.modules['linux_api_context']
    retained = sys.modules.pop('retained_evidence')
    need(getattr(retained, '__file__', None) == str(repo / RETAINED), 'retained module changed')
    need(Path(sys.executable).absolute() == Path(live['python']), 'live interpreter path differs')
    venv = Path(live['python']).parent.parent
    site = venv / 'lib/python3.12/site-packages'
    need(site.is_absolute() and site.is_dir() and not any(p.is_symlink() for p in (site, *site.parents)),
         'physical live site-packages required')
    # No .pth files or sitecustomize are executed. Full venv byte identity is
    # established before/after the run; this case rechecks selected live tools.
    sys.path.append(str(site))
    return live, site, api


class Router:
    def __init__(self, repo, sources, authority):
        self.repo, self.sources, self.authority = repo, sources, authority
        self.executed = []

    def execute(self, script, arguments):
        target = Path(script)
        if not target.is_absolute(): target = self.repo / target
        need(target.is_absolute() and '..' not in target.parts and self.repo in target.parents,
             'repository checker required')
        relative = target.relative_to(self.repo).as_posix()
        need(relative in self.authority['admitted_repository_python_sources'],
             'checker is not in sealed source authority')
        raw = self.sources.read(relative)
        self.executed.append({'path': relative, 'sha256': sha(raw)})
        prior_main, prior_argv, prior_path = sys.modules.get('__main__'), sys.argv, sys.path[:]
        target_module = ModuleType('__main__')
        target_module.__file__, target_module.__cached__, target_module.__package__ = str(target), None, None
        sys.modules['__main__'] = target_module
        sys.argv = [str(target), *arguments]
        sys.path.insert(0, str(target.parent))
        try:
            exec(compile(raw, str(target), 'exec', dont_inherit=True), target_module.__dict__)
            return 0
        except SystemExit as error:
            return error.code if type(error.code) is int else (0 if error.code is None else 1)
        finally:
            sys.argv = prior_argv
            sys.path[:] = prior_path
            if prior_main is None: sys.modules.pop('__main__', None)
            else: sys.modules['__main__'] = prior_main


def run(args):
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and
         os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted' and sys.platform == 'linux',
         'GitHub hosted Linux required')
    repo = Path(args.repo)
    need(repo.is_absolute() and repo.resolve() == repo and not any(p.is_symlink() for p in (repo, *repo.parents)),
         'physical repository required')
    graph, sources, ctx, authority = bootstrap(repo, args.helper_revision, args.candidate_revision, args.product_context)
    live, site, api = admit_live_inputs(graph, sources, repo, args.api_profile, args.full_before)
    need(ROUTING_REVIEWED, 'M6 routing remains unreviewed')
    router = Router(repo, sources, authority)
    need(ROUTER_NAME not in sys.modules, 'preloaded M6 router refused')
    holder = ModuleType(ROUTER_NAME)
    holder.router = router
    holder.qa_mode = 'native-qa-v1'
    holder.context, holder.live = ctx, live
    holder.api_profile, holder.full_before = args.api_profile, args.full_before
    sys.modules[ROUTER_NAME] = holder
    report = {'version': 'som.m6-native-source-entry.v1', 'helper_revision': args.helper_revision,
              'candidate_revision': args.candidate_revision, 'logical_argv': [args.script, *args.arguments],
              'profile_sha256': live['context_sha256'], 'full_before_sha256': live['full_before_sha256'],
              'pid': os.getpid(), 'scope': args.scope, 'interpreter': str(Path(sys.executable).absolute()), 'site_packages': str(site),
              'entry_executed': False, 'entry_exit_code': None, 'source_postcheck': False,
              'product_acceptance_passed': False}
    code = 1
    try:
        report['entry_executed'] = True
        code = router.execute(args.script, args.arguments)
        report['entry_exit_code'] = code
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        code = 1
    finally:
        try:
            sources.verify()
            if args.scope == 'step':
                ctx.verify()
            else:
                ctx.sources.verify()
                need(read(ctx.path) == ctx.raw, 'product context changed')
                retained = sys.modules.get('retained_evidence')
                if retained is None:
                    retained = graph.raw_module(sources, 'retained_evidence', RETAINED)
                need(getattr(retained, '__file__', None) == str(repo / RETAINED)
                     and callable(getattr(retained, 'sealed_files', None))
                     and sources.read(RETAINED) == read(repo / RETAINED), 'retained verifier changed')
                api.recheck_case(live)
            report['source_postcheck'] = True
        except BaseException as error:
            report['postcheck_error'] = type(error).__name__ + ': ' + str(error)
            code = 1
        report['executed'] = router.executed
        report['source_sha256'] = dict(sorted({**sources.pins, **ctx.sources.pins}.items()))
        save(args.postcheck, report)
        sys.modules.pop(ROUTER_NAME, None)
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'product-context', 'api-profile', 'full-before', 'postcheck'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('helper-revision', 'candidate-revision', 'script'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--scope', choices=('step', 'case'), required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.arguments[:1] == ['--']: args.arguments = args.arguments[1:]
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
