#!/usr/bin/env python3
"""Collect every operator's surviving candidates into one ranked register.

Six operators now measure different things against different expectations. A
single ranked list is only meaningful if it is honest about that, so this does
not pool their significances into one number.

**Significances from different operators are not comparable.** A 5 sigma pixel
residual is measured against injection-recovery scatter; a 5 sigma ZTF change is
measured against the field's own offset distribution; a 4 sigma SED departure is
measured against the scatter of a fitted colour relation. Each is a departure
from that operator's own null, and the nulls are not the same quantity. The
register therefore keeps the operator, the null it was scored against, and what
would falsify it on every row, and sorts within operator rather than pretending a
global ordering exists.

**What the register is for** is the opposite of ranking: it is the place where a
position flagged by more than one operator becomes visible. A pixel residual is
weak evidence on its own. A pixel residual at the position of an X-ray source
with no optical counterpart, in a field where ZTF says something changed, is
worth a human looking at it. Independent confirmation is the only thing here that
upgrades a candidate, and it is computed rather than asserted.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ROOT / "public/data/layers"
DEFAULT_OUTPUT = ROOT / "pipeline/results/anomaly-register"
DEFAULT_PUBLIC = LAYERS / "anomaly-register.json"

# Two candidates from different operators are treated as the same place on the
# sky inside this radius. Generous, because the operators have very different
# positional precision: eROSITA is arcseconds, HIPASS is arcminutes.
COINCIDENCE_ARCSEC = 30.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def separation_arcsec(a: tuple[float, float], b: tuple[float, float]) -> float:
    ra1, dec1 = a
    ra2, dec2 = b
    cos_dec = math.cos(math.radians((dec1 + dec2) / 2.0))
    return math.hypot((ra1 - ra2) * cos_dec, dec1 - dec2) * 3600.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", default="200")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    args = parser.parse_args()

    suffix = "" if args.set in {"none", ""} else f"-{args.set}"
    regions_dir = LAYERS / "selected-regions"

    entries: list[dict[str, Any]] = []
    sources_seen: list[str] = []
    missing: list[str] = []

    pixels = load(regions_dir / f"region-anomalies{suffix}.json")
    if pixels:
        sources_seen.append("pixel-residual")
        for item in pixels.get("topCandidates", []):
            entries.append({
                "operator": "pixel-residual",
                "what": "Rubin minus reference residual with no boring explanation",
                "regionId": item.get("regionId"),
                "tract": item.get("tract"),
                "position": {"raDeg": item["sky"]["raDeg"], "decDeg": item["sky"]["decDeg"]},
                "significance": item.get("empiricalSigma"),
                "scoredAgainst": "injection-recovery blank-position scatter at this template scale",
                "detail": f"{item.get('direction')} at {item.get('effectiveRadiusArcsec')}\" scale, "
                          f"seen at {item.get('scalesDetected', 1)} scales",
                "falsifiedBy": [
                    "re-measure in the second Rubin band",
                    "re-measure against DES or HSC",
                    "inject a synthetic source of the same amplitude and confirm recovery",
                ],
            })
    else:
        missing.append("pixel-residual")

    xray = load(LAYERS / "xray-counterparts/comparison.json")
    if xray:
        sources_seen.append("xray-counterpart")
        for item in xray.get("sources", []):
            if item.get("opticalCounterpart"):
                continue
            entries.append({
                "operator": "xray-counterpart",
                "what": "eRASS1 X-ray source with no optical counterpart",
                "regionId": item.get("regionId"),
                "tract": item.get("tract"),
                "position": {"raDeg": item["position"]["raDeg"], "decDeg": item["position"]["decDeg"]},
                "significance": None,
                "scoredAgainst": "blank-aperture scatter in the same Rubin image",
                "detail": f"no optical flux above {item.get('detectionThresholdNjy', 0):.0f} nJy, "
                          f"limiting {item.get('limitingMagAB')} AB; "
                          f"X-ray likelihood {item.get('xrayDetectionLikelihood')}",
                "falsifiedBy": [
                    "check the eRASS1 detection likelihood; a spurious X-ray detection looks identical",
                    "measure the chance-coincidence rate for this field",
                    "go deeper in the optical",
                ],
            })
    else:
        missing.append("xray-counterpart")

    ztf = load(LAYERS / "ztf-variability/comparison.json")
    if ztf:
        sources_seen.append("variability")
        for item in ztf.get("mostChanged", []):
            # The variability operator flags its whole changed population when the
            # brighter/fainter split is one-sided, which means the comparison is
            # biased rather than the objects having moved. Those must not enter a
            # register whose purpose is to surface things worth looking at.
            if item.get("populationSystematic"):
                continue
            entries.append({
                "operator": "variability",
                "what": "changed between the ZTF baseline and the Rubin epoch",
                "regionId": item.get("regionId"),
                "tract": item.get("tract"),
                "position": {"raDeg": item["raDeg"], "decDeg": item["decDeg"]},
                "significance": abs(item.get("changeSignificance") or 0.0),
                "scoredAgainst": "the field's own Rubin-minus-ZTF offset distribution",
                "detail": f"{item.get('epochs')} ZTF epochs, relative change "
                          f"{item.get('relativeChangeMag', 0):+.2f} mag",
                "falsifiedBy": [
                    "a single Rubin epoch cannot separate a real change from a bad measurement",
                    "re-measure in another Rubin epoch or band",
                ],
            })
    else:
        missing.append("variability")

    sed = load(LAYERS / "sed/consistency.json")
    if sed:
        sources_seen.append("sed-departure")
        for item in (sed.get("noteworthy") or {}).get("sources", []):
            entries.append({
                "operator": "sed-departure",
                "what": "Rubin flux departs from what the infrared SED predicts",
                "regionId": None,
                "tract": None,
                "position": {"raDeg": item["raDeg"], "decDeg": item["decDeg"]},
                "significance": abs(item.get("significanceSigma") or 0.0),
                "scoredAgainst": "observed scatter about the fitted infrared-colour relation",
                "detail": f"residual {item.get('colourRelationResidualDex', 0):+.2f} dex",
                "falsifiedBy": [
                    "a power law is a crude SED; real near-to-mid infrared curvature sets a floor",
                    "fit a stellar population model instead",
                ],
            })
    else:
        missing.append("sed-departure")

    # Independent confirmation. This is the only thing in the register that
    # upgrades a candidate, and it is computed from positions rather than
    # asserted. Coincidence between two rows of the same operator means nothing.
    for entry in entries:
        entry["confirmedBy"] = []
    for index, entry in enumerate(entries):
        for other in entries[index + 1 :]:
            if other["operator"] == entry["operator"]:
                continue
            gap = separation_arcsec(
                (entry["position"]["raDeg"], entry["position"]["decDeg"]),
                (other["position"]["raDeg"], other["position"]["decDeg"]),
            )
            if gap <= COINCIDENCE_ARCSEC:
                entry["confirmedBy"].append({"operator": other["operator"], "separationArcsec": round(gap, 2)})
                other["confirmedBy"].append({"operator": entry["operator"], "separationArcsec": round(gap, 2)})

    multi = [entry for entry in entries if entry["confirmedBy"]]
    by_operator: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_operator.setdefault(entry["operator"], []).append(entry)
    for rows in by_operator.values():
        rows.sort(key=lambda item: -(item["significance"] or 0.0))

    summary = {
        "schemaVersion": "layers-anomaly-register-v1",
        "generatedAt": utc_now(),
        "regionSet": args.set,
        "operatorsIncluded": sources_seen,
        "operatorsMissing": missing,
        "counts": {
            "candidates": len(entries),
            "byOperator": {name: len(rows) for name, rows in by_operator.items()},
            "flaggedByMoreThanOneOperator": len(multi),
        },
        "policy": {
            "significancesAreNotComparableAcrossOperators": True,
            "note": (
                "Each significance is a departure from its own operator's null, and those nulls are "
                "different quantities. Rows are sorted within an operator, never pooled into one "
                "global ranking, because a global ranking would imply a comparison that has not been "
                "made."
            ),
            "noneAreDetections": True,
            "whatWouldUpgradeOne": (
                "Independent confirmation: the same position flagged by an operator that measures "
                "something else. That is computed here from positions and is the only upgrade path "
                "the register offers."
            ),
        },
        "coincidenceRadiusArcsec": COINCIDENCE_ARCSEC,
        "multiOperator": sorted(multi, key=lambda item: -len(item["confirmedBy"])),
        "byOperator": by_operator,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"register: {len(entries)} candidates from {len(sources_seen)} operators")
    for name, rows in sorted(by_operator.items()):
        print(f"  {name:20s} {len(rows)}")
    if missing:
        print(f"not yet available: {', '.join(missing)}")
    print(f"flagged by more than one operator: {len(multi)}")


if __name__ == "__main__":
    main()
