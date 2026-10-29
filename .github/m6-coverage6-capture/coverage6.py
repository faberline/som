#!/usr/bin/env python3
"""Fresh six-case old-Python baseline observations, never product acceptance."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time
import capture as c

EXAMPLES = '4d91b175e9c75cadb61ae335dc1540582b3d89c01815abe651b02d57ac6889ae'
INDEX = 'b49af2a7ba842e5dd810d8d010fcf93436474df7f49f1c569367bed1bc94ed1b'
COVERAGE = 'e8d506220cdd2991ce01e2e6c5f63cb3fe618520b0c4d4d0984dcb44b9d1afd8'
IDS = ('cov-v1-types', 'cov-v2-types', 'cov-v3-types', 'cov-v4-types', 'cov-v5-min', 'cov-v5-max')
WIRES = ('6a053374c2e33d7d7547f8c55c88fbd7e2eb6f7fdae0459a7439e9508f7c9ec8',
         '8c122aa5de3187edbc00dc1d0c9896757fe8d67506e4c481163766291e77ff61',
         'e145d0439af486f12c5175263902ef279f7b4ad452e71e2549d7c8a4d063b370',
         'e6a6c426ded3c58812418a84f1da0c7a171bda9bc2cdfb81cdfcf5d6d1635154',
         '640adcd5a7fe45766de35136efe4ca7815fb5819aa7bf9f7c8acf97a2a9f1d12',
         '7604750e06bf45083508991e0e94ce55bca380ee96ba112b7b641b0f0b0deffb')


def pinned(repo, name, digest):
    raw = c.physical_file(repo, name)
    c.need(c.sha(raw) == digest, 'coverage authority differs')
    return c.parse(raw)


def inputs(repo):
    examples = pinned(repo, 'som-rs/contracts/rust-output-v1/target-examples.json', EXAMPLES)
    index = pinned(repo, 'som-rs/tests/rust-output-v1/cases/index.json', INDEX)
    coverage = pinned(repo, 'som-rs/evidence/rust-output-v1/coverage-cases-python-2026-10-15.json', COVERAGE)
    c.qualify_vectors(c.physical_file(repo, 'som-rs/contracts/compiler-v1/canonical-vectors.json'))
    def unique(rows):
        output = {}
        for row in rows:
            c.need(type(row) is dict and type(row.get('id')) is str and row['id'] not in output, 'duplicate authority ID')
            output[row['id']] = row
        return output
    indexed = unique(index['cases'])
    frozen = unique(coverage['cases'])
    all_examples = unique(examples['examples'])
    c.need(set(indexed) == set(frozen) == set(IDS), 'coverage scope differs')
    rows = []
    for case_id, expected_wire in zip(IDS, WIRES):
        item, proof = indexed[case_id], frozen[case_id]
        example = all_examples[case_id + '-json-rust']
        source = 'som-rs/tests/rust-output-v1/cases/' + case_id + '.prompt.json'
        c.need(item['prompt'] == case_id + '.prompt.json' and example['source'] == source
               and example['target'] == 'rust', 'coverage source differs')
        raw = c.physical_file(repo, source)
        c.need(c.sha(raw) == item['prompt_file_sha256'], 'coverage prompt file differs')
        prompt = c.parse(raw)
        wire_envelope = c.parse(example['body'].encode('utf-8'))
        c.need(type(wire_envelope) is dict and set(wire_envelope) == {'format', 'prompt', 'target'}
               and wire_envelope['format'] == 'som.prompt-json.v1' and wire_envelope['target'] == 'rust'
               and wire_envelope['prompt'] == prompt and c.value_sha(prompt) == proof['prompt_sha256'],
               'coverage prompt differs')
        request = c.compact({'format': wire_envelope['format'], 'prompt': wire_envelope['prompt']})
        c.need(c.sha(request) == expected_wire, 'coverage wire differs')
        files = proof['python_files']
        c.need(type(files) is dict and files and all(type(k) is str and type(v) is str
               and re.fullmatch('[0-9a-f]{64}', v) is not None for k, v in files.items()), 'coverage files invalid')
        rows.append((case_id, prompt, request, files))
    return rows


def execute(directory, prompt, wire, expected, result, request):
    result.update(form='python', status='attempted', expected_status=200)
    try:
        result['compile_request'] = c.save(directory / 'python.compile.request.raw', wire)
        def compile_received(status, raw):
            result['compile_status'] = status
            result['compile_response'] = c.save(directory / 'python.compile.response.raw', raw)
        status, response = request('POST', '/v1/compile', wire, compile_received)
        c.need(status == 200, 'coverage compile status differs')
        doc = c.parse(response)
        c.need(type(doc) is dict and doc.get('status') == 'ok', 'coverage compile result differs')
        bundle = doc.get('bundle')
        c.need(type(bundle) is dict and bundle.get('version') == 'som.dsl-bundle.v1'
               and 'target' not in bundle and c.value_sha(bundle.get('prompt')) == c.value_sha(prompt),
               'coverage bundle differs')
        body = c.compact({'bundle': bundle})
        result['assemble_request'] = c.save(directory / 'python.assemble.request.raw', body)
        def assemble_received(status, raw):
            result['assemble_status'] = status
            result['assemble_response'] = c.save(directory / 'python.assemble.response.raw', raw)
        status, response = request('POST', '/v1/assemble', body, assemble_received)
        c.need(status == 200, 'coverage assemble status differs')
        doc = c.parse(response)
        c.need(type(doc) is dict and doc.get('status') == 'ok', 'coverage assemble result differs')
        files = doc.get('files')
        c.need(type(files) is dict and files and all(type(k) is str and type(v) is str for k, v in files.items()),
               'coverage files invalid')
        result['files_sha256'] = {k: c.sha(v.encode('utf-8', 'strict')) for k, v in sorted(files.items())}
        result['files_map_sha256'] = c.value_sha(files)
        c.need(result['files_sha256'] == expected, 'coverage frozen file map differs')
        result['expected_frozen_files_matched'] = True
        result['status'] = 'captured_product'
    except Exception:
        result['status'] = 'failed'
        raise


def wait_owned_listener(process, probe, observations, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + 30
    while True:
        c.need(process.poll() is None and clock() < deadline, 'baseline startup failed')
        prior_probes = len(observations)
        try:
            probe('start')
            return
        except c.Refusal:
            if len(observations) == prior_probes:
                raise
            last = observations[-1]
            if last.get('exit_code') != 1 or last.get('stdout_sha256') != c.sha(b'') or last.get('stderr_sha256') != c.sha(b''):
                raise
            sleep(0.1)


def guarded_request(method, endpoint, body, probe, receive):
    probe('before:' + endpoint)
    status, raw = c.http(method, endpoint, body)
    receive(status, raw)
    probe('after:' + endpoint)
    return status, raw


def empty_ports(run, observations, phase):
    passed = True
    for port in (c.PORT, 8765, 18765):
        folder = run / f'port-{phase}-{port}'
        folder.mkdir()
        item = {'port': port, 'attempted': True, 'exit_code': None}
        observations[str(port)] = item
        error = None
        try:
            result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', f'-iTCP:{port}', '-sTCP:LISTEN', '-Fp'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
            stdout, stderr = result.stdout, result.stderr
            item['exit_code'] = result.returncode
        except subprocess.TimeoutExpired as exc:
            error = exc
            stdout, stderr = exc.stdout or b'', exc.stderr or b''
        except OSError as exc:
            error = exc
            stdout, stderr = b'', b''
        if error is not None:
            item['error_type'] = type(error).__name__
        item['stdout_raw'] = c.save(folder / 'stdout.raw', stdout)
        item['stderr_raw'] = c.save(folder / 'stderr.raw', stderr)
        if error is not None or item['exit_code'] != 1 or stdout or stderr:
            passed = False
    return passed


def new_summary():
    return {'version': 'som.coverage6.fresh-baseline-capture.v1', 'status': 'unrun',
            'role': 'fresh 2bce baseline observation; not historical wire recovery',
            'product_acceptance_passed': False, 'rows': [{'id': cid, 'status': 'unrun'} for cid in IDS],
            'ports': {'before': {}, 'after': {}}, 'ownership_probes': [],
            'cleanup': {'child_started': False, 'child_stopped': False, 'escalated': False}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    repo = args.root
    out = repo / '.local/m6-coverage6/capture'
    summary = new_summary()
    process = None
    root = None
    stage = 'host'
    try:
        c.need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
               and os.environ.get('RUNNER_OS') == 'macOS' and os.environ.get('GITHUB_REPOSITORY') == 'faberline/som'
               and platform.system() == 'Darwin' and platform.machine() == 'arm64'
               and platform.mac_ver()[0].split('.')[0] == '26', 'GitHub macOS host required')
        c.need(repo.is_absolute() and repo.resolve() == repo and not out.exists() and not out.is_symlink(), 'output path refused')
        for directory in (repo / '.local', out.parent):
            c.need(not directory.is_symlink(), 'output symlink refused')
        out.mkdir(parents=True)
        root = out
        run = out / 'run'
        run.mkdir()
        stage = 'source'
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=10).stdout.decode().strip()
        c.need(re.fullmatch(r'[0-9a-f]{40}', head) is not None and head == os.environ.get('GITHUB_SHA'), 'checkout differs')
        summary['source_commit'] = head
        rows = inputs(repo)
        for i, (_, prompt, wire, _) in enumerate(rows):
            summary['rows'][i].update(prompt_sha256=c.value_sha(prompt), request_sha256=c.sha(wire))
        baseline = repo / '.local/m6-hosted/baseline'
        stage = 'baseline_identity'
        health = c.identity(baseline)
        summary['identity_before'] = c.identity_observation(baseline, run / 'identity-before')
        stage = 'ports'
        c.need(empty_ports(run, summary['ports']['before'], 'before'), 'before listener check failed')
        stage = 'start'
        keep = ('HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'SDKROOT', 'DEVELOPER_DIR')
        environment = {key: os.environ[key] for key in keep if key in os.environ}
        environment['SOM_TS_PRETTIER'] = str(repo / '.local/m6-hosted/prettier')
        with (run / 'baseline.stdout').open('wb') as stdout, (run / 'baseline.stderr').open('wb') as stderr:
            process = subprocess.Popen([str(baseline / 'som'), 'serve', '--assets', str(baseline / 'assets'),
                                        '--port', str(c.PORT)], cwd=baseline, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            summary['child_pid'] = process.pid
            def probe(label):
                c.need(process.poll() is None, 'child exited before ownership probe')
                observation = {'label': label}
                summary['ownership_probes'].append(observation)
                folder = run / f'owned-{len(summary["ownership_probes"]):03d}'
                c.owned_listener(process.pid, folder, observation)
                c.need(process.poll() is None, 'child exited after ownership probe')
            stage = 'listener_start'
            wait_owned_listener(process, probe, summary['ownership_probes'])
            def request(method, endpoint, body, receive):
                return guarded_request(method, endpoint, body, probe, receive)
            stage = 'health'
            health_folder = run / 'health'
            health_folder.mkdir()
            def health_received(status, raw):
                summary['health'] = {'status': status, 'response': c.save(health_folder / 'response.raw', raw)}
            status, raw = request('GET', '/healthz', None, health_received)
            c.need(status == 200 and c.parse(raw) == health, 'baseline health differs')
            stage = 'capture'
            for i, (cid, prompt, wire, files) in enumerate(rows):
                folder = run / cid
                folder.mkdir()
                execute(folder, prompt, wire, files, summary['rows'][i], request)
        stage = 'post_identity'
        c.need(c.identity(baseline) == health, 'baseline changed')
        summary['identity_after'] = c.identity_observation(baseline, run / 'identity-after')
        c.need(inputs(repo) == rows, 'coverage inputs changed')
        after = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, check=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=10).stdout.decode().strip()
        c.need(after == head, 'checkout changed')
        summary['source_commit_after'] = after
        summary['status'] = 'captured'
    except Exception as exc:
        summary['status'] = 'failed'
        summary['failed_stage'] = stage
        summary['failure_type'] = type(exc).__name__
        if isinstance(exc, c.Refusal):
            summary['failure_reason'] = str(exc)
    finally:
        if process is not None:
            try:
                c.stop_child(process, summary['cleanup'])
                if summary['cleanup']['escalated']:
                    summary['status'] = 'failed'
            except (OSError, ValueError, subprocess.SubprocessError):
                summary['status'] = 'failed'
                summary['cleanup']['child_stopped'] = False
        if root is not None:
            try:
                summary['cleanup']['listeners_absent'] = empty_ports(run, summary['ports']['after'], 'after')
                if not summary['cleanup']['listeners_absent']:
                    summary['status'] = 'failed'
            except (OSError, ValueError, subprocess.SubprocessError):
                summary['status'] = 'failed'
                summary['cleanup']['listeners_absent'] = False
            (root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': summary['status'], 'failed_stage': summary.get('failed_stage'),
                      'product_acceptance_passed': False}))
    return 0 if summary['status'] == 'captured' and summary['cleanup'].get('child_stopped') and summary['cleanup'].get('listeners_absent') else 1


if __name__ == '__main__':
    raise SystemExit(main())
