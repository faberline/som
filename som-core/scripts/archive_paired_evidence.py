"""Save raw paired-run evidence outside temporary job directories."""

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path

EXCLUDED = {".venv", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__"}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def files(root, output):
    for directory, folders, names in os.walk(root, followlinks=False):
        current = Path(directory)
        folders[:] = sorted(name for name in folders if name not in EXCLUDED and current / name != output)
        for name in list(folders):
            path = current / name
            if path.is_symlink():
                folders.remove(name)
                yield path
        yield from (current / name for name in sorted(names))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--diagnostic", action="store_true", help="Allow retired partial runs; never use for an active run.")
    parser.add_argument("--evidence", type=Path, action="append", default=[],
                        help="Also preserve a repository file or directory. May be repeated.")
    args = parser.parse_args()
    kit = args.kit.resolve()
    root = kit.parents[2]
    output = kit / "archive"
    output.mkdir(exist_ok=True)
    destination = output / "records.tar.gz"
    manifest_path = output / "manifest.json"
    if destination.exists() or manifest_path.exists():
        raise ValueError("archive already exists; preserve it and choose a separate kit")
    jobs = json.loads((kit / "jobs.json").read_text())["jobs"]
    sources = [(path, "kit/" + path.relative_to(kit).as_posix()) for path in files(kit, output)]
    sources.append((Path(__file__).resolve(), "tools/archive_paired_evidence.py"))
    additional_evidence = []
    for value in args.evidence:
        path = value.resolve()
        if not path.is_relative_to(root) or not path.exists():
            raise ValueError("additional evidence must exist inside the repository: " + str(value))
        additional_evidence.append(path.relative_to(root).as_posix())
        included = files(path, output) if path.is_dir() else [path]
        sources.extend((item, "evidence/" + item.relative_to(root).as_posix()) for item in included)
    jobs_summary = []
    for job in jobs:
        directory = Path(job["directory"])
        if not directory.is_dir():
            raise ValueError("missing job directory: " + str(directory))
        events = sorted(directory.glob("run-logs/*/events.jsonl"))
        metadata = sorted(directory.glob("run-logs/*/metadata.json"))
        if not args.diagnostic and (not events or len(events) != len(metadata)):
            raise ValueError("formal job has not finished: " + job["id"])
        jobs_summary.append({"job": job["id"], "event_logs": len(events), "attempt_metadata": len(metadata)})
        sources.extend((path, "jobs/" + job["id"] + "/" + path.relative_to(directory).as_posix()) for path in files(directory, output))
    seal = json.loads((kit / "seal.json").read_text())
    dependencies = []
    for name, expected in seal.get("files", {}).items():
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("sealed dependency escapes repository")
        actual = sha256(path) if path.is_file() else None
        matches = actual == expected
        dependencies.append({"path": name, "expected_sha256": expected, "current_sha256": actual, "included": matches})
        if matches:
            sources.append((path, "sealed/" + name))
        elif not args.diagnostic:
            raise ValueError("formal sealed dependency changed: " + name)
    manifest = {"kind": "diagnostic" if args.diagnostic else "formal", "kit": str(kit),
                "archiver_sha256": sha256(Path(__file__)),
                "additional_evidence": additional_evidence,
                "jobs": jobs_summary, "sealed_dependencies": dependencies, "files": [],
                "excluded_directories": sorted(EXCLUDED),
                "scope": "Raw job logs, returned source parts, prepared tasks, final product files, checks, and kit reports. Virtual environments and caches are excluded. Symlink targets are recorded without following them. Changed diagnostic dependencies are listed but not substituted for the sealed originals."}
    with tempfile.NamedTemporaryFile(dir=output, prefix="records-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with tarfile.open(temporary, "w:gz", compresslevel=6, dereference=False) as archive:
            for path, name in sorted(sources, key=lambda entry: entry[1]):
                before = path.lstat()
                info = archive.gettarinfo(str(path), arcname=name)
                record = {"path": name, "source": str(path), "mtime_ns": before.st_mtime_ns}
                if path.is_symlink():
                    record.update(kind="symlink", target=os.readlink(path))
                    archive.addfile(info)
                elif path.is_file():
                    record.update(kind="file", bytes=before.st_size, sha256=sha256(path))
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
                else:
                    raise ValueError("unsupported evidence file: " + str(path))
                after = path.lstat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("evidence changed during archive: " + str(path))
                manifest["files"].append(record)
        expected = {row["path"]: row for row in manifest["files"]}
        if len(expected) != len(manifest["files"]):
            raise ValueError("duplicate archive names")
        with tarfile.open(temporary, "r:gz") as archive:
            seen = set()
            for entry in archive:
                row = expected[entry.name]
                if row["kind"] == "symlink":
                    assert entry.issym() and entry.linkname == row["target"]
                else:
                    stream = archive.extractfile(entry)
                    assert stream is not None and hashlib.file_digest(stream, "sha256").hexdigest() == row["sha256"]
                seen.add(entry.name)
            assert seen == set(expected)
        manifest.update(archive_sha256=sha256(temporary), archive_bytes=temporary.stat().st_size,
                        verified_files=len(expected), verified=True)
        temporary.replace(destination)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({key: manifest[key] for key in ("kind", "archive_sha256", "archive_bytes", "verified_files", "verified")}))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
