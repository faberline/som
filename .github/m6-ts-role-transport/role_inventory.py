"""Read exact role file and strictly internal alias identities."""
import hashlib
import os
from pathlib import Path
import stat


def need(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def inventory(root):
    root = Path(root)
    need(root.is_absolute() and root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)),
         'physical role root required')
    files = {}
    def walk_error(error):
        raise error

    for folder, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        for name in dirs + names:
            path = Path(folder) / name; info = path.lstat(); rel = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path); resolved = path.resolve(strict=True)
                need(not os.path.isabs(target) and root in resolved.parents and resolved.is_file(),
                     'only internal file aliases permitted')
                files[rel] = {'type': 'internal-alias', 'target': target,
                              'mode': stat.S_IMODE(info.st_mode), 'target_sha256': digest(resolved)}
            elif stat.S_ISREG(info.st_mode):
                need(info.st_nlink == 1, 'hardlink refused')
                files[rel] = {'sha256': digest(path), 'size': info.st_size, 'mode': stat.S_IMODE(info.st_mode)}
            else:
                need(stat.S_ISDIR(info.st_mode), 'special file refused')
    return files
