"""Evaluation-only placement and complete, byte-addressed response delivery."""

import argparse
import bisect
import hashlib
import json
import re
import subprocess
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def install(response, root):
    paths = []
    for name, source in response["files"].items():
        target = root / name
        if (not re.fullmatch(r"[A-Za-z_0-9/.-]+\.(py|toml|ini|md)", name)
                or any(p in {"", ".", ".."} for p in name.split("/"))
                or name.startswith("/") or not target.resolve().is_relative_to(root)
                or any(p.is_symlink() for p in (target, *target.parents))):
            raise ValueError("unsafe generated path: " + name)
        paths.append((target, source))
    if len(paths) > 12 or sum(len(s.encode()) for _, s in paths) > 65536:
        raise ValueError("generated output exceeds contract")
    for target, source in paths:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)


def page(data, start=0, end=None, page_index=1, delivery_id=None):
    end = len(data) if end is None else end
    if not 0 <= start < end <= len(data):
        raise ValueError("invalid byte range")
    value = data[start:end].decode("utf-8")
    if page_index < 1:
        raise ValueError('page index must be positive')
    metadata = {"page": page_index, "delivery_id": delivery_id, "start": start, "end": end, "total": len(data),
                "response_sha256": digest(data), "page_sha256": digest(data[start:end])}
    print("SOM_RESPONSE_BEGIN " + json.dumps(metadata, sort_keys=True), flush=True)
    print(value, end="", flush=True)
    print("\nSOM_RESPONSE_END " + json.dumps(metadata, sort_keys=True), flush=True)
    return metadata


def observed_ranges(data, outputs, delivery_id=None):
    """Only exact complete pages from actual tool outputs count as observed."""
    spans = []
    pattern = re.compile(r"SOM_RESPONSE_BEGIN (\{[^\n]+\})\n(.*?)\nSOM_RESPONSE_END (\{[^\n]+\})", re.S)
    for output in outputs:
        for match in pattern.finditer(output):
            before, after = json.loads(match[1]), json.loads(match[3])
            chunk = match[2].encode()
            if (before == after and (delivery_id is None or before.get('delivery_id') == delivery_id) and before["response_sha256"] == digest(data)
                    and before["total"] == len(data)
                    and before["page_sha256"] == digest(chunk)
                    and data[before["start"]:before["end"]] == chunk):
                spans.append((before["start"], before["end"]))
        # A tool may retain the head, the tail, or both around a truncation
        # marker. Count only bytes that exactly match the declared response.
        for marker in re.finditer(r'SOM_RESPONSE_BEGIN (\{[^\n]+\})\n', output):
            meta = json.loads(marker[1])
            if delivery_id is not None and meta.get('delivery_id') != delivery_id:
                continue
            if meta.get('response_sha256') != digest(data) or meta.get('total') != len(data):
                continue
            start, end = meta.get('start'), meta.get('end')
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(data):
                continue
            actual = output[marker.end():].encode()
            count = 0
            for expected, observed in zip(data[start:end], actual):
                if expected != observed: break
                count += 1
            if count: spans.append((start, start + count))
        for marker in re.finditer(r'\nSOM_RESPONSE_END (\{[^\n]+\})', output):
            meta = json.loads(marker[1])
            if delivery_id is not None and meta.get('delivery_id') != delivery_id:
                continue
            if meta.get('response_sha256') != digest(data) or meta.get('total') != len(data):
                continue
            start, end = meta.get('start'), meta.get('end')
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(data):
                continue
            actual = output[:marker.start()].encode()
            count = 0
            for expected, observed in zip(reversed(data[start:end]), reversed(actual)):
                if expected != observed: break
                count += 1
            if count: spans.append((end - count, end))
    boundaries = [0]
    for char in data.decode('utf-8'):
        boundaries.append(boundaries[-1] + len(char.encode()))
    spans = [(boundaries[bisect.bisect_left(boundaries, start)],
              boundaries[bisect.bisect_right(boundaries, end)-1]) for start,end in spans]
    spans = [(start,end) for start,end in spans if start < end]
    covered = 0
    for start, end in sorted(spans):
        if start > covered:
            break
        covered = max(covered, end)
    missing = []
    cursor = 0
    for start, end in sorted(spans):
        if start > cursor: missing.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < len(data): missing.append((cursor, len(data)))
    return {"complete": not missing, "covered_prefix_bytes": covered,
            "response_bytes": len(data), "observed_ranges": sorted(set(spans)),
            "missing_ranges": missing}


def native_outputs(path):
    outputs = []
    def collect(output):
        if isinstance(output, str):
            outputs.append(output)
            try:
                nested = json.loads(output)
            except ValueError:
                return
            if isinstance(nested, (dict, list)):
                collect(nested)
        elif isinstance(output, list):
            for part in output:
                collect(part)
        elif isinstance(output, dict):
            for key in ("text", "output", "content"):
                if key in output:
                    collect(output[key])
    for line in path.read_text().splitlines():
        row = json.loads(line)
        p = row.get("payload", {})
        if row.get("type") == "response_item" and p.get("type") in {"function_call_output", "custom_tool_call_output"}:
            output = p.get("output", "")
            collect(output)
    return outputs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["generate", "read", "audit"])
    p.add_argument("--cli", type=Path)
    p.add_argument("--url", default="http://127.0.0.1:8766")
    p.add_argument("--prompt", type=Path, default=Path("prompt.json"))
    p.add_argument("--response", type=Path)
    p.add_argument("--session", type=Path)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--end", type=int)
    p.add_argument('--page', type=int, default=1)
    p.add_argument('--check-script', type=Path)
    p.add_argument('--check-python', type=Path)
    p.add_argument('--check-job')
    a = p.parse_args()
    root = Path.cwd().resolve()
    if a.action == "generate":
        if not a.cli:
            p.error("--cli is required")
        runs = root / "som-results"
        runs.mkdir(exist_ok=True)
        folder = runs / f"{len(list(runs.glob('[0-9]*')))+1:04d}"
        folder.mkdir()
        (folder / 'prompt.json').write_bytes(a.prompt.read_bytes())
        result = subprocess.run([str(a.cli), "generate", str(a.prompt), "--url", a.url], capture_output=True, timeout=65)
        (folder / "response.json").write_bytes(result.stdout)
        (folder / "stderr.txt").write_bytes(result.stderr)
        response = json.loads(result.stdout)
        if result.returncode or response.get("status") != "ok":
            print(result.stdout.decode(), end="")
            raise SystemExit(result.returncode or 1)
        install(response, root)
        (folder / "manifest.json").write_text(json.dumps({"response_sha256": digest(result.stdout),
            'prompt_sha256': digest((folder / 'prompt.json').read_bytes()),
            'representation': 'complete Rust CLI JSON response, including every returned source byte',
            "files": {k: digest(v.encode()) for k,v in response["files"].items()}}, indent=2)+"\n")
        page(result.stdout, delivery_id=str((folder/'response.json').resolve()))
        if a.check_script:
            if not a.check_python or not a.check_job:
                p.error('batched checking requires --check-python and --check-job')
            # Source visibility can only be audited after this tool result has
            # reached the model. The controller performs that audit at job end.
            checked = subprocess.run([str(a.check_python), str(a.check_script), a.check_job, '--defer-source-visibility'])
            raise SystemExit(checked.returncode)
    elif a.action == "read":
        page(a.response.read_bytes(), a.start, a.end, a.page, str(a.response.resolve()))
    else:
        result = observed_ranges(a.response.read_bytes(), native_outputs(a.session), str(a.response.resolve()))
        print(json.dumps(result))
        raise SystemExit(0 if result["complete"] else 1)


if __name__ == "__main__":
    main()
