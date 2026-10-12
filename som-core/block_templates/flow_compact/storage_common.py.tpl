

MAX_OBJECT_BYTES = 1048576
object_router = APIRouter(prefix="/objects", dependencies=[Depends(require_access)])


@object_router.put("/{key}")
async def upload_object(request: Request, key: Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")]) -> dict[str, str | int]:
    """Upload object."""
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > MAX_OBJECT_BYTES:
            raise HTTPException(status_code=413, detail="object exceeds 1 MiB")
    receipt = await run_in_threadpool(flow_object_put, key, bytes(buffer), "", "")
    return {"key": receipt.key, "size": receipt.size, "sha256": receipt.sha256}


@object_router.get("/{key}")
def download_object(key: Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")]) -> Response:
    """Download object."""
    body = storage_get(key)
    if len(body) > MAX_OBJECT_BYTES:
        raise HTTPException(status_code=413, detail="stored object exceeds 1 MiB")
    return Response(content=body, media_type="application/octet-stream", headers={"X-Content-SHA256": hashlib.sha256(body).hexdigest()})


@object_router.delete("/{key}", status_code=204)
def remove_object(key: Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")]) -> Response:
    """Remove object."""
    with suppress(ObjectMissing):
        storage_delete(key)
    return Response(status_code=204)
