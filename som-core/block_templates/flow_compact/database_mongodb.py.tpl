url = os.environ.get("MONGODB_URL", "")
database_name = os.environ.get("MONGODB_DATABASE", "")
if not url.startswith(("mongodb://", "mongodb+srv://")) or not database_name:
    raise RuntimeError("MONGODB_URL and MONGODB_DATABASE are required")


class _MongoState:
    """Create a client in its active loop and close it on shutdown."""

    def __init__(self) -> None:
        self._client: AsyncMongoClient[dict[str, object]] | None = None

    @property
    def client(self) -> AsyncMongoClient[dict[str, object]]:
        """Return the current lifespan's client."""
        if self._client is None:
            self._client = AsyncMongoClient(url, serverSelectionTimeoutMS=2000, tz_aware=True, appname=__package__ or "generated-service")
        return self._client

    @property
    def database(self) -> AsyncDatabase[dict[str, object]]:
        """Use only the configured database."""
        return self.client[database_name]

    async def close(self) -> None:
        """Discard the client before another lifespan begins."""
        if self._client is not None:
            await self._client.close()
            self._client = None


mongo = _MongoState()


async def initialize() -> None:
    """Require real transactions and initialize the frozen schema."""
    await mongo.database.command("ping")
    hello = await mongo.client.admin.command("hello")
    if not hello.get("setName") and hello.get("msg") != "isdbgrid":
        raise RuntimeError("MongoDB transactions require a replica set or mongos")
    await mongo.database["_som_flow_runs"].create_index([("owner", 1), ("flow", 1), ("key", 1)], unique=True)
    for model in (_ParentDocument, _ChildDocument):
        collection = mongo.database[model.name]
        for name, kind in model.unique.items():
            await collection.create_index(name, unique=True, partialFilterExpression={name: {"$type": kind}})
        for columns in model.indexes:
            await collection.create_index([(name, 1) for name in columns])


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Close the connection even if initialization fails."""
    try:
        await initialize()
        yield
    finally:
        await mongo.close()
