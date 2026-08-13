#!/usr/bin/env python3
"""Validate generic external catalog-layer identity and science invariants."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def without_global_ranks(value: dict) -> dict:
    value = json.loads(json.dumps(value))
    for audit in value.get("assumptionAudits", []):
        audit.pop("rank", None)
    return value


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", nargs="?", type=Path, default=root / "pipeline/output/wise-stellar-masses/manifest.json")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors = []
    if manifest.get("schemaVersion") != 1 or manifest.get("adapterContract") != "layers-catalog-layer-v1":
        errors.append("unsupported external catalog-layer contract")
    sources = manifest.get("sources", {})
    for key, local in (
        ("wiseCatalog", root / "pipeline/cache/wise-stellar-masses-table1.dat"),
        ("sparcCatalog", root / "pipeline/cache/sparc/SPARC_Lelli2016c.mrt"),
    ):
        source = sources.get(key, {})
        if not local.is_file() or source.get("sha256") != sha256(local):
            errors.append(f"{key}: source missing or checksum mismatch")
        if not source.get("url") or source.get("records") is None:
            errors.append(f"{key}: source provenance incomplete")
    contract = manifest.get("scienceContract", {})
    if contract.get("expectedOffsetDex") != 0.1 or contract.get("expectedScatterDex") != 0.18:
        errors.append("paper-supported expected offset/scatter changed")
    targets = manifest.get("targets", [])
    if len(targets) != 111 or len({item.get("targetId") for item in targets}) != 111:
        errors.append("expected 111 distinct WISE–SPARC target matches")
    pilot_ids = {"ngc0100", "ugc00191", "ugc00634", "ugc00891"}
    if pilot_ids & {item.get("targetId") for item in targets}:
        errors.append("a Rubin pilot was assigned a WISE mass absent from the published catalog")
    counts = {"expected": 0, "noteworthy": 0, "large": 0}
    for item in targets:
        target_id = item.get("targetId", "<missing>")
        layer = item.get("layer", {})
        comparison = item.get("comparison", {})
        if layer.get("kind") != "catalog" or layer.get("renderMode") != "table" or layer.get("id") != manifest.get("layerId"):
            errors.append(f"{target_id}: layer is not a generic catalog/table layer")
        if comparison.get("comparisonMode") != "catalog-profile" or comparison.get("status") != "published":
            errors.append(f"{target_id}: comparison is not a published catalog-profile comparison")
        gates = comparison.get("compatibility", {})
        if any(gates.get(key) is not True for key in ("targetIdentityMatched", "quantityMatched", "unitsMatched", "distanceScaleShared", "modelDeclared")):
            errors.append(f"{target_id}: compatibility gate failed")
        measurement = (comparison.get("measurements") or [{}])[0]
        sigma = measurement.get("significanceSigma", -1)
        expected_class = "large" if sigma >= 3 else "noteworthy" if sigma >= 2 else "expected"
        if measurement.get("classification") != expected_class:
            errors.append(f"{target_id}: classification disagrees with sigma")
        else:
            counts[expected_class] += 1
        if measurement.get("systematicUncertainty") != 0.18 or measurement.get("expectedCenter") != 0.1:
            errors.append(f"{target_id}: expected cross-survey uncertainty model missing")
        if not measurement.get("provenance") or len(measurement.get("caveats", [])) < 5:
            errors.append(f"{target_id}: measurement provenance/caveats incomplete")
        if expected_class == "expected" and comparison.get("assumptionAudits"):
            errors.append(f"{target_id}: expected difference was promoted to an assumption audit")
        if expected_class != "expected" and len(comparison.get("assumptionAudits", [])) != 1:
            errors.append(f"{target_id}: noteworthy/large difference lacks exactly one audit")
        public_path = root / "public" / item.get("record", "").lstrip("/")
        if not public_path.is_file():
            errors.append(f"{target_id}: public layer record missing")
            continue
        public = json.loads(public_path.read_text(encoding="utf-8"))
        if (
            public.get("targetId") != target_id
            or public.get("layer") != layer
            or without_global_ranks(public.get("comparison", {})) != without_global_ranks(comparison)
        ):
            errors.append(f"{target_id}: public layer record differs from manifest")
        for audit in public.get("comparison", {}).get("assumptionAudits", []):
            if not isinstance(audit.get("rank"), int) or audit["rank"] < 0:
                errors.append(f"{target_id}: public global audit rank is invalid")
    if manifest.get("cohort", {}).get("classifications") != counts:
        errors.append("cohort classification summary differs from records")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Validated 111 generic WISE catalog layers and comparisons: {counts}; no fabricated Rubin-pilot masses")


if __name__ == "__main__":
    main()
