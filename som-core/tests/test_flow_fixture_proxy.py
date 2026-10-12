"""The fixture must preserve redirects for the browser to follow."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

import requests

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from local_flow_services import FlowFixture


class RedirectProxyTest(unittest.TestCase):
    """A provider redirect must not be followed by the fixture proxy."""

    def test_authorize_redirect_keeps_browser_in_control(self):
        calls = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                calls.append(self.path)
                if self.path == "/authorize":
                    self.send_response(302)
                    self.send_header("Location", "/callback")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.send_response(400)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        fixture = FlowFixture.__new__(FlowFixture)
        fixture.upstream = f"http://127.0.0.1:{provider.server_port}"
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), fixture.handler())
        threads = [threading.Thread(target=server.serve_forever, daemon=True)
                   for server in (provider, proxy)]
        for thread in threads:
            thread.start()
        try:
            response = requests.get(
                f"http://127.0.0.1:{proxy.server_port}/authorize",
                allow_redirects=False, timeout=5,
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["Location"], "/callback")
            self.assertEqual(calls, ["/authorize"])
        finally:
            for server in (proxy, provider):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=5)

    def test_before_commit_keeps_exact_bytes_and_separates_commit_phases(self):
        fixture = FlowFixture.__new__(FlowFixture)
        fixture.lock = threading.RLock()
        fixture.faults = [dict(provider="s3", operation="put", key="owned",
                              when="before_commit", gate="create")]
        fixture.gates = {"create": threading.Event()}
        fixture.events = []
        body = "資料\n".encode() + b"\x00\xff"
        # An after-commit observation cannot consume a before-commit rule.
        self.assertFalse(fixture.after_commit("s3", "put", "owned"))
        self.assertEqual(len(fixture.faults), 1)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fixture.before_commit, "s3", "owned", body)
            try:
                from flow_fault_checks import wait_for
                wait_for(lambda: fixture.events, "provider request bytes")
                self.assertFalse(future.done())
                self.assertEqual(fixture.events[0]["event"], "paused_before_commit")
                self.assertEqual(base64.b64decode(fixture.events[0]["body"]), body)
                self.assertEqual(fixture.faults, [])
            finally:
                fixture.gates["create"].set()
            future.result(timeout=5)


if __name__ == "__main__":
    unittest.main()
