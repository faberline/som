"""Versioned local GCS protocol fixture. This is never a product module."""

import base64
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
import hashlib
import json
import threading
from urllib.parse import unquote

import google_crc32c


@dataclass(frozen=True)
class StoredObject:
    """Keep immutable contents and ownership metadata for each generation."""

    body: bytes
    description: dict[str, object]


class VersionedGCS:
    """Model generations, conditions and paginated version observations."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.buckets: dict[str, dict[str, dict[int, StoredObject]]] = {}
        self.live: dict[tuple[str, str], int] = {}
        self.next_generation = 1
        self.events: list[dict[str, object]] = []
        self.faults: list[dict[str, str]] = []

    def bucket(self, name: str) -> None:
        """Create only tester-owned buckets through a private control path."""
        with self.lock:
            self.buckets.setdefault(name, {})

    def record(self, **event: object) -> None:
        self.events.append(event)

    def drop_reply(self, operation: str, key: str) -> bool:
        for index, fault in enumerate(self.faults):
            if fault == {"operation": operation, "key": key, "when": "after_commit"}:
                self.faults.pop(index)
                self.record(operation=operation, key=key, event="response_lost")
                return True
        return False

    @staticmethod
    def response(status: int, content: object, headers: dict[str, str] | None = None,
                 drop: bool = False) -> tuple[int, bytes, dict[str, str], bool]:
        if isinstance(content, bytes):
            body = content
        else:
            body = json.dumps(content).encode()
            headers = {"Content-Type": "application/json", **(headers or {})}
        return status, body, headers or {}, drop

    def error(self, status: int, message: str) -> tuple[int, bytes, dict[str, str], bool]:
        return self.response(status, {"error": {"code": status, "message": message}})

    def handle(self, method: str, path: str, query: dict[str, list[str]],
               headers: dict[str, str], body: bytes) -> tuple[int, bytes, dict[str, str], bool]:
        """Handle the SDK wire protocol without consulting generated source."""
        with self.lock:
            return self._handle(method, path, query, headers, body)

    def _handle(self, method: str, path: str, query: dict[str, list[str]],
                headers: dict[str, str], body: bytes) -> tuple[int, bytes, dict[str, str], bool]:
        parts = path.split("/b/", 1)
        if len(parts) != 2:
            return self.error(400, "bucket path required")
        bucket, separator, rest = parts[1].partition("/o")
        bucket = unquote(bucket)
        if bucket not in self.buckets:
            return self.error(404, "bucket missing")
        if not separator:
            return self.response(200, {"name": bucket, "kind": "storage#bucket",
                                       "versioning": {"enabled": True}})
        if method == "POST" and path.startswith("/upload/"):
            return self.upload(bucket, query, headers, body)
        if rest in {"", "/"} and method == "GET":
            return self.list_objects(bucket, query)
        key = unquote(rest.removeprefix("/"))
        versions = self.buckets[bucket].get(key, {})
        requested = query.get("generation", [str(self.live.get((bucket, key), 0))])[0]
        try:
            generation = int(requested)
        except ValueError:
            return self.error(400, "invalid generation")
        selected = versions.get(generation)
        if selected is None:
            self.record(operation=method, key=key, generation=generation, status=404)
            return self.error(404, "generation missing")
        condition = query.get("ifGenerationMatch")
        if condition is not None and condition != [str(generation)]:
            self.record(operation=method, key=key, generation=generation,
                        condition=condition, status=412)
            return self.error(412, "generation condition failed")
        if method == "DELETE":
            del versions[generation]
            if self.live.get((bucket, key)) == generation:
                self.live.pop((bucket, key))
            self.record(operation="delete", key=key, generation=generation,
                        condition=condition, committed=True)
            return self.response(204, b"", drop=self.drop_reply("delete", key))
        if method not in {"GET", "HEAD"}:
            return self.error(405, "unsupported method")
        self.record(operation="read", key=key, generation=generation)
        if query.get("alt") == ["media"] or path.startswith("/download/"):
            content = selected.body
            response_headers = {"Content-Type": "application/octet-stream",
                                "x-goog-generation": str(generation),
                                "x-goog-hash": "crc32c=" + str(selected.description["crc32c"])}
            requested_range = headers.get("Range", headers.get("range"))
            if requested_range:
                start, end = requested_range.removeprefix("bytes=").split("-", 1)
                lower, upper = int(start or 0), min(int(end) if end else len(content) - 1,
                                                 len(content) - 1)
                response_headers["Content-Range"] = f"bytes {lower}-{upper}/{len(content)}"
                return self.response(206, content[lower:upper + 1], response_headers)
            return self.response(200, content, response_headers)
        return self.response(200, selected.description)

    @staticmethod
    def upload_data(headers: dict[str, str], body: bytes) -> tuple[dict[str, object], bytes]:
        """Read the SDK multipart request at the provider boundary."""
        content_type = headers.get("Content-Type", headers.get("content-type", ""))
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
        parts = list(message.iter_parts())
        if len(parts) != 2:
            raise ValueError("multipart upload required")
        metadata = json.loads(parts[0].get_payload(decode=True))
        content = parts[1].get_payload(decode=True)
        if not isinstance(metadata, dict) or not isinstance(content, bytes):
            raise ValueError("invalid multipart data")
        return metadata, content

    def upload(self, bucket: str, query: dict[str, list[str]], headers: dict[str, str],
               body: bytes) -> tuple[int, bytes, dict[str, str], bool]:
        try:
            metadata, content = self.upload_data(headers, body)
        except ValueError:
            return self.error(400, "multipart upload required")
        key = metadata["name"]
        current = self.live.get((bucket, key), 0)
        condition = query.get("ifGenerationMatch")
        if condition is not None and condition != [str(current)]:
            self.record(operation="upload", key=key, condition=condition, status=412)
            return self.error(412, "generation condition failed")
        generation = self.next_generation
        self.next_generation += 1
        description = {
            "kind": "storage#object", "name": key, "bucket": bucket,
            "generation": str(generation), "metageneration": "1",
            "size": str(len(content)), "contentType": "application/octet-stream",
            "md5Hash": base64.b64encode(hashlib.md5(content).digest()).decode(),
            "crc32c": base64.b64encode(google_crc32c.value(content).to_bytes(4, "big")).decode(),
            "metadata": metadata.get("metadata", {}),
        }
        self.buckets[bucket].setdefault(key, {})[generation] = StoredObject(content, description)
        self.live[bucket, key] = generation
        self.record(operation="upload", key=key, generation=generation,
                    condition=condition, committed=True, metadata=description["metadata"])
        return self.response(200, description, drop=self.drop_reply("upload", key))

    def list_objects(self, bucket: str, query: dict[str, list[str]]) -> tuple[int, bytes, dict[str, str], bool]:
        prefix = query.get("prefix", [""])[0]
        include_versions = query.get("versions", ["false"])[0].lower() == "true"
        selected = [item.description for key, versions in sorted(self.buckets[bucket].items())
                    if key.startswith(prefix) for generation, item in sorted(versions.items())
                    if include_versions or self.live.get((bucket, key)) == generation]
        offset = int(query.get("pageToken", ["0"])[0])
        size = min(2, int(query.get("maxResults", ["2"])[0]))
        content: dict[str, object] = {"kind": "storage#objects", "items": selected[offset:offset + size]}
        if offset + size < len(selected):
            content["nextPageToken"] = str(offset + size)
        self.record(operation="list", prefix=prefix, versions=include_versions,
                    offset=offset, count=len(content["items"]))
        return self.response(200, content)
