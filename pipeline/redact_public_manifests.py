#!/usr/bin/env python3
"""Strip local filesystem paths out of the published layer manifests.

``pipeline/results`` is gitignored because it holds pixels, several of which are
access-restricted and cannot be redistributed. A public manifest that prints a
path into it advertises a directory layout no reader has, and it states where
restricted data sits on one machine. Neither is useful to a reader and the
second is the kind of thing that should never be published by accident.

This was found by a test that walks every published manifest rather than a
named list. The previous checks named specific files, so each new operator's
output was unguarded by construction, and one shipped with paths in it.

Only the path string is removed. The checksum and byte count stay, because they
are the provenance that lets someone verify a file they reproduce themselves,
and they name nothing about this machine.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "public/data/layers"

LOCAL_PATH = re.compile(r"pipeline[\\/]results", re.I)
# Keys whose value is a path into the local results tree. Removing the key is
# right rather than blanking it: an empty string reads like a missing file.
PATH_KEYS = {"path", "cachePath", "localPath", "localFits", "file", "filePath"}
REDACTION_NOTE = (
    "Local paths are not published. Checksums and byte counts remain so a file reproduced from "
    "the recorded source can be verified; the pixels live outside this repository."
)


def redact(node: Any) -> tuple[Any, int]:
    removed = 0
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in PATH_KEYS and isinstance(value, str) and LOCAL_PATH.search(value):
                removed += 1
                continue
            cleaned, count = redact(value)
            removed += count
            out[key] = cleaned
        return out, removed
    if isinstance(node, list):
        cleaned_list = []
        for item in node:
            cleaned, count = redact(item)
            removed += count
            cleaned_list.append(cleaned)
        return cleaned_list, removed
    return node, removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--check", action="store_true", help="report without writing")
    args = parser.parse_args()

    touched, total = [], 0
    for path in sorted(args.root.rglob("*.json")):
        text = path.read_text(encoding="utf-8")
        if not LOCAL_PATH.search(text):
            continue
        payload = json.loads(text)
        cleaned, removed = redact(payload)
        remaining = LOCAL_PATH.findall(json.dumps(cleaned))
        if isinstance(cleaned, dict) and removed:
            cleaned.setdefault("localPathPolicy", REDACTION_NOTE)
        rel = path.relative_to(ROOT).as_posix()
        touched.append((rel, removed, len(remaining)))
        total += removed
        if not args.check and removed:
            path.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")

    verb = "would remove" if args.check else "removed"
    for rel, removed, remaining in touched:
        flag = f"  STILL PRESENT: {remaining}" if remaining else ""
        print(f"{verb} {removed:4d} from {rel}{flag}")
    print(f"\n{verb} {total} local paths across {len(touched)} manifests")
    if any(remaining for _, _, remaining in touched):
        raise SystemExit("some local paths sit under keys this does not know; extend PATH_KEYS")


if __name__ == "__main__":
    main()
