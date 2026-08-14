#!/usr/bin/env python3
"""Cross-match Rubin sources against Gaia DR3 and fit the effective Rubin epoch.

This is the cheapest comparison operator in the plan: catalogue against image,
with no pixel alignment, no PSF matching, and no bandpass transfer. It is also
the one that repays the registration blocker, because the residual it measures
*is* the astrometric accuracy of the Rubin-to-reference alignment.

**The epoch problem, and why it is fitted rather than assumed.** Propagating Gaia
positions to the Rubin epoch needs that epoch, and it is not available: the SODA
cutouts carry no date keyword, and DP2 deep-coadd SIA records publish ``t_min``
and ``t_max`` as NaN. Assuming a release date would bury an unverified constant
inside every astrometric result.

Instead the epoch is solved for. Each Gaia star's proper motion is known, so the
epoch difference is a single free parameter, and the value that minimises the
matched-source residual is the effective epoch of the coadd. That turns missing
metadata into a measurement, and it is falsifiable twice over: the fitted epoch
must be consistent from field to field, and it must land inside the plausible
range for the release. Both checks are reported.

Sources present in one catalogue and absent from the other are kept, not
discarded. A Rubin source with no Gaia counterpart is usually a galaxy, which is
uninteresting, but it is also how a transient or a high-proper-motion object
would appear. A Gaia source with no Rubin counterpart is either masked, variable,
or moved further than the search radius allows.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_layer_registration import centroid_sources, robust_sigma

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUBIN = ROOT / "pipeline/results/rubin-pixels-200/manifest.json"
DEFAULT_GAIA = ROOT / "pipeline/results/gaia-200"
DEFAULT_OUTPUT = ROOT / "pipeline/results/gaia-crossmatch"
DEFAULT_PUBLIC = ROOT / "public/data/layers/gaia-crossmatch/comparison.json"

GAIA_REFERENCE_EPOCH_JYEAR = 2016.0
# DP2 draws on commissioning and first-year data. The fit is searched across a
# generous bracket so the answer is driven by the astrometry, not by the prior.
EPOCH_SEARCH_JYEAR = np.arange(2023.0, 2027.01, 0.25)
MATCH_RADIUS_ARCSEC = 1.5
MIN_MATCHES = 10
MAS_PER_YEAR_TO_DEG = 1.0 / (3600.0 * 1000.0)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_gaia(path: Path) -> list[dict[str, float]]:
    rows = []
    for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig", errors="replace"))):
        try:
            ra = float(row["ra"])
            dec = float(row["dec"])
        except (KeyError, TypeError, ValueError):
            continue

        def maybe(key: str) -> float:
            try:
                value = float(row.get(key, "") or "nan")
            except ValueError:
                return float("nan")
            return value

        rows.append({
            "sourceId": row.get("source_id", ""),
            "ra": ra,
            "dec": dec,
            "pmra": maybe("pmra"),
            "pmdec": maybe("pmdec"),
            "refEpoch": maybe("ref_epoch") if math.isfinite(maybe("ref_epoch")) else GAIA_REFERENCE_EPOCH_JYEAR,
            "gMag": maybe("phot_g_mean_mag"),
            "ruwe": maybe("ruwe"),
        })
    return rows


def propagate(gaia: list[dict[str, float]], epoch: float) -> np.ndarray:
    positions = np.empty((len(gaia), 2), dtype=float)
    for index, star in enumerate(gaia):
        dt = epoch - star["refEpoch"]
        pmra = star["pmra"] if math.isfinite(star["pmra"]) else 0.0
        pmdec = star["pmdec"] if math.isfinite(star["pmdec"]) else 0.0
        dec = star["dec"] + pmdec * dt * MAS_PER_YEAR_TO_DEG
        # pmra is already the great-circle rate (pmra*), so the cos(dec) factor
        # converts it to a coordinate increment.
        ra = star["ra"] + pmra * dt * MAS_PER_YEAR_TO_DEG / max(math.cos(math.radians(dec)), 1e-6)
        positions[index] = (ra, dec)
    return positions


def match_residual(
    rubin_sky: np.ndarray, gaia_positions: np.ndarray, radius_arcsec: float
) -> tuple[int, float, float, np.ndarray]:
    if not len(rubin_sky) or not len(gaia_positions):
        return 0, float("nan"), float("nan"), np.array([])
    scale = math.cos(math.radians(float(np.median(gaia_positions[:, 1]))))
    tree = cKDTree(np.column_stack((gaia_positions[:, 0] * scale, gaia_positions[:, 1])))
    query = np.column_stack((rubin_sky[:, 0] * scale, rubin_sky[:, 1]))
    limit = radius_arcsec / 3600.0
    distance, index = tree.query(query, distance_upper_bound=limit)
    matched = np.isfinite(distance)
    separations = distance[matched] * 3600.0
    if separations.size == 0:
        return 0, float("nan"), float("nan"), index
    return int(separations.size), float(np.median(separations)), float(np.percentile(separations, 95)), index


def measure_region(record: dict[str, Any], gaia_dir: Path) -> dict[str, Any]:
    region_id = record["regionId"]
    gaia_path = gaia_dir / region_id / "gaia-dr3.csv"
    if not gaia_path.is_file():
        raise ValueError("no Gaia catalogue cached for this region")
    gaia = load_gaia(gaia_path)
    if len(gaia) < MIN_MATCHES:
        raise ValueError(f"only {len(gaia)} Gaia sources in this field")

    mosaic = ROOT / record["mosaic"]["path"]
    with fits.open(mosaic, memmap=False) as hdus:
        image = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
        variance = np.asarray(hdus["VARIANCE"].data, dtype=np.float64)
        mask = np.asarray(hdus["MASK"].data)
        wcs = WCS(hdus["IMAGE"].header).celestial
    pixel_scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
    valid = np.isfinite(image) & np.isfinite(variance) & (variance > 0) & ((mask & ((1 << 0) | (1 << 3))) == 0)

    sources = centroid_sources(image, valid, 0.0, pixel_scale)
    if len(sources) < MIN_MATCHES:
        raise ValueError(f"only {len(sources)} Rubin sources detected")
    sky = np.array([wcs.pixel_to_world_values(item["x"], item["y"]) for item in sources], dtype=float)

    # Fit the epoch: the value that minimises the matched-source residual.
    trials = []
    for epoch in EPOCH_SEARCH_JYEAR:
        positions = propagate(gaia, float(epoch))
        count, median, p95, _ = match_residual(sky, positions, MATCH_RADIUS_ARCSEC)
        if count >= MIN_MATCHES:
            trials.append({"epoch": float(epoch), "matched": count, "medianArcsec": median, "p95Arcsec": p95})
    if not trials:
        raise ValueError("no epoch produced enough matches")
    best = min(trials, key=lambda item: item["medianArcsec"])

    # The no-propagation case is the honest baseline: it is what the current
    # pipeline effectively assumes by ignoring proper motion entirely.
    static_positions = np.array([[star["ra"], star["dec"]] for star in gaia], dtype=float)
    static_count, static_median, static_p95, _ = match_residual(sky, static_positions, MATCH_RADIUS_ARCSEC)

    best_positions = propagate(gaia, best["epoch"])
    matched_count, _, _, index = match_residual(sky, best_positions, MATCH_RADIUS_ARCSEC)
    matched_gaia = {int(value) for value in index if value < len(gaia)}
    rubin_unmatched = int(len(sources) - matched_count)
    gaia_unmatched = int(len(gaia) - len(matched_gaia))

    return {
        "regionId": region_id,
        "tract": record["tract"],
        "band": record["band"],
        "pixelScaleArcsec": round(pixel_scale, 5),
        "counts": {
            "gaiaSources": len(gaia),
            "rubinSources": len(sources),
            "matched": matched_count,
            "rubinWithoutGaia": rubin_unmatched,
            "gaiaWithoutRubin": gaia_unmatched,
        },
        "epochFit": {
            "fittedJyear": best["epoch"],
            "searchRange": [float(EPOCH_SEARCH_JYEAR[0]), float(EPOCH_SEARCH_JYEAR[-1])],
            "searchStepYears": float(EPOCH_SEARCH_JYEAR[1] - EPOCH_SEARCH_JYEAR[0]),
            "atFittedEpoch": {"medianArcsec": best["medianArcsec"], "p95Arcsec": best["p95Arcsec"], "matched": best["matched"]},
            "withoutPropagation": {"medianArcsec": static_median, "p95Arcsec": static_p95, "matched": static_count},
            "improvementArcsec": (static_median - best["medianArcsec"]) if math.isfinite(static_median) else None,
            "method": (
                "Rubin DP2 deep coadds publish no usable observation epoch, so it is solved for as the "
                "single free parameter that minimises the Gaia-matched astrometric residual."
            ),
        },
        "interpretation": (
            "Rubin sources without a Gaia counterpart are mostly galaxies, which Gaia does not catalogue; "
            "this count is not by itself a transient search. Gaia sources without a Rubin counterpart are "
            "masked, variable, or moved further than the match radius."
        ),
        "trials": trials,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubin-manifest", type=Path, default=DEFAULT_RUBIN)
    parser.add_argument("--gaia", type=Path, default=DEFAULT_GAIA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--only-region", action="append", default=[])
    args = parser.parse_args()

    payload = json.loads(args.rubin_manifest.read_text(encoding="utf-8"))
    only = {value.strip() for value in args.only_region if value.strip()}
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for record in payload["regions"]:
        if only and record["regionId"] not in only:
            continue
        if not record.get("validation", {}).get("scienceReady") or not record.get("mosaic"):
            skipped.append({"regionId": record["regionId"], "reason": "no science-ready Rubin mosaic"})
            continue
        try:
            result = measure_region(record, args.gaia)
        except Exception as error:  # noqa: BLE001 - report, never silently drop
            skipped.append({"regionId": record["regionId"], "reason": f"{type(error).__name__}: {error}"})
            print(f"[skipped] {record['regionId']}: {type(error).__name__}: {error}", flush=True)
            continue
        records.append(result)
        fit = result["epochFit"]
        print(
            f"[matched] {result['regionId']} n={result['counts']['matched']} "
            f"epoch={fit['fittedJyear']:.2f} median={fit['atFittedEpoch']['medianArcsec']:.3f}\" "
            f"(static {fit['withoutPropagation']['medianArcsec']:.3f}\")",
            flush=True,
        )

    epochs = np.array([item["epochFit"]["fittedJyear"] for item in records], dtype=float)
    fitted = np.array([item["epochFit"]["atFittedEpoch"]["p95Arcsec"] for item in records], dtype=float)
    static = np.array([item["epochFit"]["withoutPropagation"]["p95Arcsec"] for item in records], dtype=float)
    summary = {
        "schemaVersion": "layers-gaia-crossmatch-v1",
        "generatedAt": utc_now(),
        "comparisonKind": "catalogue cross-match",
        "gatedByBandpassTransfer": False,
        "counts": {"measured": len(records), "skipped": len(skipped)},
        "epochConsistency": {
            "medianFittedJyear": float(np.median(epochs)) if epochs.size else None,
            "scatterYears": float(robust_sigma(epochs)) if epochs.size > 2 else None,
            "note": (
                "A real coadd epoch is a property of the release, so the field-to-field scatter is the "
                "test of whether the fit is measuring an epoch or absorbing an unrelated systematic."
            ),
        },
        "astrometry": {
            "medianP95AtFittedEpochArcsec": float(np.nanmedian(fitted)) if fitted.size else None,
            "medianP95WithoutPropagationArcsec": float(np.nanmedian(static)) if static.size else None,
            "thresholdArcsec": 0.30,
            "passAtFittedEpoch": int(np.sum(fitted <= 0.30)) if fitted.size else 0,
            "passWithoutPropagation": int(np.sum(static <= 0.30)) if static.size else 0,
        },
        "skipped": skipped,
        "regions": [{key: value for key, value in item.items() if key != "trials"} for item in records],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nmeasured {len(records)} regions, {len(skipped)} skipped")
    if epochs.size:
        print(f"fitted epoch: median {np.median(epochs):.2f} scatter {robust_sigma(epochs):.2f} yr")
        print(
            f"astrometry p95: {np.nanmedian(static):.3f}\" static -> "
            f"{np.nanmedian(fitted):.3f}\" at fitted epoch; "
            f"{summary['astrometry']['passWithoutPropagation']} -> {summary['astrometry']['passAtFittedEpoch']} pass 0.30\""
        )


if __name__ == "__main__":
    main()
