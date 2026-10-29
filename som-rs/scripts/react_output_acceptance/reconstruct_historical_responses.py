"""Reconstruct byte-identical historical response files from a later sealed capture.

This is derived data, not the historical wire capture or product acceptance.
The caller must restore the exact #130 evidence using restore_hosted_evidence.mjs.
The caller must separately restore the approved historical input index. Nothing here
fetches, publishes, changes secrets, or grants permission for additional inputs.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

MAP_SHA = '3b06627c31626238960d38ddb9e772e57c900a98a28ba6407790ca8196edd8ca'
EVIDENCE_SHA = '3759239392208eab81bf740ca9ca2a41465be3e3e4995de2bd255774f4596bea'
INPUT_SHA = '78375feb14ed286ac5df4c52f13d5d1ae301b2057d5e3894edf0f36c2705c498'

class Refused(ValueError): pass

def need(ok):
    if not ok: raise Refused('historical response reconstruction refused')

def valid_manifest_name(name):
    return isinstance(name, str) and re.fullmatch(r'package-[0-9]{3}[.]manifest[.]json', name) is not None

def valid_response_name(name):
    return isinstance(name, str) and re.fullmatch(r'run/python-baseline-raw/calls/[0-9]{6}[.]response[.]raw', name) is not None

def sha(data): return hashlib.sha256(data).hexdigest()
def read(path):
    need(not path.is_symlink() and path.is_file())
    return path.read_bytes()
def parse(data):
    def unique(pairs):
        d = {}
        for key, value in pairs:
            need(key not in d); d[key] = value
        return d
    return json.loads(data, object_pairs_hook=unique)
def safe(root, name):
    need(isinstance(name, str) and name and not name.startswith('/') and '\\' not in name)
    parts = name.split('/')
    need(all(p not in ('', '.', '..') for p in parts))
    current = root
    for part in parts:
        current = current / part
        need(not current.is_symlink())
    return current

def indexed_files(root, index_sha, evidence):
    raw = read(root / ('evidence-index.json' if evidence else 'inputs-index.json'))
    need(sha(raw) == index_sha)
    index = parse(raw)
    need(index['version'] == ('som.m6-evidence-index.v1' if evidence else 'som.m6-input-index.v1'))
    result = {}
    for package in index['packages']:
        need(valid_manifest_name(package['manifest']))
        manifest_raw = read(safe(root, package['manifest']))
        need(sha(manifest_raw) == package['manifest_sha256'])
        manifest = parse(manifest_raw)
        need(manifest['version'] == 'som.m6-sealed-inputs-manifest.v1')
        if evidence:
            need(sha(read(safe(root, package['archive']))) == package['cipher_sha256'] == manifest['cipher_sha256'])
        for item in manifest['files']:
            name = item['path']
            safe(root, name)
            need(name not in result and re.fullmatch('[0-9a-f]{64}', item['sha256']) is not None)
            result[name] = (item['sha256'], item['bytes'], package, manifest)
    return result

def checked(root, name, index):
    need(name in index)
    value = read(safe(root, name)); expected, size, _, _ = index[name]
    need(len(value) == size and sha(value) == expected)
    return value

def derive(map_raw, input_packages, input_root, evidence_packages, evidence_root,
           *, map_sha=MAP_SHA, input_sha=INPUT_SHA, evidence_sha=EVIDENCE_SHA, expected_count=668):
    need(sha(map_raw) == map_sha)
    m = parse(map_raw)
    need(m['artifact']['artifact_id'] == 12843589912 and m['artifact']['run_id'] == 40743738705
         and m['artifact']['head_sha'] == '40cffc13b3bff35e3ea5804b9f43a49cdef7b3c0'
         and m['artifact']['evidence_index_sha256'] == evidence_sha
         and m['historical_input_index_sha256'] == input_sha)
    old = indexed_files(input_packages, input_sha, False)
    later = indexed_files(evidence_packages, evidence_sha, True)
    rows = m['reconstructed_cases']; need(len(rows) == expected_count)
    expected = {('public', i) for i in range(127)} | {('legacy', i) for i in range(544) if i not in (531, 535, 541)}
    if expected_count == 668: need({(r['source'], r['index']) for r in rows} == expected)
    output = {}; ids = set()
    for row in rows:
        source, i = row['source'], row['index']
        need(source in ('public', 'legacy') and type(i) is int and row['status'] == 200
             and row['source_request_equal'] is False and row['historical_request_sha256'] != row['later_baseline_request_sha256'])
        need((source, i) not in ids); ids.add((source, i))
        prefix = (f'.local/rust-compiler-goal/acceptance/public-products-003b/{i:03d}' if source == 'public'
                  else f'.local/rust-compiler-goal/acceptance/legacy544/run-005/{i:04d}')
        request_path = prefix + '/assemble.request.raw'
        if request_path in old:
            request = checked(input_root, request_path, old)
        else:
            request_obj = parse(checked(input_root, prefix + '/assemble.request.json', old))
            request = json.dumps(request_obj, ensure_ascii=False, separators=(',', ':')).encode()
        need(sha(request) == row['historical_request_sha256'])
        need(row['historical_raw_path'] == prefix + '/assemble.response.raw.b64'
             and row['historical_json_path'] == prefix + '/assemble.response.json')
        candidates = row['source_candidates']; need(isinstance(candidates, list) and candidates)
        # All listed source candidates must be authentic, byte-identical captures.
        raw = None
        for candidate in candidates:
            name = candidate['path']; need(valid_response_name(name))
            data = checked(evidence_root, name, later)
            _, size, package, _ = later[name]
            need(candidate['package'] == package['archive'] and candidate['manifest'] == package['manifest']
                 and candidate['manifest_sha256'] == package['manifest_sha256']
                 and candidate['cipher_sha256'] == package['cipher_sha256'] and candidate['size'] == size
                 and sha(data) == row['decoded_response_sha256'] and (raw is None or raw == data))
            raw = data
        raw_file = base64.b64encode(raw) + b'\n'
        json_file = (json.dumps({'http_status': 200, 'body': parse(raw)}, ensure_ascii=False, indent=2) + '\n').encode()
        need(sha(raw_file) == row['historical_raw_sha256'] and sha(json_file) == row['historical_json_sha256'])
        for name, data in ((row['historical_raw_path'], raw_file), (row['historical_json_path'], json_file)):
            need(name not in output and name not in old); output[name] = data
    return output

def main():
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ('map', 'input-packages', 'input-root', 'evidence-packages', 'evidence-root', 'out'):
        p.add_argument('--' + flag, required=True, type=Path)
    a = p.parse_args()
    output = derive(read(a.map), a.input_packages, a.input_root, a.evidence_packages, a.evidence_root)
    need(not a.out.exists() and not a.out.is_symlink() and a.out.parent.is_dir())
    stage = Path(tempfile.mkdtemp(prefix='.reconstruct-', dir=a.out.parent))
    try:
        for name, data in output.items():
            path = safe(stage, name); path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'xb') as f: f.write(data)
        os.rename(stage, a.out)
    except BaseException:
        import shutil
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print('Derived 668 response pairs; original wire capture and product acceptance are not inferred.')

if __name__ == '__main__': main()
