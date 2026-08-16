#!/usr/bin/env python3
"""How complete is the catalogue, and how many of its outliers are noise?

The catalogue reports 880 sources of 46,574 above a 5-sigma departure from their
field's median flux ratio. On its own that number is unreadable: nobody can tell
whether it is an interesting tail or the expected behaviour of a heavy-tailed
distribution, and "how complete are you, and what is your false-positive rate"
is the third question any scientist asks of a catalogue.

Both are measured here by injection, not modelled.

**Completeness.** Synthetic sources are added to the Rubin frame across a range
of magnitudes, detection is re-run exactly as the catalogue runs it, and the
recovered fraction is reported per magnitude bin. That is the selection function
the catalogue currently lacks.

**False-positive rate.** The injected sources go into *both* frames with a flux
ratio set to the field's own median -- that is, they are constructed to have no
departure at all. Any that come out above 5 sigma are false positives by
construction, because the truth for them is known. This measures the cut's
reliability without needing a model of the noise.

Injection uses the same empirical-null discipline as the rest of the project:
positions are drawn away from existing flux, and the injected profile is matched
to the frame's own measured PSF width rather than assumed.
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

from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from photutils.background import Background2D, MedianBackground
from photutils.segmentation import SourceCatalog, deblend_sources, detect_sources

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTS = ROOT / "pipeline/results/reconciled-regions-200"
DEFAULT_OUTPUT = ROOT / "public/data/layers/selected-regions/catalogue-reliability.json"

DETECT_SIGMA = 3.0
MIN_PIXELS = 5
BACKGROUND_BOX = 64
AB_ZERO_POINT_NJY = 3.63078054770e12

MAG_BINS = np.arange(19.0, 25.51, 0.5)
PER_BIN = 25
MATCH_RADIUS_PIXELS = 3.0
DEPARTURE_CUT = 5.0
RANDOM_SEED = 20260815


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def mag_to_njy(mag: float) -> float:
    return float(AB_ZERO_POINT_NJY * 10 ** (-0.4 * mag))


def measure_psf_sigma(image: np.ndarray, valid: np.ndarray) -> float:
    """Second-moment width of the brightest compact sources, in pixels."""
    background = Background2D(image, box_size=BACKGROUND_BOX, filter_size=3,
                              mask=~valid, bkg_estimator=MedianBackground())
    detection = image - background.background
    segments = detect_sources(detection, DETECT_SIGMA * background.background_rms,
                              npixels=MIN_PIXELS, mask=~valid)
    if segments is None:
        return 2.0
    catalogue = SourceCatalog(detection, segments, mask=~valid)
    widths = np.asarray(catalogue.semiminor_sigma.value, dtype=np.float64)
    fluxes = np.asarray(catalogue.segment_flux, dtype=np.float64)
    compact = np.isfinite(widths) & (widths > 0.5) & (widths < 8) & (fluxes > 0)
    if compact.sum() < 5:
        return 2.0
    order = np.argsort(-fluxes[compact])[:30]
    return float(np.median(widths[compact][order]))


def source_flux_ratio(rubin: np.ndarray, reference: np.ndarray, valid: np.ndarray) -> float | None:
    """Median Rubin/reference segment-flux ratio over detected sources."""
    background = Background2D(rubin, box_size=BACKGROUND_BOX, filter_size=3,
                              mask=~valid, bkg_estimator=MedianBackground())
    detection = rubin - background.background
    segments = detect_sources(detection, DETECT_SIGMA * background.background_rms,
                              npixels=MIN_PIXELS, mask=~valid)
    if segments is None:
        return None
    rubin_flux = np.asarray(SourceCatalog(detection, segments, mask=~valid).segment_flux, dtype=np.float64)
    reference_flux = np.asarray(
        SourceCatalog(reference, segments, mask=~valid).segment_flux, dtype=np.float64
    )
    usable = (rubin_flux > 0) & (reference_flux > 0)
    if usable.sum() < 20:
        return None
    ratio = float(np.median(rubin_flux[usable] / reference_flux[usable]))
    return ratio if np.isfinite(ratio) and ratio > 0 else None


def blank_positions(image: np.ndarray, valid: np.ndarray, count: int, margin: int,
                    rng: np.random.Generator) -> list[tuple[int, int]]:
    """Positions away from existing flux, so an injection is not stacked on a source."""
    height, width = image.shape
    threshold = np.nanpercentile(image[valid], 90) if valid.any() else np.inf
    chosen: list[tuple[int, int]] = []
    for _ in range(count * 60):
        if len(chosen) >= count:
            break
        x = int(rng.integers(margin, width - margin))
        y = int(rng.integers(margin, height - margin))
        window = image[y - margin : y + margin + 1, x - margin : x + margin + 1]
        if not valid[y - margin : y + margin + 1, x - margin : x + margin + 1].all():
            continue
        if np.nanmax(window) > threshold:
            continue
        if any(abs(x - px) < 2 * margin and abs(y - py) < 2 * margin for px, py in chosen):
            continue
        chosen.append((x, y))
    return chosen


def inject(image: np.ndarray, positions: list[tuple[int, int]], flux: float, sigma: float) -> None:
    """Add a Gaussian of the given total flux at each position, in place."""
    half = int(np.ceil(4 * sigma))
    y, x = np.mgrid[-half : half + 1, -half : half + 1]
    profile = np.exp(-(x**2 + y**2) / (2 * sigma**2))
    profile /= profile.sum()
    for px, py in positions:
        image[py - half : py + half + 1, px - half : px + half + 1] += profile * flux


def run_region(path: Path, rng: np.random.Generator) -> dict[str, Any] | None:
    with fits.open(path, memmap=False) as hdus:
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        reference = np.asarray(hdus["REFERENCE"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data).astype(bool)
        header = hdus["RUBIN"].header
    valid = common & np.isfinite(rubin) & np.isfinite(reference)
    if valid.sum() < 0.3 * rubin.size:
        return None
    wcs = WCS(header).celestial
    pixel_scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
    sigma = measure_psf_sigma(rubin, valid)

    # The field's own flux ratio, measured the way the catalogue measures it:
    # from detected sources, not from pixels. The per-pixel median is dominated
    # by sky, where the ratio is noise, and injecting at that value gave every
    # source a systematic offset from the population -- a false-positive rate of
    # exactly 1.0, which is what a wrong null looks like.
    field_ratio = source_flux_ratio(rubin, reference, valid)
    if field_ratio is None:
        return None

    margin = max(8, int(np.ceil(4 * sigma)) + 2)
    bins: list[dict[str, Any]] = []
    false_positives = 0
    recovered_total = 0

    for mag in MAG_BINS:
        flux = mag_to_njy(float(mag))
        positions = blank_positions(rubin, valid, PER_BIN, margin, rng)
        if len(positions) < PER_BIN // 2:
            continue
        test_rubin = rubin.copy()
        test_reference = reference.copy()
        inject(test_rubin, positions, flux, sigma)
        # Same source in the reference, scaled by the field ratio: identical
        # physics, no departure.
        inject(test_reference, positions, flux / field_ratio, sigma)

        background = Background2D(test_rubin, box_size=BACKGROUND_BOX, filter_size=3,
                                  mask=~valid, bkg_estimator=MedianBackground())
        detection = test_rubin - background.background
        segments = detect_sources(detection, DETECT_SIGMA * background.background_rms,
                                  npixels=MIN_PIXELS, mask=~valid)
        if segments is None:
            continue
        segments = deblend_sources(detection, segments, npixels=MIN_PIXELS,
                                   nlevels=32, contrast=0.001, progress_bar=False)
        rubin_cat = SourceCatalog(detection, segments, background=background.background,
                                  error=background.background_rms, mask=~valid)
        reference_cat = SourceCatalog(test_reference, segments,
                                      error=background.background_rms, mask=~valid)
        xs = np.asarray(rubin_cat.xcentroid, dtype=np.float64)
        ys = np.asarray(rubin_cat.ycentroid, dtype=np.float64)
        rubin_flux = np.asarray(rubin_cat.segment_flux, dtype=np.float64)
        reference_flux = np.asarray(reference_cat.segment_flux, dtype=np.float64)

        with np.errstate(invalid="ignore", divide="ignore"):
            log_ratio = np.where(
                (rubin_flux > 0) & (reference_flux > 0),
                np.log10(rubin_flux / np.maximum(reference_flux, 1e-30)), np.nan,
            )
        good = np.isfinite(log_ratio)
        if good.sum() < 20:
            continue
        centre = float(np.median(log_ratio[good]))
        lo, hi = np.percentile(log_ratio[good], [15.865, 84.135])
        spread = float(hi - lo) / 2.0
        if spread <= 0:
            continue

        recovered = 0
        flagged = 0
        for px, py in positions:
            distance = np.hypot(xs - px, ys - py)
            if distance.size == 0:
                continue
            index = int(np.argmin(distance))
            if distance[index] > MATCH_RADIUS_PIXELS:
                continue
            recovered += 1
            if np.isfinite(log_ratio[index]):
                if abs((log_ratio[index] - centre) / spread) >= DEPARTURE_CUT:
                    flagged += 1
        bins.append({
            "magAB": round(float(mag), 2),
            "injected": len(positions),
            "recovered": recovered,
            "completeness": round(recovered / len(positions), 4),
            "recoveredFlaggedAboveCut": flagged,
        })
        recovered_total += recovered
        false_positives += flagged

    if not bins:
        return None
    complete = [b for b in bins if b["completeness"] >= 0.9]
    return {
        "regionId": path.parent.name,
        "pixelScaleArcsec": round(pixel_scale, 4),
        "psfSigmaPixels": round(sigma, 3),
        "fieldFluxRatio": round(field_ratio, 4),
        "faintest90PercentCompleteMagAB": max((b["magAB"] for b in complete), default=None),
        "injectedTotal": sum(b["injected"] for b in bins),
        "recoveredTotal": recovered_total,
        "falsePositives": false_positives,
        "falsePositiveRate": round(false_positives / recovered_total, 5) if recovered_total else None,
        "bins": bins,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--regions", type=int, default=24,
                        help="How many regions to inject into. Every region would take hours "
                             "and the rate converges long before that.")
    args = parser.parse_args()

    rng = np.random.default_rng(RANDOM_SEED)
    paths = sorted(p for region in sorted(args.products.iterdir()) if region.is_dir()
                   for p in region.glob("*.fits"))
    # Evenly spaced through the set rather than the first N, so the sample is not
    # all one part of the sky.
    if len(paths) > args.regions:
        step = len(paths) / args.regions
        paths = [paths[int(i * step)] for i in range(args.regions)]

    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            result = run_region(path, rng)
        except Exception as error:
            print(f"[failed] {path.parent.name}: {type(error).__name__}: {error}", flush=True)
            continue
        if result is None:
            continue
        records.append(result)
        print(
            f"{result['regionId']:18s} 90% complete to {result['faintest90PercentCompleteMagAB']} mag  "
            f"false positives {result['falsePositives']}/{result['recoveredTotal']}",
            flush=True,
        )

    if not records:
        raise SystemExit("no region produced a reliability measurement")

    injected = sum(r["injectedTotal"] for r in records)
    recovered = sum(r["recoveredTotal"] for r in records)
    false_positives = sum(r["falsePositives"] for r in records)
    limits = [r["faintest90PercentCompleteMagAB"] for r in records
              if r["faintest90PercentCompleteMagAB"] is not None]

    # Completeness per magnitude, pooled across regions.
    pooled: dict[float, dict[str, int]] = {}
    for record in records:
        for entry in record["bins"]:
            slot = pooled.setdefault(entry["magAB"], {"injected": 0, "recovered": 0, "flagged": 0})
            slot["injected"] += entry["injected"]
            slot["recovered"] += entry["recovered"]
            slot["flagged"] += entry["recoveredFlaggedAboveCut"]

    # Apply the measured rate to the catalogue, since a rate nobody multiplies
    # by anything is just another number on a page.
    catalogue = ROOT / "public/data/layers/selected-regions/source-catalogue.json"
    application: dict[str, Any] | None = None
    if catalogue.is_file():
        counts = json.loads(catalogue.read_text(encoding="utf-8")).get("counts", {})
        clean = counts.get("clean")
        flagged = counts.get("cleanAbove5SigmaFromFieldRatio")
        rate = false_positives / recovered if recovered else None
        if clean and flagged is not None and rate is not None:
            expected = clean * rate
            application = {
                "cleanSources": clean,
                "flaggedAbove5Sigma": flagged,
                "falsePositiveRate": round(rate, 5),
                "expectedFalsePositives": round(expected, 1),
                "excessOverNoise": round(flagged - expected, 1),
                "reading": (
                    f"About {expected:.0f} of the {flagged} flagged sources are consistent with "
                    f"noise at this rate, leaving roughly {flagged - expected:.0f} in excess. "
                    "Excess is not the same as real: this test injects sources with identical "
                    "colours in both frames, so it cannot produce a bandpass-driven departure, "
                    "and the bandpass transfer between these surveys is unvalidated. The excess "
                    "is an upper bound on what could be astrophysical, not a count of it."
                ),
            }

    payload = {
        "schemaVersion": "layers-catalogue-reliability-v1",
        "generatedAt": utc_now(),
        "question": (
            "How complete is the source catalogue, and how many of its 5-sigma departures are "
            "false positives?"
        ),
        "method": {
            "completeness": (
                "Synthetic Gaussians at the frame's own measured PSF width are injected at blank "
                "positions across a magnitude ladder, detection is re-run exactly as the catalogue "
                "runs it, and the recovered fraction is reported per bin."
            ),
            "falsePositives": (
                "The same sources are injected into both frames with the field's own median flux "
                "ratio, so they carry no departure by construction. Any recovered injection "
                "measuring above the 5-sigma cut is a false positive with known truth, which "
                "measures the cut's reliability without needing a noise model."
            ),
            "departureCut": DEPARTURE_CUT,
            "sampledRegions": len(records),
            "whySampled": (
                "Injection is expensive and the rate converges long before 190 regions; the sample "
                "is spread evenly through the set rather than taken from the front."
            ),
        },
        "counts": {
            "regions": len(records),
            "injected": injected,
            "recovered": recovered,
            "overallCompleteness": round(recovered / injected, 4) if injected else None,
            "falsePositives": false_positives,
            "falsePositiveRate": round(false_positives / recovered, 5) if recovered else None,
            "median90PercentCompleteMagAB": float(np.median(limits)) if limits else None,
            "regionsAttempted": len(paths),
            "regionsYieldingAMeasurement": len(records),
            "whySomeRegionsDropOut": (
                "A region needs at least 20 detected sources with positive flux in both frames to "
                "define its own flux ratio, and at least 30% valid area. Sparse or heavily masked "
                "regions do not qualify and are excluded rather than measured against a ratio "
                "derived from too little."
            ),
        },
        "completenessByMagnitude": [
            {
                "magAB": mag,
                "injected": slot["injected"],
                "recovered": slot["recovered"],
                "completeness": round(slot["recovered"] / slot["injected"], 4) if slot["injected"] else None,
                "falsePositiveRate": round(slot["flagged"] / slot["recovered"], 5) if slot["recovered"] else None,
            }
            for mag, slot in sorted(pooled.items())
        ],
        "applicationToCatalogue": application,
        "howToReadIt": (
            "Multiply the catalogue's flagged count by the false-positive rate to estimate how "
            "many of its outliers are noise. The remainder is not thereby real: the bandpass "
            "transfer between these surveys is unvalidated, so a genuine colour difference "
            "produces a departure that this test, which injects identical colours, cannot create."
        ),
        "regions": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("\n" + json.dumps(payload["counts"], indent=2))
    print(f"wrote {display_path(args.output)}")


if __name__ == "__main__":
    main()
