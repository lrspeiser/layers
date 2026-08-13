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
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits

from audit_layer_registration import centroid_sources, fit_sky_plane, robust_sigma
from reconcile_image_layers import read_comparison, shifted_comparison


MIN_CALIBRATION_STARS = 50
MIN_COLOR_SPAN_MAG = 0.80
MAX_CROSS_VALIDATION_RMS_MAG = 0.08
APERTURE_RADIUS_PIXELS = 8
SKY_INNER_RADIUS_PIXELS = 12
SKY_OUTER_RADIUS_PIXELS = 18
MIN_SIGNAL_TO_NOISE = 20.0


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


def audit_target(slug: str, coverage: dict, args: argparse.Namespace) -> dict:
    output_dir = args.comparisons / slug
    reconciliation_path = output_dir / "reconciliation.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
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
    with fits.open(matched_path, memmap=False) as hdus:
        rubin_z = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        rubin_variance = np.asarray(hdus["RUBIN_VAR"].data, dtype=np.float64)
        legacy_z = np.asarray(hdus["COMPARISON"].data, dtype=np.float64)
        legacy_z_variance = np.asarray(hdus["COMPARISON_VAR"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data, dtype=bool)
        pixel_scale = float(hdus["RUBIN"].header["PIXSCALE"])

    reference_prefix = "legacy" if comparison_layer == "legacy-survey-dr10" else "panstarrs"
    legacy_r_path = reference_root / slug / f"{reference_prefix}_{predictor_band}.fits"
    if not legacy_r_path.is_file():
        return {
            "schemaVersion": 1,
            "objectId": slug,
            "status": "blocked",
            "reason": f"The {reference_label} {predictor_band}-band predictor product has not been acquired.",
        }
    legacy_r, legacy_r_variance, legacy_r_valid = read_comparison(legacy_r_path, comparison_layer)
    shift_record = reconciliation["registration"]["appliedComparisonShiftPixels"]
    legacy_r, legacy_r_variance, legacy_r_valid = shifted_comparison(
        legacy_r,
        legacy_r_variance,
        legacy_r_valid,
        shift_record["x"],
        shift_record["y"],
    )
    legacy_r_common = legacy_r_valid & common
    exclusion = max(coverage[slug]["major_axis_arcmin"] * 60 / pixel_scale * 1.5, 60)
    legacy_r_sky, legacy_r_sky_record = fit_sky_plane(legacy_r, legacy_r_common, exclusion)
    legacy_r -= legacy_r_sky

    sources = centroid_sources(rubin_z, common, exclusion, pixel_scale)
    rows = []
    for source in sources:
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
        color_uncertainty = 1.085736 * math.sqrt(
            (legacy_r_sample[1] / legacy_r_sample[0]) ** 2 + (legacy_z_sample[1] / legacy_z_sample[0]) ** 2
        )
        delta_uncertainty = 1.085736 * math.sqrt(
            (rubin_sample[1] / rubin_sample[0]) ** 2 + (legacy_z_sample[1] / legacy_z_sample[0]) ** 2
        )
        rows.append(
            {
                "x": source["x"],
                "y": source["y"],
                "fold": spatial_fold(source["x"], source["y"]),
                "legacyRMinusZMag": color,
                "rubinZMinusLegacyZMag": delta,
                "colorUncertaintyMag": color_uncertainty,
                "deltaUncertaintyMag": delta_uncertainty,
            }
        )

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
                "predictorBandSource": str(legacy_r_path.resolve()),
                "predictorBandSourceSha256": sha256(legacy_r_path),
                "predictorBandSkyModelNjy": legacy_r_sky_record,
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

    color = np.asarray([row["legacyRMinusZMag"] for row in rows])
    delta = np.asarray([row["rubinZMinusLegacyZMag"] for row in rows])
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
            "qualifiedHighSnrSources": len(rows),
            "retainedCalibrationStars": int(retained.sum()),
            "rejectedOutliers": int((~retained).sum()),
            "signalToNoiseMinimum": MIN_SIGNAL_TO_NOISE,
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
            "predictorBandSource": str(legacy_r_path.resolve()),
            "predictorBandSourceSha256": sha256(legacy_r_path),
            "predictorBandSkyModelNjy": legacy_r_sky_record,
            "referenceSurvey": reference_label,
        },
        "limitations": [
            "The fitted relation is empirical and field-specific; it is validated on held-out stars only.",
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
        "reason": "Held-out field stars constrain the color term, but extended-source transfer and recovery tests remain pending.",
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
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    coverage = {item["slug"]: item for item in json.loads(args.coverage.read_text(encoding="utf-8"))["targets"]}
    slugs = sorted(path.parent.name for path in args.comparisons.glob("*/reconciliation.json"))
    if args.only:
        slugs = [slug for slug in slugs if slug in set(args.only)]
    summary = []
    for slug in slugs:
        result = audit_target(slug, coverage, args)
        output = args.comparisons / slug / "filter-response-audit.json"
        if not output.is_file():
            output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        summary.append({"objectId": slug, "status": result["status"]})
        print(f"[{slug}] {result['status']}")
    (args.comparisons / "filter-response-summary.json").write_text(
        json.dumps({"schemaVersion": 1, "targets": summary}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
