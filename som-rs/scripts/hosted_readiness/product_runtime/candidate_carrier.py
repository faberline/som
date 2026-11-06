"""Carry exact tested compiler bytes and record any byte-identical rebuild origin."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile

VERSION = 'som.tested-candidate-carrier.v1'
ROLES = {'SOM_RUFF': 'native/bin/ruff', 'SOM_RUSTFMT': 'compiler-rustfmt',
         'SOM_TS_NODE': 'native/bin/node', 'SOM_TS_PRETTIER': 'native/prettier'}


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def clean(name):
    require(type(name) is str and name and not name.startswith('/') and '\\' not in name
            and all(p not in {'', '.', '..'} for p in name.split('/'))
            and not any(ord(c) < 32 for c in name), 'unsafe carrier member')
    return name


def regular(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'linked carrier input')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 300 * 1024 * 1024,
            'bounded regular single-link carrier input required')
    body = path.read_bytes()
    return {'sha256': digest(body), 'size': len(body), 'mode': stat.S_IMODE(info.st_mode)}


def tree(root):
    root = Path(root).absolute()
    require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), 'regular carrier tree required')
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink() and (path.is_dir() or path.is_file()), 'linked or special carrier tree member')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = regular(path)
    require(result, 'empty carrier tree')
    return result


def portable(value):
    if isinstance(value, dict) and 'sha256' in value and 'size' in value:
        return {key: value[key] for key in ('sha256', 'size', 'mode')}
    return {key: portable(row) for key, row in value.items()}


def identities(root):
    root = Path(root)
    return {'binary': regular(root / 'binary'), 'assets': tree(root / 'assets'),
            'tools': {name: tree(root / relative) if name == 'SOM_TS_PRETTIER' else regular(root / relative)
                      for name, relative in ROLES.items()}}


def pack(args):
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'carrier packing is GitHub-only')
    rebuilt = getattr(args, 'byte_identical_rebuild', False)
    require(type(rebuilt) is bool, 'explicit boolean byte-identical rebuild provenance required')
    compile_raw = args.compile_manifest.read_bytes()
    proof = json.loads(compile_raw)
    require(proof.get('tested_sha') == args.candidate_revision and proof.get('experiment_complete') is True
            and proof.get('identity_stability_passed') is True and proof.get('passed') == 2041
            and proof['initial_identities'] == proof['final_identities']
            and proof['cleanup']['cleanup_passed'] is True, 'completed same-candidate compile proof required')
    expected = portable({k: proof['initial_identities'][k] for k in ('binary', 'assets', 'tools')})
    roots = {'binary': args.binary, 'assets': args.assets, 'native': args.native, 'compiler-rustfmt': args.rustfmt}
    members = {}
    for name, source in roots.items():
        if source.is_dir():
            members.update({name + '/' + relative: (source / relative, item) for relative, item in tree(source).items()})
        else:
            members[name] = (source, regular(source))
    def observed(name):
        prefix = name + '/'
        return ({key[len(prefix):]: row[1] for key, row in members.items() if key.startswith(prefix)}
                if any(key.startswith(prefix) for key in members) else members[name][1])
    actual = {'binary': observed('binary'), 'assets': observed('assets'),
              'tools': {key: observed(value) for key, value in ROLES.items()}}
    require(actual == expected, 'already tested binary, asset or formatter bytes changed before packing')
    args.out.mkdir(mode=0o700, parents=False, exist_ok=False)
    archive_path = args.out / 'candidate.tar.gz'
    with tarfile.open(archive_path, 'x:gz') as archive:
        for name, (source, item) in sorted(members.items()):
            header = tarfile.TarInfo(clean(name))
            header.mode, header.size = item['mode'], item['size']
            with source.open('rb') as stream:
                archive.addfile(header, stream)
            require(regular(source) == item, 'source carrier member changed during copy')
    (args.out / 'compile-manifest.json').write_bytes(compile_raw)
    record = {'version': VERSION, 'candidate_revision': args.candidate_revision,
              'compile_manifest_sha256': digest(compile_raw), 'archive_sha256': regular(archive_path)['sha256'],
              'original_prefixes': {key: str(path.absolute()) for key, path in roots.items()},
              'files': {name: item for name, (_, item) in sorted(members.items())}, 'tested_identities': expected,
              'product_acceptance_passed': False, 'compiler_rebuilt': rebuilt,
              'origin_role': ('new build compared byte-for-byte with the admitted old candidate'
                              if rebuilt else 'original already tested candidate bytes'),
              'github': {key: os.environ.get(key) for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA')}}
    (args.out / 'carrier.json').write_bytes(canonical(record) + b'\n')
    return record


def restore(carrier, out, compile_manifest, candidate_revision):
    carrier, out = Path(carrier), Path(out)
    raw = (carrier / 'carrier.json').read_bytes()
    record = json.loads(raw)
    proof_raw = Path(compile_manifest).read_bytes()
    proof = json.loads(proof_raw)
    require(record.get('version') == VERSION and record.get('candidate_revision') == candidate_revision
            and proof.get('tested_sha') == candidate_revision and proof.get('experiment_complete') is True
            and proof.get('identity_stability_passed') is True
            and record.get('compile_manifest_sha256') == digest(proof_raw)
            and (carrier / 'compile-manifest.json').read_bytes() == proof_raw,
            'carrier is not bound to the admitted complete compile artifact')
    require(type(record.get('compiler_rebuilt')) is bool
            and record.get('origin_role') == ('new build compared byte-for-byte with the admitted old candidate'
                 if record['compiler_rebuilt'] else 'original already tested candidate bytes'),
            'carrier build provenance is missing or inconsistent')
    expected = portable({key: proof['initial_identities'][key] for key in ('binary', 'assets', 'tools')})
    require(record['tested_identities'] == expected, 'carrier tested identity record differs')
    archive_path = carrier / 'candidate.tar.gz'
    require(regular(archive_path)['sha256'] == record['archive_sha256'], 'carrier archive changed')
    require(len(record['files']) <= 100000 and sum(row['size'] for row in record['files'].values()) < 2 * 1024**3,
            'carrier inventory exceeds fixed bounds')
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    seen = set()
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive:
            name = clean(member.name)
            require(member.isfile() and not member.pax_headers and name in record['files'] and name not in seen,
                    'unlisted, duplicate or nonregular carrier member')
            seen.add(name)
            wanted = record['files'][name]
            require(member.size == wanted['size'] and member.mode == wanted['mode'], 'carrier member metadata differs')
            stream = archive.extractfile(member)
            body = stream.read(300 * 1024 * 1024 + 1)
            require(len(body) == member.size and digest(body) == wanted['sha256'], 'carrier member bytes differ')
            path = out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as destination:
                destination.write(body)
            path.chmod(member.mode)
    require(seen == set(record['files']) and tree(out) == record['files'], 'carrier restored universe differs')
    require(identities(out) == expected, 'restored tested binary, assets or native formatters differ')
    return {'root': out, 'binary': out / 'binary', 'assets': out / 'assets',
            'tools': {key: out / value for key, value in ROLES.items()},
            'identity': expected, 'carrier_sha256': digest(raw), 'archive_sha256': record['archive_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('binary', 'assets', 'native', 'rustfmt', 'compile-manifest', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--candidate-revision', required=True)
    parser.add_argument('--byte-identical-rebuild', action='store_true', default=False,
                        help='record a new build whose complete portable identities equal the admitted candidate')
    pack(parser.parse_args())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
