#!/usr/bin/env python3
"""Print the contract tree SHA-256 of a directory.

For every path under the root in sorted(root.rglob("*")) order:
a symlink adds "<relpath>\\0symlink:<link target>\\n" and is not followed,
a regular file adds "<relpath>\\0<sha256 of its bytes>\\n",
a directory adds nothing. relpath uses forward slashes.
The tree SHA-256 is the SHA-256 of the concatenated lines.
"""

import argparse
import hashlib
import os
import pathlib
import sys


def tree_sha(root: pathlib.Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    files = links = 0
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(f"{rel}\0symlink:{os.readlink(path)}\n".encode())
            links += 1
        elif path.is_file():
            digest.update(f"{rel}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
            files += 1
    return digest.hexdigest(), files, links


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=pathlib.Path)
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"error: not a directory: {args.root}", file=sys.stderr)
        return 2
    sha, files, links = tree_sha(args.root)
    print(f"{sha} files={files} symlinks={links}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
