"""Independent rules fixtures and frozen public hashes. Run on GitHub only."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]


def module(name):
    spec = importlib.util.spec_from_file_location("independent_" + name, HERE / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


render = module("render_rules")
refusal = module("refusal_rules")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(relative):
    return json.loads((REPO / relative).read_bytes())


def generated_hashes(prompt):
    return {name: sha(text.encode()) for name, text in render.render_files(prompt).items()}


class RenderTests(unittest.TestCase):
    def test_tracked_public_examples_match_pre_freeze_byte_hashes(self):
        evidence = read("som-rs/evidence/react-output-v1/public-products-2026-10-22.json")
        cases = {row["index"]: row for row in evidence["cases"]}
        for version in range(1, 5):
            with self.subTest(version=version):
                path = REPO / f"som-rs/contracts/compiler-v1/examples/v{version}.prompt.json"
                expected = cases[str(118 + version)]
                self.assertEqual(sha(path.read_bytes()), expected["prompt_file_sha256"])
                self.assertEqual(generated_hashes(json.loads(path.read_bytes())), expected["generated_files"])

    def test_frozen_public_fixture_prompts_match_pre_freeze_hashes(self):
        relative = "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"
        inventory = read("som-rs/contracts/react-output-v1/case-inventory.json")
        self.assertEqual(sha((REPO / relative).read_bytes()), inventory["sources"]["cnl_positive_fixtures"][relative])
        fixtures = [row for row in read(relative)["cases"] if row["category"] == "version_positive"
                    and row["expected_prompt"]["version"] in {f"som.prompt.v{i}" for i in range(1, 5)}]
        expected = {row["index"]: row for row in read("som-rs/evidence/react-output-v1/public-products-2026-10-22.json")["cases"]}
        self.assertEqual(len(fixtures), 4)
        for index, case in enumerate(fixtures):
            with self.subTest(case=case["id"]):
                self.assertEqual(sha(canonical(case["expected_prompt"])), case["prompt_sha256"])
                self.assertEqual(generated_hashes(case["expected_prompt"]), expected[f"{index:03d}"]["generated_files"])

    def test_all_nine_frozen_name_edits_match_pre_freeze_hashes(self):
        names = read("som-rs/contracts/react-output-v1/target-examples.json")["name_cases"]
        expected = {row["id"]: row for row in read("som-rs/evidence/react-output-v1/name-cases-2026-10-22.json")["cases"]}
        self.assertEqual(len(names), 9)
        for case in names:
            with self.subTest(case=case["id"]):
                body = json.loads(case["body"])
                if case["form"] == "json":
                    prompt = body["prompt"]
                else:
                    base = REPO / "som-rs/contracts/compiler-v1/examples" / case["base_example"]
                    prompt = json.loads(base.with_suffix(".prompt.json").read_bytes())
                    lines = base.read_text().splitlines()
                    for edit in case["line_edits"]:
                        old, new = lines[edit["line"] - 1], edit["with"]
                        if old.startswith("Field "):
                            self.assertEqual(old.split()[2:], new.split()[2:])
                            field = next(row for row in prompt["resource"]["fields"] if row["name"] == old.split()[1])
                            field["name"] = new.split()[1]
                        elif old.startswith("Use table "):
                            prompt["resource"]["table"] = new[len("Use table "):-1]
                        elif old.startswith("Use path "):
                            prompt["resource"]["path"] = json.loads(new[len("Use path "):-1])
                        else:
                            self.fail("unrecognized frozen name edit")
                        lines[edit["line"] - 1] = new
                    self.assertEqual("\n".join(lines) + "\n", body["source"])
                prompt_bytes = (json.dumps(prompt, sort_keys=True, indent=2) + "\n").encode()
                self.assertEqual(sha(prompt_bytes), expected[case["id"]]["prompt_file_sha256"])
                self.assertEqual(generated_hashes(prompt), expected[case["id"]]["generated_files"])

    def test_nullable_defaults_query_names_and_long_filter_format(self):
        prompt = read("som-rs/contracts/compiler-v1/examples/v3.prompt.json")
        prompt["resource"]["fields"] += [{"name": "count", "type": "int", "required": False, "unique": False, "default": 0}]
        long_name = "field_" + "x" * 45
        prompt["resource"]["fields"][2]["name"] = long_name
        prompt["query"]["filter_field"] = long_name
        generated = render.render_files(prompt)
        schema = generated["src/schema.ts"]
        self.assertIn("  count?: number;", schema)
        self.assertIn("  description?: string | null;", schema)
        self.assertIn("  created_at: string;", schema)
        self.assertIn("  filter: {\n", schema)
        self.assertIn(f'    param: "{long_name}",', schema)
        self.assertIn(f"filtered by `{long_name}`", generated["README.md"])
        prompt["version"] = "som.prompt.v2"
        self.assertIn('param: "filter_value"', render.render_files(prompt)["src/schema.ts"])
        prompt["version"] = "som.prompt.v1"
        schema = render.render_files(prompt)["src/schema.ts"]
        self.assertIn("limit?: never;", schema)
        self.assertIn("QUERY: QuerySpec | null = null;", schema)
        self.assertNotIn("created_at", schema)

    def test_unfrozen_prompt_version_is_refused(self):
        with self.assertRaises(ValueError):
            render.render_files({"version": "som.prompt.v5"})


def bundle(target):
    assets = {name: {"id": target + "." + name, "sha256": sha((target + name).encode())}
              for name in ("grammar", "compiler_contract", "selection_policy", "literal_policy", "serializer",
                           "target_contract", "catalog", "block_library", "backend", "formatter")}
    assets["semantic_contracts"] = []
    result = {"version": "som.dsl-bundle.v1" if target == "python" else "som.dsl-bundle.v2",
        "prompt": {"version": "som.prompt.v1", "package": "fixture", "resource": {"fields": [{"name": "title"}, {"name": "memo"}]}},
        "assets": assets, "dsl": {"version": "som.dsl." + target + ".v1", "files": [
            {"path": f"file-{index:02d}", "blocks": [{"id": target + ".block", "name": f"body-{index}",
             "params": {"field": "title", "nested": ["title", {"field": "memo"}]}, "refs": ["title"]}]} for index in range(19)]}}
    if target != "python":
        result["target"] = target
    result["hashes"] = {"prompt_sha256": sha(canonical(result["prompt"])), "dsl_sha256": sha(canonical(result["dsl"])),
                         "payload_sha256": sha(canonical(result))}
    return result


class RefusalTests(unittest.TestCase):
    def setUp(self):
        self.bases = [bundle(target) for target in ("react", "typescript", "rust", "python")]
        self.v5 = {"version": "som.prompt.v5", "package": "v5"}
        self.shared = {name: {"id": "v5." + name, "sha256": "f" * 64} for name in
                       ("grammar", "compiler_contract", "selection_policy", "literal_policy", "serializer")}
        self.shared["semantic_contracts"] = [{"id": "related-business.v1", "sha256": "e" * 64}]

    def request(self, identity):
        return json.loads(refusal.request_for(identity, *self.bases, self.v5, self.shared))["bundle"]

    def test_all_sixteen_mutations_and_hashes_preserve_sources(self):
        before = canonical([self.bases, self.v5, self.shared])
        outputs = {f"A{i}": self.request(f"A{i}") for i in range(1, 17)}
        self.assertEqual(canonical([self.bases, self.v5, self.shared]), before)
        self.assertEqual(outputs["A1"]["assets"]["formatter"]["sha256"], "0" * 64)
        for name in ("target_contract", "catalog", "block_library", "backend", "formatter"):
            self.assertEqual(outputs["A2"]["assets"][name], self.bases[1]["assets"][name])
        for identity, target in (("A3", "react"), ("A4", "python"), ("A10", "rust"), ("A11", "typescript"), ("A12", "react"), ("A13", "react")):
            self.assertEqual(outputs[identity]["target"], target)
        self.assertEqual(outputs["A3"]["version"], "som.dsl-bundle.v1")
        self.assertEqual(outputs["A12"]["dsl"], self.bases[1]["dsl"])
        self.assertEqual(outputs["A13"]["dsl"], self.bases[2]["dsl"])
        self.assertEqual(outputs["A5"]["dsl"]["files"][0]["blocks"][0]["id"], "orm.base")
        self.assertEqual(outputs["A6"]["prompt"], self.v5)
        for name, value in self.shared.items():
            self.assertEqual(outputs["A6"]["assets"][name], value)
        self.assertEqual(outputs["A8"]["prompt"]["resource"]["fields"][0]["name"], "__proto__")
        block = outputs["A8"]["dsl"]["files"][0]["blocks"][0]
        self.assertEqual(block["params"], {"field": "__proto__", "nested": ["__proto__", {"field": "memo"}]})
        self.assertEqual(block["refs"], ["title"])
        self.assertEqual(len(outputs["A9"]["dsl"]["files"]), 20)
        self.assertEqual(outputs["A9"]["dsl"]["files"][-1], self.bases[0]["dsl"]["files"][-1])
        self.assertEqual(outputs["A14"]["dsl"]["files"][0]["blocks"][0]["id"], "typescript.block")
        sizes = [len(row["blocks"]) for row in outputs["A15"]["dsl"]["files"]]
        self.assertEqual(sum(sizes), 65)
        self.assertLessEqual(max(sizes), 64)
        self.assertEqual([row["path"] for row in outputs["A16"]["dsl"]["files"][:2]], ["file-01", "file-00"])
        self.assertEqual([row["blocks"] for row in outputs["A16"]["dsl"]["files"]], [row["blocks"] for row in self.bases[0]["dsl"]["files"]])
        for identity, value in outputs.items():
            hashes = value.pop("hashes")
            self.assertEqual(hashes["prompt_sha256"], sha(canonical(value["prompt"])))
            self.assertEqual(hashes["dsl_sha256"], sha(canonical(value["dsl"])))
            if identity == "A7":
                self.assertNotEqual(hashes["payload_sha256"], sha(canonical(value)))
            else:
                self.assertEqual(hashes["payload_sha256"], sha(canonical(value)))

    def test_a7_changes_exactly_one_hex_digit_without_rehashing(self):
        changed = self.request("A7")
        before = self.bases[0]
        self.assertEqual({key: value for key, value in changed.items() if key != "hashes"}, {key: value for key, value in before.items() if key != "hashes"})
        old, new = before["hashes"]["payload_sha256"], changed["hashes"]["payload_sha256"]
        self.assertEqual(sum(a != b for a, b in zip(old, new)), 1)
        self.assertEqual(changed["hashes"]["prompt_sha256"], before["hashes"]["prompt_sha256"])

    def test_unknown_recipe_and_exhausted_block_capacity_are_refused(self):
        with self.assertRaises(ValueError):
            self.request("A17")
        self.bases[0]["dsl"]["files"] = [copy.deepcopy(self.bases[0]["dsl"]["files"][0])]
        block = self.bases[0]["dsl"]["files"][0]["blocks"][0]
        self.bases[0]["dsl"]["files"][0]["blocks"] = [copy.deepcopy(block) for _ in range(64)]
        with self.assertRaises(ValueError):
            self.request("A15")


if __name__ == "__main__":
    unittest.main()
