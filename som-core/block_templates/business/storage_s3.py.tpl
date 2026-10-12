import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

S3_BUCKET = setting("S3_BUCKET")
s3_endpoint = local_override("SOM_TEST_S3_ENDPOINT")
s3_options = {"aws_access_key_id": "local-test", "aws_secret_access_key": "local-test"} if s3_endpoint else {}
s3_client = boto3.client("s3", endpoint_url=s3_endpoint, region_name=os.environ.get("AWS_REGION", "us-east-1"), config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}, s3={"addressing_style": "path"}), **s3_options)


def s3_error(exc):
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404"}:
            raise ObjectMissing() from exc
        if code in {"PreconditionFailed", "ConditionalRequestConflict", "412"}:
            raise ObjectExists() from exc
    raise ObjectUnavailable() from exc


def storage_put(key, body, sha256):
    try:
        s3_client.put_object(Bucket=S3_BUCKET, Key=key, Body=body, ContentType="application/octet-stream", Metadata={"sha256": sha256}, IfNoneMatch="*")
    except (BotoCoreError, ClientError) as exc:
        s3_error(exc)


def storage_get(key):
    try:
        result = s3_client.get_object(Bucket=S3_BUCKET, Key=key, Range=f"bytes=0-{MAX_OBJECT_BYTES}")
        with result["Body"] as stream:
            return stream.read(MAX_OBJECT_BYTES + 1)
    except (BotoCoreError, ClientError) as exc:
        s3_error(exc)


def storage_delete(key):
    try:
        s3_client.delete_object(Bucket=S3_BUCKET, Key=key)
    except (BotoCoreError, ClientError) as exc:
        s3_error(exc)
