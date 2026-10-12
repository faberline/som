"""Build a separate quality library without changing IDs or token ABI."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "som-core/block_templates/quality"


def main() -> None:
    original = json.loads((ROOT / "som-code-python/data/blocks/composed/blocks.json").read_text())
    catalog = json.loads((ROOT / "som-code-python/data/block-contracts/composed.json").read_text())
    updated = deepcopy(original)
    changed = []
    for block in updated:
        identity = block["id"]
        if identity not in catalog["blocks"]:
            continue
        path = TEMPLATES / (identity + ".json")
        if path.exists():
            patch = json.loads(path.read_text())
            block.update(patch)
        block["version"] += 1
        # All module imports are metadata. Local SDK imports remain local only
        # when importing there is necessary for startup/environment ordering.
        imports = re.findall(r"^(?:from [^\n]+ import [^\n]+|import [^\n]+)$", block["template"], re.M)
        block["imports"] = sorted(set(block["imports"] + imports))
        block["template"] = re.sub(r"^(?:from [^\n]+ import [^\n]+|import [^\n]+)\n?", "", block["template"], flags=re.M)
        canonical = json.dumps(block, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        before = next(item for item in original if item["id"] == identity)
        assert block["params"] == before["params"], identity
        assert {key: value for key, value in catalog["blocks"][identity].items()
                if key != "source_sha256"} == {
                    key: value for key, value in json.loads(
                        (ROOT / "som-code-python/data/block-contracts/composed.json").read_text()
                    )["blocks"][identity].items() if key != "source_sha256"}, identity
        catalog["blocks"][identity]["source_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
        changed.append(identity)
    destination = ROOT / "som-code-python/data/blocks/composed-quality"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "blocks.json").write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
    catalog["quality_contract"] = "som.python-quality.v1"
    (ROOT / "som-code-python/data/block-contracts/composed-quality.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"total": len(updated), "quality_blocks": len(changed),
                      "params_and_token_encoding": "unchanged"}))


if __name__ == "__main__":
    main()
