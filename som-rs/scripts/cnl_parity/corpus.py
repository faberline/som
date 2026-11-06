"""Build fixed CNL comparison inputs from the retained compiler checkout."""

import argparse
import hashlib
import json
from pathlib import Path


TARGETS = ["python", "rust", "typescript", "react"]


def build(root: Path) -> dict:
    cases = {}

    def add(name, source, negative=None, targets=()):
        assert name not in cases, name
        cases[name] = {
            "id": name,
            "source": source,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "negative": negative,
            "targets": list(targets),
        }

    examples = root / "contracts/compiler-v1/examples"
    fixtures = root / "tests/compiler-v1"
    repositories = root / "tests/cnl-dsl"
    for path in sorted(examples.glob("*.cnl")):
        add(f"examples/{path.name}", path.read_text(), False, TARGETS)
    for path in sorted(repositories.glob("*.cnl")):
        source = path.read_text()
        if path.stem == "defer-queue-task":
            add("repo/defer-trailing-padding", source + " \t", False, TARGETS)
            add("repo/defer-string-queue-id", source.replace(
                "Input queue_id has type integer.", "Input queue_id has type string.", 1
            ), True, TARGETS)
        elif path.stem == "flaskr-post":
            add("repo/flaskr-self-reference", source.replace(
                "Set argument child_id to input post_id.",
                'Set argument child_id to step fetched field "id".', 1
            ), True, TARGETS)
        add(f"repo/{path.stem}", source, False, TARGETS)
    for case in json.loads((fixtures / "cnl-positive-fixtures.json").read_text())["cases"]:
        add(f"frozen/{case['id']}", case["cnl"], False, TARGETS)
    names = json.loads((fixtures / "self-name-fixtures.json").read_text())
    for group in ("positive", "accepted", "negative"):
        for case in names[group]:
            add(f"names/{group}/{case['id']}", case["cnl"], group == "negative", TARGETS)
    v7 = (examples / "v7.cnl").read_text()
    for case in json.loads((fixtures / "cnl-negative-cases.json").read_text())["cases"]:
        lines = v7.splitlines()
        if "replace_line" in case:
            lines[case["replace_line"] - 1] = case["with"]
        for edit in case.get("replace_lines", []):
            lines[edit["line"] - 1] = edit["with"]
        if "insert_before_line" in case:
            insert = case["insert_before_line"]
            lines.insert(insert["line"] - 1, insert["text"])
        add(f"negative/{case['id']}", "\n".join(lines) + "\n", True, TARGETS)
    original_ids = sorted(cases)
    assert len(original_ids) == 178, len(original_ids)

    # The original matrix stays fixed. These extra inputs compare parsing cheaply.
    for version in range(1, 8):
        source = (examples / f"v{version}.cnl").read_text()
        prefix = f"expanded/v{version}"
        for suffix, value in {
            "no-final-newline": source.rstrip("\n"),
            "eof-space": source + " ",
            "eof-tab": source + "\t",
            "crlf-padding": source.replace("\n", "\r\n") + " \t ",
            "indented": "\n".join(" \t" + line + "\t " for line in source.splitlines()) + "\n",
            "blank-lines": "\n\t\n" + source.replace("\n", "\n \t\n"),
            "bom": "\ufeff" + source,
            "bare-cr": source + "\r",
            "extra-sentence": source + " \tUse package extra.\n",
            "missing-period": source.replace("Use SOM CNL version 1.", "Use SOM CNL version 1", 1),
            "unknown-language": source.replace("Use SOM CNL version 1.", "Use SOM CNL version 9.", 1),
            "unknown-version": source.replace(f"Use prompt version {version}.", "Use prompt version 99.", 1),
            "version-leading-zero": source.replace(f"Use prompt version {version}.", f"Use prompt version 0{version}.", 1),
            "version-extra-token": source.replace(f"Use prompt version {version}.", f"Use prompt version {version} extra.", 1),
        }.items():
            add(f"{prefix}/{suffix}", value)
        lines = source.splitlines(keepends=True)
        for index, line in enumerate(lines):
            add(f"{prefix}/missing-line-{index + 1:03}", "".join(lines[:index] + lines[index + 1:]))
            add(f"{prefix}/duplicate-line-{index + 1:03}", "".join(lines[:index] + [line] + lines[index:]))
            if line.startswith(("Begin ", "End ")):
                add(f"{prefix}/eof-before-{index + 1:03}", "".join(lines[:index]))

    flaskr = (repositories / "flaskr-post.cnl").read_text()
    unknown = flaskr.replace("Bind contract related-flow.v1 ", "Bind contract not-a-contract.v1 ", 1)
    malformed = "Set argument child_id from input post_id."
    original_argument = "Set argument child_id to input post_id."
    bad_argument = flaskr.replace(original_argument, malformed, 1)
    wrong_close = flaskr.replace("End flow get_post.", "End flow bad_name.", 1)
    duplicate = flaskr.replace("Use package flaskr_blog.\n", "Use package flaskr_blog.\nUse package again.\n", 1)
    overflow = flaskr.replace("Limit query results to 20.", "Limit query results to 184467440737095516160.", 1)
    mixed = {
        "unknown-contract": unknown,
        "unknown-contract-then-bad-argument": unknown.replace(original_argument, malformed, 1),
        "unknown-contract-then-bad-close": unknown.replace("End flow get_post.", "End flow bad_name.", 1),
        "overflow-then-bad-argument": overflow.replace(original_argument, malformed, 1),
        "duplicate-then-bad-argument": duplicate.replace(original_argument, malformed, 1),
        "bad-argument-then-lexical-error": bad_argument + 'Use path "\\uD800".\n',
        "bad-close-then-lexical-error": wrong_close + "\r",
        "unknown-version-then-lexical-error": flaskr.replace("Use prompt version 7.", "Use prompt version 99.", 1) + "\r",
        "unknown-version-then-syntax-error": flaskr.replace("Use prompt version 7.", "Use prompt version 99.", 1).replace(original_argument, malformed, 1),
        "early-syntax-then-unknown-contract": unknown.replace("Use package flaskr_blog.", "Use package flaskr_blog extra.", 1),
        "early-syntax-then-overflow": overflow.replace("Use package flaskr_blog.", "Use package flaskr_blog extra.", 1),
        "syntax-then-duplicate": duplicate.replace("Use SOM CNL version 1.", "Use SOM CNL version 1 extra.", 1),
    }
    for name, source in mixed.items():
        add(f"expanded/mixed/{name}", source, True, TARGETS)

    for name, value in {
        "unicode": '"臺灣 😀 é"',
        "surrogate-pair": '"\\ud83d\\ude00"',
        "high-surrogate": '"\\ud800"',
        "low-surrogate": '"\\udc00"',
        "invalid-escape": '"\\q"',
        "unterminated": '"oops',
        "control": '"line\x01"',
        "json-adjacent": '"text"oops',
    }.items():
        source = v7.replace('Return label from literal string "台灣 \\"demo\\"\\nline".',
                            f"Return label from literal string {value}.", 1)
        assert source != v7, name
        add(f"expanded/literal/{name}", source)
    for number in ("-0", "-0.0", "1e2", "1e9999", "01", "+1", "NaN", "184467440737095516160"):
        add(f"expanded/number/{number}", v7.replace("literal float -0.0", f"literal float {number}", 1))

    # Balanced excess depth must be scanned before semantic budgets are applied.
    for depth in (1, 2, 3, 4, 16, 128, 512):
        opening = "".join(f"Begin transaction nested_{n}.\n" for n in range(depth))
        closing = "".join(f"End transaction nested_{n}.\n" for n in reversed(range(depth)))
        nested = flaskr.replace("Begin step fetched", opening + "Begin step fetched", 1)
        nested = nested.replace("End step fetched.\n", "End step fetched.\n" + closing, 1)
        add(f"expanded/depth/{depth}/balanced", nested)
        add(f"expanded/depth/{depth}/bad-argument", nested.replace(original_argument, malformed, 1))
        add(f"expanded/depth/{depth}/bad-close", nested.replace("End flow get_post.", "End flow bad_name.", 1))
        add(f"expanded/depth/{depth}/lexical-error", nested + "\r")

    # Exercise each branch arm and both orders of mixed block nesting. Build the
    # flat source with lists, so the corpus generator has no recursion limit.
    for family in ("branch-then", "branch-else", "mixed-branch-first", "mixed-transaction-first"):
        for depth in (1, 2, 3, 4, 16, 128, 512):
            openings, closings = [], []
            deepest_end = None
            for level in range(depth):
                transaction = family.startswith("mixed-") and (
                    level % 2 == (0 if family == "mixed-transaction-first" else 1)
                )
                name = f"stress_{family.replace('-', '_')}_{level}"
                if transaction:
                    openings.append(f"Begin transaction {name}.\n")
                    deepest_end = f"End transaction {name}."
                    closings.append(deepest_end + "\n")
                else:
                    arm = "else" if family == "branch-else" else "then"
                    openings.append(
                        f"Begin branch {name} when input post_id is greater than literal integer 0.\n"
                        "Begin then.\n"
                        + ("End then.\nBegin else.\n" if arm == "else" else "")
                    )
                    deepest_end = f"End branch {name}."
                    closings.append(
                        ("End then.\nBegin else.\n" if arm == "then" else "")
                        + "End else.\nExport no values.\n" + deepest_end + "\n"
                    )
            opening = "".join(openings)
            blocks = opening + "".join(reversed(closings))
            nested = flaskr.replace("Begin step fetched", blocks + "Begin step fetched", 1)
            prefix = f"expanded/nested/{family}/{depth}"
            variants = {
                "balanced": nested,
                "bad-argument": nested.replace(original_argument, malformed, 1),
                "bad-close": nested.replace(deepest_end, "End branch wrong_name.", 1),
                "lexical-error": nested + "\r",
                "eof-in-block": flaskr.split("Begin step fetched", 1)[0] + opening,
            }
            for suffix, source in variants.items():
                # Include JSON transport overhead, not only the CNL byte count.
                request = {"format": "som.cnl.v1", "source": source, "target": "typescript"}
                assert len(json.dumps(request).encode()) < 262144, (prefix, suffix)
                add(f"{prefix}/{suffix}", source)

    for name, source in (("empty", ""), ("spaces", " \t"), ("blank-crlf", " \r\n\t\r\n")):
        add(f"expanded/{name}", source)

    return {
        "version": "som.cnl-parity-corpus.v1",
        "original_ids": original_ids,
        "distinct_source_count": len({case["source_sha256"] for case in cases.values()}),
        "cases": [cases[key] for key in sorted(cases)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path, help="Retained SOM repository root")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    corpus = build(args.baseline / "som-rs")
    data = (json.dumps(corpus, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    args.output.write_bytes(data)
    print(f"{len(corpus['cases'])} labels, {corpus['distinct_source_count']} distinct inputs")
    print(f"SHA-256 {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
