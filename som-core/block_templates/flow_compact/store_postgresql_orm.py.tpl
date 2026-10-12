SESSIONS = async_sessionmaker(flow_engine, expire_on_commit=False)
MODELS: dict[str, type[_SomRow]] = {"parent": ParentModel, "child": ChildModel}


class FlowStore:
    """Share one SQLAlchemy session for writes and their journal."""

    def __init__(self) -> None:
        self.session: AsyncSession | None = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Commit once without replaying business work."""
        async with SESSIONS() as session, session.begin():
            self.session = session
            try: yield
            finally: self.session = None

    @asynccontextmanager
    async def connected(self) -> AsyncIterator[AsyncSession]:
        """Reuse the current session or close a temporary read."""
        if self.session is not None: yield self.session
        else:
            async with SESSIONS() as session: yield session

    def active(self) -> AsyncSession:
        """Require a transaction for business writes."""
        if self.session is None: raise RuntimeError("transaction required")
        return self.session

    async def scoped(self, role: str, parent_id: int | None) -> Select[tuple[_SomRow]]:
        """Check parent existence before every child operation."""
        statement = sa.select(MODELS[role])
        if role == "child":
            await self.row("parent", parent_id)
            statement = statement.filter_by(**{FOREIGN_KEY: parent_id})
        return statement

    async def model(self, role: str, row_id: int | None, parent_id: int | None = None, lock: bool = False) -> _SomRow:
        """Read an isolated resource and optionally lock its revision."""
        statement = (await self.scoped(role, parent_id)).filter_by(id=row_id)
        if lock: statement = statement.with_for_update()
        async with self.connected() as session:
            row = await session.scalar(statement)
            if row is None: raise failure(404, "resource missing")
            return row

    async def row(self, role: str, row_id: int | None, parent_id: int | None = None) -> tuple[JsonObject, int]:
        """Expose only public fields from the concrete read model."""
        row = await self.model(role, row_id, parent_id)
        return read_row(role, row), row.som_revision()

    async def rows(self, request: CrudRequest) -> list[JsonObject]:
        """Apply declared filters and stable pagination."""
        spec = RESOURCES[request.role]
        table = MODELS[request.role].__table__
        statement = await self.scoped(request.role, request.parent_id)
        if spec.filter_field and request.filter_value is not None: statement = statement.filter_by(**{spec.filter_field: request.filter_value})
        if spec.date_field and request.before is not None: statement = statement.where(table.c[spec.date_field] < request.before)
        statement = statement.order_by(table.c.id).limit(request.limit).offset(request.offset)
        async with self.connected() as session:
            return [read_row(request.role, row) for row in await session.scalars(statement)]

    async def mutate(self, request: CrudRequest, effect: Effect | None = None) -> JsonObject:
        """Lock the resource before changing its business fields."""
        session = self.active()
        values = write_values(request.role, request.data, request.operation == "patch") if request.operation in {"create", "patch"} else {}
        if request.operation == "create":
            if request.role == "child":
                await self.row("parent", request.parent_id)
                values[FOREIGN_KEY] = request.parent_id
            row = MODELS[request.role](**values)
            session.add(row)
        else:
            row = await self.model(request.role, request.row_id, request.parent_id, True)
            if request.operation == "delete":
                await session.delete(row)
                await session.flush()
                return {}
            if not values: return read_row(request.role, row)
            if effect is not None:
                previous = read_row(request.role, row)
                effect.before = {key: previous[key] for key in values}
                effect.fields = list(values)
            for key, value in values.items(): setattr(row, key, value)
            row.som_advance()
        await session.flush()
        if effect is not None: effect.row_id, effect.revision, effect.owned = row.id, row.som_revision(), True
        return read_row(request.role, row)

    async def reverse(self, effect: Effect) -> int | None:
        """Restore only unchanged rows and the selected business fields."""
        session = self.active()
        statement = sa.select(MODELS[effect.role]).filter_by(id=effect.row_id).with_for_update()
        row = await session.scalar(statement)
        if row is None or row.som_revision() != effect.revision: raise failure(409, "revision conflict")
        if not effect.fields:
            await session.delete(row)
            await session.flush()
            return None
        for key, value in write_values(effect.role, effect.before, True).items(): setattr(row, key, value)
        row.som_advance()
        await session.flush()
        return row.som_revision()

    async def entry(self, run_id: str, owner: str) -> _SomRun:
        """Read only the owner's durable run."""
        async with self.connected() as session:
            row = await session.scalar(sa.select(_SomRun).filter_by(run_id=run_id, owner=owner))
            if row is None: raise failure(404, "run missing")
            return row

    async def load(self, run_id: str, owner: str) -> RunRecord:
        """Validate persisted data at the JSON boundary."""
        return RunRecord.model_validate((await self.entry(run_id, owner)).record)

    async def start(self, record: RunRecord) -> tuple[RunRecord, bool]:
        """Create one owner-bound idempotent run."""
        keys = record.model_dump(include={"owner", "flow", "key"})
        try:
            async with self.transaction():
                self.active().add(_SomRun(**keys, run_id=record.run_id, epoch=record.epoch, request_hash=record.request_hash, record=json_object(record.model_dump(mode="json"))))
                await self.active().flush()
                self.active().add(_SomLease(run_id=record.run_id, epoch=record.epoch, until=time.time()+30))
            return record, True
        except IntegrityError:
            async with self.connected() as session:
                found = await session.scalar(sa.select(_SomRun).filter_by(**keys))
                if found is None: raise
                previous = RunRecord.model_validate(found.record)
                if previous.request_hash != record.request_hash: raise failure(409, "idempotency content changed") from None
                return previous, False

    async def save(self, record: RunRecord) -> None:
        """Lock the lease and check the epoch before committing writes."""
        session = self.active()
        lease = await session.scalar(sa.select(_SomLease).filter_by(run_id=record.run_id).with_for_update().execution_options(populate_existing=True))
        if lease is None or lease.epoch != record.epoch or lease.until <= time.time(): raise failure(503, "claim lost")
        entry = await self.entry(record.run_id, record.owner)
        if entry.epoch != record.epoch: raise failure(503, "claim lost")
        entry.record = json_object(record.model_dump(mode="json"))

    async def renew(self, record: RunRecord, release: bool = False) -> None:
        """Update only the independent lease row."""
        statement = sa.update(_SomLease).filter_by(run_id=record.run_id, epoch=record.epoch)
        if not release: statement = statement.where(_SomLease.until > time.time())
        async with self.transaction():
            found = await self.active().scalar(statement.values(until=0 if release else time.time()+30).returning(_SomLease.run_id))
            if found is None: raise failure(503, "claim lost")

    async def claim(self, record: RunRecord) -> RunRecord | None:
        """Advance the recovery epoch without replaying business work."""
        async with self.transaction():
            lease = await self.active().scalar(sa.select(_SomLease).filter_by(run_id=record.run_id).with_for_update())
            if lease is None: raise failure(404, "run missing")
            if lease.until >= time.time(): return None
            entry = await self.entry(record.run_id, record.owner)
            latest = RunRecord.model_validate(entry.record)
            if latest.state in {"succeeded", "compensated"}: return latest
            latest.epoch += 1
            latest.state = "compensating"
            lease.epoch, lease.until = latest.epoch, time.time()+30
            entry.epoch, entry.record = latest.epoch, json_object(latest.model_dump(mode="json"))
            return latest
