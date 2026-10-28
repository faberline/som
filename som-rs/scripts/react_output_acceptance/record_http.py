#!/usr/bin/env python3
"""Keep raw HTTP evidence while an unchanged Python checker runs on GitHub.

Usage: record_http.py --out NEW_DIR -- SCRIPT ORIGINAL_ARGUMENTS
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import sys
import threading
import traceback
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
VERSION = "som.m6-http-recording.v1"
REFUSAL = "raw HTTP recording refused"
MAX_BODY = 128 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_CALLS = 20_000
ALLOWED_SCRIPTS = frozenset((
    "som-rs/scripts/typescript_output_acceptance/check_python_baseline.py",
    "som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py",
    "som-rs/tests/react-output-v1/check_python_controls.py",
    "som-rs/tests/rust-output-v1/check_python_controls.py",
    "som-rs/tests/typescript-output-v1/check_python_controls.py",
))
CHILD_ENV = frozenset((
    "HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "CI", "SDKROOT",
    "DEVELOPER_DIR", "CARGO_HOME", "RUSTUP_HOME", "GITHUB_ACTIONS",
    "RUNNER_ENVIRONMENT", "RUNNER_OS", "RUNNER_ARCH", "RUNNER_TEMP",
    "CARGO_NET_OFFLINE", "PYTHONDONTWRITEBYTECODE", "SOM_TS_PRETTIER",
))


class RecordingError(ValueError):
    def __init__(self):
        super().__init__(REFUSAL)


def need(condition):
    if not condition:
        raise RecordingError()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def physical(directory: Path):
    need(directory.is_absolute() and str(directory) == os.path.abspath(directory))
    for item in (*reversed(directory.parents), directory):
        info = item.lstat()
        need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))


def read_regular(filename: Path):
    physical(filename.parent)
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BODY)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BODY + 1)
        need(len(raw) == info.st_size and len(raw) <= MAX_BODY)
        return raw
    finally:
        os.close(descriptor)


def write_new(filename: Path, raw: bytes):
    physical(filename.parent)
    descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        os.fchmod(stream.fileno(), 0o600)


def safe_error(error):
    reason = getattr(error, "reason", error)
    number = getattr(reason, "errno", None)
    return {"type": type(error).__name__, "errno": number if type(number) is int else None}


def request_identity(request):
    url = request.full_url
    need(type(url) is str)
    parsed = urllib.parse.urlsplit(url)
    need(parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1", "localhost"))
    need(parsed.port in (18917, 18919) and parsed.username is None and parsed.password is None)
    need(not parsed.query and not parsed.fragment)
    method = request.get_method()
    need((method == "POST" and parsed.path in ("/v1/compile", "/v1/assemble"))
         or (method == "GET" and parsed.path == "/healthz"))
    body = request.data
    need(body is None or type(body) is bytes)
    raw = b"" if body is None else body
    need(len(raw) <= MAX_BODY)
    return method, url, raw


class Call:
    def __init__(self, recorder, identity, number):
        method, url, request = identity
        self.recorder = recorder
        self.lock = threading.RLock()
        self.response = bytearray()
        self.done = False
        self.record = {
            "id": number, "method": method, "url": url,
            "request_file": f"calls/{number:06d}.request.raw", "request_sha256": digest(request),
            "status": None, "response_file": f"calls/{number:06d}.response.raw",
            "response_sha256": None, "error": None, "complete": False,
        }
        recorder.account(len(request))
        write_new(recorder.out / self.record["request_file"], request)

    def append(self, chunk):
        need(type(chunk) is bytes)
        with self.lock:
            if self.done:
                need(not chunk)
                return
            need(len(self.response) + len(chunk) <= MAX_BODY)
            self.recorder.account(len(chunk))
            self.response.extend(chunk)

    def finish(self, complete=False, error=None):
        with self.lock:
            if self.done:
                return
            self.done = True
            self.record["complete"] = complete is True
            self.record["error"] = error
            raw = bytes(self.response)
            self.record["response_sha256"] = digest(raw)
            write_new(self.recorder.out / self.record["response_file"], raw)
            write_new(self.recorder.out / f"calls/{self.record['id']:06d}.json",
                      (json.dumps(self.record, indent=2, sort_keys=True) + "\n").encode())

    def attach(self, response):
        """Keep the actual HTTPResponse object, including its HTTPError behavior."""
        need(type(response.status) is int)
        self.record["status"] = response.status
        original_read, original_close = response.read, response.close

        def read(*args, **kwargs):
            try:
                raw = original_read(*args, **kwargs)
                self.append(raw)
                amount = args[0] if args else kwargs.get("amt")
                complete = (amount is None or (type(amount) is int and amount < 0)
                            or (amount != 0 and (not raw or getattr(response, "length", None) == 0)))
                if complete:
                    self.finish(complete=True)
                return raw
            except BaseException as error:
                self.finish(error=safe_error(error))
                raise

        def close():
            try:
                return original_close()
            finally:
                self.finish()

        response.read, response.close = read, close
        return response


class Recorder:
    def __init__(self, out: Path):
        self.out = out
        self.lock = threading.RLock()
        self.calls = []
        self.errors = []
        self.total_bytes = 0
        self.original_open = None

    def account(self, size):
        with self.lock:
            need(self.total_bytes + size <= MAX_TOTAL)
            self.total_bytes += size

    def open(self, handler, http_class, request, **kwargs):
        try:
            identity = request_identity(request)
            with self.lock:
                need(len(self.calls) < MAX_CALLS)
                call = Call(self, identity, len(self.calls) + 1)
                self.calls.append(call)
        except RecordingError:
            with self.lock:
                self.errors.append("request_refused")
            raise
        try:
            response = self.original_open(handler, http_class, request, **kwargs)
        except BaseException as error:
            call.finish(error=safe_error(error))
            raise
        try:
            return call.attach(response)
        except BaseException as error:
            call.finish(error=safe_error(error))
            response.close()
            raise

    def __enter__(self):
        self.original_open = urllib.request.AbstractHTTPHandler.do_open
        recorder = self

        def recording_open(handler, http_class, request, **kwargs):
            return recorder.open(handler, http_class, request, **kwargs)

        urllib.request.AbstractHTTPHandler.do_open = recording_open
        return self

    def __exit__(self, *_exception):
        urllib.request.AbstractHTTPHandler.do_open = self.original_open
        for call in self.calls:
            call.finish()


def run_checker(out: str | Path, script: str | Path, arguments: list[str]):
    need(os.environ.get("GITHUB_ACTIONS") == "true"
         and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted")
    root = Path(os.path.abspath(REPO))
    target = Path(os.path.abspath(script))
    output = Path(os.path.abspath(out))
    need(target.is_relative_to(root) and target.relative_to(root).as_posix() in ALLOWED_SCRIPTS)
    need(output.is_relative_to(root / ".local") and not output.exists() and not output.is_symlink())
    physical(output.parent)
    source = read_regular(target)
    output.mkdir(mode=0o700)
    (output / "calls").mkdir(mode=0o700)
    before_argv, before_path, before_env = sys.argv, sys.path[:], dict(os.environ)
    recorder = Recorder(output)
    code = 1
    failed_recording = False
    try:
        sys.argv = [str(target), *arguments]
        sys.path[:] = [str(target.parent), *before_path]
        os.environ.clear()
        os.environ.update({key: value for key, value in before_env.items() if key in CHILD_ENV})
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        with recorder:
            try:
                runpy.run_path(str(target), run_name="__main__")
                code = 0
            except SystemExit as error:
                if error.code is None:
                    code = 0
                elif isinstance(error.code, int):
                    code = int(error.code)
                else:
                    print(error.code, file=sys.stderr)
                    code = 1
            except Exception:
                traceback.print_exc()
                code = 1
    except Exception:
        failed_recording = True
        code = 1
    finally:
        sys.argv, sys.path[:] = before_argv, before_path
        os.environ.clear()
        os.environ.update(before_env)
    need(read_regular(target) == source)
    if failed_recording:
        recorder.errors.append("recording_failed")
    manifest = {
        "version": VERSION, "script": {"path": target.relative_to(root).as_posix(), "sha256": digest(source)},
        "argv": [str(target), *arguments], "exit_code": code,
        "calls": [call.record for call in recorder.calls], "recording_errors": recorder.errors,
    }
    write_new(output / "manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return code if not recorder.errors else 1


class QuietParser(argparse.ArgumentParser):
    def error(self, _message):
        raise RecordingError()


def main(argv=None):
    try:
        parser = QuietParser(description=__doc__.splitlines()[0])
        parser.add_argument("--out", required=True)
        parser.add_argument("checker", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv)
        command = args.checker[1:] if args.checker[:1] == ["--"] else args.checker
        need(command and all(type(value) is str for value in command))
        return run_checker(args.out, command[0], command[1:])
    except Exception:
        print(REFUSAL, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
