from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from .database import client, database
from .integrations import require_access
from .models import {{parent_name}} as Parent, {{child_name}} as Child
from .schemas import {{parent_name}}Create as ParentCreate, {{parent_name}}Read as ParentRead, {{parent_name}}Patch as ParentPatch
from .schemas import {{child_name}}Create as ChildCreate, {{child_name}}Read as ChildRead, {{child_name}}Patch as ChildPatch
from .schemas import BatchCreate, BatchRead


def create_router(get_session):
    router = APIRouter(dependencies=[Depends(require_access)])

    def encode(values):
        return {key: datetime.combine(value, time(), timezone.utc) if type(value) is date else value for key, value in values.items()}

    async def transaction(callback):
        try:
            async with client.start_session() as session:
                return await session.with_transaction(callback)
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail="unique value conflict") from exc
        except PyMongoError as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc

    async def require_parent(parent_id, session=None, touch=False):
        if touch:
            row = await Parent.get_pymongo_collection().find_one_and_update({"_id": parent_id}, {"$inc": {"_som_revision": 1}}, session=session)
        else:
            row = await Parent.find_one(Parent.id == parent_id, session=session)
        if row is None:
            raise HTTPException(status_code=404, detail="parent not found")
        return row

    async def require_child(parent_id, child_id, session=None):
        await require_parent(parent_id, session)
        row = await Child.find_one(Child.id == child_id, Child.{{foreign_key}} == parent_id, session=session)
        if row is None:
            raise HTTPException(status_code=404, detail="child not found")
        return row

    async def insert(model, values, session):
        # Both the counter and new document are part of the same transaction.
        counter = await database["_som_counters"].find_one_and_update({"_id": model.get_settings().name}, {"$inc": {"value": 1}}, upsert=True, return_document=ReturnDocument.AFTER, session=session)
        now = datetime.now(timezone.utc)
        # BSON stores millisecond precision. Return the same timestamp that a
        # later read will return, including the immediate POST response.
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        row = model(id=counter["value"], created_at=now, updated_at=now, **values)
        await row.insert(session=session)
        return row
