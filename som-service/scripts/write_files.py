#!/usr/bin/env python3
"""Write the files of a som-service assemble reply into a directory.

Usage: write_files.py <directory> < reply.json
The input is either the `files` map (what `som-service assemble` prints) or
the whole `/assemble` reply with `status` and `files`.
"""
import json
import os
import sys

out = sys.argv[1]
data = json.load(sys.stdin)
files = data["files"] if "status" in data and "files" in data else data
for rel, text in files.items():
    if rel.startswith("/") or ".." in rel.split("/"):
        sys.exit("unsafe path in reply: " + rel)
    path = os.path.join(out, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
