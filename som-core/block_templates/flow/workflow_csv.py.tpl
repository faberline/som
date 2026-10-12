CUSTOM_FIELD_SOURCE = {{custom_fields}}
CUSTOM_FIELDS: dict[str, str] = TypeAdapter(dict[str, str]).validate_json(CUSTOM_FIELD_SOURCE)


def workflow_payload(rows: list[JsonObject]) -> JsonObject:
    """Export business fields in their declared order."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(CUSTOM_FIELDS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: r"\N" if row[key] is None else row[key] for key in CUSTOM_FIELDS})
    return workflow_result(json_value(rows), output.getvalue().encode())


def csv_cell(raw: str, kind: str) -> JsonValue:
    """Convert only the approved scalar types."""
    if raw == r"\N": return None
    if kind == "int": return int(raw)
    if kind == "float": return float(raw)
    if kind == "bool":
        if raw.lower() not in {"true", "false"}: raise ValueError("invalid boolean")
        return raw.lower() == "true"
    return raw


def csv_records(source: str) -> list[JsonObject]:
    """Validate every bounded CSV row before the first write."""
    reader = csv.DictReader(io.StringIO(source, newline=""), strict=True)
    if reader.fieldnames != list(CUSTOM_FIELDS):
        raise HTTPException(status_code=422, detail="CSV header mismatch")
    result: list[JsonObject] = []
    try:
        for row in reader:
            if len(result) == 20 or None in row or any(value is None for value in row.values()):
                raise ValueError("invalid CSV size or width")
            values: JsonObject = {}
            for key, kind in CUSTOM_FIELDS.items():
                raw = row[key]
                if raw is None: raise ValueError("missing CSV cell")
                values[key] = csv_cell(raw, kind)
            result.append(json_object(ChildInput.model_validate(values).model_dump(mode="json")))
    except (ValueError, csv.Error) as error:
        raise HTTPException(status_code=422, detail="invalid CSV") from error
    if not result: raise HTTPException(status_code=422, detail="CSV requires rows")
    return result
