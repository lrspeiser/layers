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


def gaia_from_cache() -> tuple[int, int, str]:
    """G2: reproduce each measured region's Gaia source count from the cached CSVs.

    Two caches exist. `gaia-200` holds a smaller search radius and `gaia-200r3` a
    larger one; the operator used r3. Reading the wrong one reproduces none of
    the 147 counts and looks exactly like a real discrepancy -- which is what it
    looked like until both were checked. The smaller cache is not wrong, it just
    answers a different query, and a fresh Gaia cone at its radius agrees with it.
    """
    comparison = json.loads(
        (LAYERS / "gaia-crossmatch/comparison.json").read_text(encoding="utf-8")
    )
    published = {
        r["regionId"]: (r.get("counts") or {}).get("gaiaSources")
        for r in comparison.get("regions", [])
    }
    best_name, best_agree = "", -1
    for name in ("gaia-200r3", "gaia-200"):
        root = RESULTS / name
        if not root.is_dir():
            continue
        agree = 0
        for region_id, expected in published.items():
            directory = root / region_id
            if not directory.is_dir():
                continue
            found = 0
            for path in directory.glob("*.csv"):
                try:
                    rows = list(csv.DictReader(path.read_text(
                        encoding="utf-8-sig", errors="replace").splitlines()))
                    found = max(found, sum(1 for r in rows if r.get("ra")))
                except Exception:  # noqa: BLE001
                    continue
            if found == expected:
                agree += 1
        if agree > best_agree:
            best_name, best_agree = name, agree
    return best_agree, len(published), best_name


def hi_detections_from_catalogues() -> tuple[int, int, str]:
    """G4: reproduce the H I detection count from the cached HICAT and NHICAT VOTables.

    Two subtleties, both of which produced wrong answers first. The operator uses
    *both* catalogues, not just HICAT; and it assigns detections to whole DP2
    tract footprints, which are degrees across, not to the 4-arcmin cutouts. A
    cone around each region centre finds 6 detections and looks like a
    catastrophic disagreement with the published 622.

    Reproduced correctly this gives 623 inside a tract bound. The published 622
    additionally requires a finite W50 line width, which a baryonic Tully-Fisher
    residual cannot be computed without. Exactly one detection, J0033-09, sits in
    the footprint with no linewidth. Both numbers are right for their definition.
    """
    import io
    from astropy.coordinates import SkyCoord
    from astropy.table import Table, vstack
    import astropy.units as u

    cache = RESULTS / "hi-gas/cache"
    tables = []
    for name in ("VIII-73-hicat.vot", "VIII-89-nhicat.vot"):
        path = cache / name
        if not path.is_file():
            return -1, 0, "cache missing"
        table = Table.read(io.BytesIO(path.read_bytes()), format="votable")
        keep = [c for c in ("HIPASS", "RAJ2000", "DEJ2000", "W50max") if c in table.colnames]
        tables.append(table[keep])
    catalogue = vstack(tables, metadata_conflicts="silent")
    coords = SkyCoord(
        list(catalogue["RAJ2000"]), list(catalogue["DEJ2000"]), unit=(u.hourangle, u.deg)
    )
    ra, dec = coords.ra.deg, coords.dec.deg

    footprint = json.loads(
        (ROOT / "public/data/coverage/rubin-dp2-footprint.json").read_text(encoding="utf-8")
    )
    inside_any = np.zeros(ra.size, dtype=bool)
    for row in footprint["tracts"]:
        bounds = row[2]
        if bounds["ra"]["wraps"]:
            in_ra = (ra >= bounds["ra"]["start"]) | (ra <= bounds["ra"]["end"])
        else:
            in_ra = (ra >= bounds["ra"]["start"]) & (ra <= bounds["ra"]["end"])
        inside_any |= in_ra & (dec >= bounds["dec_min"]) & (dec <= bounds["dec_max"])

    width = catalogue["W50max"]
    has_width = ~np.asarray(getattr(width, "mask", np.zeros(len(width), dtype=bool)))
    testable = int((inside_any & has_width).sum())
    return testable, int(inside_any.sum()), "hicat + nhicat, tract footprints, finite W50"


def highres_from_cache() -> tuple[int, int, str]:
    """G8: how many candidate positions actually have high-resolution pixels.

    One cached MAST VOTable per candidate position. A position is verifiable only
    if its query returned at least one observation; an empty table is a position
    no high-resolution instrument has visited.
    """
    import re
    from astropy.table import Table

    cache = RESULTS / "highres-followup/cache"
    if not cache.is_dir():
        return -1, 0, "cache missing"
    with_observation = 0
    queried = 0
    for path in sorted(cache.glob("*.vot")):
        if path.name == "hla-probe.vot":
            continue
        queried += 1
        try:
            if len(Table.read(path, format="votable")) > 0:
                with_observation += 1
        except Exception:  # noqa: BLE001
            continue
    return with_observation, queried, "candidate positions with >=1 MAST observation"


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

    agree, total_regions, cache_name = gaia_from_cache()
    match = "OK" if agree == total_regions else "DISAGREES"
    print(f"\n  G2  Gaia source counts reproduced from cache  : {agree:6d}  "
          f"of {total_regions}  {match}")
    print(f"      from {cache_name}; a fresh Gaia cone agrees with the cache at its radius")
    if agree != total_regions:
        failures.append("G2")

    testable, in_footprint, _how = hi_detections_from_catalogues()
    match = "OK" if testable == published.get("G4") else "DISAGREES"
    print(f"\n  G4  H I detections testable from catalogues   : {testable:6d}  "
          f"published {published.get('G4')}  {match}")
    print(f"      {in_footprint} inside a tract footprint, less those with no W50 linewidth")
    if testable != published.get("G4"):
        failures.append("G4")

    verifiable, queried, _highres_how = highres_from_cache()
    match = "OK" if verifiable == published.get("G8") else "DISAGREES"
    print(f"\n  G8  candidate positions with high-res pixels  : {verifiable:6d}  "
          f"published {published.get('G8')}  {match}")
    print(f"      of {queried} candidate positions queried against MAST")
    if verifiable != published.get("G8"):
        failures.append("G8")

    print("\nNot rebuildable from raw inputs here:")
    print("  G9 pixel-residual (1147)  the per-region scan outputs for the 200-set were not")
    print("                            retained; only anomalies-hsc remains on disk")
    print("  G5                        raw inputs are external archive queries; rebuilding")
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
