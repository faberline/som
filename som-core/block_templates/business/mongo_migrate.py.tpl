"""Create the declared collections and indexes. Downgrade is test-only."""
import argparse
import asyncio
import os

from .database import client, database, database_name, initialize


async def migrate(action):
    try:
        if action == "upgrade":
            await initialize()
        else:
            expected = os.environ.get("SOM_TEST_DATABASE_NAME", "")
            if os.environ.get("APP_ENV") != "test" or not database_name.startswith("som_test_") or expected != database_name:
                raise RuntimeError("downgrade requires the explicitly named isolated test database")
            for name in ({{child_table}}, {{parent_table}}, "_som_counters"):
                await database.drop_collection(name)
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["upgrade", "downgrade"])
    asyncio.run(migrate(parser.parse_args().action))
