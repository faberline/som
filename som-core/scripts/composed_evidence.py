"""Keep process and HTTP evidence without changing any frozen verifier file."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

import httpx


def failure_details(error):
    frames = traceback.extract_tb(error.__traceback__)
    last = frames[-1]
    actual = error.args[0] if error.args else 'See the saved HTTP and process evidence.'
    return {'expected': last.line, 'actual': json.loads(json.dumps(actual, default=str)),
            'location': f'{last.filename}:{last.lineno}', 'check': last.name}


@contextmanager
def capture(root):
    directory = root / 'check-results' / ('evidence-' + str(time.time_ns()))
    directory.mkdir(parents=True)
    record = (directory / 'events.jsonl').open('w')
    lock = threading.Lock()
    original_run, original_request = subprocess.run, httpx.Client.request
    from verify_related import Server
    replaced = []

    def event(value):
        with lock:
            record.write(json.dumps(value, default=str) + '\n')
            record.flush()

    class RecordedServer(Server):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.log.close()
            self.log = (directory / f'uvicorn-{self.port}.log').open('w+')

    def recorded_run(*args, **kwargs):
        start = time.monotonic()
        try:
            result = original_run(*args, **kwargs)
            event({'kind': 'process', 'args': args[0] if args else kwargs.get('args'),
                   'exit_code': result.returncode, 'stdout': result.stdout,
                   'stderr': result.stderr, 'seconds': time.monotonic() - start})
            return result
        except Exception as exc:
            event({'kind': 'process_error', 'error': str(exc)})
            raise

    def recorded_request(self, *args, **kwargs):
        response = original_request(self, *args, **kwargs)
        # Auth values are intentionally excluded. The synthetic HTTP body hash
        # still binds each observed response to the corresponding request.
        body = None
        if not response.request.url.path.startswith(('/auth', '/__', '/token', '/authorize', '/jwks')):
            try:
                body = response.json()
            except ValueError:
                pass
        event({'kind': 'http', 'method': response.request.method,
               'path': response.request.url.path, 'query': str(response.request.url.query),
               'request_sha256': hashlib.sha256(response.request.content).hexdigest(),
               'status': response.status_code,
               'response_sha256': hashlib.sha256(response.content).hexdigest(),
               'response_bytes': len(response.content), 'json': body})
        return response

    for name, module in list(sys.modules.items()):
        if name.startswith(('verify_', 'audit_business_')) and getattr(module, 'Server', None) is Server:
            replaced.append(module)
            module.Server = RecordedServer
    subprocess.run, httpx.Client.request = recorded_run, recorded_request
    try:
        yield directory
    finally:
        subprocess.run, httpx.Client.request = original_run, original_request
        for module in replaced:
            module.Server = Server
        record.close()
