"""Stable names for the new compact library's internal Python symbols."""

NAMES = {
    "BoundedInput": "_Bounded",
    "PartialInput": "_Partial",
    "ManagedRead": "_ManagedRead",
    "BatchCreate": "_BatchCreate",
    "BatchRead": "_BatchRead",
    "FlowStore": "_Store",
    "FlowContext": "_Context",
    "RunRecord": "_Run",
    "CrudRequest": "_RequestData",
    "ResourceSpec": "_Resource",
    "JsonObject": "_JsonMap",
    "JsonValue": "_Json",
    "ParentInput": "_ParentInput",
    "ParentChanges": "_ParentChanges",
    "ParentView": "_ParentView",
    "ChildInput": "_ChildInput",
    "ChildChanges": "_ChildChanges",
    "ChildView": "_ChildView",
    "flow_object_get": "object_get",
    "flow_object_put": "object_put",
    "flow_object_reverse": "object_reverse",
    "flow_object_version": "object_version",
}


def source_names(source: str) -> str:
    """Rename only Python identifiers, never strings or user names."""
    result = []
    index = 0
    while index < len(source):
        start = index
        char = source[index]
        if char == "#":
            stop = source.find("\n", index)
            index = len(source) if stop < 0 else stop + 1
        elif char in {"'", '"'}:
            marker = char * (3 if source[index:index + 3] == char * 3 else 1)
            index += len(marker)
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                elif source.startswith(marker, index):
                    index += len(marker)
                    break
                else:
                    index += 1
        elif char.isascii() and (char.isalpha() or char == "_"):
            index += 1
            while index < len(source) and source[index].isascii() and (
                source[index].isalnum() or source[index] == "_"
            ):
                index += 1
            result.append(NAMES.get(source[start:index], source[start:index]))
            continue
        else:
            index += 1
        result.append(source[start:index])
    return "".join(result)


def rewrite_calls(source: str, replacements: dict[str, str]) -> str:
    """Change fixed call sites, leaving data literals and comments intact."""
    result = []
    index = 0
    while index < len(source):
        start = index
        char = source[index]
        if char == "#":
            stop = source.find("\n", index)
            index = len(source) if stop < 0 else stop + 1
        elif char in {"'", '"'}:
            marker = char * (3 if source[index:index + 3] == char * 3 else 1)
            index += len(marker)
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                elif source.startswith(marker, index):
                    index += len(marker)
                    break
                else:
                    index += 1
        else:
            match = next((key for key in replacements if source.startswith(key, index)), None)
            boundary = index == 0 or not (source[index - 1].isalnum() or source[index - 1] in "_.")
            if match is not None and boundary:
                result.append(replacements[match])
                index += len(match)
                continue
            index += 1
        result.append(source[start:index])
    return "".join(result)
