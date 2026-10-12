class FlowStore:
    """MongoDB mutations and journal share one real replica-set transaction."""

    def __init__(self) -> None:
        self.session: AsyncClientSession | None = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """No automatic replay of forward business operations."""
        async with client.start_session() as session, await session.start_transaction():
            self.session = session
            try:
                yield
            finally:
                self.session = None

    def active(self) -> AsyncClientSession:
        """Reject mutations without their declared transaction."""
        if self.session is None:
            raise RuntimeError("database transaction required")
        return self.session

    async def row(self, role: str, row_id: int, parent_id: int | None = None) -> tuple[JsonObject, int]:
        """Read a concrete resource and enforce its parent scope."""
        query: dict[str, object] = {"_id": row_id}
        if role == "child":
            if parent_id is None:
                raise HTTPException(status_code=404, detail="parent not found")
            await self.row("parent", parent_id)
            query[FOREIGN_KEY] = parent_id
        found = await database[RESOURCES[role].table].find_one(query, session=self.session)
        if found is None:
            raise HTTPException(status_code=404, detail=role + " not found")
        revision = found.get("_som_revision", 0)
        if not isinstance(revision, int):
            raise RuntimeError("invalid managed revision")
        found["id"] = row_id
        return read_row(role, found), revision

    async def rows(self, request: CrudRequest) -> list[JsonObject]:
        """Use the same filters, ID order and bounds as PostgreSQL."""
        spec = RESOURCES[request.role]
        query: dict[str, object] = {}
        if request.role == "child":
            if request.parent_id is None:
                raise HTTPException(status_code=404, detail="parent not found")
            await self.row("parent", request.parent_id)
            query[FOREIGN_KEY] = request.parent_id
        if spec.filter_field and request.filter_value is not None:
            query[spec.filter_field] = request.filter_value
        if spec.date_field and request.before is not None:
            query[spec.date_field] = {"$lt": encode_date(request.before)}
        cursor = database[spec.table].find(query, session=self.session).sort("_id", 1).skip(request.offset).limit(request.limit)
        result = []
        async for found in cursor:
            found["id"] = found["_id"]
            result.append(read_row(request.role, found))
        return result

    async def created(self, request: CrudRequest) -> dict[str, object]:
        """Allocate the integer ID and insert one validated document."""
        values = write_values(request.role, request.data)
        sequence = await database["_som_counters"].find_one_and_update({"_id": RESOURCES[request.role].table}, {"$inc": {"value": 1}}, upsert=True, return_document=ReturnDocument.AFTER, session=self.active())
        if sequence is None or not isinstance(sequence["value"], int):
            raise RuntimeError("invalid integer counter")
        found: dict[str, object] = {key: encode_date(value) if isinstance(value, date) else value for key, value in values.items()}
        found.update(_id=sequence["value"], _som_revision=0, _som_guard=0, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        if request.role == "child": found[FOREIGN_KEY] = request.parent_id
        await database[RESOURCES[request.role].table].insert_one(found, session=self.active())
        return found

    async def mutate(self, request: CrudRequest, effect: Effect | None = None) -> JsonObject:
        """Mutate concrete validated fields; guard relationships on writes."""
        session = self.active()
        collection = database[RESOURCES[request.role].table]
        if request.role == "child" and await database[RESOURCES["parent"].table].find_one_and_update({"_id": request.parent_id}, {"$inc": {"_som_guard": 1}}, session=session) is None:
            raise HTTPException(status_code=404, detail="parent not found")
        if request.operation == "create":
            found = await self.created(request)
        else:
            if request.row_id is None:
                raise HTTPException(status_code=404, detail="row not found")
            previous, revision = await self.row(request.role, request.row_id, request.parent_id)
            predicate = {"_id": request.row_id, "_som_revision": revision}
            if request.operation == "delete":
                if request.role == "parent" and await database[RESOURCES["child"].table].find_one({FOREIGN_KEY: request.row_id}, session=session):
                    raise HTTPException(status_code=409, detail="parent has children")
                deleted = await collection.delete_one(predicate, session=session)
                if deleted.deleted_count != 1:
                    raise HTTPException(status_code=409, detail="concurrent resource change")
                return {}
            values = write_values(request.role, request.data, True)
            if not values:
                return previous
            if effect is not None:
                effect.before = {key: previous[key] for key in values}
                effect.fields = list(values)
            changed: dict[str, object] = {key: encode_date(value) if isinstance(value, date) else value for key, value in values.items()}
            changed["updated_at"] = datetime.now(UTC)
            updated = await collection.find_one_and_update(predicate, {"$set": changed, "$inc": {"_som_revision": 1}}, return_document=ReturnDocument.AFTER, session=session)
            if updated is None:
                raise HTTPException(status_code=409, detail="concurrent resource change")
            found = updated
        row_id = integer(JSON_VALUE.validate_python(found["_id"], strict=True))
        found["id"] = row_id
        result = read_row(request.role, found)
        if effect is not None:
            effect.row_id = row_id
            effect.revision = integer(JSON_VALUE.validate_python(found["_som_revision"], strict=True))
            effect.owned = True
        return result

    async def reverse(self, effect: Effect) -> int | None:
        """Refuse to undo any later independent write."""
        collection = database[RESOURCES[effect.role].table]
        predicate = {"_id": effect.row_id, "_som_revision": effect.revision}
        if effect.fields:
            values = write_values(effect.role, effect.before, True)
            changed: dict[str, object] = {key: encode_date(value) if isinstance(value, date) else value for key, value in values.items()}
            changed["updated_at"] = datetime.now(UTC)
            result = await collection.update_one(predicate, {"$set": changed, "$inc": {"_som_revision": 1}}, session=self.active())
            count = result.matched_count
        else:
            if effect.role == "parent" and await database[RESOURCES["child"].table].find_one({FOREIGN_KEY: effect.row_id}, session=self.active()):
                raise HTTPException(status_code=409, detail="parent has foreign children")
            deleted = await collection.delete_one(predicate, session=self.active())
            count = deleted.deleted_count
        if count != 1:
            raise HTTPException(status_code=409, detail="compensation revision conflict")
        return effect.revision+1 if effect.fields else None

    async def load(self, run_id: str, owner: str) -> RunRecord:
        """Only the owner can inspect execution data."""
        found = await database["_som_flow_runs"].find_one({"_id": run_id, "owner": owner})
        if found is None:
            raise HTTPException(status_code=404, detail="run not found")
        return RunRecord.model_validate(found["record"])

    async def start(self, record: RunRecord) -> tuple[RunRecord, bool]:
        """Unique owner/flow/key is enforced in the real database."""
        try:
            async with self.transaction():
                await database["_som_flow_runs"].insert_one({"_id": record.run_id, "owner": record.owner, "flow": record.flow, "key": record.key, "epoch": record.epoch, "record": record.model_dump(mode="json")}, session=self.active())
                await database["_som_flow_leases"].insert_one({"_id": record.run_id, "epoch": record.epoch, "until": time.time()+30}, session=self.active())
            return record, True
        except DuplicateKeyError:
            found = await database["_som_flow_runs"].find_one({"owner": record.owner, "flow": record.flow, "key": record.key})
            if found is None:
                raise RuntimeError("unique run not found") from None
            previous = RunRecord.model_validate(found["record"])
            if previous.request_hash != record.request_hash:
                raise HTTPException(status_code=409, detail="idempotency content changed") from None
            return previous, False

    async def save(self, record: RunRecord) -> None:
        """An epoch conflict aborts business writes and their journal."""
        lease = await database["_som_flow_leases"].find_one_and_update({"_id": record.run_id, "epoch": record.epoch, "until": {"$gt": time.time()}}, {"$inc": {"fence": 1}}, session=self.active())
        if lease is None:
            raise HTTPException(status_code=503, detail="execution lease lost")
        result = await database["_som_flow_runs"].update_one({"_id": record.run_id, "epoch": record.epoch}, {"$set": {"record": record.model_dump(mode="json")}}, session=self.active())
        if result.matched_count != 1:
            raise HTTPException(status_code=503, detail="execution claim lost")

    async def renew(self, record: RunRecord, release: bool = False) -> None:
        """Independent lease writes do not modify the journal document."""
        predicate: dict[str, object] = {"_id": record.run_id, "epoch": record.epoch}
        if not release:
            predicate["until"] = {"$gt": time.time()}
        result = await database["_som_flow_leases"].update_one(predicate, {"$set": {"until": 0 if release else time.time()+30}})
        if result.matched_count != 1:
            raise HTTPException(status_code=503, detail="execution claim lost")

    async def claim(self, record: RunRecord) -> RunRecord | None:
        """Atomically advance the recovery epoch after the lease expires."""
        async with self.transaction():
            lease = await database["_som_flow_leases"].find_one_and_update({"_id": record.run_id, "until": {"$lt": time.time()}}, {"$inc": {"epoch": 1}, "$set": {"until": time.time()+30}}, return_document=ReturnDocument.AFTER, session=self.active())
            if lease is None: return None
            found = await database["_som_flow_runs"].find_one({"_id": record.run_id}, session=self.active())
            if found is None: raise HTTPException(status_code=404, detail="run not found")
            latest = RunRecord.model_validate(found["record"])
            if latest.state in {"succeeded", "compensated"}: return latest
            latest.epoch = integer(JSON_VALUE.validate_python(lease["epoch"], strict=True))
            latest.state = "compensating"
            await database["_som_flow_runs"].update_one({"_id": latest.run_id}, {"$set": {"epoch": latest.epoch, "record": latest.model_dump(mode="json")}}, session=self.active())
            return latest
