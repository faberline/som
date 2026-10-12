def quoted(name: str) -> str:
    """Quote a schema identifier; values always remain bound parameters."""
    return '"' + name.replace('"', '""') + '"'


def table_name(role: str) -> str:
    """Bind only a frozen resource table."""
    return quoted(RESOURCES[role].table)


def predicate(values: Mapping[str, object]) -> str:
    """Build equality tests from checked field names."""
    return " AND ".join(quoted(name) + "=:" + name for name in values)


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

    def active(self) -> AsyncConnection:
        """Require a transaction before any business write."""
        if self.session is None:
            raise RuntimeError("transaction required")
        return self.session

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        """Reuse a write connection or close a temporary read."""
        if self.session is not None:
            yield self.session
        else:
            async with flow_engine.connect() as connection:
                yield connection

    async def query(self, statement: str, values: Mapping[str, object] | None = None) -> list[dict[str, object]]:
        """Validate SDK rows at the database boundary."""
        async with self.connection() as connection:
            result = await connection.execute(sa.text(statement), values or {})
            return [ROW.validate_python(dict(row)) for row in result.mappings()]

    async def one(self, statement: str, values: Mapping[str, object] | None = None, status: int = 409) -> dict[str, object]:
        """Require one matching resource, revision or lease."""
        rows = await self.query(statement, values)
        if not rows:
            raise failure(status, "resource or claim missing")
        return rows[0]

    async def scope(self, role: str, parent_id: int | None) -> dict[str, int | None]:
        """Keep a child's parent present until its write commits."""
        if role != "child":
            return {}
        lock = " FOR KEY SHARE" if self.session is not None else ""
        await self.one("SELECT id FROM " + table_name("parent") + " WHERE id=:id" + lock,
                       {"id": parent_id}, 404)
        return {FOREIGN_KEY: parent_id}

    async def row(self, role: str, row_id: int | None, parent_id: int | None = None) -> tuple[ResourceRow, int]:
        """Read a typed resource inside its parent scope."""
        values = {**await self.scope(role, parent_id), "id": row_id}
        found = await self.one("SELECT * FROM " + table_name(role) + " WHERE " + predicate(values), values, 404)
        return read_row(role, found), integer(found["_som_revision"])

    async def rows(self, request: CrudRequest) -> list[ResourceRow]:
        """Apply declared filters in stable ID order."""
        spec = RESOURCES[request.role]
        values: dict[str, object] = dict(await self.scope(request.role, request.parent_id))
        if spec.filter_field and request.filter_value is not None:
            values[spec.filter_field] = request.filter_value
        conditions = predicate(values) or "TRUE"
        if spec.date_field and request.before is not None:
            conditions += " AND " + quoted(spec.date_field) + " < :_before"
            values["_before"] = request.before
        statement = "SELECT * FROM " + table_name(request.role) + " WHERE " + conditions + " ORDER BY id LIMIT :_limit OFFSET :_offset"
        values.update(_limit=request.limit, _offset=request.offset)
        return [read_row(request.role, found) for found in await self.query(statement, values)]

    async def write(self, role: str, values: Mapping[str, object], target: Mapping[str, object] | None = None) -> dict[str, object]:
        """Write checked columns and advance their private revision."""
        self.active()
        columns = ", ".join(quoted(name) for name in values)
        binds = ", ".join(":" + name for name in values)
        data = dict(values)
        if target is None:
            statement = "INSERT INTO " + table_name(role) + " (" + columns + ") VALUES (" + binds + ")"
        else:
            assignments = ", ".join(quoted(name) + "=:" + name for name in values)
            data.update(_id=target["id"], _revision=target["_som_revision"])
            statement = "UPDATE " + table_name(role) + " SET " + assignments + ", updated_at=clock_timestamp(), _som_revision=_som_revision+1 WHERE id=:_id AND _som_revision=:_revision"
        return await self.one(statement + " RETURNING *", data)

    async def delete(self, role: str, target: Mapping[str, object]) -> None:
        """Delete only the expected resource revision."""
        self.active()
        await self.one("DELETE FROM " + table_name(role) + " WHERE " + predicate(target) + " RETURNING id", target)

    async def mutate(self, request: CrudRequest, effect: Effect | None = None) -> ResourceRow | None:
        """Validate input and preserve the selected compensation snapshot."""
        self.active()
        scope = await self.scope(request.role, request.parent_id)
        target = None
        if request.operation == "create":
            values = {**write_values(request.role, request.data), **scope}
        else:
            previous, revision = await self.row(request.role, request.row_id, request.parent_id)
            target = {"id": request.row_id, "_som_revision": revision}
            if request.operation == "delete":
                await self.delete(request.role, target)
                return None
            values = write_values(request.role, request.data, True)
            if not values:
                return previous
            if effect is not None:
                effect.before = json_object(previous.model_dump(mode="json", include=set(values)))
        found = await self.write(request.role, values, target)
        if effect is not None:
            effect.row_id, effect.revision, effect.owned = integer(found["id"]), integer(found["_som_revision"]), True
        return read_row(request.role, found)

    async def reverse(self, effect: Effect) -> int | None:
        """Restore only this step's fields and unchanged revision."""
        target = {"id": effect.row_id, "_som_revision": effect.revision}
        if effect.undo == "delete_created":
            await self.delete(effect.role, target)
            return None
        found = await self.write(effect.role, write_values(effect.role, effect.before, True), target)
        return integer(found["_som_revision"])

    async def load(self, run_id: str, owner: str) -> RunRecord:
        """Read only the owner's durable run."""
        found = await self.one("SELECT record FROM _som_flow_runs WHERE run_id=:run_id AND owner=:owner", {"run_id": run_id, "owner": owner}, 404)
        return RunRecord.model_validate(found["record"])

    async def start(self, record: RunRecord) -> tuple[RunRecord, bool]:
        """Create one idempotent run and lease atomically."""
        values = record.model_dump(include={"run_id", "owner", "flow", "key", "request_hash", "epoch"})
        values["record"] = Jsonb(model_json(record))
        async with self.transaction():
            created = await self.query('INSERT INTO _som_flow_runs (run_id, owner, flow, "key", request_hash, epoch, record) VALUES (:run_id, :owner, :flow, :key, :request_hash, :epoch, :record) ON CONFLICT (owner, flow, "key") DO NOTHING RETURNING run_id', values)
            if created:
                await self.one("INSERT INTO _som_flow_leases (run_id, epoch, until) VALUES (:run_id, :epoch, :until) RETURNING run_id", {"run_id": record.run_id, "epoch": record.epoch, "until": time.time()+30})
                return record, True
            previous = RunRecord.model_validate((await self.one('SELECT record FROM _som_flow_runs WHERE owner=:owner AND flow=:flow AND "key"=:key', values))["record"])
            if previous.request_hash != record.request_hash:
                raise failure(409, "idempotency content changed")
            return previous, False

    async def save(self, record: RunRecord) -> None:
        """Lock the live epoch before committing its journal."""
        self.active()
        values = {"run_id": record.run_id, "epoch": record.epoch, "now": time.time(), "record": Jsonb(model_json(record))}
        await self.one("SELECT run_id FROM _som_flow_leases WHERE run_id=:run_id AND epoch=:epoch AND until>:now FOR UPDATE", values, 503)
        await self.one("UPDATE _som_flow_runs SET record=:record WHERE run_id=:run_id AND epoch=:epoch RETURNING run_id", values, 503)

    async def renew(self, record: RunRecord, release: bool = False) -> None:
        """Renew only the independent lease row."""
        values = {"run_id": record.run_id, "epoch": record.epoch, "now": time.time(), "until": 0 if release else time.time()+30}
        live = "" if release else " AND until>:now"
        async with self.transaction():
            await self.one("UPDATE _som_flow_leases SET until=:until WHERE run_id=:run_id AND epoch=:epoch" + live + " RETURNING run_id", values, 503)

    async def claim(self, record: RunRecord) -> RunRecord | None:
        """Advance the recovery epoch without replaying business work."""
        async with self.transaction():
            rows = await self.query("UPDATE _som_flow_leases SET epoch=epoch+1, until=:until WHERE run_id=:run_id AND until<:now RETURNING epoch", {"run_id": record.run_id, "now": time.time(), "until": time.time()+30})
            if not rows:
                return None
            latest = await self.load(record.run_id, record.owner)
            if latest.state in {"succeeded", "compensated"}:
                await self.active().rollback()
                return latest
            latest.epoch, latest.state = integer(rows[0]["epoch"]), "compensating"
            await self.one("UPDATE _som_flow_runs SET epoch=:epoch, record=:record WHERE run_id=:run_id RETURNING run_id", {"run_id": latest.run_id, "epoch": latest.epoch, "record": Jsonb(model_json(latest))})
            return latest
