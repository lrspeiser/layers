#!/usr/bin/env python3
"""Prove catalog, public packages, and local SQLite expose identical comparisons."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=root / "public" / "data" / "layers-catalog.json")
    parser.add_argument("--packages", type=Path, default=root / "public" / "data" / "comparisons")
    parser.add_argument("--database", type=Path, default=root / "pipeline" / "output" / "layers.sqlite")
    parser.add_argument("--sparc-index", type=Path, default=root / "public" / "data" / "sparc-profiles.json")
    parser.add_argument("--pilot-packages", type=Path, default=root / "public" / "data" / "pilot-audits")
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
        database_layers = {
            (target_key, layer_key): json.loads(record)
            for target_key, layer_key, record in connection.execute(
                "SELECT targets.target_key, layers.layer_key, layers.metadata_json "
                "FROM layers JOIN targets ON targets.id = layers.target_id"
            )
        }
        database_pilot_audits = {
            key: json.loads(record) if record else None
            for key, record in connection.execute("SELECT target_key, pilot_audit_json FROM targets")
        }
    finally:
        connection.close()
    errors = []
    if set(database) != set(expected):
        errors.append("database comparison keys differ from catalog")
    catalog_layers = {
        (target["id"], layer["id"]): layer
        for target in catalog["targets"]
        for layer in target["layers"]
    }
    if set(database_layers) != set(catalog_layers):
        errors.append("database layer keys differ from catalog/API source")
    for key, layer in catalog_layers.items():
        if key in database_layers and canonical(database_layers[key]) != canonical(layer):
            errors.append(f"{key[0]}/{key[1]}: database layer differs from catalog/API source")
    catalog_pilot_audits = {target["id"]: target.get("pilotAudit") for target in catalog["targets"]}
    if set(database_pilot_audits) != set(catalog_pilot_audits):
        errors.append("database pilot audit target keys differ from catalog")
    for target_id, audit in catalog_pilot_audits.items():
        if canonical(database_pilot_audits.get(target_id)) != canonical(audit):
            errors.append(f"{target_id}: database pilot audit differs from catalog/API source")
        package_path = args.pilot_packages / f"{target_id}.json"
        if audit:
            if not package_path.is_file():
                errors.append(f"{target_id}: missing public pilot audit package")
            elif canonical(json.loads(package_path.read_text(encoding='utf-8')).get('pilotAudit')) != canonical(audit):
                errors.append(f"{target_id}: public pilot audit package differs from catalog")
        elif package_path.is_file():
            errors.append(f"{target_id}: unexpected public pilot audit package")
    for key, (slug, comparison) in expected.items():
        if key in database and canonical(database[key]) != canonical(comparison):
            errors.append(f"{key}: database record differs from catalog")
        package_path = args.packages / f"{comparison.get('comparisonKey', slug)}.json"
        if not package_path.is_file():
            errors.append(f"{key}: missing public QA package")
            continue
        package = json.loads(package_path.read_text(encoding="utf-8"))
        if canonical(package.get("comparison")) != canonical(comparison):
            errors.append(f"{key}: public QA package differs from catalog")
    profile_index = json.loads(args.sparc_index.read_text(encoding="utf-8"))
    expected_profile_ids = {
        target["id"] for target in catalog["targets"]
        if any(layer["id"] == "sparc-2016" for layer in target["layers"])
    }
    if set(profile_index.get("targets", {})) != expected_profile_ids:
        errors.append("SPARC profile index target keys differ from catalog")
    for target_id in sorted(expected_profile_ids):
        layer = catalog_layers[(target_id, "sparc-2016")]
        web_path = layer.get("assets", {}).get("data")
        if not web_path:
            errors.append(f"{target_id}/sparc-2016: missing public data path")
            continue
        record_path = root / "public" / web_path.lstrip("/")
        if not record_path.is_file():
            errors.append(f"{target_id}/sparc-2016: missing public profile record")
            continue
        index_record = profile_index["targets"].get(target_id, {})
        if index_record.get("data") != web_path or index_record.get("sha256") != sha256(record_path):
            errors.append(f"{target_id}/sparc-2016: profile index path or checksum differs")
        profile_record = json.loads(record_path.read_text(encoding="utf-8")).get("target", {})
        if profile_record.get("targetId") != target_id or profile_record.get("summary") != layer.get("profileSummary"):
            errors.append(f"{target_id}/sparc-2016: profile record identity or summary differs")
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        f"Validated {len(expected)} comparison record(s), {len(catalog_layers)} layers, "
        f"{sum(value is not None for value in catalog_pilot_audits.values())} pilot audits, and {len(expected_profile_ids)} SPARC profiles across catalog/API source, packages, and SQLite"
    )


if __name__ == "__main__":
    main()
