RESOURCES: dict[str, ResourceSpec] = {
    "parent": ResourceSpec(table={{parent_table}}, create=ParentInput, patch=ParentChanges, read=ParentView, filter_field={{parent_filter}}, date_field={{parent_date}}),
    "child": ResourceSpec(table={{child_table}}, create=ChildInput, patch=ChildChanges, read=ChildView, filter_field={{child_filter}}, date_field={{child_date}}),
}
FOREIGN_KEY = {{foreign_key}}


def read_row(role: str, value: object) -> JsonObject:
    """Discard private database columns through the concrete read model."""
    return json_object(RESOURCES[role].read.model_validate(value).model_dump(mode="json"))


def write_values(role: str, value: JsonObject, patch: bool = False) -> dict[str, str | int | float | bool | date | None]:
    """Convert JSON to a concrete model before business writes."""
    model = RESOURCES[role].patch if patch else RESOURCES[role].create
    values: TypeAdapter[dict[str, str | int | float | bool | date | None]] = TypeAdapter(dict[str, str | int | float | bool | date | None])
    return values.validate_python(model.model_validate(value).model_dump(exclude_unset=patch), strict=True)
