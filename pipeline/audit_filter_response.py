#!/usr/bin/env python3
"""Constrain cross-survey filter terms with held-out field stars.

This audit tests whether a reference-survey color can predict Rubin point-source
photometry in the comparison band.  Passing this audit does not by itself make
an extended galaxy comparable: stellar and diffuse-galaxy SEDs differ, and the
per-pixel color transform still requires PSF-matched multi-band products plus
injection/recovery validation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.spatial import cKDTree

from audit_layer_registration import centroid_sources, fit_sky_plane, robust_sigma
from reconcile_image_layers import read_comparison, read_rubin, shifted_comparison


MIN_CALIBRATION_STARS = 50
MIN_COLOR_SPAN_MAG = 0.80
MAX_CROSS_VALIDATION_RMS_MAG = 0.08
APERTURE_RADIUS_PIXELS = 8
SKY_INNER_RADIUS_PIXELS = 12
SKY_OUTER_RADIUS_PIXELS = 18
MIN_SIGNAL_TO_NOISE = 20.0
MAX_CATALOG_MAG_ERROR = 0.05
MAX_PSF_KRON_DIFFERENCE_MAG = 0.20
CATALOG_MATCH_RADIUS_ARCSEC = 0.80


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aperture_flux(
    image: np.ndarray,
    variance: np.ndarray,
    valid: np.ndarray,
    x: float,
    y: float,
) -> tuple[float, float] | None:
    x0, y0 = int(round(x)), int(round(y))
    radius = SKY_OUTER_RADIUS_PIXELS + 1
    if x0 - radius < 0 or y0 - radius < 0 or x0 + radius >= image.shape[1] or y0 + radius >= image.shape[0]:
        return None
    image_patch = image[y0 - radius : y0 + radius + 1, x0 - radius : x0 + radius + 1]
    variance_patch = variance[y0 - radius : y0 + radius + 1, x0 - radius : x0 + radius + 1]
    valid_patch = valid[y0 - radius : y0 + radius + 1, x0 - radius : x0 + radius + 1]
    yy, xx = np.indices(image_patch.shape, dtype=np.float64)
    radial = np.hypot(xx - radius - (x - x0), yy - radius - (y - y0))
    aperture = radial <= APERTURE_RADIUS_PIXELS
    annulus = (radial >= SKY_INNER_RADIUS_PIXELS) & (radial <= SKY_OUTER_RADIUS_PIXELS)
    if not valid_patch[aperture].all() or valid_patch[annulus].mean() < 0.98:
        return None
    sky = float(np.median(image_patch[annulus]))
    flux = float(np.sum(image_patch[aperture] - sky))
    aperture_variance = float(np.sum(variance_patch[aperture]))
    sky_variance = float(np.median(variance_patch[annulus])) / max(int(annulus.sum()), 1)
    uncertainty = math.sqrt(max(0.0, aperture_variance + aperture.sum() ** 2 * sky_variance))
    if not np.isfinite(flux) or not np.isfinite(uncertainty) or uncertainty <= 0:
        return None
    return flux, uncertainty


def robust_linear_fit(color: np.ndarray, delta: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    design = np.column_stack((np.ones(color.size), color))
    active = np.isfinite(color) & np.isfinite(delta)
    for _ in range(12):
        coefficients, *_ = np.linalg.lstsq(design[active], delta[active], rcond=None)
        residuals = delta - design @ coefficients
        median = float(np.median(residuals[active]))
        sigma = robust_sigma(residuals[active])
        if not np.isfinite(sigma) or sigma <= 0:
            break
        next_active = active & (np.abs(residuals - median) < 3.0 * sigma)
        if np.array_equal(next_active, active):
            break
        active = next_active
    return coefficients, residuals, active


def spatial_fold(x: float, y: float) -> int:
    return (int(x // 240) + 3 * int(y // 240)) % 5


def panstarrs_catalog_rows(
    slug: str,
    coverage: dict,
    reconciliation: dict,
    catalog_root: Path,
    rubin_root: Path,
) -> tuple[list[dict], list[dict], int, dict, dict, dict]:
    """Measure Rubin fluxes at independently calibrated PS1 stellar positions."""
    catalog_path = catalog_root / f"{slug}.csv"
    manifest_path = catalog_root / "manifest.json"
    if not catalog_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError(
            "The checksum-backed Pan-STARRS DR2 mean-object catalog has not been acquired. "
            f"Run fetch_panstarrs_catalog.py --only {slug}."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_record = next((record for record in manifest["targets"] if record["objectId"] == slug), None)
    if manifest_record is None or manifest_record.get("sha256") != sha256(catalog_path):
        raise RuntimeError("The Pan-STARRS catalog cache does not match its acquisition manifest.")

    calibration_manifest_path = rubin_root / slug / "rubin_i_calibration_manifest.json"
    calibration_manifest = (
        json.loads(calibration_manifest_path.read_text(encoding="utf-8"))
        if calibration_manifest_path.is_file()
        else None
    )
    if calibration_manifest:
        rubin_path = Path(calibration_manifest["product"])
        if not rubin_path.is_file() or sha256(rubin_path) != calibration_manifest["productSha256"]:
            raise RuntimeError("The Rubin calibration field does not match its checksum manifest.")
    else:
        rubin_path = Path(reconciliation["products"]["sourceRubin"])
    rubin_i, rubin_variance, rubin_valid, rubin_header = read_rubin(rubin_path)
    pixel_scale = float(rubin_header["PIXSCALE"])
    exclusion = max(coverage[slug]["major_axis_arcmin"] * 60 / pixel_scale * 1.5, 60)
    rubin_sky, rubin_sky_record = fit_sky_plane(rubin_i, rubin_valid, exclusion)
    rubin_i -= rubin_sky
    sources = centroid_sources(rubin_i, rubin_valid, exclusion, pixel_scale)
    rubin_support = {
        "path": str(rubin_path.resolve()),
        "sha256": sha256(rubin_path),
        "calibrationFieldManifest": str(calibration_manifest_path.resolve()) if calibration_manifest else None,
        "calibrationFieldManifestSha256": sha256(calibration_manifest_path) if calibration_manifest else None,
        "fieldWidthArcmin": calibration_manifest.get("fieldWidthArcmin") if calibration_manifest else coverage[slug]["field_width_arcmin"],
    }
    if not sources:
        return [], [], 0, manifest_record, rubin_sky_record, rubin_support
    positions = np.asarray([[source["x"], source["y"]] for source in sources])
    tree = cKDTree(positions)
    wcs = WCS(rubin_header).celestial
    height, width = rubin_i.shape
    catalog_matches: dict[int, tuple[float, dict, float, float, float, float]] = {}
    with catalog_path.open("r", encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            try:
                r_mag = float(record["rMeanPSFMag"])
                i_mag = float(record["iMeanPSFMag"])
                r_error = float(record["rMeanPSFMagErr"])
                i_error = float(record["iMeanPSFMagErr"])
                r_kron = float(record["rMeanKronMag"])
                i_kron = float(record["iMeanKronMag"])
                ra = float(record["raMean"])
                dec = float(record["decMean"])
            except (KeyError, TypeError, ValueError):
                continue
            values = (r_mag, i_mag, r_error, i_error, r_kron, i_kron, ra, dec)
            if not all(np.isfinite(values)) or min(r_mag, i_mag, r_error, i_error, r_kron, i_kron) <= -900:
                continue
            if int(record["nr"]) < 2 or int(record["ni"]) < 2:
                continue
            if max(r_error, i_error) > MAX_CATALOG_MAG_ERROR:
                continue
            if abs(r_mag - r_kron) > MAX_PSF_KRON_DIFFERENCE_MAG or abs(i_mag - i_kron) > MAX_PSF_KRON_DIFFERENCE_MAG:
                continue
            catalog_x, catalog_y = wcs.world_to_pixel_values(ra, dec)
            catalog_x, catalog_y = float(catalog_x), float(catalog_y)
            if not (0 <= catalog_x < width and 0 <= catalog_y < height):
                continue
            if math.hypot(catalog_x - (width - 1) / 2, catalog_y - (height - 1) / 2) <= exclusion:
                continue
            distance, index = tree.query(
                [catalog_x, catalog_y], distance_upper_bound=CATALOG_MATCH_RADIUS_ARCSEC / pixel_scale
            )
            if not np.isfinite(distance):
                continue
            index = int(index)
            previous = catalog_matches.get(index)
            if previous is None or distance < previous[0]:
                catalog_matches[index] = (float(distance), record, r_mag, i_mag, r_error, i_error)

    rows = []
    for index, (distance, record, r_mag, i_mag, r_error, i_error) in catalog_matches.items():
        source = sources[index]
        rubin_sample = aperture_flux(
            rubin_i, rubin_variance, rubin_valid, source["x"], source["y"]
        )
        if rubin_sample is None or rubin_sample[0] <= 0 or rubin_sample[0] / rubin_sample[1] < MIN_SIGNAL_TO_NOISE:
            continue
        rubin_mag = 31.4 - 2.5 * math.log10(rubin_sample[0])
        color = r_mag - i_mag
        delta = rubin_mag - i_mag
        rows.append(
            {
                "x": source["x"],
                "y": source["y"],
                "fold": spatial_fold(source["x"], source["y"]),
                "referenceColorMag": color,
                "rubinMinusReferenceMag": delta,
                "colorUncertaintyMag": math.sqrt(r_error**2 + i_error**2),
                "deltaUncertaintyMag": math.sqrt(
                    (1.085736 * rubin_sample[1] / rubin_sample[0]) ** 2 + i_error**2
                ),
                "catalogObjectId": record["objID"],
                "catalogToRubinCentroidArcsec": float(distance * pixel_scale),
            }
        )
    return rows, sources, len(catalog_matches), manifest_record, rubin_sky_record, rubin_support


def bootstrap_coefficients(color: np.ndarray, delta: np.ndarray, samples: int = 400) -> dict:
    generator = np.random.default_rng(20260812)
    coefficients = []
    design = np.column_stack((np.ones(color.size), color))
    for _ in range(samples):
        selected = generator.integers(0, color.size, color.size)
        fit, *_ = np.linalg.lstsq(design[selected], delta[selected], rcond=None)
        coefficients.append(fit)
    values = np.asarray(coefficients)
    return {
        "samples": samples,
        "interceptStdMag": float(np.std(values[:, 0], ddof=1)),
        "slopeStd": float(np.std(values[:, 1], ddof=1)),
        "interceptP16P84Mag": [float(item) for item in np.percentile(values[:, 0], [16, 84])],
        "slopeP16P84": [float(item) for item in np.percentile(values[:, 1], [16, 84])],
    }


def audit_target(comparison_key: str, coverage: dict, args: argparse.Namespace) -> dict:
    output_dir = args.comparisons / comparison_key
    reconciliation_path = output_dir / "reconciliation.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    slug = reconciliation["objectId"]
    if reconciliation.get("status") == "blocked":
        return {
            "schemaVersion": 1,
            "objectId": slug,
            "status": "blocked",
            "reason": "Image registration must pass before a filter-response audit.",
        }
    comparison_layer = reconciliation["layerIds"][1]
    target_band = reconciliation["band"]
    if comparison_layer == "legacy-survey-dr10" and target_band == "z":
        predictor_band = "r"
        reference_label = "Legacy"
        reference_root = args.legacy_root
    elif comparison_layer == "panstarrs-dr1-stack" and target_band == "i":
        predictor_band = "r"
        reference_label = "PanSTARRS"
        reference_root = args.panstarrs_root
    else:
        return {
            "schemaVersion": 1,
            "objectId": slug,
            "status": "blocked",
            "reason": "No validated empirical color adapter is implemented for this survey and target-band pair.",
        }

    matched_path = Path(reconciliation["products"]["matchedPair"])
    catalog_support = None
    detected_sources = []
    catalog_candidates = None
    if comparison_layer == "panstarrs-dr1-stack":
        try:
            rows, detected_sources, catalog_candidates, catalog_record, rubin_sky_record, rubin_support = panstarrs_catalog_rows(
                slug, coverage, reconciliation, args.panstarrs_catalog_root, args.rubin_root
            )
        except (FileNotFoundError, RuntimeError) as error:
            return {
                "schemaVersion": 1,
                "objectId": slug,
                "status": "blocked",
                "reason": str(error),
            }
        catalog_path = args.panstarrs_catalog_root / f"{slug}.csv"
        catalog_support = {
            "catalog": "Pan-STARRS DR2 MeanObjectView",
            "catalogPath": str(catalog_path.resolve()),
            "catalogSha256": sha256(catalog_path),
            "catalogRows": catalog_record["rows"],
            "queryUrl": catalog_record["queryUrl"],
            "documentation": catalog_record["documentation"],
            "rubinCalibrationField": rubin_support,
            "rubinSkyModelNjy": rubin_sky_record,
            "positionMatchRadiusArcsec": CATALOG_MATCH_RADIUS_ARCSEC,
            "maximumCatalogMagnitudeError": MAX_CATALOG_MAG_ERROR,
            "maximumPsfMinusKronMagnitude": MAX_PSF_KRON_DIFFERENCE_MAG,
        }
    else:
        with fits.open(matched_path, memmap=False) as hdus:
            rubin_z = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
            rubin_variance = np.asarray(hdus["RUBIN_VAR"].data, dtype=np.float64)
            legacy_z = np.asarray(hdus["COMPARISON"].data, dtype=np.float64)
            legacy_z_variance = np.asarray(hdus["COMPARISON_VAR"].data, dtype=np.float64)
            common = np.asarray(hdus["COMMON_MASK"].data, dtype=bool)
            pixel_scale = float(hdus["RUBIN"].header["PIXSCALE"])

    reference_prefix = "legacy" if comparison_layer == "legacy-survey-dr10" else "panstarrs"
    legacy_r_path = reference_root / slug / f"{reference_prefix}_{predictor_band}.fits"
    if comparison_layer == "legacy-survey-dr10" and not legacy_r_path.is_file():
        return {
            "schemaVersion": 1,
            "objectId": slug,
            "status": "blocked",
            "reason": f"The {reference_label} {predictor_band}-band predictor product has not been acquired.",
        }
    if comparison_layer == "legacy-survey-dr10":
        legacy_r, legacy_r_variance, legacy_r_valid = read_comparison(legacy_r_path, comparison_layer)
        shift_record = reconciliation["registration"]["appliedComparisonShiftPixels"]
        legacy_r, legacy_r_variance, legacy_r_valid = shifted_comparison(
            legacy_r, legacy_r_variance, legacy_r_valid, shift_record["x"], shift_record["y"]
        )
        legacy_r_common = legacy_r_valid & common
        exclusion = max(coverage[slug]["major_axis_arcmin"] * 60 / pixel_scale * 1.5, 60)
        legacy_r_sky, legacy_r_sky_record = fit_sky_plane(legacy_r, legacy_r_common, exclusion)
        legacy_r -= legacy_r_sky
        detected_sources = centroid_sources(rubin_z, common, exclusion, pixel_scale)
        rows = []
        for source in detected_sources:
            samples = [
                aperture_flux(rubin_z, rubin_variance, common, source["x"], source["y"]),
                aperture_flux(legacy_z, legacy_z_variance, common, source["x"], source["y"]),
                aperture_flux(legacy_r, legacy_r_variance, legacy_r_common, source["x"], source["y"]),
            ]
            if any(sample is None for sample in samples):
                continue
            rubin_sample, legacy_z_sample, legacy_r_sample = samples
            assert rubin_sample and legacy_z_sample and legacy_r_sample
            if min(rubin_sample[0], legacy_z_sample[0], legacy_r_sample[0]) <= 0:
                continue
            if min(rubin_sample[0] / rubin_sample[1], legacy_z_sample[0] / legacy_z_sample[1], legacy_r_sample[0] / legacy_r_sample[1]) < MIN_SIGNAL_TO_NOISE:
                continue
            color = -2.5 * math.log10(legacy_r_sample[0] / legacy_z_sample[0])
            delta = -2.5 * math.log10(rubin_sample[0] / legacy_z_sample[0])
            rows.append(
                {
                    "x": source["x"], "y": source["y"], "fold": spatial_fold(source["x"], source["y"]),
                    "referenceColorMag": color, "rubinMinusReferenceMag": delta,
                    "colorUncertaintyMag": 1.085736 * math.sqrt((legacy_r_sample[1] / legacy_r_sample[0]) ** 2 + (legacy_z_sample[1] / legacy_z_sample[0]) ** 2),
                    "deltaUncertaintyMag": 1.085736 * math.sqrt((rubin_sample[1] / rubin_sample[0]) ** 2 + (legacy_z_sample[1] / legacy_z_sample[0]) ** 2),
                }
            )
    sources = detected_sources

    if len(rows) < 10:
        audit = {
            "schemaVersion": 1,
            "objectId": slug,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "status": "qa-failed",
            "reason": "Insufficient common-footprint high-S/N stars for a cross-validated color relation.",
            "layerIds": reconciliation["layerIds"],
            "targetBand": target_band,
            "predictorBands": [predictor_band, target_band],
            "model": None,
            "sample": {
                "detectedPointSources": len(sources),
                "catalogPointSourceCandidates": catalog_candidates,
                "qualifiedHighSnrSources": len(rows),
                "retainedCalibrationStars": len(rows),
                "signalToNoiseMinimum": MIN_SIGNAL_TO_NOISE,
                "minimumRequiredForFitAttempt": 10,
            },
            "crossValidation": None,
            "thresholds": {
                "minimumCalibrationStars": MIN_CALIBRATION_STARS,
                "minimumColorSpanMag": MIN_COLOR_SPAN_MAG,
                "maximumCrossValidationRmsMag": MAX_CROSS_VALIDATION_RMS_MAG,
            },
            "pointSourceCalibrationPass": False,
            "extendedSourceTransferPass": False,
            "filterMatched": False,
            "quantitativeDifferenceAllowed": False,
            "supportingProducts": {
                "matchedPairSha256": sha256(matched_path),
                **(catalog_support or {
                    "predictorBandSource": str(legacy_r_path.resolve()),
                    "predictorBandSourceSha256": sha256(legacy_r_path),
                    "predictorBandSkyModelNjy": legacy_r_sky_record,
                }),
                "referenceSurvey": reference_label,
            },
            "limitations": [
                "The common mask leaves too few fully supported stellar apertures and sky annuli for fitting.",
                "No color coefficient, uncertainty, or significance is estimated from this insufficient sample.",
                "A failed calibration-support gate blocks resolved-light and astrophysical inference.",
            ],
        }
        audit_path = output_dir / "filter-response-audit.json"
        audit_path.write_text(json.dumps(audit, indent=2, allow_nan=False), encoding="utf-8")
        reconciliation["filterResponse"] = {
            "matched": False,
            "pointSourceCalibrationPass": False,
            "extendedSourceTransferPass": False,
            "audit": str(audit_path.resolve()),
            "auditSha256": sha256(audit_path),
            "heldOutRmsMag": None,
            "reason": audit["reason"],
        }
        reconciliation["quantitativeDifferenceAllowed"] = False
        reconciliation_path.write_text(json.dumps(reconciliation, indent=2, allow_nan=False), encoding="utf-8")
        return audit

    color = np.asarray([row["referenceColorMag"] for row in rows])
    delta = np.asarray([row["rubinMinusReferenceMag"] for row in rows])
    coefficients, residuals, retained = robust_linear_fit(color, delta)
    retained_indices = np.flatnonzero(retained)
    cross_validation_residuals = []
    fold_records = []
    for fold in range(5):
        test = retained & (np.asarray([row["fold"] for row in rows]) == fold)
        train = retained & ~test
        if test.sum() == 0 or train.sum() < 10:
            continue
        train_design = np.column_stack((np.ones(train.sum()), color[train]))
        fold_coefficients, *_ = np.linalg.lstsq(train_design, delta[train], rcond=None)
        test_design = np.column_stack((np.ones(test.sum()), color[test]))
        fold_residuals = delta[test] - test_design @ fold_coefficients
        cross_validation_residuals.extend(fold_residuals.tolist())
        fold_records.append(
            {
                "fold": fold,
                "trainingStars": int(train.sum()),
                "heldOutStars": int(test.sum()),
                "rmsMag": float(np.sqrt(np.mean(fold_residuals**2))),
                "medianAbsoluteResidualMag": float(np.median(np.abs(fold_residuals))),
            }
        )
    cross_validation_residuals = np.asarray(cross_validation_residuals)
    color_span = float(np.percentile(color[retained], 95) - np.percentile(color[retained], 5))
    cv_rms = float(np.sqrt(np.mean(cross_validation_residuals**2)))
    point_source_pass = bool(
        retained.sum() >= MIN_CALIBRATION_STARS
        and color_span >= MIN_COLOR_SPAN_MAG
        and cv_rms <= MAX_CROSS_VALIDATION_RMS_MAG
    )
    fit_residual_sigma = robust_sigma(residuals[retained])
    audit = {
        "schemaVersion": 1,
        "objectId": slug,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "point-source-pass-extended-source-pending" if point_source_pass else "qa-failed",
        "layerIds": reconciliation["layerIds"],
        "targetBand": target_band,
        "predictorBands": [predictor_band, target_band],
        "model": {
            "equation": f"Rubin_{target_band} - {reference_label}_{target_band} = intercept + slope * ({reference_label}_{predictor_band} - {reference_label}_{target_band})",
            "interceptMag": float(coefficients[0]),
            "slope": float(coefficients[1]),
            "fitRobustScatterMag": fit_residual_sigma,
            "bootstrap": bootstrap_coefficients(color[retained], delta[retained]),
        },
        "sample": {
            "detectedPointSources": len(sources),
            "catalogPointSourceCandidates": catalog_candidates,
            "qualifiedHighSnrSources": len(rows),
            "retainedCalibrationStars": int(retained.sum()),
            "rejectedOutliers": int((~retained).sum()),
            "signalToNoiseMinimum": MIN_SIGNAL_TO_NOISE,
            "referenceColorP05P95Mag": [float(item) for item in np.percentile(color[retained], [5, 95])],
            "legacyRMinusZP05P95Mag": [float(item) for item in np.percentile(color[retained], [5, 95])],
            "colorSpanMag": color_span,
            "apertureRadiusPixels": APERTURE_RADIUS_PIXELS,
            "skyAnnulusPixels": [SKY_INNER_RADIUS_PIXELS, SKY_OUTER_RADIUS_PIXELS],
        },
        "crossValidation": {
            "method": "five spatial folds",
            "rmsMag": cv_rms,
            "medianAbsoluteResidualMag": float(np.median(np.abs(cross_validation_residuals))),
            "folds": fold_records,
        },
        "thresholds": {
            "minimumCalibrationStars": MIN_CALIBRATION_STARS,
            "minimumColorSpanMag": MIN_COLOR_SPAN_MAG,
            "maximumCrossValidationRmsMag": MAX_CROSS_VALIDATION_RMS_MAG,
        },
        "pointSourceCalibrationPass": point_source_pass,
        "extendedSourceTransferPass": False,
        "filterMatched": False,
        "quantitativeDifferenceAllowed": False,
        "supportingProducts": {
            "matchedPairSha256": sha256(matched_path),
            **(catalog_support or {
                "predictorBandSource": str(legacy_r_path.resolve()),
                "predictorBandSourceSha256": sha256(legacy_r_path),
                "predictorBandSkyModelNjy": legacy_r_sky_record,
            }),
            "referenceSurvey": reference_label,
        },
        "limitations": [
            "The fitted relation is empirical and field-specific; it is validated on held-out stars only.",
            *(["Pan-STARRS catalog photometry is independently calibrated; this stellar test does not calibrate the DR1 stack-pixel zero points used by a future resolved-light transform."] if catalog_support else []),
            "Stellar colors do not establish the transformation for an extended galaxy with spatially varying stellar populations, dust, and emission lines.",
            "Aperture-level validation absorbs average PSF differences but does not create a PSF-matched r-band image.",
            "Extended-source synthetic photometry and injection/recovery remain mandatory before interpreting a diffuse-light difference.",
        ],
    }
    audit_path = output_dir / "filter-response-audit.json"
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    audit["auditSha256"] = sha256(audit_path)
    reconciliation["filterResponse"] = {
        "matched": False,
        "pointSourceCalibrationPass": point_source_pass,
        "extendedSourceTransferPass": False,
        "audit": str(audit_path.resolve()),
        "auditSha256": audit["auditSha256"],
        "heldOutRmsMag": cv_rms,
            "reason": (
                "Held-out field stars pass the predeclared sample, color-span, and RMS gates; extended-source transfer remains pending."
                if point_source_pass
                else "The empirical relation is precise, but one or more predeclared stellar calibration gates remain unmet."
            ),
    }
    reconciliation["quantitativeDifferenceAllowed"] = False
    reconciliation_path.write_text(json.dumps(reconciliation, indent=2), encoding="utf-8")
    return audit


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline" / "results" / "dp2-sparc-coverage.json")
    parser.add_argument("--comparisons", type=Path, default=root / "pipeline" / "output" / "comparisons")
    parser.add_argument("--legacy-root", type=Path, default=root / "pipeline" / "output" / "legacy-survey")
    parser.add_argument("--panstarrs-root", type=Path, default=root / "pipeline" / "output" / "panstarrs")
    parser.add_argument("--panstarrs-catalog-root", type=Path, default=root / "pipeline" / "cache" / "panstarrs-dr2-mean")
    parser.add_argument("--rubin-root", type=Path, default=root / "pipeline" / "output" / "dp2-sparc")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    coverage = {item["slug"]: item for item in json.loads(args.coverage.read_text(encoding="utf-8"))["targets"]}
    comparison_keys = sorted(path.parent.name for path in args.comparisons.glob("*/reconciliation.json"))
    if args.only:
        comparison_keys = [key for key in comparison_keys if json.loads((args.comparisons / key / "reconciliation.json").read_text(encoding="utf-8")).get("objectId") in set(args.only)]
    summary = []
    for comparison_key in comparison_keys:
        result = audit_target(comparison_key, coverage, args)
        output = args.comparisons / comparison_key / "filter-response-audit.json"
        if not output.is_file():
            output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        summary.append({"comparisonKey": comparison_key, "objectId": result["objectId"], "status": result["status"]})
        print(f"[{comparison_key}] {result['status']}")
    (args.comparisons / "filter-response-summary.json").write_text(
        json.dumps({"schemaVersion": 1, "targets": summary}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
