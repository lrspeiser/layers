#!/usr/bin/env python3
"""Fit and test a Rubin-colour bandpass transfer against each reference survey.

Every SPARC pilot failed at exactly this step: point-source colour calibration
passed, but the resolved-galaxy transfer left 0.379 to 1.080 mag of residual
against a 0.08 mag tolerance. Those pilots had only one Rubin band, so no Rubin
colour existed and no colour term could be fitted at all. A second band per
region now makes the fit possible for the first time.

The question this stage answers is narrow and falsifiable:

    Does a linear term in Rubin colour reconcile Rubin and the reference
    bandpass on compact sources, and by how much does it reduce the residual?

It fits ``m_ref - m_rubin = a + b * (m_band2 - m_rubin)`` on matched compact
sources, reports the coefficients with their uncertainties, and compares the
residual scatter before and after. A fit that does not reduce the residual below
the declared tolerance leaves the bandpass blocker in place. Passing on compact
sources is necessary, not sufficient: an extended-source transfer must still be
demonstrated separately before any missing-light claim.
"""

from __future__ import annotations

import argparse
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
from reproject import reproject_interp

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_layer_registration import centroid_sources, robust_sigma

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECONCILED = ROOT / "pipeline/results/reconciled-regions/manifest.json"
DEFAULT_BAND2 = ROOT / "pipeline/results/rubin-pixels-50-band2/manifest.json"
DEFAULT_OUTPUT = ROOT / "pipeline/results/bandpass-transfer"
DEFAULT_PUBLIC = ROOT / "public/data/layers/selected-regions/bandpass-transfer.json"

# The tolerance the pilots declared for a usable filter transfer.
RESIDUAL_TOLERANCE_MAG = 0.08
MIN_SOURCES = 15
APERTURE_ARCSEC = 1.5


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def aperture_flux(image: np.ndarray, x: float, y: float, radius_pixels: float) -> float:
    height, width = image.shape
    lo_y, hi_y = max(0, int(y - radius_pixels) - 1), min(height, int(y + radius_pixels) + 2)
    lo_x, hi_x = max(0, int(x - radius_pixels) - 1), min(width, int(x + radius_pixels) + 2)
    if hi_y <= lo_y or hi_x <= lo_x:
        return float("nan")
    patch = image[lo_y:hi_y, lo_x:hi_x]
    yy, xx = np.mgrid[lo_y:hi_y, lo_x:hi_x]
    inside = np.hypot(xx - x, yy - y) <= radius_pixels
    if not inside.any():
        return float("nan")
    values = patch[inside]
    finite = np.isfinite(values)
    if finite.sum() < 0.8 * inside.sum():
        return float("nan")
    return float(values[finite].sum())


def weighted_line_fit(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    """Ordinary least squares with a robust iterative clip, returning uncertainties."""
    keep = np.ones(x.size, dtype=bool)
    coefficients = np.array([0.0, 0.0])
    for _ in range(5):
        if keep.sum() < 4:
            break
        design = np.column_stack((np.ones(int(keep.sum())), x[keep]))
        coefficients, *_ = np.linalg.lstsq(design, y[keep], rcond=None)
        residual = y - (coefficients[0] + coefficients[1] * x)
        sigma = robust_sigma(residual[keep])
        if not np.isfinite(sigma) or sigma <= 0:
            break
        updated = np.abs(residual - np.median(residual[keep])) < 3.0 * sigma
        if updated.sum() < 4 or np.array_equal(updated, keep):
            keep = updated if updated.sum() >= 4 else keep
            break
        keep = updated
    design = np.column_stack((np.ones(int(keep.sum())), x[keep]))
    residual = y[keep] - design @ coefficients
    dof = max(1, int(keep.sum()) - 2)
    variance = float(residual @ residual) / dof
    covariance = variance * np.linalg.inv(design.T @ design)
    # Before and after must be measured on the same sources, or the clip alone
    # changes the number and the comparison says nothing about the colour term.
    baseline = y[keep] - np.median(y[keep])
    return {
        "zeroPointMag": float(coefficients[0]),
        "zeroPointUncertaintyMag": float(math.sqrt(abs(covariance[0, 0]))),
        "colourTerm": float(coefficients[1]),
        "colourTermUncertainty": float(math.sqrt(abs(covariance[1, 1]))),
        "usedSources": int(keep.sum()),
        "rejectedSources": int(x.size - keep.sum()),
        "residualScatterMag": float(robust_sigma(residual)),
        "offsetOnlyScatterMag": float(robust_sigma(baseline)),
        # The least-squares fit minimises RMS, not the robust scatter, so both are
        # reported. RMS must fall; if the robust scatter does not, the colour term
        # is improving the tail rather than the core.
        "residualRmsMag": float(np.sqrt(np.mean(residual**2))),
        "offsetOnlyRmsMag": float(np.sqrt(np.mean(baseline**2))),
        "keep": keep,
    }


def measure_region(
    record: dict[str, Any], band2: dict[str, Any], output: Path
) -> dict[str, Any]:
    matched_path = ROOT / record["products"]["matchedPair"]
    with fits.open(matched_path, memmap=False, checksum=True) as hdus:
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        reference = np.asarray(hdus["REFERENCE"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data, dtype=np.uint8) > 0
        grid_header = hdus["RUBIN"].header
        grid_wcs = WCS(grid_header).celestial
    pixel_scale = float(record["pixelScaleArcsec"])

    # Put the second Rubin band on the same grid, with the same surface-brightness
    # to flux-per-pixel correction the first band received.
    with fits.open(ROOT / band2["mosaic"]["path"], memmap=False) as hdus:
        second = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
        second_wcs = WCS(hdus["IMAGE"].header).celestial
    second_native = float(np.mean(proj_plane_pixel_scales(second_wcs)) * 3600.0)
    aligned, footprint = reproject_interp(
        (second, second_wcs), grid_wcs.to_header(relax=True), shape_out=rubin.shape, order="bilinear"
    )
    aligned = aligned * (pixel_scale / second_native) ** 2
    second_valid = (footprint > 0) & np.isfinite(aligned)

    usable = common & second_valid
    if usable.sum() < 1000:
        raise ValueError(f"only {int(usable.sum())} pixels overlap the second band")

    sources = centroid_sources(rubin, usable, 0.0, pixel_scale)
    if len(sources) < MIN_SOURCES:
        raise ValueError(f"only {len(sources)} compact sources detected")

    radius = max(2.0, APERTURE_ARCSEC / pixel_scale)
    colour = []
    offset = []
    for source in sources:
        primary = aperture_flux(rubin, source["x"], source["y"], radius)
        other = aperture_flux(aligned, source["x"], source["y"], radius)
        ref = aperture_flux(reference, source["x"], source["y"], radius)
        if not all(np.isfinite(value) and value > 0 for value in (primary, other, ref)):
            continue
        colour.append(-2.5 * math.log10(other / primary))
        offset.append(-2.5 * math.log10(ref / primary))
    if len(colour) < MIN_SOURCES:
        raise ValueError(f"only {len(colour)} sources gave positive flux in all three planes")

    colour_array = np.asarray(colour)
    offset_array = np.asarray(offset)
    fit = weighted_line_fit(colour_array, offset_array)
    fit.pop("keep", None)
    # A constant zero-point offset is not a bandpass transfer. The comparison
    # that matters is whether adding the colour term beats simply removing the
    # median offset, measured on the same retained sources.
    before = fit.pop("offsetOnlyScatterMag")
    after = fit["residualScatterMag"]
    rms_before = fit.pop("offsetOnlyRmsMag")
    rms_after = fit["residualRmsMag"]

    significant = abs(fit["colourTerm"]) > 2.0 * fit["colourTermUncertainty"]
    passes = after <= RESIDUAL_TOLERANCE_MAG

    payload = {
        "schemaVersion": "layers-bandpass-transfer-v1",
        "regionId": record["regionId"],
        "tract": record["tract"],
        "generatedAt": utc_now(),
        "rubinBand": record["rubinBand"],
        "rubinColourBand": band2["band"],
        "referenceBand": record["referenceBand"],
        "referenceSurveyId": record["referenceSurveyId"],
        "model": (
            f"m_ref - m_rubin_{record['rubinBand']} = a + b * "
            f"(m_rubin_{band2['band']} - m_rubin_{record['rubinBand']})"
        ),
        "sources": {
            "detected": len(sources),
            "usable": len(colour),
            "apertureRadiusArcsec": round(radius * pixel_scale, 4),
            "colourRange": [float(np.min(colour_array)), float(np.max(colour_array))],
        },
        "fit": fit,
        "residual": {
            "beforeTransferMag": before,
            "afterTransferMag": after,
            "improvementMag": before - after,
            "beforeTransferRmsMag": rms_before,
            "afterTransferRmsMag": rms_after,
            "rmsImprovementMag": rms_before - rms_after,
            "toleranceMag": RESIDUAL_TOLERANCE_MAG,
            "withinTolerance": passes,
            "colourTermReducesScatter": bool(after < before),
        },
        "colourTermSignificant": significant,
        "compactSourceTransferDemonstrated": bool(passes),
        "interpretation": (
            "This is a compact-source result. Point sources are unresolved and share the PSF, so a "
            "colour term fitted here does not establish that the same transfer holds across a resolved "
            "galaxy, where the stellar population, dust, and surface brightness all vary with radius. "
            "The extended-source transfer remains the open blocker."
        ),
    }
    region_dir = output / record["regionId"]
    region_dir.mkdir(parents=True, exist_ok=True)
    (region_dir / "bandpass-transfer.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def universality_test(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Test whether one colour term explains every field.

    A bandpass colour term is a property of the two filter systems, so it must be
    the same constant in every field. If the field-to-field spread far exceeds the
    stated per-field uncertainties, the fits are being driven by something
    field-dependent and the transfer is not established, however significant any
    individual fit looks.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        key = f"{item['rubinColourBand']}-{item['rubinBand']}-vs-{item['referenceSurveyId']}"
        grouped.setdefault(key, []).append(item)

    results = {}
    for key, rows in grouped.items():
        if len(rows) < 3:
            continue
        terms = np.array([row["fit"]["colourTerm"] for row in rows])
        errors = np.array([row["fit"]["colourTermUncertainty"] for row in rows])
        errors = np.where(errors > 0, errors, np.nan)
        weights = 1.0 / errors**2
        if not np.isfinite(weights).any():
            continue
        mean = float(np.nansum(weights * terms) / np.nansum(weights))
        uncertainty = float(math.sqrt(1.0 / np.nansum(weights)))
        chi2 = float(np.nansum(weights * (terms - mean) ** 2))
        dof = max(1, len(rows) - 1)
        reduced = chi2 / dof
        results[key] = {
            "fields": len(rows),
            "weightedMeanColourTerm": mean,
            "weightedMeanUncertainty": uncertainty,
            "reducedChiSquare": reduced,
            "fieldSpread": float(np.std(terms)),
            "medianStatedUncertainty": float(np.nanmedian(errors)),
            "spreadToUncertaintyRatio": float(np.std(terms) / np.nanmedian(errors)),
            "consistentWithOneConstant": bool(reduced <= 2.0),
        }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
    parser.add_argument("--band2", type=Path, default=DEFAULT_BAND2)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--only-region", action="append", default=[])
    args = parser.parse_args()

    reconciled = json.loads(args.reconciled.read_text(encoding="utf-8"))
    band2_payload = json.loads(args.band2.read_text(encoding="utf-8"))
    band2_by_region = {
        item["regionId"]: item
        for item in band2_payload["regions"]
        if item.get("validation", {}).get("scienceReady") and item.get("mosaic")
    }

    only = {value.strip() for value in args.only_region if value.strip()}
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for record in reconciled["regions"]:
        region_id = record["regionId"]
        if only and region_id not in only:
            continue
        band2 = band2_by_region.get(region_id)
        if not band2:
            skipped.append({"regionId": region_id, "reason": "no validated second Rubin band"})
            continue
        if band2["band"] == record["rubinBand"]:
            skipped.append({"regionId": region_id, "reason": "second band duplicates the primary band"})
            continue
        try:
            result = measure_region(record, band2, args.output)
        except Exception as error:  # noqa: BLE001 - report, never silently drop
            skipped.append({"regionId": region_id, "reason": f"{type(error).__name__}: {error}"})
            print(f"[skipped] {region_id}: {type(error).__name__}: {error}", flush=True)
            continue
        records.append(result)
        print(
            f"[{'pass' if result['residual']['withinTolerance'] else 'fail'}] {region_id} "
            f"{result['rubinColourBand']}-{result['rubinBand']} colour term "
            f"{result['fit']['colourTerm']:+.3f}+/-{result['fit']['colourTermUncertainty']:.3f} "
            f"residual {result['residual']['beforeTransferMag']:.3f} -> {result['residual']['afterTransferMag']:.3f} mag",
            flush=True,
        )

    passing = [item for item in records if item["residual"]["withinTolerance"]]
    terms = [item["fit"]["colourTerm"] for item in records]
    before = [item["residual"]["beforeTransferMag"] for item in records]
    after = [item["residual"]["afterTransferMag"] for item in records]
    summary = {
        "schemaVersion": "layers-bandpass-transfer-summary-v1",
        "generatedAt": utc_now(),
        "toleranceMag": RESIDUAL_TOLERANCE_MAG,
        "counts": {
            "measured": len(records),
            "withinTolerance": len(passing),
            "skipped": len(skipped),
        },
        "aggregate": {
            "medianColourTerm": float(np.median(terms)) if terms else None,
            "colourTermScatter": float(robust_sigma(np.asarray(terms))) if len(terms) > 2 else None,
            "medianResidualBeforeMag": float(np.median(before)) if before else None,
            "medianResidualAfterMag": float(np.median(after)) if after else None,
        },
        "universality": universality_test(records),
        "policy": {
            "compactSourceOnly": True,
            "clearsBandpassBlocker": False,
            "note": (
                "A compact-source transfer never clears the bandpass blocker on its own. The pilots "
                "already passed point-source colour calibration and still failed the resolved-galaxy "
                "transfer by 5 to 13 times the tolerance. This stage measures whether a Rubin colour "
                "term exists and how much residual it removes; the extended-source test is separate."
            ),
        },
        "skipped": skipped,
        "regions": records,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"\nmeasured {len(records)} regions, {len(passing)} within {RESIDUAL_TOLERANCE_MAG} mag, {len(skipped)} skipped",
        flush=True,
    )
    if terms:
        print(
            f"median colour term {np.median(terms):+.4f}  "
            f"residual {np.median(before):.4f} -> {np.median(after):.4f} mag",
            flush=True,
        )
    for key, item in summary["universality"].items():
        verdict = "consistent" if item["consistentWithOneConstant"] else "INCONSISTENT"
        print(
            f"  {key}: {item['fields']} fields, term {item['weightedMeanColourTerm']:+.4f}"
            f" +/- {item['weightedMeanUncertainty']:.4f}, reduced chi2 {item['reducedChiSquare']:.1f}"
            f" -> {verdict} with a single constant",
            flush=True,
        )


if __name__ == "__main__":
    main()
