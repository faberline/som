router = APIRouter(dependencies=[Depends(require_access)])


async def crud(request: CrudRequest) -> ResourceRow | list[ResourceRow] | None:
    """Share resource reads and writes with flows."""
    store = FlowStore()
    if request.operation == "get":
        return (await store.row(request.role, request.row_id, request.parent_id))[0]
    if request.operation == "list":
        return await store.rows(request)
    async with store.transaction():
        return await store.mutate(request)


def register_route(path: str, role: str, action: CrudOperation, maximum: int = 100) -> None:
    """Register only the operation selected by this block."""
    spec = RESOURCES[role]
    async def endpoint(request: Request) -> Response:
        """Validate HTTP values before each resource operation."""
        try:
            data = json_object(await request.json()) if action in {"create", "patch"} else {}
            params = request.query_params
            query = CrudRequest.model_validate({"role": role, "operation": action, "parent_id": request.path_params.get("parent_id") if role == "child" else None, "row_id": request.path_params.get(role + "_id"), "data": data, "limit": params.get("limit", "20"), "offset": params.get("offset", "0"), "filter_value": params.get(spec.filter_field) if spec.filter_field else None, "before": params.get("before") if spec.date_field else None})
            if query.limit > maximum:
                raise failure(422, "page limit exceeded")
            result = await crud(query)
        except (ValidationError, ValueError) as error:
            raise failure(422, "invalid resource input") from error
        except ConflictError as error:
            raise failure(409, "resource conflict") from error
        status = 201 if action == "create" else 204 if action == "delete" else 200
        return Response(status_code=status) if status == 204 else JSONResponse(content=result_json(result), status_code=status)
    methods = {"create": "POST", "patch": "PATCH", "delete": "DELETE", "get": "GET", "list": "GET"}
    model = GenericAlias(list, spec.read) if action == "list" else spec.read
    router.add_api_route(path, endpoint, methods=[methods[action]], name=role + "_" + action, response_model=None if action == "delete" else model)


async def _workflow_import(parent_id: int, records: list[ChildInputData]) -> list[ChildRow]:
    """Validate a CSV batch before its single transaction."""
    store = FlowStore()
    try:
        async with store.transaction():
            return await store.batch(parent_id, records)
    except ConflictError as error:
        raise failure(409, "resource conflict") from error
