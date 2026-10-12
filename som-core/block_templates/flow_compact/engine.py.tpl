flow_router = APIRouter()
FLOW_ERRORS = (HTTPException, BackendError, ValueError)


def flow_reply(record: RunRecord, status_only: bool = False) -> JSONResponse:
    """Expose the owner outcome without private snapshots."""
    content: JsonObject = {"run_id": record.run_id, "state": record.state}
    if record.state == "succeeded":
        content["result"] = record.result
    elif record.state in {"compensated", "recovery_required"}:
        content["detail"] = record.error_detail
    status = {"running": 202, "succeeded": 200, "compensating": 202, "compensated": record.error_status, "recovery_required": 503}
    return JSONResponse(content=content, status_code=200 if status_only else status[record.state])


@asynccontextmanager
async def execution_lease(record: RunRecord) -> AsyncIterator[None]:
    """Cancel work after a failed heartbeat or a lost claim."""
    owner = asyncio.current_task()
    lost = asyncio.Event()
    async def heartbeat() -> None:
        """Renew outside the business transaction."""
        try:
            while True:
                await asyncio.sleep(5)
                await FlowStore().renew(record)
        except (HTTPException, BackendError):
            lost.set()
            if owner is not None:
                owner.cancel()
    task = asyncio.create_task(heartbeat())
    try:
        yield
    except asyncio.CancelledError:
        if lost.is_set():
            raise failure(503, "lease lost") from None
        raise
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def workflow_rows(parent_id: int, store: FlowStore | None = None) -> list[ChildRow]:
    """Share the export scope and row bound."""
    active = store if store is not None else FlowStore()
    rows = await active.rows(CrudRequest(role="child", operation="list", parent_id=parent_id, limit=1001))
    if len(rows) > 1000:
        raise failure(413, "workflow exceeds 1000 rows")
    return [ChildRow.model_validate(row) for row in rows]


class FlowContext:
    """Share the selected effects and durable journal."""

    def __init__(self, record: RunRecord) -> None:
        self.record = record
        self.store = FlowStore()
        self.inputs = FlowInput.model_validate(record.inputs)

    async def save_result[Result](self, name: str, result: Result, checkpoint: bool = True) -> Result:
        """Journal a validated result before the next step."""
        self.record.values[name] = result_json(result)
        if checkpoint:
            await self.persist()
        return result

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Discard staged journal changes after a rollback."""
        previous = self.record.model_copy(deep=True)
        committed = False
        try:
            async with self.store.transaction():
                yield
                await self.store.save(self.record)
            committed = True
        finally:
            if not committed:
                self.record = previous

    async def persist(self) -> None:
        """Commit a fenced control checkpoint."""
        async with self.transaction():
            pass

    async def resource(self, request: CrudRequest, effect: Effect | None) -> ResourceRow | list[ResourceRow] | None:
        """Reuse CRUD validation and database operations."""
        if request.operation == "get":
            return (await self.store.row(request.role, request.row_id, request.parent_id))[0]
        if request.operation == "list":
            return await self.store.rows(request)
        if effect is not None:
            effect.role = request.role
        result = await self.store.mutate(request, effect)
        if effect is not None and effect.owned:
            self.record.effects.append(effect)
        return result

    async def external_inverse(self, effect: Effect) -> None:
        """Record a proven version before deleting that version."""
        if effect.uncertain:
            version = await run_in_threadpool(flow_object_version, effect.key, self.record.run_id, effect.step)
            if version is None:
                raise ObjectUnavailable()
            effect.version, effect.owned, effect.uncertain = version, True, False
            await self.persist()
        if effect.owned and not await run_in_threadpool(flow_object_reverse, effect.key, self.record.run_id, effect.step, effect.version):
            raise ObjectUnavailable()
        self.record.effects.pop()
        await self.persist()

    async def reverse(self) -> None:
        """Undo committed groups in reverse order without replay."""
        self.record.state = "compensating"
        await self.persist()
        while self.record.effects:
            last = self.record.effects[-1]
            if last.key:
                if last.undo != "delete_created":
                    raise RuntimeError("invalid object inverse")
                await self.external_inverse(last)
                continue
            async with self.transaction():
                while self.record.effects and self.record.effects[-1].group == last.group:
                    effect = self.record.effects[-1]
                    if bool(effect.before) != (effect.undo == "restore_patch"):
                        raise RuntimeError("invalid resource inverse")
                    revision = await self.store.reverse(effect) if effect.owned else None
                    self.record.effects.pop()
                    if revision is not None:
                        for prior in self.record.effects:
                            if (prior.role, prior.row_id) == (effect.role, effect.row_id):
                                prior.revision = revision
        self.record.state = "compensated"
        await self.persist()


{{flow_program}}


def business_failure(error: Exception) -> tuple[int, str]:
    """Retain the business status while hiding provider secrets."""
    if isinstance(error, HTTPException):
        return error.status_code, str(error.detail)
    if isinstance(error, ValueError):
        return 422, "invalid flow input or result"
    if isinstance(error, ConflictError):
        return 409, "resource conflict"
    return 503, "provider unavailable"


def unavailable_run(run_id: str) -> HTTPException:
    """Report an unfinished outcome without claiming a durable state."""
    return HTTPException(503, {"run_id": run_id, "message": "execution journal or lease unavailable"})


async def recover_record(record: RunRecord) -> RunRecord:
    """Claim expired work and finish only its compensation."""
    try:
        claimed = await FlowStore().claim(record)
        if claimed is None or claimed.state in {"succeeded", "compensated"}:
            return await FlowStore().load(record.run_id, record.owner)
    except BackendError as error:
        raise unavailable_run(record.run_id) from error
    return await run_record(claimed, inverse=True)


async def run_record(record: RunRecord, inverse: bool = False) -> RunRecord:
    """Run forward once or resume a fenced compensation."""
    ctx = FlowContext(record)
    try:
        async with execution_lease(record):
            if inverse:
                await ctx.reverse()
            else:
                ctx.record.result = await forward(ctx)
                ctx.record.state = "succeeded"
                await ctx.persist()
    except FLOW_ERRORS as error:
        try:
            latest = await FlowStore().load(record.run_id, record.owner)
        except BackendError as unavailable:
            raise unavailable_run(record.run_id) from unavailable
        if latest.epoch != record.epoch:
            return latest
        if inverse:
            latest.state = "recovery_required"
            with suppress(HTTPException, BackendError):
                await FlowContext(latest).persist()
        else:
            latest.error_status, latest.error_detail = business_failure(error)
            try:
                await FlowContext(latest).persist()
            except (HTTPException, BackendError) as unavailable:
                raise unavailable_run(record.run_id) from unavailable
            await FlowStore().renew(latest, release=True)
            return await recover_record(latest)
    finally:
        with suppress(HTTPException, BackendError):
            await asyncio.shield(FlowStore().renew(record, release=True))
    try:
        return await FlowStore().load(record.run_id, record.owner)
    except BackendError as unavailable:
        raise unavailable_run(record.run_id) from unavailable


@flow_router.post("/flows/" + FLOW_NAME + "/runs")
async def execute_flow(payload: FlowInput, identity: Annotated[Identity, Depends(require_access)], idempotency_key: Annotated[str, Header(min_length=1, max_length=128)]) -> JSONResponse:
    """Create one owner-bound idempotent run."""
    inputs = model_json(payload, True)
    digest = hashlib.sha256(json.dumps({"plan": PLAN_HASH, "input": inputs}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    record = RunRecord(run_id=uuid.uuid4().hex, owner=identity["sub"], flow=FLOW_NAME, key=idempotency_key, request_hash=digest, inputs=inputs)
    record, created = await FlowStore().start(record)
    return flow_reply(await run_record(record) if created else record)


@flow_router.get("/flow-runs/{run_id}")
async def flow_status(run_id: str, identity: Annotated[Identity, Depends(require_access)]) -> JSONResponse:
    """Read only the caller's run."""
    return flow_reply(await FlowStore().load(run_id, identity["sub"]), status_only=True)


@flow_router.post("/flow-runs/{run_id}/recover")
async def recover_flow(run_id: str, identity: Annotated[Identity, Depends(require_access)]) -> JSONResponse:
    """Finish compensation without redoing forward operations."""
    return flow_reply(await recover_record(await FlowStore().load(run_id, identity["sub"])))
