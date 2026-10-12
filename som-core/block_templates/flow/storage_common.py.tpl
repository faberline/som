class FlowObject(BaseModel):
    """Validated provider receipt with immutable ownership information."""
    key: str
    size: int
    sha256: str
    body: str | None = None
    version: str | None = None
    owned: bool = False


def flow_object_get(key: str) -> FlowObject:
    """Close SDK resources through the existing storage adapter."""
    body = storage_get(key)
    if len(body) > MAX_OBJECT_BYTES:
        raise HTTPException(status_code=413, detail="object is too large")
    return FlowObject(key=key, size=len(body), sha256=hashlib.sha256(body).hexdigest(), body=base64.b64encode(body).decode())


def check_flow_body(body: bytes) -> str:
    """Apply the existing object bounds before an external write."""
    if not body:
        raise HTTPException(status_code=422, detail="empty object")
    if len(body) > MAX_OBJECT_BYTES:
        raise HTTPException(status_code=413, detail="object is too large")
    return hashlib.sha256(body).hexdigest()
