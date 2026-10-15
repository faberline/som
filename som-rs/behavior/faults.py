"""Seeded faults (issue #46): each one is a small edit to the files of a Python product, and the
suite must report a failure in the fault's category. Every category in suite.CATEGORIES has at
least one fault.

An edit is (file suffix, old text, new text). The suffix must match exactly one product file,
and the old text must occur in it, so a fault never passes because its edit did not apply.
"""
from __future__ import annotations

V3 = "cov-v3-types"
V2 = "example-v2"
V4 = "example-v4"
V5 = "cov-v5-max"

ROUTE_ON_COLLECTION = '''    @router.put("/tickets")
    def replace_all() -> dict:
        return {}

    return router'''

FAULTS: list[dict] = [
    {"id": "create-200", "case": V3, "category": "crud",
     "description": "create answers 200 instead of 201",
     "edits": [("/api.py", 'response_model=schemas.TicketRead, status_code=201', 'response_model=schemas.TicketRead')]},
    {"id": "patch-skips-a-field", "case": V3, "category": "crud",
     "description": "patch ignores the priority member",
     "edits": [("/api.py", "            setattr(row, key, value)",
                "            if key != \"priority\":\n                setattr(row, key, value)")]},
    {"id": "delete-keeps-the-row", "case": V3, "category": "crud",
     "description": "delete answers 204 without deleting",
     "edits": [("/api.py", "        db.delete(row)\n", "")]},
    {"id": "not-found-detail", "case": V3, "category": "not_found",
     "description": "a missing row answers 404 with another detail",
     "edits": [("/api.py", 'detail="not found"', 'detail="missing"')]},
    {"id": "conflict-400", "case": V3, "category": "unique",
     "description": "a unique conflict answers 400 instead of 409",
     "edits": [("/api.py", "status_code=409", "status_code=400")]},
    {"id": "create-allows-unknown-members", "case": V3, "category": "validation",
     "description": "create no longer forbids unknown members",
     "edits": [("/schemas.py", '    model_config = ConfigDict(extra="forbid")\n    subject: str\n', "    subject: str\n")]},
    {"id": "default-true", "case": V3, "category": "defaults",
     "description": "the boolean default is True instead of False",
     "edits": [("/schemas.py", "closed: bool = False", "closed: bool = True")]},
    {"id": "nullable-column-refuses-null", "case": V3, "category": "null",
     "description": "patch refuses null for a nullable column",
     "edits": [("/schemas.py", '"note": True,', '"note": False,')]},
    {"id": "bool-filter-ignored", "case": V3, "category": "filter",
     "description": "the boolean filter is ignored",
     "edits": [("/api.py", "stmt = stmt.where(models.Ticket.closed == closed)", "stmt = stmt")]},
    {"id": "string-filter-case-insensitive", "case": V2, "category": "filter",
     "description": "the string filter ignores case",
     "edits": [("/api.py", "models.Item.title == filter_value", "models.Item.title.ilike(filter_value)")]},
    {"id": "before-inclusive", "case": V4, "category": "date_filter",
     "description": "the before filter includes the cut date",
     "edits": [("/api.py", "models.Todo.deadline_on < before", "models.Todo.deadline_on <= before")]},
    {"id": "max-limit-plus-one", "case": V3, "category": "limit",
     "description": "the limit bound is one above the frozen maximum",
     "edits": [("/api.py", "le=50", "le=51")]},
    {"id": "default-limit-10", "case": V3, "category": "limit",
     "description": "the default limit is 10 instead of 20",
     "edits": [("/api.py", "Query(default=20", "Query(default=10")]},
    {"id": "offset-ignored", "case": V3, "category": "pagination",
     "description": "the offset is ignored",
     "edits": [("/api.py", ".limit(limit).offset(offset)", ".limit(limit)")]},
    {"id": "id-descending", "case": V3, "category": "order",
     "description": "lists are in descending id order",
     "edits": [("/api.py", ".order_by(models.Ticket.id)", ".order_by(models.Ticket.id.desc())")]},
    {"id": "updated-at-frozen", "case": V3, "category": "timestamps",
     "description": "updated_at no longer changes on update",
     "edits": [("/models.py", "onupdate=sa.func.now(), ", "")]},
    {"id": "extra-route", "case": V3, "category": "fallback",
     "description": "PUT on the collection is routed",
     "edits": [("/api.py", "    return router", ROUTE_ON_COLLECTION)]},
    {"id": "list-under-missing-parent", "case": V5, "category": "scope",
     "description": "listing children of a missing parent answers 200 []",
     "edits": [("/api.py", "        require_parent(db, parent_id)\n        statement = select(Child)",
                "        statement = select(Child)")]},
    {"id": "child-under-any-parent", "case": V5, "category": "isolation",
     "description": "a child is found under any parent",
     "edits": [("/api.py", "Child.id == child_id, Child.account_id == parent_id", "Child.id == child_id")]},
    {"id": "list-every-child", "case": V5, "category": "isolation",
     "description": "a parent lists every child",
     "edits": [("/api.py", "select(Child).where(Child.account_id == parent_id)", "select(Child)")]},
    {"id": "cascade-delete", "case": V5, "category": "restrict",
     "description": "deleting a parent cascades to its children",
     "edits": [("_billing.py", 'ondelete="RESTRICT"', 'ondelete="CASCADE"')]},
    {"id": "batch-21", "case": V5, "category": "batch",
     "description": "a batch admits 21 children",
     "edits": [("/schemas.py", "max_length=20", "max_length=21")]},
    {"id": "batch-commits-the-parent", "case": V5, "category": "batch_atomic",
     "description": "the batch commits the parent before its children",
     "edits": [("/api.py", "            db.flush()\n            for item in data.children:",
                "            db.commit()\n            for item in data.children:")]},
    {"id": "string-bound-256", "case": V5, "category": "bounds",
     "description": "strings of 256 characters are admitted",
     "edits": [("/schemas.py", "len(value) > 255", "len(value) > 256")]},
    {"id": "healthz-up", "case": V5, "category": "healthz",
     "description": "healthz answers another status",
     "edits": [("/app.py", 'return {"status": "ok"}', 'return {"status": "up"}')]},
]


def apply(files: dict[str, str], fault: dict) -> dict[str, str]:
    """The product files with the fault's edits applied; refuses an edit that does not apply."""
    seeded = dict(files)
    for suffix, old, new in fault["edits"]:
        paths = [path for path in seeded if path.endswith(suffix) and not path.startswith("tests/")]
        if len(paths) != 1:
            raise ValueError(f"{fault['id']}: {suffix} matches {sorted(paths)}")
        text = seeded[paths[0]]
        if old not in text:
            raise ValueError(f"{fault['id']}: the text to replace is not in {paths[0]}")
        seeded[paths[0]] = text.replace(old, new)
        if seeded[paths[0]] == text:
            raise ValueError(f"{fault['id']}: the edit changed nothing in {paths[0]}")
    return seeded
