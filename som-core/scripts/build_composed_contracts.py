"""Build new metadata without changing any previously approved block bytes."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import argparse

from som_core.composed import CATALOG, SLOTS, canonical

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "som-code-python/data/blocks/composed"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rebuild-candidate', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=args.rebuild_candidate)
    contract = ROOT / "som-rs/contracts/related-business.v1.md"
    contract.parent.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("sealed_requirements", ROOT / "som-rs/evals/business-paired-2026-09-30-r3/prepare.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    common = module.task_text({}).split("INTERPRETATION\n", 1)[1]
    contract.write_text("# Related business service contract v1\n\n" +
        "The supplied operations select which parent/child CRUD and batch routes exist. "
        "Rules below apply to selected routes and chosen capabilities. Omitted operations "
        "must not appear in OpenAPI. Each service has one parent, one child, one database, "
        "one auth provider, one storage choice, and one workflow.\n\n" + common)
    blocks = json.loads((OUT.parent / "v5/blocks.json").read_text())
    # Keep every legacy block intact. A private alias prevents a user resource
    # named Document from becoming the base class of the second resource.
    for original in list(blocks):
        if original['id'] in {'orm.mongo.base', 'orm.mongo.parent', 'orm.mongo.child'}:
            revised = deepcopy(original)
            revised['id'] = original['id'].replace('orm.mongo.', 'orm.composed.mongo.')
            revised['template'] = revised['template'].replace('from beanie import Document', 'from beanie import Document as _SomBeanieDocument').replace('(Document):', '(_SomBeanieDocument):')
            blocks.append(revised)
    blocks.append({"id":"test.composed.behavior", "version":1, "imports":["import json"], "params":{"routes":"string"}, "template":'''class ComposedServiceTest(unittest.TestCase):
    def test_selected_routes_require_identity(self):
        with TestClient(app) as client:
            schema = client.get("/openapi.json").json()
            for route in json.loads({{routes}}):
                self.assertIn(route["method"], schema["paths"][route["path"]])
                path = route["path"].replace("{parent_id}", "1").replace("{child_id}", "1")
                response = client.request(route["method"], path, json={})
                self.assertEqual(response.status_code, 401, response.text)


if __name__ == "__main__":
    unittest.main()'''})
    (OUT / "blocks.json").write_text(json.dumps(blocks, indent=2)+"\n")
    library = {b["id"]: b for b in blocks}
    templates = json.loads((ROOT / "som-core/data/business-service/templates.json").read_text())
    entries = {}
    for dsl in templates.values():
        if not any(f["path"] == SLOTS["integrations"] for f in dsl["files"]):
            continue
        for f in dsl["files"]:
            slot = next(k for k,v in SLOTS.items() if v == f["path"])
            names = {b["name"]: slot+":"+b["name"] for b in f["blocks"]}
            for raw in f["blocks"]:
                b = deepcopy(raw)
                if b['id'] in {'orm.mongo.base', 'orm.mongo.parent', 'orm.mongo.child'}:
                    b['id'] = b['id'].replace('orm.mongo.', 'orm.composed.mongo.')
                if b["id"] == "test.business.behavior":
                    b["id"] = "test.composed.behavior"; b["params"] = {"routes":{"$ref":"contract.routes"}}
                bid, node = b["id"], names[b["name"]]
                requires = [names[r] for r in b["refs"]]
                if slot in {"models", "migration"} and b["name"] == "child": requires.append(slot+":parent")
                if slot == "tests" and b["name"] in {"parent", "child"}: requires.append("tests:head")
                if node == "app:head": requires += ["models:parent", "models:child", "schemas:parent", "schemas:child", "schemas:batch", "integrations:auth"]
                if node == "app:app": requires += ["app:tail", "database:database", "integrations:auth", "integrations:storage"]
                if node == "database:database": requires += ["models:parent", "models:child"]
                caps, query, operation, routes = {}, None, None, []
                if any(x in bid for x in (".mongodb", ".mongo.")) or bid == "migration.mongo": caps["database"] = "mongodb"
                elif bid.startswith(("orm.related.", "migration.related.", "api.related.")) or bid in {"orm.base", "service.database", "service.alembic.env", "service.alembic.ini"} or bid.endswith(".postgresql"): caps["database"] = "postgresql"
                for kind in ("auth", "storage", "workflow"):
                    for prefix in (kind+".", "readme."+kind+".", "api.business."+kind+"."):
                        if bid.startswith(prefix) and bid[len(prefix):] not in {"common"}:
                            caps[kind] = bid[len(prefix):]
                if bid.endswith((".plain", ".boolean", ".date")):
                    parts = bid.split("."); query = ["parent" if "parent" in parts else "child", parts[-1]]
                if slot == "app" and (bid.startswith("api.related.") or bid.startswith("api.mongo.")):
                    parts = bid.split(".")
                    if parts[-1] == "batch": operation = ["batch", "batch_create"]
                    elif parts[2] in {"parent", "child"}: operation = parts[2:4]
                    if operation: routes = [": ".join(operation)]
                if node == "app:workflow": routes = ["workflow:export"] + (["workflow:import"] if caps.get("workflow") == "csv" else [])
                meta = {"slot":slot, "provides":node, "requires":sorted(set(requires)), "capabilities":caps, "query":query, "operation":operation,
                        "routes":routes, "bindings":b["params"], "params":library[bid]["params"],
                        "authorization":"require_access" if operation or node == "app:workflow" else None,
                        "transaction":"atomic" if operation == ["batch","batch_create"] or caps.get("workflow") == "csv" and node == "app:workflow" else None,
                        "source_sha256":hashlib.sha256(canonical(library[bid]).encode()).hexdigest()}
                if bid in entries and entries[bid] != meta: raise ValueError("ambiguous block contract: "+bid)
                entries[bid] = meta
    required = ["models:base", "models:parent", "models:child", "schemas:bounds", "schemas:parent", "schemas:child", "schemas:batch", "app:head", "app:workflow", "app:tail", "app:app", "tests:head", "tests:parent", "tests:child", "tests:test", "database:database", "init:init", "project:project", "readme:readme", "readme:auth", "readme:storage", "readme:workflow", "integrations:auth_common", "integrations:auth", "integrations:storage"]
    result = {"version":"som.block-contracts.v1", "contract_sha256":hashlib.sha256(contract.read_bytes()).hexdigest(), "slots":SLOTS, "blocks":entries,
              "required_common":required, "required_database":{"postgresql":["migration:head", "migration:parent", "migration:child", "migration:tail", "alembic_env:env", "alembic_ini:config"], "mongodb":["mongo_migration:migration"]}}
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"blocks":len(blocks),"composable_contracts":len(entries),"contract_sha256":result["contract_sha256"]}))


if __name__ == "__main__": main()
