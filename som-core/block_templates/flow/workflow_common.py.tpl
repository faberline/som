def workflow_result(data: JsonValue, body: bytes | None = None) -> JsonObject:
    """Encode the selected calculation and its exact output bytes."""
    if body is None:
        body = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    return {"body": base64.b64encode(body).decode(), "size": len(body), "sha256": hashlib.sha256(body).hexdigest(), "data": data}


