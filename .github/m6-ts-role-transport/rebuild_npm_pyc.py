#!/usr/bin/env python3
"""Rebuild 18 pinned bytecode files from public npm source; never execute them."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import py_compile
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--metadata', type=Path, required=True)
    args = p.parse_args()
    assert os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
    assert sys.version_info[:3] == (3, 12, 11)
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    root = args.root.resolve(strict=True)
    assert root.is_relative_to(temp) and root == temp / 'npm-prefix/lib/node_modules/npm'
    raw = args.metadata.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '8265ef89e448992f5d84cbdbc2f36e83f890fbad6ce246e8221ad6bc852203b2'
    rows = json.loads(raw)['files']; assert len(rows) == 18
    for row in rows:
        member = PurePosixPath(row['path'])
        assert not member.is_absolute() and '..' not in member.parts and str(member).endswith('.pyc')
        target = root / str(member); assert not target.exists() and not target.is_symlink()
        source = Path(importlib.util.source_from_cache(str(target)))
        assert source.resolve(strict=True) == source and source.is_file() and source.stat().st_size == row['source_size']
        os.utime(source, (row['source_mtime'], row['source_mtime']))
        dfile = '/opt/homebrew/lib/node_modules/npm/' + source.relative_to(root).as_posix()
        py_compile.compile(str(source), cfile=str(target), dfile=dfile, doraise=True, optimize=0,
                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        data = target.read_bytes()
        assert len(data) == row['size'] and hashlib.sha256(data).hexdigest() == row['sha256']
    print(json.dumps({'files': len(rows), 'exact_matches': len(rows), 'content_executed': False,
                      'product_acceptance': 'not_run'}, sort_keys=True))


if __name__ == '__main__':
    main()
