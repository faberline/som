class FlowStore:
    """Share one connection for business writes and their journal."""

    def __init__(self) -> None:
        self.session: AsyncConnection | None = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Commit once or roll back all writes."""
        async with flow_engine.begin() as connection:
            self.session = connection
            try:
                yield
            finally:
                self.session = None

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        """Close temporary reads and reuse the write connection."""
        if self.session is not None:
            yield self.session
        else:
            async with flow_engine.connect() as connection:
                yield connection

    async def query(self, statement: Executable) -> list[dict[str, object]]:
        """Validate SDK row mappings at the database boundary."""
        async with self.connection() as connection:
            result = await connection.execute(statement)
            return [ROW.validate_python(dict(row)) for row in result.mappings()]

    async def one(self, statement: Executable, status: int = 409) -> dict[str, object]:
        """Require one returned resource, claim or revision."""
        rows = await self.query(statement)
        if not rows:
            raise failure(status, "resource or claim missing")
        return rows[0]

    async def scope(self, role: str, parent_id: int | None) -> dict[str, int | None]:
        """Keep a child's parent present until its write commits."""
        if role == "child":
            statement = sa.select(table_for("parent").c.id).filter_by(id=parent_id)
            if self.session is not None:
                statement = statement.with_for_update(read=True, key_share=True)
            await self.one(statement, 404)
            return {FOREIGN_KEY: parent_id}
        return {}

    async def row(self, role: str, row_id: int | None, parent_id: int | None = None) -> tuple[ResourceRow, int]:
        """Read a concrete model within its parent scope."""
        scope = await self.scope(role, parent_id)
        found = await self.one(sa.select(table_for(role)).filter_by(**scope, id=row_id), 404)
        return read_row(role, found), integer(json_value(found["_som_revision"]))

    async def rows(self, request: CrudRequest) -> list[ResourceRow]:
        """Apply declared filters in stable ID order."""
        spec, table = RESOURCES[request.role], table_for(request.role)
        scope = await self.scope(request.role, request.parent_id)
        statement = sa.select(table).filter_by(**scope)
        if spec.filter_field and request.filter_value is not None:
            statement = statement.filter_by(**{spec.filter_field: request.filter_value})
        if spec.date_field and request.before is not None:
            statement = statement.where(table.c[spec.date_field] < request.before)
        statement = statement.order_by(table.c.id).limit(request.limit).offset(request.offset)
        return [read_row(request.role, row) for row in await self.query(statement)]

    def active(self) -> AsyncConnection:
        """Refuse a mutation without its declared transaction."""
        if self.session is None:
            raise RuntimeError("transaction required")
        return self.session

    async def mutate(self, request: CrudRequest, effect: Effect | None = None) -> ResourceRow | None:
        """Validate business fields and use revision checks."""
        self.active()
        scope = await self.scope(request.role, request.parent_id)
        table = table_for(request.role)
        if request.operation == "create":
            values = write_values(request.role, request.data)
            values.update(scope)
            statement: Executable = table.insert().values(values).returning(table)
        else:
            previous, revision = await self.row(request.role, request.row_id, request.parent_id)
            target = {"id": request.row_id, "_som_revision": revision}
            if request.operation == "delete":
                await self.one(table.delete().filter_by(**target).returning(table.c.id))
                return None
            values = write_values(request.role, request.data, True)
            if not values:
                return previous
            if effect is not None:
                effect.before = json_object(previous.model_dump(mode="json", include=set(values)))
            statement = patch_statement(table, values).filter_by(**target).returning(table)
        found = await self.one(statement)
        if effect is not None:
            effect.row_id = integer(json_value(found["id"]))
            effect.revision = integer(json_value(found["_som_revision"]))
            effect.owned = True
        return read_row(request.role, found)

    async def reverse(self, effect: Effect) -> int | None:
        """Restore only this step's fields and revision."""
        self.active()
        table = table_for(effect.role)
        restore = effect.undo == "restore_patch"
        statement = patch_statement(table, write_values(effect.role, effect.before, True)) if restore else table.delete()
        await self.one(statement.filter_by(id=effect.row_id, _som_revision=effect.revision).returning(table.c.id))
        return effect.revision + 1 if restore else None

    async def load(self, run_id: str, owner: str) -> RunRecord:
        """Read only the owner's run."""
        found = await self.one(sa.select(FLOW_RUNS.c.record).filter_by(run_id=run_id, owner=owner), 404)
        return RunRecord.model_validate(found["record"])

    async def start(self, record: RunRecord) -> tuple[RunRecord, bool]:
        """Create an idempotent run and lease atomically."""
        values = record.model_dump(include={"run_id", "owner", "flow", "key", "request_hash", "epoch"})
        values["record"] = record.model_dump(mode="json")
        async with self.transaction():
            rows = await self.query(pg_insert(FLOW_RUNS).values(values).on_conflict_do_nothing(index_elements=["owner", "flow", "key"]).returning(FLOW_RUNS.c.run_id))
            if rows:
                await self.one(FLOW_LEASES.insert().values(run_id=record.run_id, epoch=record.epoch, until=time.time() + 30).returning(FLOW_LEASES.c.run_id))
                return record, True
            found = await self.one(sa.select(FLOW_RUNS.c.record).filter_by(owner=record.owner, flow=record.flow, key=record.key))
            previous = RunRecord.model_validate(found["record"])
            if previous.request_hash != record.request_hash:
                raise failure(409, "idempotency content changed")
            return previous, False

    async def save(self, record: RunRecord) -> None:
        """Lock the live epoch before committing its journal."""
        self.active()
        await self.one(sa.select(FLOW_LEASES).filter_by(**run_key(record)).where(FLOW_LEASES.c.until > time.time()).with_for_update(), 503)
        await self.one(FLOW_RUNS.update().filter_by(**run_key(record)).values(record=record.model_dump(mode="json")).returning(FLOW_RUNS.c.run_id), 503)

    async def renew(self, record: RunRecord, release: bool = False) -> None:
        """Update only the independent lease row."""
        statement = FLOW_LEASES.update().filter_by(**run_key(record))
        if not release:
            statement = statement.where(FLOW_LEASES.c.until > time.time())
        async with self.transaction():
            await self.one(statement.values(until=0 if release else time.time() + 30).returning(FLOW_LEASES.c.run_id), 503)

    async def claim(self, record: RunRecord) -> RunRecord | None:
        """Advance the recovery epoch without replaying business work."""
        async with self.transaction():
            rows = await self.query(FLOW_LEASES.update().filter_by(run_id=record.run_id).where(FLOW_LEASES.c.until < time.time()).values(epoch=FLOW_LEASES.c.epoch + 1, until=time.time() + 30).returning(FLOW_LEASES.c.epoch))
            if not rows:
                return None
            latest = await self.load(record.run_id, record.owner)
            if latest.state in {"succeeded", "compensated"}:
                await self.active().rollback()
                return latest
            latest.epoch = integer(json_value(rows[0]["epoch"]))
            latest.state = "compensating"
            await self.one(FLOW_RUNS.update().filter_by(run_id=latest.run_id).values(epoch=latest.epoch, record=latest.model_dump(mode="json")).returning(FLOW_RUNS.c.run_id))
            return latest
