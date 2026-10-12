RESOURCES: dict[str, ResourceSpec] = {
    "parent": ResourceSpec({{parent_table}}, ParentInputData, ParentPatchData, ParentRow, {{parent_filter}}, {{parent_date}}),
    "child": ResourceSpec({{child_table}}, ChildInputData, ChildPatchData, ChildRow, {{child_filter}}, {{child_date}}),
}
FOREIGN_KEY = {{foreign_key}}

type ColumnValues = dict[str, str | int | float | bool | date | None]
COLUMN_VALUES: TypeAdapter[ColumnValues] = TypeAdapter(ColumnValues)


def read_row(role: str, value: object) -> ResourceRow:
    """Keep private columns outside the public read model."""
    return RESOURCES[role].read.model_validate(value)


def write_values(role: str, value: JsonObject, patch: bool = False) -> ColumnValues:
    """Validate business fields before converting database values."""
    model = RESOURCES[role].patch if patch else RESOURCES[role].create
    try:
        return COLUMN_VALUES.validate_python(model.model_validate(value).model_dump(exclude_unset=patch), strict=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="invalid resource input") from error
