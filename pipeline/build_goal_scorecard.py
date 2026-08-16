#!/usr/bin/env python3
"""Score each goal against what the archives hold, not only against its target.

Three of this goal set's targets were computed from what archives declare rather
than what they serve: 727 optical pairs and 229 morphology pairs are sums of
footprint overlap, and 180 two-band regions assumes two bands per tract. Each was
unreachable when written -- by 181 pairs, 195 verdicts and 7 regions.

That distinction should not live only in prose, where it reads like an excuse.
This computes, per goal, three numbers from the manifests: what was asked, what
the archives can actually supply, and what was delivered. A goal is ``met`` when
it reaches its stated target and ``at-ceiling`` when it reaches what exists
instead. The gap between target and ceiling is reported as its own quantity, so
nobody has to take a summary's word for it.

Regenerate after any operator re-runs. The accompanying test asserts the
delivered numbers do not regress and that no goal claims more than it measured.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ROOT / "public/data/layers"
DEFAULT_OUTPUT = LAYERS / "goal-scorecard.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def score(gid, name, target, ceiling, delivered, unit, evidence, note=None,
          ceiling_basis="independently measured from the archive") -> dict[str, Any]:
    met = delivered is not None and target is not None and delivered >= target
    at_ceiling = delivered is not None and ceiling is not None and ceiling > 0 and delivered >= ceiling
    return {
        "id": gid,
        "name": name,
        "statedTarget": target,
        "archiveCeiling": ceiling,
        # Whether the ceiling was measured independently of the result, or is
        # simply the delivered number. The second makes "at-ceiling" circular,
        # so it is labelled rather than left for a reader to infer.
        "ceilingBasis": ceiling_basis if ceiling is not None else None,
        "ceilingIsIndependentOfResult": (
            None if ceiling is None else ceiling_basis != "the delivered value itself"
        ),
        "delivered": delivered,
        "unit": unit,
        "targetExceedsCeilingBy": (target - ceiling) if (target and ceiling and target > ceiling) else 0,
        "fractionOfCeiling": round(delivered / ceiling, 4) if (delivered and ceiling) else None,
        "status": "met" if met else ("at-ceiling" if at_ceiling else "below-ceiling"),
        "evidence": evidence,
        "note": note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    regions_dir = LAYERS / "selected-regions"
    bands = load(regions_dir / "dp2-band-availability.json")
    optical = load(regions_dir / "optical-coverage-truth.json")
    highres = load(LAYERS / "highres-followup/verification-truth.json")
    gaia = load(LAYERS / "gaia-crossmatch/comparison.json")
    sed = load(LAYERS / "sed/consistency.json")
    btfr = load(LAYERS / "hi-gas/baryonic-tully-fisher.json")
    lensing = load(LAYERS / "lensing-light/correlation.json")
    xray = load(LAYERS / "xray-counterparts/comparison.json")
    radio = load(LAYERS / "radio-counterparts/comparison.json")
    ztf = load(LAYERS / "ztf-variability/comparison.json")
    ztf_truth = load(LAYERS / "ztf-variability/coverage-truth.json")
    register = load(LAYERS / "anomaly-register.json")
    summary = load(LAYERS / "site-summary.json")

    optical_totals = optical.get("totals") or {}
    verifiable = highres.get("whatIsActuallyVerifiable") or {}
    rendered = [
        key
        for key in (
            "reconciliation", "crossCheck", "curveOfGrowth", "euclid", "register", "radio",
            "xray", "gaia", "sed", "gas", "lensing", "bandpass", "recovery",
        )
        if summary.get(key)
    ]

    goals = [
        score(
            "G0", "Acquire the 200-tract set, two bands each", 180,
            bands.get("regionsWithAtLeastTwoBands"), bands.get("secondBandValidated"),
            "regions with a validated second band",
            "selected-regions/dp2-band-availability.json",
            "27 of the 200 regions carry exactly one band in the entire DP2 release, so no "
            "acquisition strategy yields a second one for them. The 6 between the delivered "
            "167 and the reachable 173 are not unattempted: acquisition ran three passes, and "
            "each of those 6 failed pixel validation on every band DP2 holds for it. There is "
            "no further pass to run, so 167 is where this goal stops rather than where it "
            "paused.",
        ),
        score(
            "G1", "Same-band optical across the reference surveys", 727,
            optical_totals.get("pixelsValidated"), optical_totals.get("reconciledPairs"),
            "reconciled optical pairs", "selected-regions/optical-coverage-truth.json",
            "The target is a sum of footprint overlaps, 199+164+162+198=723. HSC PDR2 contributes "
            "162 to it and 0 to reality: PDR2 publishes only HiPS tiles, which carry no calibrated "
            "flux and no variance plane. The 21 pairs between the delivered 521 and the reachable "
            "542 are attributed per region in reconciliation-losses.json: 12 to four regions that "
            "hold no Rubin product and so fail against every reference, 4 to regions lacking one "
            "survey's optical product, 2 to reconciler failures with a recorded reason, and 3 to "
            "pairs the pilot run reconciled outside the 200-region set. None is a silent drop.",
        ),
        score(
            "G2", "Gaia cross-match and registration", 200,
            # The ceiling is set by stellar density, not by effort. A Gaia epoch
            # fit needs at least 10 stars detected in both catalogues, and a deep
            # extragalactic field can hold 10 Gaia stars and 10 Rubin detections
            # with fewer than 10 in common. Lowering the floor would raise this
            # number and make every epoch it produced less trustworthy.
            (gaia.get("counts") or {}).get("measured"),
            (gaia.get("counts") or {}).get("measured"), "regions measured",
            "gaia-crossmatch/comparison.json",
            "147 of 200 measured. Of the 53 skipped: 44 have no epoch with 10+ matched stars, "
            "7 have no science-ready Rubin mosaic, 2 have too few Rubin detections. An "
            "independent ceiling was attempted and is not derivable. Every one of the 200 fields "
            "holds at least 10 Gaia sources, so Gaia density does not bound it; the skipped "
            "fields are sparser (median 20 sources against 31 for the measured ones) but none "
            "falls below the floor. The match radius is 1.5 arcsec, five times the reconciler's "
            "residual, so matching is not the constraint either. What binds is the overlap "
            "between Gaia stars and Rubin detections, and that is the measurement itself -- "
            "bounding it independently would need a stellar-detection model this project has no "
            "way to validate, so the ceiling is labelled self-derived rather than dressed up as "
            "independent. Astrometry reached 0.085 arcsec p95 at the fitted epoch, better than "
            "the 0.086-0.220 arcsec the goal quoted from the pilots.",
            ceiling_basis="the delivered value itself",
        ),
        score(
            "G3", "SED consistency", 600, None, (sed.get("counts") or {}).get("sedSources"),
            "sources with an SED", "sed/consistency.json",
            "2MASS and AllWISE. GALEX is not included, so this is not the full three-survey SED "
            "the goal described.",
        ),
        score(
            "G4", "Neutral-gas scaling relation", 200, None,
            (btfr.get("counts") or {}).get("attempted"), "H I detections tested",
            "hi-gas/baryonic-tully-fisher.json",
        ),
        score(
            "G5", "Mass vs light", 200, None, (lensing.get("counts") or {}).get("pairs"),
            "lensing-light pairs", "lensing-light/correlation.json",
        ),
        score(
            "G6", "Counterpart association", 252, None,
            ((xray.get("counts") or {}).get("regionsQueried") or 0)
            + ((radio.get("counts") or {}).get("fieldsSearched") or 0),
            "fields searched for counterparts",
            "xray-counterparts/comparison.json, radio-counterparts/comparison.json",
        ),
        score(
            "G7", "Variability", 190,
            # Independently measured, not taken from the result: 5 regions hold no
            # ZTF object with 20+ epochs and 7 have no Rubin mosaic, so 188 is the
            # most any threshold could yield.
            (ztf_truth.get("ceilings") or {}).get("ifObjectFloorRelaxedToOne"),
            (ztf.get("counts") or {}).get("regionsMeasured"),
            "regions with light curves", "ztf-variability/coverage-truth.json",
            "185 measured under a 5-object, 20-epoch floor. Relaxing the object floor to one "
            "would add at most 3 regions, each resting its variability statistic on 2 to 4 light "
            "curves. 5 regions hold no usable ZTF object at all and 7 have no science-ready Rubin "
            "mosaic, so 188 is the ceiling at any threshold and the target of 190 exceeds it by 2.",
        ),
        score(
            "G8", "Morphology validation", 229,
            verifiable.get("withActualPixelsAtThePosition"), verifiable.get("verdictsDelivered"),
            "independent-resolution verdicts",
            "highres-followup/verification-truth.json",
            "The target is a footprint sum, HST 198 + JWST 12 + Euclid 14 = 224. A verdict is "
            "delivered on a candidate and the register holds 34, so 195 of the 229 were "
            "unreachable regardless of sky coverage.",
        ),
        score(
            "G9", "Anomaly scan across all operators", 2600, None,
            (register.get("comparisonsEvaluated") or {}).get("total"),
            "comparisons evaluated", "anomaly-register.json",
        ),
        score(
            "G10", "Put it on the site", 3, None, len(rendered),
            "operator result sets rendered", "/differences, /overlay/[tract]",
        ),
    ]

    counts = {
        "met": sum(1 for g in goals if g["status"] == "met"),
        "atCeiling": sum(1 for g in goals if g["status"] == "at-ceiling"),
        "belowCeiling": sum(1 for g in goals if g["status"] == "below-ceiling"),
        "targetsExceedingArchiveCeiling": sum(1 for g in goals if g["targetExceedsCeilingBy"]),
    }
    payload = {
        "schemaVersion": "layers-goal-scorecard-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Per goal: what was asked, what the archives can supply, and what was delivered. A "
            "target above the ceiling was unreachable when written, and that is reported as a "
            "quantity rather than argued in prose."
        ),
        "counts": counts,
        "policy": {
            "comparisonReadyProducts": 0,
            "astrophysicalClaimsStanding": 0,
            "note": (
                "No comparison has cleared every gate and no astrophysical claim stands. A goal "
                "reaching its ceiling is a statement about acquisition and measurement, never "
                "about a result being publishable."
            ),
        },
        "goals": goals,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    for goal in goals:
        ceiling = goal["archiveCeiling"] if goal["archiveCeiling"] is not None else "-"
        print(
            f"{goal['id']:4s} {goal['status']:13s} target {str(goal['statedTarget']):>5s}  "
            f"ceiling {str(ceiling):>5s}  delivered {str(goal['delivered']):>6s}  {goal['unit']}"
        )
    print("\n" + json.dumps(counts))


if __name__ == "__main__":
    main()
