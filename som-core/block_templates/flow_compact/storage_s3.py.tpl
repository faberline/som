
S3_BUCKET = setting("S3_BUCKET")
s3_endpoint = local_override("SOM_TEST_S3_ENDPOINT")
s3_client = boto3.client("s3", endpoint_url=s3_endpoint, region_name=os.environ.get("AWS_REGION", "us-east-1"), config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}, s3={"addressing_style": "path"}), aws_access_key_id="local-test" if s3_endpoint else None, aws_secret_access_key="local-test" if s3_endpoint else None)


@contextmanager
def storage_errors() -> Iterator[None]:
    """Map SDK failures at the shared provider boundary."""
    try:
        yield
    except (BotoCoreError, ClientError) as error:
        code = error.response.get("Error", {}).get("Code") if isinstance(error, ClientError) else ""
        if code in {"NoSuchKey", "NoSuchVersion", "404"}:
            raise ObjectMissing() from error
        if code in {"PreconditionFailed", "ConditionalRequestConflict", "412", "409"}:
            raise ObjectExists() from error
        raise ObjectUnavailable() from error


def storage_get(key: str) -> bytes:


    """Storage get."""
    with storage_errors():
        result = s3_client.get_object(Bucket=S3_BUCKET, Key=key, Range=f"bytes=0-{MAX_OBJECT_BYTES}")
        with result["Body"] as stream:
            return stream.read(MAX_OBJECT_BYTES + 1)


def storage_delete(key: str) -> None:


    """Storage delete."""
    with storage_errors():
        s3_client.delete_object(Bucket=S3_BUCKET, Key=key)
CLOSERS.append(s3_client.close)
