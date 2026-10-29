#!/usr/bin/env python3
"""DRAFT GitHub Linux QA cache feasibility probe; never acceptance."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import prepare_public as source

NODE_SHA = '3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538'
PRETTIER_SHA = 'f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299'
LOCK_SHA = 'c6f3856e1a620d78d4357ef54f89488612a5c1606bbf0219265fc984334b5814'
PACKAGE_SHA = 'ba9cda19860b88d13f319bc8762c03ba738c81e8aa32158f35a6864b6c1d8230'
REF_FORMATTER = '636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8'
PRODUCER_HEAD = '62637173ffeecde2655482377a218b22bf54618a'
PRODUCER_TAR_SHA = '5f453d9e3f4fc17e36c9c16d17dacc157a34915fc7c9e477f49da3e81b807171'

class Refusal(ValueError):
    pass

def need(ok, why):
    if not ok:
        raise Refusal(why)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def digest_file(path):
    need(not path.is_symlink() and path.is_file(), 'file missing or symlink')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def tree(root, allow_symlinks):
    need(root.is_dir() and not root.is_symlink(), 'tree root missing or symlink')
    lines, files, links = [], 0, 0
    def walk_error(error):
        raise Refusal('tree scan failed') from error
    for directory, dirs, names in os.walk(root, topdown=True, followlinks=False, onerror=walk_error):
        directory = Path(directory)
        for name in sorted(dirs + names):
            p = directory / name
            rel = p.relative_to(root).as_posix()
            need('\n' not in rel and '\x00' not in rel and '\\' not in rel, 'invalid tree path')
            if p.is_symlink():
                need(allow_symlinks, 'tree symlink refused')
                target = os.readlink(p)
                need('\n' not in target and '\x00' not in target and not os.path.isabs(target)
                     and p.resolve().is_relative_to(root.resolve()) and p.exists(), 'tree link refused')
                lines.append((rel, f'{rel}\0symlink:{target}\n'))
                links += 1
            elif p.is_file():
                lines.append((rel, f'{rel}\0{digest_file(p)}\n'))
                files += 1
            else:
                need(p.is_dir(), 'tree special file refused')
    lines.sort(key=lambda item: item[0])
    return {'sha256': sha(''.join(line for _, line in lines).encode('utf-8')), 'files': files, 'symlinks': links}

def run(argv, env, cwd, output_dir, name, commands):
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=600, check=False)
        stdout, stderr, code, outcome = result.stdout, result.stderr, result.returncode, 'exited'
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code, outcome = exc.stdout or b'', exc.stderr or b'', None, 'timed_out'
    except OSError:
        stdout, stderr, code, outcome = b'', b'', None, 'spawn_failed'
    for suffix, data in (('stdout', stdout), ('stderr', stderr)):
        with (output_dir / (name + '.' + suffix)).open('xb') as stream:
            stream.write(data)
    receipt = {'name': name, 'argv': argv, 'outcome': outcome, 'exit_code': code,
               'stdout_sha256': sha(stdout), 'stderr_sha256': sha(stderr)}
    commands.append(receipt)
    need(outcome == 'exited' and code == 0, 'QA command failed or did not finish')
    return stdout

def protect_tree(root):
    need(root.is_dir() and not root.is_symlink(), 'cache missing')
    def walk_error(error):
        raise Refusal('cache scan failed') from error
    for directory, dirs, files in os.walk(root, topdown=False, followlinks=False, onerror=walk_error):
        for name in dirs + files:
            p = Path(directory) / name
            need(not p.is_symlink(), 'cache symlink refused')
            mode = stat.S_IMODE(p.stat().st_mode)
            p.chmod(mode & ~0o222)
        p = Path(directory)
        p.chmod(stat.S_IMODE(p.stat().st_mode) & ~0o222)

def cache_snapshot(root):
    need(root.is_dir() and not root.is_symlink(), 'cache missing')
    need(stat.S_IMODE(root.stat().st_mode) & 0o222 == 0, 'cache root is writable')
    entries = {}
    def walk_error(error):
        raise Refusal('cache scan failed') from error
    for directory, dirs, names in os.walk(root, topdown=True, followlinks=False, onerror=walk_error):
        here = Path(directory)
        for name in sorted(dirs + names):
            path = here / name
            rel = path.relative_to(root).as_posix()
            need(not path.is_symlink(), 'cache symlink refused')
            mode = stat.S_IMODE(path.stat().st_mode)
            need(mode & 0o222 == 0, 'cache is writable')
            if path.is_file():
                entries[rel] = {'kind': 'file', 'mode': mode, 'sha256': digest_file(path)}
            else:
                need(path.is_dir(), 'cache special file refused')
                entries[rel] = {'kind': 'directory', 'mode': mode}
    return {'root_mode': stat.S_IMODE(root.stat().st_mode), 'entries': entries}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Linux' and platform.machine() == 'x86_64', 'host refused')
    os_release = Path('/etc/os-release').read_bytes()
    os_fields = dict(line.split('=', 1) for line in os_release.decode('utf-8').splitlines() if '=' in line)
    need(os_fields.get('ID', '').strip('"') == 'ubuntu'
         and os_fields.get('VERSION_ID', '').strip('"') == '22.04', 'Ubuntu 22.04 required')
    repo, root = args.repo, args.root
    temp = Path(os.environ['RUNNER_TEMP']).resolve()
    need(repo.is_absolute() and repo.resolve() == repo and root.is_absolute() and root.resolve() == root
         and root == temp / 'som-react-linux-qa-closure' and not root.is_symlink(), 'root refused')
    output = root / 'probe'
    need(not output.exists() and not output.is_symlink(), 'probe output exists')
    output.mkdir()
    report = {'version': 'som.react-linux-qa-ubuntu22-replay.v1', 'state': 'incomplete',
              'role': 'unsealed same-cache Ubuntu 22.04 observation', 'product_acceptance_passed': False,
              'producer_head': PRODUCER_HEAD, 'verifier_head': os.environ.get('GITHUB_SHA'),
              'runner': {key: os.environ.get(key) for key in ('RUNNER_OS', 'RUNNER_ARCH', 'ImageOS', 'ImageVersion', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')},
              'os_release_sha256': sha(os_release), 'commands': []}
    try:
        verifier = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=30, check=True).stdout.decode('ascii').strip()
        need(re.fullmatch(r'[0-9a-f]{40}', verifier) is not None and verifier == report['verifier_head'], 'verifier head differs')
        node = root / 'node/bin/node'
        npm = root / 'node/lib/node_modules/npm/bin/npm-cli.js'
        npm_root = root / 'node/lib/node_modules/npm'
        cache = root / 'npm-cache-new'
        manifest_raw = (root / 'public-inputs.json').read_bytes()
        manifest = source.parse(manifest_raw)
        seed_raw = (root / 'seed-report.json').read_bytes()
        seed = source.parse(seed_raw)
        producer_raw = (root / 'producer-report.json').read_bytes()
        producer = source.parse(producer_raw)
        restore_raw = (root / 'restore-receipt.json').read_bytes()
        restored = source.parse(restore_raw)
        need(manifest['version'] == 'som.react-linux-qa-public-inputs.v1'
             and manifest['source_commit'] == PRODUCER_HEAD and manifest['product_acceptance_passed'] is False
             and seed['version'] == 'som.react-linux-qa-cache-seed.v1' and seed['product_acceptance_passed'] is False
             and seed['manifest_sha256'] == sha(manifest_raw) and seed['lock_sha256'] == LOCK_SHA
             and len(manifest['packages']) == len(seed['entries']) == 123
             and producer['version'] == 'som.react-linux-qa-offline-probe.v1'
             and producer['state'] == 'offline-reference-install-observed'
             and producer['product_acceptance_passed'] is False
             and restored == {'state': 'public-source-restored', 'producer_head': PRODUCER_HEAD,
                              'source_tar_sha256': PRODUCER_TAR_SHA, 'artifact_id': 12873066929,
                              'run_id': 40817465924, 'product_acceptance_passed': False}, 'seed identity differs')
        need(digest_file(node) == NODE_SHA and digest_file(npm) == manifest['node']['npm_cli_sha256'], 'node/npm differs')
        npm_before = tree(npm_root, True)
        lock = repo / 'som-rs/contracts/react-output-v1/reference/package-lock.json'
        package = repo / 'som-rs/contracts/react-output-v1/reference/package.json'
        need(digest_file(lock) == LOCK_SHA and digest_file(package) == PACKAGE_SHA, 'frozen product input differs')
        rows = source.lock_rows(source.parse(lock.read_bytes()))
        manifest_rows = {row['path']: row for row in manifest['packages']}
        seed_rows = {row['path']: row for row in seed['entries']}
        need(len(manifest_rows) == len(seed_rows) == len(rows), 'package paths repeated')
        for row in rows:
            observed, seeded = manifest_rows[row['path']], seed_rows[row['path']]
            need(all(observed.get(key) == value for key, value in row.items()), 'public package binding differs')
            need(all(seeded.get(key) == observed[key] for key in ('path', 'url', 'integrity', 'sha256')),
                 'seed package binding differs')
            name = observed['file']
            need(name == 'tarballs/' + observed['sha256'] + '.tgz' and re.fullmatch(r'tarballs/[0-9a-f]{64}\.tgz', name),
                 'package path differs')
            tarball = root / name
            need(digest_file(tarball) == observed['sha256'], 'package bytes differ')
            raw = tarball.read_bytes()
            need(len(raw) == observed['bytes'] and 'sha512-' + base64.b64encode(hashlib.sha512(raw).digest()).decode('ascii') == row['integrity'],
                 'package integrity differs')
        artifacts_path = repo / source.ARTIFACT_PATH
        need(digest_file(artifacts_path) == source.PINS[source.ARTIFACT_PATH], 'native source differs')
        artifacts = source.parse(artifacts_path.read_bytes())
        node_rows = [x for x in artifacts['tools'] if x.get('tool') == 'node' and x.get('host') == 'x86_64-unknown-linux-gnu']
        need(len(node_rows) == 1, 'native node source differs')
        archive = root / 'node-archive.tar.xz'
        need(digest_file(archive) == source.NODE_ARCHIVE, 'node archive differs')
        extracted = output / 'source-replay'
        extracted.mkdir()
        node_replay = source.extract_node(archive.read_bytes(), node_rows[0], extracted)
        need(node_replay == manifest['node'] and digest_file(extracted / 'bin/node') == NODE_SHA
             and tree(extracted / 'lib/node_modules/npm', True) == npm_before, 'npm archive replay differs')
        cache_before = tree(cache, False)
        need(cache_before == producer['cache_tree'], 'producer cache tree differs')
        snapshot_before = cache_snapshot(cache)
        (output / 'cache-before.json').write_text(json.dumps(snapshot_before, sort_keys=True, indent=2) + '\n')
        protect_tree(cache)
        need(tree(cache, False) == cache_before and cache_snapshot(cache) == snapshot_before, 'cache changed while making read-only')
        home, logs, product = output / 'home', output / 'logs', output / 'product'
        for folder in (home, logs, product):
            folder.mkdir()
        userconfig = output / 'npmrc'
        userconfig.write_bytes(b'')
        shutil.copyfile(package, product / 'package.json')
        shutil.copyfile(lock, product / 'package-lock.json')
        env = {'HOME': str(home), 'PATH': str(node.parent) + ':/usr/bin:/bin', 'LANG': 'C.UTF-8',
               'npm_config_cache': str(cache), 'npm_config_offline': 'true', 'npm_config_audit': 'false',
               'npm_config_fund': 'false', 'npm_config_update_notifier': 'false', 'npm_config_progress': 'false',
               'npm_config_ignore_scripts': 'true', 'npm_config_userconfig': str(userconfig),
               'npm_config_logs_dir': str(logs)}
        glibc = run(['/usr/bin/getconf', 'GNU_LIBC_VERSION'], env, product, output, 'glibc-version', report['commands']).decode('utf-8', 'strict').strip()
        need(re.fullmatch(r'glibc [0-9]+\.[0-9]+', glibc) is not None, 'glibc version invalid')
        node_version = run([str(node), '--version'], env, product, output, 'node-version', report['commands']).decode('utf-8', 'strict').strip()
        need(node_version == 'v26.5.0', 'node version differs')
        npm_version = run([str(node), str(npm), '--version'], env, product, output, 'npm-version', report['commands']).decode('utf-8', 'strict').strip()
        need(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', npm_version) is not None, 'npm version invalid')
        elf = run(['/usr/bin/readelf', '-d', str(node)], env, product, output, 'node-elf', report['commands']).decode('utf-8', 'strict')
        need(re.search(r'\(NEEDED\).*libnode', elf, re.I) is None, 'node uses separate libnode')
        run(['/usr/bin/readelf', '--version-info', str(node)], env, product, output, 'node-elf-versions', report['commands'])
        run([str(node), str(npm), 'ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'],
            env, product, output, 'npm-ci-offline', report['commands'])
        need(digest_file(product / 'package.json') == PACKAGE_SHA and digest_file(product / 'package-lock.json') == LOCK_SHA,
             'frozen product files changed')
        need({p.name for p in product.iterdir()} == {'package.json', 'package-lock.json', 'node_modules'}, 'unexpected product root')
        installed = product / 'node_modules'
        prettier = installed / 'prettier'
        prettier_tree = tree(prettier, False)
        need(prettier_tree == {'sha256': PRETTIER_SHA, 'files': 56, 'symlinks': 0}, 'prettier tree differs')
        prettier_version = run([str(node), str(prettier / 'bin/prettier.cjs'), '--version'], env, product,
                               output, 'prettier-version', report['commands']).decode('utf-8', 'strict').strip()
        need(prettier_version == '3.9.10', 'prettier version differs')
        for package_name, expected_version in (('@rolldown/binding-linux-x64-gnu', '1.2.15'), ('lightningcss-linux-x64-gnu', '1.33.0')):
            doc_path = installed / package_name / 'package.json'
            need(not doc_path.is_symlink() and doc_path.is_file(), 'Linux platform package missing')
            doc = source.parse(doc_path.read_bytes())
            need(doc.get('name') == package_name and doc.get('version') == expected_version, 'Linux platform package differs')
        cache_after = tree(cache, False)
        snapshot_after = cache_snapshot(cache)
        (output / 'cache-after.json').write_text(json.dumps(snapshot_after, sort_keys=True, indent=2) + '\n')
        npm_after = tree(npm_root, True)
        need(cache_before == cache_after and snapshot_before == snapshot_after and npm_before == npm_after, 'cache or npm tree changed')
        need(digest_file(lock) == LOCK_SHA and digest_file(package) == PACKAGE_SHA
             and sha((root / 'public-inputs.json').read_bytes()) == sha(manifest_raw)
             and sha((root / 'seed-report.json').read_bytes()) == sha(seed_raw)
             and sha((root / 'producer-report.json').read_bytes()) == sha(producer_raw)
             and sha((root / 'restore-receipt.json').read_bytes()) == sha(restore_raw), 'source or receipt changed')
        qa_formula = {'node': NODE_SHA, 'libnode': None, 'prettier_tree_sha256': PRETTIER_SHA}
        report.update(state='offline-reference-install-observed', node_version=node_version, npm_version=npm_version, glibc=glibc,
                      prettier_version=prettier_version, node_sha256=NODE_SHA, npm_tree=npm_before,
                      producer_report_sha256=sha(producer_raw), source_tar_sha256=PRODUCER_TAR_SHA,
                      node_archive_sha256=source.NODE_ARCHIVE, node_archive_replay=node_replay,
                      cache_tree=cache_before, prettier_tree=prettier_tree,
                      qa_formatter_formula=qa_formula, qa_formatter_sha256=sha(canonical(qa_formula)),
                      react_bundle_formatter_sha256=REF_FORMATTER,
                      scope='QA tool observation only; no production descriptor or product gates changed')
    except Exception as exc:
        report.update(state='failed', failure_type=type(exc).__name__)
        if isinstance(exc, Refusal):
            report['failure_reason'] = str(exc)
    finally:
        (output / 'report.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'state': report['state'], 'product_acceptance_passed': False}))
    return 0 if report['state'] == 'offline-reference-install-observed' else 1

if __name__ == '__main__':
    raise SystemExit(main())
