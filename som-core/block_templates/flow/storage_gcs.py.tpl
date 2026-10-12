def verify_flow_storage() -> None:
    """GCS supplies immutable generations on conditional creation."""


def flow_generation(value: object) -> int:
    """Accept only a positive provider generation, never a boolean."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, str) and 1 <= len(value) <= 20 and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    raise ObjectUnavailable()


def flow_object_put(key: str, body: bytes, owner: str, step: str) -> FlowObject:
    """Keep provider ownership and the created generation in one request."""
    digest = check_flow_body(body)
    blob = gcs_bucket.blob(key)
    blob.metadata = {"sha256": digest, "som-run": owner, "som-step": step}
    try:
        blob.upload_from_string(body, if_generation_match=0, timeout=5, retry=None)
        generation = flow_generation(blob.generation)
        return FlowObject(key=key, size=len(body), sha256=digest, version=str(generation), owned=True)
    except (gcs_errors.PreconditionFailed, gcs_errors.Conflict) as error:
        existing = flow_object_get(key)
        if existing.body != base64.b64encode(body).decode(): raise ObjectExists() from error
        return existing
    except (gcs_errors.GoogleAPIError, requests.RequestException) as error:
        raise ObjectUnavailable() from error


def flow_object_reverse(key: str, owner: str, step: str, version: str | None) -> bool:
    """A changed generation may not be deleted by this execution."""
    blob = gcs_bucket.blob(key, generation=flow_generation(version) if version is not None else None)
    try:
        blob.reload(timeout=5, retry=None)
        if blob.metadata is None:
            return False
        metadata = TypeAdapter(dict[str, str]).validate_python(blob.metadata, strict=True)
        generation = flow_generation(blob.generation)
        if metadata.get("som-run") != owner or metadata.get("som-step") != step:
            return False
        blob.delete(if_generation_match=generation, timeout=5, retry=None)
        return True
    except gcs_errors.NotFound:
        return version is not None
    except (gcs_errors.GoogleAPIError, requests.RequestException, ValidationError) as error:
        raise ObjectUnavailable() from error
