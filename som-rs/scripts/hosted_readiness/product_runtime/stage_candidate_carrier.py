"""Copy exact current compiler inputs into owned single-link files on GitHub."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

FIELDS = ('st_dev', 'st_ino', 'st_nlink', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
MAXIMUM = 300 * 1024**2


def require(value, message):
    if not value: raise ValueError(message)


def no_links(path):
    require(path.is_absolute() and '..' not in path.parts
            and not any(part.is_symlink() for part in (path, *path.parents)), 'absolute unlinked path required')


def stage(binary, rustfmt, compile_manifest, candidate_revision, out):
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub staging only')
    require(re.fullmatch('[0-9a-f]{40}', candidate_revision), 'exact current candidate required')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    for path in (binary, rustfmt, compile_manifest, out): no_links(path)
    require(temporary in out.parents and not out.exists() and out.parent.is_dir(), 'new owned staging root required')
    info = compile_manifest.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 32 * 1024**2, 'regular compile proof required')
    raw = compile_manifest.read_bytes()
    proof = json.loads(raw)
    require(proof['tested_sha'] == candidate_revision and proof['experiment_complete'] is True
            and proof['passed'] == 2041 and proof['identity_stability_passed'] is True
            and proof['initial_identities'] == proof['final_identities']
            and proof['cleanup']['cleanup_passed'] is True and proof['product_acceptance_passed'] is False,
            'complete stable current same-job compile proof required')
    wanted = {'binary': proof['initial_identities']['binary'], 'rustfmt': proof['initial_identities']['tools']['SOM_RUSTFMT']}
    out.mkdir(mode=0o700)
    record = {'version': 'som.current-candidate-single-link-staging.v1', 'candidate_revision': candidate_revision,
              'compile_manifest_sha256': hashlib.sha256(raw).hexdigest(), 'files': {}, 'product_acceptance_passed': False}
    for name, source in (('binary', binary), ('rustfmt', rustfmt)):
        with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink > 0 and before.st_size <= MAXIMUM,
                    'regular bounded compiler input required')
            body = stream.read(MAXIMUM + 1)
            actual = {'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body), 'mode': stat.S_IMODE(before.st_mode)}
            require(actual == {key: wanted[name][key] for key in actual}, 'input differs from actual tested current bytes')
            destination = out / name
            with destination.open('xb') as output:
                output.write(body); os.fchmod(output.fileno(), actual['mode']); output.flush(); os.fsync(output.fileno())
            after = os.fstat(stream.fileno())
            require(all(getattr(before, key) == getattr(after, key) for key in FIELDS)
                    and all(getattr(after, key) == getattr(source.lstat(), key) for key in FIELDS), 'input fd/path identity changed')
            stream.seek(0)
            require(stream.read(MAXIMUM + 1) == body, 'source bytes changed after staging')
        installed = destination.stat()
        require(stat.S_ISREG(installed.st_mode) and installed.st_nlink == 1
                and installed.st_size == actual['size'] and stat.S_IMODE(installed.st_mode) == actual['mode']
                and hashlib.sha256(destination.read_bytes()).hexdigest() == actual['sha256'], 'single-link copy differs')
        record['files'][name] = {'source': str(source), 'source_nlink': before.st_nlink,
                                 'staged': str(destination), 'staged_nlink': 1, **actual}
    require(compile_manifest.read_bytes() == raw, 'compile proof changed during staging')
    with (out / 'receipt.json').open('x') as output: json.dump(record, output, sort_keys=True); output.write('\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('binary', 'rustfmt', 'compile-manifest', 'out'): parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--candidate-revision', required=True)
    args = parser.parse_args()
    stage(args.binary, args.rustfmt, args.compile_manifest, args.candidate_revision, args.out)


if __name__ == '__main__': main()
