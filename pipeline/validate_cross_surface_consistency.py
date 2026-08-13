#!/usr/bin/env python3
"""Prove catalog, public packages, and local SQLite expose identical comparisons."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=root / "public" / "data" / "layers-catalog.json")
    parser.add_argument("--packages", type=Path, default=root / "public" / "data" / "comparisons")
    parser.add_argument("--database", type=Path, default=root / "pipeline" / "output" / "layers.sqlite")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    expected = {
        comparison["id"]: (target["id"], comparison)
        for target in catalog["targets"]
        for comparison in target.get("comparisons", [])
    }
    connection = sqlite3.connect(args.database)
    try:
        database = {
            key: json.loads(record)
            for key, record in connection.execute("SELECT comparison_key, record_json FROM comparisons")
        }
    finally:
        connection.close()
    errors = []
    if set(database) != set(expected):
        errors.append("database comparison keys differ from catalog")
    for key, (slug, comparison) in expected.items():
        if key in database and canonical(database[key]) != canonical(comparison):
            errors.append(f"{key}: database record differs from catalog")
        package_path = args.packages / f"{slug}.json"
        if not package_path.is_file():
            errors.append(f"{key}: missing public QA package")
            continue
        package = json.loads(package_path.read_text(encoding="utf-8"))
        if canonical(package.get("comparison")) != canonical(comparison):
            errors.append(f"{key}: public QA package differs from catalog")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Validated {len(expected)} identical comparison record(s) across catalog/API source, packages, and SQLite")


if __name__ == "__main__":
    main()
