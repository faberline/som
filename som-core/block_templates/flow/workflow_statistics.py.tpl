VALUE_FIELD = {{value_field}}


def workflow_payload(rows: list[JsonObject]) -> JsonObject:
    """Calculate the approved finite-value statistics."""
    values = [float(str(row[VALUE_FIELD])) for row in rows if row[VALUE_FIELD] is not None]
    return workflow_result({"count": len(values), "mean": statistics.fmean(values) if values else None, "median": statistics.median(values) if values else None, "minimum": min(values) if values else None, "maximum": max(values) if values else None})
