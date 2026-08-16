#!/usr/bin/env python3
"""Publish where this project actually has pixels, as a MOC.

"Where do you have data" is the question this project has answered the hard way
seven separate times, and got wrong every time it trusted a declared footprint:

* NGC 0100 overlapped, and its pixels were masked.
* HIPASS is all-sky, and returned 0 detections in the cutouts.
* eROSITA covered 55 tracts and yielded 8 detections.
* MAST listed 25 HST observations, and the nearest frame was 24.1 arcsec outside.
* VLASS overlapped 197 regions and produced 39 sources.
* A Euclid polygon contained a position whose tile was all zeros.
* The optical goal's ~727 target was a sum of footprint overlaps; 542 have pixels.

A Multi-Order Coverage map is the machine-readable form of the right answer.
Publishing one lets someone else skip the mistake instead of repeating the
measurements, which is the most useful thing this project can hand over.

Two MOCs are written per survey, and the difference between them is the point:

* **claimed** -- what the region planner's footprint overlap asserted;
* **served** -- the regions where an archive actually returned validated pixels.

Both are IVOA MOC FITS, loadable in Aladin and by any MOC-aware client, plus a
small JSON summary of the areas so the gap is legible without a viewer.
"""

from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

warnings.filterwarnings("ignore")

import astropy.units as u
from astropy.coordinates import SkyCoord
from mocpy import MOC

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ROOT / "public/data/layers"
DEFAULT_REGIONS = ROOT / "pipeline/results/coverage/selected-regions-200.json"
DEFAULT_OUTPUT = ROOT / "public/data/moc"
DEFAULT_SUMMARY = LAYERS / "selected-regions/coverage-moc.json"

# The cutouts are 3.41 arcmin across, so order 14 (about 13 arcsec cells) traces
# a region boundary far finer than the region itself.
MOC_ORDER = 14
CUTOUT_RADIUS_DEG = 3.41 / 60.0 / 2.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def moc_from_regions(centres: list[tuple[float, float]]) -> MOC | None:
    if not centres:
        return None
    ra = np.array([c[0] for c in centres]) * u.deg
    dec = np.array([c[1] for c in centres]) * u.deg
    radius = np.full(len(centres), CUTOUT_RADIUS_DEG) * u.deg
    return MOC.from_cones(lon=ra, lat=dec, radius=radius, max_depth=MOC_ORDER, union_strategy="small_cones")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", type=Path, default=DEFAULT_REGIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()

    regions = json.loads(args.regions.read_text(encoding="utf-8"))["regions"]
    centre = {r["id"]: (float(r["center"][0]), float(r["center"][1])) for r in regions}

    # What the planner asserted, from footprint overlap.
    claimed: dict[str, list[tuple[float, float]]] = {}
    for region in regions:
        for survey in region.get("confirmedSurveyIds", []):
            claimed.setdefault(survey, []).append(centre[region["id"]])

    # What an archive actually served and validated. Each entry names the
    # manifest that proves it, so a claim of coverage is traceable to a file.
    served_sources: dict[str, tuple[Path, str]] = {
        "legacy-surveys-dr10": (LAYERS / "selected-regions/rubin-reference-reconciliation-200.json", "reconciled"),
        "des-dr2": (LAYERS / "selected-regions/rubin-des-reconciliation.json", "reconciled"),
        "panstarrs-dr2": (LAYERS / "selected-regions/rubin-ps1-reconciliation.json", "reconciled"),
        "vlass": (LAYERS / "selected-regions/vlass.json", "scienceReady"),
        "gaia-dr3": (LAYERS / "gaia-crossmatch/comparison.json", "regionsList"),
        # ZTF is deliberately absent: its manifest records counts and the regions
        # it skipped, but never enumerates the ones it measured, so there is no
        # list to turn into coverage. Wiring it anyway produced "served 0", which
        # would have published the claim that ZTF covers none of this sky.
    }
    served: dict[str, list[tuple[float, float]]] = {}
    provenance: dict[str, str] = {}
    broken_wiring: list[dict[str, str]] = []
    for survey, (path, mode) in served_sources.items():
        payload = load(path)
        for record in payload.get("regions", []):
            if mode == "reconciled":
                ok = record.get("status") in {"matched", "qa-failed"}
            elif mode == "regionsList":
                # These operators list only what they successfully measured, so
                # presence in the list is the evidence.
                ok = True
            else:
                ok = bool(record.get("scienceReady"))
            if ok and record.get("regionId") in centre:
                served.setdefault(survey, []).append(centre[record["regionId"]])
        if survey in served:
            provenance[survey] = display_path(path)
        else:
            # A wired manifest that yields nothing means the wiring is wrong, not
            # that the survey covers nothing. Saying the second out loud would be
            # the exact error this whole file exists to stop.
            broken_wiring.append({"surveyId": survey, "manifest": display_path(path)})

    args.output.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for survey in sorted(set(claimed) | set(served)):
        record: dict[str, Any] = {"surveyId": survey}
        for label, table in (("claimed", claimed), ("served", served)):
            moc = moc_from_regions(table.get(survey, []))
            if moc is None:
                record[f"{label}Regions"] = 0
                record[f"{label}AreaSqDeg"] = 0.0
                continue
            name = f"{survey}-{label}.fits"
            moc.save(args.output / name, format="fits", overwrite=True)
            record[f"{label}Regions"] = len(table.get(survey, []))
            record[f"{label}AreaSqDeg"] = round(float(moc.sky_fraction * 41252.96), 4)
            record[f"{label}Moc"] = f"/data/moc/{name}"
        # A survey with no wired manifest has not been measured here, and must
        # not be reported as serving nothing: "gap 200" for Gaia would say the
        # opposite of the truth, since Gaia was measured by a catalogue operator
        # rather than a pixel one. Absence of evidence is recorded as absence of
        # evidence.
        if survey in served_sources and survey in served:
            record["servedMeasured"] = True
            record["servedEvidence"] = provenance.get(survey)
            if record.get("claimedRegions"):
                record["regionsClaimedWithoutPixels"] = (
                    record["claimedRegions"] - record.get("servedRegions", 0)
                )
        else:
            record["servedMeasured"] = False
            record["servedRegions"] = None
            record["servedAreaSqDeg"] = None
            record.pop("servedMoc", None)
            record["note"] = (
                "No served-coverage manifest is wired for this survey, so this row states what "
                "was claimed and makes no claim about what was served."
            )
        entries.append(record)

    measured = [e for e in entries if e.get("servedMeasured")]
    payload = {
        "schemaVersion": "layers-coverage-moc-v1",
        "generatedAt": utc_now(),
        "purpose": (
            "Where this project actually has pixels, as an IVOA MOC, next to what footprint "
            "overlap claimed. The gap between the two is the error this project has measured "
            "seven separate times, published so nobody has to measure it again."
        ),
        "mocOrder": MOC_ORDER,
        "cellArcsec": round(float(np.degrees(np.sqrt(np.pi / (3 * 4**MOC_ORDER))) * 3600), 1),
        "regionRadiusDeg": CUTOUT_RADIUS_DEG,
        "howToUse": (
            "Load a .fits MOC in Aladin, or read it with mocpy: "
            "MOC.load('served.fits', format='fits'). Intersect it with your own MOC to find "
            "where both projects have data before fetching anything."
        ),
        "claimedVsServed": (
            "claimed comes from the region planner's confirmedSurveyIds, which is footprint "
            "overlap. served comes from a manifest recording that an archive returned validated "
            "pixels. Only the second is evidence."
        ),
        "counts": {
            "surveys": len(entries),
            "surveysWithServedCoverageMeasured": len(measured),
            "surveysClaimedOnly": len(entries) - len(measured),
            "wiredButYieldedNoRegions": len(broken_wiring),
            "regionsClaimedWithoutPixels": sum(
                e.get("regionsClaimedWithoutPixels", 0) for e in measured
            ),
        },
        "wiredButYieldedNoRegions": broken_wiring,
        "surveys": entries,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"{'survey':24s} {'claimed':>8s} {'served':>7s} {'gap':>5s}  {'claimed deg2':>12s} {'served deg2':>11s}")
    for entry in entries:
        if not entry["servedMeasured"]:
            print(f"{entry['surveyId']:24s} {entry.get('claimedRegions', 0):8d} "
                  f"{'not measured':>7s} {'':>5s}  {entry.get('claimedAreaSqDeg', 0):12.3f}")
            continue
        print(
            f"{entry['surveyId']:24s} {entry.get('claimedRegions', 0):8d} "
            f"{entry.get('servedRegions', 0):7d} {entry.get('regionsClaimedWithoutPixels', 0):5d}  "
            f"{entry.get('claimedAreaSqDeg', 0):12.3f} {entry.get('servedAreaSqDeg', 0):11.3f}"
        )
    print(f"\nwrote {display_path(args.summary)} and {len(list(args.output.glob('*.fits')))} MOC files")


if __name__ == "__main__":
    main()
