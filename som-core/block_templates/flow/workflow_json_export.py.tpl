def workflow_payload(rows: list[JsonObject]) -> JsonObject:
    """Export complete rows as JSON."""
    return workflow_result(json_value(rows))
