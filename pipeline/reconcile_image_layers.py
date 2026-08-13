#!/usr/bin/env python3
"""Create reproducible sky-subtracted, registered, PSF-matched image pairs.

The output is deliberately *not* declared photometrically comparable while the
survey bandpasses remain unreconciled.  It is the calibrated intermediate that
later filter-response, measurement, and injection/recovery stages consume.
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
from astropy.wcs import WCS
from scipy.ndimage import binary_erosion, convolve1d, gaussian_filter, shift

from audit_layer_registration import centroid_sources, fit_sky_plane, match_sources, robust_sigma
from gaia_registration import gaia_epoch_registration, product_epochs


NANOMAGGY_TO_NJY = 3630.780547701
PSF_TOLERANCE_FRACTION = 0.10


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


def gaussian_kernel(sigma: float) -> np.ndarray:
    if sigma <= 0.01:
        return np.array([1.0], dtype=np.float64)
    radius = max(1, int(math.ceil(4 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    return kernel / kernel.sum()


def normalized_convolution(
    image: np.ndarray, variance: np.ndarray, valid: np.ndarray, sigma: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Convolve signal and independent-pixel variance without filling masks."""
    kernel = gaussian_kernel(sigma)
    radius = (len(kernel) - 1) // 2
    if radius == 0:
        return image.copy(), variance.copy(), valid.copy(), radius
    weight = valid.astype(np.float64)
    numerator = convolve1d(convolve1d(np.where(valid, image, 0.0), kernel, axis=0), kernel, axis=1)
    normalization = convolve1d(convolve1d(weight, kernel, axis=0), kernel, axis=1)
    variance_numerator = convolve1d(
        convolve1d(np.where(valid, variance, 0.0), kernel**2, axis=0), kernel**2, axis=1
    )
    output_valid = normalization > 0.995
    output_valid &= binary_erosion(valid, iterations=radius, border_value=0)
    output = np.full(image.shape, np.nan, dtype=np.float32)
    output_variance = np.full(image.shape, np.nan, dtype=np.float32)
    output[output_valid] = (numerator[output_valid] / normalization[output_valid]).astype(np.float32)
    output_variance[output_valid] = (
        variance_numerator[output_valid] / normalization[output_valid] ** 2
    ).astype(np.float32)
    return output, output_variance, output_valid, radius


def shifted_comparison(
    image: np.ndarray, variance: np.ndarray, valid: np.ndarray, dx: float, dy: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    shifted_image = shift(np.where(valid, image, 0.0), (dy, dx), order=3, mode="constant", cval=0.0, prefilter=True)
    shifted_weight = shift(valid.astype(np.float32), (dy, dx), order=1, mode="constant", cval=0.0, prefilter=False)
    shifted_variance = shift(np.where(valid, variance, 0.0), (dy, dx), order=1, mode="constant", cval=0.0, prefilter=False)
    output_valid = shifted_weight > 0.999
    output_image = np.full(image.shape, np.nan, dtype=np.float64)
    output_variance = np.full(image.shape, np.nan, dtype=np.float64)
    output_image[output_valid] = shifted_image[output_valid] / shifted_weight[output_valid]
    output_variance[output_valid] = shifted_variance[output_valid] / shifted_weight[output_valid] ** 2
    return output_image, output_variance, output_valid


def read_rubin(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, fits.Header]:
    with fits.open(path, memmap=False) as hdus:
        image = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
        variance = np.asarray(hdus["VARIANCE"].data, dtype=np.float64)
        header = hdus["IMAGE"].header.copy()
    valid = np.isfinite(image) & np.isfinite(variance) & (variance > 0)
    return image, variance, valid, header


def read_comparison(path: Path, layer_id: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with fits.open(path, memmap=False) as hdus:
        image = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
        if layer_id == "legacy-survey-dr10":
            image *= NANOMAGGY_TO_NJY
            ivar = np.asarray(hdus["IVAR"].data, dtype=np.float64) / NANOMAGGY_TO_NJY**2
            variance = np.full(image.shape, np.nan, dtype=np.float64)
            good = np.isfinite(ivar) & (ivar > 0)
            variance[good] = 1.0 / ivar[good]
        else:
            variance = np.asarray(hdus["VARIANCE"].data, dtype=np.float64)
            mask = np.asarray(hdus["MASK"].data)
            variance[mask != 0] = np.nan
    valid = np.isfinite(image) & np.isfinite(variance) & (variance > 0)
    return image, variance, valid


def write_pair(
    path: Path,
    header: fits.Header,
    rubin: np.ndarray,
    rubin_variance: np.ndarray,
    comparison: np.ndarray,
    comparison_variance: np.ndarray,
    rubin_valid: np.ndarray,
    comparison_valid: np.ndarray,
    common: np.ndarray,
    metadata: dict,
) -> None:
    science_header = header.copy()
    science_header["BUNIT"] = "nJy"
    science_header["LAYERQA"] = ("MATCHED", "Layers reconciliation stage")
    science_header["FILTERMT"] = (False, "Filter response has not been reconciled")
    science_header["PSFMATCH"] = (True, "Both sides convolved to common Gaussian FWHM")
    science_header["SKYMATCH"] = (True, "Robust fitted sky planes subtracted")
    variance_header = header.copy()
    variance_header["BUNIT"] = "nJy^2"
    difference = np.full(rubin.shape, np.nan, dtype=np.float32)
    difference_variance = np.full(rubin.shape, np.nan, dtype=np.float32)
    difference[common] = (rubin[common] - comparison[common]).astype(np.float32)
    difference_variance[common] = (rubin_variance[common] + comparison_variance[common]).astype(np.float32)
    primary = fits.PrimaryHDU()
    for key, value in metadata.items():
        primary.header[key] = value
    hdus = [
        primary,
        fits.ImageHDU(rubin.astype(np.float32), header=science_header, name="RUBIN"),
        fits.ImageHDU(rubin_variance.astype(np.float32), header=variance_header, name="RUBIN_VAR"),
        fits.ImageHDU(comparison.astype(np.float32), header=science_header, name="COMPARISON"),
        fits.ImageHDU(comparison_variance.astype(np.float32), header=variance_header, name="COMPARISON_VAR"),
        fits.ImageHDU(rubin_valid.astype(np.uint8), name="RUBIN_MASK"),
        fits.ImageHDU(comparison_valid.astype(np.uint8), name="COMPARISON_MASK"),
        fits.ImageHDU(common.astype(np.uint8), name="COMMON_MASK"),
        fits.ImageHDU(difference, header=science_header, name="DIFFERENCE"),
        fits.ImageHDU(difference_variance, header=variance_header, name="DIFF_VAR"),
    ]
    # CHECKSUM cards produced by the installed Astropy/CFITSIO stack are not
    # byte-stable across otherwise identical writes.  These generated
    # intermediates already receive an external SHA-256 in reconciliation.json,
    # so omit FITS CHECKSUM cards and replace the file cleanly to keep release
    # provenance byte-for-byte reproducible.
    path.unlink(missing_ok=True)
    fits.HDUList(hdus).writeto(path)


def reconcile_one(root: Path, audit_path: Path, coverage: dict, args: argparse.Namespace) -> dict:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    slug = audit["objectId"]
    band = audit["band"]
    comparison_layer = audit["layerIds"][1]
    if comparison_layer == "legacy-survey-dr10":
        comparison_path = args.legacy_root / slug / f"legacy_{band}.fits"
    elif comparison_layer == "panstarrs-dr1-stack":
        comparison_path = args.panstarrs_root / slug / f"panstarrs_{band}.fits"
    else:
        raise ValueError(f"unsupported comparison layer {comparison_layer}")
    rubin_path = args.rubin_root / slug / f"rubin_{band}.fits"
    comparison_key = audit.get("comparisonKey", audit_path.parent.name)
    output_dir = args.output / comparison_key
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "matched-pair.fits"
    if not audit.get("astrometryPass"):
        result = {
            "schemaVersion": 1,
            "objectId": slug,
            "status": "blocked",
            "reason": "The measured astrometric residual exceeds the predeclared 0.30 arcsec p95 threshold.",
            "sourceAudit": str(audit_path.resolve()),
        }
        (output_dir / "reconciliation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    rubin, rubin_variance, rubin_valid, header = read_rubin(rubin_path)
    comparison, comparison_variance, comparison_valid = read_comparison(comparison_path, comparison_layer)
    pixel_scale = float(header["PIXSCALE"])
    offset = audit["sourceRegistration"]["medianOffsetArcsec"]
    dx, dy = offset["x"] / pixel_scale, offset["y"] / pixel_scale
    comparison, comparison_variance, comparison_valid = shifted_comparison(
        comparison, comparison_variance, comparison_valid, dx, dy
    )
    initial_common = rubin_valid & comparison_valid
    exclusion = max(coverage[slug]["major_axis_arcmin"] * 60 / pixel_scale * 1.5, 60)
    rubin_sky, rubin_sky_record = fit_sky_plane(rubin, initial_common, exclusion)
    comparison_sky, comparison_sky_record = fit_sky_plane(comparison, initial_common, exclusion)
    rubin -= rubin_sky
    comparison -= comparison_sky

    rubin_fwhm = float(audit["sourceRegistration"]["rubinMedianFwhmArcsec"])
    comparison_fwhm = float(audit["sourceRegistration"]["comparisonMedianFwhmArcsec"])
    target_fwhm = max(rubin_fwhm, comparison_fwhm) * 1.05
    gaussian_factor = 2.354820045
    rubin_sigma = math.sqrt(max(0.0, target_fwhm**2 - rubin_fwhm**2)) / gaussian_factor / pixel_scale
    comparison_sigma = math.sqrt(max(0.0, target_fwhm**2 - comparison_fwhm**2)) / gaussian_factor / pixel_scale
    rubin, rubin_variance, rubin_valid, rubin_radius = normalized_convolution(
        rubin, rubin_variance, rubin_valid, rubin_sigma
    )
    comparison, comparison_variance, comparison_valid, comparison_radius = normalized_convolution(
        comparison, comparison_variance, comparison_valid, comparison_sigma
    )
    common = rubin_valid & comparison_valid
    # Keep each registered layer's own valid footprint for honest display. All
    # cross-survey calculations remain strictly gated by COMMON_MASK below.
    rubin[~rubin_valid] = np.nan
    rubin_variance[~rubin_valid] = np.nan
    comparison[~comparison_valid] = np.nan
    comparison_variance[~comparison_valid] = np.nan

    post_rubin_sources = centroid_sources(rubin, common, exclusion, pixel_scale)
    post_comparison_sources = centroid_sources(comparison, common, exclusion, pixel_scale)
    post_registration = match_sources(post_rubin_sources, post_comparison_sources, pixel_scale)
    if comparison_layer == "panstarrs-dr1-stack":
        corrected_post_registration = gaia_epoch_registration(
            post_rubin_sources,
            post_comparison_sources,
            WCS(header),
            pixel_scale,
            root / "pipeline/cache/gaia-dr3" / f"{slug}.csv",
            product_epochs(root, slug, band),
            root,
        )
        if corrected_post_registration:
            post_registration = {
                **corrected_post_registration,
                "uncorrectedSourceRegistration": post_registration,
            }
    post_rubin_fwhm = post_registration.get("rubinMedianFwhmArcsec")
    post_comparison_fwhm = post_registration.get("comparisonMedianFwhmArcsec")
    psf_difference = None
    if post_rubin_fwhm and post_comparison_fwhm:
        psf_difference = abs(post_rubin_fwhm - post_comparison_fwhm) / max(post_rubin_fwhm, post_comparison_fwhm)
    psf_pass = post_registration.get("matchedSources", 0) >= 5 and psf_difference is not None and psf_difference <= PSF_TOLERANCE_FRACTION

    _, rubin_post_sky = fit_sky_plane(rubin, common, exclusion)
    _, comparison_post_sky = fit_sky_plane(comparison, common, exclusion)
    difference_values = (rubin - comparison)[common]
    difference_sigma = robust_sigma(difference_values)
    sky_tolerance = max(1.0, 0.10 * difference_sigma)
    sky_offset = abs(float(np.median(difference_values)))
    sky_pass = np.isfinite(sky_offset) and sky_offset <= sky_tolerance

    write_pair(
        output_path,
        header,
        rubin,
        rubin_variance,
        comparison,
        comparison_variance,
        rubin_valid,
        comparison_valid,
        common,
        {
            "OBJECT": slug,
            "BAND": band,
            "CMPLAYER": comparison_layer,
            "PSFFWHM": (target_fwhm, "Common Gaussian target FWHM, arcsec"),
        },
    )
    status = "matched-not-photometrically-comparable" if psf_pass and sky_pass else "qa-failed"
    result = {
        "schemaVersion": 1,
        "objectId": slug,
        "comparisonKey": comparison_key,
        "createdAt": stable_created_at(output_dir / "reconciliation.json"),
        "status": status,
        "layerIds": audit["layerIds"],
        "comparisonLayerLabel": audit["comparisonLayerLabel"],
        "band": band,
        "products": {
            "matchedPair": str(output_path.resolve()),
            "matchedPairSha256": sha256(output_path),
            "sourceRubin": str(rubin_path.resolve()),
            "sourceRubinSha256": sha256(rubin_path),
            "sourceComparison": str(comparison_path.resolve()),
            "sourceComparisonSha256": sha256(comparison_path),
        },
        "registration": {
            "commonWcs": True,
            "commonFootprint": float(common.mean()) > 0.5,
            "commonValidPixelFraction": float(common.mean()),
            "astrometryThresholdArcsec": audit["astrometryThresholdArcsec"],
            "astrometryPass": post_registration.get("residualP95Arcsec") is not None
            and post_registration["residualP95Arcsec"] <= audit["astrometryThresholdArcsec"],
            "sourceRegistration": post_registration,
            "appliedComparisonShiftPixels": {"x": dx, "y": dy},
        },
        "units": {"matched": True, "unit": "nJy", "varianceUnit": "nJy^2"},
        "sky": {
            "matched": bool(sky_pass),
            "rubinSubtractedPlaneNjy": rubin_sky_record,
            "comparisonSubtractedPlaneNjy": comparison_sky_record,
            "postMatchRubinPlaneNjy": rubin_post_sky,
            "postMatchComparisonPlaneNjy": comparison_post_sky,
            "differenceMedianNjy": float(np.median(difference_values)),
            "differenceRobustSigmaNjy": difference_sigma,
            "medianToleranceNjy": sky_tolerance,
        },
        "psf": {
            "matched": bool(psf_pass),
            "model": "circular Gaussian approximation to empirical stellar FWHM",
            "targetFwhmArcsec": target_fwhm,
            "rubinKernelSigmaPixels": rubin_sigma,
            "comparisonKernelSigmaPixels": comparison_sigma,
            "rubinKernelRadiusPixels": rubin_radius,
            "comparisonKernelRadiusPixels": comparison_radius,
            "postMatchFractionalFwhmDifference": psf_difference,
            "toleranceFraction": PSF_TOLERANCE_FRACTION,
        },
        "filterResponse": {
            "matched": False,
            "reason": "Nominally similar band names do not establish equal throughput; a color transform or synthetic-photometry model is still required.",
        },
        "quantitativeDifferenceAllowed": False,
        "limitations": [
            "The Gaussian PSF model does not capture spatially varying PSF wings.",
            "Resampling and convolution introduce covariance not represented by the per-pixel variance planes.",
            "The survey filter responses are not yet reconciled, so DIFFERENCE is a QA plane and must not be interpreted as missing light.",
        ],
    }
    (output_dir / "reconciliation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline" / "results" / "dp2-sparc-coverage.json")
    parser.add_argument("--audits", type=Path, default=root / "pipeline" / "output" / "comparisons")
    parser.add_argument("--rubin-root", type=Path, default=root / "pipeline" / "output" / "dp2-sparc")
    parser.add_argument("--legacy-root", type=Path, default=root / "pipeline" / "output" / "legacy-survey")
    parser.add_argument("--panstarrs-root", type=Path, default=root / "pipeline" / "output" / "panstarrs")
    parser.add_argument("--output", type=Path, default=root / "pipeline" / "output" / "comparisons")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    coverage = {item["slug"]: item for item in json.loads(args.coverage.read_text(encoding="utf-8"))["targets"]}
    audit_paths = sorted(args.audits.glob("*/registration-audit.json"))
    if args.only:
        audit_paths = [path for path in audit_paths if json.loads(path.read_text(encoding="utf-8")).get("objectId") in set(args.only)]
    summary = []
    for audit_path in audit_paths:
        result = reconcile_one(root, audit_path, coverage, args)
        summary.append({"objectId": result["objectId"], "status": result["status"]})
        print(f"[{result['objectId']}] {result['status']}")
    summary_path = args.output / "reconciliation-summary.json"
    summary_path.write_text(json.dumps({"schemaVersion": 1, "targets": summary}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
