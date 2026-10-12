def encode_date(value: date) -> datetime:
    """Store a date at UTC midnight."""
    return datetime.combine(value, time(), UTC)


class _Collection(NamedTuple):
    """Freeze collection names and their version 1 indexes."""
    name: str
    unique: dict[str, str]
    indexes: list[list[str]]


def _query_indexes(fields: list[str | None], prefix: list[str]) -> list[list[str]]:
    """Keep filter order and the final integer ID."""
    return [[*prefix, field, "_id"] for field in fields if field is not None]
