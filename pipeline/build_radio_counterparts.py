#!/usr/bin/env python3
"""Ask whether each VLASS radio source has an optical counterpart in Rubin.

The radio half of the counterpart operator. The X-ray half used a published
catalogue; this one cannot, because the CIRADA component catalogue that serves
VLASS detections is unreachable. Sources are therefore detected in the cutout
pixels directly, which is a different kind of input and is treated as one.

**Detecting rather than looking up changes what must be proven.** A catalogue
entry has survived someone else's vetting. A peak found here has not, so the
threshold is set from the image's own robust noise, sources are required to be
resolved above the beam, and the count of detections per field is reported so an
implausible yield is visible rather than silently becoming 200 candidates.

**Radio and optical are not the same emission.** A radio source with no optical
counterpart is an association result, not a photometric difference, and the
interesting cases are the ones where a genuine radio detection has no optical
light down to a stated depth. That depth comes from the scatter of the identical
aperture at blank positions in the Rubin image, never from the variance planes,
which understate the true uncertainty here by a median factor of about six.

VLASS quick-look images carry a few-percent flux-scale uncertainty and are not
uniformly primary-beam corrected, so the radio flux reported is indicative.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

warnings.filterwarnings("ignore")

from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy.ndimage import binary_dilation, label, maximum_filter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_layer_registration import robust_sigma

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VLASS = ROOT / "pipeline/results/vlass/manifest.json"
DEFAULT_RUBIN = ROOT / "pipeline/results/rubin-pixels-200/manifest.json"
DEFAULT_OUTPUT = ROOT / "pipeline/results/radio-counterparts"
DEFAULT_PUBLIC = ROOT / "public/data/layers/radio-counterparts/comparison.json"

RADIO_DETECTION_SIGMA = 7.0
# VLASS quick-look resolution is about 2.5 arcsec; a detection narrower than the
# beam is not a source.
VLASS_BEAM_ARCSEC = 2.5
OPTICAL_APERTURE_ARCSEC = 2.0
OPTICAL_DETECTION_SIGMA = 5.0
AB_ZERO_POINT_NJY = 3.63078054770e12
# Above this, a field is producing more detections than VLASS plausibly holds in
# 4 arcmin and is treated as noise-dominated rather than rich.
MAX_DETECTIONS_PER_FIELD = 25


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def aperture(image: np.ndarray, x: float, y: float, radius: float) -> float | None:
    height, width = image.shape
    lo_y, hi_y = max(0, int(y - radius) - 1), min(height, int(y + radius) + 2)
    lo_x, hi_x = max(0, int(x - radius) - 1), min(width, int(x + radius) + 2)
    if hi_y - lo_y < 3 or hi_x - lo_x < 3:
        return None
    yy, xx = np.mgrid[lo_y:hi_y, lo_x:hi_x]
    inside = np.hypot(xx - x, yy - y) <= radius
    values = image[lo_y:hi_y, lo_x:hi_x][inside]
    finite = np.isfinite(values)
    if finite.sum() < 0.8 * inside.sum():
        return None
    return float(values[finite].sum())


def blank_scatter(image: np.ndarray, radius: float, seed: int, trials: int = 150) -> float:
    generator = np.random.default_rng(seed)
    height, width = image.shape
    margin = int(radius) + 3
    if height <= 2 * margin or width <= 2 * margin:
        return float("nan")
    draws = []
    for _ in range(trials * 6):
        if len(draws) >= trials:
            break
        value = aperture(
            image,
            float(generator.integers(margin, width - margin)),
            float(generator.integers(margin, height - margin)),
            radius,
        )
        if value is not None and np.isfinite(value):
            draws.append(value)
    return float(robust_sigma(np.asarray(draws))) if len(draws) >= 30 else float("nan")


def detect_radio(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    with fits.open(path, memmap=False) as hdus:
        data = np.squeeze(np.asarray(hdus[0].data, dtype=np.float64))
        wcs = WCS(hdus[0].header).celestial
    if data.ndim != 2 or not wcs.has_celestial:
        return None
    scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
    finite = np.isfinite(data)
    if finite.sum() < 0.5 * data.size:
        return None
    noise = robust_sigma(data[finite])
    if not np.isfinite(noise) or noise <= 0:
        return None

    beam_pixels = max(1, int(round(VLASS_BEAM_ARCSEC / scale)))
    peaks = (data == maximum_filter(data, size=beam_pixels)) & (data > RADIO_DETECTION_SIGMA * noise) & finite
    structures, count = label(binary_dilation(peaks, iterations=1))
    sources = []
    for index in range(1, count + 1):
        pixels = np.argwhere(structures == index)
        # A single pixel above threshold is narrower than the beam and cannot be
        # a real radio source at this resolution.
        if pixels.shape[0] < 2:
            continue
        yy, xx = pixels[:, 0], pixels[:, 1]
        weights = np.clip(data[yy, xx], 0, None)
        total = float(weights.sum())
        if total <= 0:
            continue
        cx = float((xx * weights).sum() / total)
        cy = float((yy * weights).sum() / total)
        ra, dec = wcs.pixel_to_world_values(cx, cy)
        sources.append({
            "raDeg": float(ra),
            "decDeg": float(dec),
            "peakJyPerBeam": float(np.nanmax(data[yy, xx])),
            "significance": float(np.nanmax(data[yy, xx]) / noise),
            "areaPixels": int(pixels.shape[0]),
        })
    sources.sort(key=lambda item: -item["significance"])
    return sources, {"noiseJyPerBeam": float(noise), "pixelScaleArcsec": scale, "beamPixels": beam_pixels}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vlass", type=Path, default=DEFAULT_VLASS)
    parser.add_argument("--rubin-manifest", type=Path, default=DEFAULT_RUBIN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    args = parser.parse_args()

    vlass = json.loads(args.vlass.read_text(encoding="utf-8"))["regions"]
    rubin = {
        item["regionId"]: item
        for item in json.loads(args.rubin_manifest.read_text(encoding="utf-8"))["regions"]
        if item.get("validation", {}).get("scienceReady") and item.get("mosaic")
    }

    matches: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    crowded: list[str] = []
    fields = 0
    for region in vlass:
        region_id = region["regionId"]
        if not region.get("scienceReady") or region_id not in rubin:
            continue
        try:
            found = detect_radio(ROOT / region["localFits"]["path"])
        except Exception as error:
            skipped.append({"regionId": region_id, "reason": f"{type(error).__name__}: {error}"})
            continue
        if found is None:
            skipped.append({"regionId": region_id, "reason": "radio image unusable"})
            continue
        sources, radio_meta = found
        fields += 1
        if len(sources) > MAX_DETECTIONS_PER_FIELD:
            crowded.append(region_id)
            skipped.append({"regionId": region_id,
                            "reason": f"{len(sources)} radio detections in 4 arcmin, treated as noise-dominated"})
            continue
        if not sources:
            continue

        with fits.open(ROOT / rubin[region_id]["mosaic"]["path"], memmap=False) as hdus:
            optical = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
            mask = np.asarray(hdus["MASK"].data)
            wcs = WCS(hdus["IMAGE"].header).celestial
        optical = np.where((mask & ((1 << 0) | (1 << 3))) == 0, optical, np.nan)
        scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
        radius = OPTICAL_APERTURE_ARCSEC / scale
        noise = blank_scatter(optical, radius, seed=abs(hash(region_id)) % (2**31))
        if not np.isfinite(noise) or noise <= 0:
            skipped.append({"regionId": region_id, "reason": "could not measure optical blank-aperture noise"})
            continue
        threshold = OPTICAL_DETECTION_SIGMA * noise
        limiting = -2.5 * math.log10(threshold / AB_ZERO_POINT_NJY) if threshold > 0 else None

        for source in sources:
            x, y = wcs.world_to_pixel_values(source["raDeg"], source["decDeg"])
            if not (np.isfinite(x) and np.isfinite(y)):
                continue
            if not (radius + 2 <= x < optical.shape[1] - radius - 2 and radius + 2 <= y < optical.shape[0] - radius - 2):
                continue
            flux = aperture(optical, float(x), float(y), radius)
            if flux is None:
                continue
            matches.append({
                "regionId": region_id,
                "tract": region["tract"],
                "position": {"raDeg": source["raDeg"], "decDeg": source["decDeg"]},
                "radio": {
                    "peakJyPerBeam": source["peakJyPerBeam"],
                    "significance": source["significance"],
                    "areaPixels": source["areaPixels"],
                    "imageNoiseJyPerBeam": radio_meta["noiseJyPerBeam"],
                },
                "opticalFluxNjy": flux,
                "opticalThresholdNjy": threshold,
                "opticalSignificance": flux / noise,
                "opticalCounterpart": bool(flux >= threshold),
                "limitingMagAB": limiting,
            })
        print(f"[radio] {region_id} {len(sources)} detections", flush=True)

    detections = [item for item in matches if item["opticalCounterpart"]]
    blanks = [item for item in matches if not item["opticalCounterpart"]]
    summary = {
        "schemaVersion": "layers-radio-counterparts-v1",
        "generatedAt": utc_now(),
        "comparisonKind": "counterpart association",
        "gatedByBandpassTransfer": False,
        "sourceOfPositions": (
            "Detected in the VLASS cutout pixels. The CIRADA component catalogue that would supply "
            "vetted positions is unreachable, so these detections have not survived anyone else's "
            "vetting and the thresholds are correspondingly conservative."
        ),
        "method": {
            "radioDetectionSigma": RADIO_DETECTION_SIGMA,
            "beamArcsec": VLASS_BEAM_ARCSEC,
            "minimumAreaPixels": 2,
            "opticalApertureArcsec": OPTICAL_APERTURE_ARCSEC,
            "opticalDetectionSigma": OPTICAL_DETECTION_SIGMA,
            "opticalNoise": "blank-aperture scatter in the same Rubin image, not the variance planes",
            "maxDetectionsPerField": MAX_DETECTIONS_PER_FIELD,
        },
        "counts": {
            "fieldsSearched": fields,
            "fieldsSkipped": len(skipped),
            "fieldsNoiseDominated": len(crowded),
            "radioSourcesInsideRubinPixels": len(matches),
            "withOpticalCounterpart": len(detections),
            "withoutOpticalCounterpart": len(blanks),
        },
        "limitingMagnitude": {
            "median": float(np.median([item["limitingMagAB"] for item in matches if item["limitingMagAB"]]))
            if matches else None,
        },
        "caveats": [
            "Radio and optical trace different emission, so this is association, not a photometric "
            "difference.",
            "VLASS quick-look flux is uncertain at the few-percent level and primary-beam correction "
            "is not uniform across epochs, so the radio flux is indicative.",
            "Positions are detections rather than catalogue entries, so a spurious radio peak looks "
            "exactly like an optically dark source. Chance coincidence is not yet quantified.",
        ],
        "skipped": skipped,
        "sources": matches,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nfields searched: {fields}, skipped {len(skipped)} ({len(crowded)} noise-dominated)")
    print(f"radio sources inside Rubin pixels: {len(matches)}")
    print(f"  with an optical counterpart:    {len(detections)}")
    print(f"  without, to a stated depth:     {len(blanks)}")


if __name__ == "__main__":
    main()
