import hashlib
import re

from fastapi import Path as ApiPath
from starlette.concurrency import run_in_threadpool

MAX_OBJECT_BYTES = 1048576
object_router = APIRouter(prefix="/objects", dependencies=[Depends(require_access)])


class ObjectMissing(Exception):
    pass


class ObjectExists(Exception):
    pass


class ObjectUnavailable(Exception):
    pass


def object_response_error(error):
    if isinstance(error, ObjectMissing):
        raise HTTPException(status_code=404, detail="object not found") from error
    raise HTTPException(status_code=503, detail="object storage unavailable; retry the request") from error


@object_router.put("/{key}")
async def upload_object(request: Request, key: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_OBJECT_BYTES:
            raise HTTPException(status_code=413, detail="object exceeds 1 MiB")
    if not body:
        raise HTTPException(status_code=422, detail="empty object")
    body = bytes(body)
    sha256 = hashlib.sha256(body).hexdigest()
    try:
        await run_in_threadpool(storage_put, key, body, sha256)
    except ObjectExists:
        try:
            existing = await run_in_threadpool(storage_get, key)
        except (ObjectMissing, ObjectUnavailable) as exc:
            object_response_error(exc)
        if existing != body:
            raise HTTPException(status_code=409, detail="object key already contains different data")
    except ObjectUnavailable as exc:
        object_response_error(exc)
    return {"key": key, "size": len(body), "sha256": sha256}


@object_router.get("/{key}")
def download_object(key: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")):
    try:
        body = storage_get(key)
    except (ObjectMissing, ObjectUnavailable) as exc:
        object_response_error(exc)
    if len(body) > MAX_OBJECT_BYTES:
        raise HTTPException(status_code=413, detail="stored object exceeds 1 MiB")
    return Response(content=body, media_type="application/octet-stream", headers={"X-Content-SHA256": hashlib.sha256(body).hexdigest()})


@object_router.delete("/{key}", status_code=204)
def remove_object(key: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")):
    try:
        storage_delete(key)
    except ObjectMissing:
        pass
    except ObjectUnavailable as exc:
        object_response_error(exc)
    return Response(status_code=204)
