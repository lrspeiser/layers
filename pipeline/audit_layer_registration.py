#!/usr/bin/env python3
"""Measure Rubin/reference-layer registration readiness without overstating QA.

This is an audit, not the matching operation.  It measures common WCS support,
background planes, source-centroid residuals, and empirical PSF widths.  Gates
remain false for operations that have not actually been applied (notably PSF
and filter-response matching).
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import binary_erosion, gaussian_filter, maximum_filter
from scipy.spatial import cKDTree

from gaia_registration import gaia_epoch_registration, product_epochs

NANOMAGGY_TO_NJY = 3630.780547701
ASTROMETRY_THRESHOLD_ARCSEC = 0.30


def robust_sigma(values: np.ndarray) -> float:
    values = values[np.isfinite(values)]
    if not values.size:
        return math.nan
    median = np.median(values)
    return float(1.4826 * np.median(np.abs(values - median)))


def fit_sky_plane(data: np.ndarray, valid: np.ndarray, exclusion_radius_pixels: float) -> tuple[np.ndarray, dict]:
    height, width = data.shape
    yy, xx = np.indices(data.shape, dtype=np.float32)
    radius = np.hypot(xx - (width - 1) / 2, yy - (height - 1) / 2)
    sample = valid & (radius > exclusion_radius_pixels)
    xnorm = (xx - (width - 1) / 2) / max(width, 1)
    ynorm = (yy - (height - 1) / 2) / max(height, 1)
    for _ in range(4):
        values = data[sample]
        design = np.column_stack((np.ones(values.size), xnorm[sample], ynorm[sample]))
        coefficients, *_ = np.linalg.lstsq(design, values, rcond=None)
        model_values = design @ coefficients
        residuals = values - model_values
        sigma = robust_sigma(residuals)
        if not np.isfinite(sigma) or sigma <= 0:
            break
        keep = np.abs(residuals - np.median(residuals)) < 3.5 * sigma
        positions = np.flatnonzero(sample)
        next_sample = np.zeros_like(sample)
        next_sample.flat[positions[keep]] = True
        sample = next_sample
    plane = coefficients[0] + coefficients[1] * xnorm + coefficients[2] * ynorm
    final_residual = data[sample] - plane[sample]
    return plane.astype(np.float32), {
        "constant": float(coefficients[0]),
        "xSlopeAcrossImage": float(coefficients[1]),
        "ySlopeAcrossImage": float(coefficients[2]),
        "samplePixels": int(sample.sum()),
        "residualMedian": float(np.median(final_residual)),
        "residualSigma": robust_sigma(final_residual),
    }


def centroid_sources(data: np.ndarray, valid: np.ndarray, exclusion_radius_pixels: float, pixel_scale: float) -> list[dict]:
    smooth = gaussian_filter(np.where(valid, data, 0.0), 1.0)
    background = gaussian_filter(np.where(valid, data, 0.0), 10.0)
    detection = smooth - background
    sigma = robust_sigma(detection[valid])
    if not np.isfinite(sigma) or sigma <= 0:
        return []
    height, width = data.shape
    yy, xx = np.indices(data.shape)
    radius = np.hypot(xx - (width - 1) / 2, yy - (height - 1) / 2)
    patch_valid = binary_erosion(valid, iterations=6)
    maxima = (detection == maximum_filter(detection, size=9)) & patch_valid & (detection > 8 * sigma)
    maxima &= (xx > 12) & (xx < width - 13) & (yy > 12) & (yy < height - 13) & (radius > exclusion_radius_pixels)
    candidates = np.argwhere(maxima)
    if len(candidates) > 250:
        strengths = detection[candidates[:, 0], candidates[:, 1]]
        candidates = candidates[np.argsort(strengths)[-250:]]
    sources = []
    for y, x in candidates:
        patch = data[y - 6:y + 7, x - 6:x + 7].astype(np.float64)
        patch_valid = valid[y - 6:y + 7, x - 6:x + 7]
        border = np.concatenate((patch[0], patch[-1], patch[1:-1, 0], patch[1:-1, -1]))
        sky = np.median(border[np.isfinite(border)])
        flux = np.where(patch_valid, np.maximum(patch - sky, 0), 0)
        total = flux.sum()
        if total <= 0:
            continue
        py, px = np.indices(flux.shape)
        cx = float((px * flux).sum() / total)
        cy = float((py * flux).sum() / total)
        var_x = float((((px - cx) ** 2) * flux).sum() / total)
        var_y = float((((py - cy) ** 2) * flux).sum() / total)
        covariance = float((((px - cx) * (py - cy)) * flux).sum() / total)
        eigenvalues = np.linalg.eigvalsh([[var_x, covariance], [covariance, var_y]])
        if eigenvalues[0] <= 0:
            continue
        fwhm_arcsec = 2.35482 * math.sqrt(float(np.mean(eigenvalues))) * pixel_scale
        ellipticity = 1 - math.sqrt(float(eigenvalues[0] / eigenvalues[1]))
        if 0.5 <= fwhm_arcsec <= 4.0 and ellipticity < 0.45:
            sources.append({"x": float(x - 6 + cx), "y": float(y - 6 + cy), "fwhmArcsec": fwhm_arcsec})
    return sources


def match_sources(rubin_sources: list[dict], comparison_sources: list[dict], pixel_scale: float) -> dict:
    if not rubin_sources or not comparison_sources:
        return {"matchedSources": 0, "medianOffsetArcsec": None, "residualRmsArcsec": None, "residualP95Arcsec": None}
    comparison_positions = np.array([[item["x"], item["y"]] for item in comparison_sources])
    tree = cKDTree(comparison_positions)
    pairs = []
    used = set()
    for source in rubin_sources:
        distance, index = tree.query([source["x"], source["y"]], distance_upper_bound=3.0)
        if np.isfinite(distance) and int(index) not in used:
            used.add(int(index))
            comparison = comparison_sources[int(index)]
            pairs.append((source["x"] - comparison["x"], source["y"] - comparison["y"], source["fwhmArcsec"], comparison["fwhmArcsec"]))
    if not pairs:
        return {"matchedSources": 0, "medianOffsetArcsec": None, "residualRmsArcsec": None, "residualP95Arcsec": None}
    array = np.asarray(pairs)
    median_offset = np.median(array[:, :2], axis=0)
    residual = np.hypot(array[:, 0] - median_offset[0], array[:, 1] - median_offset[1]) * pixel_scale
    retained = np.ones(len(array), dtype=bool)
    # The nearest-neighbour proposal may contain blends, moving sources, or
    # centroid failures.  Iteratively reject only gross radial outliers using a
    # robust, data-derived bound; keep the predeclared 0.30-arcsec publication
    # threshold unchanged and report both proposed and retained counts.
    for _ in range(5):
        center = np.median(array[retained, :2], axis=0)
        radial = np.hypot(array[:, 0] - center[0], array[:, 1] - center[1]) * pixel_scale
        median_radial = float(np.median(radial[retained]))
        radial_sigma = robust_sigma(radial[retained])
        cutoff = max(0.35, median_radial + 4.0 * radial_sigma)
        next_retained = radial <= cutoff
        if next_retained.sum() < 10 or np.array_equal(next_retained, retained):
            break
        retained = next_retained
    array = array[retained]
    median_offset = np.median(array[:, :2], axis=0)
    residual_vectors_pixels = array[:, :2] - median_offset
    residual = np.hypot(residual_vectors_pixels[:, 0], residual_vectors_pixels[:, 1]) * pixel_scale
    residual_median_arcsec = {
        "x": float(np.median(residual_vectors_pixels[:, 0]) * pixel_scale),
        "y": float(np.median(residual_vectors_pixels[:, 1]) * pixel_scale),
    }
    residual_mad_arcsec = {
        "x": float(1.4826 * np.median(np.abs(residual_vectors_pixels[:, 0] - np.median(residual_vectors_pixels[:, 0]))) * pixel_scale),
        "y": float(1.4826 * np.median(np.abs(residual_vectors_pixels[:, 1] - np.median(residual_vectors_pixels[:, 1]))) * pixel_scale),
    }
    return {
        "matchedSources": len(pairs),
        "retainedSources": int(len(array)),
        "rejectedOutliers": int(len(pairs) - len(array)),
        "outlierPolicy": "iterative radial median plus 4 robust sigma; minimum 0.35 arcsec cutoff",
        "medianOffsetArcsec": {"x": float(median_offset[0] * pixel_scale), "y": float(median_offset[1] * pixel_scale)},
        "residualRmsArcsec": float(np.sqrt(np.mean(residual ** 2))),
        "residualP95Arcsec": float(np.percentile(residual, 95)),
        "residualMedianArcsec": residual_median_arcsec,
        "residualMadArcsec": residual_mad_arcsec,
        "rubinMedianFwhmArcsec": float(np.median(array[:, 2])),
        "comparisonMedianFwhmArcsec": float(np.median(array[:, 3])),
    }


def wcs_grid_residual(left: WCS, right: WCS, shape: tuple[int, int]) -> float:
    points = np.array([[0, 0], [shape[1] - 1, 0], [0, shape[0] - 1], [shape[1] - 1, shape[0] - 1], [(shape[1] - 1) / 2, (shape[0] - 1) / 2]])
    left_world = np.column_stack(left.pixel_to_world_values(points[:, 0], points[:, 1]))
    right_world = np.column_stack(right.pixel_to_world_values(points[:, 0], points[:, 1]))
    cos_dec = np.cos(np.deg2rad(left_world[:, 1]))
    separation = np.hypot((left_world[:, 0] - right_world[:, 0]) * cos_dec, left_world[:, 1] - right_world[:, 1]) * 3600
    return float(np.max(separation))


def audit_candidate(root: Path, args: argparse.Namespace, coverage: dict, slug: str, candidate: dict, comparison_key: str) -> dict:
    target = coverage[slug]
    band = candidate["band"]
    with fits.open(args.rubin_root / slug / f"rubin_{band}.fits", memmap=True) as rubin_hdus, fits.open(candidate["path"], memmap=False) as comparison_hdus:
        rubin_image = np.asarray(rubin_hdus["IMAGE"].data, dtype=np.float64)
        rubin_variance = np.asarray(rubin_hdus["VARIANCE"].data, dtype=np.float64)
        rubin_wcs = WCS(rubin_hdus["IMAGE"].header)
        comparison_wcs = WCS(comparison_hdus["IMAGE"].header)
        pixel_scale = float(rubin_hdus["IMAGE"].header["PIXSCALE"])
        if candidate["format"] == "legacy":
            comparison_image = np.asarray(comparison_hdus["IMAGE"].data, dtype=np.float64) * NANOMAGGY_TO_NJY
            comparison_variance = np.full(comparison_image.shape, np.nan, dtype=np.float64)
            comparison_ivar = np.asarray(comparison_hdus["IVAR"].data, dtype=np.float64) / (NANOMAGGY_TO_NJY ** 2)
            good_ivar = np.isfinite(comparison_ivar) & (comparison_ivar > 0)
            comparison_variance[good_ivar] = 1.0 / comparison_ivar[good_ivar]
            unit_transform = {"from": "nanomaggy", "to": "nJy", "factor": NANOMAGGY_TO_NJY}
        else:
            comparison_image = np.asarray(comparison_hdus["IMAGE"].data, dtype=np.float64)
            comparison_variance = np.asarray(comparison_hdus["VARIANCE"].data, dtype=np.float64)
            comparison_mask = np.asarray(comparison_hdus["MASK"].data)
            comparison_variance[comparison_mask != 0] = np.nan
            unit_transform = {"from": "PS1 stack data units", "to": "nJy", "factor": "per-skycell; see acquisition manifest"}
    valid = np.isfinite(rubin_image) & np.isfinite(rubin_variance) & (rubin_variance > 0) & np.isfinite(comparison_image) & np.isfinite(comparison_variance) & (comparison_variance > 0)
    major_axis_pixels = target["major_axis_arcmin"] * 60 / pixel_scale
    exclusion = max(major_axis_pixels * 1.5, 60)
    rubin_sky, rubin_sky_record = fit_sky_plane(rubin_image, valid, exclusion)
    comparison_sky, comparison_sky_record = fit_sky_plane(comparison_image, valid, exclusion)
    rubin_sources = centroid_sources(rubin_image - rubin_sky, valid, exclusion, pixel_scale)
    comparison_sources = centroid_sources(comparison_image - comparison_sky, valid, exclusion, pixel_scale)
    astrometry = match_sources(rubin_sources, comparison_sources, pixel_scale)
    if candidate["layerId"] == "panstarrs-dr1-stack":
        corrected_astrometry = gaia_epoch_registration(
            rubin_sources, comparison_sources, rubin_wcs, pixel_scale,
            root / "pipeline/cache/gaia-dr3" / f"{slug}.csv",
            product_epochs(root, slug, band), root,
        )
        if corrected_astrometry:
            astrometry = {**corrected_astrometry, "uncorrectedSourceRegistration": astrometry}
    measured_residual = astrometry.get("residualP95Arcsec")
    qa = {
        "schemaVersion": 1,
        "objectId": slug,
        "comparisonKey": comparison_key,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "layerIds": ["rubin-dp2-deep-coadd", candidate["layerId"]],
        "comparisonLayerLabel": candidate["label"],
        "status": "qa",
        "band": band,
        "commonWcs": wcs_grid_residual(rubin_wcs, comparison_wcs, rubin_image.shape) < 0.001,
        "commonFootprint": float(valid.mean()) > 0.5,
        "unitsMatched": True,
        "unitTransform": unit_transform,
        "skyModelMeasured": True,
        "skyMatched": False,
        "psfMeasured": astrometry.get("matchedSources", 0) >= 5,
        "psfMatched": False,
        "filterMatched": False,
        "filterTransform": None,
        "wcsGridResidualArcsec": wcs_grid_residual(rubin_wcs, comparison_wcs, rubin_image.shape),
        "astrometryThresholdArcsec": ASTROMETRY_THRESHOLD_ARCSEC,
        "astrometryPass": measured_residual is not None and measured_residual <= ASTROMETRY_THRESHOLD_ARCSEC,
        "commonValidPixelFraction": float(valid.mean()),
        "rubinSkyModel": rubin_sky_record,
        "comparisonSkyModelNjy": comparison_sky_record,
        "sourceRegistration": astrometry,
        "limitations": [
            "Sky planes have been measured but not yet applied to a released matched product.",
            "Empirical PSFs have been measured but the sharper layer has not yet been convolved.",
            f"Rubin and {candidate['label']} nominal bandpasses are not identical; no color transformation has been applied.",
        ],
    }
    output_dir = args.output / comparison_key
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "registration-audit.json").write_text(json.dumps(qa, indent=2), encoding="utf-8")
    print(f"[{target['sparc_id']}] {candidate['label']} {band}: {astrometry.get('matchedSources', 0)} sources, p95={measured_residual}")
    return qa


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline" / "results" / "dp2-sparc-coverage.json")
    parser.add_argument("--rubin-root", type=Path, default=root / "pipeline" / "output" / "dp2-sparc")
    parser.add_argument("--legacy-root", type=Path, default=root / "pipeline" / "output" / "legacy-survey")
    parser.add_argument("--legacy-manifest", type=Path, default=root / "pipeline" / "output" / "legacy-survey" / "manifest.json")
    parser.add_argument("--panstarrs-root", type=Path, default=root / "pipeline" / "output" / "panstarrs")
    parser.add_argument("--panstarrs-manifest", type=Path, default=root / "pipeline" / "output" / "panstarrs" / "manifest.json")
    parser.add_argument("--output", type=Path, default=root / "pipeline" / "output" / "comparisons")
    args = parser.parse_args()

    coverage = {item["slug"]: item for item in json.loads(args.coverage.read_text(encoding="utf-8"))["targets"]}
    legacy_manifest = json.loads(args.legacy_manifest.read_text(encoding="utf-8")) if args.legacy_manifest.is_file() else {"targets": []}
    panstarrs_manifest = json.loads(args.panstarrs_manifest.read_text(encoding="utf-8")) if args.panstarrs_manifest.is_file() else {"targets": []}
    legacy_by_slug = {record["target"]["slug"]: record for record in legacy_manifest["targets"]}
    panstarrs_by_slug = {record["target"]["slug"]: record for record in panstarrs_manifest["targets"]}
    for slug in sorted(set(legacy_by_slug) | set(panstarrs_by_slug)):
        target = coverage[slug]
        candidates = []
        for band in ("r", "i", "z", "g"):
            rubin_path = args.rubin_root / slug / f"rubin_{band}.fits"
            if not rubin_path.is_file():
                continue
            with fits.open(rubin_path, memmap=True) as rubin_hdus:
                rubin_usable = np.isfinite(rubin_hdus["IMAGE"].data).any()
            if not rubin_usable:
                continue
            legacy_product = legacy_by_slug.get(slug, {}).get("bands", {}).get(band, {})
            legacy_path = args.legacy_root / slug / f"legacy_{band}.fits"
            if legacy_path.is_file() and legacy_product.get("science_coverage"):
                candidates.append(
                    {
                        "layerId": "legacy-survey-dr10",
                        "label": "Legacy Survey DR10",
                        "band": band,
                        "coverage": legacy_product.get("valid_pixel_fraction", 0),
                        "path": legacy_path,
                        "format": "legacy",
                    }
                )
            panstarrs_product = panstarrs_by_slug.get(slug, {}).get("bands", {}).get(band, {})
            panstarrs_path = args.panstarrs_root / slug / f"panstarrs_{band}.fits"
            if panstarrs_path.is_file() and panstarrs_product.get("science_coverage"):
                candidates.append(
                    {
                        "layerId": "panstarrs-dr1-stack",
                        "label": "Pan-STARRS1 DR1",
                        "band": band,
                        "coverage": panstarrs_product.get("valid_pixel_fraction", 0),
                        "path": panstarrs_path,
                        "format": "panstarrs",
                    }
                )
        if not candidates:
            output_dir = args.output / slug
            output_dir.mkdir(parents=True, exist_ok=True)
            qa = {"schemaVersion": 1, "objectId": slug, "status": "blocked", "reason": "No common usable optical band between Rubin DP2 and an acquired reference image layer."}
            (output_dir / "registration-audit.json").write_text(json.dumps(qa, indent=2), encoding="utf-8")
            print(f"[{target['sparc_id']}] blocked: no common band")
            continue
        primary = max(candidates, key=lambda item: item["coverage"])
        selected = {}
        for layer_id in sorted({item["layerId"] for item in candidates}):
            layer_candidates = [item for item in candidates if item["layerId"] == layer_id]
            preferred_band = "z" if layer_id == "legacy-survey-dr10" else "i"
            selected[layer_id] = next((item for item in layer_candidates if item["band"] == preferred_band), max(layer_candidates, key=lambda item: item["coverage"]))
        selected[primary["layerId"]] = primary
        for layer_id, candidate in selected.items():
            comparison_key = slug if layer_id == primary["layerId"] else f"{slug}--{layer_id}"
            audit_candidate(root, args, coverage, slug, candidate, comparison_key)


if __name__ == "__main__":
    main()
