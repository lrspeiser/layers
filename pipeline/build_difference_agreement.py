#!/usr/bin/env python3
"""Cross-match the difference peaks between reference pairings.

A single reference can show that Rubin and it disagree somewhere. It cannot say
which of the two the disagreement belongs to. Three references can, and the
logic is the same one the attribution operator uses on whole-field scales,
applied here per position:

* a peak that appears against **several independent references** is on the Rubin
  side, because Rubin is the only term those comparisons share;
* a peak that appears against **one** belongs to that reference or to its
  matching, and is not worth a second look.

Legacy, DES and Pan-STARRS are independently calibrated and were reduced by
different pipelines, so agreement between them is meaningful rather than
circular. Positions are matched on the sky, not in pixels: the three pairings sit
on different grids (0.4, 0.263 and 0.25 arcsec) and a pixel offset means a
different angle in each.

This does not turn a confirmed peak into a detection. Every pairing shares the
same unvalidated bandpass transfer, so a genuine colour difference would appear
in all three and be "confirmed" by this test. What the test removes is the much
larger population of per-reference artefacts.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ROOT / "public/data/layers"
DEFAULT_OUTPUT = LAYERS / "selected-regions/difference-agreement.json"

PAIRINGS = (
    ("legacy", "difference-index.json", "difference-peaks"),
    ("des", "difference-index-des.json", "difference-peaks-des"),
    ("ps1", "difference-index-ps1.json", "difference-peaks-ps1"),
)
MATCH_ARCSEC = 2.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def separation_arcsec(a: dict[str, float], b: dict[str, float]) -> float:
    """Small-angle separation, which is exact enough at arcsecond scales."""
    dec = np.radians((a["decDeg"] + b["decDeg"]) / 2.0)
    d_ra = (a["raDeg"] - b["raDeg"]) * np.cos(dec)
    d_dec = a["decDeg"] - b["decDeg"]
    return float(np.hypot(d_ra, d_dec) * 3600.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    peaks_by_pairing: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for name, index_file, peaks_dir in PAIRINGS:
        index_path = LAYERS / "selected-regions" / index_file
        if not index_path.is_file():
            continue
        index = json.loads(index_path.read_text(encoding="utf-8"))
        regions: dict[str, list[dict[str, Any]]] = {}
        for entry in index["regions"]:
            path = LAYERS / peaks_dir / f"{entry['regionId']}.json"
            if not path.is_file():
                continue
            regions[entry["regionId"]] = json.loads(path.read_text(encoding="utf-8")).get("peaks", [])
        peaks_by_pairing[name] = regions

    if len(peaks_by_pairing) < 2:
        raise SystemExit("Agreement needs at least two pairings; Rubin being the shared term is the method.")

    all_regions = sorted(set().union(*(set(v) for v in peaks_by_pairing.values())))
    records: list[dict[str, Any]] = []
    for region_id in all_regions:
        present = [name for name in peaks_by_pairing if region_id in peaks_by_pairing[name]]
        if len(present) < 2:
            continue

        # Only off-source peaks are worth cross-matching. An on-source peak is a
        # PSF-match residual and appears in every pairing by construction, so
        # "confirmed" would be meaningless for them.
        candidates = {
            name: [p for p in peaks_by_pairing[name][region_id] if not p["onSource"]]
            for name in present
        }
        merged: list[dict[str, Any]] = []
        for name in present:
            for peak in candidates[name]:
                for group in merged:
                    if separation_arcsec(peak["sky"], group["sky"]) <= MATCH_ARCSEC:
                        group["seenIn"][name] = round(peak["sigma"], 2)
                        break
                else:
                    merged.append({"sky": peak["sky"], "seenIn": {name: round(peak["sigma"], 2)}})

        confirmed = [g for g in merged if len(g["seenIn"]) >= 2]
        for group in merged:
            group["referenceCount"] = len(group["seenIn"])
            # Direction has to agree too: one reference brighter and another
            # fainter at the same spot is not a confirmation of anything.
            signs = {np.sign(v) for v in group["seenIn"].values()}
            group["directionsAgree"] = len(signs) == 1
        records.append({
            "regionId": region_id,
            "tract": int(region_id.rsplit("-", 1)[-1]),
            "pairingsAvailable": present,
            "offSourcePeaksByPairing": {name: len(candidates[name]) for name in present},
            "distinctPositions": len(merged),
            "confirmedByTwoOrMore": len(confirmed),
            "confirmedAndDirectionsAgree": sum(
                1 for g in confirmed if g["directionsAgree"]
            ),
            "positions": sorted(merged, key=lambda g: -g["referenceCount"])[:20],
        })

    multi = [r for r in records if r["confirmedByTwoOrMore"]]
    payload = {
        "schemaVersion": "layers-difference-agreement-v1",
        "generatedAt": utc_now(),
        "purpose": (
            "Which difference peaks appear against more than one independent reference. Rubin is "
            "the only term the pairings share, so a repeated peak is on the Rubin side and a "
            "solitary one belongs to the reference that shows it."
        ),
        "matchRadiusArcsec": MATCH_ARCSEC,
        "matchedOnSky": (
            "Positions are matched in sky coordinates, not pixels: the pairings sit on 0.4, 0.263 "
            "and 0.25 arcsec grids, so the same pixel offset is a different angle in each."
        ),
        "onSourceExcluded": (
            "Only off-source peaks are cross-matched. A peak on a bright source is a PSF-match "
            "residual that appears in every pairing by construction, so confirming one would mean "
            "nothing."
        ),
        "counts": {
            "regionsWithTwoOrMorePairings": len(records),
            "distinctOffSourcePositions": sum(r["distinctPositions"] for r in records),
            "confirmedByTwoOrMore": sum(r["confirmedByTwoOrMore"] for r in records),
            "confirmedWithAgreeingDirection": sum(r["confirmedAndDirectionsAgree"] for r in records),
            "regionsWithAConfirmedPosition": len(multi),
        },
        "caveat": (
            "Confirmation across references is not a detection. Every pairing shares the same "
            "unvalidated bandpass transfer, so a genuine colour difference would appear in all of "
            "them and be confirmed here. What this removes is the larger population of "
            "per-reference artefacts."
        ),
        "regions": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # The full record is 129 KB and holds every off-source position. The page
    # only needs the confirmed ones, so it gets a file small enough to import.
    slim = {
        "schemaVersion": "layers-difference-agreement-slim-v1",
        "generatedAt": payload["generatedAt"],
        "matchRadiusArcsec": payload["matchRadiusArcsec"],
        "counts": payload["counts"],
        "caveat": payload["caveat"],
        "onSourceExcluded": payload["onSourceExcluded"],
        "confirmed": [
            {
                "regionId": record["regionId"],
                "tract": record["tract"],
                "sky": group["sky"],
                "seenIn": group["seenIn"],
                "referenceCount": group["referenceCount"],
                "directionsAgree": group["directionsAgree"],
            }
            for record in records
            for group in record["positions"]
            if group["referenceCount"] >= 2
        ],
    }
    slim_path = args.output.parent / "difference-agreement-slim.json"
    slim_path.write_text(json.dumps(slim, separators=(",", ":")) + "\n", encoding="utf-8")

    print(json.dumps(payload["counts"], indent=2))
    print(f"slim index {slim_path.stat().st_size} bytes, {len(slim['confirmed'])} confirmed")
    for record in sorted(multi, key=lambda r: -r["confirmedByTwoOrMore"])[:10]:
        print(f"  {record['regionId']:18s} {record['confirmedByTwoOrMore']} confirmed "
              f"of {record['distinctPositions']} positions, pairings {record['pairingsAvailable']}")


if __name__ == "__main__":
    main()
