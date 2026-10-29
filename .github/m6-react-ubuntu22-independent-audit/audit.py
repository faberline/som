#!/usr/bin/env python3
"""Independently audit one retained Ubuntu 22.04 QA replay, not acceptance."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import tarfile
import zipfile

RUN = 40818588943
HEAD = '448862a76656095696f2791d46428a0b87e063e1'
JOB = 123265438449
ARTIFACT = 12873517359
ZIP_SHA = 'b117cb220f22d9045dd1727dcd2beb0b487e42287830895202ebf659ee4d5fff'
ZIP_BYTES = 82590609
PRODUCER = '62637173ffeecde2655482377a218b22bf54618a'
SOURCE_TAR = '5f453d9e3f4fc17e36c9c16d17dacc157a34915fc7c9e477f49da3e81b807171'
SOURCE_ZIP = '10a7f2bf058be21c670ddac89afece05c305ee717b9a879de4c95256fd6fe2dc'
NODE_SHA = '3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538'
NODE_ARCHIVE = '9f619528f1db5ddc41dccf54211066fb42228d69a156733c69cb9d6cc92e358c'
NPM_TREE = {'sha256': '69965a8f2a75cad99018e333ff1819bdc8cbf4f17570e77f24ad5f95aa57e558', 'files': 1921, 'symlinks': 0}
CACHE_TREE = {'sha256': '5267851624684b07307609f59d0b13705bd7934a1fd59bde2045463a3ee6ddda', 'files': 246, 'symlinks': 0}
PRETTIER_TREE = {'sha256': 'f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299', 'files': 56, 'symlinks': 0}
LOCK_SHA = 'c6f3856e1a620d78d4357ef54f89488612a5c1606bbf0219265fc984334b5814'
PACKAGE_SHA = 'ba9cda19860b88d13f319bc8762c03ba738c81e8aa32158f35a6864b6c1d8230'
FORMATTER = '636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8'
TAR_NAME = 'react-ubuntu22-replay.tar'
EXPECTED_ZIP = {TAR_NAME, TAR_NAME + '.sha256', 'react-ubuntu22-source-audit.json',
                'som-react-linux-qa-closure/restore-receipt.json', 'som-react-linux-qa-closure/producer-report.json',
                'react-producer-run.json', 'react-audit-run.json', 'react-producer-artifact.json'}
MAX_TAR = 2 * 1024**3
MAX_MEMBER = 512 * 1024**2
MAX_ENTRIES = 200000

class AuditError(ValueError):
    pass

def need(value, reason):
    if not value:
        raise AuditError(reason)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def json_strict(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate JSON member')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(AuditError('nonfinite JSON')))
    except (UnicodeError, ValueError) as exc:
        raise AuditError('invalid JSON') from exc

def path_name(name):
    need(isinstance(name, str) and name and not any(c in name for c in ('\\', '\x00', '\n', '\r')), 'invalid archive path')
    name = name.rstrip('/')
    need(name and not PurePosixPath(name).is_absolute() and all(p not in ('', '.', '..') for p in name.split('/')), 'unsafe archive path')
    return name

def stream_sha(stream):
    digest = hashlib.sha256()
    while True:
        raw = stream.read(1024 * 1024)
        if not raw:
            return digest.hexdigest()
        digest.update(raw)

def file_sha(path):
    need(path.is_file() and not path.is_symlink(), 'input missing or linked')
    with path.open('rb') as source:
        return stream_sha(source)

def metadata(run, jobs, artifact, job_log):
    need(run.get('id') == RUN and run.get('head_sha') == HEAD and run.get('status') == 'completed'
         and run.get('conclusion') == 'success' and run.get('run_attempt') == 1, 'replay run differs')
    all_jobs = jobs.get('jobs')
    need(isinstance(all_jobs, list) and len(all_jobs) == 1, 'job inventory differs')
    job = all_jobs[0]
    need(job.get('id') == JOB and job.get('name') == 'replay' and job.get('head_sha') == HEAD
         and job.get('status') == 'completed' and job.get('conclusion') == 'success', 'replay job differs')
    steps = job.get('steps')
    need(isinstance(steps, list) and len(steps) > 0 and all(x.get('conclusion') == 'success' for x in steps), 'job steps differ')
    required = {'Run replay synthetic fixtures', 'Check original run and artifact identities',
                'Download and independently recheck retained public evidence', 'Restore exact retained cache and public tools',
                'Observe offline reference install on Ubuntu 22', 'Preserve raw replay results',
                'Run actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'}
    names = [x.get('name') for x in steps]
    need(all(names.count(x) == 1 for x in required), 'required replay step missing')
    need(artifact.get('id') == ARTIFACT and artifact.get('expired') is False and artifact.get('size_in_bytes') == ZIP_BYTES
         and artifact.get('digest') == 'sha256:' + ZIP_SHA and artifact.get('workflow_run', {}).get('id') == RUN
         and artifact.get('workflow_run', {}).get('head_sha') == HEAD, 'replay artifact differs')
    text = job_log.decode('utf-8', 'strict')
    prefix = r'replay\tSet up job\t[^\n]*'
    need(re.search(prefix + r'##\[group\]Operating System\n' + prefix + r'Ubuntu\n' + prefix + r'22\.04\.5\n', text) is not None
         and re.search(prefix + r'Image: ubuntu-22\.04\n' + prefix + r'Version: 20261025\.340\.1\n', text) is not None,
         'runner image log differs')
    return sha(job_log)

def cache_snapshot(doc):
    need(isinstance(doc, dict) and set(doc) == {'root_mode', 'entries'}, 'cache snapshot shape differs')
    mode, entries = doc['root_mode'], doc['entries']
    need(type(mode) is int and 0 <= mode <= 0o7777 and mode & 0o222 == 0 and isinstance(entries, dict), 'cache root mode differs')
    rows = []
    for name, item in entries.items():
        need(path_name(name) == name and isinstance(item, dict), 'cache path differs')
        kind, member_mode = item.get('kind'), item.get('mode')
        need(type(member_mode) is int and 0 <= member_mode <= 0o7777 and member_mode & 0o222 == 0, 'cache member mode differs')
        if kind == 'directory':
            need(set(item) == {'kind', 'mode'}, 'cache directory differs')
        else:
            need(kind == 'file' and set(item) == {'kind', 'mode', 'sha256'} and isinstance(item['sha256'], str)
                 and re.fullmatch('[0-9a-f]{64}', item['sha256']), 'cache file differs')
            rows.append((name, name + '\0' + item['sha256'] + '\n'))
        parts = name.split('/')
        for i in range(1, len(parts)):
            ancestor = entries.get('/'.join(parts[:i]))
            need(isinstance(ancestor, dict) and ancestor.get('kind') == 'directory', 'cache parent missing or not directory')
    rows.sort(key=lambda x: x[0])
    observed = {'sha256': sha(''.join(row[1] for row in rows).encode()), 'files': len(rows), 'symlinks': 0}
    need(observed == CACHE_TREE, 'cache snapshot tree differs')
    return observed

class HashingReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.bytes = 0
    def read(self, size=-1):
        raw = self.stream.read(size)
        self.bytes += len(raw)
        need(self.bytes <= MAX_TAR, 'replay tar exceeds bound')
        self.digest.update(raw)
        return raw

def tree_result(rows):
    rows.sort(key=lambda row: row[0])
    return {'sha256': sha(''.join(name + '\0' + digest + '\n' for name, digest in rows).encode()),
            'files': len(rows), 'symlinks': 0}

def inspect_tar(incoming):
    reader = HashingReader(incoming)
    small, names, links, npm, prettier, files, total = {}, set(), set(), [], [], {}, 0
    prefix_npm = 'probe/source-replay/lib/node_modules/npm/'
    prefix_prettier = 'probe/product/node_modules/prettier/'
    capture = {'probe/report.json', 'probe/cache-before.json', 'probe/cache-after.json',
               'probe/product/package.json', 'probe/product/package-lock.json',
               'probe/product/node_modules/@rolldown/binding-linux-x64-gnu/package.json',
               'probe/product/node_modules/lightningcss-linux-x64-gnu/package.json'}
    commands = ('glibc-version', 'node-version', 'npm-version', 'node-elf', 'node-elf-versions', 'npm-ci-offline', 'prettier-version')
    capture.update('probe/' + name + '.' + suffix for name in commands for suffix in ('stdout', 'stderr'))
    with tarfile.open(fileobj=reader, mode='r|') as archive:
        for member in archive:
            name = path_name(member.name)
            need(name == 'probe' or name.startswith('probe/'), 'replay tar root differs')
            need(name not in names and len(names) < MAX_ENTRIES and 0 <= member.size <= MAX_MEMBER, 'replay tar member differs')
            names.add(name)
            total += member.size
            need(total <= MAX_TAR and (member.isfile() or member.isdir() or member.issym()), 'replay tar special member refused')
            if member.issym():
                link = member.linkname
                need(link and not link.startswith('/') and not any(c in link for c in ('\\', '\x00', '\n', '\r')), 'replay tar link refused')
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
                need(target == 'probe' or target.startswith('probe/'), 'replay tar link leaves root')
                need(not name.startswith(prefix_npm) and not name.startswith(prefix_prettier), 'pinned tool tree link refused')
                links.add(name)
                continue
            if not member.isfile():
                continue
            if name in capture:
                need(member.size <= 16 * 1024**2, 'captured member too large')
                raw = archive.extractfile(member).read(member.size + 1)
                need(len(raw) == member.size, 'replay tar captured member truncated')
                small[name] = raw
            elif name.startswith(prefix_npm) or name.startswith(prefix_prettier) or name == 'probe/source-replay/bin/node':
                source = archive.extractfile(member)
                need(source is not None, 'replay tar member unreadable')
                digest = stream_sha(source)
                if name.startswith(prefix_npm):
                    npm.append((name[len(prefix_npm):], digest))
                elif name.startswith(prefix_prettier):
                    prettier.append((name[len(prefix_prettier):], digest))
                else:
                    files[name] = digest
    while reader.read(1024 * 1024):
        pass
    for name in names:
        parts = name.split('/')
        need(not any('/'.join(parts[:i]) in links for i in range(1, len(parts))), 'replay tar link parent refused')
    need(set(small) == capture, 'replay tar captures missing')
    need(files.get('probe/source-replay/bin/node') == NODE_SHA, 'replayed node differs')
    need(tree_result(npm) == NPM_TREE and tree_result(prettier) == PRETTIER_TREE, 'replayed npm or prettier tree differs')
    return small, reader.digest.hexdigest(), reader.bytes

def receipts(raw, report):
    names = ['glibc-version', 'node-version', 'npm-version', 'node-elf', 'node-elf-versions', 'npm-ci-offline', 'prettier-version']
    root = '/home/runner/work/_temp/som-react-linux-qa-closure'
    node = root + '/node/bin/node'
    npm = root + '/node/lib/node_modules/npm/bin/npm-cli.js'
    expected = [['/usr/bin/getconf', 'GNU_LIBC_VERSION'], [node, '--version'], [node, npm, '--version'],
                ['/usr/bin/readelf', '-d', node], ['/usr/bin/readelf', '--version-info', node],
                [node, npm, 'ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'],
                [node, root + '/probe/product/node_modules/prettier/bin/prettier.cjs', '--version']]
    records = report.get('commands')
    need(isinstance(records, list) and [x.get('name') for x in records] == names, 'command sequence differs')
    stdout = {}
    for row, argv in zip(records, expected):
        need(row.get('argv') == argv and row.get('outcome') == 'exited' and row.get('exit_code') == 0, 'command execution receipt differs')
        for suffix in ('stdout', 'stderr'):
            data = raw['probe/' + row['name'] + '.' + suffix]
            need(sha(data) == row.get(suffix + '_sha256'), 'command output differs')
            if suffix == 'stdout':
                stdout[row['name']] = data.decode('utf-8', 'strict')
    need(stdout['node-version'].strip() == report.get('node_version') == 'v26.5.0', 'node version differs')
    need(stdout['npm-version'].strip() == report.get('npm_version') and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', report['npm_version']), 'npm version differs')
    need(stdout['prettier-version'].strip() == report.get('prettier_version') == '3.9.10', 'prettier version differs')
    need(stdout['glibc-version'].strip() == report.get('glibc') and re.fullmatch(r'glibc [0-9]+\.[0-9]+', report['glibc']), 'glibc version differs')
    need('(NEEDED)' in stdout['node-elf'] and not re.search(r'\(NEEDED\).*libnode', stdout['node-elf'], re.I)
         and 'GLIBC_' in stdout['node-elf-versions'], 'ELF observations differ')
    return len(records)

def runner_metadata(report):
    expected = {'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64', 'ImageOS': 'ubuntu22',
                'ImageVersion': '20261025.340.1', 'GITHUB_RUN_ID': str(RUN), 'GITHUB_RUN_ATTEMPT': '1'}
    observed = report.get('runner')
    if observed != expected:
        need(isinstance(observed, dict), 'runner metadata is not an object')
        missing = sorted(set(expected) - set(observed))
        extra = len(set(observed) - set(expected))
        need(not missing and extra == 0,
             'runner fields differ; missing=' + json.dumps(missing, ensure_ascii=True) + '; extra_count=' + str(extra))
        for key, value in expected.items():
            actual = observed[key]
            if actual != value:
                if isinstance(actual, str):
                    safe = json.dumps(actual[:160], ensure_ascii=True)
                    if len(actual) > 160:
                        safe += ' (truncated)'
                elif actual is None or type(actual) in (int, float, bool):
                    safe = json.dumps(actual, ensure_ascii=True, allow_nan=False)
                else:
                    safe = '<' + type(actual).__name__ + '>'
                raise AuditError('runner.' + key + ' differs; actual=' + safe)
        raise AuditError('runner metadata differs')
    digest = report.get('os_release_sha256')
    need(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'runner os_release_sha256 invalid')

def audit(zip_path, run, jobs, artifact, job_log):
    log_sha = metadata(run, jobs, artifact, job_log)
    need(zip_path.stat().st_size == ZIP_BYTES and file_sha(zip_path) == ZIP_SHA, 'replay ZIP differs')
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        need(len(infos) == len(EXPECTED_ZIP) and {x.filename for x in infos} == EXPECTED_ZIP, 'replay ZIP inventory differs')
        idx = {item.filename: item for item in infos}
        need(all(not x.is_dir() and not x.flag_bits & 1 for x in infos) and idx[TAR_NAME].file_size <= MAX_TAR, 'replay ZIP member refused')
        def read(name):
            need(idx[name].file_size <= 16 * 1024**2, 'replay ZIP receipt too large')
            return archive.read(idx[name])
        side = read(TAR_NAME + '.sha256').decode('ascii', 'strict')
        match = re.fullmatch(r'([0-9a-f]{64})  [^\n]*react-ubuntu22-replay\.tar\n', side)
        need(match is not None, 'replay TAR sidecar differs')
        source_audit = json_strict(read('react-ubuntu22-source-audit.json'))
        restore = json_strict(read('som-react-linux-qa-closure/restore-receipt.json'))
        producer_raw = read('som-react-linux-qa-closure/producer-report.json')
        producer = json_strict(producer_raw)
        producer_run = json_strict(read('react-producer-run.json'))
        original_audit_run = json_strict(read('react-audit-run.json'))
        producer_artifact = json_strict(read('react-producer-artifact.json'))
        need(producer_run.get('id') == 40817465924 and producer_run.get('head_sha') == PRODUCER and producer_run.get('conclusion') == 'success'
             and producer_run.get('status') == 'completed', 'producer run differs')
        need(original_audit_run.get('id') == 40818025549 and original_audit_run.get('head_sha') == '34aca02762745daa2e6d42b588ae54c80a2c7df8'
             and original_audit_run.get('status') == 'completed' and original_audit_run.get('conclusion') == 'success', 'source audit run differs')
        need(producer_artifact.get('id') == 12873066929 and producer_artifact.get('digest') == 'sha256:' + SOURCE_ZIP
             and producer_artifact.get('size_in_bytes') == 523384472 and producer_artifact.get('expired') is False
             and producer_artifact.get('workflow_run', {}).get('id') == 40817465924
             and producer_artifact.get('workflow_run', {}).get('head_sha') == PRODUCER, 'producer artifact differs')
        need(restore == {'state': 'public-source-restored', 'producer_head': PRODUCER, 'source_tar_sha256': SOURCE_TAR,
                         'artifact_id': 12873066929, 'run_id': 40817465924, 'product_acceptance_passed': False}, 'restore receipt differs')
        need(source_audit.get('version') == 'som.react-linux-independent-audit.v1'
             and source_audit.get('state') == 'partial-evidence-consistent' and source_audit.get('product_acceptance_passed') is False
             and source_audit.get('source_commit') == PRODUCER and source_audit.get('tar_sha256') == SOURCE_TAR
             and source_audit.get('package_count') == 123 and source_audit.get('node_license_count') == 152
             and source_audit.get('cache_tree') == CACHE_TREE and source_audit.get('npm_tree') == NPM_TREE
             and source_audit.get('prettier_tree') == PRETTIER_TREE, 'source audit receipt differs')
        need(producer.get('version') == 'som.react-linux-qa-offline-probe.v1'
             and producer.get('state') == 'offline-reference-install-observed' and producer.get('product_acceptance_passed') is False
             and producer.get('cache_tree') == CACHE_TREE and producer.get('npm_tree') == NPM_TREE, 'producer receipt differs')
        with archive.open(idx[TAR_NAME]) as tar_stream:
            raw, tar_sha, tar_size = inspect_tar(tar_stream)
        need(tar_sha == match.group(1) and tar_size == idx[TAR_NAME].file_size, 'replay TAR differs')
    report = json_strict(raw['probe/report.json'])
    need(report.get('version') == 'som.react-linux-qa-ubuntu22-replay.v1' and report.get('state') == 'offline-reference-install-observed'
         and report.get('product_acceptance_passed') is False and report.get('producer_head') == PRODUCER and report.get('verifier_head') == HEAD
         and report.get('source_tar_sha256') == SOURCE_TAR and report.get('producer_report_sha256') == sha(producer_raw), 'replay report differs')
    runner_metadata(report)
    need(report.get('node_sha256') == NODE_SHA and report.get('node_archive_sha256') == NODE_ARCHIVE
         and report.get('npm_tree') == NPM_TREE and report.get('cache_tree') == CACHE_TREE
         and report.get('prettier_tree') == PRETTIER_TREE and report.get('react_bundle_formatter_sha256') == FORMATTER
         and report.get('node_version') == producer.get('node_version') and report.get('npm_version') == producer.get('npm_version')
         and report.get('prettier_version') == producer.get('prettier_version'), 'tool report differs')
    before, after = json_strict(raw['probe/cache-before.json']), json_strict(raw['probe/cache-after.json'])
    need(cache_snapshot(before) == CACHE_TREE and cache_snapshot(after) == CACHE_TREE and before == after, 'recorded cache snapshots differ')
    need(sha(raw['probe/product/package.json']) == PACKAGE_SHA and sha(raw['probe/product/package-lock.json']) == LOCK_SHA, 'reference files differ')
    for name, version in (('@rolldown/binding-linux-x64-gnu', '1.2.15'), ('lightningcss-linux-x64-gnu', '1.33.0')):
        data = json_strict(raw['probe/product/node_modules/' + name + '/package.json'])
        need(data.get('name') == name and data.get('version') == version, 'platform package metadata differs')
    count = receipts(raw, report)
    formula = {'node': NODE_SHA, 'libnode': None, 'prettier_tree_sha256': PRETTIER_TREE['sha256']}
    canonical = json.dumps(formula, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    need(report.get('qa_formatter_formula') == formula and report.get('qa_formatter_sha256') == sha(canonical), 'QA formatter identity differs')
    return {'version': 'som.react-ubuntu22-independent-audit.v1', 'state': 'partial-evidence-consistent',
            'product_acceptance_passed': False, 'run_id': RUN, 'job_id': JOB, 'head': HEAD, 'artifact_id': ARTIFACT,
            'zip_sha256': ZIP_SHA, 'replay_tar_sha256': tar_sha, 'source_tar_sha256': SOURCE_TAR, 'job_log_sha256': log_sha,
            'cache_tree': CACHE_TREE, 'npm_tree': NPM_TREE, 'prettier_tree': PRETTIER_TREE, 'commands': count,
            'unverified': ['The original OS release bytes were not retained; runner image is corroborated by the GitHub job log and recorded runner metadata.',
                           'The two cache snapshots are recorder observations. This audit checks their complete maps and hashes; it did not independently observe the original execution.',
                           'The replay retains source-replayed Node/npm and installed Prettier; the original source archive and cache are tied through prior audit receipts, not re-read here.',
                           'Only platform package metadata is checked; other installed package bytes are not independently compared.',
                           'Command environment and operating-system network isolation were not independently observed.',
                           'This unsealed reference install is not product acceptance, release, or default-switch evidence.']}

def main():
    parser = argparse.ArgumentParser()
    for name in ('zip', 'run-json', 'jobs-json', 'artifact-json', 'job-log', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Linux' and platform.machine() == 'x86_64', 'GitHub hosted Linux required')
    temp = Path(os.environ['RUNNER_TEMP']).resolve()
    need(args.output.is_absolute() and args.output.parent == temp and not args.output.exists() and not args.output.is_symlink(), 'audit output refused')
    try:
        for item in (args.zip, args.run_json, args.jobs_json, args.artifact_json, args.job_log):
            need(item.is_absolute() and item.parent == temp and item.is_file() and not item.is_symlink(), 'audit input path refused')
        need(all(item.stat().st_size <= 32 * 1024**2 for item in (args.run_json, args.jobs_json, args.artifact_json, args.job_log)),
             'audit metadata exceeds bound')
        output = audit(args.zip, json_strict(args.run_json.read_bytes()), json_strict(args.jobs_json.read_bytes()),
                       json_strict(args.artifact_json.read_bytes()), args.job_log.read_bytes())
    except Exception as exc:
        output = {'version': 'som.react-ubuntu22-independent-audit.v1', 'state': 'failed', 'product_acceptance_passed': False,
                  'failure_type': type(exc).__name__}
        if isinstance(exc, AuditError):
            output['failure_reason'] = str(exc)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(output, stream, sort_keys=True, indent=2)
        stream.write('\n')
    print(json.dumps(output, sort_keys=True))
    return 0 if output['state'] == 'partial-evidence-consistent' else 1

if __name__ == '__main__':
    raise SystemExit(main())
