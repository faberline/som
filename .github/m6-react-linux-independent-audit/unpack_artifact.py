#!/usr/bin/env python3
"""Retain only a verified public QA tar from one GitHub artifact ZIP."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zipfile

EXPECTED_ZIP = '10a7f2bf058be21c670ddac89afece05c305ee717b9a879de4c95256fd6fe2dc'
ZIP_SIZE = 523384472
TAR_NAME = 'react-linux-qa-public-inputs.tar'
SIDE_NAME = TAR_NAME + '.sha256'

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise ValueError('GitHub hosted runner required')
    if len(sys.argv) != 3:
        raise ValueError('expected input zip and output tar')
    source, target = map(Path, sys.argv[1:])
    temp = Path(os.environ['RUNNER_TEMP']).resolve()
    if (source != temp / 'react-qa-artifact.zip' or target != temp / TAR_NAME
            or source.parent.resolve() != temp or target.parent.resolve() != temp):
        raise ValueError('artifact paths differ')
    if source.is_symlink() or not source.is_file() or source.stat().st_size != ZIP_SIZE or digest(source) != EXPECTED_ZIP:
        raise ValueError('artifact zip differs')
    if target.exists() or target.is_symlink():
        raise ValueError('output exists')
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) != 2 or {x.filename for x in entries} != {TAR_NAME, SIDE_NAME}:
            raise ValueError('artifact members differ')
        index = {x.filename: x for x in entries}
        if any(x.is_dir() or x.flag_bits & 1 for x in entries) or index[SIDE_NAME].file_size > 256 or index[TAR_NAME].file_size > 2 * 1024**3:
            raise ValueError('artifact member invalid')
        side = archive.read(index[SIDE_NAME]).decode('ascii', 'strict')
        match = re.fullmatch(r'([0-9a-f]{64})  [^\n]*react-linux-qa-public-inputs\.tar\n', side)
        if match is None:
            raise ValueError('artifact tar checksum invalid')
        calculated = hashlib.sha256()
        with archive.open(index[TAR_NAME]) as incoming, target.open('xb') as output:
            while True:
                block = incoming.read(1024 * 1024)
                if not block:
                    break
                calculated.update(block)
                output.write(block)
        if calculated.hexdigest() != match.group(1):
            raise ValueError('artifact tar checksum differs')
    print(json.dumps({'state': 'verified-public-artifact-unpacked', 'artifact_zip_sha256': EXPECTED_ZIP,
                      'tar_sha256': calculated.hexdigest(), 'product_acceptance_passed': False}))

if __name__ == '__main__':
    main()
