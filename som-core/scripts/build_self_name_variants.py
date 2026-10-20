"""Append the #57 `self` name variant blocks to the approved block libraries.

usage: build_self_name_variants.py [--check]

Each variant is its base block with exact text substitutions, appended after the
existing blocks. No existing block changes. compiler-v1 selects a variant only when a
resource field or the foreign key is named `self` (selection-policy.json
`self_name_variants`). `--check` verifies that the libraries already hold exactly the
derived variants and changes nothing.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLOCKS = ROOT / "som-code-python/data/blocks"

CONSTRUCT = (
    "def _som_construct(*args: object, **kwargs: object) -> None:\n"
    '    """Set mapped values; the row is positional so any field name works."""\n'
    "    (row,) = args\n"
    "    kind = type(row)\n"
    "    for key, value in kwargs.items():\n"
    "        if not hasattr(kind, key):\n"
    '            message = f"{key!r} is an invalid keyword argument for {kind.__name__}"\n'
    "            raise TypeError(message)\n"
    "        setattr(row, key, value)\n"
    "\n\n"
)
REGISTRY_IMPORT = "from sqlalchemy.orm import registry as _som_registry"

# (library, base id, variant id, imports: a line to add or an (old, new) replacement, [(old, new), ...])
VARIANTS = [
    ("v5", "orm.base", "orm.base.self", [REGISTRY_IMPORT], [
        ("class Base(DeclarativeBase):\n    pass",
         CONSTRUCT + "class Base(DeclarativeBase):\n    registry = _som_registry(constructor=_som_construct)"),
    ]),
    ("v5", "orm.mongo.base", "orm.mongo.base.self", [], [
        ("from beanie import Document\nfrom pydantic import Field",
         "from beanie import Document as _SomBeanieDocument\nfrom pydantic import Field\n\n\n"
         "class Document(_SomBeanieDocument):\n"
         "    def __init__(*args: object, **data: object) -> None:\n"
         "        (row,) = args\n"
         "        if not isinstance(row, _SomBeanieDocument):\n"
         "            raise TypeError(row)\n"
         "        super(_SomBeanieDocument, row).__init__(**data)\n"
         "        row.get_pymongo_collection()"),
    ]),
    ("composed-quality", "orm.base", "orm.base.self", [REGISTRY_IMPORT], [
        ("# pylint: disable-next=too-few-public-methods\nclass Base(DeclarativeBase):\n"
         '    """Typed service contract."""\n',
         CONSTRUCT + "# pylint: disable-next=too-few-public-methods\nclass Base(DeclarativeBase):\n"
         '    """Typed service contract."""\n\n'
         "    registry = _som_registry(constructor=_som_construct)\n"),
    ]),
    ("composed-quality", "orm.composed.mongo.base", "orm.composed.mongo.base.self", [
        ("from beanie import Document as _SomBeanieDocument", "from beanie import Document as _SomBeanieSdkDocument"),
    ], [
        ("\n\nclass _SomUniqueField(_SomTypedDict):\n",
         "\n\n# pylint: disable-next=too-many-ancestors\n"
         "class _SomBeanieDocument(_SomBeanieSdkDocument):\n"
         '    """Beanie document; the row is positional so that any field name works."""\n\n'
         "    def __init__(self, /, **data: object) -> None:\n"
         "        super(_SomBeanieSdkDocument, self).__init__(**data)\n"
         "        self.get_pymongo_collection()\n"
         "\n\nclass _SomUniqueField(_SomTypedDict):\n"),
    ]),
    ("flow-v7-compact", "compact.service.flow.store.postgresql",
     "compact.service.flow.store.postgresql.self", [], [
        ("        found = await self.one(sa.select(table_for(role)).filter_by(**scope, id=row_id), 404)\n",
         "        table = table_for(role)\n"
         '        found = await self.one(sa.select(table).where(*_som_match(table, {**scope, "id": row_id})), 404)\n'),
        ("        statement = sa.select(table).filter_by(**scope)\n",
         "        statement = sa.select(table).where(*_som_match(table, scope))\n"),
        ("            statement = statement.filter_by(**{spec.filter_field: request.filter_value})\n",
         "            statement = statement.where(table.c[spec.filter_field] == request.filter_value)\n"),
        ("\n\nclass _Store:\n",
         "\n\ndef _som_match(table: sa.Table, values: Mapping[str, object]) -> list[sa.ColumnElement[bool]]:\n"
         '    """Match columns by name so that any field name is a valid key."""\n'
         "    return [table.c[key] == value for key, value in values.items()]\n\n\nclass _Store:\n"),
    ]),
]


def derive(base: dict, variant_id: str, imports: list, edits: list[tuple[str, str]]) -> dict:
    template = base["template"]
    lines = list(base["imports"])
    for item in imports:
        if isinstance(item, str):
            lines.append(item)
        elif lines.count(item[0]) == 1:
            lines[lines.index(item[0])] = item[1]
        else:
            raise SystemExit(f"{base['id']}: import to replace is not unique: {item[0]!r}")
    for old, new in edits:
        if template.count(old) != 1:
            raise SystemExit(f"{base['id']}: substitution source is not unique: {old!r}")
        template = template.replace(old, new)
    for marker in ("filter_by(**scope", "filter_by(**{"):
        if marker in template:
            raise SystemExit(f"{variant_id}: keyword splat remains: {marker}")
    return {"id": variant_id, "version": base["version"], "imports": lines,
            "params": base["params"], "template": template}


def main() -> None:
    check = sys.argv[1:] == ["--check"]
    if sys.argv[1:] not in ([], ["--check"]):
        raise SystemExit(__doc__)
    failures = []
    for library in dict.fromkeys(row[0] for row in VARIANTS):
        path = BLOCKS / library / "blocks.json"
        raw = path.read_text()
        blocks = json.loads(raw)
        if json.dumps(blocks, indent=2) + "\n" != raw:
            raise SystemExit(f"{path}: not in the canonical library layout")
        variants = {row[2] for row in VARIANTS if row[0] == library}
        kept = [block for block in blocks if block["id"] not in variants]
        by_id = {block["id"]: block for block in kept}
        derived = [derive(by_id[base], variant, imports, edits)
                   for lib, base, variant, imports, edits in VARIANTS if lib == library]
        if blocks[len(kept):] != derived or blocks[:len(kept)] != kept:
            failures.append(str(path.relative_to(ROOT)))
            if not check:
                path.write_text(json.dumps(kept + derived, indent=2) + "\n")
    if check and failures:
        raise SystemExit("variant blocks differ: " + ", ".join(failures))
    print(("checked" if check else "wrote"), len(VARIANTS), "variants;",
          "changed: " + ", ".join(failures) if failures else "no change")


if __name__ == "__main__":
    main()
