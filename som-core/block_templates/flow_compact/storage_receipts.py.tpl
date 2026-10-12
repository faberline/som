class FlowObject(BaseModel):
    """Validate a provider receipt before using it in a flow."""
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    key: str
    size: int
    sha256: str
    body: str
    version: str | None = None
    owned: bool = False


def object_owned(value: object, owner: str, step: str) -> bool:
    """Check typed provider metadata without guessing ownership."""
    try:
        metadata = TypeAdapter(dict[str, str]).validate_python({} if value is None else value, strict=True)
    except ValueError as error:
        raise ObjectUnavailable() from error
    return metadata.get("som-run") == owner and metadata.get("som-step") == step


def flow_object_get(key: str) -> FlowObject:
    """Read bounded bytes and construct a typed receipt."""
    return object_receipt(key, storage_get(key))


def object_receipt(key: str, body: bytes, version: str | None = None, owned: bool = False) -> FlowObject:
    """Build the same first-party receipt for reads and creates."""
    if len(body) > MAX_OBJECT_BYTES:
        raise HTTPException(status_code=413, detail="object is too large")
    return FlowObject(key=key, size=len(body), sha256=hashlib.sha256(body).hexdigest(), body=base64.b64encode(body).decode(), version=version, owned=owned)


def existing_object(key: str, body: bytes, owner: str, step: str) -> FlowObject:
    """Reuse equal bytes without claiming another execution's version."""
    existing = flow_object_get(key)
    if existing.body != base64.b64encode(body).decode():
        raise ObjectExists()
    if owner:
        existing.version = flow_object_version(key, owner, step)
        existing.owned = existing.version is not None
    return existing


def check_flow_body(body: bytes) -> str:
    """Validate content before any provider write."""
    if not body:
        raise HTTPException(status_code=422, detail="empty object")
    if len(body) > MAX_OBJECT_BYTES:
        raise HTTPException(status_code=413, detail="object is too large")
    return hashlib.sha256(body).hexdigest()
