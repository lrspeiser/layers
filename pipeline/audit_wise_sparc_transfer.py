#!/usr/bin/env python3
"""Audit target-specific AllWISE W1 to SPARC 3.6 micron transfer.

This stage follows the published WISE/SPARC aperture strategy where possible:
the aperture is tied to the SPARC 23 mag/arcsec^2 isophote, apertures must be
larger than 30 arcsec, and the local sky is estimated from many independent
20x20-pixel boxes.  It adds deterministic source rejection, alternative sky
models, radial-profile checks, and block-bootstrap injection/null tests.

Passing this audit supports a relative W1-versus-3.6 light/structure result.
It does *not* by itself supply the optical color-dependent W1 mass-to-light
ratio required for a new stellar mass, baryonic mass, or delta-g_bar claim.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from astropy.wcs import WCS
from scipy.ndimage import gaussian_filter


PILOTS = ("ngc0100", "ugc00191", "ugc00634", "ugc00891")
W1_ZERO_POINT_JY = 306.682
WISE_FWHM_ARCSEC = 6.1
IRAC_FWHM_ARCSEC = 1.7
ISOPHOTE_MAG_ARCSEC2 = 23.0
SKY_BOX_PIXELS = 20
MIN_APERTURE_RADIUS_ARCSEC = 30.0
MAX_PROFILE_UNCERTAINTY_MAG = 0.25
MIN_PROFILE_BINS = 6
MAX_MEDIAN_ABSOLUTE_RESIDUAL_MAG = 0.15
MAX_PROFILE_SCATTER_MAG = 0.20
MAX_CLIPPED_FRACTION = 0.25
INJECTION_TRIALS = 32
WISE_PAPER_URL = "https://arxiv.org/abs/2404.02339"
WISE_TABLE_URL = "https://cdsarc.cds.unistra.fr/viz-bin/ReadMe/J/AJ/168/19?format=html&tex=true"
ALLWISE_DOC_URL = "https://irsa.ipac.caltech.edu/data/WISE/docs/release/AllWISE/faq.html"
SPARC_URL = "https://astroweb.cwru.edu/SPARC/"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def robust_sigma(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if not values.size:
        return float("nan")
    median = np.median(values)
    return float(1.4826 * np.median(np.abs(values - median)))


def canonical_name(value: str) -> str:
    value = re.sub(r"^LSBC\s*", "", value.strip().upper())
    return re.sub(r"[^A-Z0-9]", "", value)


def target_name(raw: str) -> str:
    return raw.split("=", 1)[-1].strip()


def load_geometry(path: Path) -> dict[str, dict]:
    records = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = target_name(row["ref_raw_id"])
            key = canonical_name(name)
            major = float(row["galdim_majaxis"]) if row["galdim_majaxis"] else None
            minor = float(row["galdim_minaxis"]) if row["galdim_minaxis"] else None
            records[key] = {
                "name": name,
                "raDeg": float(row["ra"]),
                "decDeg": float(row["dec"]),
                "majorAxisArcmin": major,
                "minorAxisArcmin": minor,
                "axisRatio": max(0.08, min(1.0, minor / major)) if major and minor else None,
                "positionAngleDegEastOfNorth": float(row["galdim_angle"]) if row["galdim_angle"] else None,
                "objectType": row["otype_txt"],
            }
    return records


def load_sparc_global(path: Path) -> dict[str, dict]:
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 18:
            continue
        try:
            values = [float(fields[index]) for index in (2, 3, 5, 6, 7, 8, 9, 11, 13, 14)]
        except ValueError:
            continue
        records[canonical_name(fields[0])] = {
            "distanceMpc": values[0],
            "distanceUncertaintyMpc": values[1],
            "inclinationDeg": values[2],
            "inclinationUncertaintyDeg": values[3],
            "luminosity36BillionLsun": values[4],
            "luminosity36UncertaintyBillionLsun": values[5],
            "effectiveRadiusKpc": values[6],
            "diskScaleLengthKpc": values[7],
            "hiMassBillionMsun": values[8],
            "hiRadiusKpc": values[9],
            "sourceRow": line,
        }
    return records


def load_cohort(path: Path) -> dict:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    if len(lines) < 4 or not lines[0].startswith("Name\t"):
        raise RuntimeError(f"Unrecognized VizieR WISE/SPARC table: {path}")
    rows = list(csv.DictReader([lines[0], *lines[3:]], delimiter="\t"))
    colors = np.asarray([float(row["W1mag"]) - float(row["IRACmag"]) for row in rows], dtype=np.float64)
    radii = np.asarray([float(row["Rad"]) for row in rows], dtype=np.float64)
    supported = colors[radii >= MIN_APERTURE_RADIUS_ARCSEC]
    center = float(np.median(supported))
    scatter = robust_sigma(supported)
    return {
        "records": len(rows),
        "recordsAtLeast30Arcsec": int(supported.size),
        "medianW1Minus36Mag": center,
        "robustScatterMag": scatter,
        "central68PercentRangeMag": [float(value) for value in np.quantile(supported, [0.16, 0.84])],
        "central95PercentRangeMag": [float(value) for value in np.quantile(supported, [0.025, 0.975])],
        "sha256": sha256(path),
    }


def interpolate_isophote(points: list[dict], level: float) -> tuple[float, str]:
    accepted = sorted((point for point in points if point["accepted"]), key=lambda point: point["radiusArcsec"])
    for left, right in zip(accepted[:-1], accepted[1:], strict=True):
        y0, y1 = left["surfaceBrightnessMagArcsec2"], right["surfaceBrightnessMagArcsec2"]
        if y0 <= level <= y1 and y1 != y0:
            fraction = (level - y0) / (y1 - y0)
            return float(left["radiusArcsec"] + fraction * (right["radiusArcsec"] - left["radiusArcsec"])), "interpolated"
    return float(accepted[-1]["radiusArcsec"]), "not-reached"


def ellipse_coordinates(shape: tuple[int, int], wcs: WCS, geometry: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    yy, xx = np.indices(shape, dtype=np.float64)
    ra, dec = wcs.pixel_to_world_values(xx, yy)
    east = (ra - geometry["raDeg"]) * math.cos(math.radians(geometry["decDeg"])) * 3600.0
    north = (dec - geometry["decDeg"]) * 3600.0
    theta = math.radians(geometry["positionAngleDegEastOfNorth"])
    major = east * math.sin(theta) + north * math.cos(theta)
    minor = east * math.cos(theta) - north * math.sin(theta)
    radius = np.sqrt(major**2 + (minor / geometry["axisRatio"]) ** 2)
    return radius, east, north


def design_matrix(x: np.ndarray, y: np.ndarray, degree: int) -> np.ndarray:
    columns = [np.ones_like(x)]
    if degree >= 1:
        columns.extend((x, y))
    if degree >= 2:
        columns.extend((x * x, x * y, y * y))
    return np.column_stack(columns)


def robust_fit(x: np.ndarray, y: np.ndarray, values: np.ndarray, degree: int) -> tuple[np.ndarray, np.ndarray]:
    matrix = design_matrix(x, y, degree)
    keep = np.isfinite(values)
    for _ in range(8):
        coeff, *_ = np.linalg.lstsq(matrix[keep], values[keep], rcond=None)
        residual = values - matrix @ coeff
        sigma = robust_sigma(residual[keep])
        updated = np.isfinite(values) & (np.abs(residual - np.median(residual[keep])) <= max(3 * sigma, 1e-6))
        if np.array_equal(updated, keep):
            break
        keep = updated
    coeff, *_ = np.linalg.lstsq(matrix[keep], values[keep], rcond=None)
    return coeff, keep


def evaluate_model(coeff: np.ndarray, shape: tuple[int, int], degree: int) -> np.ndarray:
    yy, xx = np.indices(shape, dtype=np.float64)
    xn = (xx - (shape[1] - 1) / 2) / max(shape[1], 1)
    yn = (yy - (shape[0] - 1) / 2) / max(shape[0], 1)
    return (design_matrix(xn.ravel(), yn.ravel(), degree) @ coeff).reshape(shape)


def sky_models(image: np.ndarray, valid: np.ndarray, radius: np.ndarray, aperture_radius: float) -> dict:
    boxes = []
    height, width = image.shape
    for y0 in range(0, height - SKY_BOX_PIXELS + 1, SKY_BOX_PIXELS):
        for x0 in range(0, width - SKY_BOX_PIXELS + 1, SKY_BOX_PIXELS):
            section = np.s_[y0:y0 + SKY_BOX_PIXELS, x0:x0 + SKY_BOX_PIXELS]
            usable = valid[section] & (radius[section] >= 1.35 * aperture_radius)
            if usable.mean() < 0.90:
                continue
            pixels = image[section][usable]
            mean, median, sigma = sigma_clipped_stats(pixels, sigma=3.0, maxiters=6)
            boxes.append({
                "x": (x0 + SKY_BOX_PIXELS / 2 - (width - 1) / 2) / width,
                "y": (y0 + SKY_BOX_PIXELS / 2 - (height - 1) / 2) / height,
                "mean": float(mean),
                "median": float(median),
                "sigma": float(sigma),
                "upperExcursion": float(np.percentile(pixels, 90) - median),
                "pixels": pixels.reshape(-1),
            })
    if len(boxes) < 10:
        raise RuntimeError(f"Only {len(boxes)} candidate WISE sky boxes")
    typical_sigma = float(np.median([box["sigma"] for box in boxes]))
    clean = [box for box in boxes if box["sigma"] <= 2.5 * typical_sigma and box["upperExcursion"] <= 5 * typical_sigma]
    if len(clean) < 10:
        raise RuntimeError(f"Only {len(clean)} clean WISE sky boxes")
    x = np.asarray([box["x"] for box in clean])
    y = np.asarray([box["y"] for box in clean])
    values = np.asarray([box["mean"] for box in clean])
    models = {}
    retained = None
    for degree, label in ((0, "constant"), (1, "plane"), (2, "quadratic")):
        coeff, keep = robust_fit(x, y, values, degree)
        models[label] = {
            "degree": degree,
            "coefficientsNjy": [float(value) for value in coeff],
            "image": evaluate_model(coeff, image.shape, degree),
            "retainedBoxes": int(keep.sum()),
        }
        if label == "plane":
            retained = keep
    nominal = models["plane"]["image"]
    retained_boxes = [box for box, use in zip(clean, retained, strict=True) if use]
    predictions = np.asarray([
        nominal[
            int(round((box["y"] * height) + (height - 1) / 2)),
            int(round((box["x"] * width) + (width - 1) / 2)),
        ]
        for box in retained_boxes
    ])
    residual_means = values[retained] - predictions
    residual_sigma = robust_sigma(residual_means)
    return {
        "candidateBoxes": len(boxes),
        "cleanBoxes": len(clean),
        "typicalWithinBoxSigmaNjy": typical_sigma,
        "skyLevelUncertaintyNjyPerPixel": residual_sigma,
        "models": models,
        # Preserve box-to-box sky offsets in the bootstrap.  Each block is
        # referenced to the fitted plane, not independently zeroed.
        "cleanPixelBlocks": [box["pixels"] - prediction for box, prediction in zip(retained_boxes, predictions, strict=True)],
    }


def clipped_mean(values: np.ndarray) -> tuple[float, np.ndarray]:
    finite = np.isfinite(values)
    keep = finite.copy()
    for _ in range(6):
        selected = values[keep]
        if selected.size < 8:
            break
        median = np.median(selected)
        sigma = robust_sigma(selected)
        if not np.isfinite(sigma) or sigma <= 0:
            break
        updated = finite & (values >= median - 4 * sigma) & (values <= median + 3 * sigma)
        if np.array_equal(updated, keep):
            break
        keep = updated
    return (float(np.mean(values[keep])) if keep.any() else float("nan")), keep


def interpolate_profile(points: list[dict], radii: np.ndarray, field: str) -> np.ndarray:
    accepted = sorted((point for point in points if point["accepted"]), key=lambda point: point["radiusArcsec"])
    x = np.asarray([point["radiusArcsec"] for point in accepted])
    if field == "uncertaintyMag":
        y = np.asarray([point[field] if point[field] is not None else np.nan for point in accepted])
        finite = np.isfinite(y)
        return np.interp(radii, x[finite], y[finite])
    y = np.asarray([point[field] for point in accepted])
    return np.interp(radii, x, y)


def measure_profile(
    image: np.ndarray,
    variance: np.ndarray,
    valid: np.ndarray,
    radius: np.ndarray,
    profile: list[dict],
    aperture_radius: float,
    pixel_area: float,
    sky: dict,
) -> dict:
    width = max(WISE_FWHM_ARCSEC / 2, 3.0)
    edges = np.arange(0, aperture_radius + width, width)
    if edges[-1] < aperture_radius:
        edges = np.append(edges, aperture_radius)
    edges[-1] = aperture_radius
    centers = (edges[:-1] + edges[1:]) / 2
    sparc_mu = interpolate_profile(profile, centers, "surfaceBrightnessMagArcsec2")
    sparc_unc = interpolate_profile(profile, centers, "uncertaintyMag")
    model_images = {name: item["image"] for name, item in sky["models"].items()}
    nominal = image - model_images["plane"]
    rows = []
    total_flux = 0.0
    formal_variance = 0.0
    alternative_totals = {name: 0.0 for name in model_images}
    total_pixels = 0
    for index, (lower, upper, center) in enumerate(zip(edges[:-1], edges[1:], centers, strict=True)):
        annulus = valid & (radius >= lower) & (radius < upper)
        count = int(annulus.sum())
        if count < 8:
            continue
        values = nominal[annulus]
        mean, retained = clipped_mean(values)
        retained_count = int(retained.sum())
        if retained_count < 8 or not np.isfinite(mean):
            continue
        formal_mean = math.sqrt(float(np.sum(variance[annulus][retained]))) / retained_count
        alternative_means = {}
        for name, background in model_images.items():
            alt_mean, _ = clipped_mean((image - background)[annulus])
            alternative_means[name] = alt_mean
            alternative_totals[name] += alt_mean * count
        model_spread = float(np.std(list(alternative_means.values()), ddof=1))
        mean_uncertainty = math.sqrt(formal_mean**2 + sky["skyLevelUncertaintyNjyPerPixel"]**2 + model_spread**2)
        surface_flux = mean / pixel_area
        surface_uncertainty = mean_uncertainty / pixel_area
        if surface_flux > 0:
            mu = -2.5 * math.log10((surface_flux * 1e-9) / W1_ZERO_POINT_JY)
            mu_unc = 2.5 / math.log(10) * surface_uncertainty / surface_flux
        else:
            mu, mu_unc = None, None
        total_flux += mean * count
        formal_variance += (formal_mean * count) ** 2
        total_pixels += count
        rows.append({
            "radiusArcsec": float(center),
            "innerRadiusArcsec": float(lower),
            "outerRadiusArcsec": float(upper),
            "wiseSurfaceBrightnessMagArcsec2": float(mu) if mu is not None else None,
            "wiseUncertaintyMag": float(mu_unc) if mu_unc is not None else None,
            "sparcSurfaceBrightnessMagArcsec2": float(sparc_mu[index]),
            "sparcUncertaintyMag": float(sparc_unc[index]),
            "w1Minus36Mag": float(mu - sparc_mu[index]) if mu is not None else None,
            "validPixels": count,
            "retainedPixels": retained_count,
            "clippedFraction": 1 - retained_count / count,
            "backgroundModelSpreadNjyPerPixel": model_spread,
        })
    alternative_fluxes = list(alternative_totals.values())
    background_systematic = float(np.std(alternative_fluxes, ddof=1))
    flux_uncertainty = math.sqrt(formal_variance + (sky["skyLevelUncertaintyNjyPerPixel"] * total_pixels) ** 2 + background_systematic**2)
    aperture_mag = -2.5 * math.log10((total_flux * 1e-9) / W1_ZERO_POINT_JY) if total_flux > 0 else None
    aperture_mag_unc = 2.5 / math.log(10) * flux_uncertainty / total_flux if total_flux > 0 else None
    return {
        "binWidthArcsec": width,
        "bins": rows,
        "apertureFluxNjy": total_flux,
        "apertureFluxUncertaintyNjy": flux_uncertainty,
        "apertureMagnitudeVega": aperture_mag,
        "apertureMagnitudeUncertaintyMag": aperture_mag_unc,
        "backgroundModelFluxSystematicNjy": background_systematic,
        "aperturePixels": total_pixels,
        "edgesArcsec": edges,
    }


def integrate_sparc(profile: list[dict], aperture_radius: float, axis_ratio: float, seed: int) -> dict:
    radii = np.linspace(0, aperture_radius, 2049)
    mu = interpolate_profile(profile, radii, "surfaceBrightnessMagArcsec2")
    uncertainty = interpolate_profile(profile, radii, "uncertaintyMag")
    area_weights = 2 * math.pi * axis_ratio * radii * (radii[1] - radii[0])
    normalized_flux = float(np.sum(10 ** (-0.4 * mu) * area_weights))
    magnitude = -2.5 * math.log10(normalized_flux)
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(256):
        perturbed = mu + rng.normal(0, np.nan_to_num(uncertainty, nan=0.0))
        samples.append(-2.5 * math.log10(float(np.sum(10 ** (-0.4 * perturbed) * area_weights))))
    return {
        "apertureMagnitudeVega": magnitude,
        "apertureMagnitudeUncertaintyMag": float(np.std(samples, ddof=1)),
        "integrationSamples": len(samples),
    }


def weighted_line(x: np.ndarray, y: np.ndarray, uncertainty: np.ndarray) -> dict | None:
    if x.size < 3 or np.ptp(x) <= 0:
        return None
    weights = 1 / np.maximum(uncertainty, 1e-3) ** 2
    matrix = np.column_stack((np.ones_like(x), x))
    normal = matrix.T @ (weights[:, None] * matrix)
    covariance = np.linalg.inv(normal)
    coeff = covariance @ (matrix.T @ (weights * y))
    residual = y - matrix @ coeff
    scatter = robust_sigma(residual)
    slope = float(coeff[1])
    if slope <= 0:
        return None
    scale = 1.0857362047581296 / slope
    slope_unc = math.sqrt(float(covariance[1, 1]))
    return {
        "interceptMagArcsec2": float(coeff[0]),
        "slopeMagPerArcsec": slope,
        "slopeUncertaintyMagPerArcsec": slope_unc,
        "scaleLengthArcsec": scale,
        "scaleLengthUncertaintyArcsec": scale * slope_unc / slope,
        "robustResidualScatterMag": scatter,
        "points": int(x.size),
    }


def transfer_statistics(measured: dict, aperture_radius: float) -> dict:
    qualified = [row for row in measured["bins"] if row["wiseSurfaceBrightnessMagArcsec2"] is not None
                 and row["wiseUncertaintyMag"] <= MAX_PROFILE_UNCERTAINTY_MAG
                 and row["sparcUncertaintyMag"] <= MAX_PROFILE_UNCERTAINTY_MAG
                 and row["radiusArcsec"] >= max(2 * WISE_FWHM_ARCSEC, 0.15 * aperture_radius)
                 and row["clippedFraction"] <= MAX_CLIPPED_FRACTION]
    colors = np.asarray([row["w1Minus36Mag"] for row in qualified])
    uncertainties = np.asarray([math.hypot(row["wiseUncertaintyMag"], row["sparcUncertaintyMag"]) for row in qualified])
    radii = np.asarray([row["radiusArcsec"] for row in qualified])
    if colors.size:
        weights = 1 / np.maximum(uncertainties, 1e-3) ** 2
        offset = float(np.sum(weights * colors) / np.sum(weights))
        residuals = colors - offset
        median_absolute = float(np.median(np.abs(residuals)))
        scatter = robust_sigma(residuals)
        offset_unc = math.sqrt(1 / float(np.sum(weights)))
        radial_leverage = float(radii.max() / radii.min()) if radii.min() > 0 else 0.0
        clipped = float(np.median([row["clippedFraction"] for row in qualified]))
    else:
        offset = median_absolute = scatter = offset_unc = None
        radial_leverage = clipped = 0.0
    outer = [row for row in qualified if row["radiusArcsec"] >= 0.35 * aperture_radius]
    if len(outer) >= 3:
        x = np.asarray([row["radiusArcsec"] for row in outer])
        wise = np.asarray([row["wiseSurfaceBrightnessMagArcsec2"] for row in outer])
        wise_unc = np.asarray([row["wiseUncertaintyMag"] for row in outer])
        sparc = np.asarray([row["sparcSurfaceBrightnessMagArcsec2"] for row in outer])
        sparc_unc = np.asarray([row["sparcUncertaintyMag"] for row in outer])
        wise_fit = weighted_line(x, wise, wise_unc)
        sparc_fit = weighted_line(x, sparc, sparc_unc)
    else:
        wise_fit = sparc_fit = None
    return {
        "qualifiedBins": len(qualified),
        "qualifiedRadiiArcsec": [float(value) for value in radii],
        "fittedW1Minus36Mag": offset,
        "fittedColorStatisticalUncertaintyMag": offset_unc,
        "medianAbsoluteProfileResidualMag": median_absolute,
        "robustProfileResidualScatterMag": scatter,
        "radialLeverage": radial_leverage,
        "medianClippedFraction": clipped,
        "wiseExponentialFit": wise_fit,
        "sparcExponentialFit": sparc_fit,
    }


def synthetic_model(radius: np.ndarray, profile: list[dict], color: float, pixel_area: float, pixel_scale: float) -> np.ndarray:
    mu36 = interpolate_profile(profile, np.minimum(radius, max(point["radiusArcsec"] for point in profile if point["accepted"])), "surfaceBrightnessMagArcsec2")
    surface_njy = W1_ZERO_POINT_JY * 1e9 * 10 ** (-0.4 * (mu36 + color))
    model = surface_njy * pixel_area
    sigma_pixels = math.sqrt(max(0.0, WISE_FWHM_ARCSEC**2 - IRAC_FWHM_ARCSEC**2)) / 2.354820045 / pixel_scale
    return gaussian_filter(model, sigma_pixels, mode="constant", cval=0.0)


def bootstrap_canvas(shape: tuple[int, int], blocks: list[np.ndarray], rng: np.random.Generator) -> np.ndarray:
    height, width = shape
    canvas = np.empty(shape, dtype=np.float64)
    for y0 in range(0, height, SKY_BOX_PIXELS):
        for x0 in range(0, width, SKY_BOX_PIXELS):
            source = blocks[int(rng.integers(0, len(blocks)))].copy()
            source = source[: SKY_BOX_PIXELS * SKY_BOX_PIXELS]
            if source.size < SKY_BOX_PIXELS * SKY_BOX_PIXELS:
                source = np.resize(source, SKY_BOX_PIXELS * SKY_BOX_PIXELS)
            tile = source.reshape(SKY_BOX_PIXELS, SKY_BOX_PIXELS)
            median = np.median(tile)
            sigma = robust_sigma(tile)
            tile = np.clip(tile, median - 5 * sigma, median + 5 * sigma)
            if rng.random() < 0.5:
                tile = tile[::-1]
            if rng.random() < 0.5:
                tile = tile[:, ::-1]
            y1, x1 = min(y0 + SKY_BOX_PIXELS, height), min(x0 + SKY_BOX_PIXELS, width)
            canvas[y0:y1, x0:x1] = tile[: y1 - y0, : x1 - x0]
    return canvas


def extract_aperture_flux(image: np.ndarray, radius: np.ndarray, aperture_radius: float) -> float:
    width = max(WISE_FWHM_ARCSEC / 2, 3.0)
    total = 0.0
    for lower in np.arange(0, aperture_radius, width):
        upper = min(aperture_radius, lower + width)
        annulus = (radius >= lower) & (radius < upper)
        mean, _ = clipped_mean(image[annulus])
        if np.isfinite(mean):
            total += mean * int(annulus.sum())
    return total


def injection_audit(shape: tuple[int, int], radius: np.ndarray, aperture_radius: float, model: np.ndarray,
                    blocks: list[np.ndarray], seed: int) -> dict:
    rng = np.random.default_rng(seed)
    aperture = radius <= aperture_radius
    true_flux = float(model[aperture].sum())
    recovered = []
    nulls = []
    for _ in range(INJECTION_TRIALS):
        noise = bootstrap_canvas(shape, blocks, rng)
        null_flux = extract_aperture_flux(noise, radius, aperture_radius)
        injected_flux = extract_aperture_flux(noise + model, radius, aperture_radius)
        nulls.append(null_flux)
        recovered.append((injected_flux - null_flux) / true_flux)
    recovered_array = np.asarray(recovered)
    null_array = np.asarray(nulls)
    percentiles = np.quantile(recovered_array, [0.16, 0.5, 0.84])
    null_sigma_fraction = robust_sigma(null_array) / true_flux
    null_median_fraction = float(np.median(null_array) / true_flux)
    passed = bool(
        0.90 <= percentiles[1] <= 1.10
        and percentiles[0] >= 0.80
        and percentiles[2] <= 1.20
        and abs(null_median_fraction) <= 0.10
        and null_sigma_fraction <= 0.25
    )
    return {
        "status": "pass" if passed else "qa-failed",
        "trials": INJECTION_TRIALS,
        "profile": "SPARC 3.6 radial profile shifted by measured W1-[3.6] color and convolved to the WISE PSF",
        "trueInjectedFluxNjy": true_flux,
        "recoveredFluxFractionP16P50P84": [float(value) for value in percentiles],
        "nullMedianAsTargetFluxFraction": null_median_fraction,
        "nullRobustSigmaAsTargetFluxFraction": null_sigma_fraction,
        "pass": passed,
        "thresholds": {
            "medianRecoveryFraction": [0.90, 1.10],
            "central68RecoveryFraction": [0.80, 1.20],
            "maximumAbsoluteNullMedianFraction": 0.10,
            "maximumNullRobustSigmaFraction": 0.25,
        },
    }


def classify(significance: float) -> str:
    return "large" if significance >= 3 else "noteworthy" if significance >= 2 else "expected"


def stable_created_at(path: Path) -> str:
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8")).get("createdAt")
        if existing:
            return existing
    return datetime.now(timezone.utc).isoformat()


def comparison_record(result: dict) -> dict:
    """Build the compact catalog/API view; the public package retains the full audit."""
    passed = result["status"] == "pass"
    aperture = result["aperture"]
    expected_scatter = result["cohortExpectation"]["robustScatterMag"]
    measurements = []
    if passed:
        measurements.append({
            "id": f"{result['targetId']}-wise-w1-minus-sparc36-aperture",
            "label": "WISE W1 − SPARC 3.6 µm aperture light",
            "quantity": "W1 minus 3.6 micron aperture magnitude",
            "value": aperture["w1Minus36Mag"],
            "unit": "mag",
            "statisticalUncertainty": aperture["statisticalUncertaintyMag"],
            "systematicUncertainty": aperture["systematicUncertaintyMag"],
            "expectedCenter": aperture["expectedCenterMag"],
            "expectedRange": aperture["expectedRangeMag"],
            "significanceSigma": aperture["significanceSigma"],
            "classification": aperture["classification"],
            "provenance": [
                result["provenance"]["wisePaper"], result["provenance"]["wiseTable"],
                f"sha256:{result['provenance']['wiseProductSha256']}",
                f"sha256:{result['provenance']['sparcProfileSha256']}",
                f"sha256:{result['provenance']['externalValidationSha256']}",
            ],
            "caveats": [
                "This is matched-aperture relative photometry; AllWISE Atlas pixels do not provide absolute sky surface brightness.",
                "The empirical control-sample RMS is propagated as a systematic and is not subtracted as a fitted correction.",
                "W1−[3.6] is not a stellar-mass calibration and does not revise baryonic mass or g_bar.",
            ],
        })
        structure = result.get("structuralComparison")
        if structure:
            sigma = structure["nullSignificanceSigma"]
            total_uncertainty = math.hypot(
                structure["statisticalUncertaintyDex"], structure["profileResidualSystematicDex"]
            )
            measurements.append({
                "id": f"{result['targetId']}-wise-to-sparc-scale-length",
                "label": "WISE-to-SPARC outer scale-length ratio",
                "quantity": "log10 W1 to 3.6 micron exponential scale-length ratio",
                "value": structure["log10WiseToSparcScaleLengthRatio"],
                "unit": "dex",
                "statisticalUncertainty": structure["statisticalUncertaintyDex"],
                "systematicUncertainty": structure["profileResidualSystematicDex"],
                "expectedCenter": 0.0,
                "expectedRange": [-1.96 * total_uncertainty, 1.96 * total_uncertainty],
                "significanceSigma": sigma,
                "classification": classify(sigma),
                "provenance": [
                    f"sha256:{result['provenance']['wiseProductSha256']}",
                    f"sha256:{result['provenance']['sparcProfileSha256']}",
                ],
                "caveats": [
                    "The null expectation is equal outer exponential scale length after PSF separation; it is not a population prior.",
                    "The systematic converts the measured radial-profile residual scatter into a conservative slope uncertainty.",
                    "Catalogued fixed ellipse geometry is used because spatially varying SPARC isophotal geometry is unavailable.",
                ],
            })
    return {
        "id": f"{result['targetId']}--wise-sparc-transfer",
        "comparisonKey": f"{result['targetId']}--wise-sparc-transfer",
        "comparisonMode": "catalog-profile",
        "layerIds": result["layerIds"],
        "status": "published" if passed else "qa",
        "compatibility": {
            "targetIdentityMatched": True,
            "quantityMatched": True,
            "unitsMatched": True,
            "distanceScaleShared": True,
            "modelDeclared": True,
            "limitations": result["limitations"],
        },
        "transferSummary": {
            "status": result["status"],
            "apertureRadiusArcsec": aperture["radiusArcsec"],
            "wiseW1MagnitudeVega": aperture["wiseW1MagnitudeVega"],
            "sparc36MagnitudeVega": aperture["sparc36MagnitudeVega"],
            "w1Minus36Mag": aperture["w1Minus36Mag"],
            "statisticalUncertaintyMag": aperture["statisticalUncertaintyMag"],
            "systematicUncertaintyMag": aperture["systematicUncertaintyMag"],
            "expectedCenterMag": aperture["expectedCenterMag"],
            "qualifiedRadialBins": result["radialTransfer"]["qualifiedBins"],
            "profileResidualScatterMag": result["radialTransfer"]["robustProfileResidualScatterMag"],
            "retainedSkyBoxes": result["sky"]["retainedPlaneBoxes"],
            "injectionRecoveryPass": result["injectionRecovery"]["pass"],
            "failedGates": [name for name, value in result["gates"].items() if not value],
            "massInferenceStatus": "blocked",
            "massInferenceReason": result["scienceGates"]["stellarMass"]["reason"],
        },
        "radialSeries": [
            {
                "radiusArcsec": row["radiusArcsec"],
                "wiseSurfaceBrightnessMagArcsec2": row["wiseSurfaceBrightnessMagArcsec2"],
                "wiseUncertaintyMag": row["wiseUncertaintyMag"],
                "sparcSurfaceBrightnessMagArcsec2": row["sparcSurfaceBrightnessMagArcsec2"],
                "w1Minus36Mag": row["w1Minus36Mag"],
            }
            for row in result["radialProfile"]
            if passed and row["wiseSurfaceBrightnessMagArcsec2"] is not None
        ],
        "products": {
            "qaPackage": f"/data/comparisons/{result['targetId']}--wise-sparc-transfer.json",
            "reproductionAudit": f"pipeline/output/wise-sparc-transfer/{result['targetId']}.json",
        },
        "measurements": measurements,
        "inferences": ([{
            "id": f"{result['targetId']}-relative-near-ir-light",
            "domain": "outer-light",
            "observation": (
                f"Matched-aperture photometry gives W1−[3.6] = {aperture['w1Minus36Mag']:.3f} mag "
                f"at {aperture['radiusArcsec']:.1f} arcsec."
            ),
            "modelDependentInterpretation": (
                "The relative near-IR light and outer-profile shape are supported by the transfer gates. "
                "No stellar-mass, baryonic-mass, or radial-acceleration revision is supported without an independent optical-color M/L calibration."
            ),
            "confidence": "supported",
            "assumptions": [
                "The published SPARC radial profile and catalogued fixed ellipse describe the same aperture.",
                f"The control-cohort intrinsic W1−[3.6] scatter is {expected_scatter:.3f} mag.",
                "Local sky blocks capture the relevant AllWISE background covariance.",
            ],
        }] if passed else []),
        "assumptionAudits": [],
    }


def synchronize_wise_layer_records(root: Path, wise_root: Path, results: list[dict]) -> None:
    """Make the WISE layer's public gate agree with the completed transfer audit."""
    manifest_path = wise_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result_by_target = {result["targetId"]: result for result in results}
    for item in manifest.get("targets", []):
        result = result_by_target.get(item.get("targetId"))
        if not result:
            continue
        passed = result["status"] == "pass"
        failed = [name for name, value in result["gates"].items() if not value]
        item["layer"]["note"] = (
            "Authentic public W1 science, uncertainty, and coverage cutouts. The extended-source transfer passes for relative W1−[3.6] light and structure; stellar-mass, baryonic-mass, and g_bar claims remain blocked."
            if passed else
            "Authentic public W1 science, uncertainty, and coverage cutouts. The extended-source transfer remains QA-only because one or more radial-profile gates failed; no light or mass difference is published."
        )
        item["scienceGate"] = {
            "status": "pass" if passed else "blocked",
            "reason": (
                "External controls and all target-specific aperture, sky, profile, PSF, injection, and null gates pass for relative W1−[3.6] light and structure."
                if passed else f"Extended-source transfer failed: {', '.join(failed)}."
            ),
            "supportedClaims": (["relative W1−[3.6] aperture light", "relative outer exponential scale length"] if passed else []),
            "unsupportedClaims": ["stellar-mass change", "baryonic-mass change", "delta g_bar"],
            "evidencePackage": f"/data/comparisons/{result['targetId']}--wise-sparc-transfer.json",
        }
        public_path = root / "public" / item["layer"]["assets"]["data"].lstrip("/")
        record = json.loads(public_path.read_text(encoding="utf-8"))
        record["layer"] = item["layer"]
        record["scienceGate"] = item["scienceGate"]
        public_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        item["publicRecord"]["sha256"] = sha256(public_path)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def audit_target(root: Path, target_id: str, geometry: dict, global_record: dict, cohort: dict,
                 validation: dict, args: argparse.Namespace) -> dict:
    product = args.wise_root / target_id / "allwise_w1.fits"
    profile_path = args.profiles / f"{target_id}.json"
    profile_record = json.loads(profile_path.read_text(encoding="utf-8"))["target"]
    profile = profile_record["surfaceBrightness"]
    with fits.open(product, memmap=False) as hdus:
        image = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
        variance = np.asarray(hdus["VARIANCE"].data, dtype=np.float64)
        valid = np.asarray(hdus["VALID_MASK"].data, dtype=bool)
        header = hdus["IMAGE"].header.copy()
    wcs = WCS(header)
    pixel_scale = math.sqrt(abs(float(np.linalg.det(wcs.pixel_scale_matrix)))) * 3600.0
    pixel_area = pixel_scale**2
    radius, _, _ = ellipse_coordinates(image.shape, wcs, geometry)
    aperture_radius, isophote_status = interpolate_isophote(profile, ISOPHOTE_MAG_ARCSEC2)
    sky = sky_models(image, valid, radius, aperture_radius)
    measured = measure_profile(image, variance, valid, radius, profile, aperture_radius, pixel_area, sky)
    sparc_aperture = integrate_sparc(profile, aperture_radius, geometry["axisRatio"], seed=sum(map(ord, target_id)))
    wise_mag = measured["apertureMagnitudeVega"]
    wise_unc = measured["apertureMagnitudeUncertaintyMag"]
    color = wise_mag - sparc_aperture["apertureMagnitudeVega"] if wise_mag is not None else None
    color_unc = math.sqrt(wise_unc**2 + sparc_aperture["apertureMagnitudeUncertaintyMag"]**2 + 0.006**2) if wise_unc is not None else None
    color_systematic = validation["combinedColorSystematicMag"]
    significance = (
        abs(color - cohort["medianW1Minus36Mag"])
        / math.sqrt(color_unc**2 + color_systematic**2 + cohort["robustScatterMag"]**2)
        if color is not None else None
    )
    transfer = transfer_statistics(measured, aperture_radius)
    model_color = transfer["fittedW1Minus36Mag"] if transfer["fittedW1Minus36Mag"] is not None else cohort["medianW1Minus36Mag"]
    model = synthetic_model(radius, profile, model_color, pixel_area, pixel_scale)
    model[radius > aperture_radius] = 0.0
    injection = injection_audit(image.shape, radius, aperture_radius, model, sky["cleanPixelBlocks"], sum(map(ord, target_id)) + 2718)
    gates = {
        "externalControlValidation": validation.get("status") == "pass",
        "spitzer23IsophoteReached": isophote_status == "interpolated",
        "minimum30ArcsecAperture": aperture_radius >= MIN_APERTURE_RADIUS_ARCSEC,
        "minimumTenSkyBoxes": sky["models"]["plane"]["retainedBoxes"] >= 10,
        "aperturePhotometryUncertainty": wise_unc is not None and wise_unc <= MAX_PROFILE_UNCERTAINTY_MAG,
        "minimumQualifiedProfileBins": transfer["qualifiedBins"] >= MIN_PROFILE_BINS,
        "minimumRadialLeverage": transfer["radialLeverage"] >= 2.0,
        "maximumMedianProfileResidual": transfer["medianAbsoluteProfileResidualMag"] is not None and transfer["medianAbsoluteProfileResidualMag"] <= MAX_MEDIAN_ABSOLUTE_RESIDUAL_MAG,
        "maximumProfileScatter": transfer["robustProfileResidualScatterMag"] is not None and transfer["robustProfileResidualScatterMag"] <= MAX_PROFILE_SCATTER_MAG,
        "maximumClippedPixelFraction": transfer["medianClippedFraction"] <= MAX_CLIPPED_FRACTION,
        "injectionAndNullTests": injection["pass"],
    }
    passed = all(gates.values())
    scale_measurement = None
    if transfer["wiseExponentialFit"] and transfer["sparcExponentialFit"]:
        wise_scale = transfer["wiseExponentialFit"]["scaleLengthArcsec"]
        sparc_scale = transfer["sparcExponentialFit"]["scaleLengthArcsec"]
        log_ratio = math.log10(wise_scale / sparc_scale)
        wise_scale_unc = transfer["wiseExponentialFit"]["scaleLengthUncertaintyArcsec"]
        sparc_scale_unc = transfer["sparcExponentialFit"]["scaleLengthUncertaintyArcsec"]
        statistical_uncertainty = math.hypot(wise_scale_unc / wise_scale, sparc_scale_unc / sparc_scale) / math.log(10)
        radial_span = max(transfer["qualifiedRadiiArcsec"]) - min(transfer["qualifiedRadiiArcsec"])
        slope = transfer["wiseExponentialFit"]["slopeMagPerArcsec"]
        scatter = transfer["wiseExponentialFit"]["robustResidualScatterMag"]
        systematic_uncertainty = min(0.5, scatter / max(radial_span * slope * math.log(10), 1e-6))
        scale_measurement = {
            "log10WiseToSparcScaleLengthRatio": log_ratio,
            "statisticalUncertaintyDex": statistical_uncertainty,
            "profileResidualSystematicDex": systematic_uncertainty,
            "nullSignificanceSigma": abs(log_ratio) / math.hypot(statistical_uncertainty, systematic_uncertainty),
            "wiseScaleLengthArcsec": wise_scale,
            "sparcScaleLengthArcsec": sparc_scale,
        }
    result = {
        "schemaVersion": 1,
        "product": "Layers WISE-to-SPARC extended-source transfer audit",
        "targetId": target_id,
        "sparcId": profile_record["sparcId"],
        "createdAt": stable_created_at(args.output / f"{target_id}.json"),
        "status": "pass" if passed else "qa-failed",
        "layerIds": ["wise-allwise-atlas", "sparc-2016"],
        "method": {
            "aperture": f"SPARC {ISOPHOTE_MAG_ARCSEC2:g} mag/arcsec^2 isophote",
            "sky": "robust plane from automated source-rejected 20x20-pixel outer boxes",
            "alternativeReductions": ["constant sky", "planar sky", "quadratic sky"],
            "foregroundControl": "asymmetric iterative clipping within narrow elliptical annuli plus sky-box quality rejection",
            "psf": f"profile bins start beyond 2x the {WISE_FWHM_ARCSEC:g}-arcsec WISE FWHM; injections convolve the SPARC model from {IRAC_FWHM_ARCSEC:g} to {WISE_FWHM_ARCSEC:g} arcsec",
        },
        "geometry": geometry,
        "aperture": {
            "radiusArcsec": aperture_radius,
            "isophoteStatus": isophote_status,
            "axisRatio": geometry["axisRatio"],
            "positionAngleDegEastOfNorth": geometry["positionAngleDegEastOfNorth"],
            "wiseW1MagnitudeVega": wise_mag,
            "wiseW1UncertaintyMag": wise_unc,
            "sparc36MagnitudeVega": sparc_aperture["apertureMagnitudeVega"],
            "sparc36UncertaintyMag": sparc_aperture["apertureMagnitudeUncertaintyMag"],
            "w1Minus36Mag": color,
            "statisticalUncertaintyMag": color_unc,
            "systematicUncertaintyMag": color_systematic,
            "expectedCenterMag": cohort["medianW1Minus36Mag"],
            "expectedRangeMag": cohort["central95PercentRangeMag"],
            "significanceSigma": significance,
            "classification": classify(significance) if significance is not None else None,
        },
        "sky": {
            "candidateBoxes": sky["candidateBoxes"],
            "cleanBoxes": sky["cleanBoxes"],
            "retainedPlaneBoxes": sky["models"]["plane"]["retainedBoxes"],
            "typicalWithinBoxSigmaNjy": sky["typicalWithinBoxSigmaNjy"],
            "skyLevelUncertaintyNjyPerPixel": sky["skyLevelUncertaintyNjyPerPixel"],
            "models": {name: {key: value for key, value in record.items() if key != "image"} for name, record in sky["models"].items()},
        },
        "radialTransfer": transfer,
        "structuralComparison": scale_measurement,
        "injectionRecovery": injection,
        "gates": gates,
        "scienceGates": {
            "relativeOuterLight": {"supported": passed, "reason": "All extended-source transfer gates pass." if passed else "One or more extended-source photometry gates failed."},
            "relativeStructure": {"supported": bool(passed and scale_measurement), "reason": "Requires the transfer and two valid exponential fits."},
            "stellarMass": {"supported": False, "reason": "The pilot lacks a validated optical-color W1 mass-to-light ratio; W1-[3.6] alone is not a stellar-mass calibration."},
            "baryonicMass": {"supported": False, "reason": "Blocked by the stellar-mass gate; published SPARC H I is retained unchanged."},
            "deltaGbar": {"supported": False, "reason": "Blocked until a validated radial stellar-mass normalization can be propagated through the SPARC mass model."},
        },
        "cohortExpectation": cohort,
        "externalValidation": {
            "status": validation["status"],
            "qualifiedControls": validation["selection"]["qualifiedControls"],
            "combinedColorSystematicMag": validation["combinedColorSystematicMag"],
            "wiseRmsResidualMag": validation["wiseW1"]["rmsResidualMag"],
            "sparcIntegrationRmsResidualMag": validation["sparc36ProfileIntegration"]["rmsResidualMag"],
        },
        "sparcGlobal": global_record,
        "radialProfile": measured["bins"],
        "provenance": {
            "wiseProductSha256": sha256(product),
            "sparcProfileSha256": sha256(profile_path),
            "cohortTableSha256": cohort["sha256"],
            "externalValidationSha256": sha256(args.validation),
            "wisePaper": WISE_PAPER_URL,
            "wiseTable": WISE_TABLE_URL,
            "allWiseDocumentation": ALLWISE_DOC_URL,
            "sparcArchive": SPARC_URL,
        },
        "limitations": [
            "AllWISE Atlas pixels support local-background relative photometry, not absolute sky surface brightness.",
            "The ellipse uses the catalogued SIMBAD geometry; spatially varying SPARC isophotal geometry is not available in the public profile table.",
            "Automated annular clipping is not a substitute for expert manual masking of every foreground-star wing.",
            "Block-bootstrap trials approximate correlated background/confusion using accepted local sky boxes.",
            "A failed gate is a statement about this automated transfer, not evidence that WISE, Spitzer, SPARC, or the galaxy is wrong.",
        ],
    }
    return result


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--wise-root", type=Path, default=root / "pipeline/output/wise-allwise")
    parser.add_argument("--profiles", type=Path, default=root / "public/data/sparc-profiles")
    parser.add_argument("--coordinates", type=Path, default=root / "pipeline/cache/sparc/simbad-sparc-paper-objects.csv")
    parser.add_argument("--sparc-global", type=Path, default=root / "pipeline/cache/sparc/SPARC_Lelli2016c.mrt")
    parser.add_argument("--cohort", type=Path, default=root / "pipeline/cache/wise-spitzer-photometry-table1.tsv")
    parser.add_argument("--validation", type=Path, default=root / "pipeline/output/wise-sparc-photometry-validation.json")
    parser.add_argument("--output", type=Path, default=root / "pipeline/output/wise-sparc-transfer")
    parser.add_argument("--public-output", type=Path, default=root / "public/data/comparisons")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    geometry_records = load_geometry(args.coordinates)
    global_records = load_sparc_global(args.sparc_global)
    cohort = load_cohort(args.cohort)
    validation = json.loads(args.validation.read_text(encoding="utf-8"))
    selected = tuple(args.only) if args.only else PILOTS
    results = []
    full_results = []
    args.output.mkdir(parents=True, exist_ok=True)
    args.public_output.mkdir(parents=True, exist_ok=True)
    for target_id in selected:
        profile = json.loads((args.profiles / f"{target_id}.json").read_text(encoding="utf-8"))["target"]
        key = canonical_name(profile["sparcId"])
        result = audit_target(root, target_id, geometry_records[key], global_records[key], cohort, validation, args)
        full_results.append(result)
        target_path = args.output / f"{target_id}.json"
        target_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        public_path = args.public_output / f"{target_id}--wise-sparc-transfer.json"
        comparison = comparison_record(result)
        results.append({
            "targetId": target_id,
            "sparcId": result["sparcId"],
            "status": result["status"],
            "failedGates": [name for name, passed in result["gates"].items() if not passed],
            "apertureW1Minus36Mag": result["aperture"]["w1Minus36Mag"],
            "significanceSigma": result["aperture"]["significanceSigma"],
            "audit": str(target_path.resolve()),
            "auditSha256": sha256(target_path),
            "publicPackage": f"/data/comparisons/{public_path.name}",
            "comparison": comparison,
        })
        print(f"[{target_id}] {result['status']}; failed={','.join(results[-1]['failedGates']) or 'none'}")
    synchronize_wise_layer_records(root, args.wise_root, full_results)
    manifest = {
        "schemaVersion": 1,
        "adapterContract": "layers-comparison-audit-v1",
        "createdAt": stable_created_at(args.output / "manifest.json"),
        "cohortExpectation": cohort,
        "targets": results,
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {manifest_path}")


if __name__ == "__main__":
    main()
