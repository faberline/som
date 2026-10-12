def verify_flow_storage() -> None:
    """GCS supplies immutable generations on conditional creation."""


def flow_generation(value: object) -> int:
    """Accept a positive provider generation, never a boolean."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, str) and 1 <= len(value) <= 20 and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    raise ObjectUnavailable()


def flow_object_put(key: str, body: bytes, owner: str, step: str) -> FlowObject:
    """Keep ownership metadata and generation in the same create."""
    digest = check_flow_body(body)
    blob = gcs_bucket.blob(key)
    blob.metadata = {"sha256": digest, "som-run": owner, "som-step": step}
    try:
        with storage_errors():
            blob.upload_from_string(body, if_generation_match=0, timeout=5, retry=None)
    except ObjectExists:
        return existing_object(key, body, owner, step)
    return object_receipt(key, body, str(flow_generation(blob.generation)), True)


def flow_object_reverse(key: str, owner: str, step: str, version: str | None) -> bool:
    """Delete only the proved generation owned by this step."""
    if version is None:
        return False
    blob = gcs_bucket.blob(key, generation=flow_generation(version))
    try:
        with storage_errors():
            blob.reload(timeout=5, retry=None)
            if not object_owned(blob.metadata, owner, step):
                return False
            blob.delete(if_generation_match=flow_generation(blob.generation), timeout=5, retry=None)
            return True
    except ObjectMissing:
        return True


def flow_object_version(key: str, owner: str, step: str) -> str | None:
    """Find an owned generation without changing another version."""
    try:
        with storage_errors():
            found: str | None = None
            for blob in gcs_client.list_blobs(GCS_BUCKET, prefix=key, versions=True, timeout=5, retry=None):
                if blob.name != key:
                    continue
                if object_owned(blob.metadata, owner, step):
                    version = str(flow_generation(blob.generation))
                    if found is not None and found != version:
                        raise ObjectUnavailable()
                    found = version
            return found
    except ObjectMissing:
        return None
