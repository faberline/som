"""Unsealed draft: retain source-bound React Linux QA evidence for audit."""
from __future__ import annotations
import hashlib
import json
import os
import re
import sys
import types
from pathlib import Path
import shutil
import stat

TREE_VERSION = 'som.react-qa-tree-inventory.v1'
IDENTITY_VERSION = 'som.react-qa-profile-evidence.v1'
SOURCES = {'run_gates': 'som-rs/tests/react-output-v1/run_gates.py',
           'linux_qa_profile': 'som-rs/tests/react-output-v1/linux_qa_profile.py',
           'retained_evidence': 'som-rs/scripts/react_output_acceptance/retained_evidence.py'}

class EvidenceError(ValueError):
    pass

def need(condition, reason):
    if not condition:
        raise EvidenceError(reason)

def digest(path):
    need(not path.is_symlink() and path.is_file(), 'evidence input missing or linked')
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()

def write_json(path, value, replace=False):
    with path.open('w' if replace else 'x', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')

def read_physical_bytes(path, maximum):
    need(not path.is_symlink(), 'linked file refused')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise EvidenceError('file missing or unsafe') from exc
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= maximum, 'file missing or oversized')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        need(len(raw) <= maximum, 'file oversized')
        return raw
    finally:
        os.close(fd)

def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate evidence JSON member')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError('invalid evidence JSON value')))
    except (UnicodeError, ValueError) as exc:
        raise EvidenceError('invalid evidence JSON') from exc

def read_json(path):
    return parse_json(read_physical_bytes(path, 8 * 1024 * 1024))

def same_value(left, right):
    """Compare JSON types as well as values; False is not the integer zero."""
    def canonical(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return canonical(left) == canonical(right)


def physical_repo_path(repo, path):
    """Reject links and traversal without resolving away unsafe components."""
    repo = Path(repo)
    path = Path(path)
    need(repo.is_absolute() and repo.resolve() == repo, 'repository path is not physical')
    need('..' not in path.parts, 'parent traversal refused')
    path = path if path.is_absolute() else repo / path
    need(path.is_relative_to(repo) and path != repo, 'path must be inside repository')
    need(not any(p.is_symlink() for p in (path, *path.parents)), 'linked path refused')
    return path


def sealed_files(repo):
    """Authenticate the latest sealed receipt and its previous chain."""
    repo = Path(repo)
    root = physical_repo_path(repo, 'som-rs/evidence')
    need(root.is_dir(), 'receipt directory missing')
    pattern = re.compile(r'react-output-contract-amendment-(\d+)\.json\Z')
    sealed = []
    for path in sorted(root.iterdir()):
        match = pattern.fullmatch(path.name)
        if not match:
            continue
        physical_repo_path(repo, path)
        doc = read_json(path)
        if isinstance(doc, dict) and doc.get('status') == 'amendment_sealed_after_independent_review':
            sealed.append((int(match.group(1)), path.name, path, doc))
    need(bool(sealed), 'sealed QA amendment missing')
    number, _, _, current = max(sealed)
    need(number > 0, 'invalid QA amendment number')
    target = current.get('target_contract')
    review = current.get('independent_review')
    need(isinstance(target, dict) and isinstance(review, dict), 'QA review missing')
    rounds = review.get('rounds')
    need(isinstance(rounds, list) and rounds and all(isinstance(x, dict) and type(x.get('round')) is int and x['round'] > 0 for x in rounds), 'QA review rounds missing')
    indexes = [x['round'] for x in rounds]
    last = rounds[-1]
    need(indexes == sorted(set(indexes)) and last.get('verdict') == 'APPROVED' and review.get('target_sha256') == target.get('sha256'), 'QA review does not approve target')
    need(all(isinstance(last.get(k), str) and last[k].strip() for k in ('reviewer', 'evidence', 'head')) and re.fullmatch(r'[0-9a-f]{40}', last['head']) is not None, 'QA review metadata missing')
    latest_files = current.get('files')
    while True:
        files = current.get('files')
        target = current.get('target_contract')
        need(isinstance(files, dict) and files and all(isinstance(k, str) and isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for k, v in files.items()), 'receipt file map invalid')
        canonical = json.dumps(files, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
        need(isinstance(target, dict) and target.get('id') == 'som.react-output-contract.v1' and target.get('sha256') == hashlib.sha256(canonical).hexdigest(), 'receipt target map hash differs')
        if number == 0:
            need(current.get('status') == 'contract_frozen_before_implementation' and current.get('implementation_started_before_freeze') is False, 'freeze receipt invalid')
            break
        need(current.get('status') == 'amendment_sealed_after_independent_review' and current.get('implementation_already_in_progress') is False and type(current.get('amendment')) is int and current['amendment'] == number, 'receipt chain status differs')
        previous_rel = ('som-rs/evidence/react-output-contract-amendment-%03d.json' % (number - 1) if number > 1 else 'som-rs/evidence/react-output-contract-freeze-2026-10-22.json')
        need(current.get('previous_receipt') == previous_rel, 'previous receipt path differs')
        previous_path = physical_repo_path(repo, previous_rel)
        previous = read_json(previous_path)
        need(current.get('previous_receipt_sha256') == digest(previous_path), 'previous receipt hash differs')
        need(isinstance(previous, dict) and isinstance(previous.get('target_contract'), dict) and target.get('previous_sha256') == previous['target_contract'].get('sha256'), 'previous target differs')
        old = previous.get('files')
        need(isinstance(old, dict) and set(old) <= set(files), 'receipt deletes file')
        changed = sorted(k for k in old if old[k] != files[k])
        added = sorted(k for k in files if k not in old)
        need(current.get('changed_paths') == changed and current.get('added_paths') == added, 'receipt changes differ')
        changes = current.get('changes')
        need(isinstance(changes, list) and all(isinstance(x, dict) for x in changes) and [x.get('path') for x in changes] == changed, 'receipt change records differ')
        for change in changes:
            key = change['path']
            archive = change.get('archived_copy')
            need(isinstance(archive, str) and change.get('old_sha256') == old[key] == change.get('archived_copy_sha256') and change.get('new_sha256') == files[key] and digest(physical_repo_path(repo, archive)) == old[key], 'receipt archived source differs')
        current = previous
        number -= 1
    return latest_files

def sealed_profile_helper(repo):
    """Execute only helper bytes authenticated by the sealed receipt."""
    latest_files = sealed_files(repo)
    helper_rel = SOURCES['linux_qa_profile']
    need(isinstance(latest_files, dict) and helper_rel in latest_files, 'QA helper not sealed')
    path = physical_repo_path(repo, helper_rel)
    need(path.is_file() and path.stat().st_size <= 1024 * 1024, 'QA helper missing or oversized')
    raw = read_physical_bytes(path, 1024 * 1024)
    need(len(raw) <= 1024 * 1024 and hashlib.sha256(raw).hexdigest() == latest_files[helper_rel], 'QA helper source differs from receipt')
    need('linux_qa_profile' not in sys.modules, 'preloaded QA helper refused')
    module = types.ModuleType('linux_qa_profile')
    module.__file__ = str(path)
    # Compile the exact bytes that matched the receipt; do not use cached bytecode.
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    sys.modules['linux_qa_profile'] = module
    return module

def file_record(source, target, root, max_bytes):
    need(source.is_absolute() and source.resolve() == source and not source.is_symlink()
         and source.is_file() and source.stat().st_size <= max_bytes, 'evidence source differs')
    raw = source.read_bytes()
    need(len(raw) <= max_bytes, 'evidence source too large')
    with target.open('xb') as stream:
        stream.write(raw)
    sha = hashlib.sha256(raw).hexdigest()
    need(digest(source) == sha and digest(target) == sha, 'evidence source changed')
    return {'path': target.relative_to(root).as_posix(), 'sha256': sha}

def tree_inventory(root):
    need(root.is_absolute() and root.resolve() == root and root.is_dir() and not root.is_symlink(), 'evidence tree refused')
    entries, lines = {}, []
    def fail(exc):
        raise EvidenceError('evidence tree scan failed') from exc
    for directory, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=fail):
        parent = Path(directory)
        for name in sorted(dirs + files):
            path = parent / name
            rel = path.relative_to(root).as_posix()
            need(not any(c in rel for c in ('\x00', '\n', '\r', '\\')), 'evidence tree path invalid')
            item = path.lstat()
            mode = stat.S_IMODE(item.st_mode)
            need(not stat.S_ISLNK(item.st_mode), 'evidence tree link refused')
            if stat.S_ISDIR(item.st_mode):
                entries[rel] = {'kind': 'directory', 'mode': mode}
            elif stat.S_ISREG(item.st_mode):
                value = digest(path)
                entries[rel] = {'kind': 'file', 'mode': mode, 'sha256': value}
                lines.append((rel, rel + '\0' + value + '\n'))
            else:
                raise EvidenceError('evidence tree special file refused')
    lines.sort(key=lambda item: Path(item[0]))
    return {'version': TREE_VERSION, 'root_mode': stat.S_IMODE(root.stat().st_mode), 'entries': entries,
            'tree_sha256': hashlib.sha256(''.join(row[1] for row in lines).encode('utf-8')).hexdigest(),
            'file_count': len(lines), 'symlink_count': 0}

def preserve_tree(source, destination, expected):
    before = tree_inventory(source)
    need((before['tree_sha256'], before['file_count'], before['symlink_count']) == expected, 'source evidence tree differs')
    shutil.copytree(source, destination, symlinks=True)
    retained = tree_inventory(destination)
    need(retained == before and tree_inventory(source) == before, 'evidence tree changed during copy')
    return retained

def begin(root, repo, profile_path, module):
    need(not root.exists() and not root.is_symlink(), 'evidence root exists')
    root.mkdir()
    (root / 'source').mkdir()
    state = {'version': IDENTITY_VERSION, 'status': 'incomplete', 'profile': {'before': None, 'after': None},
             'sources': {}, 'assets': {}, 'problems': []}
    write_json(root / 'identity.json', state)
    try:
        qa = module.validate_profile(profile_path)
        module.recheck(qa)
        before = file_record(profile_path, root / 'profile-before.json', root, 4096)
        need(before['sha256'] == qa['profile_sha256'], 'profile identity differs')
        state['profile']['before'] = before
        for name, rel in SOURCES.items():
            source = repo / rel
            before_source = file_record(source, root / 'source' / (name + '.before.py'), root, 2 * 1024 * 1024)
            state['sources'][name] = {'source_path': rel, 'before': before_source, 'after': None}
        node_src = Path(qa['node'])
        node_copy = root / 'node'
        need(node_src.is_absolute() and node_src.resolve() == node_src and not node_src.is_symlink()
             and node_src.is_file(), 'node source differs')
        shutil.copy2(node_src, node_copy, follow_symlinks=False)
        node_digest = digest(node_copy)
        mode = stat.S_IMODE(node_copy.stat().st_mode)
        need(node_digest == module.NODE_SHA256 and digest(node_src) == node_digest
             and stat.S_IMODE(node_src.stat().st_mode) == mode, 'node evidence differs')
        npm = preserve_tree(qa['npm_root'], root / 'npm', module.NPM_TREE)
        cache = preserve_tree(qa['cache'], root / 'cache', module.CACHE_TREE)
        write_json(root / 'npm-inventory.json', npm)
        write_json(root / 'cache-inventory.json', cache)
        state['assets'] = {'node': {'path': 'node', 'sha256': node_digest, 'mode': mode},
                           'npm': {'path': 'npm', 'inventory': 'npm-inventory.json',
                                   'tree_sha256': npm['tree_sha256'], 'file_count': npm['file_count']},
                           'cache': {'path': 'cache', 'inventory': 'cache-inventory.json',
                                     'tree_sha256': cache['tree_sha256'], 'file_count': cache['file_count']}}
        qa['retained_inventories'] = {'npm': npm, 'cache': cache}
        module.recheck(qa)
        write_json(root / 'identity.json', state, replace=True)
        return state, qa
    except (OSError, ValueError, KeyError, TypeError) as exc:
        state['status'] = 'failed'
        state['problems'].append(type(exc).__name__)
        write_json(root / 'identity.json', state, replace=True)
        raise

def finish(root, repo, profile_path, module, state, qa):
    try:
        module.recheck(qa)
        after = file_record(profile_path, root / 'profile-after.json', root, 4096)
        state['profile']['after'] = after
        need(after['sha256'] == state['profile']['before']['sha256'], 'profile changed')
        for name, rel in SOURCES.items():
            record = file_record(repo / rel, root / 'source' / (name + '.after.py'), root, 2 * 1024 * 1024)
            state['sources'][name]['after'] = record
            need(record['sha256'] == state['sources'][name]['before']['sha256'], 'source changed')
        for record in (state['profile']['before'], state['profile']['after']):
            need(digest(root / record['path']) == record['sha256'], 'retained profile changed')
        for source in state['sources'].values():
            for record in (source['before'], source['after']):
                need(digest(root / record['path']) == record['sha256'], 'retained source changed')
        node = root / 'node'
        need(digest(node) == state['assets']['node']['sha256']
             and stat.S_IMODE(node.stat().st_mode) == state['assets']['node']['mode']
             and stat.S_IMODE(Path(qa['node']).stat().st_mode) == state['assets']['node']['mode'], 'retained node changed')
        for name in ('npm', 'cache'):
            recorded = read_json(root / (name + '-inventory.json'))
            original = qa['retained_inventories'][name]
            live = qa['npm_root'] if name == 'npm' else qa['cache']
            need(same_value(recorded, original) and same_value(tree_inventory(root / name), original)
                 and same_value(tree_inventory(live), original), 'retained tree changed')
        state['status'] = 'passed'
    except (OSError, ValueError, KeyError, TypeError) as exc:
        state['status'] = 'failed'
        state['problems'].append(type(exc).__name__)
    write_json(root / 'identity.json', state, replace=True)
    return state['status'] == 'passed'
