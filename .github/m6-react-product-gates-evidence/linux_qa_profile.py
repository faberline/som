"""Unsealed draft of a Linux QA tool profile. This is not product authority.

Before use, the owner must independently review and bind the exact profile bytes
in a numbered contract amendment. The production bundle formatter stays frozen.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import stat

# Deliberately unbound. An accepted receipt must bind the final exact bytes.
PROFILE_SHA256: str | None = None
NODE_SHA256 = '3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538'
NPM_TREE = ('69965a8f2a75cad99018e333ff1819bdc8cbf4f17570e77f24ad5f95aa57e558', 1921, 0)
CACHE_TREE = ('5267851624684b07307609f59d0b13705bd7934a1fd59bde2045463a3ee6ddda', 246, 0)
PRETTIER_TREE = ('f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299', 56, 0)
REFERENCE_FORMATTER = '636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8'
PROFILE = {
    'version': 'som.react-linux-qa-profile.v1',
    'host': 'ubuntu-22.04-x86_64',
    'source': {'commit': '62637173ffeecde2655482377a218b22bf54618a',
               'run': 40817465924, 'artifact': 12873066929,
               'artifact_zip_sha256': '10a7f2bf058be21c670ddac89afece05c305ee717b9a879de4c95256fd6fe2dc',
               'tar_sha256': '5f453d9e3f4fc17e36c9c16d17dacc157a34915fc7c9e477f49da3e81b807171'},
    'node': {'path': 'node/bin/node', 'sha256': NODE_SHA256},
    'npm': {'root': 'node/lib/node_modules/npm', 'sha256': NPM_TREE[0], 'files': NPM_TREE[1], 'symlinks': 0},
    'cache': {'path': 'npm-cache-new', 'sha256': CACHE_TREE[0], 'files': CACHE_TREE[1], 'symlinks': 0},
    'prettier': {'sha256': PRETTIER_TREE[0], 'files': PRETTIER_TREE[1], 'symlinks': 0},
    'reference_formatter_sha256': REFERENCE_FORMATTER,
}

class ProfileError(ValueError):
    pass

def need(condition: bool, reason: str) -> None:
    if not condition:
        raise ProfileError(reason)

def strict_json(raw: bytes) -> object:
    def pairs(items):
        data = {}
        for key, value in items:
            need(key not in data, 'duplicate profile member')
            data[key] = value
        return data
    try:
        return json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ProfileError('invalid JSON value')))
    except (UnicodeError, ValueError) as exc:
        raise ProfileError('invalid profile JSON') from exc

def file_sha(path: Path) -> str:
    need(not path.is_symlink() and path.is_file(), 'profile file missing or linked')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()

def safe_tree(root: Path, readonly: bool) -> tuple[str, int, int]:
    need(not root.is_symlink() and root.is_dir(), 'profile tree missing or linked')
    lines = []
    def scan_error(exc):
        raise ProfileError('profile tree unreadable') from exc
    for directory, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=scan_error):
        parent = Path(directory)
        if readonly:
            need(stat.S_IMODE(parent.stat().st_mode) & 0o222 == 0, 'cache directory writable')
        for name in sorted(dirs + files):
            path = parent / name
            rel = path.relative_to(root).as_posix()
            need(not any(c in rel for c in ('\x00', '\n', '\r', '\\')), 'profile tree name invalid')
            item = path.lstat()
            need(not stat.S_ISLNK(item.st_mode), 'profile tree link refused')
            need(stat.S_ISDIR(item.st_mode) or stat.S_ISREG(item.st_mode), 'profile tree special file refused')
            if readonly:
                need(stat.S_IMODE(item.st_mode) & 0o222 == 0, 'cache member writable')
            if stat.S_ISREG(item.st_mode):
                lines.append((rel, rel + '\0' + file_sha(path) + '\n'))
    lines.sort(key=lambda item: item[0])
    return hashlib.sha256(''.join(x[1] for x in lines).encode('utf-8')).hexdigest(), len(lines), 0

def validate_host() -> None:
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Linux' and platform.machine() == 'x86_64', 'GitHub Linux host required')
    release = Path('/etc/os-release').read_text(encoding='utf-8')
    fields = {}
    for line in release.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            need(key not in fields, 'duplicate host release field')
            fields[key] = value.strip('"')
    need(fields.get('ID') == 'ubuntu' and fields.get('VERSION_ID') == '22.04', 'host version differs')

def validate_profile(path: Path) -> dict:
    need(PROFILE_SHA256 is not None, 'Linux QA profile authority is not sealed')
    validate_host()
    temp = Path(os.environ['RUNNER_TEMP']).resolve()
    root = temp / 'som-react-linux-qa-closure'
    need(root.is_dir() and not root.is_symlink() and root.resolve() == root, 'QA root differs')
    need(path.is_absolute() and not path.is_symlink() and path.parent == temp
         and path.name == 'som-react-linux-qa-profile.json', 'QA profile path differs')
    need(path.is_file() and path.stat().st_size <= 4096, 'QA profile missing or too large')
    raw = path.read_bytes()
    need(len(raw) <= 4096, 'QA profile too large')
    need(hashlib.sha256(raw).hexdigest() == PROFILE_SHA256, 'QA profile authority differs')
    actual = strict_json(raw)
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    need(canonical(actual) == canonical(PROFILE), 'QA profile contents differ')
    node = root / PROFILE['node']['path']
    npm = root / PROFILE['npm']['root']
    cache = root / PROFILE['cache']['path']
    for item in (node, npm, cache):
        need(item.resolve() == item and item.is_relative_to(root), 'QA path linked or outside root')
    need(file_sha(node) == NODE_SHA256 and os.access(node, os.X_OK), 'QA node differs')
    need(safe_tree(npm, False) == NPM_TREE, 'QA npm differs')
    need(safe_tree(cache, True) == CACHE_TREE, 'QA cache differs')
    cli = npm / 'bin/npm-cli.js'
    need(cli.is_file() and not cli.is_symlink(), 'QA npm CLI missing')
    return {'node': str(node), 'npm_cli': str(cli), 'npm_root': npm, 'cache': cache, 'profile_path': path,
            'profile_sha256': PROFILE_SHA256}

def recheck(qa: dict) -> None:
    for item in (Path(qa['node']), qa['npm_root'], qa['cache'], qa['profile_path']):
        need(not item.is_symlink() and item.resolve() == item, 'QA path changed or linked')
    need(file_sha(qa['profile_path']) == qa['profile_sha256'], 'QA profile changed')
    node = Path(qa['node'])
    need(file_sha(node) == NODE_SHA256 and stat.S_IMODE(node.stat().st_mode) & 0o111 != 0
         and os.access(node, os.X_OK), 'QA node changed or is not executable')
    need(safe_tree(qa['npm_root'], False) == NPM_TREE, 'QA npm changed')
    need(safe_tree(qa['cache'], True) == CACHE_TREE, 'QA cache changed')
