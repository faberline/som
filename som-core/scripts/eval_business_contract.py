"""Compare HTTP refusal categories with Python for old and new prompts."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

import httpx

from som_core.v1 import V1Error, validate_prompt

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prompts = []
    for name in ("service-v3", "date-service-v4", "related-service"):
        prompts += [json.loads(line)["prompt"] for line in (ROOT / f"som-core/data/{name}/rejections.jsonl").read_text().splitlines()]
    base = json.loads((ROOT / "som-core/data/business-service/requirements.jsonl").read_text().splitlines()[0])["prompt"]
    for key, value in (("database", "sqlite"), ("auth", "none"), ("storage", "local_disk"), ("workflow", "eval"), ("value_field", "title"), ("group_field", "title"), ("secret", "forbidden")):
        p = deepcopy(base)
        p["capabilities"][key] = value
        prompts.append(p)
    for path in ("/auth/me", "/objects/key", "../unsafe"):
        p = deepcopy(base); p["resources"]["parent"]["path"] = path; prompts.append(p)
    p = deepcopy(base); p["resources"]["child"]["path"] = "/export"; prompts.append(p)
    p = deepcopy(base); p["capabilities"].update(workflow="statistics", value_field="title"); prompts.append(p)
    p = deepcopy(base); p["source"] = "print('not a prompt')"; prompts.append(p)
    checks = []
    with httpx.Client(base_url=args.url, timeout=15) as client:
        for index, p in enumerate(prompts):
            try:
                validate_prompt(p)
            except V1Error as error:
                code = error.code
            else:
                continue
            response = client.post("/v1/generate", json=p)
            result = response.json()
            assert response.status_code >= 400 and result["status"] == "error" and result["code"] == code, (index, code, result)
            checks.append({"index": index, "code": code})
        malformed = client.post("/v1/generate", content="{", headers={"Content-Type": "application/json"})
        assert malformed.status_code == 400 and malformed.json()["code"] == "INVALID_JSON"
        receipt = {"health": client.get("/healthz").json(), "checks": checks, "malformed_json": "passed", "passed": len(checks) + 1}
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": receipt["passed"]}))


if __name__ == "__main__":
    main()
