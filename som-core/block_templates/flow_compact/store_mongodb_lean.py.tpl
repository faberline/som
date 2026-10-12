RUNS, LEASES = "_som_flow_runs", "_som_flow_leases"


def mongo_values(role: str, data: JsonObject, patch: bool = False) -> dict[str, object]:
    """Validate fields before converting dates for MongoDB."""
    return {key: encode_date(value) if isinstance(value, date) else value for key, value in write_values(role, data, patch).items()}


def managed(found: dict[str, object]) -> tuple[int, int]:
    """Validate the private revision and public integer ID."""
    return integer(json_value(found["_id"])), integer(json_value(found["_som_revision"]))


class FlowStore:
    """Share one MongoDB session for writes and their journal."""

    def __init__(self) -> None:
        self.session: AsyncClientSession | None = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Commit once without replaying business work."""
        async with client.start_session() as session, await session.start_transaction():
            self.session = session
            try: yield
            finally: self.session = None

    def active(self) -> AsyncClientSession:
        """Require a transaction for business writes."""
        if self.session is None: raise RuntimeError("transaction required")
        return self.session

    async def one(self, table: str, query: dict[str, object], status: int = 404) -> dict[str, object]:
        """Read one document in the current session."""
        row = await database[table].find_one(query, session=self.session)
        if row is None: raise failure(status, "resource missing")
        return row

    async def change(self, table: str, query: dict[str, object], update: dict[str, object], upsert: bool = False) -> dict[str, object] | None:
        """Return the result of one conditional write."""
        return await database[table].find_one_and_update(query, update, upsert=upsert, return_document=ReturnDocument.AFTER, session=self.session)

    async def delete(self, table: str, query: dict[str, object]) -> None:
        """Delete only an unchanged document."""
        result = await database[table].delete_one(query, session=self.active())
        if result.deleted_count != 1: raise failure(409, "revision conflict")

    async def scope(self, role: str, parent_id: int | None) -> dict[str, object]:
        """Check parent existence even for an empty child list."""
        if role != "child": return {}
        await self.row("parent", parent_id)
        return {FOREIGN_KEY: parent_id}

    async def row(self, role: str, row_id: int | None, parent_id: int | None = None) -> tuple[ResourceRow, int]:
        """Read a concrete model inside its parent scope."""
        query = await self.scope(role, parent_id)
        query["_id"] = row_id
        found = await self.one(RESOURCES[role].table, query)
        found["id"], revision = managed(found)
        return read_row(role, found), revision

    async def rows(self, request: CrudRequest) -> list[ResourceRow]:
        """Apply declared filters and stable pagination."""
        spec = RESOURCES[request.role]
        query = await self.scope(request.role, request.parent_id)
        if spec.filter_field and request.filter_value is not None: query[spec.filter_field] = request.filter_value
        if spec.date_field and request.before is not None: query[spec.date_field] = {"$lt": encode_date(request.before)}
        cursor = database[spec.table].find(query, session=self.session).sort("_id", 1).skip(request.offset).limit(request.limit)
        result = []
        async for found in cursor:
            found["id"], _ = managed(found)
            result.append(read_row(request.role, found))
        return result

    async def created(self, request: CrudRequest) -> dict[str, object]:
        """Allocate an integer ID and insert validated fields."""
        table = RESOURCES[request.role].table
        sequence = await self.change("_som_counters", {"_id": table}, {"$inc": {"value": 1}}, True)
        if sequence is None: raise RuntimeError("counter missing")
        found = mongo_values(request.role, request.data)
        found.update(_id=integer(json_value(sequence["value"])), _som_revision=0, _som_guard=0, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        if request.role == "child": found[FOREIGN_KEY] = request.parent_id
        await database[table].insert_one(found, session=self.active())
        return await self.one(table, {"_id": found["_id"]})

    async def mutate(self, request: CrudRequest, effect: Effect | None = None) -> ResourceRow | None:
        """Protect the parent and apply a checked resource write."""
        self.active()
        table = RESOURCES[request.role].table
        if request.role == "child" and await self.change(RESOURCES["parent"].table, {"_id": request.parent_id}, {"$inc": {"_som_guard": 1}}) is None: raise failure(404, "parent missing")
        if request.operation == "create": found = await self.created(request)
        else:
            previous, revision = await self.row(request.role, request.row_id, request.parent_id)
            predicate: dict[str, object] = {"_id": request.row_id, "_som_revision": revision}
            if request.operation == "delete":
                await self.unreferenced(request.role, request.row_id)
                await self.delete(table, predicate)
                return None
            values = mongo_values(request.role, request.data, True)
            if not values: return previous
            if effect is not None:
                effect.before = json_object(previous.model_dump(mode="json", include=set(values)))
            updated = await self.change(table, predicate, self.patch(values))
            if updated is None: raise failure(409, "revision conflict")
            found = updated
        found["id"], revision = managed(found)
        if effect is not None: effect.row_id, effect.revision, effect.owned = integer(json_value(found["id"])), revision, True
        return read_row(request.role, found)

    def patch(self, values: dict[str, object]) -> dict[str, object]:
        """Change selected fields and their private revision."""
        return {"$set": {**values, "updated_at": datetime.now(UTC)}, "$inc": {"_som_revision": 1}}

    async def unreferenced(self, role: str, row_id: int | None) -> None:
        """Refuse to delete a parent with any child."""
        if role == "parent" and await database[RESOURCES["child"].table].find_one({FOREIGN_KEY: row_id}, session=self.active()): raise failure(409, "parent has children")

    async def reverse(self, effect: Effect) -> int | None:
        """Preserve any independent write to the resource."""
        self.active()
        table = RESOURCES[effect.role].table
        query: dict[str, object] = {"_id": effect.row_id, "_som_revision": effect.revision}
        if effect.undo == "delete_created":
            await self.unreferenced(effect.role, effect.row_id)
            await self.delete(table, query)
            return None
        values = mongo_values(effect.role, effect.before, True)
        if await self.change(table, query, self.patch(values)) is None: raise failure(409, "revision conflict")
        return effect.revision + 1

    async def load(self, run_id: str, owner: str) -> RunRecord:
        """Read only the owner's run."""
        found = await self.one(RUNS, {"_id": run_id, "owner": owner})
        return RunRecord.model_validate(found["record"])

    async def start(self, record: RunRecord) -> tuple[RunRecord, bool]:
        """Create one owner-bound idempotent run."""
        keys = record.model_dump(include={"owner", "flow", "key"})
        try:
            async with self.transaction():
                await database[RUNS].insert_one({**keys, "_id": record.run_id, "epoch": record.epoch, "record": record.model_dump(mode="json")}, session=self.active())
                await database[LEASES].insert_one({"_id": record.run_id, "epoch": record.epoch, "until": time.time()+30}, session=self.active())
            return record, True
        except DuplicateKeyError:
            found = await self.one(RUNS, keys)
            previous = RunRecord.model_validate(found["record"])
            if previous.request_hash != record.request_hash: raise failure(409, "idempotency content changed") from None
            return previous, False

    async def save(self, record: RunRecord) -> None:
        """Fence journal writes without competing with the heartbeat."""
        self.active()
        query: dict[str, object] = {"_id": record.run_id, "epoch": record.epoch}
        lease = await database[LEASES].find_one({**query, "until": {"$gt": time.time()}})
        if lease is None or await self.change(RUNS, query, {"$set": {"record": record.model_dump(mode="json")}}) is None: raise failure(503, "claim lost")

    async def renew(self, record: RunRecord, release: bool = False) -> None:
        """Update only the independent lease document."""
        query: dict[str, object] = {"_id": record.run_id, "epoch": record.epoch}
        if not release: query["until"] = {"$gt": time.time()}
        if await self.change(LEASES, query, {"$set": {"until": 0 if release else time.time()+30}}) is None: raise failure(503, "claim lost")

    async def claim(self, record: RunRecord) -> RunRecord | None:
        """Claim expired work for compensation only."""
        try:
            async with self.transaction():
                lease = await self.change(LEASES, {"_id": record.run_id, "until": {"$lt": time.time()}}, {"$inc": {"epoch": 1}, "$set": {"until": time.time()+30}})
                if lease is None: return None
                latest = await self.load(record.run_id, record.owner)
                if latest.state in {"succeeded", "compensated"}:
                    await self.active().abort_transaction()
                    return latest
                latest.epoch = integer(json_value(lease["epoch"]))
                latest.state = "compensating"
                await self.change(RUNS, {"_id": latest.run_id}, {"$set": {"epoch": latest.epoch, "record": latest.model_dump(mode="json")}})
                return latest
        except _ClaimConflict as error:
            if error.code == 112 and error.has_error_label("TransientTransactionError"):
                return None
            raise
