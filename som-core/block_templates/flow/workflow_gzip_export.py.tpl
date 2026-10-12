def workflow_payload(rows: list[JsonObject]) -> JsonObject:
    """Export reproducible gzip bytes with a fixed timestamp."""
    data = json_value(rows)
    body = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    return workflow_result(data, gzip.compress(body, mtime=0))
