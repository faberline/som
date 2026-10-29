#!/usr/bin/env python3
"""Independent raw-data audit of fresh coverage6 baseline, not acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

HEAD = '129188f90499a0ca499e4d7e78e48b094483e250'
BASELINE = '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
SOURCE = '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af'
IDS = ('cov-v1-types', 'cov-v2-types', 'cov-v3-types', 'cov-v4-types', 'cov-v5-min', 'cov-v5-max')
WIRES = ('6a053374c2e33d7d7547f8c55c88fbd7e2eb6f7fdae0459a7439e9508f7c9ec8',
         '8c122aa5de3187edbc00dc1d0c9896757fe8d67506e4c481163766291e77ff61',
         'e145d0439af486f12c5175263902ef279f7b4ad452e71e2549d7c8a4d063b370',
         'e6a6c426ded3c58812418a84f1da0c7a171bda9bc2cdfb81cdfcf5d6d1635154',
         '640adcd5a7fe45766de35136efe4ca7815fb5819aa7bf9f7c8acf97a2a9f1d12',
         '7604750e06bf45083508991e0e94ce55bca380ee96ba112b7b641b0f0b0deffb')
PINS = {
    'som': BASELINE,
    'build.json': '0918984e7c9c00c5299d62d38209658385b960f996d84bf0514b52c04c528d73',
    'runtime.json': '7c4ba46a498bb0f601bd67ff77f99dfe75e4ee1669d6af3c53f3b338a550b970',
    'source.json': 'd40578d8845fff8bfeff627924aa501e56f3973eddb09c2d871e25adec2bbbe5',
    'assets/blocks.json': 'c7269e89d5f7b8fb2b36703cc2ccb6ef8201eccfc62fbffe526714fbd570311e',
    'assets/legacy-blocks.json': '6dd5359cc320cb804f87f3d16fa5899b5ae801f37c970e3b9ac4fced0cc0170f',
    'assets/quality-blocks.json': '0bfbf2a452a559a2661ac5c0c8355d0dfb9ce21cc310e505f121a51bb63e85cc',
    'assets/quality.json': '6ef259440b0965ed97c86a6a46bca743c1a2adf8ec9c1365c82e84dcbd67bd21',
    'assets/ruff': 'cb0cdeba051c5c5bdc85c22dc31440ac1ea17651366bf188d4df1f8fe985933a',
    'assets/pyproject.toml': '4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4',
    'assets/manifest.json': 'c406d03c0edd98ab703a616c1c2be5d81f001966ac9c84280edce5765f91a138',
}
AUTHORITY = {
    'som-rs/contracts/rust-output-v1/target-examples.json': '4d91b175e9c75cadb61ae335dc1540582b3d89c01815abe651b02d57ac6889ae',
    'som-rs/tests/rust-output-v1/cases/index.json': 'b49af2a7ba842e5dd810d8d010fcf93436474df7f49f1c569367bed1bc94ed1b',
    'som-rs/evidence/rust-output-v1/coverage-cases-python-2026-10-15.json': 'e8d506220cdd2991ce01e2e6c5f63cb3fe618520b0c4d4d0984dcb44b9d1afd8',
    'som-rs/contracts/compiler-v1/canonical-vectors.json': 'dd09b41f49702f70098574f638e10ea33646366a77dfa486539f324d6f9ff83f',
}

class AuditError(Exception):
    pass

def need(ok):
    if not ok:
        raise AuditError('evidence differs')

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result)
        result[key] = value
    return result

def parse(raw):
    need(type(raw) is bytes and len(raw) <= 32 * 1024 * 1024)
    try:
        return json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(AuditError('nonfinite JSON')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AuditError('invalid JSON') from exc

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')

def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')

def physical(root, relative):
    need(type(relative) is str and relative and '\\' not in relative and '\x00' not in relative)
    pieces = relative.split('/')
    need(not PurePosixPath(relative).is_absolute() and all(x not in ('', '.', '..') for x in pieces))
    p = root
    for item in pieces:
        p = p / item
        need(not p.is_symlink())
    need(p.is_file() and p.stat().st_size <= 32 * 1024 * 1024)
    return p.read_bytes()

def saved(root, rec, expected):
    need(type(rec) is dict and rec.get('path') == expected)
    raw = physical(root, expected)
    need(type(rec.get('bytes')) is int and rec['bytes'] == len(raw) and rec.get('sha256') == sha(raw))
    return raw

def unique(items):
    result = {}
    need(type(items) is list)
    for item in items:
        need(type(item) is dict and type(item.get('id')) is str and item['id'] not in result)
        result[item['id']] = item
    return result

def check_vectors(doc):
    need(type(doc.get('vectors')) is list and len(doc['vectors']) == 11)
    for v in doc['vectors']:
        expected = v['canonical'].encode('utf-8')
        need(canonical(parse(v['input_json'].encode('utf-8'))) == expected and sha(expected) == v['sha256'])

def check_files(files, expected):
    need(type(files) is dict and files and all(type(k) is str and type(v) is str for k, v in files.items()))
    for name in files:
        need(name and '\\' not in name and '\x00' not in name and not PurePosixPath(name).is_absolute()
             and all(part not in ('', '.', '..') for part in name.split('/')))
    observed = {k: sha(v.encode('utf-8', 'strict')) for k, v in files.items()}
    need(observed == expected)
    return observed

def lsof_owned(raw, pid):
    if type(raw) is not bytes or type(pid) is not int or pid <= 0:
        return False
    rows = raw.split(b'\n')
    return (len(rows) >= 3 and rows[-1] == b'' and rows[0] == f'p{pid}'.encode('ascii')
            and all(re.fullmatch(rb'f(?:0|[1-9][0-9]*)', x) for x in rows[1:-1])
            and len(set(rows[1:-1])) == len(rows[1:-1]))

def check_inventory(root, probe_count):
    need(type(probe_count) is int and probe_count >= 1 and not root.is_symlink())
    need({p.name for p in root.iterdir()} == {'summary.json', 'run'})
    summary = root / 'summary.json'
    run = root / 'run'
    need(not summary.is_symlink() and summary.is_file() and not run.is_symlink() and run.is_dir())
    expected = {'baseline.stdout', 'baseline.stderr', 'guards', 'health', 'identity-before', 'identity-after'}
    expected.update(IDS)
    expected.update(f'owned-{i:03d}' for i in range(1, probe_count + 1))
    expected.update(f'port-{phase}-{port}' for phase in ('before', 'after') for port in (18917, 8765, 18765))
    need({p.name for p in run.iterdir()} == expected)
    for name in ('baseline.stdout', 'baseline.stderr'):
        p = run / name
        need(not p.is_symlink() and p.is_file())
    directories = {cid: {'python.compile.request.raw', 'python.compile.response.raw',
                         'python.assemble.request.raw', 'python.assemble.response.raw'} for cid in IDS}
    directories.update({f'owned-{i:03d}': {'stdout.raw', 'stderr.raw'} for i in range(1, probe_count + 1)})
    directories.update({f'port-{phase}-{port}': {'stdout.raw', 'stderr.raw'}
                        for phase in ('before', 'after') for port in (18917, 8765, 18765)})
    directories.update({'health': {'response.raw'},
                        'identity-before': {'build.json', 'runtime.json', 'source.json', 'assets_manifest.json'},
                        'identity-after': {'build.json', 'runtime.json', 'source.json', 'assets_manifest.json'},
                        'guards': {f'{phase}-{port}.{suffix}' for phase in ('before', 'after')
                                   for port in (8765, 18765) for suffix in ('rc', 'out', 'err')}})
    for name, expected_files in directories.items():
        folder = run / name
        need(not folder.is_symlink() and folder.is_dir() and {p.name for p in folder.iterdir()} == expected_files)
        for child in folder.iterdir():
            need(not child.is_symlink() and child.is_file())


def audit(repo, root):
    sources = {}
    for path, digest in AUTHORITY.items():
        raw = physical(repo, path)
        need(sha(raw) == digest)
        sources[path] = parse(raw)
    check_vectors(sources['som-rs/contracts/compiler-v1/canonical-vectors.json'])
    examples = unique(sources['som-rs/contracts/rust-output-v1/target-examples.json']['examples'])
    index = unique(sources['som-rs/tests/rust-output-v1/cases/index.json']['cases'])
    frozen = unique(sources['som-rs/evidence/rust-output-v1/coverage-cases-python-2026-10-15.json']['cases'])
    need(set(index) == set(frozen) == set(IDS))
    summary = parse(physical(root, 'summary.json'))
    need(summary.get('version') == 'som.coverage6.fresh-baseline-capture.v1' and summary.get('status') == 'captured'
         and summary.get('role') == 'fresh 2bce baseline observation; not historical wire recovery'
         and summary.get('product_acceptance_passed') is False
         and summary.get('source_commit') == HEAD == summary.get('source_commit_after'))
    rows = unique(summary['rows'])
    need(set(rows) == set(IDS) and [r['id'] for r in summary['rows']] == list(IDS))
    for cid, wire_sha in zip(IDS, WIRES):
        row, frozen_case, source_case = rows[cid], frozen[cid], index[cid]
        example = examples[cid + '-json-rust']
        source_path = 'som-rs/tests/rust-output-v1/cases/' + cid + '.prompt.json'
        source_raw = physical(repo, source_path)
        need(example['source'] == source_path and example['target'] == 'rust'
             and source_case['prompt'] == cid + '.prompt.json' and sha(source_raw) == source_case['prompt_file_sha256'])
        prompt = parse(source_raw)
        envelope = parse(example['body'].encode('utf-8'))
        need(type(envelope) is dict and set(envelope) == {'format', 'prompt', 'target'}
             and envelope['format'] == 'som.prompt-json.v1' and envelope['target'] == 'rust'
             and canonical(envelope['prompt']) == canonical(prompt)
             and sha(canonical(prompt)) == frozen_case['prompt_sha256'] == row['prompt_sha256'])
        wire = compact({'format': envelope['format'], 'prompt': envelope['prompt']})
        need(sha(wire) == wire_sha == row['request_sha256'])
        prefix = 'run/' + cid + '/python'
        folder = root / 'run' / cid
        need(not folder.is_symlink() and folder.is_dir()
             and {p.name for p in folder.iterdir()} == {'python.compile.request.raw', 'python.compile.response.raw',
                                                        'python.assemble.request.raw', 'python.assemble.response.raw'})
        need(row.get('status') == 'captured_product' and row.get('expected_frozen_files_matched') is True
             and type(row.get('expected_status')) is int and row['expected_status'] == 200
             and type(row.get('compile_status')) is int and row['compile_status'] == 200
             and type(row.get('assemble_status')) is int and row['assemble_status'] == 200)
        need(saved(root, row['compile_request'], prefix + '.compile.request.raw') == wire)
        doc = parse(saved(root, row['compile_response'], prefix + '.compile.response.raw'))
        need(type(doc) is dict and doc.get('status') == 'ok' and type(doc.get('bundle')) is dict)
        bundle = doc['bundle']
        need(bundle.get('version') == 'som.dsl-bundle.v1' and 'target' not in bundle
             and sha(canonical(bundle.get('prompt'))) == frozen_case['prompt_sha256'])
        need(saved(root, row['assemble_request'], prefix + '.assemble.request.raw') == compact({'bundle': bundle}))
        assembled = parse(saved(root, row['assemble_response'], prefix + '.assemble.response.raw'))
        need(type(assembled) is dict and assembled.get('status') == 'ok')
        files = assembled.get('files')
        observed = check_files(files, frozen_case['python_files'])
        need(row['files_sha256'] == observed and row['files_map_sha256'] == sha(canonical(files)))
    pid = summary.get('child_pid')
    need(type(pid) is int and pid > 0)
    probes = summary['ownership_probes']
    need(type(probes) is list and len(probes) >= 27)
    check_inventory(root, len(probes))
    labels = [x['label'] for x in probes]
    start_count = 0
    while start_count < len(labels) and labels[start_count] == 'start':
        start_count += 1
    expected_labels = ['before:/healthz', 'after:/healthz'] + [label for _ in IDS for label in
        ('before:/v1/compile', 'after:/v1/compile', 'before:/v1/assemble', 'after:/v1/assemble')]
    need(start_count >= 1 and labels[start_count:] == expected_labels)
    for number, item in enumerate(probes, 1):
        prefix = f'run/owned-{number:03d}'
        out = saved(root, item['stdout_raw'], prefix + '/stdout.raw')
        err = saved(root, item['stderr_raw'], prefix + '/stderr.raw')
        need(item.get('attempted') is True and type(item.get('port')) is int and item['port'] == 18917
             and type(item.get('pid')) is int and item['pid'] == pid and err == b''
             and item.get('stdout_sha256') == sha(out) and item.get('stderr_sha256') == sha(err))
        if number < start_count:
            need(type(item.get('exit_code')) is int and item['exit_code'] == 1 and out == b'')
        else:
            need(type(item.get('exit_code')) is int and item['exit_code'] == 0 and lsof_owned(out, pid)
                 and item.get('stdout') == out.decode('ascii') and item.get('stderr') == '')
    for phase in ('before', 'after'):
        ports = summary['ports'][phase]
        need(set(ports) == {'18917', '8765', '18765'})
        for port in (18917, 8765, 18765):
            item = ports[str(port)]
            prefix = f'run/port-{phase}-{port}'
            need(type(item.get('port')) is int and item['port'] == port and item.get('attempted') is True
                 and type(item.get('exit_code')) is int and item['exit_code'] == 1
                 and saved(root, item['stdout_raw'], prefix + '/stdout.raw') == b''
                 and saved(root, item['stderr_raw'], prefix + '/stderr.raw') == b'')
        for port in (8765, 18765):
            prefix = f'run/guards/{phase}-{port}'
            need(physical(root, prefix + '.rc') == b'1\n' and physical(root, prefix + '.out') == b''
                 and physical(root, prefix + '.err') == b'')
    need(not (root / 'run/guards/missing.txt').exists())
    receipts = {}
    for phase in ('before', 'after'):
        observed = summary['identity_' + phase]
        need(set(observed) == set(PINS))
        for name, expected in PINS.items():
            item = observed[name]
            need(item.get('sha256') == expected and type(item.get('bytes')) is int and item['bytes'] > 0)
            if name in ('build.json', 'runtime.json', 'source.json', 'assets/manifest.json'):
                raw = saved(root, item['raw'], f'run/identity-{phase}/' + name.replace('/', '_'))
                need(sha(raw) == expected and len(raw) == item['bytes'])
                receipts[(phase, name)] = parse(raw)
    for name in PINS:
        need(summary['identity_before'][name]['sha256'] == summary['identity_after'][name]['sha256']
             and summary['identity_before'][name]['bytes'] == summary['identity_after'][name]['bytes'])
    build = receipts[('before', 'build.json')]
    runtime = receipts[('before', 'runtime.json')]
    source = receipts[('before', 'source.json')]
    need(source.get('commit') == SOURCE and build.get('complete') is True
         and build.get('binary_sha256') == runtime.get('binary_sha256') == BASELINE)
    for phase in ('tree_before', 'tree_after'):
        need(build[phase]['head'] == SOURCE and build[phase]['porcelain'] == '')
    need(summary['health']['status'] == 200 and type(summary['health']['status']) is int
         and canonical(parse(saved(root, summary['health']['response'], 'run/health/response.raw'))) == canonical(runtime['health']))
    cleanup = summary['cleanup']
    need(type(cleanup.get('pid')) is int and cleanup['pid'] == pid and cleanup.get('child_started') is True and cleanup.get('child_stopped') is True
         and cleanup.get('listeners_absent') is True and cleanup.get('escalated') is False
         and cleanup.get('terminate_attempted') is True and cleanup.get('terminate_sent') is True
         and cleanup.get('kill_attempted') is False and cleanup.get('kill_sent') is False
         and cleanup.get('wait_completed') is True and type(cleanup.get('wait_returncode')) is int
         and type(cleanup.get('post_wait_poll')) is int and cleanup['wait_returncode'] == cleanup['post_wait_poll']
         and cleanup['wait_returncode'] in (-15, 0))
    return {'result': 'fresh-baseline-raw-checked', 'checked_cases': 6, 'compile': 6, 'assemble': 6,
            'product_files': sum(len(frozen[x]['python_files']) for x in IDS),
            'ownership_probes': len(probes), 'product_acceptance': False,
            'limits': ['binary and asset hashes and child wait are capture observations',
                       'fresh baseline is not historical wire recovery or current candidate acceptance']}


def main():
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted')
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--capture', type=Path, required=True)
    args = parser.parse_args()
    for root in (args.repo, args.capture):
        need(root.is_absolute() and root.is_dir() and root.resolve() == root and not root.is_symlink())
    print(json.dumps(audit(args.repo, args.capture), sort_keys=True))
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (AuditError, KeyError, TypeError, ValueError, UnicodeError, OSError, RecursionError):
        print('{"result":"failed","product_acceptance":false}', file=sys.stderr)
        sys.exit(1)
