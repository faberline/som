"""Pure synthetic six-case evidence for the GitHub-only audit fixture."""
import json
from pathlib import Path
import audit_coverage as a


def make(root, real_vectors, link_name=None):
    repo, capture = root / 'repo', root / 'capture'
    repo.mkdir()
    capture.mkdir()
    (capture / 'run').mkdir()
    authority = {}
    def repo_file(name, raw):
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        authority[name] = a.sha(raw)
    def record(name, raw):
        p = capture / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if name == link_name:
            target = repo / '_synthetic_link_target'
            target.write_bytes(raw)
            p.symlink_to(target)
        else:
            p.write_bytes(raw)
        return {'path': name, 'sha256': a.sha(raw), 'bytes': len(raw)}
    def plain(name, raw):
        p = capture / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    vectors = 'som-rs/contracts/compiler-v1/canonical-vectors.json'
    repo_file(vectors, real_vectors)
    examples, indexes, frozen, rows, wires = [], [], [], [], []
    for cid in a.IDS:
        prompt = {'version': 'som.prompt.v1', 'name': cid}
        path = f'som-rs/tests/rust-output-v1/cases/{cid}.prompt.json'
        prompt_raw = a.compact(prompt)
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(prompt_raw)
        source = {'format': 'som.prompt-json.v1', 'prompt': prompt, 'target': 'rust'}
        wire = a.compact({'format': 'som.prompt-json.v1', 'prompt': prompt})
        wire_sha = a.sha(wire)
        wires.append(wire_sha)
        examples.append({'id': cid + '-json-rust', 'source': path, 'target': 'rust', 'body': a.compact(source).decode()})
        indexes.append({'id': cid, 'prompt': cid + '.prompt.json', 'prompt_file_sha256': a.sha(prompt_raw)})
        prompt_sha = a.sha(a.canonical(prompt))
        files = {'fixture.py': cid + '\n'}
        hashes = {name: a.sha(value.encode()) for name, value in files.items()}
        frozen.append({'id': cid, 'prompt_sha256': prompt_sha, 'python_files': hashes})
        bundle = {'version': 'som.dsl-bundle.v1', 'prompt': prompt}
        prefix = f'run/{cid}/python'
        rows.append({'id': cid, 'status': 'captured_product', 'expected_frozen_files_matched': True,
                     'expected_status': 200, 'compile_status': 200, 'assemble_status': 200,
                     'prompt_sha256': prompt_sha, 'request_sha256': wire_sha,
                     'compile_request': record(prefix + '.compile.request.raw', wire),
                     'compile_response': record(prefix + '.compile.response.raw', a.compact({'status': 'ok', 'bundle': bundle})),
                     'assemble_request': record(prefix + '.assemble.request.raw', a.compact({'bundle': bundle})),
                     'assemble_response': record(prefix + '.assemble.response.raw', a.compact({'status': 'ok', 'files': files})),
                     'files_sha256': hashes, 'files_map_sha256': a.sha(a.canonical(files))})
    for name, value in (
        ('som-rs/contracts/rust-output-v1/target-examples.json', {'examples': examples}),
        ('som-rs/tests/rust-output-v1/cases/index.json', {'cases': indexes}),
        ('som-rs/evidence/rust-output-v1/coverage-cases-python-2026-10-15.json', {'cases': frozen}),
    ):
        repo_file(name, a.compact(value))
    pid = 42
    labels = ['start', 'before:/healthz', 'after:/healthz'] + [label for _ in a.IDS for label in
              ('before:/v1/compile', 'after:/v1/compile', 'before:/v1/assemble', 'after:/v1/assemble')]
    probes = []
    for i, label in enumerate(labels, 1):
        prefix = f'run/owned-{i:03d}'
        out, err = b'p42\nf6\n', b''
        probes.append({'label': label, 'port': 18917, 'pid': pid, 'attempted': True, 'exit_code': 0,
                       'stdout': out.decode(), 'stderr': '', 'stdout_sha256': a.sha(out), 'stderr_sha256': a.sha(err),
                       'stdout_raw': record(prefix + '/stdout.raw', out), 'stderr_raw': record(prefix + '/stderr.raw', err)})
    ports = {'before': {}, 'after': {}}
    for phase in ports:
        for port in (18917, 8765, 18765):
            prefix = f'run/port-{phase}-{port}'
            ports[phase][str(port)] = {'port': port, 'attempted': True, 'exit_code': 1,
                                       'stdout_raw': record(prefix + '/stdout.raw', b''),
                                       'stderr_raw': record(prefix + '/stderr.raw', b'')}
        for port in (8765, 18765):
            for suffix, value in (('rc', b'1\n'), ('out', b''), ('err', b'')):
                plain(f'run/guards/{phase}-{port}.{suffix}', value)
    health = {'status': 'ok'}
    build = {'complete': True, 'binary_sha256': a.BASELINE,
             'tree_before': {'head': a.SOURCE, 'porcelain': ''}, 'tree_after': {'head': a.SOURCE, 'porcelain': ''}}
    runtime = {'binary_sha256': a.BASELINE, 'health': health}
    source = {'commit': a.SOURCE}
    receipt = {'build.json': a.compact(build), 'runtime.json': a.compact(runtime),
               'source.json': a.compact(source), 'assets/manifest.json': b'{}'}
    pins = {name: a.sha(data) for name, data in receipt.items()}
    pins['som'] = a.BASELINE
    identity = {}
    for phase in ('before', 'after'):
        identity[phase] = {}
        for name, digest in pins.items():
            data = receipt.get(name)
            item = {'sha256': digest, 'bytes': len(data) if data is not None else 123}
            if data is not None:
                item['raw'] = record(f'run/identity-{phase}/' + name.replace('/', '_'), data)
            identity[phase][name] = item
    plain('run/baseline.stdout', b'')
    plain('run/baseline.stderr', b'')
    summary = {'version': 'som.coverage6.fresh-baseline-capture.v1', 'status': 'captured',
               'role': 'fresh 2bce baseline observation; not historical wire recovery', 'product_acceptance_passed': False,
               'source_commit': a.HEAD, 'source_commit_after': a.HEAD, 'rows': rows,
               'child_pid': pid, 'ownership_probes': probes, 'ports': ports,
               'identity_before': identity['before'], 'identity_after': identity['after'],
               'health': {'status': 200, 'response': record('run/health/response.raw', a.compact(health))},
               'cleanup': {'pid': pid, 'child_started': True, 'child_stopped': True, 'listeners_absent': True,
                           'escalated': False, 'terminate_attempted': True, 'terminate_sent': True,
                           'kill_attempted': False, 'kill_sent': False, 'wait_completed': True,
                           'wait_returncode': -15, 'post_wait_poll': -15}}
    (capture / 'summary.json').write_bytes(a.compact(summary))
    return repo, capture, authority, pins, tuple(wires), summary
