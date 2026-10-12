import os
from contextlib import asynccontextmanager

from beanie import init_beanie
from pymongo import AsyncMongoClient

url = os.environ.get("MONGODB_URL", "")
database_name = os.environ.get("MONGODB_DATABASE", "")
if not url.startswith(("mongodb://", "mongodb+srv://")) or not database_name:
    raise RuntimeError("MONGODB_URL and MONGODB_DATABASE are required")
client = AsyncMongoClient(url, serverSelectionTimeoutMS=2000, tz_aware=True)
database = client[database_name]


async def initialize():
    from .models import {{parent_name}}, {{child_name}}
    await database.command("ping")
    hello = await client.admin.command("hello")
    if not hello.get("setName") and hello.get("msg") != "isdbgrid":
        raise RuntimeError("MongoDB transactions require a replica set or mongos")
    await init_beanie(database=database, document_models=[{{parent_name}}, {{child_name}}])
    for model in ({{parent_name}}, {{child_name}}):
        collection = model.get_pymongo_collection()
        for name, info in model.UNIQUE_FIELDS.items():
            if info["unique"]:
                await collection.create_index(name, unique=True, partialFilterExpression={name: {"$type": info["bson_type"]}})
        for columns in model.QUERY_INDEXES:
            await collection.create_index([(name, 1) for name in columns])


@asynccontextmanager
async def lifespan(app):
    await initialize()
    try:
        yield
    finally:
        await client.close()


def get_session():
    return None
