"""The M2 provider fixture must enforce the real SDK version protocol."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.auth.credentials import AnonymousCredentials
from google.cloud.storage import Client
import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "flow_gcs_fixture", ROOT / "som-core/scripts/flow_gcs_fixture.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def provider():
    state = MODULE.VersionedGCS()
    state.bucket("isolated")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def request(self):
            parsed = urlsplit(self.path)
            status, body, headers, drop = state.handle(
                self.command, parsed.path, parse_qs(parsed.query), dict(self.headers),
                self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if drop:
                self.connection.shutdown(2)
                self.connection.close()
                return
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        do_GET = request
        do_POST = request
        do_DELETE = request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = Client(project="test-local", credentials=AnonymousCredentials(),
                    client_options={"api_endpoint": "http://127.0.0.1:" + str(server.server_port)})
    try:
        yield state, client
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_version_history_conditions_and_exact_delete(provider):
    state, client = provider
    bucket = client.bucket("isolated")
    first = bucket.blob("key")
    first.metadata = {"som-run": "run", "som-step": "put"}
    first.upload_from_string(b"first", if_generation_match=0, retry=None)
    second = bucket.blob("key")
    second.upload_from_string(b"second", if_generation_match=first.generation, retry=None)
    assert second.generation != first.generation
    assert first.download_as_bytes(retry=None) == b"first"
    assert bucket.blob("key").download_as_bytes(retry=None) == b"second"
    with pytest.raises(PreconditionFailed):
        second.delete(if_generation_match=first.generation, retry=None)
    first.delete(if_generation_match=first.generation, retry=None)
    with pytest.raises(NotFound):
        first.reload(retry=None)
    assert bucket.blob("key").download_as_bytes(retry=None) == b"second"
    assert [(event["generation"], event["condition"]) for event in state.events
            if event["operation"] == "delete"] == [(first.generation, [str(first.generation)])]


def test_version_listing_reads_all_pages_and_metadata(provider):
    state, client = provider
    for index in range(5):
        blob = client.bucket("isolated").blob("key")
        blob.metadata = {"som-run": str(index), "som-step": "put"}
        blob.upload_from_string(str(index), retry=None)
    versions = list(client.list_blobs("isolated", prefix="key", versions=True))
    assert len(versions) == 5
    assert {blob.metadata["som-run"] for blob in versions} == {str(i) for i in range(5)}
    assert len({blob.generation for blob in versions}) == 5
    assert [event["offset"] for event in state.events if event["operation"] == "list"] == [0, 2, 4]


@pytest.mark.parametrize("operation", ["upload", "delete"])
def test_commit_can_succeed_while_the_response_is_lost(provider, operation):
    state, client = provider
    blob = client.bucket("isolated").blob("key")
    if operation == "delete":
        blob.upload_from_string(b"owned", if_generation_match=0, retry=None)
    state.faults.append({"operation": operation, "key": "key", "when": "after_commit"})
    with pytest.raises(requests.RequestException):
        if operation == "upload":
            blob.upload_from_string(b"owned", if_generation_match=0, retry=None)
        else:
            blob.delete(if_generation_match=blob.generation, retry=None)
    versions = list(client.list_blobs("isolated", prefix="key", versions=True))
    assert len(versions) == (1 if operation == "upload" else 0)
    assert state.events[-2]["event"] == "response_lost"
