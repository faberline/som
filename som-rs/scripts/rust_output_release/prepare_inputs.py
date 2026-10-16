#!/usr/bin/env python3
"""Pin the #51 switch inputs and write the prerequisites file.

- Python canaries: the #44 post-switch smoke12 (flow) and legacy100 (legacy)
  prompts, CNL and full replies, copied after their hashes match the #44
  prerequisites. Generate, compile (CNL and prompt JSON) and assemble replies
  and the CLI stdout come from the current default on 8765 with read-only
  calls. The candidate CLI must print the same bytes against 8765.
- Rust canaries: example-v1 and example-v5 compiled and assembled by the
  accepted candidate on its QA port. The assembled files must equal the
  products of the acceptance run.
- M2 rollback inputs: the #44 legacy default receipt, binary, model manifest
  and blocks.

Nothing outside the new output directory is written.

usage: prepare_inputs.py --candidate DIR --acceptance RUN_DIR --audit AUDIT_JSON --out NEW_DIR
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_common as rc  # noqa: E402

QA = rc.REPO / 'som-rs/som-targets/qa'
sys.path.insert(0, str(QA))
import check_rust_products as products  # type: ignore[import-not-found]  # noqa: E402
import check_targets as targets  # type: ignore[import-not-found]  # noqa: E402

SWITCH_001 = rc.REPO / '.local/rust-compiler-goal/acceptance/default-switch-001'
RUST_CASES = ('example-v1', 'example-v5')


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


def python_case(pin, case, previous, new):
    relative = f'python/{case["name"]}'
    files = {}
    for name in ('prompt', 'cnl', 'expected_response'):
        source = rc.bound(case[name])
        files[name] = pin.root / relative / name
        files[name].parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, files[name])
        rc.require(rc.digest(files[name]) == case[name]['sha256'], f'copy of {name} differs')
    prompt = rc.load(files['prompt'])
    expected_bytes = files['expected_response'].read_bytes()
    expected = json.loads(expected_bytes)
    steps = []
    raw, step = pin.http(8765, 'generate', relative, '/v1/generate', rc.canon(prompt).encode())
    rc.require(json.loads(raw) == expected, 'default generate differs from the #44 reply')
    steps.append(step)
    raw, step = pin.http(8765, 'compile-cnl', relative, '/v1/compile',
                         rc.canon({'format': 'som.cnl.v1', 'source': files['cnl'].read_text()}).encode())
    compiled = json.loads(raw)
    steps.append(step)
    raw, step = pin.http(8765, 'compile-json', relative, '/v1/compile',
                         rc.canon({'format': 'som.prompt-json.v1', 'prompt': prompt}).encode())
    rc.require(rc.exact_json(json.loads(raw), compiled), 'CNL and prompt JSON compile differ')
    steps.append(step)
    bundle = compiled['bundle']
    rc.require(rc.exact_json(bundle['prompt'], prompt), 'compiled prompt differs')
    raw, step = pin.http(8765, 'assemble', relative, '/v1/assemble', rc.canon({'bundle': bundle}).encode())
    rc.require(json.loads(raw) == expected, 'default assemble differs from the #44 reply')
    steps.append(step)
    saved = pin.write(f'{relative}/bundle.json', rc.canon(bundle).encode())
    binaries = {'previous': previous, 'new': new}
    stdout, step = pin.cli(binaries, 8765, 'cli-generate', relative, ['generate', str(files['prompt'])],
                           [binding(files['prompt'])])
    rc.require(stdout == expected_bytes, 'CLI generate differs from the #44 reply bytes')
    steps.append(step)
    stdout, step = pin.cli(binaries, 8765, 'cli-compile', relative, ['compile', str(files['cnl'])],
                           [binding(files['cnl'])])
    rc.require(rc.exact_json(json.loads(stdout), bundle), 'CLI bundle differs')
    steps.append(step)
    stdout, step = pin.cli(binaries, 8765, 'cli-assemble', relative, ['assemble', saved['path']], [saved])
    rc.require(json.loads(stdout) == expected, 'CLI assemble differs')
    steps.append(step)
    pinned = {'name': case['name'], 'family': case['family'], 'steps': steps}
    m2 = {'name': case['name'], 'prompt': binding(files['prompt']),
          'expected_response': binding(files['expected_response'])}
    return pinned, m2


def rust_case(pin, case, port, new, run):
    relative = f'rust/{case["id"]}'
    cnl = pin.write(f'{relative}/source.cnl', case['cnl'].encode())
    steps = []
    raw, step = pin.http(port, 'compile-json', relative, '/v1/compile', products.compile_body(case, 'json').encode())
    compiled = json.loads(raw)
    steps.append(step)
    raw, step = pin.http(port, 'compile-cnl', relative, '/v1/compile', products.compile_body(case, 'cnl').encode())
    rc.require(rc.exact_json(json.loads(raw), compiled), 'Rust CNL and prompt JSON compile differ')
    steps.append(step)
    bundle = compiled['bundle']
    rc.require(bundle['target'] == 'rust', 'bundle target is not rust')
    raw, step = pin.http(port, 'assemble', relative, '/v1/assemble', targets.dump({'bundle': bundle}).encode())
    files = json.loads(raw)['files']
    steps.append(step)
    accepted = run / 'products' / products_key(case['id'])
    for name, content in files.items():
        rc.require((accepted / name).read_text() == content, f'{case["id"]} {name} differs from the accepted product')
    saved = pin.write(f'{relative}/bundle.json', rc.canon(bundle).encode())
    binaries = {'new': new}
    stdout, step = pin.cli(binaries, port, 'cli-compile', relative,
                           ['compile', cnl['path'], '--target', 'rust'], [cnl])
    rc.require(rc.exact_json(json.loads(stdout), bundle), 'Rust CLI bundle differs')
    steps.append(step)
    stdout, step = pin.cli(binaries, port, 'cli-assemble', relative, ['assemble', saved['path']], [saved])
    rc.require(json.loads(stdout) == json.loads(raw), 'Rust CLI assemble differs')
    steps.append(step)
    return {'name': case['id'], 'family': 'rust', 'files': len(files), 'steps': steps}


def products_key(case_id):
    return re.sub(r'[^a-z0-9]+', '_', case_id.lower())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--acceptance', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    candidate, run, audit, out = (p.resolve() for p in (args.candidate, args.acceptance, args.audit, args.out))
    rc.require(not out.exists(), f'preserve existing output: {out}')
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
    rc.require(rc.digest(legacy_path) == current['previous_default_sha256'], 'legacy default snapshot differs')
    legacy = rc.load(legacy_path)
    out.mkdir(parents=True)
    pin = Pinner(out / 'inputs')
    python, m2 = [], []
    for case in switch['canaries']:
        pinned, clone = python_case(pin, case, previous, new)
        python.append(pinned)
        m2.append(clone)
    cases = {case['id']: case for case in products.acceptance_cases()}
    rust = [rust_case(pin, cases[name], port, new, run) for name in RUST_CASES]
    summary = rc.load(run / 'summary.json')
    config = {
        'version': rc.PREREQUISITES_VERSION,
        'repo': str(rc.REPO),
        'base_controller': {'path': str(rc.BASE_PATH), 'sha256': rc.BASE_SHA256},
        'candidate': {'binary': binding(new), 'runtime': binding(candidate / 'runtime.json'),
                      'build': binding(candidate / 'build.json'), 'source': binding(candidate / 'source.json')},
        'gates': {
            'acceptance': {**binding(run / 'summary.json'),
                           'assertions': {'/status': 'passed', '/blockers': [],
                                          '/candidate_after/binary_sha256': runtime['binary_sha256']}},
            'audit': {**binding(audit), 'assertions': {'/status': 'passed', '/run': str(run)}},
        },
        'legacy_default': binding(legacy_path),
        'legacy_binary': binding(Path(legacy['command'][0])),
        'legacy_model_manifest': binding(rc.REPO / 'som-rs/models/current/manifest.json'),
        'legacy_blocks': binding(Path(legacy['command'][5])),
        'python_canaries': python,
        'rust_canaries': rust,
        'm2_canaries': m2,
        'acceptance_summary_header_sha256': summary['header_sha256'],
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
