#!/usr/bin/env python3
"""Independently audit cached Rubin SODA cutouts and derived mosaics."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from acquire_dp2_pixels import inspect_masked_image, inspect_mosaic
from download_dp2_matches import sha256


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=root / "pipeline/results/rubin-pixels-50/manifest.json")
    parser.add_argument("--public-manifest", type=Path, default=root / "public/data/coverage/rubin-pixels-50.json")
    parser.add_argument("--report", type=Path, default=root / "pipeline/results/rubin-pixels-50/validation-report.json")
    parser.add_argument("--require-regions", type=int)
    args = parser.parse_args()
    local = json.loads(args.manifest.read_text(encoding="utf-8"))
    public_text = args.public_manifest.read_text(encoding="utf-8")
    public = json.loads(public_text)
    errors: list[str] = []
    warnings: list[str] = []
    regions = local.get("regions", [])
    if args.require_regions is not None and len(regions) != args.require_regions:
        errors.append(f"Expected {args.require_regions} regions, found {len(regions)}")
    if len({record.get("regionId") for record in regions}) != len(regions):
        errors.append("Region IDs are not unique")

    source_count = 0
    source_bytes = 0
    mosaic_bytes = 0
    science_ready = 0
    for record in regions:
        label = record.get("regionId", "<missing>")
        for source in record.get("sources", []):
            source_count += 1
            path_value = source.get("path")
            if not path_value:
                if source.get("status") in {"cached", "fetched"}:
                    errors.append(f"{label}: successful source lacks a path")
                continue
            path = root / path_value
            if not path.is_file():
                errors.append(f"{label}: missing source {path_value}")
                continue
            source_bytes += path.stat().st_size
            if path.stat().st_size != source.get("bytes") or sha256(path) != source.get("sha256"):
                errors.append(f"{label}: source byte count or SHA-256 mismatch")
            inspection = inspect_masked_image(path)
            if not inspection.get("valid"):
                errors.append(f"{label}: cached SODA source fails IMAGE/VARIANCE/MASK/WCS validation")

        if record.get("status") not in {"complete", "validation-failed"}:
            continue
        mosaic = record.get("mosaic", {})
        mosaic_path = root / mosaic.get("path", "")
        if not mosaic_path.is_file():
            errors.append(f"{label}: mosaic is missing")
            continue
        mosaic_bytes += mosaic_path.stat().st_size
        if mosaic_path.stat().st_size != mosaic.get("bytes") or sha256(mosaic_path) != mosaic.get("sha256"):
            errors.append(f"{label}: mosaic byte count or SHA-256 mismatch")
        inspection = inspect_mosaic(mosaic_path)
        if inspection.get("scienceReady") != record.get("validation", {}).get("scienceReady"):
            errors.append(f"{label}: recorded science-readiness does not match independent inspection")
        science_ready += int(inspection.get("scienceReady") is True)
        if record.get("validation", {}).get("comparisonReady"):
            errors.append(f"{label}: comparison-ready was claimed before cross-survey QA")

    expected_summary = {
        "regionCount": len(regions),
        "completeRegionCount": sum(record.get("status") == "complete" for record in regions),
        "scienceReadyRegionCount": science_ready,
        "comparisonReadyRegionCount": 0,
        "sourceCutoutCount": source_count,
        "sourceBytes": source_bytes,
        "mosaicBytes": mosaic_bytes,
    }
    for key, expected in expected_summary.items():
        if local.get("summary", {}).get(key) != expected:
            errors.append(f"Local summary {key} does not match evidence")
        if public.get("summary", {}).get(key) != expected:
            errors.append(f"Public summary {key} does not match evidence")
    if len(public.get("regions", [])) != len(regions):
        errors.append("Public region list length differs from local evidence")
    for forbidden in ("Authorization", "RUBIN_RSP_TOKEN", "X-Amz-", "Signature=", "Expires=", "http://", "https://", "C:\\"):
        if forbidden in public_text:
            errors.append(f"Public manifest contains forbidden material: {forbidden}")

    report = {
        "schemaVersion": "layers-rubin-pixels-validation-v1",
        "validatedAt": datetime.now(timezone.utc).isoformat(),
        "passed": not errors,
        "summary": expected_summary,
        "errors": errors,
        "warnings": warnings,
        "invariants": {
            "sourceChecksumsVerified": not any("source byte" in value or "missing source" in value for value in errors),
            "maskedImagePlanesVerified": not any("IMAGE/VARIANCE/MASK" in value for value in errors),
            "mosaicChecksumsVerified": not any("mosaic byte" in value or "mosaic is missing" in value for value in errors),
            "wcsAndUnitsVerified": science_ready == expected_summary["completeRegionCount"],
            "comparisonReadyClaimsSuppressed": not any("comparison-ready" in value for value in errors),
            "publicManifestRedacted": not any("forbidden material" in value for value in errors),
            "quotaWithinAccountLimit": local.get("policy", {}).get("sodaRequestsPerMinute", 999) <= 35,
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print(f"PASS: {len(regions)} regions, {source_count} SODA cutouts, {science_ready} science-ready inputs, 0 comparison-ready")


if __name__ == "__main__":
    main()
