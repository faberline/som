WORKFLOW = {{workflow}}
VALUE_FIELD = {{value_field}}
GROUP_FIELD = {{group_field}}
CUSTOM_FIELD_SOURCE = {{custom_fields}}
CUSTOM_FIELDS: dict[str, str] = TypeAdapter(dict[str, str]).validate_json(CUSTOM_FIELD_SOURCE)


def workflow_payload(rows: list[JsonObject]) -> JsonObject:
    """Keep the approved workflow calculations and encode bytes explicitly."""
    data: JsonValue = json_value(rows)
    body = json.dumps(rows, separators=(",", ":")).encode()
    if WORKFLOW == "gzip_export": body = gzip.compress(body, mtime=0)
    elif WORKFLOW == "csv":
        target = io.StringIO(newline="")
        writer = csv.writer(target)
        writer.writerow(CUSTOM_FIELDS)
        for row in rows:
            writer.writerow([r"\N" if row[name] is None else str(row[name]).lower() if isinstance(row[name], bool) else row[name] for name in CUSTOM_FIELDS])
        body = target.getvalue().encode()
    elif WORKFLOW == "decimal_summary":
        totals: dict[str, Decimal] = {}
        for row in rows:
            if VALUE_FIELD is not None and GROUP_FIELD is not None and row[VALUE_FIELD] is not None:
                key = text_value(row[GROUP_FIELD])
                totals[key] = totals.get(key, Decimal(0))+Decimal(str(row[VALUE_FIELD]))
        data = {"totals": {key: str(value) for key, value in sorted(totals.items())}}
        body = json.dumps(data, separators=(",", ":")).encode()
    elif WORKFLOW == "statistics":
        values = [float(str(row[VALUE_FIELD])) for row in rows if VALUE_FIELD is not None and row[VALUE_FIELD] is not None]
        data = {"count": len(values), "mean": statistics.mean(values) if values else None, "median": statistics.median(values) if values else None, "minimum": min(values) if values else None, "maximum": max(values) if values else None}
        body = json.dumps(data, separators=(",", ":")).encode()
    return {"body": base64.b64encode(body).decode(), "size": len(body), "sha256": hashlib.sha256(body).hexdigest(), "data": data}


def csv_records(source: str) -> list[JsonObject]:
    """Parse and validate the entire bounded batch before writing anything."""
    if WORKFLOW != "csv": raise HTTPException(status_code=422, detail="CSV capability required")
    reader = csv.DictReader(io.StringIO(source, newline=""))
    if reader.fieldnames != list(CUSTOM_FIELDS):
        raise HTTPException(status_code=422, detail="CSV header mismatch")
    result: list[JsonObject] = []
    try:
        for row in reader:
            if len(result) == 20: raise HTTPException(status_code=422, detail="CSV has more than 20 rows")
            values: JsonObject = {}
            for key, kind in CUSTOM_FIELDS.items():
                raw = row[key]
                if raw is None: raise ValueError("missing CSV cell")
                if raw == r"\N": values[key] = None
                elif kind == "int": values[key] = int(raw)
                elif kind == "float": values[key] = float(raw)
                elif kind == "bool":
                    if raw.lower() not in {"true", "false"}: raise ValueError("invalid boolean")
                    values[key] = raw.lower() == "true"
                else: values[key] = raw
            result.append(json_object(ChildCreate.model_validate(values).model_dump(mode="json")))
    except (ValueError, csv.Error) as error:
        raise HTTPException(status_code=422, detail="invalid CSV") from error
    if not result: raise HTTPException(status_code=422, detail="CSV requires at least one row")
    return result
