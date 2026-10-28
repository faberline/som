#!/usr/bin/env python3
"""Independent read-only check of the default named by ROOT/som-rs/runtime/default.json.

It sends no signal and writes nothing outside its fresh --output. It checks:

- the process on PORT is the receipt identity (uid, argv, start, listener,
  loaded executable, cwd) and its binary has the expected sha256;
- /healthz equals the receipt health exactly; for the candidate it also equals
  the candidate receipt health, lists the python and rust targets and reports
  the rustfmt hash of the pinned toolchain file;
- runtime/current and compiler-assets/current name the served binary and
  assets, and the assets match their manifest;
- on the repository: the legacy M2 binary, model and block pointers are kept;
- every pinned Python canary of the expected default (the candidate set, or the
  previous-default set when a #57 candidate brings its own assets), and the Rust
  canaries when the default is the candidate, byte for byte over HTTP and the
  default's own CLI.

usage: check_default.py --prerequisites FILE --root DIR --port N --expect-binary-sha256 SHA --output NEW_DIR
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_common as rc  # noqa: E402

LEGACY_POINTERS = {'som-rs/models/current': {'kind': 'symlink', 'target': 'flow-m2-candidate/smoke-r31-001'},
                   'som-code-python/data/blocks/current': {'kind': 'symlink', 'target': 'flow-v7-compact'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--prerequisites', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--expect-binary-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run = rc.Release(output, str(rc.REPO))
    checks = {}
    started = time.time()

    def check(name, function):
        try:
            result = function()
            checks[name] = {'passed': True, **({'detail': result} if result is not None else {})}
        except Exception as error:  # every check runs and is recorded
            checks[name] = {'passed': False, 'error': repr(error)}

    config = rc.load(args.prerequisites)
    runtime, _ = rc.check_prerequisites(config)
    rc.apply_formatter_env(config)
    receipt = rc.load(root / 'som-rs/runtime/default.json')
    command = receipt['command']
    candidate = args.expect_binary_sha256 == config['candidate']['binary']['sha256']
    compiler = rc.compiler_profile(config)
    expected = rc.expected_runtime(config, args.expect_binary_sha256) if compiler else runtime

    def owner():
        rc.require(rc.digest(command[0]) == args.expect_binary_sha256, 'default binary is not the expected one')
        rc.require(command[-2:] == ['--port', str(args.port)], 'receipt port differs')
        rc.require(rc.identity(receipt['pid'], command, args.port) == receipt['identity'], 'owner differs')
        rc.assert_current(config, receipt, candidate=candidate)
        return {'pid': receipt['pid'], 'start': receipt['identity']['start']}

    def health():
        actual = run.health(args.port, receipt['health'])
        if compiler or candidate:
            rc.require(rc.exact_json(actual, expected['health']), 'health differs from the pinned compiler receipt')
            targets = expected['health']['targets'] if compiler else ['python', 'rust']
            rc.require(actual['targets'] == targets, 'targets differ')
        if candidate:
            toolchain = rc.load(rc.REPO / 'som-rs/contracts/rust-output-v1/toolchain.json')
            rustfmt = Path(toolchain['rust']['bin_dir']) / 'rustfmt'
            rc.require(rc.digest(rustfmt) == actual['rust_target']['formatter_sha256'], 'rustfmt hash differs')
        return {'targets': actual.get('targets', ['python'])}

    def pointers():
        rc.require(rc.link_state(root / 'som-rs/runtime/current')
                   == {'kind': 'symlink', 'target': str(Path(command[0]).parent)}, 'runtime/current differs')
        rc.require(rc.link_state(root / 'som-rs/compiler-assets/current')
                   == {'kind': 'symlink', 'target': command[3]}, 'compiler-assets/current differs')
        rc.assets_match(Path(command[3]), receipt['health'])

    def legacy():
        if compiler:
            return 'compiler-only profile: previous compiler inputs are hash-bound'
        if root != rc.REPO:
            return 'sandbox root: the M2 pointers live in the repository'
        for relative, state in LEGACY_POINTERS.items():
            rc.require(rc.link_state(root / relative) == state, f'{relative} changed')
        for name in ('legacy_binary', 'legacy_model_manifest', 'legacy_blocks'):
            rc.bound(config[name])
        return None

    def canaries():
        cases = rc.candidate_cases(config) if candidate else rc.previous_cases(config)
        run.canary(args.port, {'previous': Path(command[0]), 'new': Path(command[0])}, cases, 'post-check')
        return {'cases': [c['name'] for c in cases]}

    rollback_check = 'previous_compiler_inputs' if compiler else 'legacy_rollback_assets'
    for name, function in [('owner', owner), ('health', health), ('pointers', pointers),
                           (rollback_check, legacy), ('canaries', canaries)]:
        check(name, function)
    # The owner must be the same process after the canaries.
    check('owner_after', owner)
    result = {'version': 'som.rust-output-default-check.v1', 'root': str(root), 'port': args.port,
              'expect_binary_sha256': args.expect_binary_sha256, 'candidate': candidate,
              **({'profile': 'compiler-only'} if compiler else {}),
              'script_sha256': rc.digest(__file__), 'common_sha256': rc.digest(rc.__file__),
              'prerequisites_sha256': rc.receipt_sha(config), 'default_sha256': rc.digest(root / 'som-rs/runtime/default.json'),
              'started_at': started, 'ended_at': time.time(), 'checks': checks,
              'passed': all(c['passed'] for c in checks.values())}
    (output / 'result.json').write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps({'passed': result['passed'], 'failed': [k for k, v in checks.items() if not v['passed']]}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
