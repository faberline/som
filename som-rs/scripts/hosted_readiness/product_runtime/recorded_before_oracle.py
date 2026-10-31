"""Serve exact recorded before replies. This process is Python, never old SOM.

The input profile must be tracked, source admitted and independently reviewed.
The original compiler identity and its capture time remain separate facts.
All executable qualification belongs on GitHub runners.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import urllib.parse
import zipfile

SELF = 'som-rs/scripts/hosted_readiness/product_runtime/recorded_before_oracle.py'
VERSION = 'som.linux-qa-recorded-before.v1'
ROLE = 'recorded-same-request-before'
INVENTORY_SHA = '83489c5cc729b7f79e513cfbf2b6c7c93cca717daff093a19f101d566ca6c44f'
ORIGINAL_INDEX_SHA = '3759239392208eab81bf740ca9ca2a41465be3e3e4995de2bd255774f4596bea'
APPROVED_INDEX_SHA = 'c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf'
COMPILER_SHA = '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
COMPILER_SOURCE = '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af'
COUNTS = {'react_python': 2684, 'rust006': 699, 'rust006_conditional_assemble': 697, 'typescript125': 492}
MAX_FILE = 128 * 1024**2
MAX_ARCHIVE = 2 * 1024**3
PORT = 18979
FRESH_BASE = 'run/rust-before-assemble/'
RECEIPT_PATHS = {'calibration': 'run/receipt.json', 'fresh': FRESH_BASE + 'receipt.json'}
SHA = re.compile('[0-9a-f]{64}\\Z')


class OracleError(ValueError):
    pass


def need(value, reason):
    if not value:
        raise OracleError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for name, value in items:
            need(name not in result, 'duplicate JSON member')
            result[name] = value
        return result
    def constant(value):
        raise OracleError('nonfinite JSON number')
    return json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs, parse_constant=constant)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def relative(value):
    need(type(value) is str and value and not value.startswith('/') and '\\' not in value
         and all(part not in ('', '.', '..') for part in value.split('/'))
         and not any(ord(char) < 32 or ord(char) == 127 for char in value), 'unsafe input member')
    return value


def physical(path, *, directory=False):
    path = Path(path).absolute()
    need('..' not in path.parts, 'traversing physical path')
    for item in (path, *path.parents):
        mode = item.lstat().st_mode
        need(not stat.S_ISLNK(mode), 'linked input path')
        need(stat.S_ISDIR(mode) if item != path or directory else stat.S_ISREG(mode), 'special input path')
    return path


def read(path, maximum=MAX_FILE):
    path = physical(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= maximum, 'bounded physical input required')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    stamp = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)
    need(stamp(before) == stamp(after) == stamp(path.stat()) and len(raw) == before.st_size, 'input changed during read')
    return raw


def file_sha(path, maximum=MAX_ARCHIVE):
    path = physical(path)
    info = path.stat()
    need(info.st_nlink == 1 and info.st_size <= maximum, 'bounded archive required')
    with path.open('rb') as stream:
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
    need((info.st_ino, info.st_dev, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
         (path.stat().st_ino, path.stat().st_dev, path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ctime_ns),
         'archive changed during hash')
    return value


def write(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


class Archive:
    """Bind restored bytes to an exact independently pinned GitHub artifact."""
    def __init__(self, root, pin, metadata):
        self.root = physical(root, directory=True)
        self.used = {}
        self.sealed_hashes = {}
        self.pin = pin
        need(type(pin) is dict and set(pin) == {'id', 'name', 'size_in_bytes', 'digest', 'run_id', 'head_sha', 'index_sha256'},
             'exact artifact profile fields required')
        need(type(pin['id']) is int and type(pin['run_id']) is int and type(pin['size_in_bytes']) is int
             and SHA.fullmatch(pin['index_sha256']) and re.fullmatch('[0-9a-f]{40}', pin['head_sha'])
             and re.fullmatch('sha256:[0-9a-f]{64}', pin['digest']), 'artifact profile identity differs')
        origin = metadata.get('workflow_run', {})
        need(all(metadata.get(name) == pin[name] for name in ('id', 'name', 'size_in_bytes', 'digest'))
             and metadata.get('expired') is False and origin.get('id') == pin['run_id']
             and origin.get('head_sha') == pin['head_sha'] and origin.get('repository_id') == 1390374878,
             'artifact metadata differs from reviewed source/run')
        self.archive = self.root / 'artifact.zip'
        need(self.archive.stat().st_size == pin['size_in_bytes']
             and file_sha(self.archive) == pin['digest'].removeprefix('sha256:'), 'whole artifact bytes differ')
        self.members = {}
        with zipfile.ZipFile(self.archive) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            need(len(names) == len(set(names)) and len(names) <= 2001 and 'evidence-index.json' in names,
                 'artifact member universe differs')
            for item in infos:
                relative(item.filename)
                need(stat.S_IFMT(item.external_attr >> 16) in (0, stat.S_IFREG) and not item.is_dir()
                     and item.file_size <= MAX_FILE and not item.flag_bits & 1, 'artifact member type differs')
            index_raw = archive.read('evidence-index.json')
            need(sha(index_raw) == pin['index_sha256'] and read(self.root / 'sealed/evidence-index.json') == index_raw,
                 'artifact index differs')
            index = parse(index_raw)
            self.sealed_hashes['evidence-index.json'] = sha(index_raw)
            need(index.get('version') == 'som.m6-evidence-index.v1' and type(index.get('packages')) is list
                 and 1 <= len(index['packages']) <= 1000, 'artifact index schema differs')
            expected = {'evidence-index.json'}
            for number, package in enumerate(index['packages']):
                name = f'package-{number:03d}'
                need(package['archive'] == name + '.sealed' and package['manifest'] == name + '.manifest.json',
                     'artifact package sequence differs')
                expected.update((package['archive'], package['manifest']))
                raw = archive.read(package['manifest'])
                need(sha(raw) == package['manifest_sha256']
                     and read(self.root / 'sealed' / package['manifest']) == raw, 'artifact manifest differs')
                manifest = parse(raw)
                self.sealed_hashes[package['manifest']] = sha(raw)
                need(manifest['version'] == 'som.m6-sealed-inputs-manifest.v1'
                     and manifest['cipher_sha256'] == package['cipher_sha256']
                     and manifest['total_files'] == package['total_files'] == len(manifest['files']), 'artifact manifest identity differs')
                with archive.open(package['archive']) as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                need(digest == package['cipher_sha256']
                     and file_sha(self.root / 'sealed' / package['archive']) == digest, 'original encrypted package differs')
                self.sealed_hashes[package['archive']] = digest
                # Index bytes count raw payloads. uncompressed_bytes counts the
                # separately encoded base64 JSON envelope, not the payload sum.
                need(package['bytes'] == sum(item['bytes'] for item in manifest['files'])
                     and type(manifest['uncompressed_bytes']) is int
                     and package['bytes'] <= manifest['uncompressed_bytes'] <= 4 * MAX_FILE,
                     'original plaintext member size universe differs')
                for member in manifest['files']:
                    member_name = relative(member['path'])
                    need(member_name not in self.members and set(member) == {'path', 'sha256', 'bytes', 'mode'}
                         and SHA.fullmatch(member['sha256']) and type(member['bytes']) is int
                         and 0 <= member['bytes'] <= MAX_FILE and member['mode'] in (0o644, 0o755), 'artifact raw member identity differs')
                    self.members[member_name] = dict(member)
            need(set(names) == expected, 'unexpected artifact members')

    def read(self, name):
        name = relative(name)
        need(name in self.members, 'unbound restored member')
        raw = read(self.root / 'restored' / name)
        item = self.members[name]
        need(len(raw) == item['bytes'] and sha(raw) == item['sha256']
             and stat.S_IMODE((self.root / 'restored' / name).stat().st_mode) == item['mode'], 'restored raw member differs')
        self.used[name] = item['sha256']
        return raw

    def json(self, name):
        return parse(self.read(name))

    def verify(self):
        need(file_sha(self.archive) == self.pin['digest'].removeprefix('sha256:'), 'artifact changed during oracle run')
        for name, digest in self.sealed_hashes.items():
            need(file_sha(self.root / 'sealed' / name) == digest, 'sealed source metadata or ciphertext changed')
        for name, digest in list(self.used.items()):
            need(sha(self.read(name)) == digest, 'used raw source changed')


class RecordedTable:
    """Keep every named slot while using only exact unmodified HTTP request bytes."""
    def __init__(self, slots, entries):
        need(type(slots) is list and type(entries) is list and len(slots) == len(entries), 'table scope differs')
        ids = [row['slot_id'] for row in slots]
        need(len(ids) == len(set(ids)), 'duplicate named slot id')
        self.entries, self.index, self.hits = [], collections.defaultdict(list), collections.Counter()
        for slot, entry in zip(slots, entries):
            fields = ('slot_id', 'cohort', 'group', 'case', 'phase', 'form', 'variant', 'method', 'endpoint')
            identity = {key: slot[key] for key in fields}
            need(entry['identity'] == identity and identity['method'] == 'POST'
                 and identity['endpoint'] in ('/v1/compile', '/v1/assemble')
                 and type(entry['request']) is bytes and type(entry['response']) is bytes
                 and type(entry['status']) is int and 100 <= entry['status'] <= 599, 'table entry role differs')
            key = (identity['method'], identity['endpoint'], sha(entry['request']))
            previous = self.index[key]
            need(all(row['request'] == entry['request'] and row['status'] == entry['status']
                     and row['response'] == entry['response'] for row in previous), 'ambiguous same-request source replies')
            row = {**entry, 'identity': identity}
            previous.append(row)
            self.entries.append(row)

    def lookup(self, method, endpoint, request):
        need(type(request) is bytes, 'raw request bytes required')
        key = (method, endpoint, sha(request))
        candidates = self.index.get(key, [])
        need(candidates and all(row['request'] == request for row in candidates), 'internal oracle miss')
        self.hits[key] += 1
        return candidates[0]['status'], candidates[0]['response'], [row['identity'] for row in candidates]

    def public(self):
        return [{'identity': row['identity'], 'request_sha256': sha(row['request']), 'status': row['status'],
                 'response_sha256': sha(row['response']), 'source': row['source']} for row in self.entries]

    def observed_keys(self):
        return [{'method': method, 'endpoint': endpoint, 'request_sha256': digest, 'calls': count,
                 'matching_named_slot_ids': [row['identity']['slot_id'] for row in self.index[(method, endpoint, digest)]]}
                for (method, endpoint, digest), count in sorted(self.hits.items())]


class Approved:
    """Read only original member bytes from the independently approved 18 inputs."""
    def __init__(self, root):
        self.root, self.members, self.used = physical(root, directory=True), {}, {}
        self.manifest_hashes = {}
        raw = read(self.root / 'inputs-index.json')
        need(sha(raw) == APPROVED_INDEX_SHA, 'approved input index differs')
        index = parse(raw)
        need(index['version'] == 'som.m6-input-index.v1' and len(index['packages']) == 18, 'approved input package scope differs')
        for number, package in enumerate(index['packages']):
            name = f'package-{number:03d}'
            need(package['archive'] == name + '.sealed' and package['manifest'] == name + '.manifest.json', 'approved package sequence differs')
            raw = read(self.root / package['manifest'])
            need(sha(raw) == package['manifest_sha256'], 'approved manifest differs')
            self.manifest_hashes[package['manifest']] = sha(raw)
            manifest = parse(raw)
            need(manifest['version'] == 'som.m6-sealed-inputs-manifest.v1'
                 and manifest['total_files'] == len(manifest['files']), 'approved member scope differs')
            for item in manifest['files']:
                path = relative(item['path'])
                need(path not in self.members and SHA.fullmatch(item['sha256'])
                     and type(item['bytes']) is int and 0 <= item['bytes'] <= MAX_FILE
                     and item['mode'] in (0o644, 0o755), 'approved member identity differs')
                self.members[path] = dict(item)

    def read(self, name):
        name = relative(name)
        need(name in self.members, 'member is absent from original approved inputs')
        raw = read(self.root / 'restored' / name)
        item = self.members[name]
        need(len(raw) == item['bytes'] and sha(raw) == item['sha256']
             and stat.S_IMODE((self.root / 'restored' / name).stat().st_mode) == item['mode'], 'approved original bytes differ')
        self.used[name] = item['sha256']
        return raw

    def json(self, name):
        return parse(self.read(name))

    def verify(self):
        need(sha(read(self.root / 'inputs-index.json')) == APPROVED_INDEX_SHA, 'approved index changed')
        for name, digest in self.manifest_hashes.items():
            need(sha(read(self.root / name)) == digest, 'approved original manifest changed')
        for name, digest in list(self.used.items()):
            need(sha(self.read(name)) == digest, 'approved member changed')


def compiler_metadata(approved, source_reader):
    release_path = 'som-rs/evidence/typescript-output-release-2026-10-22-issue65.json'
    release = parse(source_reader(release_path))['candidate']
    need(release['binary_sha256'] == COMPILER_SHA and release['build_commit'] == COMPILER_SOURCE,
         'original compiler release role differs')
    prefix = '.local/m6-hosted/baseline/'
    need(sha(approved.read(prefix + 'som')) == COMPILER_SHA and len(approved.read(prefix + 'som')) == release['binary_bytes'],
         'original compiler binary bytes differ')
    result = {'binary_sha256': COMPILER_SHA, 'source_revision': COMPILER_SOURCE,
              'role': 'original-source-compiler-not-the-Linux-service-executable',
              'release_source_sha256': sha(source_reader(release_path))}
    for name, key in [('source.json', 'source_json_sha256'), ('build.json', 'build_receipt_sha256'),
                      ('runtime.json', 'runtime_receipt_sha256')]:
        raw = approved.read(prefix + name)
        need(sha(raw) == release[key], 'original compiler metadata differs')
        result[name + '_sha256'] = sha(raw)
    result['health'] = approved.json(prefix + 'runtime.json')['health']
    result['health_role'] = 'original-runtime-metadata; not a live Linux compiler health response'
    return result


def original_entries(slots, original, calibration):
    """Rederive all 3,875 raw source joins. Do not execute frozen project loaders."""
    need(calibration['version'] == 'som.recorded-before-calibration.v1' and calibration['passed'] is True
         and calibration['inventory_sha256'] == INVENTORY_SHA and calibration['evidence_index_sha256'] == ORIGINAL_INDEX_SHA
         and calibration['roles'] == {name: count for name, count in COUNTS.items() if name != 'rust006_conditional_assemble'}
         and calibration['runtime_comparison'] == 'unrun' and calibration['product_acceptance'] == 'not_run', 'reviewed calibration receipt differs')
    active = [slot for slot in slots if slot['cohort'] != 'rust006_conditional_assemble']
    calibrated = calibration['rows']
    need(len(active) == len(calibrated) == 3875 and [row['slot_id'] for row in calibrated] == [slot['slot_id'] for slot in active],
         'complete calibrated slot order required')
    reports = {name: original.json(path) for name, path in {
        'react_python': 'run/python-baseline/report.json', 'rust006': 'run/rust-baseline/report.json',
        'typescript125': 'run/ts-baseline/report.json'}.items()}
    python_rows = {(row['source'], row['id'], row['variant']): row for row in reports['react_python']['rows']}
    rust_rows = {(row['phase'], row['id']): row for row in reports['rust006']['rows'] if row['check'] == 'compile_equal'}
    ts_rows = {(row['group'], row['id']): row for row in reports['typescript125']['cases']}
    need(len(python_rows) == 1342 and len(rust_rows) == 699 and len(ts_rows) == 125, 'original report scopes differ')
    calls = {}
    for name in ('python', 'rust'):
        recording = original.json(f'run/{name}-baseline-raw/manifest.json')
        need(recording['exit_code'] == 0 and recording['recording_errors'] == [], 'original recorder failed')
        items = recording['calls']
        need([row['id'] for row in items] == list(range(1, len(items) + 1)), 'original recorder sequence differs')
        calls[name] = {row['id']: row for row in items}
    entries = {}
    for slot, checked in zip(active, calibrated):
        need(checked['slot_id'] == slot['slot_id'] and checked['cohort'] == slot['cohort'] and checked['case'] == slot['case']
             and checked['method'] == slot['method'] and checked['endpoint'] == slot['endpoint']
             and checked['state'] == 'calibrated' and checked['runtime_comparison'] == 'unrun'
             and checked['rebuilt_sha256'] == checked['historical_request_sha256'] == slot['historical_request_sha256']
             and checked['source_candidates'] == checked['verified_candidates'] == len(slot['source_candidates']) > 0,
             'calibrated named entry differs')
        action = 'compile' if slot['endpoint'] == '/v1/compile' else 'assemble'
        cohort = slot['cohort']
        if cohort == 'react_python':
            report = python_rows[(slot['group'], slot['case'], slot['variant'])]
            status, response_sha = report[f'baseline_{action}_status'], report[f'baseline_{action}_response_sha256']
            need(report[f'baseline_{action}_request_sha256'] == slot['historical_request_sha256'], 'Python report request differs')
        elif cohort == 'rust006':
            report = rust_rows[(slot['phase'], slot['case'])]
            status, response_sha = report['baseline_status'], report['baseline_response_sha256']
            need(action == 'compile' and report['passed'] is True
                 and report['baseline_request_sha256'] == slot['historical_request_sha256'], 'Rust report request differs')
        else:
            case = ts_rows[(slot['group'], slot['case'])]
            matches = [row for row in case['requests'] if row['form'] == slot['form']]
            need(case['passed'] is True and len(matches) == 1, 'TypeScript original form differs')
            report = matches[0]
            report_prefix = 'baseline' if action == 'compile' else 'baseline_assemble'
            status, response_sha = report[report_prefix + '_status'], report[report_prefix + '_sha256']
            request_sha = report['body_sha256'] if action == 'compile' else report['baseline_assemble_request_sha256']
            need(request_sha == slot['historical_request_sha256'], 'TypeScript report request differs')
        observed = []
        for candidate in slot['source_candidates']:
            request = original.read(candidate['request_member'])
            response = original.read(candidate['response_member'])
            need(candidate['manifest_bound'] is True and sha(request) == slot['historical_request_sha256']
                 == candidate['request_package']['sha256'] and sha(response) == response_sha
                 == candidate['response_sha256'] == candidate['response_package']['sha256']
                 and candidate['status'] == status, 'original source candidate bytes/status differ')
            name = candidate['capture']
            if name in calls:
                call = calls[name][candidate['call_id']]
                prefix = f"run/{name}-baseline-raw/calls/{candidate['call_id']:06d}"
                url = urllib.parse.urlsplit(call['url'])
                need(original.json(prefix + '.json') == call and call['method'] == slot['method']
                     and url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port == 18917
                     and url.path == slot['endpoint'] and not url.query and not url.fragment
                     and call['complete'] is True and call['error'] is None and call['status'] == status
                     and call['request_sha256'] == sha(request) and call['response_sha256'] == sha(response)
                     and candidate['request_member'] == prefix + '.request.raw'
                     and candidate['response_member'] == prefix + '.response.raw', 'original raw call role differs')
            else:
                need(name == 'ts125' and cohort == 'typescript125', 'unknown source capture role')
                request_name = report['request_file'] if action == 'compile' else report['baseline_assemble_request_file']
                need(candidate['request_member'] == 'run/ts-baseline/' + request_name
                     and candidate['response_member'] == 'run/ts-baseline/' + report[report_prefix + '_response_file'], 'TypeScript raw member role differs')
            observed.append((request, response))
        need(all(pair == observed[0] for pair in observed), 'conflicting duplicate source bytes')
        entries[slot['slot_id']] = {'identity': {key: slot[key] for key in ('slot_id', 'cohort', 'group', 'case', 'phase', 'form', 'variant', 'method', 'endpoint')},
            'request': observed[0][0], 'response': observed[0][1], 'status': status,
            'source': {'role': 'later-recorded-before', 'compiler_binary_sha256': COMPILER_SHA,
                       'compiler_source_revision': COMPILER_SOURCE, 'source_candidates': slot['source_candidates']}}
    return entries


def fresh_entries(slots, original, fresh, approved, entries, metadata):
    """Bind the separately captured 697 assemblies to their real before bundles."""
    plan = fresh.json(FRESH_BASE + 'plan.json')
    receipt = fresh.json(FRESH_BASE + 'receipt.json')
    need(plan['role'] == receipt['role'] == 'fresh-before' and plan['historical_wire'] is False
         and receipt['historical_wire'] is False and plan['product_acceptance'] == receipt['product_acceptance'] == 'not_run'
         and receipt['passed'] is True and receipt['unrun'] == 0 and receipt['error'] is None
         and receipt['cleanup_errors'] == [] and receipt['baseline_sha256'] == receipt['baseline_sha256_after'] == COMPILER_SHA
         and receipt['baseline_provenance']['build_commit'] == COMPILER_SOURCE, 'fresh before role or result differs')
    need(receipt['runner']['github_sha'] == fresh.pin['head_sha']
         and str(receipt['runner']['github_run_id']) == str(fresh.pin['run_id'])
         and receipt['runner']['machine'] == 'arm64', 'fresh compiler actual producing run differs')
    selected = [slot for slot in slots if slot['cohort'] == 'rust006_conditional_assemble']
    need(len(selected) == len(plan['slots']) == len(receipt['rows']) == 697
         and [row['slot_id'] for row in plan['slots']] == [slot['slot_id'] for slot in selected]
         and [row['slot_id'] for row in receipt['rows']] == [slot['slot_id'] for slot in selected], 'fresh complete named slot order differs')
    rust_report = original.json('run/rust-baseline/report.json')
    rust_rows = {(row['phase'], row['id'], row['check']): row for row in rust_report['rows']}
    need(len(rust_rows) == len(rust_report['rows']) == 2093, 'original Rust report scope differs')
    compile_slots = {(slot['phase'], slot['case']): slot for slot in slots if slot['cohort'] == 'rust006'}
    refused = {key for key, slot in compile_slots.items() if slot['report_status'] != 200}
    need({key[1] for key in refused} == {'RS12_CNL_V3_FILTER_LIMIT', 'RS13_JSON_V3_FILTER_OFFSET'}
         and {(row['phase'], row['case']) for row in plan['refusals']} == refused and len(plan['refusals']) == 2,
         'exact two compile refusals must have no assembly slot')
    for row in plan['refusals']:
        slot = compile_slots[(row['phase'], row['case'])]
        need(row['role'] == 'historical-compile-refusal-no-assemble' and row['status'] == slot['report_status']
             and row['response_sha256'] == slot['report_response_sha256'], 'fresh plan changed original refusal')
    original_calls = original.json('run/rust-baseline-raw/manifest.json')['calls']
    run = '.local/rust-compiler-goal/acceptance/rust-output-055/run-003/'
    phase1 = approved.json(run + 'rust-products.json')
    need(sha(approved.read(run + 'rust-products.json')) == rust_report['rust_products_sha256'], 'accepted phase1 source differs')
    phase1_rows = {row['id']: row for row in phase1['cases']}
    need(len(phase1_rows) == len(phase1['cases']) == 115, 'accepted phase1 product inventory differs')
    phase2 = approved.json(run + 'phase2/summary.json')
    for slot, planned, captured in zip(selected, plan['slots'], receipt['rows']):
        key = (slot['phase'], slot['case'])
        compile_slot = compile_slots[key]
        compile_entry = entries[compile_slot['slot_id']]
        bundle = parse(compile_entry['response'])['bundle']
        # This is the exact frozen HTTP writer, not sorted canonical JSON.
        request = json.dumps({'bundle': bundle}, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        refused_row = rust_rows[(*key, 'baseline_bundle_refused')]
        need(refused_row['passed'] is True and refused_row['qa_status'] == 400
             and refused_row['request_sha256'] == refused_row['qa_request_sha256'] == sha(request)
             == planned['prior_candidate_refusal_request_sha256'] == planned['request_sha256'], 'fresh request serializer calibration differs')
        source_calls = [call for call in original_calls if call['method'] == 'POST'
                        and call['url'] == 'http://127.0.0.1:18919/v1/assemble'
                        and call['request_sha256'] == sha(request)
                        and call['response_sha256'] == refused_row['qa_response_sha256']]
        need(source_calls, 'original candidate-side old-bundle request is missing')
        for call in source_calls:
            prefix = f"run/rust-baseline-raw/calls/{call['id']:06d}"
            need(call['status'] == 400 and call['complete'] is True and call['error'] is None
                 and original.json(prefix + '.json') == call
                 and original.read(prefix + '.request.raw') == request
                 and sha(original.read(prefix + '.response.raw')) == call['response_sha256'], 'original candidate refusal raw bytes differ')
        name = str(slot['slot_id'])
        need(fresh.read(FRESH_BASE + 'requests/' + name + '.raw') == request
             and planned['case'] == slot['case'] and planned['phase'] == slot['phase']
             and planned['method'] == slot['method'] == 'POST' and planned['endpoint'] == slot['endpoint'] == '/v1/assemble'
             and planned['baseline_compile_response_sha256'] == sha(compile_entry['response']), 'fresh named request source differs')
        raw = fresh.read(FRESH_BASE + 'responses/' + name + '.raw')
        need(captured['status'] == 200 and captured['matched'] is True and captured['comparison'] == 'passed'
             and captured['response_sha256'] == sha(raw), 'fresh actual raw response differs')
        files = parse(raw).get('files')
        need(type(files) is dict and all(type(path) is str and type(body) is str for path, body in files.items()), 'fresh complete product map required')
        if key[0] == 'phase1':
            wanted = phase1_rows[key[1]]['files_sha256']
        else:
            case_path = run + 'phase2/cases/' + re.sub('[^A-Za-z0-9]+', '_', key[1]).strip('_') + '.json'
            case_raw = approved.read(case_path)
            need(sha(case_raw) == phase2['case_sha256'][key[1]]
                 == rust_report['records_sha256']['phase2_case_files'][key[1]], 'accepted phase2 source differs')
            wanted = parse(case_raw)['rust']['files_sha256']
        rebased = rust_rows[(*key, 'rebased_files')]
        need(sha(json_bytes(files)) == wanted == rebased['files_sha256'] == rebased['recorded_files_sha256']
             == planned['expected_files_sha256'] and rebased['passed'] is True and rebased['qa_status'] == 200,
             'fresh files differ from independent accepted product map')
        expected_calls = [call for call in original_calls if call['method'] == 'POST'
                          and call['url'] == 'http://127.0.0.1:18919/v1/assemble'
                          and call['request_sha256'] == rebased['qa_request_sha256']
                          and call['response_sha256'] == rebased['qa_response_sha256']]
        need(expected_calls and planned['accepted_response_sha256'] == rebased['qa_response_sha256'], 'accepted whole file map source unavailable')
        for call in expected_calls:
            prefix = f"run/rust-baseline-raw/calls/{call['id']:06d}"
            need(call['status'] == 200 and call['complete'] is True and call['error'] is None
                 and original.json(prefix + '.json') == call
                 and sha(original.read(prefix + '.request.raw')) == call['request_sha256']
                 and parse(original.read(prefix + '.response.raw'))['files'] == files, 'accepted complete file map bytes differ')
        need(slot['slot_id'] not in entries and slot['historical_request_sha256'] is None, 'fresh input cannot overwrite original missing wire')
        entries[slot['slot_id']] = {'identity': {field: slot[field] for field in ('slot_id', 'cohort', 'group', 'case', 'phase', 'form', 'variant', 'method', 'endpoint')},
            'request': request, 'response': raw, 'status': 200,
            'source': {'role': 'fresh-before; not original historical assembly wire', 'compiler_binary_sha256': COMPILER_SHA,
                       'compiler_source_revision': COMPILER_SOURCE, 'capture_run_id': fresh.pin['run_id'],
                       'capture_source_revision': fresh.pin['head_sha'], 'original_missing_request_sha256': None}}
    # This validator must rederive the original raw process, socket, health,
    # direct wait, and cleanup evidence. A receipt boolean cannot replace it.
    return entries


def verify_fresh_process(fresh, approved, receipt, metadata):
    """Read old raw observations. Never query or relabel an old PID as live here."""
    prefix = '.local/m6-hosted/baseline/'
    need(receipt['baseline_provenance']['build_commit'] == COMPILER_SOURCE
         and receipt['baseline_sha256'] == receipt['baseline_sha256_after'] == COMPILER_SHA,
         'fresh source compiler identity differs')
    for name in ('source.json', 'build.json', 'runtime.json'):
        need(sha(approved.read(prefix + name)) == receipt['baseline_provenance'][name] == metadata[name + '_sha256'],
             'fresh source compiler original metadata differs')
    expected_assets = {name.removeprefix(prefix + 'assets/'): sha(approved.read(name))
                       for name in approved.members if name.startswith(prefix + 'assets/')}
    need(expected_assets and receipt['assets'] == expected_assets and receipt['assets_unchanged'] is True,
         'fresh source compiler complete assets differ')
    rustfmt = '2c56fd066853c80c786a8a3a0e5434b7f808ace8b2cf66ae6e1a13566aa2815f'
    need(receipt['rustfmt']['sha256'] == receipt['rustfmt_sha256_after'] == rustfmt
         and receipt['rustfmt']['path'] == '/opt/homebrew/Cellar/rust/1.96.1/bin/rustfmt', 'source Rust formatter identity differs')
    version = fresh.read(FRESH_BASE + 'rustfmt-version.stdout')
    need(version.decode().strip() == receipt['rustfmt']['version'] and version.startswith(b'rustfmt 1.9.0')
         and fresh.read(FRESH_BASE + 'rustfmt-version.stderr') == b'', 'source Rust formatter actual probe differs')
    raw = fresh.read(FRESH_BASE + 'health.raw')
    need(receipt['health_status'] == 200 and receipt['health_sha256'] == sha(raw)
         and parse(raw) == metadata['health'], 'fresh source compiler actual raw health differs')
    pid, owner = receipt['pid'], receipt['owner_pid']
    need(type(pid) is int and type(owner) is int and pid > 1 and owner > 1 and pid != owner
         and receipt['port'] == 18917 and receipt['argv'][-2:] == ['--port', '18917'], 'fresh owned source process role differs')
    identity_fields = ('pid', 'parent_pid', 'pgid', 'start_time', 'binary_device', 'binary_inode', 'binary_size')
    start, end = receipt['process_start'], receipt['process_end']
    need(all(start[name] == end[name] for name in identity_fields)
         and start['pid'] == start['pgid'] == pid and start['parent_pid'] == owner,
         'source process identity changed between raw observations')
    need(all(start[name] == receipt['binary_prelaunch'][name] for name in ('binary_device', 'binary_inode', 'binary_size')),
         'source running executable differs from original prelaunch file')
    commands = {
        'identity': ['/bin/ps', '-ww', '-p', str(pid), '-o', 'pid=,ppid=,pgid=,stat=,command='],
        'start_time': ['/bin/ps', '-p', str(pid), '-o', 'lstart='],
        'listeners': ['/usr/sbin/lsof', '-nP', '-iTCP:18917', '-sTCP:LISTEN', '-Fp'],
        'executable': ['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid), '-d', 'txt', '-FfDin'],
    }
    for stage, observed in [('start', start), ('end', end)]:
        need(set(observed['commands']) == set(commands), 'source process raw command scope differs')
        output = {}
        for name, argv in commands.items():
            record = observed['commands'][name]
            stdout = fresh.read(f'{FRESH_BASE}process-evidence/{stage}-{name}.stdout')
            stderr = fresh.read(f'{FRESH_BASE}process-evidence/{stage}-{name}.stderr')
            need(record['argv'] == argv and record['exit_code'] == 0 and not stderr
                 and sha(stdout) == record['stdout_sha256'] and sha(stderr) == record['stderr_sha256'],
                 'source process actual raw command differs')
            output[name] = stdout.decode('utf-8', 'strict').strip()
        fields = output['identity'].split(None, 4)
        need(len(fields) == 5 and fields[:3] == [str(pid), str(owner), str(pid)] and not fields[3].startswith('Z')
             and fields[4] == ' '.join(receipt['argv']) and output['start_time'] == start['start_time'],
             'raw source PID/parent/group/start/argv differs')
        need([line[1:] for line in output['listeners'].splitlines() if line.startswith('p')] == [str(pid)],
             'source baseline listener was not exclusively owned')
        descriptors, current = [], None
        for line in output['executable'].splitlines():
            if line.startswith('f'):
                current = {'fd': line[1:]}
                descriptors.append(current)
            elif current is not None and line[:1] in ('D', 'i', 'n'):
                need(line[0] not in current, 'duplicate executable descriptor field')
                current[line[0]] = line[1:]
        need(any(item.get('fd') == 'txt' and 'D' in item and 'i' in item
                 and int(item['D'], 0) == start['binary_device'] and int(item['i']) == start['binary_inode']
                 for item in descriptors), 'raw source mapped executable inode differs')
    listener, sockets = receipt['listener_after'], receipt['sockets_after']
    stdout, stderr = fresh.read(FRESH_BASE + 'process-evidence/cleanup-listeners.stdout'), fresh.read(FRESH_BASE + 'process-evidence/cleanup-listeners.stderr')
    need(listener['argv'] == commands['listeners'] and listener['listener_absent'] is True and listener['exit_code'] == 1
         and not stdout and not stderr and sha(stdout) == listener['stdout_sha256'] and sha(stderr) == listener['stderr_sha256'],
         'raw source listener cleanup differs')
    stdout, stderr = fresh.read(FRESH_BASE + 'process-evidence/cleanup-sockets.stdout'), fresh.read(FRESH_BASE + 'process-evidence/cleanup-sockets.stderr')
    need(sockets['argv'] == ['/usr/sbin/netstat', '-an', '-p', 'tcp'] and sockets['exit_code'] == 0 and not stderr
         and sha(stdout) == sockets['stdout_sha256'] and sha(stderr) == sockets['stderr_sha256'], 'raw socket cleanup command differs')
    states = []
    for line in stdout.decode().splitlines():
        fields = line.split()
        if fields and fields[0] in ('tcp4', 'tcp6') and len(fields) >= 6 and fields[3].endswith('.18917'):
            states.append(fields[5])
    binding = fresh.json(FRESH_BASE + 'process-evidence/cleanup-bind.json')
    need(binding == receipt['bind_after'] and binding['address'] == '127.0.0.1' and binding['port'] == 18917
         and states == sockets['states'] and 'LISTEN' not in states
         and (binding['available'] is True or (binding['available'] is False and binding['errno'] == 48 and 'TIME_WAIT' in states)),
         'source listener/TIME_WAIT/bind distinction differs')
    owner_record = fresh.json(FRESH_BASE + 'candidate-owner.json')
    need(owner_record['version'] == 'som.m6-owned-candidate.v1' and owner_record['product_acceptance_passed'] is False,
         'source owner record differs')
    events = owner_record['events']
    waits = [event for event in events if event.get('kind') in ('candidate-wait', 'candidate-cleanup-wait') and event.get('state') == 'completed']
    need(len(waits) == 1 and waits[0]['pid'] == pid and type(waits[0]['exit_code']) is int
         and waits[0]['exit_code'] == receipt['wait_exit'] and receipt['process_group_gone'] is True,
         'source direct child wait record missing or conflicting')
    groups = [event for event in events if event.get('kind') == 'candidate-group-observation']
    need(len(groups) == 2 and {event['stage'] for event in groups} == {'before-term', 'before-kill'}, 'source group observation scope differs')
    live_at_last = None
    for event in groups:
        stdout = fresh.read(FRESH_BASE + 'candidate-group-' + event['stage'] + '.stdout')
        stderr = fresh.read(FRESH_BASE + 'candidate-group-' + event['stage'] + '.stderr')
        need(event['argv'] == ['/bin/ps', '-axo', 'pid=,ppid=,pgid=,stat=,command=']
             and event['exit_code'] == 0 and not stderr and sha(stdout) == event['stdout_sha256']
             and sha(stderr) == event['stderr_sha256'], 'source owned group observation raw bytes differ')
        rows = [line.split(None, 4) for line in stdout.decode().splitlines() if line.strip()]
        need(any(len(row) == 5 and row[:3] == [str(pid), str(owner), str(pid)] for row in rows), 'source group leader identity absent')
        live = [int(row[0]) for row in rows if len(row) == 5 and row[2] == str(pid) and not row[3].startswith('Z')]
        need(event['live_count'] == len(live), 'source live group observation count differs')
        if event['stage'] == 'before-kill':
            live_at_last = live
    return {'source_role': 'fresh-before', 'historical_wire': False, 'raw_process_health_listener_verified': True,
            'direct_wait_exit': receipt['wait_exit'], 'listener_absent': True,
            'last_raw_group_observation_live_pids': live_at_last,
            'process_group_gone': 'producer_attested; not upgraded to independent post-wait OS observation',
            'node_libnode_prettier_after': 'unverified; no product acceptance transferred',
            'product_acceptance_passed': False}


def prepare_data(source_reader, profile_path, inputs_root):
    """Admit complete source data from a tracked reviewed profile and whole artifacts."""
    profile_path = relative(profile_path)
    profile_raw = source_reader(profile_path)
    profile = parse(profile_raw)
    need(set(profile) == {'version', 'role', 'admission_complete', 'inventory_path', 'source_compiler', 'artifacts', 'receipts'}
         and profile['version'] == VERSION and profile['role'] == ROLE and profile['admission_complete'] is True,
         'complete reviewed tracked recorded-before profile required')
    need(profile['source_compiler'] == {'binary_sha256': COMPILER_SHA, 'source_revision': COMPILER_SOURCE},
         'original before compiler cannot be replaced by current or Linux rebuild')
    inventory_path = relative(profile['inventory_path'])
    inventory_raw = source_reader(inventory_path)
    need(sha(inventory_raw) == INVENTORY_SHA, 'entire frozen named inventory differs')
    inventory = parse(inventory_raw)
    slots = inventory['slots']
    need(len(slots) == 4572 and [slot['slot_id'] for slot in slots] == list(range(1, 4573))
         and dict(collections.Counter(slot['cohort'] for slot in slots)) == COUNTS,
         'complete named table order/counts differ')
    need(len({tuple(slot[name] for name in ('cohort', 'group', 'case', 'phase', 'form', 'variant', 'method', 'endpoint'))
              for slot in slots}) == 4572, 'duplicate named source identity')
    root = physical(inputs_root, directory=True)
    catalog_raw = read(root / 'catalog.json')
    catalog = parse(catalog_raw)
    need(set(catalog) == set(profile['artifacts']) == {'original', 'calibration', 'fresh'}
         and set(profile['receipts']) == {'calibration', 'fresh'}, 'exact explicit artifact roles required')
    original_pin = profile['artifacts']['original']
    need(original_pin == {'id': 12843589912, 'name': 'm6-sealed-raw-evidence', 'size_in_bytes': 249935784,
        'digest': 'sha256:33fbad9d63cfa257636384b9b7c10e1406f2306922e4f28e8189c79f703b041e',
        'run_id': 40743738705, 'head_sha': '40cffc13b3bff35e3ea5804b9f43a49cdef7b3c0', 'index_sha256': ORIGINAL_INDEX_SHA},
        'original before artifact role differs')
    calibration_pin, fresh_pin = profile['artifacts']['calibration'], profile['artifacts']['fresh']
    need(calibration_pin['id'] == 12883513668 and calibration_pin['run_id'] == 40843685234
         and calibration_pin['head_sha'] == '9925c3dc36bf538934afe6975f61bdcf44b3bfed'
         and calibration_pin['digest'] == 'sha256:109e809fd0f628644dc7d02098806ac920fc8b8ca1b07fb2aa63e167fd9b4895',
         'independently calibrated original request artifact differs')
    need(fresh_pin['id'] == 12884153563 and fresh_pin['run_id'] == 40844637378
         and fresh_pin['head_sha'] == 'e120a04d8ca9c2989638c643e834b1e66d0a8e01'
         and fresh_pin['digest'] == 'sha256:63aed26168d112b6b5c2f3a1bf030d0f97fcc0734aaf4bb9d42cce9e13d43f69',
         'actual fresh before artifact differs')
    archives = {name: Archive(root / name, profile['artifacts'][name], catalog[name]) for name in ('original', 'calibration', 'fresh')}
    receipts = {}
    for name in ('calibration', 'fresh'):
        binding = profile['receipts'][name]
        need(set(binding) == {'path', 'sha256'} and binding['path'] == RECEIPT_PATHS[name]
             and type(binding['sha256']) is str and SHA.fullmatch(binding['sha256']), 'exact original receipt member pin required')
        raw = archives[name].read(binding['path'])
        need(sha(raw) == binding['sha256'], 'original complete receipt bytes differ')
        receipts[name] = parse(raw)
    approved = Approved(root / 'approved')
    metadata = compiler_metadata(approved, source_reader)
    entries = original_entries(slots, archives['original'], receipts['calibration'])
    entries = fresh_entries(slots, archives['original'], archives['fresh'], approved, entries, metadata)
    need(set(entries) == set(range(1, 4573)), 'complete before table has missing or extra entries')
    table = RecordedTable(slots, [entries[slot['slot_id']] for slot in slots])
    process = verify_fresh_process(archives['fresh'], approved, receipts['fresh'], metadata)
    proof = {'version': 'som.recorded-before-data-proof.v1', 'profile_sha256': sha(profile_raw),
             'inventory_sha256': INVENTORY_SHA, 'named_slots': 4572, 'cohorts': dict(COUNTS),
             'table_sha256': sha(json_bytes(table.public()) + b'\n'), 'source_compiler': metadata,
             'fresh_process_scope': process, 'artifact_pins': profile['artifacts'],
             'original_missing_wire_fields_changed': False, 'runtime_comparison': 'unrun',
             'product_acceptance_passed': False}

    def verify():
        need(source_reader(profile_path) == profile_raw and source_reader(inventory_path) == inventory_raw
             and read(root / 'catalog.json') == catalog_raw, 'source profile/inventory/catalog changed')
        compiler_metadata(approved, source_reader)
        for archive in archives.values():
            archive.verify()
        approved.verify()
    verify()
    return table, metadata, proof, verify


class SourceGit:
    """Read exact regular current Git blobs without importing project helpers."""
    def __init__(self, repo, revision):
        self.root, self.revision, self.pins = physical(repo, directory=True), revision, {}
        need(type(revision) is str and re.fullmatch('[0-9a-f]{40}', revision), 'exact source revision required')
        need(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision
             and self.git('rev-parse', '--show-toplevel').decode().strip() == str(self.root), 'actual current Git source required')
        self.read(SELF)

    def git(self, *arguments):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *arguments],
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}, stderr=subprocess.PIPE, timeout=60)

    def read(self, name):
        name = relative(name)
        entry = self.git('ls-tree', '-z', self.revision, '--', name)
        need(entry.count(b'\0') == 1 and entry.endswith(b'\t' + name.encode() + b'\0')
             and entry.startswith((b'100644 blob ', b'100755 blob ')), 'regular tracked source required')
        raw = self.git('cat-file', 'blob', self.revision + ':' + name)
        need(read(self.root / name) == raw and (name not in self.pins or self.pins[name] == sha(raw)), 'raw source changed or differs from Git')
        self.pins[name] = sha(raw)
        return raw

    def verify(self):
        need(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == self.revision, 'source HEAD changed')
        for name in list(self.pins):
            self.read(name)


def serve(table, out, health, verify_data, *, port=PORT, data_proof=None):
    """Own no child processes. The shared launcher proves this process lifecycle."""
    need(type(table) is RecordedTable and port == PORT and health['role'] == ROLE
         and health['actual_runtime'] == 'qualified-linux-python'
         and health['source_compiler']['binary_sha256'] == COMPILER_SHA
         and health['source_compiler']['source_revision'] == COMPILER_SOURCE,
         'explicit recorded service/runtime/compiler roles required')
    out = Path(out).absolute()
    need(not out.exists() and not any(path.is_symlink() for path in out.parents), 'new oracle output required')
    out.mkdir(mode=0o700)
    if data_proof is not None:
        write(out / 'data-proof.json', json_bytes(data_proof) + b'\n')
    write(out / 'table.json', json_bytes(table.public()) + b'\n')
    health_raw = json_bytes(health) + b'\n'
    write(out / 'health.json', health_raw)
    state = {'version': 'som.recorded-before-service-run.v1', 'role': ROLE,
             'actual_runtime': 'qualified-linux-python', 'source_compiler': health['source_compiler'],
             'pid': os.getpid(), 'port': port, 'calls': [], 'misses': 0, 'errors': [],
             'table_sha256': sha(read(out / 'table.json')), 'table_complete': len(table.entries) == 4572,
             'runtime_named_coverage': 'owner_verifier_required', 'product_acceptance_passed': False,
             'owned_waits_complete': False, 'owned_cleanup_complete': False,
             'request_loop_finished': False, 'data_unchanged': False, 'raw_logs_complete': False}
    stopped = {'signal': None}

    def stop(number, frame):
        if stopped['signal'] is None:
            stopped['signal'] = number

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            pass  # Only retained raw records contain requests. stdout has no raw bodies.

        def exchange(self):
            index = len(state['calls'])
            relative_root = f'calls/{index:06d}'
            row = {'id': index + 1, 'method': self.command, 'endpoint': self.path,
                   'request_file': relative_root + '.request.raw', 'response_file': relative_root + '.response.raw',
                   'request_sha256': None, 'response_sha256': None, 'status': None,
                   'matching_named_slots': [], 'complete': False, 'oracle_miss': False,
                   'request_body_complete': False,
                   'started_ns': time.time_ns(), 'error': None}
            state['calls'].append(row)
            request = b''
            response = b''
            try:
                lengths = self.headers.get_all('Content-Length', [])
                need(not self.headers.get_all('Transfer-Encoding') and not self.headers.get_all('Expect'), 'ambiguous HTTP framing')
                if self.command == 'POST':
                    need(len(lengths) == 1 and re.fullmatch('[0-9]+', lengths[0])
                         and int(lengths[0]) <= MAX_FILE, 'bounded exact HTTP body required')
                    request = self.rfile.read(int(lengths[0]))
                    need(len(request) == int(lengths[0]), 'partial HTTP request')
                else:
                    need(not lengths or lengths == ['0'], 'unexpected body for method')
                row['request_body_complete'] = True
                write(out / row['request_file'], request)
                row['request_sha256'] = sha(request)
                if self.command == 'GET' and self.path == '/healthz':
                    row['status'], response = 200, health_raw
                else:
                    row['status'], response, row['matching_named_slots'] = table.lookup(self.command, self.path, request)
            except OracleError as error:
                state['misses'] += 1
                row.update(oracle_miss=True, status=500, error=str(error))
                response = json_bytes({'status': 'internal_error', 'code': 'INTERNAL_ORACLE_MISS',
                                       'source_role': ROLE, 'compiler_refusal': False}) + b'\n'
                if row['request_sha256'] is None:
                    write(out / row['request_file'], request)
                    row['request_sha256'] = sha(request)
            except BaseException as error:
                row['error'] = type(error).__name__
                state['errors'].append({'id': row['id'], 'stage': 'request', 'error': type(error).__name__})
                raise
            try:
                write(out / row['response_file'], response)
                row['response_sha256'] = sha(response)
                self.send_response(row['status'])
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(response)
                self.wfile.flush()
                row['complete'] = True
            except BaseException as error:
                row['error'] = type(error).__name__
                state['errors'].append({'id': row['id'], 'stage': 'response', 'error': type(error).__name__})
                raise
            finally:
                row['finished_ns'] = time.time_ns()
                write(out / (relative_root + '.json'), json_bytes(row) + b'\n')
                self.close_connection = True

        do_GET = exchange
        do_POST = exchange
        do_PUT = exchange
        do_DELETE = exchange
        do_PATCH = exchange
        do_HEAD = exchange
        do_OPTIONS = exchange

    class Server(HTTPServer):
        allow_reuse_address = False

        def handle_error(self, request, client_address):
            state['errors'].append({'stage': 'HTTP handler', 'error': type(sys.exception()).__name__})

    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    server = None
    try:
        verify_data()
        for number in previous:
            signal.signal(number, stop)
        server = Server(('127.0.0.1', port), Handler)
        server.timeout = 0.2
        print(json.dumps({'stage': 'listening', 'address': f'127.0.0.1:{port}', 'purpose': ROLE}), flush=True)
        while stopped['signal'] is None:
            server.handle_request()
        state['request_loop_finished'] = True
    except BaseException as error:
        state['errors'].append({'stage': 'serve', 'error': type(error).__name__})
        raise
    finally:
        if server is not None:
            server.server_close()
        for number, handler in previous.items():
            signal.signal(number, handler)
        try:
            verify_data()
            state['data_unchanged'] = True
        except BaseException as error:
            state['errors'].append({'stage': 'final data proof', 'error': type(error).__name__})
        state['stop_signal'] = stopped['signal']
        state['observed_wire_keys'] = table.observed_keys()
        state['raw_logs_complete'] = all(row['complete'] and row['request_body_complete'] and row['request_sha256'] == sha(read(out / row['request_file']))
                                         and row['response_sha256'] == sha(read(out / row['response_file'])) for row in state['calls'])
        write(out / 'result.json', json_bytes(state) + b'\n')
    return 0 if state['request_loop_finished'] and state['data_unchanged'] and state['raw_logs_complete'] and not state['misses'] and not state['errors'] else 1


def verify_recorded_run(root, source_reader, helper_revision, candidate_revision, profile_path, inputs_root, *, repo, runtime):
    """Rederive saved data and every reply; process ownership is also checked by the shared launcher."""
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux'
         and helper_revision == candidate_revision, 'GitHub Linux exact-source oracle verifier required')
    sources = SourceGit(repo, helper_revision)
    need(source_reader(SELF) == sources.read(SELF), 'oracle verifier differs from native admitted raw source')
    def reader(name):
        raw = sources.read(name)
        need(source_reader(name) == raw, 'oracle data source differs from native source admission')
        return raw
    table, metadata, proof, verify = prepare_data(reader, profile_path, inputs_root)
    root = physical(root, directory=True)
    result_raw, table_raw, health_raw = read(root / 'result.json'), read(root / 'table.json'), read(root / 'health.json')
    result, health = parse(result_raw), parse(health_raw)
    need(table_raw == json_bytes(table.public()) + b'\n' and read(root / 'data-proof.json') == json_bytes(proof) + b'\n',
         'saved complete oracle table/data proof differs')
    compiler = {name: metadata[name] for name in ('binary_sha256', 'source_revision', 'health', 'health_role')}
    need(runtime['version'] == 'som.recorded-before-oracle-runtime.v1'
         and runtime['actual_runtime'] == health['actual_runtime'] == result['actual_runtime'] == 'qualified-linux-python'
         and runtime['source_compiler'] == health['source_compiler'] == result['source_compiler'] == compiler
         and runtime['source_revision'] == health['source_revision'] == helper_revision
         and runtime['candidate_revision'] == health['candidate_revision'] == candidate_revision
         and runtime['pid'] == health['pid'] == result['pid'] and runtime['port'] == result['port'] == PORT
         and runtime['binary_sha256'] == health['runtime_python_sha256']
         and runtime['profile_path'] == profile_path and runtime['profile_sha256'] == health['profile_sha256'] == proof['profile_sha256']
         and runtime['health'] == health and runtime['accepted'] is False,
         'actual Python runtime and original compiler source roles differ')
    need(result['version'] == 'som.recorded-before-service-run.v1' and result['role'] == health['role'] == ROLE
         and result['table_complete'] is True and result['table_sha256'] == health['table_sha256'] == sha(table_raw)
         and result['request_loop_finished'] is True and result['data_unchanged'] is True
         and result['raw_logs_complete'] is True and result['misses'] == 0 and result['errors'] == []
         and result['product_acceptance_passed'] is False and result['owned_waits_complete'] is False
         and result['owned_cleanup_complete'] is False and result['runtime_named_coverage'] == 'owner_verifier_required',
         'oracle data/call lifecycle is incomplete; child cannot grant shared waits or product acceptance')
    need(result['stop_signal'] == signal.SIGTERM, 'recorded service did not finish through the owned normal stop')
    expected_files = {'table.json', 'health.json', 'data-proof.json', 'result.json'}
    for index, row in enumerate(result['calls']):
        prefix = f'calls/{index:06d}'
        need(row['id'] == index + 1 and row['request_file'] == prefix + '.request.raw'
             and row['response_file'] == prefix + '.response.raw' and row['complete'] is True
             and row['request_body_complete'] is True and row['oracle_miss'] is False and row['error'] is None
             and parse(read(root / (prefix + '.json'))) == row, 'oracle original per-call record differs')
        expected_files.update((prefix + '.json', row['request_file'], row['response_file']))
        request, response = read(root / row['request_file']), read(root / row['response_file'])
        need(sha(request) == row['request_sha256'] and sha(response) == row['response_sha256'], 'oracle raw request/reply differs')
        if row['method'] == 'GET' and row['endpoint'] == '/healthz':
            need(request == b'' and row['status'] == 200 and response == health_raw and row['matching_named_slots'] == [], 'oracle health call differs')
        else:
            status, wanted, matches = table.lookup(row['method'], row['endpoint'], request)
            need(status == row['status'] and wanted == response and matches == row['matching_named_slots'],
                 'oracle served status/body/named source list differs')
    need(table.observed_keys() == result['observed_wire_keys'], 'oracle observed wire-key counts differ')
    actual_files = set()
    for path in root.rglob('*'):
        info = path.lstat()
        need(not stat.S_ISLNK(info.st_mode) and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)), 'linked or special oracle proof member')
        if stat.S_ISREG(info.st_mode):
            actual_files.add(path.relative_to(root).as_posix())
    need(actual_files == expected_files, 'oracle retained raw file universe differs')
    verify()
    sources.verify()
    need(read(root / 'result.json') == result_raw and read(root / 'table.json') == table_raw and read(root / 'health.json') == health_raw,
         'oracle saved proof changed during rederivation')
    return {'version': 'som.recorded-before-run-verification.v1', 'profile_sha256': proof['profile_sha256'],
            'helper_revision': helper_revision, 'candidate_revision': candidate_revision,
            'table_sha256': sha(table_raw), 'table': table.public(), 'calls': result['calls'],
            'result_sha256': sha(result_raw), 'runtime_named_coverage': 'owner_verifier_required',
            'product_acceptance_passed': False}


def main(argv=None):
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and os.environ.get('RUNNER_OS') == 'Linux' and sys.platform == 'linux' and os.uname().machine == 'x86_64'
         and sys.version_info[:2] == (3, 12) and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
         'GitHub qualified Linux Python3.12 -I -S -B required')
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'inputs-root', 'out'):
        parser.add_argument('--' + name, required=True, type=Path)
    for name in ('helper-revision', 'candidate-revision', 'profile', 'python-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args(argv)
    need(args.helper_revision == args.candidate_revision == os.environ.get('GITHUB_SHA')
         and args.port == PORT and SHA.fullmatch(args.python_sha256), 'actual source/frame and fixed QA port required')
    python = physical(sys.executable)
    need(read(python)[:4] == b'\x7fELF' and stat.S_IMODE(python.stat().st_mode) & 0o111
         and file_sha(python) == args.python_sha256, 'actual running Python differs from qualified parent-selected executable')
    sources = SourceGit(args.repo, args.helper_revision)
    temporary = physical(os.environ['RUNNER_TEMP'], directory=True)
    output = args.out.absolute()
    input_root = physical(args.inputs_root, directory=True)
    need(temporary in output.parents and not output.exists() and '..' not in output.parts
         and not any(path.is_symlink() for path in output.parents)
         and all(root != output and root not in output.parents and output not in root.parents for root in (sources.root, input_root)),
         'new isolated owned output required')
    table, metadata, proof, verify = prepare_data(sources.read, args.profile, input_root)
    compiler = {name: metadata[name] for name in ('binary_sha256', 'source_revision', 'health', 'health_role')}
    health = {'version': 'som.recorded-before-oracle-health.v1', 'status': 'ok', 'role': ROLE,
              'actual_runtime': 'qualified-linux-python', 'runtime_python_sha256': args.python_sha256,
              'source_revision': args.helper_revision, 'candidate_revision': args.candidate_revision,
              'pid': os.getpid(), 'profile_sha256': proof['profile_sha256'], 'table_sha256': proof['table_sha256'],
              'source_compiler': compiler}
    def verify_all():
        verify()
        sources.verify()
        need(file_sha(python) == args.python_sha256, 'Python executable changed during recorded service run')
    return serve(table, output, health, verify_all, port=args.port, data_proof=proof)


if __name__ == '__main__':
    raise SystemExit(main())
