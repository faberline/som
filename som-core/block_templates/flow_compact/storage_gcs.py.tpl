
def local_gcs_credentials() -> Credentials:

    """Local gcs credentials."""
    return AnonymousCredentials()  # type: ignore[no-untyped-call]


GCS_BUCKET = setting("GCS_BUCKET")
gcs_endpoint = local_override("STORAGE_EMULATOR_HOST")
gcs_client = gcs_storage.Client(project=setting("GCS_PROJECT"), credentials=local_gcs_credentials() if gcs_endpoint else None)
gcs_bucket = gcs_client.bucket(GCS_BUCKET)


@contextmanager
def storage_errors() -> Iterator[None]:
    """Map SDK failures at the shared provider boundary."""
    try:
        yield
    except gcs_errors.NotFound as error:
        raise ObjectMissing() from error
    except (gcs_errors.PreconditionFailed, gcs_errors.Conflict) as error:
        raise ObjectExists() from error
    except (gcs_errors.GoogleAPIError, requests.RequestException) as error:
        raise ObjectUnavailable() from error


def storage_get(key: str) -> bytes:


    """Storage get."""
    with storage_errors():
        value: object = gcs_bucket.blob(key).download_as_bytes(start=0, end=MAX_OBJECT_BYTES, timeout=5, retry=None)
        if not isinstance(value, bytes):
            raise ObjectUnavailable("SDK did not return bytes")
        return value


def storage_delete(key: str) -> None:


    """Storage delete."""
    with storage_errors():
        gcs_bucket.blob(key).delete(timeout=5, retry=None)
CLOSERS.append(gcs_client.close)
