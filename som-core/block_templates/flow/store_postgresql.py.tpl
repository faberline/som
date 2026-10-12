flow_engine = create_async_engine(database_url(), pool_pre_ping=True)


class FlowStore:
    """PostgreSQL business writes and their journal share one connection."""

    def __init__(self) -> None:
        self.connection: AsyncConnection | None = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Commit once; all exceptions roll back the entire scope."""
        async with flow_engine.begin() as connection:
            self.connection = connection
            try:
                yield
            finally:
                self.connection = None

    def active(self) -> AsyncConnection:
        """Reject accidental writes outside the declared transaction."""
        if self.connection is None:
            raise RuntimeError("database transaction required")
        return self.connection

    @asynccontextmanager
    async def connected(self) -> AsyncIterator[AsyncConnection]:
        """Reuse the transaction, or close a temporary read connection."""
        if self.connection is not None:
            yield self.connection
        else:
            async with flow_engine.connect() as connection:
                yield connection

    async def row(self, role: str, row_id: int, parent_id: int | None = None) -> tuple[JsonObject, int]:
        """Find one row, preserving parent isolation."""
        table = Base.metadata.tables[RESOURCES[role].table]
        statement = select(table).where(table.c.id == row_id)
        if role == "child":
            if parent_id is None:
                raise HTTPException(status_code=404, detail="parent not found")
            await self.row("parent", parent_id)
            statement = statement.where(table.c[FOREIGN_KEY] == parent_id)
        async with self.connected() as connection:
            found = (await connection.execute(statement)).mappings().first()
            if found is None:
                raise HTTPException(status_code=404, detail=role + " not found")
            revision = found["_som_revision"]
            if not isinstance(revision, int):
                raise RuntimeError("invalid managed revision")
            return read_row(role, found), revision

    async def rows(self, request: CrudRequest) -> list[JsonObject]:
        """Read in ID order, with the declared filters and limits."""
        role = request.role
        table = Base.metadata.tables[RESOURCES[role].table]
        statement = select(table)
        if role == "child":
            if request.parent_id is None:
                raise HTTPException(status_code=404, detail="parent not found")
            await self.row("parent", request.parent_id)
            statement = statement.where(table.c[FOREIGN_KEY] == request.parent_id)
        spec = RESOURCES[role]
        if spec.filter_field and request.filter_value is not None:
            statement = statement.where(table.c[spec.filter_field] == request.filter_value)
        if spec.date_field and request.before is not None:
            statement = statement.where(table.c[spec.date_field] < request.before)
        statement = statement.order_by(table.c.id).limit(request.limit).offset(request.offset)
        async with self.connected() as connection:
            return [read_row(role, found) for found in (await connection.execute(statement)).mappings()]

    async def mutate(self, request: CrudRequest, effect: Effect | None = None) -> JsonObject:
        """Apply one checked mutation within the caller's transaction."""
        connection = self.active()
        table = Base.metadata.tables[RESOURCES[request.role].table]
        if request.role == "child":
            if request.parent_id is None:
                raise HTTPException(status_code=404, detail="parent not found")
            await self.row("parent", request.parent_id)
        if request.operation == "create":
            values = write_values(request.role, request.data)
            if request.role == "child": values[FOREIGN_KEY] = request.parent_id
            found = (await connection.execute(table.insert().values(values).returning(table))).mappings().one()
        else:
            if request.row_id is None:
                raise HTTPException(status_code=404, detail="row not found")
            previous, revision = await self.row(request.role, request.row_id, request.parent_id)
            predicate = (table.c.id == request.row_id) & (table.c["_som_revision"] == revision)
            if request.operation == "delete":
                (await connection.execute(table.delete().where(predicate)))
                return {}
            values = write_values(request.role, request.data, True)
            if not values:
                return previous
            if effect is not None:
                effect.before = {key: previous[key] for key in values}
                effect.fields = list(values)
            found = (await connection.execute(table.update().where(predicate).values(values).values(updated_at=datetime.now(UTC), _som_revision=revision+1).returning(table))).mappings().one()
        result = read_row(request.role, found)
        if effect is not None:
            effect.row_id = integer(result["id"])
            effect.revision = integer(found["_som_revision"])
            effect.owned = True
        return result

    async def reverse(self, effect: Effect) -> int | None:
        """Use compare-and-swap; never replace another writer's changes."""
        table = Base.metadata.tables[RESOURCES[effect.role].table]
        predicate = (table.c.id == effect.row_id) & (table.c["_som_revision"] == effect.revision)
        if effect.fields:
            values = write_values(effect.role, effect.before, True)
            restored = table.update().where(predicate).values(values).values(updated_at=datetime.now(UTC), _som_revision=effect.revision+1).returning(table.c["_som_revision"])
            result = (await self.active().execute(restored)).scalar_one_or_none()
        else:
            deleted = table.delete().where(predicate).returning(table.c["_som_revision"])
            result = (await self.active().execute(deleted)).scalar_one_or_none()
        if result is None:
            raise HTTPException(status_code=409, detail="compensation revision conflict")
        return int(result) if effect.fields else None

    async def load(self, run_id: str, owner: str) -> RunRecord:
        """Load only the caller's durable run."""
        async with flow_engine.connect() as connection:
            record = (await connection.execute(select(FLOW_RUNS.c.record).where(FLOW_RUNS.c.run_id == run_id, FLOW_RUNS.c.owner == owner))).scalar_one_or_none()
            if record is None:
                raise HTTPException(status_code=404, detail="run not found")
            return RunRecord.model_validate(record)

    async def start(self, record: RunRecord) -> tuple[RunRecord, bool]:
        """Atomically create or find an idempotent execution."""
        try:
            async with flow_engine.begin() as connection:
                (await connection.execute(FLOW_RUNS.insert().values(run_id=record.run_id, owner=record.owner, flow=record.flow, key=record.key, request_hash=record.request_hash, epoch=record.epoch, record=record.model_dump(mode="json"))))
                (await connection.execute(FLOW_LEASES.insert().values(run_id=record.run_id, epoch=record.epoch, until=time.time()+30)))
            return record, True
        except IntegrityError:
            async with flow_engine.connect() as connection:
                found = (await connection.execute(select(FLOW_RUNS.c.record).where(FLOW_RUNS.c.owner == record.owner, FLOW_RUNS.c.flow == record.flow, FLOW_RUNS.c.key == record.key))).scalar_one()
                previous = RunRecord.model_validate(found)
                if previous.request_hash != record.request_hash:
                    raise HTTPException(status_code=409, detail="idempotency content changed") from None
                return previous, False

    async def save(self, record: RunRecord) -> None:
        """Fence every business commit with the durable execution epoch."""
        connection = self.active()
        lease = (await connection.execute(select(FLOW_LEASES.c.until).where(FLOW_LEASES.c.run_id == record.run_id, FLOW_LEASES.c.epoch == record.epoch).with_for_update())).scalar_one_or_none()
        if lease is None or lease < time.time():
            raise HTTPException(status_code=503, detail="execution lease lost")
        result = (await connection.execute(FLOW_RUNS.update().where(FLOW_RUNS.c.run_id == record.run_id, FLOW_RUNS.c.epoch == record.epoch).values(record=record.model_dump(mode="json"))))
        if result.rowcount != 1:
            raise HTTPException(status_code=503, detail="execution claim lost")

    async def renew(self, record: RunRecord, release: bool = False) -> None:
        """Lease updates use a separate row, not the business journal row."""
        async with flow_engine.begin() as connection:
            statement = FLOW_LEASES.update().where(FLOW_LEASES.c.run_id == record.run_id, FLOW_LEASES.c.epoch == record.epoch)
            if not release:
                statement = statement.where(FLOW_LEASES.c.until > time.time())
            result = (await connection.execute(statement.values(until=0 if release else time.time()+30)))
            if result.rowcount != 1:
                raise HTTPException(status_code=503, detail="execution claim lost")

    async def claim(self, record: RunRecord) -> RunRecord | None:
        """Claim expired work for compensation only."""
        async with flow_engine.begin() as connection:
            lease = (await connection.execute(select(FLOW_LEASES).where(FLOW_LEASES.c.run_id == record.run_id).with_for_update())).mappings().one()
            if lease["until"] >= time.time(): return None
            latest = RunRecord.model_validate((await connection.execute(select(FLOW_RUNS.c.record).where(FLOW_RUNS.c.run_id == record.run_id))).scalar_one())
            if latest.state in {"succeeded", "compensated"}: return latest
            latest.epoch += 1
            latest.state = "compensating"
            (await connection.execute(FLOW_RUNS.update().where(FLOW_RUNS.c.run_id == latest.run_id).values(epoch=latest.epoch, record=latest.model_dump(mode="json"))))
            (await connection.execute(FLOW_LEASES.update().where(FLOW_LEASES.c.run_id == latest.run_id).values(epoch=latest.epoch, until=time.time()+30)))
            return latest
