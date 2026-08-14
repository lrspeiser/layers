#!/usr/bin/env python3
"""Measure diffuse detection limits per selected region by injection and recovery.

The SPARC pilots proved this stage on three fields; this runner applies it to
every reconciled DP2 region. Artificial exponential sources are inserted into
the real matched pixels at random blank positions and refitted, so the reported
threshold absorbs confusion, unmasked artifacts, sky residuals, and the pixel
covariance introduced by reprojection and PSF matching.

Two numbers here do work that nothing else in the pipeline does:

* ``faintest90PercentCompleteMu0MagArcsec2`` is the limiting surface brightness.
  Without it, a residual has no stated detectability and cannot be defended.
* ``empiricalToFormalNoiseRatio`` compares the measured scatter of blank-position
  fits against the propagated per-pixel variance. It is a direct measurement of
  the resampling-covariance blocker: a ratio above one means the variance planes
  understate the real uncertainty by that factor.

This validates the measurement stage. It does not reconcile the bandpasses and
does not turn any residual into a missing-light claim.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from scipy.ndimage import binary_dilation, gaussian_filter

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_layer_registration import robust_sigma
from validate_diffuse_recovery import validate_layer

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "pipeline/results/reconciled-regions/manifest.json"
DEFAULT_OUTPUT = ROOT / "pipeline/results/region-recovery"
DEFAULT_PUBLIC = ROOT / "public/data/layers/selected-regions/region-diffuse-recovery.json"

# A tract field has no single central object to exclude, unlike the SPARC pilots,
# so there is no meaningful central exclusion radius.
CENTRAL_EXCLUSION_PIXELS = 0.0

# The method assumes blank positions are blank. A 4 arcmin tract cutout is full
# of real sources, and drawing "null" positions on top of them makes the measured
# null distribution a measurement of the galaxy population rather than of the
# noise. Detected sources are therefore masked out of the injection footprint.
SOURCE_DETECTION_SIGMA = 2.5
SOURCE_MASK_GROWTH_PIXELS = 3


def mask_sources_in_variance(
    image: np.ndarray, variance: np.ndarray, common: np.ndarray
) -> tuple[np.ndarray, dict[str, Any]]:
    """Drop detected sources from every template fit without fragmenting placement.

    ``choose_positions`` requires 95% of a template box to lie inside the mask it
    is given, so removing scattered source pixels from that mask makes placement
    impossible in a crowded tract field. ``fit_template_amplitude`` independently
    discards pixels whose variance is not finite and positive, so marking source
    pixels there excludes them from the fit while leaving the placement footprint
    contiguous.
    """
    filled = np.where(common & np.isfinite(image), image, 0.0)
    detection = gaussian_filter(filled, 1.5) - gaussian_filter(filled, 12.0)
    sigma = robust_sigma(detection[common])
    if not np.isfinite(sigma) or sigma <= 0:
        return variance, {"applied": False, "reason": "no finite detection scatter"}
    sources = binary_dilation(
        np.abs(detection) > SOURCE_DETECTION_SIGMA * sigma,
        iterations=SOURCE_MASK_GROWTH_PIXELS,
    )
    masked = variance.copy()
    masked[sources & common] = np.nan
    usable = common & ~sources
    return masked, {
        "applied": True,
        "detectionSigma": SOURCE_DETECTION_SIGMA,
        "growthPixels": SOURCE_MASK_GROWTH_PIXELS,
        "skyFractionOfCommon": float(usable.sum() / max(int(common.sum()), 1)),
        "note": (
            "Detected sources are excluded from every template fit via the variance plane, so blank "
            "positions measure sky rather than the galaxy population."
        ),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def layer_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Reduce the per-size records to the numbers that gate a published claim."""
    measured = [item for item in result["sizes"] if item.get("status") == "measured"]
    limits = {
        str(item["effectiveRadiusArcsec"]): item["faintest90PercentCompleteMu0MagArcsec2"]
        for item in measured
    }
    usable = [value for value in limits.values() if value is not None]
    ratios = [item["empiricalToFormalNoiseRatio"] for item in measured if item.get("empiricalToFormalNoiseRatio")]
    false_positives = [item["nullFalsePositiveFraction"] for item in measured]
    return {
        "measuredSizes": len(measured),
        "skippedSizes": len(result["sizes"]) - len(measured),
        "limitingMu0ByEffectiveRadiusArcsec": limits,
        "deepestMu0MagArcsec2": max(usable) if usable else None,
        "medianEmpiricalToFormalNoiseRatio": float(np.median(ratios)) if ratios else None,
        "maxNullFalsePositiveFraction": max(false_positives) if false_positives else None,
    }


def validate_region(record: dict[str, Any], output: Path, seed: int) -> dict[str, Any]:
    path = ROOT / record["products"]["matchedPair"]
    with fits.open(path, memmap=False, checksum=True) as hdus:
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        rubin_variance = np.asarray(hdus["RUBIN_VARIANCE"].data, dtype=np.float64)
        reference = np.asarray(hdus["REFERENCE"].data, dtype=np.float64)
        reference_variance = np.asarray(hdus["REFERENCE_VARIANCE"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data, dtype=np.uint8) > 0

    pixel_scale = float(record["pixelScaleArcsec"])
    psf_fwhm = float(record["psf"]["targetFwhmArcsec"])

    layers = {}
    for name, image, variance in (
        ("rubin", rubin, rubin_variance),
        ("reference", reference, reference_variance),
    ):
        sky_variance, mask_record = mask_sources_in_variance(image, variance, common)
        result = validate_layer(
            image,
            sky_variance,
            common,
            pixel_scale,
            psf_fwhm,
            CENTRAL_EXCLUSION_PIXELS,
            seed,
        )
        layers[name] = {
            "summary": {**layer_summary(result), "sourceMask": mask_record},
            "sizes": result["sizes"],
        }

    payload = {
        "schemaVersion": "layers-region-diffuse-recovery-v1",
        "regionId": record["regionId"],
        "tract": record["tract"],
        "generatedAt": utc_now(),
        "rubinBand": record["rubinBand"],
        "referenceBand": record["referenceBand"],
        "referenceSurveyId": record["referenceSurveyId"],
        "pixelScaleArcsec": pixel_scale,
        "matchedPsfFwhmArcsec": psf_fwhm,
        "commonPixelFraction": record["commonPixelFraction"],
        "layers": layers,
        "interpretation": (
            "Limits are measured on these matched pixels only. They state what a smooth exponential "
            "source of the given size would need to be to be recovered 90% of the time, not that any "
            "detected structure is real or that the two surveys are photometrically comparable."
        ),
    }
    region_dir = output / record["regionId"]
    region_dir.mkdir(parents=True, exist_ok=True)
    (region_dir / "diffuse-recovery.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def public_record(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in payload.items() if key != "layers"
    } | {
        "layers": {name: layer["summary"] for name, layer in payload["layers"].items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--only-region", action="append", default=[])
    parser.add_argument("--seed", type=int, default=20260814)
    parser.add_argument(
        "--require-matched",
        action="store_true",
        help="Only measure regions whose reconciliation passed every QA gate.",
    )
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    only = {value.strip() for value in args.only_region if value.strip()}
    records = []
    failures = []
    for record in payload["regions"]:
        if only and record["regionId"] not in only:
            continue
        if args.require_matched and record["status"] != "matched":
            continue
        try:
            result = validate_region(record, args.output, args.seed)
        except Exception as error:  # noqa: BLE001 - report, never silently drop
            failures.append({"regionId": record["regionId"], "error": f"{type(error).__name__}: {error}"})
            print(f"[failed] {record['regionId']}: {type(error).__name__}: {error}", flush=True)
            continue
        records.append(result)
        rubin = result["layers"]["rubin"]["summary"]
        reference = result["layers"]["reference"]["summary"]
        print(
            f"[measured] {record['regionId']} rubin_mu0={rubin['deepestMu0MagArcsec2']} "
            f"ref_mu0={reference['deepestMu0MagArcsec2']} "
            f"noise_ratio={rubin['medianEmpiricalToFormalNoiseRatio']:.2f}"
            if rubin["medianEmpiricalToFormalNoiseRatio"]
            else f"[measured] {record['regionId']}",
            flush=True,
        )

    ratios = [
        item["layers"]["rubin"]["summary"]["medianEmpiricalToFormalNoiseRatio"]
        for item in records
        if item["layers"]["rubin"]["summary"]["medianEmpiricalToFormalNoiseRatio"]
    ]
    summary = {
        "schemaVersion": "layers-region-diffuse-recovery-summary-v1",
        "generatedAt": utc_now(),
        "counts": {"measured": len(records), "failed": len(failures)},
        "resamplingCovariance": {
            "measured": bool(ratios),
            "medianRubinEmpiricalToFormalNoiseRatio": float(np.median(ratios)) if ratios else None,
            "note": (
                "Ratio of the measured blank-position scatter to the propagated per-pixel uncertainty. "
                "Values above 1 quantify how far the independent-pixel variance planes understate the "
                "true uncertainty after reprojection and PSF matching."
            ),
        },
        "failures": failures,
        "regions": [public_record(item) for item in records],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nmeasured {len(records)} regions, {len(failures)} failed", flush=True)
    if ratios:
        print(f"median Rubin empirical/formal noise ratio: {np.median(ratios):.3f}", flush=True)


if __name__ == "__main__":
    main()
