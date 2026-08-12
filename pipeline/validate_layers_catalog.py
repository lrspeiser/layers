#!/usr/bin/env python3
"""Validate the survey-neutral Layers catalog and publication invariants."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_LAYER_FIELDS = {
    "id", "survey", "release", "instrument", "kind", "availability",
    "renderMode", "bands", "units", "calibration", "hasVariance",
    "hasMask", "hasWcs", "note", "provenance",
}
REGISTRATION_GATES = {
    "commonWcs", "commonFootprint", "psfMatched", "skyMatched", "unitsMatched",
}
MEASUREMENT_FIELDS = {
    "id", "label", "quantity", "value", "unit", "statisticalUncertainty",
    "systematicUncertainty", "expectedRange", "significanceSigma",
    "classification", "provenance", "caveats",
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", nargs="?", type=Path, default=root / "public" / "data" / "layers-catalog.json")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    errors: list[str] = []

    if catalog.get("schemaVersion") != 1 or catalog.get("product") != "Layers":
        errors.append("catalog: unsupported identity or schema version")
    targets = catalog.get("targets", [])
    selection = catalog.get("targetSelection", {})
    if selection.get("count") != len(targets):
        errors.append("catalog: targetSelection count does not match targets")
    if selection.get("complete") is not True:
        errors.append("catalog: declared target selection is incomplete")
    ids = [target.get("id") for target in targets]
    if len(ids) != len(set(ids)):
        errors.append("catalog: duplicate target ids")

    published_count = 0
    for target in targets:
        target_id = target.get("id", "<missing>")
        layers = target.get("layers", [])
        layer_ids = [layer.get("id") for layer in layers]
        if len(layer_ids) != len(set(layer_ids)):
            errors.append(f"{target_id}: duplicate layer ids")
        for layer in layers:
            missing = REQUIRED_LAYER_FIELDS - layer.keys()
            if missing:
                errors.append(f"{target_id}/{layer.get('id')}: missing {sorted(missing)}")
            if layer.get("kind") != "image" and layer.get("renderMode") == "image":
                errors.append(f"{target_id}/{layer.get('id')}: non-image layer forced into image view")
            if layer.get("availability") == "published" and layer.get("kind") == "image":
                if not layer.get("assets", {}).get("preview"):
                    errors.append(f"{target_id}/{layer.get('id')}: published image has no preview")

        for comparison in target.get("comparisons", []):
            unknown = set(comparison.get("layerIds", [])) - set(layer_ids)
            if unknown:
                errors.append(f"{target_id}/{comparison.get('id')}: unknown layer ids {sorted(unknown)}")
            if comparison.get("status") != "published":
                continue
            published_count += 1
            registration = comparison.get("registration", {})
            failed = [gate for gate in REGISTRATION_GATES if registration.get(gate) is not True]
            if failed:
                errors.append(f"{target_id}/{comparison.get('id')}: failed gates {sorted(failed)}")
            threshold = registration.get("qaThresholdArcsec")
            residual = registration.get("maxResidualArcsec")
            if threshold is None or residual is None or residual > threshold:
                errors.append(f"{target_id}/{comparison.get('id')}: astrometric residual is missing or failed")
            for measurement in comparison.get("measurements", []):
                missing = MEASUREMENT_FIELDS - measurement.keys()
                if missing:
                    errors.append(f"{target_id}/{comparison.get('id')}/{measurement.get('id')}: missing {sorted(missing)}")
                sigma = measurement.get("significanceSigma")
                classification = measurement.get("classification")
                expected = "large" if sigma is not None and sigma >= 3 else "noteworthy" if sigma is not None and sigma >= 2 else "expected"
                if classification != expected:
                    errors.append(f"{target_id}/{comparison.get('id')}/{measurement.get('id')}: classification disagrees with sigma")

    summary = catalog.get("summary", {})
    if summary.get("targets") != len(targets):
        errors.append("catalog: summary target count is wrong")
    if summary.get("publishedComparisons") != published_count:
        errors.append("catalog: summary published comparison count is wrong")

    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Validated Layers catalog: {len(targets)} targets, {sum(len(t['layers']) for t in targets)} layers, {published_count} published comparisons")


if __name__ == "__main__":
    main()
