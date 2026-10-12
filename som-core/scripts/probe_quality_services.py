"""Preserve failed authoring probes separately from acceptance evidence."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "som-core/src"))
from som_core import quality
from som_core.v1 import assemble, load_blocks, resolve_prompt_refs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--indices")
    parser.add_argument("--data",type=Path,default=ROOT / "som-core/data/composed/regression.jsonl")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = [json.loads(line) for line in args.data.read_text().splitlines()]
    library = load_blocks(ROOT / "som-code-python/data/blocks/composed-quality")
    binary = Path(sys.executable).parent / "ruff"
    results = []
    for index in map(int, args.indices.split(",")) if args.indices else range(len(rows)):
        row = rows[index]
        files = assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library)
        root = Path(tempfile.mkdtemp(prefix="som-quality-probe-", dir="/private/tmp"))
        for name, source in files.items():
            if name.endswith(".py"):
                source = quality.assemble_imports(source)
                common = ["--config", str(quality.PROFILE), "--config", "lint.isort.known-first-party=" + json.dumps([row["prompt"]["package"]]), "--no-cache", "--stdin-filename", name]
                for command in ([str(binary), "check", *common, "--select", "I", "--fix", "-"], [str(binary), "format", *common, "-"]):
                    formatted = subprocess.run(command, input=source, text=True, capture_output=True, check=False)
                    if formatted.returncode == 0:
                        source = formatted.stdout
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        from verify_python_quality import verify
        result = {"index": index, "package": row["prompt"]["package"], "directory": str(root)}
        result.update(verify(root, args.output / str(index)))
        results.append(result)
        (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps({"index": index, "package": result["package"], "passed": result["passed"], "directory": str(root)}), flush=True)


if __name__ == "__main__":
    main()
