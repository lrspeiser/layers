#!/usr/bin/env python3
"""Deliver an independent-resolution verdict for each verifiable candidate.

``check_highres_followup.py`` answers whether high-resolution imaging exists at a
candidate's position. That is a coverage map, not a verdict, and stopping there
leaves the actual question unanswered. This fetches the images and measures them.

**What a verdict is here.** Rubin's PSF in these products is around 2 arcsec.
HST resolves tenths of an arcsecond, so it can settle two things Rubin cannot:

* *Is anything there at all?* Measured as aperture flux at the candidate position
  against the scatter of the same aperture at blank positions in the same image.
  For an X-ray source with no Rubin counterpart, a detection here refutes the
  candidate and a non-detection deepens it.
* *Is the Rubin measurement a blend?* Counting resolved peaks inside one Rubin
  PSF. A candidate whose Rubin photometry actually covers several HST sources is
  explained by blending, which no amount of Rubin analysis could have revealed.

**The filter matters more than the exposure count.** A position with 25 HST
observations sounds decisive until the observations turn out to be near-UV. A
non-detection in F300W does not mean a non-detection in the optical, and the
verdict records the filter and says so rather than reporting the count alone.
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests

warnings.filterwarnings("ignore")

from astropy.io import fits
from astropy.io.votable import parse as parse_votable
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy.ndimage import binary_dilation, gaussian_filter, label, maximum_filter

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_layer_registration import robust_sigma

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COVERAGE = ROOT / "public/data/layers/highres-followup/coverage.json"
DEFAULT_CACHE = ROOT / "pipeline/results/highres-followup/images"
DEFAULT_OUTPUT = ROOT / "pipeline/results/highres-followup"
DEFAULT_PUBLIC = ROOT / "public/data/layers/highres-followup/verdicts.json"

HLA_SIAP = "https://hla.stsci.edu/cgi-bin/hlaSIAP.cgi"
SEARCH_SIZE_DEG = 0.01
APERTURE_ARCSEC = 1.0
DETECTION_SIGMA = 5.0
# One Rubin PSF. Resolved peaks inside this are what Rubin merged into one source.
RUBIN_PSF_ARCSEC = 2.0

# Filters that actually constrain an optical counterpart. A near-UV
# non-detection says much less, and the verdict is downgraded accordingly.
OPTICAL_FILTERS = {"F555W", "F606W", "F625W", "F775W", "F814W", "F850LP", "F435W", "F110W", "F160W"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_images(cache: Path, ra: float, dec: float, key: str) -> list[dict[str, Any]]:
    path = cache / f"{key}-siap.vot"
    if not path.is_file():
        response = requests.get(
            HLA_SIAP,
            params={"POS": f"{ra},{dec}", "SIZE": SEARCH_SIZE_DEG, "FORMAT": "image/fits", "imagetype": "combined"},
            timeout=180,
        )
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    table = parse_votable(str(path)).get_first_table().to_table()
    rows = []
    for row in table:
        spectral = str(row["Spectral_Elt"])
        if spectral.lower() == "detection":
            continue  # a detection-stack, not a calibrated science image
        rows.append({
            "url": str(row["URL"]),
            "detector": str(row["Detector"]),
            "filter": spectral,
            "exposureSeconds": float(row["ExpTime"]),
            "title": str(row["Title"]),
        })
    rows.sort(key=lambda item: -item["exposureSeconds"])
    return rows


def measure(path: Path, ra: float, dec: float) -> dict[str, Any] | None:
    with fits.open(path, memmap=False) as hdus:
        image = None
        header = None
        for hdu in hdus:
            data = getattr(hdu, "data", None)
            if data is not None and np.ndim(data) == 2:
                image = np.asarray(data, dtype=np.float64)
                header = hdu.header
                break
    if image is None:
        return None
    wcs = WCS(header).celestial
    if not wcs.has_celestial:
        return None
    scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
    x, y = wcs.world_to_pixel_values(ra, dec)
    if not (np.isfinite(x) and np.isfinite(y)):
        return None
    radius = max(1.5, APERTURE_ARCSEC / scale)
    height, width = image.shape
    if not (radius + 2 <= x < width - radius - 2 and radius + 2 <= y < height - radius - 2):
        # Outside the frame. Reported rather than swallowed, because an archive
        # pointing near a position is not the same as an image containing it.
        return {
            "outsideImage": True,
            "pixel": [float(x), float(y)],
            "shape": [int(height), int(width)],
            "offEdgeArcsec": round(
                max(0.0, max(-x, x - width, -y, y - height)) * scale, 1
            ),
        }

    def aperture_sum(cx: float, cy: float) -> float | None:
        lo_y, hi_y = int(cy - radius) - 1, int(cy + radius) + 2
        lo_x, hi_x = int(cx - radius) - 1, int(cx + radius) + 2
        if lo_y < 0 or lo_x < 0 or hi_y > height or hi_x > width:
            return None
        yy, xx = np.mgrid[lo_y:hi_y, lo_x:hi_x]
        inside = np.hypot(xx - cx, yy - cy) <= radius
        values = image[lo_y:hi_y, lo_x:hi_x][inside]
        finite = np.isfinite(values)
        if finite.sum() < 0.8 * inside.sum():
            return None
        return float(values[finite].sum())

    flux = aperture_sum(float(x), float(y))
    if flux is None:
        return None

    generator = np.random.default_rng(20260814)
    margin = int(radius) + 3
    blanks = []
    for _ in range(1500):
        if len(blanks) >= 200:
            break
        bx = float(generator.integers(margin, width - margin))
        by = float(generator.integers(margin, height - margin))
        value = aperture_sum(bx, by)
        if value is not None and np.isfinite(value):
            blanks.append(value)
    if len(blanks) < 30:
        return None
    noise = float(robust_sigma(np.asarray(blanks)))
    median = float(np.median(blanks))
    significance = (flux - median) / noise if noise > 0 else float("nan")

    # Resolved peaks inside one Rubin PSF: what Rubin would have merged.
    box = int(RUBIN_PSF_ARCSEC / scale)
    lo_y, hi_y = max(0, int(y) - box), min(height, int(y) + box + 1)
    lo_x, hi_x = max(0, int(x) - box), min(width, int(x) + box + 1)
    stamp = image[lo_y:hi_y, lo_x:hi_x]
    peaks = 0
    if stamp.size > 25 and np.isfinite(stamp).any():
        smooth = gaussian_filter(np.nan_to_num(stamp), 1.0)
        sigma = robust_sigma(smooth[np.isfinite(smooth)])
        if np.isfinite(sigma) and sigma > 0:
            local = (smooth == maximum_filter(smooth, size=3)) & (smooth > median + 5 * sigma)
            _, peaks = label(binary_dilation(local, iterations=1))

    return {
        "pixelScaleArcsec": scale,
        "apertureRadiusArcsec": round(radius * scale, 3),
        "apertureFlux": flux,
        "blankMedian": median,
        "blankScatter": noise,
        "significance": significance,
        "detected": bool(np.isfinite(significance) and significance >= DETECTION_SIGMA),
        "resolvedPeaksWithinOneRubinPsf": int(peaks),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--max-images", type=int, default=2)
    args = parser.parse_args()

    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    verifiable = [item for item in coverage["candidates"] if item.get("verifiable")]

    verdicts: list[dict[str, Any]] = []
    for candidate in verifiable:
        ra = candidate["position"]["raDeg"]
        dec = candidate["position"]["decDeg"]
        key = f"{candidate['operator']}-{ra:.5f}{dec:+.5f}"
        try:
            images = list_images(args.cache, ra, dec, key)
        except Exception as error:
            verdicts.append({**candidate, "verdict": "unresolved", "reason": f"image list failed: {error}"})
            continue
        if not images:
            verdicts.append({**candidate, "verdict": "unresolved", "reason": "no calibrated science image at this position"})
            continue

        measurements = []
        for image in images[: args.max_images]:
            path = args.cache / f"{key}-{image['filter']}-{int(image['exposureSeconds'])}.fits"
            if not path.is_file():
                try:
                    response = requests.get(image["url"], timeout=300)
                    response.raise_for_status()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(response.content)
                except Exception:
                    continue
            try:
                result = measure(path, ra, dec)
            except Exception:
                result = None
            if result:
                measurements.append({**image, **result})

        inside = [item for item in measurements if not item.get("outsideImage")]
        if measurements and not inside:
            worst = min(measurements, key=lambda item: item.get("offEdgeArcsec", 1e9))
            verdicts.append({
                **candidate,
                "verdict": "not-covered",
                "reason": (
                    f"{len(measurements)} HST image(s) were returned for this position and none of "
                    f"them contains it; the nearest falls {worst.get('offEdgeArcsec')} arcsec off the "
                    f"frame edge. The archive records a pointing nearby, not pixels here."
                ),
                "measurements": measurements,
            })
            print(f"[not-covered] {candidate['operator']} at {ra:.5f}{dec:+.5f}: outside every returned frame", flush=True)
            continue
        if not inside:
            verdicts.append({**candidate, "verdict": "unresolved", "reason": "no image could be measured at this position"})
            continue
        measurements = inside

        best = max(measurements, key=lambda item: item["exposureSeconds"])
        optical = best["filter"].upper() in OPTICAL_FILTERS
        detected = any(item["detected"] for item in measurements)
        peaks = max(item["resolvedPeaksWithinOneRubinPsf"] for item in measurements)

        if detected:
            verdict = "refuted"
            reason = (
                f"HST detects a source at this position at {best['significance']:.1f} sigma in "
                f"{best['detector']} {best['filter']}, so the Rubin non-detection is a depth or "
                f"resolution limit rather than an absence."
            )
        elif not optical:
            verdict = "inconclusive"
            reason = (
                f"No HST source at {DETECTION_SIGMA:.0f} sigma, but the deepest available filter is "
                f"{best['filter']} on {best['detector']}, which is not an optical band. A near-UV "
                f"non-detection does not establish an optical one."
            )
        else:
            verdict = "survives"
            reason = (
                f"No HST source at {DETECTION_SIGMA:.0f} sigma in {best['detector']} {best['filter']} "
                f"({best['exposureSeconds']:.0f}s), which is an optical band, so the candidate "
                f"survives an independent check at higher resolution."
            )
        if peaks > 1:
            reason += f" HST resolves {peaks} peaks inside one Rubin PSF, so the Rubin measurement is a blend."

        verdicts.append({
            **candidate,
            "verdict": verdict,
            "reason": reason,
            "opticalFilterAvailable": optical,
            "resolvedPeaksWithinOneRubinPsf": peaks,
            "measurements": measurements,
        })
        print(f"[{verdict}] {candidate['operator']} at {ra:.5f}{dec:+.5f}: {reason}", flush=True)

    unverifiable = [item for item in coverage["candidates"] if not item.get("verifiable")]
    summary = {
        "schemaVersion": "layers-highres-verdicts-v1",
        "generatedAt": utc_now(),
        "archive": "Hubble Legacy Archive combined images via SIAP",
        "detectionSigma": DETECTION_SIGMA,
        "rubinPsfArcsec": RUBIN_PSF_ARCSEC,
        "counts": {
            "verifiable": len(verifiable),
            "verdictsDelivered": len(verdicts),
            "refuted": sum(1 for item in verdicts if item["verdict"] == "refuted"),
            "survives": sum(1 for item in verdicts if item["verdict"] == "survives"),
            "inconclusive": sum(1 for item in verdicts if item["verdict"] == "inconclusive"),
            "unresolved": sum(1 for item in verdicts if item["verdict"] == "unresolved"),
            "notCovered": sum(1 for item in verdicts if item["verdict"] == "not-covered"),
            "notVerifiable": len(unverifiable),
        },
        "interpretation": (
            "A verdict of survives means one independent check at higher resolution did not kill the "
            "candidate, not that it is real. Inconclusive means imaging exists but not in a band that "
            "settles the question, which is a different and honest outcome from either."
        ),
        "verdicts": verdicts,
        "notVerifiable": [
            {"operator": item["operator"], "position": item["position"], "reason": item["verdict"]}
            for item in unverifiable
        ],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "verdicts.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(verdicts)} verdicts delivered of {len(verifiable)} verifiable candidates")
    for key, value in summary["counts"].items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
