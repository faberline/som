CUSTOM_FIELDS: dict[str, str] = {
{{#fields}}    {{name_literal}}: "{{type}}",
{{/fields}}}
CSV_ROWS: TypeAdapter[list[dict[str, str]]] = TypeAdapter(list[dict[str, str]])


def workflow_payload(rows: list[ChildRow]) -> WorkflowReceipt:
    """Export business fields in their declared order."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(CUSTOM_FIELDS), lineterminator="\n")
    writer.writeheader()
    for model in rows:
        row = model_json(model)
        writer.writerow({key: r"\N" if row[key] is None else row[key] for key in CUSTOM_FIELDS})
    return workflow_result(result_json(rows), output.getvalue().encode())


def csv_cell(raw: str, kind: str) -> JsonValue:
    """Convert the five approved scalar types before model validation."""
    if raw == r"\N": return None
    if kind == "int": return int(raw)
    if kind == "float": return float(raw)
    if kind == "bool":
        if raw.lower() not in {"true", "false"}: raise ValueError("invalid boolean")
        return raw.lower() == "true"
    return raw


def csv_records(source: str) -> list[ChildInputData]:
    """Check a bounded, typed CSV batch before the first write."""
    try:
        reader = csv.DictReader(io.StringIO(source, newline=""), strict=True)
        if reader.fieldnames != list(CUSTOM_FIELDS):
            raise HTTPException(status_code=422, detail="CSV header mismatch")
        rows = CSV_ROWS.validate_python(list(islice(reader, 21)), strict=True)
        if not 1 <= len(rows) <= 20:
            raise ValueError("expected 1..20 CSV rows")
        return [ChildInputData.model_validate({key: csv_cell(row[key], kind) for key, kind in CUSTOM_FIELDS.items()}) for row in rows]
    except (ValueError, csv.Error) as error:
        raise HTTPException(status_code=422, detail="invalid CSV") from error
