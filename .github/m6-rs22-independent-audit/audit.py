#!/usr/bin/env python3
"""DRAFT. Offline, GitHub-hosted-only data audit of d423 RS22 capture.

No product imports, subprocesses, network or services. Never print raw data.
Exit 2 means checked data but typed refusal diagnostics remain unproved.
This program is not acceptance and must be independently reviewed before use.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

HEAD = 'd423911da37e459dbd56346f5d45f976aa6040ac'
BASELINE = '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
MATRIX = 'ebf77d94f2a58a55e19b9de4b9e339635e292f51d5bbe4e949d274463bad0578'
NEGATIVE = '9bb36d689ad4be26e608ac8be779a1a8bb8db043d37788b5819c9fe1bf402e22'
PHASE = 'b2d13ec6ede88ee6175ba12abd7b608453330030abb184c66a37c886d1e8e7e6'
VECTORS = 'dd09b41f49702f70098574f638e10ea33646366a77dfa486539f324d6f9ff83f'
MANIFEST = '428e948240ca94c5be26e390fe7d058e6570087ec4cac700ab949c38b0bc2486'
REFUSALS = {'RS12_CNL_V3_FILTER_LIMIT', 'RS13_JSON_V3_FILTER_OFFSET'}
LIMIT = 32 * 1024 * 1024
EMPTY = hashlib.sha256(b'').hexdigest()

class AuditError(Exception):
    pass

def need(ok, reason):
    if not ok:
        raise AuditError(reason)

def digest(b):
    return hashlib.sha256(b).hexdigest()

def pairs(items):
    d = {}
    for k, v in items:
        need(k not in d, 'duplicate JSON key')
        d[k] = v
    return d

def parse(b):
    need(type(b) is bytes and len(b) <= LIMIT, 'invalid JSON size')
    try:
        return json.loads(b.decode('utf-8', 'strict'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(AuditError('nonfinite JSON')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AuditError('invalid JSON') from exc

def canonical(v):
    # Python's JSON float rendering is accepted only after frozen-vector checks.
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AuditError('invalid canonical value') from exc

def qualify_vectors(b):
    need(digest(b) == VECTORS, 'canonical vectors SHA mismatch')
    vectors = parse(b)['vectors']
    need(type(vectors) is list and len(vectors) == 11, 'canonical vector count mismatch')
    for vector in vectors:
        expected = vector['canonical'].encode('utf-8')
        need(canonical(parse(vector['input_json'].encode('utf-8'))) == expected
             and digest(expected) == vector['sha256'], 'canonical vector mismatch')

def physical(root, rel):
    need(type(rel) is str and rel and '\\' not in rel and '\x00' not in rel, 'invalid path')
    parts = rel.split('/')
    need(not PurePosixPath(rel).is_absolute() and all(p not in ('', '.', '..') for p in parts), 'unsafe path')
    path = root
    for part in parts:
        path = path / part
        need(not path.is_symlink(), 'symlink refused')
    need(path.is_file() and path.stat().st_size <= LIMIT, 'missing or oversize file')
    return path.read_bytes()

def pinned(root, path, sha):
    b = physical(root, path)
    need(digest(b) == sha, 'pinned input mismatch')
    return parse(b)

def saved(root, record, expected_path):
    need(type(record) is dict and record.get('path') == expected_path, 'saved path mismatch')
    raw = physical(root, expected_path)
    need(type(record.get('bytes')) is int and record['bytes'] == len(raw)
         and record.get('sha256') == digest(raw), 'saved bytes mismatch')
    return raw

def unique(items, predicate=lambda x: True):
    need(type(items) is list, 'expected list')
    found = {}
    for item in items:
        need(type(item) is dict, 'invalid row')
        if predicate(item):
            key = item.get('id')
            need(type(key) is str and key not in found, 'duplicate or missing ID')
            found[key] = item
    return found

def absent_port(port, observation):
    need(type(observation) is dict and observation.get('port') == port
         and observation.get('exit_code') == 1 and observation.get('stdout') == ''
         and observation.get('stderr') == '' and observation.get('stdout_sha256') == EMPTY
         and observation.get('stderr_sha256') == EMPTY, 'listener evidence differs')

def check_guard(root, phase, port):
    prefix = f'run/guards/{phase}-{port}'
    need(physical(root, prefix + '.rc') == b'1\n' and physical(root, prefix + '.out') == b''
         and physical(root, prefix + '.err') == b'', 'protected listener guard differs')

def check_cleanup(s, root):
    ports = s['ports']
    for phase in ('before', 'after'):
        need(set(ports[phase]) == {'18917', '8765', '18765'}, 'port inventory differs')
        for port in (18917, 8765, 18765):
            absent_port(port, ports[phase][str(port)])
        for port in (8765, 18765):
            check_guard(root, phase, port)
    pid = s.get('child_pid')
    need(type(pid) is int and pid > 0, 'child PID missing')
    owned = ports['owned']
    out = saved(root, owned.get('stdout_raw'), 'run/listener-owned/stdout.raw')
    err = saved(root, owned.get('stderr_raw'), 'run/listener-owned/stderr.raw')
    lines = out.split(b'\n')
    valid_fields = (len(lines) >= 3 and lines[-1] == b'' and lines[0] == f'p{pid}'.encode('ascii')
                    and all(re.fullmatch(rb'f(?:0|[1-9][0-9]*)', x) is not None for x in lines[1:-1])
                    and len(set(lines[1:-1])) == len(lines[1:-1]))
    need(owned.get('port') == 18917 and owned.get('pid') == pid and owned.get('attempted') is True
         and owned.get('exit_code') == 0 and valid_fields and err == b''
         and owned.get('stdout') == out.decode('ascii') and owned.get('stderr') == ''
         and owned.get('stdout_sha256') == digest(out) and owned.get('stderr_sha256') == EMPTY,
         'owned listener differs')
    c = s['cleanup']
    need(c.get('pid') == pid and c.get('child_started') is True and c.get('child_stopped') is True
         and c.get('listeners_absent') is True and c.get('escalated') is False
         and c.get('wait_completed') is True and type(c.get('wait_returncode')) is int
         and type(c.get('post_wait_poll')) is int and c['wait_returncode'] == c['post_wait_poll']
         and c.get('terminate_attempted') is True and c.get('terminate_sent') is True
         and c['wait_returncode'] in (-15, 0)
         and c.get('kill_attempted') is False and c.get('kill_sent') is False, 'cleanup evidence differs')

def native_diagnostic(raw, case, request):
    doc = parse(raw)
    need(type(doc) is dict and doc.get('code') == case['expected']['code'], 'refusal code differs')
    ds = doc.get('diagnostics')
    need(type(ds) is list and len(ds) == 1 and type(ds[0]) is dict, 'refusal diagnostics differ')
    expected = dict(case['expected'])
    if case['id'] == 'RS13_JSON_V3_FILTER_OFFSET':
        # Locate this exact field value, not another occurrence of the same string.
        marker = b'"filter_field":' + case['span_text'].encode('utf-8')
        need(request.count(marker) == 1, 'refusal field location ambiguous')
        start = request.index(marker) + len(b'"filter_field":')
        end = start + len(case['span_text'].encode('utf-8'))
        prefix = request[:start].decode('utf-8', 'strict')
        text = request[start:end].decode('utf-8', 'strict')
        need(text == case['span_text'], 'refusal span differs')
        expected.update(byte_start=start, byte_end=end, line=prefix.count('\n') + 1,
                        column=len(prefix.rsplit('\n', 1)[-1]) + 1,
                        end_line=prefix.count('\n') + 1,
                        end_column=len(prefix.rsplit('\n', 1)[-1]) + len(text) + 1)
    if case['id'] == 'RS12_CNL_V3_FILTER_LIMIT':
        source = parse(request)['source'].encode('utf-8')
        need(source[expected['byte_start']:expected['byte_end']].decode('utf-8') == case['span_text'],
             'native CNL source span differs')
    # Extra diagnostic fields have no independent expected values, so fail closed.
    need(ds[0] == expected, 'native complete frozen diagnostic differs')
    return doc

def check_refusal(raw, case):
    doc = parse(raw)
    need(type(doc) is dict and doc.get('code') == case['expected']['code'], 'typed refusal code differs')
    ds = doc.get('diagnostics')
    need(type(ds) is list and len(ds) == 1 and type(ds[0]) is dict, 'typed refusal diagnostics missing')
    # No independent complete typed diagnostic authority exists. Do not claim a pass.

def check_case(root, inputs, row, case, frozen, supplied, result):
    cid = row['id']
    need(re.fullmatch(r'RS[0-9]{2}_[A-Z0-9_]+', cid) is not None, 'case ID format differs')
    status = 400 if cid in REFUSALS else 200
    need(row['form'] == case['form'] and row['expected_control_status'] == status
         and row['frozen_prompt_sha256'] == frozen['prompt_sha256'] == supplied['expected_prompt_sha256']
         and result['prompt_sha256'] == row['frozen_prompt_sha256'], 'case authority differs')
    sr = supplied['requests']['json']
    need(sr['path'] == row['typed_rust_source_path'] and sr['raw_sha256'] == row['typed_rust_source_sha256'], 'typed source authority differs')
    rust = saved(root, result['typed_rust_input'], f'run/{cid}/typed-rust.input.raw')
    public_rust = physical(inputs, sr['path'])
    need(public_rust == rust and digest(public_rust) == sr['raw_sha256'] == result['typed_rust_input_sha256'],
         'typed source bytes differ')
    envelope = parse(rust)
    need(type(envelope) is dict and set(envelope) == {'format', 'prompt', 'target'}
         and envelope['format'] == 'som.prompt-json.v1' and envelope['target'] == 'rust'
         and digest(canonical(envelope['prompt'])) == row['frozen_prompt_sha256'], 'typed source prompt differs')
    typed_wire = json.dumps({'format': envelope['format'], 'prompt': envelope['prompt']}, ensure_ascii=False,
                            separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')
    native_wire = case['python_control_body'].encode('utf-8')
    need(digest(native_wire) == row['native_control_wire_sha256']
         and digest(typed_wire) == row['fresh_python_typed_request_sha256'], 'wire input hash differs')
    expected_files = {'typed-rust.input.raw'}
    for form in ('native', 'typed'):
        expected_files.update({form + '.compile.request.raw', form + '.compile.response.raw'})
        if status == 200:
            expected_files.update({form + '.assemble.request.raw', form + '.assemble.response.raw'})
    case_dir = root / 'run' / cid
    need(not case_dir.is_symlink() and case_dir.is_dir()
         and {p.name for p in case_dir.iterdir()} == expected_files, 'raw case inventory differs')
    maps, replies = [], {}
    for form, expected_wire in (('native', native_wire), ('typed', typed_wire)):
        r = result[form]
        need(r.get('form') == form and r.get('expected_status') == status and r.get('compile_status') == status,
             'compile status differs')
        prefix = f'run/{cid}/{form}'
        request = saved(root, r['compile_request'], prefix + '.compile.request.raw')
        raw = saved(root, r['compile_response'], prefix + '.compile.response.raw')
        need(request == expected_wire, 'compile request bytes differ')
        replies[form] = raw
        if status == 400:
            need(r.get('status') == 'captured_refusal' and r.get('products') == 'not_applicable_refusal'
                 and not any(k.startswith('assemble_') or k.startswith('files_') for k in r), 'refusal product present')
            if form == 'native':
                native_diagnostic(raw, case, request)
            else:
                check_refusal(raw, case)
            continue
        need(r.get('status') == 'captured_product' and r.get('assemble_status') == 200, 'assemble status differs')
        reply = parse(raw)
        bundle = reply.get('bundle') if type(reply) is dict else None
        need(type(bundle) is dict and bundle.get('version') == 'som.dsl-bundle.v1'
             and digest(canonical(bundle.get('prompt'))) == row['frozen_prompt_sha256'], 'compiled bundle differs')
        assemble_wire = saved(root, r['assemble_request'], prefix + '.assemble.request.raw')
        expected_assemble = json.dumps({'bundle': bundle}, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')
        need(assemble_wire == expected_assemble, 'assemble request differs')
        doc = parse(saved(root, r['assemble_response'], prefix + '.assemble.response.raw'))
        files = doc.get('files') if type(doc) is dict else None
        need(type(files) is dict and files and all(type(k) is str and type(v) is str for k, v in files.items()), 'file map missing')
        for name in files:
            need(name and '\\' not in name and '\x00' not in name and not PurePosixPath(name).is_absolute()
                 and all(p not in ('', '.', '..') for p in name.split('/')), 'unsafe product file path')
        per_file = {k: digest(v.encode('utf-8', 'strict')) for k, v in files.items()}
        need(r.get('files_sha256') == per_file and r.get('files_map_sha256') == digest(canonical(files)), 'product file hash differs')
        maps.append(files)
    if status == 200:
        need(len(maps) == 2 and maps[0] == maps[1], 'native/typed product differs')
    elif native_wire == typed_wire:
        need(replies['native'] == replies['typed'], 'identical request replies differ')

def audit(root, repo, inputs):
    matrix = pinned(repo, '.github/m6-rs22-capture/input-matrix.json', MATRIX)
    negative = pinned(repo, 'som-rs/contracts/rust-output-v1/negative-cases.json', NEGATIVE)
    phase = pinned(repo, 'som-rs/contracts/rust-output-v1/phase2/case-inventory.json', PHASE)
    manifest = pinned(inputs, 'manifest.json', MANIFEST)
    qualify_vectors(physical(repo, 'som-rs/contracts/compiler-v1/canonical-vectors.json'))
    need(manifest.get('version') == 'som.public-rust-input-completion.v1'
         and manifest.get('tested_sha') == 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'
         and matrix.get('typed_artifact_input_manifest_sha256') == MANIFEST, 'input manifest identity differs')
    groups = [unique(matrix['rows']), unique(negative['cases'], lambda x: x['id'].startswith('RS')),
              unique(phase['rows'], lambda x: x.get('kind') == 'rs'),
              unique(manifest['cohorts']['rust_phase2'], lambda x: x.get('kind') == 'rs')]
    summary = parse(physical(root, 'summary.json'))
    need(summary.get('version') == 'som.rs22.fresh-baseline-capture.v1'
         and summary.get('status') == 'captured' and summary.get('product_acceptance_passed') is False
         and summary.get('role') == 'fresh fixed 2bce baseline observation; not historical wire recovery'
         and summary.get('source_commit') == HEAD == summary.get('source_commit_after')
         and summary.get('baseline_binary_sha256') == BASELINE, 'capture identity differs')
    results = unique(summary['rows'])
    groups.append(results)
    ids = set(groups[0])
    need(len(ids) == 22 and REFUSALS <= ids and all(set(g) == ids for g in groups), 'case inventory differs')
    for cid in sorted(ids):
        check_case(root, inputs, groups[0][cid], groups[1][cid], groups[2][cid], groups[3][cid], results[cid])
    check_cleanup(summary, root)
    return {'result': 'incomplete', 'checked': {'compile': 44, 'assemble': 40, 'native_refusals': 2,
                                                'typed_refusal_raw_and_code': 2},
            'unproved': ['complete typed refusal diagnostics lack independent frozen rules',
                         'runner provenance, artifact authenticity and baseline identity require separate verification'],
            'product_acceptance': False}

def main():
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
         'GitHub hosted runner required')
    p = argparse.ArgumentParser()
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--inputs', type=Path, required=True)
    args = p.parse_args()
    for root in (args.capture, args.repo, args.inputs):
        need(root.is_absolute() and root.is_dir() and root.resolve() == root and not root.is_symlink(), 'root refused')
    print(json.dumps(audit(args.capture, args.repo, args.inputs), sort_keys=True))
    return 2

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (AuditError, KeyError, TypeError, ValueError, UnicodeError, OSError, RecursionError):
        # No exception details: private raw material must never enter logs.
        print('{"result":"failed","product_acceptance":false}', file=sys.stderr)
        sys.exit(1)
