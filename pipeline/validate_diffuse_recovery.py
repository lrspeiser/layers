#!/usr/bin/env python3
"""Measure diffuse-source detection limits with deterministic injection/recovery.

Artificial exponential sources are inserted into the real, reconciled images at
random outer-field positions.  A local plane plus the known source template is
fit in each stamp.  Detection thresholds come from the empirical distribution
of identical fits at blank positions, so resampling covariance, confusion, sky
residuals, and unmasked artifacts contribute to the measured limit.

This validates the measurement stage; it does not make the two filters equal or
turn a residual into a missing-light claim.
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
from scipy.ndimage import gaussian_filter

from audit_layer_registration import robust_sigma


AB_ZEROPOINT_NJY = 31.4
AXIS_RATIO = 0.65
RADII_ARCSEC = (3.0, 6.0, 12.0, 24.0)
CENTRAL_SURFACE_BRIGHTNESS_MAG = (20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0, 30.0)
TRIALS = 64
MIN_TRIALS = 32
DETECTION_SIGMA = 5.0
RECOVERY_TOLERANCE = 0.25
COMPLETENESS_TARGET = 0.90


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_created_at(path: Path) -> str:
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8")).get("createdAt")
        if existing:
            return existing
    return datetime.now(timezone.utc).isoformat()


def exponential_template(
    effective_radius_arcsec: float,
    pixel_scale_arcsec: float,
    psf_fwhm_arcsec: float,
) -> tuple[np.ndarray, float]:
    """Return a unit-total-flux, PSF-convolved elliptical exponential source."""
    scale_length_pixels = effective_radius_arcsec / 1.67834699 / pixel_scale_arcsec
    radius = int(math.ceil(4.0 * effective_radius_arcsec / pixel_scale_arcsec))
    yy, xx = np.indices((2 * radius + 1, 2 * radius + 1), dtype=np.float64)
    x = xx - radius
    y = yy - radius
    elliptical_radius = np.hypot(x, y / AXIS_RATIO)
    template = np.exp(-elliptical_radius / scale_length_pixels)
    psf_sigma_pixels = psf_fwhm_arcsec / 2.354820045 / pixel_scale_arcsec
    template = gaussian_filter(template, psf_sigma_pixels, mode="constant", cval=0.0)
    enclosed_fraction = float(template.sum() / (2 * math.pi * AXIS_RATIO * scale_length_pixels**2))
    template /= template.sum()
    return template, enclosed_fraction


def total_flux_njy(mu0_mag_arcsec2: float, effective_radius_arcsec: float) -> float:
    central_njy_arcsec2 = 10 ** ((AB_ZEROPOINT_NJY - mu0_mag_arcsec2) / 2.5)
    scale_length_arcsec = effective_radius_arcsec / 1.67834699
    return float(2 * math.pi * AXIS_RATIO * scale_length_arcsec**2 * central_njy_arcsec2)


def choose_positions(
    common: np.ndarray,
    radius: int,
    central_exclusion_pixels: float,
    trials: int,
    seed: int,
) -> list[tuple[int, int]]:
    generator = np.random.default_rng(seed)
    height, width = common.shape
    positions: list[tuple[int, int]] = []
    # Trials are evaluated one at a time, so their stamps may overlap.  A small
    # center separation avoids duplicate blank samples without making the
    # largest angular-size grid geometrically impossible in a 12-arcmin field.
    minimum_separation = 20
    for _ in range(30000):
        if len(positions) >= trials:
            break
        x = int(generator.integers(radius + 2, width - radius - 2))
        y = int(generator.integers(radius + 2, height - radius - 2))
        if math.hypot(x - width / 2, y - height / 2) <= central_exclusion_pixels + radius:
            continue
        patch = common[y - radius : y + radius + 1, x - radius : x + radius + 1]
        if patch.shape != (2 * radius + 1, 2 * radius + 1) or patch.mean() < 0.95:
            continue
        if any(math.hypot(x - other_x, y - other_y) < minimum_separation for other_x, other_y in positions):
            continue
        positions.append((x, y))
    if len(positions) < MIN_TRIALS:
        raise RuntimeError(
            f"only {len(positions)} valid injection positions found for radius {radius}; "
            f"minimum is {MIN_TRIALS}"
        )
    return positions


def fit_template_amplitude(
    image: np.ndarray,
    variance: np.ndarray,
    valid: np.ndarray,
    template: np.ndarray,
    x: int,
    y: int,
    injection_flux_njy: float = 0.0,
) -> tuple[float, float]:
    radius = template.shape[0] // 2
    image_patch = image[y - radius : y + radius + 1, x - radius : x + radius + 1].astype(np.float64)
    variance_patch = variance[y - radius : y + radius + 1, x - radius : x + radius + 1].astype(np.float64)
    valid_patch = valid[y - radius : y + radius + 1, x - radius : x + radius + 1]
    injected = image_patch + injection_flux_njy * template
    yy, xx = np.indices(template.shape, dtype=np.float64)
    xnorm = (xx - radius) / max(radius, 1)
    ynorm = (yy - radius) / max(radius, 1)
    use = valid_patch & np.isfinite(injected) & np.isfinite(variance_patch) & (variance_patch > 0)
    design = np.column_stack((template[use], np.ones(use.sum()), xnorm[use], ynorm[use]))
    weights = 1.0 / variance_patch[use]
    normal = design.T @ (weights[:, None] * design)
    right = design.T @ (weights * injected[use])
    coefficients = np.linalg.solve(normal, right)
    formal_uncertainty = math.sqrt(float(np.linalg.inv(normal)[0, 0]))
    return float(coefficients[0]), formal_uncertainty


def wilson_interval(successes: int, total: int, z: float = 1.0) -> list[float]:
    if total == 0:
        return [math.nan, math.nan]
    fraction = successes / total
    denominator = 1 + z**2 / total
    center = (fraction + z**2 / (2 * total)) / denominator
    half = z * math.sqrt(fraction * (1 - fraction) / total + z**2 / (4 * total**2)) / denominator
    return [float(max(0.0, center - half)), float(min(1.0, center + half))]


def validate_layer(
    image: np.ndarray,
    variance: np.ndarray,
    common: np.ndarray,
    pixel_scale: float,
    psf_fwhm: float,
    central_exclusion_pixels: float,
    seed: int,
) -> dict:
    size_records = []
    for size_index, effective_radius in enumerate(RADII_ARCSEC):
        template, enclosed_fraction = exponential_template(effective_radius, pixel_scale, psf_fwhm)
        radius = template.shape[0] // 2
        try:
            positions = choose_positions(
                common,
                radius,
                central_exclusion_pixels,
                TRIALS,
                seed + size_index * 1009,
            )
        except RuntimeError as error:
            size_records.append(
                {
                    "effectiveRadiusArcsec": effective_radius,
                    "axisRatio": AXIS_RATIO,
                    "templateRadiusPixels": radius,
                    "templateEnclosedFluxFraction": enclosed_fraction,
                    "status": "insufficient-common-footprint",
                    "reason": str(error),
                    "validInjectionPositions": 0,
                    "faintest90PercentCompleteMu0MagArcsec2": None,
                    "trials": [],
                }
            )
            continue
        blank = []
        formal = []
        for x, y in positions:
            amplitude, uncertainty = fit_template_amplitude(image, variance, common, template, x, y)
            blank.append(amplitude)
            formal.append(uncertainty)
        blank_values = np.asarray(blank)
        trial_count = len(positions)
        null_median = float(np.median(blank_values))
        null_sigma = robust_sigma(blank_values)
        # Real blank-position amplitudes are strongly non-Gaussian because of
        # confusion and unmasked artifacts.  Never let a Gaussian shorthand
        # set a threshold below the empirical 99th percentile.
        threshold = max(
            null_median + DETECTION_SIGMA * null_sigma,
            float(np.percentile(blank_values, 99.0)),
        )
        false_positives = int(np.sum(blank_values >= threshold))
        trials = []
        for mu0 in CENTRAL_SURFACE_BRIGHTNESS_MAG:
            injected_flux = total_flux_njy(mu0, effective_radius)
            recovered = []
            for x, y in positions:
                amplitude, _ = fit_template_amplitude(
                    image, variance, common, template, x, y, injection_flux_njy=injected_flux
                )
                recovered.append(amplitude - null_median)
            recovered_values = np.asarray(recovered)
            fractional_error = recovered_values / injected_flux - 1.0
            detected = recovered_values + null_median >= threshold
            accurate = np.abs(fractional_error) <= RECOVERY_TOLERANCE
            successes = int(np.sum(detected & accurate))
            trials.append(
                {
                    "centralSurfaceBrightnessMagArcsec2": mu0,
                    "injectedTotalFluxNjy": injected_flux,
                    "detectedFraction": float(detected.mean()),
                    "recoveredWithin25PercentFraction": float(accurate.mean()),
                    "completeFraction": successes / trial_count,
                    "completeFractionWilson68": wilson_interval(successes, trial_count),
                    "medianFractionalBias": float(np.median(fractional_error)),
                    "robustFractionalScatter": robust_sigma(fractional_error),
                }
            )
        passing = [
            trial["centralSurfaceBrightnessMagArcsec2"]
            for trial in trials
            if trial["completeFraction"] >= COMPLETENESS_TARGET
        ]
        size_records.append(
            {
                "effectiveRadiusArcsec": effective_radius,
                "status": "measured",
                "axisRatio": AXIS_RATIO,
                "templateRadiusPixels": radius,
                "templateEnclosedFluxFraction": enclosed_fraction,
                "validInjectionPositions": len(positions),
                "nullMedianNjy": null_median,
                "nullRobustSigmaNjy": null_sigma,
                "medianFormalUncertaintyNjy": float(np.median(formal)),
                "empiricalToFormalNoiseRatio": float(null_sigma / np.median(formal)),
                "detectionThresholdNjy": threshold,
                "nullFalsePositiveFraction": false_positives / trial_count,
                "faintest90PercentCompleteMu0MagArcsec2": max(passing) if passing else None,
                "trials": trials,
            }
        )
    return {"sizes": size_records}


def validate_target(comparison_key: str, coverage: dict, args: argparse.Namespace) -> dict:
    output_dir = args.comparisons / comparison_key
    reconciliation_path = output_dir / "reconciliation.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    slug = reconciliation["objectId"]
    if reconciliation.get("status") == "blocked" or not reconciliation.get("products", {}).get("matchedPair"):
        return {
            "schemaVersion": 1,
            "objectId": slug,
            "status": "blocked",
            "reason": "A passing matched image pair is required before diffuse injection/recovery.",
        }
    matched_path = Path(reconciliation["products"]["matchedPair"])
    with fits.open(matched_path, memmap=False) as hdus:
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        rubin_variance = np.asarray(hdus["RUBIN_VAR"].data, dtype=np.float64)
        comparison = np.asarray(hdus["COMPARISON"].data, dtype=np.float64)
        comparison_variance = np.asarray(hdus["COMPARISON_VAR"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data, dtype=bool)
        pixel_scale = float(hdus["RUBIN"].header["PIXSCALE"])
    psf_fwhm = float(reconciliation["psf"]["targetFwhmArcsec"])
    central_exclusion = max(coverage[slug]["major_axis_arcmin"] * 60 / pixel_scale * 1.5, 60)
    seed = int(hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8], 16)
    layers = {
        "rubin-dp2-deep-coadd": validate_layer(
            rubin, rubin_variance, common, pixel_scale, psf_fwhm, central_exclusion, seed
        ),
        reconciliation["layerIds"][1]: validate_layer(
            comparison,
            comparison_variance,
            common,
            pixel_scale,
            psf_fwhm,
            central_exclusion,
            seed,
        ),
    }
    false_positive_pass = all(
        size["nullFalsePositiveFraction"] <= 0.05
        for layer in layers.values()
        for size in layer["sizes"]
        if size.get("status") == "measured"
    )
    any_recovery = all(
        any(size["faintest90PercentCompleteMu0MagArcsec2"] is not None for size in layer["sizes"])
        for layer in layers.values()
    )
    result = {
        "schemaVersion": 1,
        "objectId": slug,
        "createdAt": stable_created_at(output_dir / "diffuse-recovery.json"),
        "status": "pass" if false_positive_pass and any_recovery else "qa-failed",
        "layerIds": reconciliation["layerIds"],
        "band": reconciliation["band"],
        "sourceMatchedPairSha256": sha256(matched_path),
        "model": {
            "profile": "elliptical exponential convolved with the matched Gaussian PSF",
            "effectiveRadiiArcsec": list(RADII_ARCSEC),
            "centralSurfaceBrightnessGridAbMagArcsec2": list(CENTRAL_SURFACE_BRIGHTNESS_MAG),
            "axisRatio": AXIS_RATIO,
            "trialsPerGridPoint": TRIALS,
            "minimumTrialsPerGridPoint": MIN_TRIALS,
            "detectionThresholdEmpiricalSigma": DETECTION_SIGMA,
            "recoveryToleranceFraction": RECOVERY_TOLERANCE,
            "completenessTarget": COMPLETENESS_TARGET,
            "positionPolicy": "up to 64 deterministic random common-mask positions outside 1.5 optical major axes; a grid point is valid only with at least 32 positions",
            "backgroundModel": "local constant plus x/y plane fitted simultaneously with the source",
        },
        "layers": layers,
        "nullTestPass": false_positive_pass,
        "recoveryGridPass": any_recovery,
        "quantitativeDiffuseLimitAvailable": false_positive_pass and any_recovery,
        "limitations": [
            "Injected profiles are smooth exponentials and do not span streams, shells, cirrus, or irregular tidal debris.",
            "The validation uses the reconciled products and therefore measures detection/photometry performance after sky subtraction; it does not retest the upstream coadd sky model.",
            "Empirical blank-position scatter includes correlated noise and confusion in this field; 32-64 positions per size do not characterize rare artifacts.",
            "A recovery limit validates detectability, not cross-survey filter transfer or the astrophysical origin of a residual.",
            "A source size is reported as insufficient-common-footprint when the overlap cannot hold at least 32 valid blank injections; no limit is extrapolated for that size.",
        ],
    }
    output_path = output_dir / "diffuse-recovery.json"
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["auditSha256"] = sha256(output_path)
    reconciliation["injectionRecovery"] = {
        "status": result["status"],
        "nullTestPass": false_positive_pass,
        "recoveryGridPass": any_recovery,
        "audit": str(output_path.resolve()),
        "auditSha256": result["auditSha256"],
        "model": result["model"],
    }
    reconciliation_path.write_text(json.dumps(reconciliation, indent=2), encoding="utf-8")
    return result


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline" / "results" / "dp2-sparc-coverage.json")
    parser.add_argument("--comparisons", type=Path, default=root / "pipeline" / "output" / "comparisons")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    coverage = {item["slug"]: item for item in json.loads(args.coverage.read_text(encoding="utf-8"))["targets"]}
    comparison_keys = sorted(path.parent.name for path in args.comparisons.glob("*/reconciliation.json"))
    if args.only:
        selected = set(args.only)
        comparison_keys = [key for key in comparison_keys if json.loads((args.comparisons / key / "reconciliation.json").read_text(encoding="utf-8")).get("objectId") in selected]
    summary = []
    for comparison_key in comparison_keys:
        result = validate_target(comparison_key, coverage, args)
        if result["status"] == "blocked":
            (args.comparisons / comparison_key / "diffuse-recovery.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
        summary.append({"comparisonKey": comparison_key, "objectId": result["objectId"], "status": result["status"]})
        print(f"[{comparison_key}] {result['status']}", flush=True)
    (args.comparisons / "diffuse-recovery-summary.json").write_text(
        json.dumps({"schemaVersion": 1, "targets": summary}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
