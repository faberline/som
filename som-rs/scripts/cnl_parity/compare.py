"""Compare compiler records without changing either report or expected values."""

import argparse
import collections
import hashlib
import json
from pathlib import Path


def compare(corpus, before, after, parse_only=False):
    expected = set()
    for case in corpus["cases"]:
        expected.add(f"cnl/{case['id']}")
        if not parse_only:
            expected.update(f"{target}/{case['id']}" for target in case["targets"])
    differences = []
    for key in sorted(expected | before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if key not in expected or old is None or new is None or old != new:
            fields = []
            if isinstance(old, dict) and isinstance(new, dict):
                fields = sorted(field for field in old.keys() | new.keys()
                                if old.get(field) != new.get(field))
            differences.append({"record": key, "fields": fields})
    failures = [
        {"side": side, "record": key, "reason": reason}
        for side, report in (("baseline", before), ("candidate", after))
        for key, record in report.items()
        for reason in ("expectation_mismatch", "assemble_error")
        if reason in record
    ]
    original = set(corpus["original_ids"])
    products = collections.Counter(
        key.split("/", 1)[0] for key, record in after.items()
        if key.split("/", 1)[1] in original and "files" in record
    )
    summary = {
        "version": "som.cnl-parity-result.v1",
        "parse_only": parse_only,
        "input_labels": len(corpus["cases"]),
        "distinct_inputs": corpus["distinct_source_count"],
        "expected_records": len(expected),
        "baseline_records": len(before),
        "candidate_records": len(after),
        "differences": differences,
        "unexpected_outcomes": failures,
        "original_products": dict(sorted(products.items())),
        "passed": not differences and not failures,
    }
    if not parse_only and sum(products.values()) != 322:
        summary["passed"] = False
        summary["product_count_error"] = "The original 178 inputs must produce 322 products."
    return summary


def check_original(corpus, report, retained, parse_only=False):
    prefixes = ("cnl",) if parse_only else ("cnl", "python", "rust", "typescript", "react")
    keys = {f"{prefix}/{case}" for prefix in prefixes for case in corpus["original_ids"]}
    return sorted(key for key in keys if key not in retained or report.get(key) != retained[key])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--parse-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--original-report", type=Path)
    args = parser.parse_args()
    inputs = [path.read_bytes() for path in (args.corpus, args.baseline, args.candidate)]
    summary = compare(*(json.loads(data) for data in inputs), parse_only=args.parse_only)
    if args.original_report:
        retained_bytes = args.original_report.read_bytes()
        corpus, before, after = (json.loads(data) for data in inputs)
        retained = json.loads(retained_bytes)
        original_differences = {
            "baseline": check_original(corpus, before, retained, args.parse_only),
            "candidate": check_original(corpus, after, retained, args.parse_only),
        }
        summary["original_reference_differences"] = original_differences
        summary["original_reference_sha256"] = hashlib.sha256(retained_bytes).hexdigest()
        summary["passed"] &= not any(original_differences.values())
    summary["sha256"] = {
        label: hashlib.sha256(data).hexdigest()
        for label, data in zip(("corpus", "baseline", "candidate"), inputs)
    }
    text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
