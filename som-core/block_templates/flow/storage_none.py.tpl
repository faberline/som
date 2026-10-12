def verify_flow_storage() -> None:
    """This capability has no object provider."""


def flow_object_put(_key: str, _body: bytes, _owner: str, _step: str) -> FlowObject:
    """The generation validator rejects object nodes for this capability."""
    raise ObjectUnavailable()


def flow_object_reverse(_key: str, _owner: str, _step: str, _version: str | None) -> bool:
    """An object intent cannot occur in a storage-free valid plan."""
    raise ObjectUnavailable()
