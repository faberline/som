def flow_reply(record: RunRecord, status_only: bool = False) -> JSONResponse:
    """Expose outcomes, never input data or reverse snapshots."""
    content: JsonObject = {"run_id": record.run_id, "state": record.state}
    if record.state == "succeeded": content["result"] = record.result
    elif record.state in {"compensated", "recovery_required"}: content["detail"] = record.error_detail
    status = {"running": 202, "succeeded": 200, "compensating": 202, "compensated": record.error_status, "recovery_required": 503}[record.state]
    return JSONResponse(content=content, status_code=200 if status_only else status)


@asynccontextmanager
async def execution_lease(record: RunRecord) -> AsyncIterator[None]:
    """Renew only while this request executes; no recovery scheduler."""
    async def heartbeat() -> None:
        """Keep the current epoch alive without changing journal snapshots."""
        while True:
            await asyncio.sleep(5)
            await FlowStore().renew(record)
    task: asyncio.Task[None] = asyncio.create_task(heartbeat())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def ordered_compare[Scalar: (int, float, str)](operation: str, left: Scalar, right: Scalar) -> bool:
    """Compare operands only after checking their concrete scalar types."""
    if operation == "lt": return left < right
    if operation == "le": return left <= right
    if operation == "gt": return left > right
    return left >= right


class FlowRunner:
    """Execute exactly the validated model choices and durable inverses."""

    def __init__(self, record: RunRecord) -> None:
        self.record = record
        self.store = FlowStore()

    def value(self, ref: FlowRef) -> JsonValue:
        """Resolve one typed reference without evaluating source text."""
        if isinstance(ref, ConstantRef): return ref.literal
        if isinstance(ref, InputRef): return self.record.inputs[ref.input]
        result = self.record.values[ref.step]
        for field in ref.field.split(".") if ref.field else []:
            result = json_object(result)[field]
        return result

    def condition(self, predicate: Predicate) -> bool:
        """Branch only on the declared predicate."""
        left = self.value(predicate.left)
        if predicate.op == "is_null": return left is None
        if predicate.right is None:
            raise RuntimeError("comparison operand missing")
        right = self.value(predicate.right)
        if predicate.op == "eq": return left == right
        if predicate.op == "ne": return left != right
        if isinstance(left, str) and isinstance(right, str):
            return ordered_compare(predicate.op, left, right)
        if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
            return ordered_compare(predicate.op, float(left), float(right))
        raise HTTPException(status_code=422, detail="incompatible comparison data")

    async def persist(self) -> None:
        """Save a non-business checkpoint with its current claim."""
        async with self.store.transaction():
            await self.store.save(self.record)

    async def walk(self, nodes: list[FlowNode], group: str = "") -> None:
        """Do not reorder, fill or replay any node."""
        for node in nodes:
            if isinstance(node, TransactionNode):
                if CONTROL_IDS[node.id] != "transaction": raise RuntimeError("transaction block missing")
                async with self.store.transaction():
                    await self.walk(node.steps, node.name)
                    await self.store.save(self.record)
            elif isinstance(node, BranchNode):
                if CONTROL_IDS[node.id] != "if": raise RuntimeError("branch block missing")
                taken = self.condition(node.condition)
                self.record.branches[node.name] = taken
                visible = set(self.record.values)
                await self.walk(node.then if taken else node.otherwise, group)
                exports = {name: self.value(binding.then if taken else binding.otherwise) for name, binding in node.exports.items()}
                self.record.values = {name: value for name, value in self.record.values.items() if name in visible}
                self.record.values.update(exports)
                if not group: await self.persist()
            else:
                await self.action(node, group)

    async def action(self, node: ActionNode, group: str) -> None:
        """Dispatch only a selected, registered approved action."""
        operation = ACTION_IDS[node.id]
        arguments = {key: self.value(value) for key, value in node.args.items()}
        effect = Effect(group=group, step=node.name, block=node.id, undo=node.undo or "") if node.undo is not None else None
        if operation == "authorize":
            result: JsonValue = {"sub": self.record.owner, "role": "editor"}
        elif operation.startswith("object."):
            result = await self.object_action(node, arguments, effect)
        elif operation == "workflow.run":
            rows = await self.store.rows(CrudRequest(role="child", operation="list", parent_id=integer(arguments["parent_id"]), limit=1001))
            if len(rows) > 1000: raise HTTPException(status_code=413, detail="workflow limited to 1000 rows")
            result = workflow_payload(rows)
        elif operation in {"batch.create", "workflow.import"}:
            effect = None
            result = await self.batch_action(node, arguments, group)
        else:
            result = await self.resource_action(operation, arguments, effect)
        if node.id in CHECKED_IDS:
            integer(json_object(result).get("id"))
        self.record.values[node.name] = result
        if effect is not None and effect.owned and not operation.startswith("object."):
            self.record.effects.append(effect)
        if not group and not operation.startswith("object."):
            await self.persist()

    async def resource_action(self, operation: str, arguments: JsonObject, effect: Effect | None) -> JsonValue:
        """Validate all business data with concrete input/read models."""
        role, action = operation.split(".")
        request = CrudRequest(role="parent" if role == "parent" else "child", operation="get" if action == "get" else "list" if action == "list" else "patch" if action == "patch" else "create",
                              parent_id=integer(arguments["parent_id"]) if "parent_id" in arguments else None,
                              row_id=integer(arguments[role+"_id"]) if action in {"get", "patch"} else None,
                              data=json_object(arguments["data"]) if "data" in arguments else {})
        if action == "get":
            if request.row_id is None: raise HTTPException(status_code=404, detail="row not found")
            return (await self.store.row(role, request.row_id, request.parent_id))[0]
        if action == "list": return json_value(await self.store.rows(request))
        if effect is not None: effect.role = role
        return await self.store.mutate(request, effect)

    async def batch_action(self, node: ActionNode, arguments: JsonObject, group: str) -> JsonValue:
        """Record every created row under the selected batch inverse."""
        operation = ACTION_IDS[node.id]
        parent: JsonObject | None = None
        if operation == "batch.create":
            effect = Effect(group=group, step=node.name, block=node.id, undo=node.undo or "", role="parent")
            parent = await self.store.mutate(CrudRequest(role="parent", operation="create", data=json_object(arguments["parent"])), effect)
            self.record.effects.append(effect)
            parent_id = integer(parent["id"])
            values = arguments["children"]
            if not isinstance(values, list) or not 1 <= len(values) <= 20:
                raise HTTPException(status_code=422, detail="expected 1..20 children")
            children = [json_object(value) for value in values]
        else:
            parent_id = integer(arguments["parent_id"])
            children = csv_records(text_value(arguments["csv"]))
        rows: list[JsonValue] = []
        for value in children:
            effect = Effect(group=group, step=node.name, block=node.id, undo=node.undo or "", role="child")
            rows.append(await self.store.mutate(CrudRequest(role="child", operation="create", parent_id=parent_id, data=value), effect))
            self.record.effects.append(effect)
        return {"parent": parent, "children": rows} if operation == "batch.create" else rows

    async def object_action(self, node: ActionNode, arguments: JsonObject, effect: Effect | None) -> JsonObject:
        """Intent precedes provider calls. Unknown outcomes stay reversible."""
        key = text_value(arguments["key"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key):
            raise HTTPException(status_code=422, detail="invalid object key")
        if ACTION_IDS[node.id] == "object.get":
            return json_object((await run_in_threadpool(flow_object_get, key)).model_dump(mode="json"))
        if effect is None: raise RuntimeError("object creation needs an inverse")
        effect.group = "external_"+node.name
        effect.key = key
        effect.uncertain = True
        self.record.effects.append(effect)
        await self.persist()
        body = base64.b64decode(text_value(arguments["body"]), validate=True)
        try:
            receipt = await run_in_threadpool(flow_object_put, key, body, self.record.run_id, node.name)
        except ObjectExists:
            effect.uncertain = False
            await self.persist()
            raise
        effect.owned = receipt.owned
        effect.version = receipt.version
        effect.uncertain = False
        result = json_object(receipt.model_dump(mode="json"))
        self.record.values[node.name] = result
        await self.persist()
        return result

    async def reverse(self) -> None:
        """Reverse each group atomically. Stop at the first conflict."""
        self.record.state = "compensating"
        await self.persist()
        while self.record.effects:
            last = self.record.effects[-1]
            if last.key:
                if INVERSE_IDS[last.undo] != "delete_created": raise RuntimeError("object inverse missing")
                completed = (not last.owned and not last.uncertain) or await run_in_threadpool(flow_object_reverse, last.key, self.record.run_id, last.step, last.version)
                if not completed:
                    raise HTTPException(status_code=503, detail="external outcome remains uncertain")
                self.record.effects.pop()
                await self.persist()
                continue
            group = last.group
            async with self.store.transaction():
                while self.record.effects and self.record.effects[-1].group == group:
                    effect = self.record.effects[-1]
                    inverse = INVERSE_IDS[effect.undo]
                    if bool(effect.fields) != (inverse == "restore_patch"):
                        raise RuntimeError("inverse and committed effect disagree")
                    if effect.owned:
                        revision = await self.store.reverse(effect)
                        if revision is not None:
                            for previous in self.record.effects[:-1]:
                                if previous.role == effect.role and previous.row_id == effect.row_id:
                                    previous.revision = revision
                    self.record.effects.pop()
                await self.store.save(self.record)
        self.record.state = "compensated"
        await self.persist()


async def compensate(record: RunRecord, error: tuple[int, str] | None = None) -> RunRecord:
    """Claim and finish only reverse work; never resume forward nodes."""
    store = FlowStore()
    claimed = await store.claim(record)
    if claimed is None: return record
    if claimed.state in {"succeeded", "compensated"}: return claimed
    runner = FlowRunner(claimed)
    if error is not None:
        claimed.error_status, claimed.error_detail = error
    try:
        async with execution_lease(claimed):
            await runner.reverse()
    except (HTTPException, BackendError, ObjectMissing, ObjectExists, ObjectUnavailable):
        latest = await store.load(record.run_id, record.owner)
        if latest.epoch != claimed.epoch:
            return latest
        latest.state = "recovery_required"
        try:
            await FlowRunner(latest).persist()
        except (HTTPException, BackendError):
            return await store.load(record.run_id, record.owner)
        claimed = latest
    await store.renew(claimed, release=True)
    return claimed


flow_router = APIRouter()


@flow_router.post("/flows/" + PLAN.name + "/runs")
async def execute_flow(payload: FlowInput, identity: Annotated[Identity, Depends(require_access)], idempotency_key: Annotated[str, Header(min_length=1, max_length=128)]) -> JSONResponse:
    """Execute once under an owner-scoped idempotency key."""
    inputs = json_object(payload.model_dump(mode="json", exclude_unset=True))
    digest = hashlib.sha256(json.dumps({"plan": PLAN_HASH, "input": inputs}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    record = RunRecord(run_id=uuid.uuid4().hex, owner=identity["sub"], flow=PLAN.name, key=idempotency_key, request_hash=digest, inputs=inputs)
    store = FlowStore()
    record, created = await store.start(record)
    if not created: return flow_reply(record)
    runner = FlowRunner(record)
    try:
        async with execution_lease(record):
            await runner.walk(PLAN.steps)
            record.result = {name: runner.value(ref) for name, ref in PLAN.result.items()}
            record.state = "succeeded"
            await runner.persist()
    except (HTTPException, BackendError, ObjectMissing, ObjectExists, ObjectUnavailable) as error:
        latest = await store.load(record.run_id, record.owner)
        if latest.epoch != record.epoch:
            return flow_reply(latest)
        status = error.status_code if isinstance(error, HTTPException) else 409 if isinstance(error, (ConflictError, ObjectExists)) else 404 if isinstance(error, ObjectMissing) else 503
        detail = str(error.detail) if isinstance(error, HTTPException) else "resource conflict" if isinstance(error, (ConflictError, ObjectExists)) else "provider unavailable"
        await store.renew(record, release=True)
        record = await compensate(latest, (status, detail))
    except asyncio.CancelledError:
        await asyncio.shield(store.renew(record, release=True))
        raise
    await store.renew(record, release=True)
    return flow_reply(record)


@flow_router.get("/flow-runs/{run_id}")
async def flow_status(run_id: str, identity: Annotated[Identity, Depends(require_access)]) -> JSONResponse:
    """Return the owner's public execution status."""
    return flow_reply(await FlowStore().load(run_id, identity["sub"]), status_only=True)


@flow_router.post("/flow-runs/{run_id}/recover")
async def recover_flow(run_id: str, identity: Annotated[Identity, Depends(require_access)]) -> JSONResponse:
    """Recover compensation after a failed request or process restart."""
    record = await FlowStore().load(run_id, identity["sub"])
    return flow_reply(await compensate(record))
