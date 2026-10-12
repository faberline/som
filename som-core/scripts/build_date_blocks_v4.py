"""Add immutable date-filter service blocks to the approved v3 library."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from build_service_blocks import block

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "som-code-python/data/blocks/v3/blocks.json"
TARGET = ROOT / "som-code-python/data/blocks/v4/blocks.json"


def clone(blocks: dict[str, dict], old: str, new: str) -> dict:
    result = deepcopy(blocks[old])
    result["id"] = new
    result["params"]["fields"] = "fields_v4"
    result["imports"] = [
        item.replace(
            "from datetime import datetime", "from datetime import date, datetime"
        )
        for item in result["imports"]
    ]
    return result


def additions(base: list[dict]) -> list[dict]:
    blocks = {item["id"]: item for item in base}
    model = clone(blocks, "orm.model.service", "orm.model.service.date")
    model["params"]["date_field"] = "identifier"
    model["template"] = model["template"].replace(
        '        sa.Index("ix_{{table}}_{{filter_field}}_id", "{{filter_field}}", "id"),',
        '        sa.Index("ix_{{table}}_{{filter_field}}_id", "{{filter_field}}", "id"),\n'
        '        sa.Index("ix_{{table}}_{{date_field}}_id", "{{date_field}}", "id"),',
    )

    schema = clone(blocks, "schema.service", "schema.service.date")

    listing = block(
        "api.list.boolean.date",
        {
            "name": "identifier",
            "path": "string",
            "filter_field": "identifier",
            "date_field": "identifier",
            "max_limit": "integer",
        },
        """
    @router.get({{path}}, response_model=list[schemas.{{name}}Read])
    def list_items(
        {{filter_field}}: bool | None = None,
        before: date | None = None,
        limit: int = Query(default=20, ge=1, le={{max_limit}}),
        offset: int = Query(default=0, ge=0),
        db: Session = Depends(get_session),
    ) -> list[models.{{name}}]:
        stmt = select(models.{{name}})
        if {{filter_field}} is not None:
            stmt = stmt.where(models.{{name}}.{{filter_field}} == {{filter_field}})
        if before is not None:
            stmt = stmt.where(models.{{name}}.{{date_field}} < before)
        stmt = stmt.order_by(models.{{name}}.id).limit(limit).offset(offset)
        return list(db.scalars(stmt))
""",
        ["from datetime import date", "from fastapi import Query"],
    )

    test = clone(blocks, "test.service", "test.service.date")
    test["params"]["date_field"] = "identifier"
    test["template"] = test["template"].replace(
        '        self.assertEqual(self.client.delete({{path}} + f"/{item_id}").status_code, 204)',
        "        dated = self.client.patch(\n"
        '            {{path}} + f"/{item_id}", json={"{{date_field}}": "2030-01-02"}\n'
        "        )\n"
        "        self.assertEqual(dated.status_code, 200, dated.text)\n"
        '        self.assertEqual(dated.json()["{{date_field}}"], "2030-01-02")\n'
        "        self.assertEqual(\n"
        '            [row["id"] for row in self.client.get(\n'
        '                {{path}}, params={"{{filter_field}}": True, "before": "2030-01-03"}\n'
        "            ).json()], [item_id]\n"
        "        )\n"
        "        self.assertEqual(self.client.get(\n"
        '            {{path}}, params={"before": "2030-01-02"}\n'
        "        ).json(), [])\n"
        "        self.assertEqual(self.client.get(\n"
        '            {{path}}, params={"before": "not-a-date"}\n'
        "        ).status_code, 422)\n"
        '        self.assertEqual(self.client.delete({{path}} + f"/{item_id}").status_code, 204)',
    )

    migration = clone(blocks, "migration.service", "migration.service.date")
    migration["params"]["date_field"] = "identifier"
    migration["template"] = (
        migration["template"]
        .replace(
            '    op.create_index("ix_{{table}}_{{filter_field}}_id", "{{table}}", ["{{filter_field}}", "id"])',
            '    op.create_index("ix_{{table}}_{{filter_field}}_id", "{{table}}", ["{{filter_field}}", "id"])\n'
            '    op.create_index("ix_{{table}}_{{date_field}}_id", "{{table}}", ["{{date_field}}", "id"])',
        )
        .replace(
            '    op.drop_index("ix_{{table}}_{{filter_field}}_id", table_name="{{table}}")',
            '    op.drop_index("ix_{{table}}_{{date_field}}_id", table_name="{{table}}")\n'
            '    op.drop_index("ix_{{table}}_{{filter_field}}_id", table_name="{{table}}")',
        )
    )

    readme = deepcopy(blocks["service.readme"])
    readme["id"] = "service.readme.date"
    readme["template"] += (
        "\n\nThe list route accepts before=YYYY-MM-DD. It selects dates strictly before "
        "that day. Rows with a null date do not match. Combine it with the "
        "boolean filter when needed.\n"
    )
    return [model, schema, listing, test, migration, readme]


def main() -> None:
    base = json.loads(SOURCE.read_text())
    all_blocks = base + additions(base)
    ids = [item["id"] for item in all_blocks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate block id")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(all_blocks, indent=2) + "\n")
    print(TARGET)


if __name__ == "__main__":
    main()
