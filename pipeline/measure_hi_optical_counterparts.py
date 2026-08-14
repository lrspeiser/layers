#!/usr/bin/env python3
"""Attach optical counterparts to the H I sample and close the Tully-Fisher terms.

``build_hi_gas_comparison.py`` produces gas mass and a rotation-velocity lower
bound for every HICAT/NHICAT detection inside the Rubin footprint, and stops
there because two terms are missing: the stellar mass, and the inclination that
turns the observed line width W50 into a rotation velocity. Both come from an
optical image, and neither needs the bandpass transfer, because this is a
scaling-relation comparison rather than a photometric difference.

Optical cutouts are requested at the **H I position**, not at a Rubin tract
centre. HIPASS has a 15.5 arcmin beam and its detections sit a median 38 arcmin
from the tract centres the Rubin cutouts were built around, so a tract-centred
cutout contains no counterpart at all.

Two things are handled explicitly because this project has already been bitten
by both:

* **Pixel area.** The Legacy viewer rewrites the output WCS when ``pixscale`` is
  requested but preserves the 0.262 arcsec coadd values, so flux per requested
  pixel needs the area factor. Omitting it understated every Rubin/Legacy flux
  ratio by 2.33x elsewhere in this pipeline.
* **Counterpart ambiguity.** The HIPASS beam contains many galaxies. The
  brightest extended source near the centre is recorded as the *candidate*
  counterpart together with how much brighter it is than the runner-up, so a
  crowded, ambiguous field is visible in the output rather than silently
  resolved by picking one.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests

warnings.filterwarnings("ignore")

from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import binary_dilation, gaussian_filter, label

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_layer_registration import robust_sigma

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "pipeline/results/hi-gas/manifest.json"
DEFAULT_CACHE = ROOT / "pipeline/results/hi-gas/optical"
DEFAULT_OUTPUT = ROOT / "pipeline/results/hi-gas"
DEFAULT_PUBLIC = ROOT / "public/data/layers/hi-gas/baryonic-tully-fisher.json"

CUTOUT_ENDPOINT = "https://www.legacysurvey.org/viewer/fits-cutout"
LEGACY_NATIVE_PIXEL_SCALE_ARCSEC = 0.262
NANOMAGGY_TO_NJY = 3630.780547701
AB_ZERO_POINT_NJY = 3.63078054770e12

# 512 px is the service cap; 0.7 arcsec/px gives a 6 arcmin field, which covers
# the HIPASS positional uncertainty of roughly 1 to 2 arcmin.
REQUEST_PIXELS = 512
REQUEST_PIXEL_SCALE_ARCSEC = 0.7
REQUEST_BANDS = "grz"
SEARCH_RADIUS_ARCMIN = 2.0
REQUEST_PAUSE_SECONDS = 0.5

# Solar absolute magnitude in the r band, AB.
SOLAR_ABSOLUTE_R_MAG = 4.65
# A single declared mass-to-light ratio, stated rather than fitted, so the
# stellar term is reproducible and its assumption is visible.
STELLAR_MASS_TO_LIGHT_R = 1.0
STELLAR_MASS_TO_LIGHT_SYSTEMATIC_DEX = 0.15

HELIUM_CORRECTION = 1.36
BTFR_NORMALISATION = 47.0
BTFR_SLOPE = 4.0
BTFR_INTRINSIC_SCATTER_DEX = 0.11
# Below this axis ratio the galaxy is close to face-on and the sin(i) correction
# blows up, so the deprojected velocity is not usable.
MIN_INCLINATION_DEG = 30.0

# Discriminants. The first full run ranked 35 objects as noteworthy and every one
# at the top was a measurement failure with the same signature: an axis ratio
# below any physical disk, which pins the inclination at 90 degrees and so
# minimises the predicted mass, together with tens of candidate segments in the
# field, which inflates the measured flux by sweeping up neighbours. Both push
# the residual positive, which is also the sign of the sample median.
#
# Thresholds are set from the measured distributions of this sample, not from
# priors. A first attempt guessed them and kept 1 object out of 283.
#
# Real disks do not go below about 0.15 in axis ratio; anything flatter is a
# blend, a diffraction spike, or a segmentation failure. (Keeps 252 of 283.)
MIN_PHYSICAL_AXIS_RATIO = 0.15
# An unambiguous counterpart should dominate its field. The median ratio is 3.66,
# so this keeps the better half. (Keeps 155 of 283.)
MIN_FLUX_RATIO_TO_RUNNER_UP = 3.0
# Candidate count is deliberately NOT a discriminant. The median field has 39
# segments above 3 sigma in a 6 arcmin Legacy cutout, which measures how crowded
# the sky is, not whether this counterpart is ambiguous. Using it as a cut
# removed 280 of 283 objects while telling us nothing about any of them.


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_cutout(session: requests.Session, cache: Path, ra: float, dec: float, name: str) -> Path | None:
    path = cache / f"{name}.fits"
    if path.is_file() and path.stat().st_size > 0:
        return path
    params = {
        "ra": f"{ra:.7f}",
        "dec": f"{dec:.7f}",
        "size": REQUEST_PIXELS,
        "layer": "ls-dr10",
        "pixscale": REQUEST_PIXEL_SCALE_ARCSEC,
        "bands": REQUEST_BANDS,
    }
    try:
        response = session.get(CUTOUT_ENDPOINT, params=params, timeout=180)
        if response.status_code != 200 or len(response.content) < 10000:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    except Exception:
        return None
    finally:
        time.sleep(REQUEST_PAUSE_SECONDS)
    return path if path.is_file() else None


def measure_counterpart(path: Path, ra: float, dec: float) -> dict[str, Any] | None:
    with fits.open(path, memmap=False) as hdus:
        cube = np.asarray(hdus[0].data, dtype=np.float64)
        wcs = WCS(hdus[0].header).celestial
    if cube.ndim != 3 or cube.shape[0] < 3:
        return None
    # bands are g, r, z in request order; r drives the luminosity.
    r_band = cube[1]
    if not np.isfinite(r_band).any():
        return None

    pixel_scale = float(np.mean(np.abs(np.diag(wcs.pixel_scale_matrix))) * 3600.0)
    area_factor = (pixel_scale / LEGACY_NATIVE_PIXEL_SCALE_ARCSEC) ** 2
    flux = np.nan_to_num(r_band, nan=0.0) * NANOMAGGY_TO_NJY * area_factor

    smooth = gaussian_filter(flux, 2.0)
    background = gaussian_filter(flux, 25.0)
    detection = smooth - background
    sigma = robust_sigma(detection[np.isfinite(detection)])
    if not np.isfinite(sigma) or sigma <= 0:
        return None
    mask = binary_dilation(detection > 3.0 * sigma, iterations=2)
    structures, count = label(mask)
    if count == 0:
        return None

    centre = np.array(wcs.world_to_pixel_values(ra, dec), dtype=float)
    radius_pixels = SEARCH_RADIUS_ARCMIN * 60.0 / pixel_scale
    candidates = []
    for index in range(1, count + 1):
        pixels = np.argwhere(structures == index)
        if pixels.shape[0] < 20:
            continue
        yy, xx = pixels[:, 0], pixels[:, 1]
        weights = np.clip(flux[yy, xx], 0, None)
        total = float(weights.sum())
        if total <= 0:
            continue
        cx = float((xx * weights).sum() / total)
        cy = float((yy * weights).sum() / total)
        offset = math.hypot(cx - centre[0], cy - centre[1])
        if offset > radius_pixels:
            continue
        var_x = float((((xx - cx) ** 2) * weights).sum() / total)
        var_y = float((((yy - cy) ** 2) * weights).sum() / total)
        covariance = float((((xx - cx) * (yy - cy)) * weights).sum() / total)
        eigenvalues = np.linalg.eigvalsh([[var_x, covariance], [covariance, var_y]])
        if eigenvalues[0] <= 0:
            continue
        axis_ratio = math.sqrt(float(eigenvalues[0] / eigenvalues[1]))
        candidates.append({
            "fluxNjy": total,
            "offsetArcmin": offset * pixel_scale / 60.0,
            "axisRatio": axis_ratio,
            "areaPixels": int(pixels.shape[0]),
        })
    if not candidates:
        return None
    candidates.sort(key=lambda item: item["fluxNjy"], reverse=True)
    best = candidates[0]
    runner_up = candidates[1]["fluxNjy"] if len(candidates) > 1 else 0.0
    best["fluxRatioToRunnerUp"] = (best["fluxNjy"] / runner_up) if runner_up > 0 else None
    best["candidateCount"] = len(candidates)
    best["pixelScaleArcsec"] = pixel_scale
    best["pixelAreaFactor"] = area_factor
    return best


def inclination_deg(axis_ratio: float, intrinsic: float = 0.2) -> float | None:
    if axis_ratio >= 1.0:
        return 0.0
    numerator = axis_ratio**2 - intrinsic**2
    denominator = 1.0 - intrinsic**2
    if denominator <= 0:
        return None
    value = numerator / denominator
    if value < 0:
        return 90.0
    return math.degrees(math.acos(math.sqrt(value)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    detections = payload["detections"][: args.limit] if args.limit else payload["detections"]
    session = requests.Session()
    session.headers["User-Agent"] = "layers-hi-optical/1.0 (research; contact via repository)"

    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, detection in enumerate(detections, start=1):
        name = detection["id"].replace("/", "_").replace(" ", "")
        ra = detection["position"]["raDeg"]
        dec = detection["position"]["decDeg"]
        path = fetch_cutout(session, args.cache, ra, dec, name)
        if path is None:
            skipped.append({"id": detection["id"], "reason": "no Legacy cutout returned"})
            continue
        try:
            counterpart = measure_counterpart(path, ra, dec)
        except Exception as error:  # noqa: BLE001
            skipped.append({"id": detection["id"], "reason": f"{type(error).__name__}: {error}"})
            continue
        if counterpart is None:
            skipped.append({"id": detection["id"], "reason": "no extended source within the search radius"})
            continue

        distance = detection["derived"]["hubbleFlowDistanceMpc"]
        flux_njy = counterpart["fluxNjy"]
        apparent = -2.5 * math.log10(flux_njy / AB_ZERO_POINT_NJY)
        absolute = apparent - 5.0 * math.log10(distance * 1e6 / 10.0)
        luminosity = 10 ** (-0.4 * (absolute - SOLAR_ABSOLUTE_R_MAG))
        stellar_mass = STELLAR_MASS_TO_LIGHT_R * luminosity

        gas_mass = detection["derived"]["gasMassMsun"]
        baryonic_mass = stellar_mass + gas_mass

        angle = inclination_deg(counterpart["axisRatio"])
        width = detection["observed"]["lineWidthW50KmS"]
        usable = angle is not None and angle >= MIN_INCLINATION_DEG
        rotation = (width / 2.0 / math.sin(math.radians(angle))) if usable else None

        record = {
            "id": detection["id"],
            "tract": detection["tract"],
            "position": detection["position"],
            "distanceMpc": distance,
            "optical": {
                "band": "r",
                "fluxNjy": flux_njy,
                "apparentMagAB": apparent,
                "absoluteMagAB": absolute,
                "luminosityLsun": luminosity,
                "axisRatio": counterpart["axisRatio"],
                "inclinationDeg": angle,
                "offsetFromHiArcmin": counterpart["offsetArcmin"],
                "candidateCount": counterpart["candidateCount"],
                "fluxRatioToRunnerUp": counterpart["fluxRatioToRunnerUp"],
                "pixelAreaFactorApplied": counterpart["pixelAreaFactor"],
            },
            "masses": {
                "stellarMassMsun": stellar_mass,
                "gasMassMsun": gas_mass,
                "baryonicMassMsun": baryonic_mass,
                "logBaryonicMassMsun": math.log10(baryonic_mass) if baryonic_mass > 0 else None,
                "massToLightR": STELLAR_MASS_TO_LIGHT_R,
            },
            "rotation": {
                "lineWidthW50KmS": width,
                "inclinationCorrectedKmS": rotation,
                "usable": usable,
                "minimumInclinationDeg": MIN_INCLINATION_DEG,
            },
        }
        # Reasons this object's measurement cannot be trusted, recorded per object
        # rather than applied as a silent cut, so the sample stays auditable.
        flags = []
        if counterpart["axisRatio"] < MIN_PHYSICAL_AXIS_RATIO:
            flags.append("axis ratio below any physical disk; segmentation likely blended or spurious")
        if angle is not None and angle >= 89.9:
            flags.append("inclination pinned at the 90 degree clamp, so the velocity correction is a limit")
        ratio = counterpart.get("fluxRatioToRunnerUp")
        if ratio is not None and ratio < MIN_FLUX_RATIO_TO_RUNNER_UP:
            flags.append("counterpart does not dominate its field; another source is comparably bright")
        record["measurementFlags"] = flags

        if usable and baryonic_mass > 0:
            predicted = BTFR_NORMALISATION * rotation**BTFR_SLOPE
            residual = math.log10(baryonic_mass) - math.log10(predicted)
            record["baryonicTullyFisher"] = {
                "relation": "M_bar = 47 * V^4 (McGaugh 2012), held fixed and not fitted to this sample",
                "logPredictedMsun": math.log10(predicted),
                "residualDex": residual,
                "assumedSystematicDex": math.sqrt(
                    STELLAR_MASS_TO_LIGHT_SYSTEMATIC_DEX**2 + BTFR_INTRINSIC_SCATTER_DEX**2
                ),
            }
        records.append(record)
        if index % 25 == 0:
            print(f"  {index}/{len(detections)} processed, {len(records)} measured", flush=True)

    usable_records = [item for item in records if "baryonicTullyFisher" in item]
    clean_records = [item for item in usable_records if not item["measurementFlags"]]
    # Calibrate and rank on the clean subset only. Including flagged objects would
    # let segmentation failures set both the offset and the scatter that every
    # other object is judged against.
    # Falling back to the flagged sample must be visible. A silent fallback would
    # let segmentation failures set the calibration while the output still claimed
    # to rank a clean subset.
    ranking_fell_back = len(clean_records) < 20
    if ranking_fell_back:
        print(
            f"WARNING: only {len(clean_records)} objects passed the measurement discriminants; "
            "ranking against the full flagged sample instead, which is not trustworthy",
            flush=True,
        )
    ranking_records = usable_records if ranking_fell_back else clean_records
    residuals = np.array([item["baryonicTullyFisher"]["residualDex"] for item in ranking_records])

    # The uncertainty has to be measured, not asserted. Declaring a systematic
    # budget and ranking against it is how a sample-wide offset turns into a page
    # of false discoveries: with an assumed 0.19 dex and an observed scatter near
    # 0.7 dex, every significance would be inflated about fourfold.
    #
    # The sample median absorbs the combined calibration of the mass-to-light
    # ratio, the aperture flux, and the distance scale. Subtracting it means the
    # absolute normalisation of the relation is explicitly NOT being tested; only
    # departures relative to the rest of the sample are. Ranking then uses the
    # observed scatter of this sample rather than a number chosen in advance.
    offset = float(np.median(residuals)) if residuals.size else 0.0
    observed_scatter = float(robust_sigma(residuals)) if residuals.size > 3 else float("nan")

    # A residual that correlates with the relation's own x-axis is not measuring
    # a departure in mass; it is measuring a problem in velocity. M_bar = 47*V^4,
    # so residual = log M_bar - 4*log V, and if the sample's velocities are
    # systematically wrong at one end the residual inherits that trend wholesale.
    # W50 is known to underestimate rotation in low-mass gas-rich dwarfs, whose
    # H I never reaches the flat part of the rotation curve.
    #
    # While this correlation is strong, no individual object can be called
    # noteworthy: it would just be a member of the low-velocity tail.
    velocities = np.array(
        [item["rotation"]["inclinationCorrectedKmS"] for item in ranking_records], dtype=float
    )
    finite = np.isfinite(residuals) & np.isfinite(velocities) & (velocities > 0)
    velocity_correlation = (
        float(np.corrcoef(residuals[finite], np.log10(velocities[finite]))[0, 1])
        if finite.sum() > 5
        else float("nan")
    )
    systematics_dominated = bool(np.isfinite(velocity_correlation) and abs(velocity_correlation) > 0.3)
    assumed = math.sqrt(STELLAR_MASS_TO_LIGHT_SYSTEMATIC_DEX**2 + BTFR_INTRINSIC_SCATTER_DEX**2)
    for item in usable_records:
        entry = item["baryonicTullyFisher"]
        relative = entry["residualDex"] - offset
        entry["sampleCalibrationOffsetDex"] = offset
        entry["relativeResidualDex"] = relative
        entry["observedScatterDex"] = observed_scatter
        if not np.isfinite(observed_scatter) or observed_scatter <= 0:
            entry["significanceSigma"] = None
            entry["classification"] = "uncalibrated"
        elif item["measurementFlags"]:
            entry["significanceSigma"] = relative / observed_scatter
            entry["classification"] = "flagged"
        elif systematics_dominated:
            entry["significanceSigma"] = relative / observed_scatter
            entry["classification"] = "systematics-dominated"
        else:
            entry["significanceSigma"] = relative / observed_scatter
            entry["classification"] = (
                "noteworthy" if abs(relative) > 2 * observed_scatter else "expected"
            )
    summary = {
        "schemaVersion": "layers-hi-baryonic-tully-fisher-v1",
        "generatedAt": utc_now(),
        "comparisonKind": "scaling-relation residual",
        "gatedByBandpassTransfer": False,
        "counts": {
            "attempted": len(detections),
            "opticalCounterpartMeasured": len(records),
            "withUsableInclination": len(usable_records),
            "skipped": len(skipped),
        },
        "assumptions": {
            "opticalSurvey": "Legacy Survey DR10 r band",
            "requestPixelScaleArcsec": REQUEST_PIXEL_SCALE_ARCSEC,
            "legacyNativePixelScaleArcsec": LEGACY_NATIVE_PIXEL_SCALE_ARCSEC,
            "pixelAreaFactorApplied": True,
            "stellarMassToLightR": STELLAR_MASS_TO_LIGHT_R,
            "solarAbsoluteRMag": SOLAR_ABSOLUTE_R_MAG,
            "heliumCorrection": HELIUM_CORRECTION,
            "minimumInclinationDeg": MIN_INCLINATION_DEG,
            "btfr": {"normalisation": BTFR_NORMALISATION, "slope": BTFR_SLOPE},
        },
        "residual": {
            "medianDex": offset,
            "observedScatterDex": observed_scatter,
            "assumedSystematicDex": assumed,
            "assumedUnderstatesObservedBy": (observed_scatter / assumed) if np.isfinite(observed_scatter) else None,
            "absoluteNormalisationTested": False,
            "rankedOnCleanSubset": len(ranking_records),
            "rankingFellBackToFlaggedSample": ranking_fell_back,
            "residualVersusLogVelocityCorrelation": velocity_correlation,
            "systematicsDominated": systematics_dominated,
            "systematicsNote": (
                "The residual correlates with log V at "
                f"{velocity_correlation:+.3f}. M_bar = 47*V^4 means residual = log M_bar - 4*log V, so a "
                "trend against V is a velocity systematic rather than a departure in baryonic mass. W50 "
                "underestimates rotation in low-mass gas-rich dwarfs whose H I never reaches the flat "
                "rotation curve. No object is classified noteworthy while this holds."
            ),
            "flaggedExcludedFromRanking": len(usable_records) - len(clean_records),
            "noteworthy": sum(
                1 for item in usable_records if item["baryonicTullyFisher"].get("classification") == "noteworthy"
            ),
            "note": (
                "The sample median is subtracted as a calibration, so this ranks relative departures "
                "only. A median offset of this size is a statement about the mass-to-light ratio, the "
                "aperture photometry, and the distance scale, not about the galaxies."
            ),
        },
        "caveats": [
            "A single declared mass-to-light ratio is applied to every galaxy; no colour-dependent "
            "stellar population model is fitted, and that is the dominant systematic.",
            "The optical counterpart is the brightest extended source inside the search radius. The "
            "HIPASS beam holds many galaxies, so fluxRatioToRunnerUp and candidateCount must be read "
            "before trusting any individual object.",
            "Distances are Hubble-flow only, so peculiar velocities are unmodelled and nearby objects "
            "carry the largest distance error.",
            "A residual is an observation about the baryon budget of these objects, not a measurement "
            "of dark matter and not a test of any specific gravity model.",
        ],
        "skipped": skipped,
        "objects": records,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "baryonic-tully-fisher.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nmeasured {len(records)} optical counterparts, {len(usable_records)} with usable inclination")
    if residuals.size:
        print(f"BTFR residual: median {offset:+.3f} dex (absorbed as calibration), observed scatter {observed_scatter:.3f} dex")
        print(f"assumed systematic {assumed:.3f} dex understates the observed scatter by {observed_scatter / assumed:.1f}x")
        print(f"ranked on {len(ranking_records)} clean objects; {len(usable_records) - len(clean_records)} flagged and excluded")
        print(f"residual vs log V correlation: {velocity_correlation:+.3f}")
        if systematics_dominated:
            print("SYSTEMATICS-DOMINATED: residual tracks velocity, so no object is called noteworthy")
        print(f"noteworthy relative departures (>2 observed sigma): {summary['residual']['noteworthy']}")


if __name__ == "__main__":
    main()
