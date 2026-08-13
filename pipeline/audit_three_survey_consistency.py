#!/usr/bin/env python3
"""Triangulate resolved z-band flux with Rubin, Legacy, and Pan-STARRS.

This is a diagnostic audit, not a publication gate.  Each reference is
registered to Rubin, all three images are sky-subtracted and PSF-matched, and
resolved 6.4-arcsec cells are compared after subtracting the median field-star
offset for the same survey pair.  Agreement can identify which two-survey
calibration deserves follow-up; it cannot by itself establish missing light.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits

from audit_filter_response import aperture_flux
from audit_layer_registration import centroid_sources, fit_sky_plane, match_sources, robust_sigma
from reconcile_image_layers import normalized_convolution, read_comparison, read_rubin, shifted_comparison

CELL_PIXELS = 16
MIN_CELL_VALID = 0.90
MIN_SNR = 20.0
ASTROMETRY_LIMIT_ARCSEC = 0.30


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stellar_offset(a, av, b, bv, valid, sources) -> dict:
    values = []
    for source in sources:
        left = aperture_flux(a, av, valid, source["x"], source["y"])
        right = aperture_flux(b, bv, valid, source["x"], source["y"])
        if not left or not right or min(left[0], right[0]) <= 0:
            continue
        if min(left[0] / left[1], right[0] / right[1]) < MIN_SNR:
            continue
        values.append(-2.5 * math.log10(left[0] / right[0]))
    array = np.asarray(values)
    return {
        "qualifiedStars": int(array.size),
        "medianOffsetMag": float(np.median(array)) if array.size else None,
        "robustScatterMag": robust_sigma(array) if array.size else None,
    }


def cell_residuals(a, av, b, bv, valid, radius, galaxy_radius, offset) -> list[float]:
    values = []
    height, width = a.shape
    for y0 in range(0, height, CELL_PIXELS):
        for x0 in range(0, width, CELL_PIXELS):
            section = (slice(y0, min(y0 + CELL_PIXELS, height)), slice(x0, min(x0 + CELL_PIXELS, width)))
            keep = valid[section] & (radius[section] <= galaxy_radius)
            if keep.sum() < CELL_PIXELS**2 * MIN_CELL_VALID:
                continue
            af, bf = float(a[section][keep].sum()), float(b[section][keep].sum())
            ava, bva = float(av[section][keep].sum()), float(bv[section][keep].sum())
            if min(af, bf, ava, bva) <= 0 or min(af / math.sqrt(ava), bf / math.sqrt(bva)) < MIN_SNR:
                continue
            values.append(-2.5 * math.log10(af / bf) - offset)
    return values


def audit(slug: str, coverage: dict, root: Path) -> dict:
    paths = {
        "rubin": root / "pipeline" / "output" / "dp2-sparc" / slug / "rubin_z.fits",
        "legacy": root / "pipeline" / "output" / "legacy-survey" / slug / "legacy_z.fits",
        "panstarrs": root / "pipeline" / "output" / "panstarrs" / slug / "panstarrs_z.fits",
    }
    if not all(path.is_file() for path in paths.values()):
        return {"schemaVersion": 1, "objectId": slug, "status": "blocked", "reason": "All three z-band products are required."}
    rubin, rubin_var, rubin_valid, header = read_rubin(paths["rubin"])
    legacy, legacy_var, legacy_valid = read_comparison(paths["legacy"], "legacy-survey-dr10")
    ps, ps_var, ps_valid = read_comparison(paths["panstarrs"], "panstarrs-dr1-stack")
    pixel_scale = float(header["PIXSCALE"])
    exclusion = max(coverage[slug]["major_axis_arcmin"] * 60 / pixel_scale * 1.5, 60)

    initial = rubin_valid & legacy_valid & ps_valid
    rubin_sky, rubin_sky_record = fit_sky_plane(rubin, initial, exclusion)
    rubin -= rubin_sky
    registrations = {}
    images = {"rubin": (rubin, rubin_var, rubin_valid)}
    fwhm = {}
    rubin_sources = centroid_sources(rubin, initial, exclusion, pixel_scale)
    for name, image, variance, valid in (("legacy", legacy, legacy_var, legacy_valid), ("panstarrs", ps, ps_var, ps_valid)):
        pair_valid = rubin_valid & valid
        sky, sky_record = fit_sky_plane(image, pair_valid, exclusion)
        image = image - sky
        reference_sources = centroid_sources(image, pair_valid, exclusion, pixel_scale)
        registration = match_sources(rubin_sources, reference_sources, pixel_scale)
        registrations[name] = {**registration, "skyModelNjy": sky_record, "astrometryPass": bool(registration.get("residualP95Arcsec") is not None and registration["residualP95Arcsec"] <= ASTROMETRY_LIMIT_ARCSEC)}
        offset = registration.get("medianOffsetArcsec")
        if not offset:
            return {"schemaVersion": 1, "objectId": slug, "status": "blocked", "reason": f"No usable {name} source registration."}
        image, variance, valid = shifted_comparison(image, variance, valid, offset["x"] / pixel_scale, offset["y"] / pixel_scale)
        images[name] = (image, variance, valid)
        fwhm[name] = registration.get("comparisonMedianFwhmArcsec")
        fwhm["rubin"] = registration.get("rubinMedianFwhmArcsec")

    if not all(record["astrometryPass"] for record in registrations.values()):
        status = "registration-blocked"
    else:
        status = "diagnostic"
    target_fwhm = max(value for value in fwhm.values() if value) * 1.05
    matched = {}
    for name, (image, variance, valid) in images.items():
        sigma = math.sqrt(max(0.0, target_fwhm**2 - fwhm[name]**2)) / 2.354820045 / pixel_scale
        matched[name] = normalized_convolution(image, variance, valid, sigma)[:3]
    common = matched["rubin"][2] & matched["legacy"][2] & matched["panstarrs"][2]
    sources = centroid_sources(matched["rubin"][0], common, exclusion, pixel_scale)
    yy, xx = np.indices(common.shape)
    radius = np.hypot(xx - (common.shape[1] - 1) / 2, yy - (common.shape[0] - 1) / 2) * pixel_scale
    galaxy_radius = coverage[slug]["major_axis_arcmin"] * 30 * 1.5
    pairs = {}
    for left, right in (("rubin", "legacy"), ("rubin", "panstarrs"), ("legacy", "panstarrs")):
        a, av, _ = matched[left]
        b, bv, _ = matched[right]
        star = stellar_offset(a, av, b, bv, common, sources)
        residuals = cell_residuals(a, av, b, bv, common, radius, galaxy_radius, star["medianOffsetMag"] or 0.0)
        array = np.asarray(residuals)
        pairs[f"{left}-minus-{right}"] = {
            "stellarCalibration": star,
            "resolvedCells": int(array.size),
            "resolvedMedianOffsetAfterStellarCalibrationMag": float(np.median(array)) if array.size else None,
            "resolvedMedianAbsoluteOffsetMag": float(np.median(np.abs(array))) if array.size else None,
            "resolvedRobustScatterMag": robust_sigma(array) if array.size else None,
        }
    return {
        "schemaVersion": 1,
        "objectId": slug,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "band": "z",
        "method": "Three-survey 6.4-arcsec resolved-cell comparison after independent source registration, sky subtraction, Gaussian PSF matching, and pair-specific median field-star calibration.",
        "astrometryThresholdArcsec": ASTROMETRY_LIMIT_ARCSEC,
        "registrationsToRubin": registrations,
        "commonValidPixelFraction": float(common.mean()),
        "commonTargetFwhmArcsec": target_fwhm,
        "pairs": pairs,
        "sourceProducts": {name: {"path": path.relative_to(root).as_posix(), "sha256": sha256(path)} for name, path in paths.items()},
        "rubinSkyModelNjy": rubin_sky_record,
        "limitations": [
            "This diagnostic uses scalar field-star offsets, not full color-dependent synthetic photometry.",
            "Resolved cells are correlated by reprojection and PSF matching.",
            "A third-survey preference identifies calibration follow-up; it is not a missing-light or mass claim.",
        ],
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    coverage = {item["slug"]: item for item in json.loads((root / "pipeline/results/dp2-sparc-coverage.json").read_text(encoding="utf-8"))["targets"]}
    results = []
    slugs = args.only or ["ugc00191", "ugc00634"]
    for slug in slugs:
        result = audit(slug, coverage, root)
        path = root / "pipeline" / "output" / "comparisons" / slug / "three-survey-consistency.json"
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        results.append({"objectId": slug, "status": result["status"]})
        print(f"[{slug}] {result['status']}")
    (root / "pipeline/output/comparisons/three-survey-consistency-summary.json").write_text(json.dumps({"schemaVersion": 1, "targets": results}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
