"""Unsealed Linux API adapter. The caller must authenticate context and source first.

This adapter uses the existing host lifecycle. It changes only explicitly scoped
tool paths and the Python site lookup. It does not change product descriptors.
"""
from __future__ import annotations

import contextlib
import pathlib
import subprocess
from unittest import mock


@contextlib.contextmanager
def configured_host(host, qa):
    """Use validated QA tools for this one API case; restore all globals on exit."""
    node, pg, lsof = str(qa['node']), pathlib.Path(qa['pg_bin']), str(qa['lsof'])
    if lsof != '/usr/bin/lsof':
        raise ValueError('Linux lsof path differs')

    def verify_cwd(pid, expected_cwd):
        if pid in host.forbidden_pids():
            return False, None
        done = subprocess.run([lsof, '-a', '-p', str(pid), '-d', 'cwd', '-Fn'],
                              capture_output=True, text=True, check=False)
        lines = done.stdout.splitlines()
        pids = [line[1:] for line in lines if line.startswith('p')]
        paths = [line[1:] for line in lines if line.startswith('n')]
        if done.returncode != 0 or pids != [str(pid)] or len(paths) != 1:
            return False, None
        return pathlib.Path(paths[0]).resolve() == expected_cwd.resolve(), paths[0]

    def listening(pid, port):
        host.check_port_allowed(port)
        done = subprocess.run([lsof, '-nP', '-a', '-p', str(pid), f'-iTCP:{port}', '-sTCP:LISTEN', '-t'],
                              capture_output=True, text=True, check=False)
        return done.returncode == 0 and str(pid) in done.stdout.split()

    # Python is the validated live venv executable, which discovers its own
    # site-packages. Do not inject the frozen macOS venv into PYTHONPATH.
    with mock.patch.object(host, 'NODE_BIN', node), mock.patch.object(host, 'PG_BIN_DIR', pg), \
         mock.patch.object(host, 'verify_process_cwd', verify_cwd), \
         mock.patch.object(host, '_python_path', lambda root: str(root)), \
         mock.patch.object(host.base_hosts, 'listening', listening):
        yield
