def verify_flow_storage() -> None:
    """Compensatable S3 creates require immutable provider versions."""
    version_required = {{versioned}}
    if version_required and s3_client.get_bucket_versioning(Bucket=S3_BUCKET).get("Status") != "Enabled":
        raise RuntimeError("S3 bucket versioning is required for flow compensation")


def flow_object_put(key: str, body: bytes, owner: str, step: str) -> FlowObject:
    """Persist the provider ownership token on the same conditional create."""
    digest = check_flow_body(body)
    try:
        with storage_errors():
            response = s3_client.put_object(Bucket=S3_BUCKET, Key=key, Body=body, IfNoneMatch="*", ContentType="application/octet-stream", Metadata={"sha256": digest, "som-run": owner, "som-step": step})
    except ObjectExists:
        return existing_object(key, body, owner, step)
    version = response.get("VersionId")
    if version is not None and not isinstance(version, str):
        raise ObjectUnavailable()
    if owner and (not version or version == "null"):
        raise ObjectUnavailable()
    return object_receipt(key, body, version, True)


def flow_object_reverse(key: str, owner: str, step: str, version: str | None) -> bool:
    """Delete this run's version. Unknown creation needs provider proof."""
    if version is None:
        return False
    try:
        with storage_errors():
            response = s3_client.head_object(Bucket=S3_BUCKET, Key=key, VersionId=version)
            if not object_owned(response.get("Metadata"), owner, step):
                return False
            s3_client.delete_object(Bucket=S3_BUCKET, Key=key, VersionId=version)
            return True
    except ObjectMissing:
        return True


def flow_object_version(key: str, owner: str, step: str) -> str | None:
    """Find one owned immutable version without changing provider data."""
    with storage_errors():
        found: str | None = None
        pages = s3_client.get_paginator("list_object_versions").paginate(Bucket=S3_BUCKET, Prefix=key)
        for page in pages:
            for item in page.get("Versions", []):
                if item["Key"] != key:
                    continue
                receipt = s3_client.head_object(Bucket=S3_BUCKET, Key=key, VersionId=item["VersionId"])
                if object_owned(receipt.get("Metadata"), owner, step):
                    if found is not None and found != item["VersionId"]:
                        raise ObjectUnavailable()
                    found = item["VersionId"]
        return found
