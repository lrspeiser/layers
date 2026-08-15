#!/usr/bin/env python3
"""Apply the pilot reconciliation stage to every selected DP2 region.

``build_selected_region_comparisons.py`` puts Rubin and one reference survey on
a shared celestial grid and stops there, so all 50 regions carry six comparison
blockers.  This runner consumes those display grids and applies the operations
that ``reconcile_image_layers.py`` validated on the SPARC pilots:

* flux-unit transfer into nJy, using each survey's documented chain and an
  independent empirical point-source scale as a cross-check;
* background matching, by fitting and subtracting a robust sigma-clipped plane
  from each side over the common footprint;
* PSF matching, by measuring empirical stellar FWHM on both sides and
  convolving the sharper layer to a common Gaussian target;
* a post-match registration audit against the declared 0.30 arcsec threshold.

It deliberately does **not** clear bandpass transfer or injection/recovery QA.
Those remain open blockers and the outputs stay ``comparisonReady: false``.
The difference plane written here is a QA product, not missing light.
"""

from __future__ import annotations

import argparse
import hashlib
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_layer_registration import centroid_sources, fit_sky_plane, match_sources, robust_sigma
from reconcile_image_layers import normalized_convolution, shifted_comparison

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "pipeline/results/selected-region-comparisons/manifest.json"
DEFAULT_PS1_EVIDENCE = ROOT / "pipeline/results/panstarrs-gap-fill/evidence/manifest.json"
DEFAULT_RUBIN_MANIFEST = ROOT / "pipeline/results/rubin-pixels-50/manifest.json"
DEFAULT_PRODUCTS = ROOT / "pipeline/results/reconciled-regions"
DEFAULT_PUBLIC = ROOT / "public/data/layers/selected-regions/rubin-reference-reconciliation.json"

NANOMAGGY_TO_NJY = 3630.780547701
AB_ZERO_POINT_NJY = 3.63078054770e12  # 3631 Jy expressed in nJy
GAUSSIAN_FWHM_PER_SIGMA = 2.354820045

# Pixel scales at which each side's *values* are defined, which is not always the
# scale of the grid those values are sampled on.
#
# Legacy DR10 coadd bricks are sampled at 0.262 arcsec/pixel. The viewer cutout
# service rewrites the output WCS when pixscale= is requested but preserves the
# coadd pixel values, so a 0.4 arcsec/pixel cutout still carries nanomaggies per
# 0.262 arcsec pixel. fetch_legacy_survey.py applies this factor for the SPARC
# pilots; the acquisition-50 path (layer_connectors -> normalize_legacy_cutouts)
# never did, so it is applied here.
LEGACY_NATIVE_COADD_PIXEL_SCALE_ARCSEC = 0.262

PSF_TOLERANCE_FRACTION = 0.10
ASTROMETRY_THRESHOLD_ARCSEC = 0.30
MIN_MATCHED_SOURCES = 5

# The empirical compact-source flux ratio is the independent check on the two
# documented unit chains. It only corroborates them when it is measured well, so
# a region must clear all three of these before flux-unit transfer is called
# cleared. Scatter is the discriminator: well-measured fields sit near 0.035 dex,
# while fields with poor common coverage or blended matches run 0.08-0.22 dex.
MAX_FLUX_SCATTER_DEX = 0.06
MIN_FLUX_SOURCES = 20
MAX_FLUX_LOG_DEPARTURE = 0.10  # |log10(scale)|, about 0.25 mag

# Blockers this stage is capable of clearing. Bandpass transfer and
# injection/recovery QA are owned by later stages and are never cleared here.
CLEARABLE_BLOCKERS = ("PSF matching", "background matching", "flux-unit transfer")
RETAINED_BLOCKERS = ("bandpass transfer", "resampling covariance", "injection/recovery QA")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def pixel_area_factor(grid_scale_arcsec: float, value_scale_arcsec: float) -> float:
    """Rescale flux-per-pixel values onto the pixel area they are sampled on.

    Both the Rubin reprojection and the Legacy cutout service preserve surface
    brightness rather than total flux, so a plane whose values are defined per
    ``value_scale`` pixel but sampled on a ``grid_scale`` grid understates the
    flux in every fixed sky aperture by exactly this factor.
    """
    return (grid_scale_arcsec / value_scale_arcsec) ** 2


def reference_flux_chain(
    record: dict[str, Any],
    ps1_evidence: dict[str, dict[str, Any]],
    grid_scale_arcsec: float,
) -> dict[str, Any]:
    """Return the documented native-unit to nJy conversion for one reference.

    ``scale`` multiplies the reference image; ``verified`` states whether the
    chain rests on a published, unambiguous definition rather than on a header
    convention this project has not independently checked.
    """
    survey = record["referenceSurveyId"]
    if survey == "legacy-surveys-dr10":
        area = pixel_area_factor(grid_scale_arcsec, LEGACY_NATIVE_COADD_PIXEL_SCALE_ARCSEC)
        return {
            "surveyId": survey,
            "nativeUnit": "nanomaggy/pixel",
            "scale": NANOMAGGY_TO_NJY * area,
            "unitScale": NANOMAGGY_TO_NJY,
            "pixelAreaFactor": area,
            "valuePixelScaleArcsec": LEGACY_NATIVE_COADD_PIXEL_SCALE_ARCSEC,
            "gridPixelScaleArcsec": grid_scale_arcsec,
            "verified": True,
            "formula": "nJy per grid pixel = nanomaggy * 3630.780547701 * (grid/0.262)^2",
            "reference": (
                "AB zero point 3631 Jy; Legacy images are calibrated in nanomaggies. The viewer cutout "
                "service preserves 0.262 arcsec coadd values when a coarser pixscale is requested, so the "
                "pixel-area factor is required. Same convention as fetch_legacy_survey.py."
            ),
        }
    if survey == "des-dr2":
        # normalize_des_cutouts.py already applied the DES 30.0 AB zeropoint, so
        # the pixels arrive in nJy and no further conversion applies. There is
        # also no pixel-area factor: the DES cutout service returns coadd pixels
        # at the coadd scale, unlike the Legacy viewer.
        return {
            "surveyId": survey,
            "nativeUnit": "nJy",
            "scale": 1.0,
            "unitScale": 1.0,
            "pixelAreaFactor": 1.0,
            "valuePixelScaleArcsec": grid_scale_arcsec,
            "gridPixelScaleArcsec": grid_scale_arcsec,
            "verified": True,
            "formula": "already nJy; DES DR2 coadds carry a fixed 30.0 AB zeropoint",
            "reference": "Applied upstream by normalize_des_cutouts.py.",
        }
    if survey == "panstarrs-dr2":
        evidence = ps1_evidence.get(record["regionId"])
        exposure = None
        if evidence:
            for product in evidence.get("products", []):
                if product.get("role") != "science":
                    continue
                # The key is unitsValidation, not units. Reading the wrong one
                # returned no exposure for every PS1 region, and the failure was
                # recorded as "no EXPTIME recorded for this skycell" -- a claim
                # about the archive that was actually a claim about this line.
                # The headers carry it: 1092 s on the first region checked.
                calibration = (
                    product.get("validation", {})
                    .get("unitsValidation", {})
                    .get("magnitudeCalibration", {})
                )
                exposure = calibration.get("exposureTimeSeconds")
                if exposure:
                    break
        if not exposure:
            return {
                "surveyId": survey,
                "nativeUnit": "PS1 linear stack unit",
                "scale": None,
                "verified": False,
                "formula": None,
                "reference": "No EXPTIME recorded for this skycell; the stack unit cannot be placed on an absolute scale.",
            }
        # normalize_ps1_reference keeps the skycell on its own native grid, so the
        # values and the grid already share a pixel area and no factor applies.
        # m = -2.5 log10(DN) + 25 + 2.5 log10(EXPTIME)  =>  f_nJy = DN * 10^-10 / EXPTIME * 3.63078e12
        scale = AB_ZERO_POINT_NJY * 1e-10 / float(exposure)
        return {
            "surveyId": survey,
            "nativeUnit": "PS1 linear stack unit",
            "scale": scale,
            "unitScale": scale,
            "pixelAreaFactor": 1.0,
            "valuePixelScaleArcsec": grid_scale_arcsec,
            "gridPixelScaleArcsec": grid_scale_arcsec,
            "verified": False,
            "formula": "nJy = DN * 10^-10 / EXPTIME * 3.63078e12, from MAG = -2.5log10(DN) + 25 + 2.5log10(EXPTIME)",
            "reference": (
                "PS1 stack convention recorded by fetch_panstarrs_gap_fill.py. This project has not "
                "independently verified it against a calibrated PS1 photometric catalog, so the "
                "absolute chain is reported as unverified and the empirical scale governs."
            ),
        }
    return {
        "surveyId": survey,
        "nativeUnit": record["inputs"]["reference"]["unit"],
        "scale": None,
        "verified": False,
        "formula": None,
        "reference": "No documented conversion is registered for this survey.",
    }


def empirical_flux_scale(
    rubin: np.ndarray,
    reference: np.ndarray,
    rubin_sources: list[dict],
    reference_sources: list[dict],
    pixel_scale: float,
) -> dict[str, Any]:
    """Fit a single multiplicative scale from matched point-source aperture flux.

    This is an internal consistency check between two images of the same sky,
    not a photometric calibration of either survey. It is measured on compact
    sources, so it does not license an extended-source transfer.
    """
    if not rubin_sources or not reference_sources:
        return {"matchedSources": 0, "scale": None, "scatterDex": None}
    radius = max(2.0, 1.5 / pixel_scale)
    height, width = rubin.shape
    yy, xx = np.indices(rubin.shape)
    ratios = []
    reference_positions = np.array([[item["x"], item["y"]] for item in reference_sources])
    for source in rubin_sources:
        distances = np.hypot(reference_positions[:, 0] - source["x"], reference_positions[:, 1] - source["y"])
        if not distances.size or distances.min() > 3.0:
            continue
        aperture = (np.hypot(xx - source["x"], yy - source["y"]) <= radius)
        if not aperture.any():
            continue
        rubin_flux = float(np.nansum(rubin[aperture]))
        reference_flux = float(np.nansum(reference[aperture]))
        if rubin_flux <= 0 or reference_flux <= 0:
            continue
        ratios.append(rubin_flux / reference_flux)
    if len(ratios) < MIN_MATCHED_SOURCES:
        return {"matchedSources": len(ratios), "scale": None, "scatterDex": None}
    values = np.asarray(ratios, dtype=np.float64)
    logs = np.log10(values)
    keep = np.abs(logs - np.median(logs)) < 3.0 * max(robust_sigma(logs), 1e-6)
    kept = values[keep] if keep.sum() >= MIN_MATCHED_SOURCES else values
    return {
        "matchedSources": int(kept.size),
        "scale": float(np.median(kept)),
        "scatterDex": float(robust_sigma(np.log10(kept))),
        "apertureRadiusArcsec": round(radius * pixel_scale, 4),
        "note": "Median Rubin/reference aperture-flux ratio on matched compact sources after 3-sigma clipping in log space.",
    }


def reconcile_region(
    record: dict[str, Any],
    ps1_evidence: dict[str, dict[str, Any]],
    rubin_native_scales: dict[str, float],
    products: Path,
) -> dict[str, Any]:
    grid_path = ROOT / record["localFits"]["path"]
    if sha256(grid_path) != record["localFits"]["sha256"]:
        raise ValueError("display-grid checksum mismatch")

    with fits.open(grid_path, memmap=False, checksum=True) as hdus:
        rubin = np.asarray(hdus["RUBIN_IMAGE"].data, dtype=np.float64)
        rubin_variance = np.asarray(hdus["RUBIN_VARIANCE"].data, dtype=np.float64)
        reference = np.asarray(hdus["REFERENCE_IMAGE"].data, dtype=np.float64)
        reference_ivar = np.asarray(hdus["REFERENCE_IVAR"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_COVERAGE"].data, dtype=np.uint8) > 0
        header = hdus["RUBIN_IMAGE"].header.copy()
        wcs = WCS(header).celestial
    pixel_scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)

    rubin_valid = np.isfinite(rubin) & np.isfinite(rubin_variance) & (rubin_variance > 0)
    reference_valid = np.isfinite(reference) & np.isfinite(reference_ivar) & (reference_ivar > 0)
    reference_variance = np.full(reference.shape, np.nan, dtype=np.float64)
    reference_variance[reference_valid] = 1.0 / reference_ivar[reference_valid]

    # --- flux-unit transfer -------------------------------------------------
    # The upstream display grid reprojects Rubin with reproject_interp, which
    # preserves surface brightness, so the Rubin plane still carries nJy per
    # native mosaic pixel while being sampled on the reference grid.
    rubin_native_scale = rubin_native_scales.get(record["regionId"])
    if not rubin_native_scale:
        raise ValueError("no Rubin native pixel scale recorded for this region")
    rubin_area_factor = pixel_area_factor(pixel_scale, rubin_native_scale)
    rubin = rubin * rubin_area_factor
    # A pixel-area factor is a rebinning, not a multiplicative recalibration.
    # Summing A independent native pixels scales the value by A and the variance
    # by A, not by A^2. Resampling correlates neighbours so this is only the
    # independent-pixel approximation; validate_region_recovery.py measures how
    # far it is wrong via the empirical-to-formal noise ratio.
    rubin_variance = rubin_variance * rubin_area_factor
    rubin_chain = {
        "unit": "nJy",
        "pixelAreaFactor": rubin_area_factor,
        "valuePixelScaleArcsec": rubin_native_scale,
        "gridPixelScaleArcsec": pixel_scale,
        "verified": True,
        "reference": (
            "Rubin SODA MaskedImage cutouts are calibrated in nJy per mosaic pixel. reproject_interp in "
            "build_selected_region_comparisons.py preserves surface brightness, so flux per grid pixel "
            "requires this area factor."
        ),
    }

    chain = reference_flux_chain(record, ps1_evidence, pixel_scale)
    if chain["scale"] is None:
        raise ValueError(f"no documented flux chain for {record['referenceSurveyId']}")
    reference = reference * chain["scale"]
    # The unit conversion is a true recalibration and scales variance by its
    # square; the pixel-area factor is a rebinning and scales it linearly.
    reference_variance = reference_variance * chain["unitScale"] ** 2 * chain["pixelAreaFactor"]

    # --- background matching ------------------------------------------------
    working = rubin_valid & reference_valid & common
    if working.sum() < 1000:
        raise ValueError(f"only {int(working.sum())} common valid pixels")
    rubin_plane, rubin_sky_record = fit_sky_plane(rubin, working, 0.0)
    reference_plane, reference_sky_record = fit_sky_plane(reference, working, 0.0)
    rubin = rubin - rubin_plane
    reference = reference - reference_plane

    # --- PSF matching -------------------------------------------------------
    pre_rubin_sources = centroid_sources(rubin, working, 0.0, pixel_scale)
    pre_reference_sources = centroid_sources(reference, working, 0.0, pixel_scale)
    pre_registration = match_sources(pre_rubin_sources, pre_reference_sources, pixel_scale)
    rubin_fwhm = pre_registration.get("rubinMedianFwhmArcsec")
    reference_fwhm = pre_registration.get("comparisonMedianFwhmArcsec")
    if not rubin_fwhm or not reference_fwhm:
        raise ValueError(
            f"insufficient matched sources to measure PSF ({pre_registration.get('matchedSources', 0)} matched)"
        )

    scale_measurement = empirical_flux_scale(rubin, reference, pre_rubin_sources, pre_reference_sources, pixel_scale)

    # Registration. The upstream display grid reprojects Rubin onto the reference
    # WCS but never applies a measured offset, so any residual astrometric
    # difference between the two archives survives into the matched pair. The
    # pilots correct this the same way: shift the reference by the median
    # source-to-source offset before convolving.
    offset = pre_registration.get("medianOffsetArcsec") or {}
    dx = float(offset.get("x", 0.0) or 0.0) / pixel_scale
    dy = float(offset.get("y", 0.0) or 0.0) / pixel_scale
    applied_shift = {"xPixels": dx, "yPixels": dy, "applied": bool(dx or dy)}
    if applied_shift["applied"]:
        reference, reference_variance, reference_valid = shifted_comparison(
            reference, reference_variance, reference_valid, dx, dy
        )

    target_fwhm = max(rubin_fwhm, reference_fwhm) * 1.05
    rubin_sigma = math.sqrt(max(0.0, target_fwhm**2 - rubin_fwhm**2)) / GAUSSIAN_FWHM_PER_SIGMA / pixel_scale
    reference_sigma = math.sqrt(max(0.0, target_fwhm**2 - reference_fwhm**2)) / GAUSSIAN_FWHM_PER_SIGMA / pixel_scale
    rubin, rubin_variance, rubin_valid, rubin_radius = normalized_convolution(rubin, rubin_variance, rubin_valid, rubin_sigma)
    reference, reference_variance, reference_valid, reference_radius = normalized_convolution(
        reference, reference_variance, reference_valid, reference_sigma
    )
    matched_common = rubin_valid & reference_valid

    # --- post-match audit ---------------------------------------------------
    post_rubin_sources = centroid_sources(rubin, matched_common, 0.0, pixel_scale)
    post_reference_sources = centroid_sources(reference, matched_common, 0.0, pixel_scale)
    post_registration = match_sources(post_rubin_sources, post_reference_sources, pixel_scale)
    post_rubin_fwhm = post_registration.get("rubinMedianFwhmArcsec")
    post_reference_fwhm = post_registration.get("comparisonMedianFwhmArcsec")
    psf_difference = None
    if post_rubin_fwhm and post_reference_fwhm:
        psf_difference = abs(post_rubin_fwhm - post_reference_fwhm) / max(post_rubin_fwhm, post_reference_fwhm)
    psf_pass = (
        post_registration.get("matchedSources", 0) >= MIN_MATCHED_SOURCES
        and psf_difference is not None
        and psf_difference <= PSF_TOLERANCE_FRACTION
    )
    residual_p95 = post_registration.get("residualP95Arcsec")
    astrometry_pass = residual_p95 is not None and residual_p95 <= ASTROMETRY_THRESHOLD_ARCSEC

    _, rubin_post_sky = fit_sky_plane(rubin, matched_common, 0.0)
    _, reference_post_sky = fit_sky_plane(reference, matched_common, 0.0)
    difference = rubin - reference
    difference_values = difference[matched_common]
    difference_sigma = robust_sigma(difference_values)

    # The background gate must test the residual *pedestal*, not the survey flux
    # scale. Any real throughput difference between two bandpasses shows up in
    # the raw median because it multiplies the astrophysical flux above sky, so
    # measuring there would charge the background stage for a bandpass effect it
    # cannot fix. Sigma-clipping removes the source-dominated tail and leaves the
    # sky pixels, where a scale error contributes almost nothing. Both numbers
    # are reported: the raw median is evidence for the bandpass blocker.
    background = difference_values[np.isfinite(difference_values)]
    for _ in range(5):
        sigma = robust_sigma(background)
        if not np.isfinite(sigma) or sigma <= 0:
            break
        keep = np.abs(background - np.median(background)) < 3.0 * sigma
        if keep.all() or keep.sum() < 100:
            break
        background = background[keep]
    background_sigma = robust_sigma(background)
    sky_offset = abs(float(np.median(background)))
    sky_tolerance = 0.10 * background_sigma
    sky_pass = bool(np.isfinite(sky_offset) and sky_tolerance > 0 and sky_offset <= sky_tolerance)

    # --- write the matched pair --------------------------------------------
    region_id = record["regionId"]
    output_dir = products / region_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "rubin-reference-matched.fits"
    primary = fits.PrimaryHDU()
    primary.header["TRACT"] = int(record["tract"])
    primary.header["RUBBAND"] = record["rubinBand"]
    primary.header["REFBAND"] = record["referenceBand"]
    primary.header["PSFMATCH"] = (psf_pass, "Both sides convolved to a common Gaussian FWHM")
    primary.header["SKYMATCH"] = (sky_pass, "Robust sigma-clipped planes subtracted")
    primary.header["UNITMTCH"] = (True, "Both planes converted to nJy")
    primary.header["FILTERMT"] = (False, "Bandpass transfer is not applied")
    primary.header["CMPRDY"] = (False, "Bandpass transfer and injection/recovery QA remain open")
    base = wcs.to_header(relax=True)

    def plane(data: np.ndarray, unit: str, comment: str) -> fits.Header:
        head = base.copy()
        head["BUNIT"] = unit
        head["LAYERQA"] = comment
        return head

    fits.HDUList([
        primary,
        fits.ImageHDU(rubin.astype(np.float32), header=plane(rubin, "nJy", "sky-subtracted, PSF-matched"), name="RUBIN"),
        fits.ImageHDU(reference.astype(np.float32), header=plane(reference, "nJy", "sky-subtracted, PSF-matched"), name="REFERENCE"),
        fits.ImageHDU(rubin_variance.astype(np.float32), header=plane(rubin_variance, "nJy2", "independent-pixel variance only"), name="RUBIN_VARIANCE"),
        fits.ImageHDU(reference_variance.astype(np.float32), header=plane(reference_variance, "nJy2", "independent-pixel variance only"), name="REFERENCE_VARIANCE"),
        fits.ImageHDU(np.where(matched_common, difference, np.nan).astype(np.float32), header=plane(difference, "nJy", "QA plane; bandpass not reconciled"), name="DIFFERENCE"),
        fits.ImageHDU(matched_common.astype(np.uint8), header=plane(matched_common, "", "1=valid on both sides after matching"), name="COMMON_MASK"),
    ]).writeto(output_path, overwrite=True, checksum=True)

    # Flux-unit transfer counts as cleared only when the empirical compact-source
    # ratio actually corroborates the applied chains. Applying a conversion is not
    # the same as demonstrating it is right.
    measured_scale = scale_measurement.get("scale")
    scatter = scale_measurement.get("scatterDex")
    flux_corroborated = bool(
        measured_scale
        and scatter is not None
        and scale_measurement.get("matchedSources", 0) >= MIN_FLUX_SOURCES
        and scatter <= MAX_FLUX_SCATTER_DEX
        and abs(math.log10(measured_scale)) <= MAX_FLUX_LOG_DEPARTURE
    )
    scale_measurement = {
        **scale_measurement,
        "corroboratesDocumentedChains": flux_corroborated,
        "gate": (
            f"at least {MIN_FLUX_SOURCES} matched sources, scatter <= {MAX_FLUX_SCATTER_DEX} dex, "
            f"and |log10(scale)| <= {MAX_FLUX_LOG_DEPARTURE}"
        ),
        "magnitudeOffset": (
            round(-2.5 * math.log10(measured_scale), 5) if measured_scale else None
        ),
    }

    cleared = []
    if psf_pass:
        cleared.append("PSF matching")
    if sky_pass:
        cleared.append("background matching")
    if flux_corroborated:
        cleared.append("flux-unit transfer")
    blockers = [item for item in record["comparisonBlockers"] if item not in cleared]

    status = "matched" if (psf_pass and sky_pass and astrometry_pass) else "qa-failed"
    return {
        "regionId": region_id,
        "tract": int(record["tract"]),
        "center": record["center"],
        "status": status,
        "rubinBand": record["rubinBand"],
        "referenceBand": record["referenceBand"],
        "referenceSurveyId": record["referenceSurveyId"],
        "sameNamedBand": record["sameNamedBand"],
        "pixelScaleArcsec": round(pixel_scale, 5),
        "commonPixelFraction": round(float(matched_common.mean()), 8),
        "units": {
            "matched": True,
            "unit": "nJy",
            "rubinChain": rubin_chain,
            "documentedChain": chain,
            "empiricalPointSourceScale": scale_measurement,
            "note": (
                "Both documented chains are applied to the pixels. The empirical scale is an independent "
                "compact-source check on the same field and is reported, not applied. A value near 1.0 "
                "corroborates both chains; a large departure indicates an unresolved calibration error, "
                "not a discovery."
            ),
        },
        "sky": {
            "matched": sky_pass,
            "rubinSubtractedPlaneNjy": rubin_sky_record,
            "referenceSubtractedPlaneNjy": reference_sky_record,
            "postMatchRubinPlaneNjy": rubin_post_sky,
            "postMatchReferencePlaneNjy": reference_post_sky,
            "differenceMedianNjy": float(np.median(difference_values)),
            "differenceRobustSigmaNjy": float(difference_sigma),
            "backgroundMedianNjy": float(np.median(background)),
            "backgroundRobustSigmaNjy": float(background_sigma),
            "backgroundPixels": int(background.size),
            "medianToleranceNjy": float(sky_tolerance),
            "gate": "sigma-clipped background pedestal must be within 10% of the clipped pixel noise",
            "note": (
                "differenceMedianNjy includes astrophysical flux and therefore carries any real "
                "cross-survey throughput difference; it is reported as bandpass evidence, not as a "
                "background failure."
            ),
        },
        "psf": {
            "matched": psf_pass,
            "model": "circular Gaussian approximation to empirical stellar FWHM",
            "preMatchRubinFwhmArcsec": rubin_fwhm,
            "preMatchReferenceFwhmArcsec": reference_fwhm,
            "targetFwhmArcsec": target_fwhm,
            "rubinKernelSigmaPixels": rubin_sigma,
            "referenceKernelSigmaPixels": reference_sigma,
            "rubinKernelRadiusPixels": rubin_radius,
            "referenceKernelRadiusPixels": reference_radius,
            "postMatchFractionalFwhmDifference": psf_difference,
            "toleranceFraction": PSF_TOLERANCE_FRACTION,
        },
        "registration": {
            "pass": astrometry_pass,
            "thresholdArcsec": ASTROMETRY_THRESHOLD_ARCSEC,
            "appliedReferenceShiftPixels": applied_shift,
            "preMatch": pre_registration,
            "postMatch": post_registration,
        },
        "products": {
            "matchedPair": relative(output_path),
            "sha256": sha256(output_path),
            "bytes": output_path.stat().st_size,
        },
        "comparisonReady": False,
        "clearedBlockers": cleared,
        "comparisonBlockers": blockers,
        "limitations": [
            "The Gaussian PSF model does not capture spatially varying PSF wings, which dominate low-surface-brightness wings.",
            "Convolution and the upstream reprojection introduce pixel covariance that the variance planes do not represent.",
            "Survey bandpasses are not reconciled, so DIFFERENCE is a QA plane and must not be read as missing light.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--panstarrs-evidence", type=Path, default=DEFAULT_PS1_EVIDENCE)
    parser.add_argument("--rubin-manifest", type=Path, default=DEFAULT_RUBIN_MANIFEST)
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--only-region", action="append", default=[])
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    ps1_evidence: dict[str, dict[str, Any]] = {}
    if args.panstarrs_evidence.is_file():
        ps1_payload = json.loads(args.panstarrs_evidence.read_text(encoding="utf-8"))
        ps1_evidence = {item["regionId"]: item for item in ps1_payload.get("regions", [])}

    rubin_payload = json.loads(args.rubin_manifest.read_text(encoding="utf-8"))
    rubin_native_scales = {
        item["regionId"]: float(item["mosaic"]["pixelScaleArcsec"])
        for item in rubin_payload["regions"]
        if item.get("mosaic", {}).get("pixelScaleArcsec")
    }

    only = {value.strip() for value in args.only_region if value.strip()}
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for record in payload["regions"]:
        if only and record["regionId"] not in only:
            continue
        try:
            result = reconcile_region(record, ps1_evidence, rubin_native_scales, args.products)
        except Exception as error:  # noqa: BLE001 - every failure is reported, never silently dropped
            failures.append({
                "regionId": record["regionId"],
                "tract": record["tract"],
                "error": f"{type(error).__name__}: {error}",
            })
            print(f"[failed] {record['regionId']}: {type(error).__name__}: {error}", flush=True)
            continue
        records.append(result)
        print(
            f"[{result['status']}] {result['regionId']} psf={result['psf']['matched']} "
            f"sky={result['sky']['matched']} astrom={result['registration']['pass']} "
            f"blockers={len(result['comparisonBlockers'])}",
            flush=True,
        )

    # A partial run must not discard regions it was never asked to touch, or a
    # single-region debug pass silently truncates the manifest the downstream
    # stages read.
    if only:
        existing_path = args.products / "manifest.json"
        if existing_path.is_file():
            previous = json.loads(existing_path.read_text(encoding="utf-8"))
            refreshed = {item["regionId"] for item in records}
            carried = [item for item in previous.get("regions", []) if item["regionId"] not in refreshed]
            records = sorted(records + carried, key=lambda item: item["regionId"])
            failures = failures + [
                item for item in previous.get("failures", [])
                if item["regionId"] not in refreshed and item["regionId"] not in only
            ]

    matched = [item for item in records if item["status"] == "matched"]
    summary = {
        "schemaVersion": "layers-selected-region-reconciliation-v1",
        "generatedAt": utc_now(),
        "method": (
            "Documented flux-unit transfer into nJy, robust sigma-clipped plane subtraction per side, "
            "empirical stellar-FWHM measurement, and Gaussian convolution to a common target FWHM, "
            "followed by a post-match registration audit against the declared 0.30 arcsec threshold."
        ),
        "counts": {
            "attempted": len(records) + len(failures),
            "reconciled": len(records),
            "matched": len(matched),
            "qaFailed": len(records) - len(matched),
            "failed": len(failures),
            "psfMatched": sum(1 for item in records if item["psf"]["matched"]),
            "skyMatched": sum(1 for item in records if item["sky"]["matched"]),
            "astrometryPassed": sum(1 for item in records if item["registration"]["pass"]),
            "comparisonReady": 0,
        },
        "policy": {
            "scienceClaimAllowed": False,
            "comparisonReady": False,
            "retainedBlockers": list(RETAINED_BLOCKERS),
            "note": (
                "Clearing PSF, background, and unit blockers does not license a quantitative claim. "
                "Bandpass transfer and injection/recovery QA still gate every difference."
            ),
        },
        "failures": failures,
        "regions": records,
    }
    args.products.mkdir(parents=True, exist_ok=True)
    (args.products / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    public = {**summary, "regions": [{key: value for key, value in item.items() if key != "products"} | {
        "products": {"sha256": item["products"]["sha256"], "bytes": item["products"]["bytes"]},
    } for item in records]}
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    print(
        f"\nreconciled {len(records)}/{len(records) + len(failures)}  matched={len(matched)}  "
        f"psf={summary['counts']['psfMatched']}  sky={summary['counts']['skyMatched']}  "
        f"astrometry={summary['counts']['astrometryPassed']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
