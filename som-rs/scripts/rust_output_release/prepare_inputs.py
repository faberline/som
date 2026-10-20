#!/usr/bin/env python3
"""Pin the #51 and #56 switch inputs and write the prerequisites file.

- Python canaries: the #44 post-switch smoke12 (flow) and legacy100 (legacy)
  prompts, CNL and full replies, copied after their hashes match the #44
  prerequisites (or their #51 pinned copies when the #44 files are gone).
  Generate, compile (CNL and prompt JSON) and assemble replies
  and the CLI stdout come from the current default on 8765 with read-only
  calls, or with --stand-in from a fresh process of the current default's
  command (same binary and assets) on a harness port, so that 8765 gets no
  request. The candidate CLI must print the same bytes.
- Rust canaries (--rust-cases, default example-v1 and example-v5) compiled and
  assembled by the accepted candidate on its QA port: a public example or
  phase 1 case with its CNL, or a phase 2 case (prompt JSON only). The
  assembled files must equal the products of the acceptance run (phase 1
  products/, or phase2/work/ for phase 2 cases).
- With --cargo-home, every Rust canary product also runs on the local
  emulators: phase 1 cases through the check_rust_products.py product gates
  on a fresh QA PostgreSQL cluster, phase 2 cases through check_phase2.py.
- M2 rollback inputs: the #44 legacy default receipt, binary, model manifest
  and blocks.
- A candidate whose health names other assets than the current default (#57,
  rust-output-v1 amendment 004) brings its own asset directory: the Python
  canaries are then pinned twice, from the current default (or its stand-in)
  as previous_python_canaries and from the candidate as python_canaries. Each
  candidate compile reply may differ from the default's only in the admitted
  asset descriptors; generate and assemble give the same bytes. --self-cases
  adds self-name canaries (tests/compiler-v1/self-name-fixtures.json) compiled
  and assembled by the candidate.
- --products-run names the run whose accepted Rust products the Rust canaries
  must equal (default: the acceptance run). An issue57 acceptance run proves
  that the Rust products are unchanged from its rust-output-055 run.

Nothing outside the new output directory is written.

usage: prepare_inputs.py --candidate DIR --acceptance RUN_DIR --audit AUDIT_JSON --out NEW_DIR
                         [--rust-cases ID ...] [--stand-in] [--cargo-home DIR]
                         [--products-run RUN_DIR] [--self-cases ID ...]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_common as rc  # noqa: E402
import rehearse  # noqa: E402

QA = rc.REPO / 'som-rs/som-targets/qa'
sys.path.insert(0, str(QA))
import check_rust_products as products  # type: ignore[import-not-found]  # noqa: E402
import check_targets as targets  # type: ignore[import-not-found]  # noqa: E402
import python_replay  # type: ignore[import-not-found]  # noqa: E402  (imported by check_targets)

SWITCH_001 = rc.REPO / '.local/rust-compiler-goal/acceptance/default-switch-001'
PINNED_051 = rc.REPO / '.local/rust-compiler-goal/acceptance/rust-output-051/inputs-001/inputs/python'
RUST_CASES = ('example-v1', 'example-v5')
PHASE2 = rc.REPO / 'som-rs/behavior/phase2/check_phase2.py'
CLUSTER_GATES = rc.REPO / 'som-rs/scripts/rust_output_acceptance/run_acceptance.py'
SELF_FIXTURES = rc.REPO / 'som-rs/tests/compiler-v1/self-name-fixtures.json'
ISSUE57_RUN = 'som.rust-output-issue57-run.v1'


def binding(path):
    return {'path': str(path), 'sha256': rc.digest(path)}


class Pinner:
    def __init__(self, root):
        self.root = root

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
        return binding(path)

    def http(self, port, name, relative, path, body):
        status, raw = rc.post_raw(port, path, body)
        rc.require(status == 200, f'{name}: HTTP {status} on {port}')
        rc.require(json.loads(raw).get('status') == 'ok', f'{name}: non-ok reply')
        request = self.write(f'{relative}/{name}.request', body)
        response = self.write(f'{relative}/{name}.response', raw)
        return raw, {'kind': 'http', 'name': name, 'path': path, 'request': request, 'response': response}

    def cli(self, binaries, port, name, relative, args, inputs):
        outputs = []
        for label in binaries.values():
            done = subprocess.run(rc.cli_argv(label, {'args': args}, port), capture_output=True, timeout=120)
            rc.require(done.returncode == 0, f'{name}: CLI exit {done.returncode}')
            outputs.append(done.stdout)
        rc.require(len(set(outputs)) == 1, f'{name}: the CLIs print different bytes')
        response = self.write(f'{relative}/{name}.stdout', outputs[0])
        return outputs[0], {'kind': 'cli', 'name': name, 'args': args, 'inputs': inputs,
                            'binaries': sorted(binaries), 'response': response}


def python_case(pin, case, previous, new, port, prefix='python'):
    relative = f'{prefix}/{case["name"]}'
    files = {}
    for name in ('prompt', 'cnl', 'expected_response'):
        source = Path(case[name]['path'])
        if not source.exists():
            source = PINNED_051 / case['name'] / name
        rc.require(rc.digest(source) == case[name]['sha256'], f'{case["name"]} {name} differs from the #44 hash')
        files[name] = pin.root / relative / name
        files[name].parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, files[name])
        rc.require(rc.digest(files[name]) == case[name]['sha256'], f'copy of {name} differs')
    prompt = rc.load(files['prompt'])
    expected_bytes = files['expected_response'].read_bytes()
    expected = json.loads(expected_bytes)
    steps = []
    raw, step = pin.http(port, 'generate', relative, '/v1/generate', rc.canon(prompt).encode())
    rc.require(json.loads(raw) == expected, 'default generate differs from the #44 reply')
    steps.append(step)
    raw, step = pin.http(port, 'compile-cnl', relative, '/v1/compile',
                         rc.canon({'format': 'som.cnl.v1', 'source': files['cnl'].read_text()}).encode())
    compiled = json.loads(raw)
    steps.append(step)
    raw, step = pin.http(port, 'compile-json', relative, '/v1/compile',
                         rc.canon({'format': 'som.prompt-json.v1', 'prompt': prompt}).encode())
    rc.require(rc.exact_json(json.loads(raw), compiled), 'CNL and prompt JSON compile differ')
    steps.append(step)
    bundle = compiled['bundle']
    rc.require(rc.exact_json(bundle['prompt'], prompt), 'compiled prompt differs')
    raw, step = pin.http(port, 'assemble', relative, '/v1/assemble', rc.canon({'bundle': bundle}).encode())
    rc.require(json.loads(raw) == expected, 'default assemble differs from the #44 reply')
    steps.append(step)
    saved = pin.write(f'{relative}/bundle.json', rc.canon(bundle).encode())
    binaries = {'previous': previous, 'new': new}
    stdout, step = pin.cli(binaries, port, 'cli-generate', relative, ['generate', str(files['prompt'])],
                           [binding(files['prompt'])])
    rc.require(stdout == expected_bytes, 'CLI generate differs from the #44 reply bytes')
    steps.append(step)
    stdout, step = pin.cli(binaries, port, 'cli-compile', relative, ['compile', str(files['cnl'])],
                           [binding(files['cnl'])])
    rc.require(rc.exact_json(json.loads(stdout), bundle), 'CLI bundle differs')
    steps.append(step)
    stdout, step = pin.cli(binaries, port, 'cli-assemble', relative, ['assemble', saved['path']], [saved])
    rc.require(json.loads(stdout) == expected, 'CLI assemble differs')
    steps.append(step)
    pinned = {'name': case['name'], 'family': case['family'], 'steps': steps}
    if prefix != 'python':
        return pinned, None
    m2 = {'name': case['name'], 'prompt': binding(files['prompt']),
          'expected_response': binding(files['expected_response'])}
    return pinned, m2


def same_python_canary(previous, new):
    """The candidate's pinned replies of one Python canary against the previous default's."""
    rc.require([s['name'] for s in previous['steps']] == [s['name'] for s in new['steps']], 'canary steps differ')
    for old, step in zip(previous['steps'], new['steps']):
        if step['kind'] == 'http' and step['name'] != 'assemble':
            rc.require(rc.bound(old['request']).read_bytes() == rc.bound(step['request']).read_bytes(),
                       f'{new["name"]} {step["name"]} requests differ')
        before, after = rc.bound(old['response']).read_bytes(), rc.bound(step['response']).read_bytes()
        if step['name'] in ('compile-cnl', 'compile-json', 'cli-compile'):
            if step['name'] == 'cli-compile':
                before, after = (rc.canon({'bundle': json.loads(b)}).encode() for b in (before, after))
            problem = python_replay.asset_only_difference(before, after)
            rc.require(problem is None, f'{new["name"]} {step["name"]}: {problem}')
        else:
            rc.require(before == after, f'{new["name"]} {step["name"]} differs from the previous default')


def self_case(pin, case, port, binaries):
    """A self-name prompt (#57): CNL and prompt JSON compile alike and the bundle assembles."""
    relative = f'python-self/{case["id"]}'
    cnl = pin.write(f'{relative}/source.cnl', case['cnl'].encode())
    prompt = pin.write(f'{relative}/prompt.json', rc.canon(case['prompt']).encode())
    steps = []
    raw, step = pin.http(port, 'compile-cnl', relative, '/v1/compile',
                         rc.canon({'format': 'som.cnl.v1', 'source': case['cnl']}).encode())
    compiled = json.loads(raw)
    steps.append(step)
    raw, step = pin.http(port, 'compile-json', relative, '/v1/compile',
                         rc.canon({'format': 'som.prompt-json.v1', 'prompt': case['prompt']}).encode())
    rc.require(rc.exact_json(json.loads(raw), compiled), f'{case["id"]}: CNL and prompt JSON compile differ')
    steps.append(step)
    bundle = compiled['bundle']
    blocks = {block['id'] for item in bundle['dsl']['files'] for block in item['blocks']}
    rc.require(set(case['python_variants']) <= blocks, f'{case["id"]}: the self variants are not selected')
    raw, step = pin.http(port, 'assemble', relative, '/v1/assemble', rc.canon({'bundle': bundle}).encode())
    files = json.loads(raw)['files']
    steps.append(step)
    saved = pin.write(f'{relative}/bundle.json', rc.canon(bundle).encode())
    stdout, step = pin.cli(binaries, port, 'cli-compile', relative, ['compile', cnl['path']], [cnl])
    rc.require(rc.exact_json(json.loads(stdout), bundle), f'{case["id"]}: CLI bundle differs')
    steps.append(step)
    stdout, step = pin.cli(binaries, port, 'cli-assemble', relative, ['assemble', saved['path']], [saved])
    rc.require(json.loads(stdout) == json.loads(raw), f'{case["id"]}: CLI assemble differs')
    steps.append(step)
    return {'name': case['id'], 'family': 'self_name', 'prompt': prompt, 'prompt_version': case['prompt']['version'],
            'python_variants': case['python_variants'], 'files': len(files), 'steps': steps}


def rust_case(pin, case, port, new):
    relative = f'rust/{case["id"]}'
    cnl = pin.write(f'{relative}/source.cnl', case['cnl'].encode()) if case['cnl'] is not None else None
    steps = []
    raw, step = pin.http(port, 'compile-json', relative, '/v1/compile', products.compile_body(case, 'json').encode())
    compiled = json.loads(raw)
    steps.append(step)
    if cnl is not None:
        raw, step = pin.http(port, 'compile-cnl', relative, '/v1/compile',
                             products.compile_body(case, 'cnl').encode())
        rc.require(rc.exact_json(json.loads(raw), compiled), 'Rust CNL and prompt JSON compile differ')
        steps.append(step)
    bundle = compiled['bundle']
    rc.require(bundle['target'] == 'rust', 'bundle target is not rust')
    raw, step = pin.http(port, 'assemble', relative, '/v1/assemble', targets.dump({'bundle': bundle}).encode())
    files = json.loads(raw)['files']
    steps.append(step)
    for name, content in files.items():
        rc.require((case['accepted'] / name).read_text() == content,
                   f'{case["id"]} {name} differs from the accepted product')
    saved = pin.write(f'{relative}/bundle.json', rc.canon(bundle).encode())
    binaries = {'new': new}
    if cnl is not None:
        stdout, step = pin.cli(binaries, port, 'cli-compile', relative,
                               ['compile', cnl['path'], '--target', 'rust'], [cnl])
        rc.require(rc.exact_json(json.loads(stdout), bundle), 'Rust CLI bundle differs')
        steps.append(step)
    stdout, step = pin.cli(binaries, port, 'cli-assemble', relative, ['assemble', saved['path']], [saved])
    rc.require(json.loads(stdout) == json.loads(raw), 'Rust CLI assemble differs')
    steps.append(step)
    return {'name': case['id'], 'family': 'rust', 'prompt_version': case['prompt']['version'],
            'phase2_id': case['phase2_id'], 'accepted_product': str(case['accepted']), 'files': len(files),
            'steps': steps}


def products_key(case_id):
    return re.sub(r'[^a-z0-9]+', '_', case_id.lower())


def rust_sources(run, ids):
    """Each canary with its prompt, CNL (or None) and the accepted product of the acceptance run."""
    sources = {case['id']: case for case in targets.sources() if not case.get('rejection')}
    sys.path.insert(0, str(PHASE2.parent))
    import check_phase2 as phase2  # type: ignore[import-not-found]
    every = phase2.cases()
    by_id = {case['id']: case for case in every}
    by_prompt = {case['prompt_sha256']: case['id'] for case in every}
    out = []
    for name in ids:
        if name in sources:
            prompt, cnl = sources[name]['prompt'], sources[name]['cnl']
        else:
            rc.require(name in by_id, f'unknown Rust canary {name}')
            prompt, cnl = by_id[name]['load_prompt'](), None
        if targets.rust_profile.check(prompt) is None:
            phase2_id, accepted = None, run / 'products' / products_key(name)
        else:
            phase2_id = by_prompt.get(phase2.qa.sha(prompt))
            rc.require(phase2_id is not None, f'{name} is not a phase 2 case')
            accepted = run / 'phase2/work' / phase2.key(phase2_id) / 'product'
        rc.require(accepted.is_dir(), f'{name}: no accepted product at {accepted}')
        out.append({'id': name, 'prompt': prompt, 'cnl': cnl, 'phase2_id': phase2_id, 'accepted': accepted})
    return out


def emulator_runs(out, cases, port, cargo_home):
    """Run every canary product on the local emulators. Returns the bound results."""
    env = {**os.environ, 'CARGO_HOME': str(cargo_home), 'CARGO_NET_OFFLINE': 'true', 'PYTHONDONTWRITEBYTECODE': '1'}
    qa = f'http://127.0.0.1:{port}'
    root = out / 'emulators'
    root.mkdir()
    runs = {}
    phase1 = [case['id'] for case in cases if case['phase2_id'] is None]
    phase2 = [case['phase2_id'] for case in cases if case['phase2_id'] is not None]
    if phase1:
        report = root / 'phase1-gates.json'
        argv = [sys.executable, str(CLUSTER_GATES), 'cluster-gates', '--qa', qa, '--work', str(root / 'phase1-work'),
                '--target-dir', str(root / 'target'), '--out', str(report), '--only', *phase1]
        with (root / 'phase1-gates.log').open('xb') as log:
            code = subprocess.run(argv, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=rc.REPO).returncode
        summary = rc.load(report)['summary']
        rc.require(summary['failed'] == [] and summary['bundle_checks'] == {'passed': len(phase1), 'total': len(phase1)}
                   and summary['product_gates'] == {'passed': len(phase1), 'total': len(phase1)},
                   f'phase 1 canary gates failed: {summary["failed"]}')
        runs['phase1_product_gates'] = {'ids': phase1, 'argv': argv, 'exit_code': code, 'report': binding(report),
                                        'product_gates': summary['product_gates']}
    if phase2:
        directory = root / 'phase2'
        argv = [sys.executable, str(PHASE2), 'run', '--language', 'rust', '--qa', qa, '--workers', '2',
                '--only', *phase2, '--out', str(directory)]
        with (root / 'phase2.log').open('xb') as log:
            code = subprocess.run(argv, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=rc.REPO).returncode
        summary = rc.load(directory / 'summary.json')
        rc.require(summary['run'] == len(phase2) and summary['passed'] == len(phase2) and summary['failed'] == [],
                   f'phase 2 canary behavior failed: {summary["failed"]}')
        runs['phase2_behavior'] = {'ids': phase2, 'argv': argv, 'exit_code': code,
                                   'summary': binding(directory / 'summary.json'),
                                   'run': summary['run'], 'passed': summary['passed']}
    return runs


def stand_in(out, current, exclude):
    """A fresh process of the current default's command on a harness port, so 8765 gets no request."""
    port = rc.free_harness_port(exclude=exclude)
    (out / 'stand-in').mkdir()
    argv = current['command'][:-1] + [str(port)]
    return rehearse.launch_detached(argv, out / 'stand-in/process.log', port, current['health'])


def default_chain(current, legacy_sha256):
    """The previous-default snapshots from the current default back to the M2 legacy default."""
    chain, receipt = [], current
    while receipt['previous_default_sha256'] != legacy_sha256:
        rc.require(len(chain) < 4, 'the default chain does not reach the legacy default')
        evidence = Path(receipt['evidence'])
        snapshot = next(p for p in sorted(evidence.iterdir()) if p.name.endswith('-previous-default.json'))
        rc.require(rc.digest(snapshot) == receipt['previous_default_sha256'], f'{snapshot} differs')
        chain.append(binding(snapshot))
        receipt = rc.load(snapshot)
    return chain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--acceptance', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--rust-cases', nargs='+', default=list(RUST_CASES))
    parser.add_argument('--stand-in', action='store_true')
    parser.add_argument('--cargo-home', type=Path)
    parser.add_argument('--products-run', type=Path)
    parser.add_argument('--self-cases', nargs='*', default=[])
    args = parser.parse_args()
    candidate, run, audit, out = (p.resolve() for p in (args.candidate, args.acceptance, args.audit, args.out))
    products_run = args.products_run.resolve() if args.products_run else run
    rc.require(not out.exists(), f'preserve existing output: {out}')
    rc.require(len(set(args.rust_cases)) == len(args.rust_cases), 'repeated Rust canary')
    sources = rust_sources(products_run, args.rust_cases)
    fixtures = {case['id']: case for case in rc.load(SELF_FIXTURES)['positive']}
    rc.require(set(args.self_cases) <= set(fixtures), 'unknown self-name case')
    runtime = rc.load(candidate / 'runtime.json')
    port = runtime['port']
    rc.harness_port(port)
    new = candidate / 'som'
    rc.require(rc.digest(new) == runtime['binary_sha256'], 'candidate binary differs from its receipt')
    current = rc.load(rc.REPO / 'som-rs/runtime/default.json')
    previous = Path(current['command'][0])
    rc.require(rc.digest(previous) == current['identity']['binary_sha256'], 'default binary differs')
    switch = rc.load(SWITCH_001 / '0001-prerequisites.json')
    legacy_path = SWITCH_001 / '0002-legacy-default.json'
    chain = default_chain(current, rc.digest(legacy_path))
    legacy = rc.load(legacy_path)
    own_assets = runtime['health']['asset_manifest_sha256'] != current['health']['asset_manifest_sha256']
    rc.require(own_assets or not args.self_cases, 'self-name canaries need a #57 candidate')
    if own_assets:
        rc.assets_match(candidate / 'assets', runtime['health'])
    out.mkdir(parents=True)
    pin = Pinner(out / 'inputs')
    python, m2, previous_python = [], [], []
    if args.stand_in:
        owner = stand_in(out, current, {port})
        source = {'kind': 'stand-in', 'argv': owner['argv'], 'port': owner['port'],
                  'binary_sha256': rc.digest(previous), 'health': current['health'],
                  'log': str(out / 'stand-in/process.log')}
    else:
        owner, source = None, {'kind': 'default', 'port': 8765}
    try:
        for case in switch['canaries']:
            pinned, clone = python_case(pin, case, previous, new, source['port'],
                                        'python-previous' if own_assets else 'python')
            (previous_python if own_assets else python).append(pinned)
            if own_assets:
                pinned, clone = python_case(pin, case, previous, new, port)
                same_python_canary(previous_python[-1], pinned)
                python.append(pinned)
            m2.append(clone)
    finally:
        if owner is not None:
            rc.terminate(rc.identity(owner['pid'], owner['argv'], owner['port']))
    python += [self_case(pin, fixtures[name], port, {'previous': previous, 'new': new}) for name in args.self_cases]
    rust = [rust_case(pin, case, port, new) for case in sources]
    emulators = emulator_runs(out, sources, port, args.cargo_home.resolve()) if args.cargo_home else None
    summary = rc.load(run / 'summary.json')
    if summary.get('version') == ISSUE57_RUN:
        acceptance_assertions = {'/passed': True, '/blockers': [],
                                 '/candidate_after/binary_sha256': runtime['binary_sha256']}
        header_sha256 = rc.digest(run / 'header.json')
    else:
        acceptance_assertions = {'/status': 'passed', '/blockers': [],
                                 '/candidate_after/binary_sha256': runtime['binary_sha256']}
        header_sha256 = summary['header_sha256']
    audit_receipt = rc.load(audit)
    if audit_receipt.get('version') == 'som.rust-output-audit-055.v1':
        audit_assertions = {'/totals': {'failed': 0, 'unrun': 0}, '/run': str(run)}
    else:
        audit_assertions = {'/status': 'passed', '/run': str(run)}
    config = {
        'version': rc.PREREQUISITES_VERSION,
        'repo': str(rc.REPO),
        'base_controller': {'path': str(rc.BASE_PATH), 'sha256': rc.BASE_SHA256},
        'candidate': {'binary': binding(new), 'runtime': binding(candidate / 'runtime.json'),
                      'build': binding(candidate / 'build.json'), 'source': binding(candidate / 'source.json'),
                      **({'assets': {'path': str(candidate / 'assets'),
                                     'manifest_sha256': runtime['health']['asset_manifest_sha256']}}
                         if own_assets else {})},
        'gates': {
            'acceptance': {**binding(run / 'summary.json'), 'assertions': acceptance_assertions},
            'audit': {**binding(audit), 'assertions': audit_assertions},
        },
        'phase1_default': {'receipt': binding(rc.REPO / 'som-rs/runtime/default.json'), 'binary': binding(previous),
                           'previous_defaults': chain},
        'python_canary_source': source,
        'rust_products_run': str(products_run),
        'rust_emulators': emulators,
        'legacy_default': binding(legacy_path),
        'legacy_binary': binding(Path(legacy['command'][0])),
        'legacy_model_manifest': binding(rc.REPO / 'som-rs/models/current/manifest.json'),
        'legacy_blocks': binding(Path(legacy['command'][5])),
        'python_canaries': python,
        **({'previous_python_canaries': previous_python} if own_assets else {}),
        'rust_canaries': rust,
        'm2_canaries': m2,
        'acceptance_summary_header_sha256': header_sha256,
    }
    rc.require(rc.digest(Path(config['legacy_model_manifest']['path'])) == legacy['health']['model_manifest_sha256'],
               'model manifest differs from the legacy health')
    rc.require(config['legacy_blocks']['sha256'] == legacy['health']['blocks_sha256'], 'blocks differ from the legacy health')
    rc.check_prerequisites(config)
    data = (json.dumps(config, indent=1) + '\n').encode()
    (out / 'prerequisites.json').write_bytes(data)
    print(json.dumps({'prerequisites': str(out / 'prerequisites.json'), 'sha256': rc.sha_bytes(data),
                      'python_steps': sum(len(c['steps']) for c in python),
                      'rust_steps': sum(len(c['steps']) for c in rust), 'm2_cases': len(m2)}))


if __name__ == '__main__':
    main()
