#!/usr/bin/env python3
"""Test whether the stellar color relation transfers to resolved galaxy light.

The test operates on PSF/sky-matched z-band images and independently matches
the reference r band to the same PSF. It measures 6.4-arcsec spatial cells inside
the declared galaxy region. A failed result is useful: it prevents a stellar
calibration from being silently applied to extended structure.
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

from audit_layer_registration import fit_sky_plane, robust_sigma
from reconcile_image_layers import normalized_convolution, read_comparison, shifted_comparison


CELL_SIZE_PIXELS = 16
MIN_VALID_FRACTION = 0.90
MIN_SIGNAL_TO_NOISE = 20.0
MIN_RESOLVED_CELLS = 20
MIN_COLOR_SUPPORT_FRACTION = 0.80
MAX_MEDIAN_ABSOLUTE_RESIDUAL_MAG = 0.08
MAX_ROBUST_SCATTER_MAG = 0.12


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_target(slug: str, coverage: dict, comparisons: Path, legacy_root: Path, panstarrs_root: Path) -> dict:
    output_dir = comparisons / slug
    reconciliation_path = output_dir / "reconciliation.json"
    stellar_path = output_dir / "filter-response-audit.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    stellar = json.loads(stellar_path.read_text(encoding="utf-8"))
    if reconciliation.get("status") == "blocked" or not stellar.get("pointSourceCalibrationPass"):
        return {
            "schemaVersion": 1,
            "objectId": slug,
            "status": "blocked",
            "reason": "Passing image reconciliation and stellar filter calibration are required first.",
        }
    comparison_layer = reconciliation.get("layerIds", [None, None])[1]
    target_band = reconciliation.get("band")
    if comparison_layer == "legacy-survey-dr10" and target_band == "z":
        reference_root = legacy_root
        reference_prefix = "legacy"
        reference_label = "Legacy"
    elif comparison_layer == "panstarrs-dr1-stack" and target_band == "i":
        reference_root = panstarrs_root
        reference_prefix = "panstarrs"
        reference_label = "Pan-STARRS"
    else:
        return {
            "schemaVersion": 1,
            "objectId": slug,
            "status": "blocked",
            "reason": "No resolved transfer adapter is implemented for this survey and band pair.",
        }

    matched_path = Path(reconciliation["products"]["matchedPair"])
    with fits.open(matched_path, memmap=False) as hdus:
        rubin_z = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        rubin_variance = np.asarray(hdus["RUBIN_VAR"].data, dtype=np.float64)
        legacy_z = np.asarray(hdus["COMPARISON"].data, dtype=np.float64)
        legacy_z_variance = np.asarray(hdus["COMPARISON_VAR"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data, dtype=bool)
        pixel_scale = float(hdus["RUBIN"].header["PIXSCALE"])

    registration_path = output_dir / "registration-audit.json"
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    legacy_r_path = reference_root / slug / f"{reference_prefix}_r.fits"
    legacy_r, legacy_r_variance, legacy_r_valid = read_comparison(legacy_r_path, comparison_layer)
    applied_shift = reconciliation["registration"]["appliedComparisonShiftPixels"]
    legacy_r, legacy_r_variance, legacy_r_valid = shifted_comparison(
        legacy_r, legacy_r_variance, legacy_r_valid, applied_shift["x"], applied_shift["y"]
    )
    exclusion = max(coverage[slug]["major_axis_arcmin"] * 60 / pixel_scale * 1.5, 60)
    legacy_r_sky, legacy_r_sky_record = fit_sky_plane(legacy_r, legacy_r_valid & common, exclusion)
    legacy_r -= legacy_r_sky
    original_fwhm = float(registration["sourceRegistration"]["comparisonMedianFwhmArcsec"])
    target_fwhm = float(reconciliation["psf"]["targetFwhmArcsec"])
    kernel_sigma = math.sqrt(max(0.0, target_fwhm**2 - original_fwhm**2)) / 2.354820045 / pixel_scale
    legacy_r, legacy_r_variance, legacy_r_valid, kernel_radius = normalized_convolution(
        legacy_r, legacy_r_variance, legacy_r_valid, kernel_sigma
    )

    height, width = rubin_z.shape
    yy, xx = np.indices(rubin_z.shape)
    radius_arcsec = np.hypot(xx - (width - 1) / 2, yy - (height - 1) / 2) * pixel_scale
    galaxy_radius_arcsec = coverage[slug]["major_axis_arcmin"] * 30 * 1.5
    spatial_cells = []
    for y0 in range(0, height, CELL_SIZE_PIXELS):
        for x0 in range(0, width, CELL_SIZE_PIXELS):
            section = (slice(y0, min(y0 + CELL_SIZE_PIXELS, height)), slice(x0, min(x0 + CELL_SIZE_PIXELS, width)))
            in_galaxy = radius_arcsec[section] <= galaxy_radius_arcsec
            valid = common[section] & legacy_r_valid[section] & in_galaxy
            if valid.sum() < CELL_SIZE_PIXELS**2 * MIN_VALID_FRACTION:
                continue
            fluxes = [rubin_z[section][valid].sum(), legacy_z[section][valid].sum(), legacy_r[section][valid].sum()]
            variances = [rubin_variance[section][valid].sum(), legacy_z_variance[section][valid].sum(), legacy_r_variance[section][valid].sum()]
            if min(fluxes) <= 0 or min(variances) <= 0:
                continue
            signal_to_noise = min(flux / math.sqrt(variance) for flux, variance in zip(fluxes, variances, strict=True))
            if signal_to_noise < MIN_SIGNAL_TO_NOISE:
                continue
            color = -2.5 * math.log10(fluxes[2] / fluxes[1])
            delta = -2.5 * math.log10(fluxes[0] / fluxes[1])
            predicted = stellar["model"]["interceptMag"] + stellar["model"]["slope"] * color
            spatial_cells.append(
                {
                    "xCenterPixel": x0 + (CELL_SIZE_PIXELS - 1) / 2,
                    "yCenterPixel": y0 + (CELL_SIZE_PIXELS - 1) / 2,
                    "legacyRMinusZMag": color,
                    "rubinZMinusLegacyZMag": delta,
                    "stellarModelPredictionMag": predicted,
                    "residualMag": delta - predicted,
                    "minimumSignalToNoise": signal_to_noise,
                }
            )

    residuals = np.asarray([cell["residualMag"] for cell in spatial_cells], dtype=np.float64)
    colors = np.asarray([cell["legacyRMinusZMag"] for cell in spatial_cells], dtype=np.float64)
    stellar_support = stellar["sample"]["legacyRMinusZP05P95Mag"]
    supported = (colors >= stellar_support[0]) & (colors <= stellar_support[1]) if colors.size else np.zeros(0, dtype=bool)
    support_fraction = float(supported.mean()) if supported.size else 0.0
    tested_residuals = residuals[supported]
    median_absolute = float(np.median(np.abs(tested_residuals))) if tested_residuals.size else None
    scatter = robust_sigma(tested_residuals) if tested_residuals.size else None
    transfer_pass = bool(
        tested_residuals.size >= MIN_RESOLVED_CELLS
        and support_fraction >= MIN_COLOR_SUPPORT_FRACTION
        and median_absolute is not None and median_absolute <= MAX_MEDIAN_ABSOLUTE_RESIDUAL_MAG
        and scatter is not None and scatter <= MAX_ROBUST_SCATTER_MAG
    )
    audit = {
        "schemaVersion": 1,
        "objectId": slug,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if transfer_pass else "qa-failed",
        "method": f"6.4-arcsec resolved cells within 1.5 times the catalogued semi-major axis; {reference_label} r matched to the {target_band}-pair PSF and sky model",
        "layerIds": reconciliation["layerIds"],
        "cellSizePixels": CELL_SIZE_PIXELS,
        "cellSizeArcsec": CELL_SIZE_PIXELS * pixel_scale,
        "galaxyRadiusArcsec": galaxy_radius_arcsec,
        "qualifiedCells": len(spatial_cells),
        "cellsWithinStellarColorSupport": int(supported.sum()),
        "stellarColorSupportMag": stellar_support,
        "colorSupportFraction": support_fraction,
        "medianAbsoluteResidualMag": median_absolute,
        "robustResidualScatterMag": scatter,
        "extendedSourceTransferPass": transfer_pass,
        "thresholds": {
            "minimumResolvedCells": MIN_RESOLVED_CELLS,
            "minimumColorSupportFraction": MIN_COLOR_SUPPORT_FRACTION,
            "maximumMedianAbsoluteResidualMag": MAX_MEDIAN_ABSOLUTE_RESIDUAL_MAG,
            "maximumRobustResidualScatterMag": MAX_ROBUST_SCATTER_MAG,
            "minimumSignalToNoise": MIN_SIGNAL_TO_NOISE,
        },
        "supportingProducts": {
            "matchedPairSha256": sha256(matched_path),
            "stellarAuditSha256": sha256(stellar_path),
            "predictorBandSourceSha256": sha256(legacy_r_path),
            "predictorBandSkyModelNjy": legacy_r_sky_record,
            "predictorBandKernelSigmaPixels": kernel_sigma,
            "predictorBandKernelRadiusPixels": kernel_radius,
            "referenceSurvey": reference_label,
        },
        "spatialCells": spatial_cells,
        "limitations": [
            "This is an empirical resolved-color transfer test, not synthetic photometry through the full survey throughput curves.",
            "Cells are not statistically independent because reprojection and PSF matching introduce covariance.",
            "Only cells inside the stellar training color range contribute to pass/fail residual statistics.",
            "A failure blocks missing-light, stellar-mass, and baryonic-acceleration claims; it does not imply either survey is wrong.",
        ],
    }
    audit_path = output_dir / "extended-source-filter-audit.json"
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    reconciliation["filterResponse"].update(
        {
            "extendedSourceTransferPass": transfer_pass,
            "extendedSourceAudit": str(audit_path.resolve()),
            "extendedSourceAuditSha256": sha256(audit_path),
            "resolvedCellCount": len(spatial_cells),
            "resolvedMedianAbsoluteResidualMag": median_absolute,
            "resolvedRobustScatterMag": scatter,
            "reason": "Resolved galaxy-cell transfer passed." if transfer_pass else "The stellar color relation did not meet the predeclared resolved galaxy-cell transfer thresholds.",
        }
    )
    reconciliation["filterResponse"]["matched"] = transfer_pass
    reconciliation["quantitativeDifferenceAllowed"] = bool(
        transfer_pass
        and reconciliation.get("registration", {}).get("astrometryPass")
        and reconciliation.get("psf", {}).get("matched")
        and reconciliation.get("sky", {}).get("matched")
        and reconciliation.get("injectionRecovery", {}).get("status") == "pass"
    )
    reconciliation["status"] = "matched-and-validated" if reconciliation["quantitativeDifferenceAllowed"] else "matched-not-photometrically-comparable"
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
        result = audit_target(slug, coverage, args.comparisons, args.legacy_root, args.panstarrs_root)
        output = args.comparisons / slug / "extended-source-filter-audit.json"
        if not output.is_file():
            output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        summary.append({"objectId": slug, "status": result["status"]})
        print(f"[{slug}] {result['status']}")
    (args.comparisons / "extended-source-filter-summary.json").write_text(
        json.dumps({"schemaVersion": 1, "targets": summary}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
