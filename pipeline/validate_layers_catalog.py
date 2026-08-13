#!/usr/bin/env python3
"""Validate the survey-neutral Layers catalog and publication invariants."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED_LAYER_FIELDS = {
    "id", "survey", "release", "instrument", "kind", "availability",
    "renderMode", "bands", "units", "calibration", "hasVariance",
    "hasMask", "hasWcs", "note", "provenance",
}
REGISTRATION_GATES = {
    "commonWcs", "commonFootprint", "psfMatched", "skyMatched", "unitsMatched", "filterMatched",
}
MEASUREMENT_FIELDS = {
    "id", "label", "quantity", "value", "unit", "statisticalUncertainty",
    "systematicUncertainty", "expectedRange", "significanceSigma",
    "classification", "provenance", "caveats",
}
ASSUMPTION_AUDIT_FIELDS = {
    "id", "rank", "title", "priorAssumption", "newEvidence",
    "affectedInference", "confidence", "priorityScore", "evidenceMagnitude",
    "systematicAlternatives", "recommendedFollowUp", "provenance", "caveat",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    plotted_profile_count = 0
    assumption_audits = []
    pilot_audits = []
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
            if layer.get("kind") == "profile" and layer.get("availability") == "available":
                plotted_profile_count += 1
                if layer.get("renderMode") != "plot" or not layer.get("assets", {}).get("data"):
                    errors.append(f"{target_id}/{layer.get('id')}: available profile has no plot data")
            if layer.get("availability") == "published" and layer.get("kind") == "image":
                if not layer.get("assets", {}).get("preview"):
                    errors.append(f"{target_id}/{layer.get('id')}: published image has no preview")
        if target.get("pilotAudit"):
            audit = target["pilotAudit"]
            pilot_audits.append(audit)
            if audit.get("claimStatus") != "blocked" or not audit.get("evidence"):
                errors.append(f"{target_id}: pilot audit must be blocked and carry evidence")
            for evidence in audit.get("evidence", []):
                if not evidence.get("path") or len(evidence.get("sha256", "")) != 64:
                    errors.append(f"{target_id}: pilot audit evidence is incomplete")
                    continue
                evidence_path = root / evidence["path"]
                if not evidence_path.is_file() or sha256(evidence_path) != evidence["sha256"]:
                    errors.append(f"{target_id}: pilot audit evidence file is missing or has a different checksum")

        for comparison in target.get("comparisons", []):
            unknown = set(comparison.get("layerIds", [])) - set(layer_ids)
            if unknown:
                errors.append(f"{target_id}/{comparison.get('id')}: unknown layer ids {sorted(unknown)}")
            for measurement in comparison.get("measurements", []):
                missing = MEASUREMENT_FIELDS - measurement.keys()
                if missing:
                    errors.append(f"{target_id}/{comparison.get('id')}/{measurement.get('id')}: missing {sorted(missing)}")
                sigma = measurement.get("significanceSigma")
                classification = measurement.get("classification")
                expected = "large" if sigma is not None and sigma >= 3 else "noteworthy" if sigma is not None and sigma >= 2 else "expected"
                if classification != expected:
                    errors.append(f"{target_id}/{comparison.get('id')}/{measurement.get('id')}: classification disagrees with sigma")
                if not measurement.get("provenance"):
                    errors.append(f"{target_id}/{comparison.get('id')}/{measurement.get('id')}: measurement has no provenance")
                if not measurement.get("caveats"):
                    errors.append(f"{target_id}/{comparison.get('id')}/{measurement.get('id')}: measurement has no caveats")
            for audit in comparison.get("assumptionAudits", []):
                assumption_audits.append(audit)
                missing = ASSUMPTION_AUDIT_FIELDS - audit.keys()
                if missing:
                    errors.append(f"{target_id}/{comparison.get('id')}/{audit.get('id')}: missing {sorted(missing)}")
                evidence = audit.get("evidenceMagnitude", {})
                if evidence.get("thresholdMultiple", 0) <= 1:
                    errors.append(f"{target_id}/{comparison.get('id')}/{audit.get('id')}: audit does not exceed its pass threshold")
                if audit.get("confidence") not in {"unreviewed", "candidate", "supported", "confirmed"}:
                    errors.append(f"{target_id}/{comparison.get('id')}/{audit.get('id')}: unsupported confidence")
                if not audit.get("provenance") or any(not item for item in audit.get("provenance", [])):
                    errors.append(f"{target_id}/{comparison.get('id')}/{audit.get('id')}: audit has incomplete provenance")
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

    summary = catalog.get("summary", {})
    if summary.get("targets") != len(targets):
        errors.append("catalog: summary target count is wrong")
    if summary.get("publishedComparisons") != published_count:
        errors.append("catalog: summary published comparison count is wrong")
    if summary.get("assumptionsWorthRechecking") != len(assumption_audits):
        errors.append("catalog: assumption audit count is wrong")
    ranks = [audit.get("rank") for audit in sorted(assumption_audits, key=lambda item: item.get("priorityScore", 0), reverse=True)]
    if ranks != list(range(1, len(assumption_audits) + 1)):
        errors.append("catalog: assumption audits are not ranked by descending priority")
    if summary.get("pilotAudits") != len(pilot_audits) or len(pilot_audits) != 4:
        errors.append("catalog: all four Rubin pilot targets must have an explicit pilot audit")
    if plotted_profile_count != len(targets):
        errors.append("catalog: every target must expose one available SPARC profile")

    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Validated Layers catalog: {len(targets)} targets, {sum(len(t['layers']) for t in targets)} layers, {published_count} published comparisons")


if __name__ == "__main__":
    main()
