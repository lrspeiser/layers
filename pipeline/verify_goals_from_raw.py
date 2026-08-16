"""Rebuild goal figures from raw inputs, not from the manifests that report them.

`verify_scorecard_counts.py` checks each goal's published figure against its own
evidence file. That catches transcription and arithmetic errors -- it caught G0's
inferred 167 -- but it cannot catch a number that was wrong where it was
produced, because the evidence file and the scorecard entry come from the same
stage and would be wrong together.

This goes to the inputs instead: the FITS products on disk, the cached
photometry CSVs. It is slower, it covers less, and it is the only form of check
that can disagree with the pipeline rather than with itself.

Coverage is deliberately partial and reported as such. An operator whose raw
inputs are an external archive query cannot be rebuilt without re-querying, and
one whose intermediates were not retained cannot be rebuilt at all. Saying which
is which is the point: "not checkable" and "checked and correct" are different
claims, and collapsing them is how §34's wrong number survived.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
import warnings
from collections import Counter

import numpy as np
from astropy.io import fits

warnings.filterwarnings("ignore")

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESULTS = ROOT / "pipeline/results"
LAYERS = ROOT / "public/data/layers"

# The variability operator's published acceptance rules. Reproduced rather than
# imported so this stays an independent check: importing the builder would make
# the two agree by construction.
MIN_EPOCHS_PER_OBJECT = 20
MIN_OBJECTS_PER_REGION = 5

RECONCILED = {
    "legacy": "reconciled-regions-200",
    "des": "reconciled-regions-des",
    "ps1": "reconciled-regions-ps1",
    "hsc": "reconciled-regions-hsc",
}


def reconciled_products() -> tuple[int, dict[str, int], list[str]]:
    """G1: count reconciled products that actually open and hold pixels."""
    per, problems = {}, []
    for label, directory in RECONCILED.items():
        root = RESULTS / directory
        if not root.is_dir():
            problems.append(f"{label}: directory absent")
            per[label] = 0
            continue
        usable = 0
        for region in sorted(p for p in root.iterdir() if p.is_dir()):
            path = region / "rubin-reference-matched.fits"
            if not path.is_file():
                continue
            try:
                with fits.open(path, memmap=True) as handle:
                    names = {hdu.name for hdu in handle}
                    if not {"RUBIN", "REFERENCE"} <= names:
                        continue
                    # Sample rather than read 628 full frames.
                    sample = np.asarray(handle["RUBIN"].section[::16, ::16], dtype=float)
                    if not np.isfinite(sample).any():
                        continue
                usable += 1
            except Exception as error:  # noqa: BLE001
                problems.append(f"{region.name}: {type(error).__name__}")
        per[label] = usable
    return sum(per.values()), per, problems


def ztf_from_photometry() -> tuple[dict[str, int], int]:
    """G7 and G9's variability term, rebuilt from the cached light curves."""
    cache = RESULTS / "ztf-variability/cache"
    counts = {"attempted": 0, "zeroUsable": 0, "underObjectFloor": 0, "measured": 0}
    objects_total = 0
    if not cache.is_dir():
        return counts, 0
    for path in sorted(cache.glob("*.csv")):
        counts["attempted"] += 1
        epochs: Counter = Counter()
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    # The operator rejects any epoch with a nonzero catflags, not
                    # only one bit. Matching a looser rule here reproduced 186
                    # instead of 185 and looked like a real disagreement.
                    if int(row.get("catflags") or 0) != 0:
                        continue
                    magnitude, error = float(row["mag"]), float(row["magerr"])
                    if not (np.isfinite(magnitude) and np.isfinite(error)) or error <= 0:
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
                epochs[row.get("oid")] += 1
        objects = [n for n in epochs.values() if n >= MIN_EPOCHS_PER_OBJECT]
        if not objects:
            counts["zeroUsable"] += 1
        elif len(objects) >= MIN_OBJECTS_PER_REGION:
            counts["measured"] += 1
            objects_total += len(objects)
        else:
            counts["underObjectFloor"] += 1
    return counts, objects_total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    card = json.loads((LAYERS / "goal-scorecard.json").read_text(encoding="utf-8"))
    published = {g["id"]: g.get("delivered") for g in card.get("goals", [])}
    register = json.loads((LAYERS / "anomaly-register.json").read_text(encoding="utf-8"))
    evaluated = register.get("comparisonsEvaluated") or {}

    failures = []
    print("Rebuilt from raw inputs:\n")

    total, per, problems = reconciled_products()
    detail = " + ".join(f"{k} {v}" for k, v in per.items())
    match = "OK" if total == published.get("G1") else "DISAGREES"
    print(f"  G1  reconciled products that open with pixels : {total:6d}  "
          f"published {published.get('G1')}  {match}")
    print(f"      {detail}")
    if problems:
        print(f"      problems: {problems[:4]}")
    if total != published.get("G1"):
        failures.append("G1")

    counts, objects = ztf_from_photometry()
    match = "OK" if counts["measured"] == published.get("G7") else "DISAGREES"
    print(f"\n  G7  regions measured from raw photometry      : {counts['measured']:6d}  "
          f"published {published.get('G7')}  {match}")
    print(f"      attempted {counts['attempted']}, zero usable {counts['zeroUsable']}, "
          f"under object floor {counts['underObjectFloor']}")
    if counts["measured"] != published.get("G7"):
        failures.append("G7")

    stated = evaluated.get("variability")
    match = "OK" if objects == stated else "DISAGREES"
    share = objects / evaluated["total"] if evaluated.get("total") else 0
    print(f"\n  G9  variability term from raw photometry      : {objects:6d}  "
          f"register {stated}  {match}")
    print(f"      that is {share:.0%} of the {evaluated.get('total')} comparisons G9 claims")
    if objects != stated:
        failures.append("G9-variability")

    print("\nNot rebuildable from raw inputs here:")
    print("  G9 pixel-residual (1147)  the per-region scan outputs for the 200-set were not")
    print("                            retained; only anomalies-hsc remains on disk")
    print("  G2 G3 G4 G5 G6 G8         raw inputs are external archive queries; rebuilding")
    print("                            means re-querying, not re-reading")
    print("  G10                       evidence is rendered pages, not a manifest")
    print("\n  These are unverified by this script, which is weaker than correct.")

    if failures:
        print(f"\nDISAGREEMENTS: {failures}")
        if args.check:
            sys.exit(1)
    else:
        print("\nevery figure rebuildable from raw inputs matches")


if __name__ == "__main__":
    main()
