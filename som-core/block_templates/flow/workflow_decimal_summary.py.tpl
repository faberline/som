VALUE_FIELD = {{value_field}}
GROUP_FIELD = {{group_field}}


def workflow_payload(rows: list[JsonObject]) -> JsonObject:
    """Sum decimal values without binary floating-point addition."""
    totals: dict[str, Decimal] = {}
    for row in rows:
        if row[VALUE_FIELD] is not None:
            key = str(row[GROUP_FIELD])
            totals[key] = totals.get(key, Decimal(0)) + Decimal(str(row[VALUE_FIELD]))
    return workflow_result({"totals": {key: str(value) for key, value in sorted(totals.items())}})
