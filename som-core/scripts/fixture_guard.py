"""Reserve a whole local fixture while a verifier changes its state.

Database names isolate rows. They do not isolate a provider's fault switches
or a database process restart. Two verifiers must not share those controls.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import threading


class FixtureBusy(RuntimeError):
    """Another verifier holds this fixture's fault controls."""


class _Lease:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.depth = 0


_LEASES: dict[Path, _Lease] = {}
_REGISTRY_LOCK = threading.Lock()


@contextmanager
def reserve_fixture(manifest: Mapping[str, object] | None) -> Iterator[None]:
    """Reject concurrent processes; allow nested checks in the same thread."""
    if manifest is None:
        yield
        return
    root = manifest.get("root")
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise ValueError("a local fixture needs an absolute owned root")
    path = Path(root).resolve(strict=True) / ".verification.lock"
    with _REGISTRY_LOCK:
        lease = _LEASES.setdefault(path, _Lease())
    with lease.lock:
        if lease.depth:
            lease.depth += 1
            try:
                yield
            finally:
                lease.depth -= 1
            return
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise FixtureBusy(f"fixture already in use: {path.parent}") from error
            os.ftruncate(descriptor, 0)
            os.write(descriptor, json.dumps({"pid": os.getpid()}).encode())
            lease.depth = 1
            try:
                yield
            finally:
                lease.depth = 0
                os.ftruncate(descriptor, 0)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
