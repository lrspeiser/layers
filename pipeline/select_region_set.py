#!/usr/bin/env python3
"""Extend the acquired region set toward a comparison-coverage target.

Coverage is counted in Rubin-by-survey comparison pairs, not tracts: every exact
tract-by-survey overlap in the index is one comparison that could be made. There
are 22,921 of them, so a 10% target is 2,292 pairs.

Selection keeps every already-acquired region first. Re-ranking from scratch
would strand validated pixels and invalidate the reconciliation, injection, and
anomaly products already built on them. New regions are then added in order of
how many confirmed overlaps they carry, which maximises pairs per Rubin cutout,
with a minimum angular separation so the set does not collapse onto one deep
field and mistake a single sky patch for coverage.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OVERLAPS = ROOT / "public/data/coverage/external-overlaps.json"
DEFAULT_FOOTPRINT = ROOT / "public/data/coverage/rubin-dp2-footprint.json"
DEFAULT_EXISTING = ROOT / "public/data/coverage/rubin-pixels-50.json"
DEFAULT_REGISTRY = ROOT / "public/data/survey-registry.json"
DEFAULT_OUTPUT = ROOT / "pipeline/results/coverage/selected-regions-200.json"

MIN_SEPARATION_DEG = 0.5
DEFAULT_CUTOUT_ARCMIN = 4.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def separation_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    ra1, dec1 = math.radians(a[0]), math.radians(a[1])
    ra2, dec2 = math.radians(b[0]), math.radians(b[1])
    cos = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlaps", type=Path, default=DEFAULT_OVERLAPS)
    parser.add_argument("--footprint", type=Path, default=DEFAULT_FOOTPRINT)
    parser.add_argument("--existing", type=Path, default=DEFAULT_EXISTING)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--target-regions", type=int, default=200)
    parser.add_argument("--cutout-size-arcmin", type=float, default=DEFAULT_CUTOUT_ARCMIN)
    parser.add_argument("--min-separation-deg", type=float, default=MIN_SEPARATION_DEG)
    args = parser.parse_args()

    overlaps = json.loads(args.overlaps.read_text(encoding="utf-8"))
    footprint = json.loads(args.footprint.read_text(encoding="utf-8"))
    registry = {item["id"]: item for item in json.loads(args.registry.read_text(encoding="utf-8"))["surveys"]}

    centers: dict[int, tuple[float, float]] = {}
    for row in footprint["tracts"]:
        centers[int(row[0])] = (float(row[1][0]), float(row[1][1]))

    confirmed: dict[int, list[str]] = {int(row[0]): list(row[1]) for row in overlaps["tracts"]}
    total_pairs = sum(len(value) for value in confirmed.values())

    existing_ids: list[int] = []
    if args.existing.is_file():
        for record in json.loads(args.existing.read_text(encoding="utf-8"))["regions"]:
            tract = int(record["tract"])
            if tract not in existing_ids:
                existing_ids.append(tract)

    selected: list[int] = [tract for tract in existing_ids if tract in confirmed]
    chosen_centers = [centers[tract] for tract in selected if tract in centers]

    ranked = sorted(
        (tract for tract in confirmed if tract not in set(selected) and tract in centers),
        key=lambda tract: (-len(confirmed[tract]), tract),
    )
    for tract in ranked:
        if len(selected) >= args.target_regions:
            break
        center = centers[tract]
        if any(separation_deg(center, other) < args.min_separation_deg for other in chosen_centers):
            continue
        selected.append(tract)
        chosen_centers.append(center)

    # If the separation rule starved the target, fill the remainder without it
    # rather than silently returning a short set.
    relaxed = 0
    if len(selected) < args.target_regions:
        for tract in ranked:
            if len(selected) >= args.target_regions:
                break
            if tract in set(selected):
                continue
            selected.append(tract)
            relaxed += 1

    regions: list[dict[str, Any]] = []
    for rank, tract in enumerate(selected, start=1):
        surveys = sorted(confirmed.get(tract, []))
        families = sorted({registry[s]["family"] for s in surveys if s in registry})
        ra, dec = centers[tract]
        regions.append({
            "id": f"dp2-tract-{tract}",
            "rank": rank,
            "tract": tract,
            "center": [ra, dec],
            "sizeArcmin": args.cutout_size_arcmin,
            "confirmedSurveyIds": surveys,
            "surveyFamilies": families,
            "previouslyAcquired": tract in set(existing_ids),
        })

    pairs = sum(len(item["confirmedSurveyIds"]) for item in regions)
    payload = {
        "schemaVersion": "layers-selected-regions-v2",
        "generatedAt": utc_now(),
        "requestedCount": args.target_regions,
        "selectedCount": len(regions),
        "selectionMethod": (
            "Already-acquired regions are kept first so validated pixels and every product built on "
            "them stay valid; remaining regions are added by confirmed-overlap count, which maximises "
            "comparison pairs per Rubin cutout, subject to a minimum angular separation."
        ),
        "coverage": {
            "totalPossiblePairs": total_pairs,
            "selectedPairs": pairs,
            "selectedShare": round(pairs / total_pairs, 5),
            "carriedOverRegions": len([item for item in regions if item["previouslyAcquired"]]),
            "newRegions": len([item for item in regions if not item["previouslyAcquired"]]),
            "separationRelaxedFor": relaxed,
        },
        "caveat": (
            "Selecting a region only makes its comparisons possible. It does not make any of them "
            "measured, and never makes one comparison-ready."
        ),
        "regions": regions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"selected {len(regions)} regions "
        f"({payload['coverage']['carriedOverRegions']} carried over, {payload['coverage']['newRegions']} new)"
    )
    print(f"comparison pairs: {pairs} / {total_pairs} = {100 * pairs / total_pairs:.2f}%")
    if relaxed:
        print(f"separation rule relaxed for {relaxed} regions to reach the target")
    print(f"wrote {args.output.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
