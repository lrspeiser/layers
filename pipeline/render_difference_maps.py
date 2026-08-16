#!/usr/bin/env python3
"""Render where Rubin and a reference disagree, as images the site can overlay.

The comparison previews so far show the Rubin frame and the reference frame side
by side and leave the reader to spot the difference by eye. The difference is
already computed -- every reconciled product carries a DIFFERENCE plane -- it
simply had no picture. This makes three per region:

* ``difference.png`` -- the difference on its own, diverging about zero. Red is
  Rubin brighter, blue is the reference brighter.
* ``difference-overlay.png`` -- the same map with everything below the display
  threshold fully transparent, so it can be laid straight over the star image.
  A reader then sees the sky and the disagreement in one frame instead of
  flicking between two.
* ``difference-significance.png`` -- unsigned, for judging extent rather than
  direction.

Scaling is in units of the **empirically measured** noise of the difference at
the smoothing scale, never the propagated variance planes: those understate the
truth on these products by a median factor of about seven, and a map scaled by
them would paint ordinary noise as a discovery.

All three are written at the same 512x512 geometry as the existing
``rubin-r.png`` and ``reference-r.png``, so the browser can stack them without
knowing any WCS.

**What a bright pixel here is not.** The filter colour term between these pairs
is measured, small and linear -- -0.080 mag per mag of Rubin g-r against DECam,
+0.007 against PS1, to under 4 millimagnitudes -- so colour alone moves a source
very little. What is not explained is the empirical field-to-field scatter, forty
times larger than the filters permit, which is photometric error, crowding, PSF
residuals or calibration structure. These maps show where the two images
disagree. They do not show where the sky is unusual.
"""

from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

warnings.filterwarnings("ignore")

import matplotlib

matplotlib.use("Agg")

from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy.ndimage import gaussian_filter, maximum_filter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTS = ROOT / "pipeline/results/reconciled-regions-200"
DEFAULT_PREVIEWS = ROOT / "public/layer-previews/selected-regions-200/comparisons"
DEFAULT_OUTPUT = ROOT / "public/data/layers/selected-regions/difference-maps.json"

# Smoothing scale for the displayed map. Single pixels are noise; this is the
# scale at which a real extended disagreement would show.
SMOOTH_ARCSEC = 1.2
DISPLAY_SIGMA = 5.0
PEAK_SIGMA = 4.0
MAX_PEAKS = 12
PEAK_SEPARATION_ARCSEC = 6.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def robust_sigma(values: np.ndarray) -> float:
    if values.size < 16:
        return float("nan")
    lo, hi = np.percentile(values, [15.865, 84.135])
    return float(hi - lo) / 2.0


def significance_map(difference: np.ndarray, valid: np.ndarray, pixel_scale: float) -> tuple[np.ndarray, float]:
    """Smoothed difference in units of its own measured scatter."""
    filled = np.where(valid, difference, 0.0)
    weight = valid.astype(np.float64)
    sigma_pixels = max(0.6, SMOOTH_ARCSEC / max(pixel_scale, 1e-6) / 2.3548)
    # Normalised convolution, so masked pixels do not drag the edges toward zero.
    smoothed = gaussian_filter(filled, sigma_pixels)
    norm = gaussian_filter(weight, sigma_pixels)
    with np.errstate(invalid="ignore", divide="ignore"):
        smoothed = np.where(norm > 0.15, smoothed / np.maximum(norm, 1e-9), np.nan)
    inside = np.isfinite(smoothed) & valid
    scatter = robust_sigma(smoothed[inside])
    if not np.isfinite(scatter) or scatter <= 0:
        return np.zeros_like(difference), float("nan")
    return np.where(inside, smoothed / scatter, np.nan), scatter


def diverging_rgba(sigma: np.ndarray, limit: float, transparent_below: float | None) -> np.ndarray:
    """Blue-white-red RGBA. Alpha ramps in above the threshold when given."""
    scaled = np.clip(np.nan_to_num(sigma, nan=0.0) / limit, -1.0, 1.0)
    magnitude = np.abs(scaled)
    red = np.where(scaled > 0, 1.0, 1.0 - magnitude * 0.85)
    green = 1.0 - magnitude * 0.85
    blue = np.where(scaled < 0, 1.0, 1.0 - magnitude * 0.85)
    if transparent_below is None:
        alpha = np.ones_like(magnitude)
    else:
        floor = transparent_below / limit
        alpha = np.clip((magnitude - floor) / max(1e-6, 1.0 - floor), 0.0, 1.0)
        alpha = alpha ** 0.65
    alpha = np.where(np.isfinite(sigma), alpha, 0.0)
    return np.dstack([red, green, blue, alpha])


def write_png(rgba: np.ndarray, path: Path) -> None:
    from matplotlib import image as mpimg

    path.parent.mkdir(parents=True, exist_ok=True)
    # Flip so the image is written in the same orientation as the existing
    # previews, which are drawn with origin at lower-left.
    mpimg.imsave(path, np.flipud(np.clip(rgba, 0.0, 1.0)))


def find_peaks(sigma: np.ndarray, pixel_scale: float) -> list[tuple[int, int, float]]:
    magnitude = np.abs(np.nan_to_num(sigma, nan=0.0))
    separation = max(3, int(round(PEAK_SEPARATION_ARCSEC / max(pixel_scale, 1e-6))))
    local_max = magnitude == maximum_filter(magnitude, size=separation)
    ys, xs = np.where(local_max & (magnitude >= PEAK_SIGMA))
    peaks = sorted(((int(x), int(y), float(sigma[y, x])) for x, y in zip(xs, ys)),
                   key=lambda item: -abs(item[2]))
    return peaks[:MAX_PEAKS]


def preview_bands(preview_dir: Path) -> tuple[str | None, str | None]:
    """The band each existing preview was rendered in.

    The filenames carry the band, and it is not always r: six regions compare
    Rubin r against a reference in g, i or z. Hardcoding "-r" produced paths that
    do not exist, and a missing image is a blank frame with no error anywhere.
    """
    def find(prefix: str) -> str | None:
        matches = sorted(preview_dir.glob(f"{prefix}-*.png"))
        for match in matches:
            band = match.stem.split("-", 1)[1]
            if len(band) == 1:
                return band
        return None

    return find("rubin"), find("reference")


def render_region(path: Path, preview_dir: Path) -> dict[str, Any] | None:
    with fits.open(path, memmap=False) as hdus:
        names = {hdu.name for hdu in hdus}
        # Prefer the fitted-kernel difference where fit_matching_kernel.py left
        # one. The Gaussian match never cancels a real PSF core, so its map is
        # dominated by subtraction residuals; the fitted kernel cuts the residual
        # at star positions by a median factor of about four.
        plane = "KERNEL_DIFFERENCE" if "KERNEL_DIFFERENCE" in names else "DIFFERENCE"
        difference = np.asarray(hdus[plane].data, dtype=np.float64)
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data).astype(bool)
        wcs = WCS(hdus["RUBIN"].header).celestial
    valid = common & np.isfinite(difference)
    if valid.sum() < 0.05 * difference.size:
        return None
    pixel_scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
    sigma, scatter = significance_map(difference, valid, pixel_scale)
    if not np.isfinite(scatter):
        return None

    write_png(diverging_rgba(sigma, DISPLAY_SIGMA, None), preview_dir / "difference.png")
    write_png(diverging_rgba(sigma, DISPLAY_SIGMA, DISPLAY_SIGMA * 0.4),
              preview_dir / "difference-overlay.png")
    magnitude = np.abs(np.nan_to_num(sigma, nan=0.0))
    grey = np.clip(magnitude / DISPLAY_SIGMA, 0, 1)
    write_png(np.dstack([grey, grey, grey, np.ones_like(grey)]),
              preview_dir / "difference-significance.png")

    # Almost every large peak sits on a bright source: PSF matching is never
    # exact in the core, so a star leaves a residual far above anything in blank
    # sky. Those are real disagreements but uninteresting ones, and a list of
    # them is just a list of the brightest stars. Classifying each peak by
    # whether it sits on source flux is what makes the map browsable.
    source_floor = np.nanpercentile(rubin[common], 98) if common.any() else np.inf
    height, width = sigma.shape
    peaks = [
        {
            # Fractional coordinates with the origin at top-left, which is how a
            # browser positions a marker over an <img>.
            "x": round(x / (width - 1), 5),
            "y": round(1.0 - y / (height - 1), 5),
            "sigma": round(value, 2),
            "direction": "rubin-brighter" if value > 0 else "reference-brighter",
            "onSource": bool(rubin[y, x] >= source_floor),
            "rubinBrightnessPercentile": round(
                float((rubin[common] < rubin[y, x]).mean() * 100.0), 2
            ) if common.any() else None,
            "sky": {
                "raDeg": round(float(wcs.pixel_to_world_values(x, y)[0]), 6),
                "decDeg": round(float(wcs.pixel_to_world_values(x, y)[1]), 6),
            },
        }
        for x, y, value in find_peaks(sigma, pixel_scale)
    ]
    finite = magnitude[np.isfinite(sigma)]
    off_source = [p for p in peaks if not p["onSource"]]
    rubin_band, reference_band = preview_bands(preview_dir)
    return {
        "differencePlane": plane,
        "kernelMatched": plane == "KERNEL_DIFFERENCE",
        "rubinBand": rubin_band,
        "referenceBand": reference_band,
        "sameNamedBand": bool(rubin_band and reference_band and rubin_band == reference_band),
        "offSourcePeakCount": len(off_source),
        "strongestOffSourceSigma": max((abs(p["sigma"]) for p in off_source), default=None),
        "pixelScaleArcsec": round(pixel_scale, 4),
        "differenceScatterNjy": float(scatter),
        "maxAbsSigma": round(float(finite.max()), 2) if finite.size else None,
        "p99AbsSigma": round(float(np.percentile(finite, 99)), 2) if finite.size else None,
        "fractionAbove3Sigma": round(float((finite >= 3).mean()), 5) if finite.size else None,
        "fractionAbove5Sigma": round(float((finite >= 5).mean()), 5) if finite.size else None,
        "peaks": peaks,
        "peakCount": len(peaks),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--previews", type=Path, default=DEFAULT_PREVIEWS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pairing", default="legacy",
                        help="Which reference this run compares Rubin against.")
    # No URL argument. A leading-slash value passed on a Git Bash command line is
    # rewritten to a Windows path -- "/layer-previews/..." arrived as
    # "C:/Program Files/Git/layer-previews/..." and was written into the manifest.
    # Deriving the URL from the previews directory removes the failure mode.
    parser.add_argument("--peaks-dir-name", default="difference-peaks")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    try:
        preview_url_root = "/" + args.previews.resolve().relative_to(ROOT / "public").as_posix()
    except ValueError as error:
        raise SystemExit(f"--previews must live under public/: {args.previews}") from error

    paths = sorted(p for region in sorted(args.products.iterdir()) if region.is_dir()
                   for p in region.glob("*.fits"))
    if args.limit:
        paths = paths[: args.limit]

    regions: list[dict[str, Any]] = []
    for path in paths:
        region_id = path.parent.name
        try:
            result = render_region(path, args.previews / region_id)
        except Exception as error:
            print(f"[failed] {region_id}: {type(error).__name__}: {error}", flush=True)
            continue
        if result is None:
            continue
        result["regionId"] = region_id
        result["tract"] = int(region_id.rsplit("-", 1)[-1])
        base = f"{preview_url_root}/{region_id}"
        result["previews"] = {
            "rubin": f"{base}/rubin-{result['rubinBand']}.png",
            "reference": f"{base}/reference-{result['referenceBand']}.png",
            "difference": f"{base}/difference.png",
            "differenceOverlay": f"{base}/difference-overlay.png",
        }
        regions.append(result)
        print(f"{region_id}  max {result['maxAbsSigma']} sigma  peaks {result['peakCount']}", flush=True)

    # Rank by how much of the frame disagrees rather than by a single hot pixel:
    # one bright peak is usually a source subtraction residual, while a large
    # area above threshold is the thing worth looking at.
    # Cross-band pairs disagree everywhere because the filters differ, and they
    # took ranks 1 and 2 on area alone -- an r-versus-z pair leading a list of
    # "most different" regions is a ranking that teaches the wrong thing. They
    # are demoted rather than dropped, and the page labels them.
    regions.sort(
        key=lambda item: (
            0 if item["sameNamedBand"] else 1,
            -(item["fractionAbove5Sigma"] or 0),
            -(item["maxAbsSigma"] or 0),
        )
    )
    for rank, region in enumerate(regions, start=1):
        region["rank"] = rank

    payload = {
        "schemaVersion": "layers-difference-maps-v1",
        "generatedAt": utc_now(),
        "pairing": args.pairing,
        "purpose": (
            "Where Rubin and the reference disagree, rendered so it can be viewed over the star "
            "image rather than inferred by comparing two frames by eye."
        ),
        "scaling": {
            "unit": "empirically measured scatter of the smoothed difference, per region",
            "smoothingArcsec": SMOOTH_ARCSEC,
            "displayLimitSigma": DISPLAY_SIGMA,
            "overlayTransparentBelowSigma": DISPLAY_SIGMA * 0.4,
            "perPixelVarianceUsed": False,
            "why": (
                "The propagated variance planes understate the true uncertainty on these products "
                "by a median factor of about seven, so a map scaled by them would paint ordinary "
                "noise as a discovery."
            ),
        },
        "colourKey": {"red": "Rubin brighter", "blue": "reference brighter"},
        "peakClassification": {
            "onSource": (
                "The peak sits on Rubin flux above the 98th percentile of the field. PSF matching "
                "is never exact in a bright core, so these are real disagreements that say more "
                "about the match than about the sky."
            ),
            "offSource": (
                "The peak sits away from bright flux. These are the ones worth looking at, and "
                "they are still not detections: the bandpass caveat below applies to every pixel."
            ),
        },
        "counts": {
            "regionsRendered": len(regions),
            "regionsWithAnyPeak": sum(1 for r in regions if r["peakCount"]),
            "totalPeaks": sum(r["peakCount"] for r in regions),
            "offSourcePeaks": sum(r["offSourcePeakCount"] for r in regions),
            "crossBandRegions": sum(1 for r in regions if not r["sameNamedBand"]),
            "kernelMatchedRegions": sum(1 for r in regions if r["kernelMatched"]),
            "sameBandRegions": sum(1 for r in regions if r["sameNamedBand"]),
            "regionsWithAnOffSourcePeak": sum(1 for r in regions if r["offSourcePeakCount"]),
        },
        "crossBandNote": (
            "A few regions compare Rubin against a reference in a different filter -- r against z "
            "in one case. Those frames disagree everywhere for a trivial reason, and the explorer "
            "labels them so a large difference is not mistaken for an interesting one."
        ),
        "caveat": (
            "The colour term between these filters is now measured directly from CALSPEC spectra and official transmission curves: -0.080 mag per mag of Rubin g-r against DECam, +0.007 against PS1, linear to under 4 millimagnitudes. So a source needs an extreme colour to shift much, and the empirical fit's field-to-field scatter -- forty times larger than the filters permit -- is photometric error, crowding, PSF residuals or calibration structure, not the bandpass. A difference here is still not a detection, but the reason is no longer the filters. These maps show where two images disagree, not where the sky is unusual."
        ),
        "caveatSupersedes": (
            "This previously read that bandpass transfer was unvalidated and a colour difference "
            "produced the signal. Synthetic photometry has since measured the filter term, and it "
            "is small and linear; see section 18 of docs/DIFFERENCE_ENGINE_STATUS.md."
        ),
        "regions": regions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # The full file is 0.9 MB. A page must never import that: a 525 KB module
    # already broke every tract route once by pushing its worker chunk past what
    # the runtime would load. So the page imports a slim index, and a region's
    # peaks are fetched from a static file only when that region is opened.
    peaks_dir = args.output.parent.parent / args.peaks_dir_name
    peaks_dir.mkdir(parents=True, exist_ok=True)
    for region in regions:
        (peaks_dir / f"{region['regionId']}.json").write_text(
            json.dumps(
                {
                    "regionId": region["regionId"],
                    "tract": region["tract"],
                    "pixelScaleArcsec": region["pixelScaleArcsec"],
                    "colourKey": payload["colourKey"],
                    "peaks": region["peaks"],
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

    index = {
        "schemaVersion": "layers-difference-index-v1",
        "generatedAt": payload["generatedAt"],
        "scaling": payload["scaling"],
        "colourKey": payload["colourKey"],
        "peakClassification": payload["peakClassification"],
        "counts": payload["counts"],
        "caveat": payload["caveat"],
        "pairing": args.pairing,
        "previewRoot": preview_url_root,
        "peakRoot": f"/data/layers/{args.peaks_dir_name}",
        "regions": [
            {
                "regionId": item["regionId"],
                "tract": item["tract"],
                "rank": item["rank"],
                "maxAbsSigma": item["maxAbsSigma"],
                "p99AbsSigma": item["p99AbsSigma"],
                "fractionAbove5Sigma": item["fractionAbove5Sigma"],
                "peakCount": item["peakCount"],
                "offSourcePeakCount": item["offSourcePeakCount"],
                "strongestOffSourceSigma": item["strongestOffSourceSigma"],
                "rubinBand": item["rubinBand"],
                "referenceBand": item["referenceBand"],
                "sameNamedBand": item["sameNamedBand"],
                "kernelMatched": item["kernelMatched"],
            }
            for item in regions
        ],
    }
    suffix = "" if args.pairing == "legacy" else f"-{args.pairing}"
    index_path = args.output.parent / f"difference-index{suffix}.json"
    index_path.write_text(json.dumps(index, separators=(",", ":")) + "\n", encoding="utf-8")

    print(f"\n{json.dumps(payload['counts'], indent=2)}")
    print(f"wrote {display_path(args.output)}")
    print(f"index {index_path.stat().st_size / 1024:.0f} KB, {len(regions)} per-region peak files")


if __name__ == "__main__":
    main()
