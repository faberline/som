from google.cloud import storage as gcs_storage
from google.auth.credentials import AnonymousCredentials as GCSAnonymousCredentials
from google.api_core import exceptions as gcs_errors
import requests as gcs_requests

GCS_BUCKET = setting("GCS_BUCKET")
gcs_endpoint = local_override("STORAGE_EMULATOR_HOST")
gcs_client = gcs_storage.Client(project=setting("GCS_PROJECT"), credentials=GCSAnonymousCredentials() if gcs_endpoint else None)
gcs_bucket = gcs_client.bucket(GCS_BUCKET)


def storage_put(key, body, sha256):
    blob = gcs_bucket.blob(key)
    blob.metadata = {"sha256": sha256}
    try:
        blob.upload_from_string(body, content_type="application/octet-stream", if_generation_match=0, timeout=5, retry=None)
    except (gcs_errors.PreconditionFailed, gcs_errors.Conflict) as exc:
        raise ObjectExists() from exc
    except (gcs_errors.GoogleAPIError, gcs_requests.RequestException) as exc:
        raise ObjectUnavailable() from exc


def storage_get(key):
    try:
        return gcs_bucket.blob(key).download_as_bytes(start=0, end=MAX_OBJECT_BYTES, timeout=5, retry=None)
    except gcs_errors.NotFound as exc:
        raise ObjectMissing() from exc
    except (gcs_errors.GoogleAPIError, gcs_requests.RequestException) as exc:
        raise ObjectUnavailable() from exc


def storage_delete(key):
    try:
        gcs_bucket.blob(key).delete(timeout=5, retry=None)
    except gcs_errors.NotFound as exc:
        raise ObjectMissing() from exc
    except (gcs_errors.GoogleAPIError, gcs_requests.RequestException) as exc:
        raise ObjectUnavailable() from exc
