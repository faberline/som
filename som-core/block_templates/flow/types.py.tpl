type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]
JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def json_value(value: object) -> JsonValue:
    """Validate a JSON scalar or container at a boundary."""
    return JSON_VALUE.validate_python(value, strict=True)


def json_object(value: object) -> JsonObject:
    """Validate data at the SDK and dynamic JSON boundary."""
    return JSON_OBJECT.validate_python(value, strict=True)


class InputRef(BaseModel):
    """Reference a validated request field."""
    model_config = ConfigDict(extra="forbid")
    input: str


class StepRef(BaseModel):
    """Reference a result that dominates the current step."""
    model_config = ConfigDict(extra="forbid")
    step: str
    field: str


class ConstantRef(BaseModel):
    """A typed constant checked by the generation contract."""
    model_config = ConfigDict(extra="forbid")
    literal: JsonValue
    type: str


type FlowRef = InputRef | StepRef | ConstantRef


class Predicate(BaseModel):
    """One approved scalar comparison."""
    model_config = ConfigDict(extra="forbid")
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "is_null"]
    left: FlowRef
    right: FlowRef | None = None


class BranchExport(BaseModel):
    """Matching results at a branch join."""
    then: FlowRef
    otherwise: FlowRef = Field(alias="else")


class ActionNode(BaseModel):
    """An approved action and its selected inverse."""
    model_config = ConfigDict(extra="forbid")
    name: str
    id: str
    args: dict[str, FlowRef]
    undo: str | None


class TransactionNode(BaseModel):
    """One actual database commit scope."""
    model_config = ConfigDict(extra="forbid")
    name: str
    id: str
    steps: list["FlowNode"]


class BranchNode(BaseModel):
    """Two scoped alternatives; only declared exports survive."""
    model_config = ConfigDict(extra="forbid")
    name: str
    id: str
    condition: Predicate
    then: list["FlowNode"]
    otherwise: list["FlowNode"] = Field(alias="else")
    exports: dict[str, BranchExport]


type FlowNode = ActionNode | TransactionNode | BranchNode
TransactionNode.model_rebuild()
BranchNode.model_rebuild()


class FlowPlan(BaseModel):
    """The validated model plan; no runtime planner may modify it."""
    model_config = ConfigDict(extra="forbid")
    name: str
    steps: list[FlowNode]
    result: dict[str, FlowRef]


class Effect(BaseModel):
    """One committed effect or unresolved external intent."""
    group: str
    step: str
    block: str
    undo: str
    role: str = ""
    row_id: int = 0
    revision: int = 0
    before: JsonObject = Field(default_factory=dict)
    fields: list[str] = Field(default_factory=list)
    key: str = ""
    version: str | None = None
    owned: bool = False
    uncertain: bool = False


class RunRecord(BaseModel):
    """Durable first-party execution data; never contains credentials."""
    run_id: str
    owner: str
    flow: str
    key: str
    request_hash: str
    epoch: int = 1
    state: Literal["running", "succeeded", "compensating", "compensated", "recovery_required"] = "running"
    inputs: JsonObject
    values: JsonObject = Field(default_factory=dict)
    branches: dict[str, bool] = Field(default_factory=dict)
    effects: list[Effect] = Field(default_factory=list)
    result: JsonObject = Field(default_factory=dict)
    error_status: int = 503
    error_detail: str = "execution interrupted"


class CrudRequest(BaseModel):
    """Validated arguments for a single resource operation."""
    role: Literal["parent", "child"]
    operation: Literal["create", "get", "list", "patch", "delete"]
    parent_id: int | None = None
    row_id: int | None = None
    data: JsonObject = Field(default_factory=dict)
    limit: int = 20
    offset: int = 0
    filter_value: bool | None = None
    before: date | None = None


class ResourceSpec(BaseModel):
    """Typed binding of one approved resource to its HTTP models."""
    table: str
    create: type[BaseModel]
    patch: type[BaseModel]
    read: type[BaseModel]
    filter_field: str | None
    date_field: str | None


PLAN_SOURCE = {{plan_json}}
PLAN = FlowPlan.model_validate_json(PLAN_SOURCE)
PLAN_HASH = hashlib.sha256(PLAN_SOURCE.encode()).hexdigest()
ACTION_IDS: dict[str, str] = {}
INVERSE_IDS: dict[str, str] = {}
CONTROL_IDS: dict[str, str] = {}
CHECKED_IDS: set[str] = set()


def integer(value: JsonValue) -> int:
    """Do not let a boolean or a string become a managed ID."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise HTTPException(status_code=422, detail="integer required")
    return value


def text_value(value: JsonValue) -> str:
    """Validate a string at the dynamic boundary."""
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="string required")
    return value


class FlowInput(BaseModel):
    """Typed request fields forbid extra fields and readonly data."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
{{#flow_inputs}}    {{name}}: {{input_type}}
{{/flow_inputs}}
