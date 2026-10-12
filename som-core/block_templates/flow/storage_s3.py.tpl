def verify_flow_storage() -> None:
    """Compensatable S3 creates require immutable provider versions."""
    version_required = {{versioned}}
    if version_required and s3_client.get_bucket_versioning(Bucket=S3_BUCKET).get("Status") != "Enabled":
        raise RuntimeError("S3 bucket versioning is required for flow compensation")


def flow_object_put(key: str, body: bytes, owner: str, step: str) -> FlowObject:
    """Persist the provider ownership token on the same conditional create."""
    digest = check_flow_body(body)
    try:
        response = s3_client.put_object(Bucket=S3_BUCKET, Key=key, Body=body, IfNoneMatch="*", ContentType="application/octet-stream", Metadata={"sha256": digest, "som-run": owner, "som-step": step})
        version = response.get("VersionId")
        if not isinstance(version, str) or not version or version == "null":
            raise ObjectUnavailable()
        return FlowObject(key=key, size=len(body), sha256=digest, version=version, owned=True)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"PreconditionFailed", "ConditionalRequestConflict", "412", "409"}:
            existing = flow_object_get(key)
            if existing.body != base64.b64encode(body).decode(): raise ObjectExists() from error
            return existing
        s3_error(error)
    except BotoCoreError as error:
        s3_error(error)


def flow_object_reverse(key: str, owner: str, step: str, version: str | None) -> bool:
    """Delete this run's version. Unknown creation needs provider proof."""
    try:
        if version is None:
            listed = s3_client.list_object_versions(Bucket=S3_BUCKET, Prefix=key, MaxKeys=100)
            for item in listed.get("Versions", []):
                if item["Key"] != key: continue
                metadata = s3_client.head_object(Bucket=S3_BUCKET, Key=key, VersionId=item["VersionId"]).get("Metadata", {})
                if metadata.get("som-run") == owner and metadata.get("som-step") == step:
                    version = item["VersionId"]
                    break
            if version is None: return False
        response = s3_client.head_object(Bucket=S3_BUCKET, Key=key, VersionId=version)
        metadata = response.get("Metadata", {})
        if metadata.get("som-run") != owner or metadata.get("som-step") != step:
            return False
        s3_client.delete_object(Bucket=S3_BUCKET, Key=key, VersionId=version, IfMatch=response["ETag"])
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "NoSuchVersion", "404"}:
            return version is not None
        s3_error(error)
    except BotoCoreError as error:
        s3_error(error)
