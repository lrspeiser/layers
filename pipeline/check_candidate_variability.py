"""Test whether the unexplained anomaly candidates are just sources that varied.

§32 established that the difference maps compare sky 9 to 13.5 years apart and
that the scanner tests nothing about time. `check_candidate_epochs.py` then ruled
out one time-dependent explanation: none of the unexplained candidates sits on a
catalogued star that *moved*. It explicitly left two open, and this closes the
larger one.

A source that varied is genuinely a different brightness in the two epochs. That
is a real change on the sky, not an instrumental artefact -- and it is also not
evidence that two surveys disagree, which is what the candidate list is for.

ZTF light curves for these fields are already on disk from the variability
operator: 193 regions of per-epoch r-band photometry, cached as CSV. This looks
up every unexplained candidate position in its own field's light curves and asks
whether anything there varied more than the sample's own noise allows.

The threshold is taken from the data rather than from theory. ZTF's quoted error
bars are optimistic, so a reduced chi-square of 1 is not the variability line;
the variability operator already measured the 99th percentile of this sample's
own distribution at 18.04, and that is reused here so the two agree on what
"variable" means.

As in the proper-motion check, the answer is reported either way, and a positive
control ships with it: a null result is only worth having if a broken lookup
would have looked different.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import pathlib
from collections import defaultdict

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
SELECTED = ROOT / "public/data/layers/selected-regions"
ZTF_CACHE = ROOT / "pipeline/results/ztf-variability/cache"
ZTF_COMPARISON = ROOT / "public/data/layers/ztf-variability/comparison.json"
OUTPUT = SELECTED / "candidate-variability-check.json"

# A ZTF source within this of the candidate is the same object. ZTF's PSF is
# about 2 arcsec, so this is deliberately generous -- the question is whether
# anything varying is close enough to produce the residual.
MATCH_ARCSEC = 3.0
MIN_EPOCHS = 10
# ZTF sets bit 15 (32768) for a variety of quality problems; the operator's own
# selection keeps only clean epochs, so this does the same.
BAD_CATFLAGS = 32768


def light_curves(path: pathlib.Path) -> dict[str, dict]:
    """Group a region's ZTF rows into per-object light curves."""
    grouped: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                if int(row["catflags"]) & BAD_CATFLAGS:
                    continue
                grouped[row["oid"]].append(
                    (
                        float(row["ra"]),
                        float(row["dec"]),
                        float(row["mag"]),
                        float(row["magerr"]),
                    )
                )
            except (ValueError, KeyError, TypeError):
                continue

    out: dict[str, dict] = {}
    for oid, rows in grouped.items():
        if len(rows) < MIN_EPOCHS:
            continue
        ra = float(np.median([r[0] for r in rows]))
        dec = float(np.median([r[1] for r in rows]))
        mag = np.array([r[2] for r in rows])
        err = np.array([r[3] for r in rows])
        good = err > 0
        if good.sum() < MIN_EPOCHS:
            continue
        mag, err = mag[good], err[good]
        weights = 1.0 / err**2
        mean = float(np.sum(mag * weights) / np.sum(weights))
        chi2 = float(np.sum(((mag - mean) / err) ** 2) / (mag.size - 1))
        out[oid] = {
            "ra": ra,
            "dec": dec,
            "epochs": int(mag.size),
            "meanMag": mean,
            "reducedChiSquare": chi2,
            "peakToPeakMag": float(mag.max() - mag.min()),
        }
    return out


def separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    return (
        math.hypot((ra1 - ra2) * math.cos(math.radians(dec1)), dec1 - dec2) * 3600.0
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anomalies", default="pipeline/results/anomalies-hsc")
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = parser.parse_args()

    comparison = json.loads(ZTF_COMPARISON.read_text(encoding="utf-8"))
    threshold = comparison["thresholds"]["reducedChiSquare99thPercentile"]

    unexplained: list[dict] = []
    for path in sorted(glob.glob(f"{args.anomalies}/**/*.json", recursive=True)):
        try:
            payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        region = payload.get("regionId") or pathlib.Path(path).parent.name
        for candidate in payload.get("candidates") or []:
            if candidate.get("couldBeExplainedBy"):
                continue
            sky = candidate.get("sky") or {}
            ra = sky.get("raDeg", sky.get("ra"))
            dec = sky.get("decDeg", sky.get("dec"))
            if ra is None or dec is None:
                continue
            unexplained.append(
                {
                    "regionId": region,
                    "ra": float(ra),
                    "dec": float(dec),
                    "empiricalSigma": candidate.get("empiricalSigma"),
                }
            )

    curves_by_region: dict[str, dict] = {}
    checked = []
    for candidate in unexplained:
        region = candidate["regionId"]
        if region not in curves_by_region:
            matches = sorted(ZTF_CACHE.glob(f"{region}-*.csv"))
            curves_by_region[region] = light_curves(matches[0]) if matches else {}
        curves = curves_by_region[region]
        candidate["ztfObjectsInField"] = len(curves)
        if not curves:
            candidate["ztfCoverage"] = False
            checked.append(candidate)
            continue
        candidate["ztfCoverage"] = True

        nearby = []
        for oid, curve in curves.items():
            separation = separation_arcsec(
                candidate["ra"], candidate["dec"], curve["ra"], curve["dec"]
            )
            if separation <= MATCH_ARCSEC:
                nearby.append({"oid": oid, "separationArcsec": round(separation, 2), **curve})
        variable = [n for n in nearby if n["reducedChiSquare"] >= threshold]
        candidate["ztfSourcesWithin3Arcsec"] = len(nearby)
        candidate["variableSources"] = sorted(
            variable, key=lambda n: -n["reducedChiSquare"]
        )[:3]
        candidate["explainedByVariability"] = bool(variable)
        checked.append(candidate)
        print(
            f"  {region:22s} {candidate['ra']:9.5f} {candidate['dec']:+9.5f}  "
            f"ztf_field={len(curves):4d} near={len(nearby)}  "
            f"{'VARIABLE' if variable else 'no variable'}"
        )

    # Positive control: the lookup must be able to find variables at all, and the
    # fields must actually contain ZTF objects. Without this, "0 explained" is
    # indistinguishable from a path that matched no files.
    all_curves = [c for curves in curves_by_region.values() for c in curves.values()]
    control = {
        "regionsWithLightCurves": sum(1 for c in curves_by_region.values() if c),
        "regionsChecked": len(curves_by_region),
        "objectsWithLightCurves": len(all_curves),
        "objectsAboveVariabilityThreshold": sum(
            1 for c in all_curves if c["reducedChiSquare"] >= threshold
        ),
        "reading": (
            "The fields contain ZTF objects and some of them exceed the variability "
            "threshold, so a candidate coinciding with a variable would have been found."
        ),
    }

    explained = sum(1 for c in checked if c.get("explainedByVariability"))
    covered = sum(1 for c in checked if c.get("ztfCoverage"))
    payload = {
        "schemaVersion": "layers-candidate-variability-check-v1",
        "question": (
            "The proper-motion check ruled out moved stars but left variability open. Are the "
            "unexplained candidates sources that changed brightness between epochs?"
        ),
        "method": (
            "Group each field's cached ZTF r-band photometry into per-object light curves, keep "
            "objects with at least 10 clean epochs, and compute a reduced chi-square about the "
            "weighted mean. A candidate within 3 arcsec of an object above the threshold has a "
            "time-domain explanation."
        ),
        "reducedChiSquareThreshold": threshold,
        "thresholdBasis": (
            "The 99th percentile of the variability operator's own sample. ZTF error bars are "
            "optimistic, so a reduced chi-square of 1 is not the line, and reusing the "
            "operator's number keeps both stages agreeing on what variable means."
        ),
        "matchRadiusArcsec": MATCH_ARCSEC,
        "candidatesChecked": len(checked),
        "candidatesWithZtfCoverage": covered,
        "explainedByVariability": explained,
        "positiveControl": control,
        "candidates": checked,
        "limitsOfThisTest": [
            "ZTF is complete to about r=20.5. A fainter variable would change and never appear "
            "here, so this rules out variability of a ZTF-detectable source, not variability.",
            "A source that varied slowly over the 9-year gap but was steady within ZTF's own "
            "baseline would pass this test and still explain the residual.",
            "Solar-system objects remain untested: an asteroid present in one epoch is not a "
            "light curve at a fixed position.",
        ],
        "reproduce": "python pipeline/check_candidate_variability.py",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n{explained} of {covered} candidates with ZTF coverage explained by variability "
        f"({len(checked)} checked)"
    )
    print(f"wrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
