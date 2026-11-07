"""Read fixed Python backend source data. This module grants no runtime authority.

Old source is read as Git data and is never imported or executed.
The current selection is actual HEAD plus matching physical source files.
It does not authenticate a build, binary, process, tool, or native caller.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import subprocess
from types import MappingProxyType

BEFORE = '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af'
COMPILER = 'som-rs/src/compiler.rs'
START = 'fn backend_manifest() -> Value {'
END = '\nfn formatter_config()'
REGIONS = {
    'before': ('7aa0082f7742f4e4bae4cf1810c9bc75fce3e5cf927b48ee513e8d072312d098', 27),
    'current': ('2bb6c3aa9ad0e45bdb39bf11bfdfd0acb1ba4577b2a5e4b3755358959b09eb93', 29),
}
ALIASES = {
    ('composed', 'CONTRACT_TEXT'), ('composed', 'CATALOG_TEXT'),
    ('flow', 'CONTRACT_TEXT'), ('flow', 'CATALOG_TEXT'), ('flow', 'COMPACT_CATALOG_TEXT'),
    ('quality', 'PROFILE_TEXT'), ('quality', 'CATALOG_TEXT'),
    ('flow_compact', 'SUPPORT_TEXT'), ('flow_compact', 'ENGINE_TEXT'),
}
MAX_BLOB = 4 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
GIT = '/usr/bin/git'


def _need(value, message):
    if not value:
        raise ValueError(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _oid(kind, raw):
    return hashlib.sha1(kind.encode() + b' ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _path(name):
    _need(type(name) is str and re.fullmatch(r'[A-Za-z0-9_./-]+', name)
          and not name.startswith('/') and all(p not in ('', '.', '..') for p in name.split('/')),
          'repository source path differs')
    return name


def _no_links(path):
    for item in (path, *path.parents):
        _need(not item.is_symlink(), 'physical source path has a symbolic link')


def _git(root, *args, limit=MAX_BLOB):
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_NO_LAZY_FETCH': '1', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    try:
        result = subprocess.run([GIT, '--no-replace-objects', '-C', str(root), *args],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('fixed read-only Git command did not complete') from error
    _need(len(result.stdout) <= limit and len(result.stderr) <= 16384,
          'Git data output exceeds the fixed bound')
    _need(result.returncode == 0, 'required immutable Git data is unavailable')
    return result.stdout


def _repository(repository):
    _need(type(repository) is type(Path()) and repository.is_absolute(), 'absolute physical repository required')
    _no_links(repository)
    _need(repository.is_dir(), 'physical repository directory required')
    _need(_git(repository, 'rev-parse', '--is-inside-work-tree', limit=128) == b'true\n',
          'ordinary Git worktree required')
    top = _git(repository, 'rev-parse', '--show-toplevel', limit=4096).decode('utf-8', 'strict').rstrip('\n')
    _need(top == str(repository), 'repository must be its exact worktree root')
    return repository


def _object(root, kind, oid):
    _need(type(oid) is str and re.fullmatch('[0-9a-f]{40}', oid), 'full Git object identity required')
    _need(_git(root, 'cat-file', '-t', oid, limit=128) == kind.encode() + b'\n', 'Git object type differs')
    size = _git(root, 'cat-file', '-s', oid, limit=128)
    _need(re.fullmatch(rb'[0-9]+\n', size) and int(size) <= MAX_BLOB, 'Git object exceeds source bound')
    raw = _git(root, 'cat-file', kind, oid)
    _need(len(raw) == int(size) and _oid(kind, raw) == oid, 'complete Git object hash differs')
    return raw


def _commit(root, revision):
    raw = _object(root, 'commit', revision)
    first = raw.split(b'\n', 1)[0]
    _need(re.fullmatch(rb'tree [0-9a-f]{40}', first), 'commit tree identity differs')
    tree = first[5:].decode('ascii')
    _object(root, 'tree', tree)
    return tree


def _git_blob(root, revision, name):
    _path(name)
    tree_oid = _commit(root, revision)
    parts = name.split('/')
    for index, part in enumerate(parts):
        tree = _object(root, 'tree', tree_oid)
        entries, at = {}, 0
        while at < len(tree):
            space = tree.index(b' ', at) if b' ' in tree[at:] else -1
            nul = tree.index(b'\0', space + 1) if space >= 0 and b'\0' in tree[space + 1:] else -1
            _need(space > at and nul > space + 1 and nul + 21 <= len(tree), 'Git tree record is incomplete')
            mode, filename = tree[at:space], tree[space + 1:nul]
            _need(filename not in entries and b'/' not in filename and filename not in (b'.', b'..'),
                  'Git tree names are invalid or repeated')
            entries[filename] = (mode, tree[nul + 1:nul + 21].hex())
            at = nul + 21
        _need(part.encode() in entries, 'exact source tree entry is missing or ambiguous')
        mode, oid = entries[part.encode()]
        if index < len(parts) - 1:
            _need(mode == b'40000', 'source parent is not an actual Git tree')
            tree_oid = oid
        else:
            _need(mode in (b'100644', b'100755'), 'regular source Git blob and mode required')
    data = _object(root, 'blob', oid)
    data.decode('utf-8', 'strict')
    return data, {'path': name, 'blob_oid': oid, 'mode': mode.decode('ascii'),
                  'bytes': len(data), 'sha256': _sha(data)}


def _resolve_include(owner, include):
    _path(owner)
    _need(type(include) is str and re.fullmatch(r'[A-Za-z0-9_./-]+', include)
          and not include.startswith('/') and '//' not in include, 'literal include path required')
    name = posixpath.normpath(posixpath.join(posixpath.dirname(owner), include))
    return _path(name)


def _tokens(text):
    """Lex strings and comments before inspecting a finite top-level constant."""
    result, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text.startswith('//', i):
            tail = text.index('\n', i) if '\n' in text[i:] else len(text)
            i = tail
            continue
        if text.startswith('/*', i):
            i, depth = i + 2, 1
            while depth:
                _need(i < len(text), 'unterminated Rust comment')
                if text.startswith('/*', i): depth, i = depth + 1, i + 2
                elif text.startswith('*/', i): depth, i = depth - 1, i + 2
                else: i += 1
            continue
        raw = re.match(r'(?:br|cr|r)(#*)"', text[i:])
        if raw:
            end = '"' + raw[1]
            begin = i + len(raw[0])
            at = text.index(end, begin) if end in text[begin:] else -1
            _need(at >= 0, 'unterminated Rust raw string')
            result.append(text[i:at + len(end)])
            i = at + len(end)
            continue
        ordinary = re.match(r'(?:b|c)?"', text[i:])
        if ordinary:
            begin = i
            i += len(ordinary[0])
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == '\\' else 1
            _need(i < len(text), 'unterminated Rust string')
            i += 1
            result.append(text[begin:i])
            continue
        char = re.match(r"(?:b)?'(?:\\(?:u\{[0-9a-fA-F_]+\}|x[0-9a-fA-F]{2}|.)|[^'\\\n])'", text[i:])
        if char:
            result.append(char[0]); i += len(char[0]); continue
        word = re.match(r'[A-Za-z_][A-Za-z_0-9]*', text[i:])
        if word:
            result.append(word[0]); i += len(word[0]); continue
        _need(ord(text[i]) >= 32 and text[i] != '\x7f', 'Rust source control byte refused')
        result.append(text[i]); i += 1
    return result


def _resolve_alias(module, constant, source):
    _need((module, constant) in ALIASES, 'unreviewed backend alias')
    tokens, depth, matches = _tokens(source.decode('utf-8', 'strict')), 0, []
    for i, token in enumerate(tokens):
        if token == '{': depth += 1
        elif token == '}':
            depth -= 1
            _need(depth >= 0, 'unbalanced Rust source braces')
        elif depth == 0 and token == 'const' and tokens[i + 1:i + 2] == [constant]:
            _need(i > 0 and tokens[i - 1] == 'pub', 'backend alias visibility differs')
            tail = tokens[i - 1:i + 12]
            _need(len(tail) == 13 and tail[:10] == ['pub', 'const', constant, ':', '&', 'str', '=', 'include_str', '!', '(']
                  and tail[11:] == [')', ';'], 'literal original include alias required')
            literal = tail[10]
            _need(re.fullmatch(r'"[A-Za-z0-9_./-]+"', literal), 'escaped or computed alias include refused')
            matches.append(_resolve_include('som-rs/src/' + module + '.rs', literal[1:-1]))
    _need(depth == 0 and len(matches) == 1, 'backend alias declaration is missing or ambiguous')
    return matches[0]


def _factory_entries(compiler, side):
    text = compiler.decode('utf-8', 'strict')
    _need(text.count(START) == 1 and END in text.split(START, 1)[1], 'backend factory boundaries differ')
    region = START + text.split(START, 1)[1].split(END, 1)[0]
    region_hash, count = REGIONS[side]
    _need(_sha(region.encode()) == region_hash, 'backend factory changed; explicit source review required')
    table_start, table_end = 'let files = [', '\n    ];'
    _need(region.count(table_start) == region.count(table_end) == 1, 'complete backend table differs')
    table = region.split(table_start, 1)[1].split(table_end, 1)[0]
    row = re.compile(r'\s*\(\s*"([A-Za-z0-9_./-]+)"\s*,\s*(?:include_str!\(\s*"([A-Za-z0-9_./-]+)"\s*\)|([a-z_][a-z_0-9]*)::([A-Z_0-9]+))\s*,?\s*\)\s*,')
    entries, at = [], 0
    while table[at:].strip():
        match = row.match(table, at)
        _need(match is not None, 'unconsumed backend table syntax')
        entries.append(match.groups()); at = match.end()
    _need(len(entries) == count and len({x[0] for x in entries}) == count, 'complete unique backend vector required')
    return entries, region_hash


def _physical_read(root, name, mode):
    path = root / _path(name)
    _no_links(path)
    before = path.lstat()
    _need(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) == int(mode[-3:], 8)
          and before.st_size <= MAX_BLOB, 'physical source mode/type/size differs')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno()); raw = stream.read(MAX_BLOB + 1); after = os.fstat(stream.fileno())
    end = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_mtime_ns, s.st_ctime_ns)
    _need(identity(before) == identity(opened) == identity(after) == identity(end)
          and len(raw) == before.st_size, 'physical source changed during read')
    return raw


def _read(root, revision, side):
    tree = _commit(root, revision)
    cache = {}
    def source(name):
        if name not in cache:
            cache[name] = _git_blob(root, revision, name)
            if side == 'current':
                raw, row = cache[name]
                _need(_physical_read(root, name, row['mode']) == raw,
                      'current physical source differs from actual HEAD')
        return cache[name]
    compiler, factory = source(COMPILER)
    pairs, region_hash = _factory_entries(compiler, side)
    entries, paths = [], set()
    for logical, direct, module, constant in pairs:
        _path(logical)
        if direct:
            name = _resolve_include(COMPILER, direct)
        else:
            owner = 'som-rs/src/' + module + '.rs'
            name = _resolve_alias(module, constant, source(owner)[0])
        _need(name not in paths, 'backend source mapping is repeated')
        paths.add(name)
        _raw, row = source(name)
        entries.append({'logical': logical, **row})
    _need(sum(len(value[0]) for value in cache.values()) <= MAX_TOTAL, 'backend source universe exceeds bound')
    if side == 'current':
        _need(_git(root, 'rev-parse', '--verify', 'HEAD', limit=128).decode().strip() == revision,
              'current HEAD changed during source read')
        for name, (raw, row) in cache.items():
            _need(_physical_read(root, name, row['mode']) == raw, 'current source changed during final recheck')
        _need(_git(root, 'rev-parse', '--verify', 'HEAD', limit=128).decode().strip() == revision,
              'current HEAD changed during final physical recheck')
    manifest = json.dumps({'version': 'som.compiler-backend.v1',
        'files': {row['logical']: row['sha256'] for row in entries}},
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return _freeze({'version': 'som.python-backend-source-data.v1', 'side': side,
        'revision': revision, 'tree_oid': tree, 'factory': factory, 'factory_region_sha256': region_hash,
        'entries': entries, 'manifest_bytes': manifest, 'backend_sha256': _sha(manifest),
        'physical_current_sources_checked': side == 'current', 'data_only': True,
        'native_authority': False, 'SourceSeal': False, 'product_acceptance_passed': False})


def read_before_backend(repository):
    """Read complete source data from the fixed historical commit, without execution."""
    return _read(_repository(repository), BEFORE, 'before')


def read_current_backend(repository, selected_revision=None):
    """Read actual HEAD and physical sources. This does not authenticate a candidate."""
    root = _repository(repository)
    revision = _git(root, 'rev-parse', '--verify', 'HEAD', limit=128).decode('ascii').strip()
    _need(re.fullmatch('[0-9a-f]{40}', revision), 'full actual HEAD required')
    _need(selected_revision is None or type(selected_revision) is str
          and re.fullmatch('[0-9a-f]{40}', selected_revision) and selected_revision == revision,
          'selected current revision differs from actual HEAD')
    return _read(root, revision, 'current')
