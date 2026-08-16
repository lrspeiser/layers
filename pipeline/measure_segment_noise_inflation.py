"""Measure the noise inflation on the catalogue's own segment shapes, not on circles.

`measure_resampling_covariance.py` established that pixels are correlated and
that a sum over N of them has a variance well above N*sigma^2 -- between 3.7x and
7.1x on circular apertures of radius 1.5 to 6 pixels. That is enough to say the
released error columns are about twice too small. It is not enough to *correct*
them.

The gap is shape. The catalogue measures flux in segments: connected regions of
whatever outline the detection threshold produced, with areas running from a
handful of pixels to several hundred. A circle of equal area is not the same
aperture -- a ragged or elongated footprint samples the noise autocorrelation
differently from a compact disc, and the largest segments run past the largest
circle measured. Correcting released values from the circular curve would mean
interpolating across shape and extrapolating past 113 pixels, on exactly the
large sources that section 21 already shows are separately biased.

So this measures the thing directly. For each region it reproduces the
catalogue's own detection -- same summed frame, same threshold -- takes the real
segment footprints, and translates each one to many random blank-sky positions.
The scatter of those sums, against sigma*sqrt(N) for the same footprint, is the
inflation factor for that segment's actual shape and size. No interpolation, no
assumed geometry.

The output is inflation as a function of segment area, which is what a correction
needs. It changes no published value: applying it is a separate decision.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
from astropy.io import fits
from photutils.background import Background2D, MedianBackground
from photutils.segmentation import detect_sources, deblend_sources

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTS = ROOT / "pipeline/results/reconciled-regions-200"
DEFAULT_OUTPUT = ROOT / "public/data/layers/selected-regions/segment-noise-inflation.json"

# Detection settings copied from build_source_catalogue.py. If these drift apart
# the measurement stops describing the catalogue's segments, so they are stated
# here rather than imported, and the test asserts they still agree.
BOX = 64
FILTER = 3
DETECT_SIGMA = 3.0
NPIXELS = 5

# Mask sources this hard before calling the rest sky, matching the conservative
# setting the circular measurement settled on.
MASK_SIGMA = 1.5
GROW = 8

PLACEMENTS = 120
MAX_SEGMENTS = 60
AREA_BINS = (0, 10, 20, 40, 80, 160, 320, 10**9)


def background_of(image: np.ndarray, mask: np.ndarray) -> Background2D:
    return Background2D(
        image, BOX, filter_size=FILTER, bkg_estimator=MedianBackground(), mask=mask
    )


def segments_and_sky(rubin: np.ndarray, reference: np.ndarray):
    """The catalogue's segmentation, plus a blank-sky mask and the Rubin RMS."""
    finite = np.isfinite(rubin) & np.isfinite(reference)
    if finite.sum() < 0.2 * rubin.size:
        raise ValueError("too few finite pixels")
    rubin_bkg = background_of(rubin, ~finite)
    reference_bkg = background_of(reference, ~finite)

    # Detection on the sum of both frames, exactly as the catalogue does.
    detection = (rubin - rubin_bkg.background) + (reference - reference_bkg.background)
    threshold = DETECT_SIGMA * np.hypot(rubin_bkg.background_rms, reference_bkg.background_rms)
    found = detect_sources(detection, threshold, npixels=NPIXELS, mask=~finite)
    if found is None:
        raise ValueError("no sources")
    try:
        found = deblend_sources(detection, found, npixels=NPIXELS)
    except Exception:
        pass

    # Blank sky: mask everything detected at a lower threshold and grow it.
    flat = rubin - rubin_bkg.background
    faint = detect_sources(flat, MASK_SIGMA * rubin_bkg.background_rms, npixels=NPIXELS)
    blank = finite.copy()
    if faint is not None:
        source = faint.data > 0
        grown = source.copy()
        for shift in range(1, GROW + 1):
            grown[shift:, :] |= source[:-shift, :]
            grown[:-shift, :] |= source[shift:, :]
            grown[:, shift:] |= source[:, :-shift]
            grown[:, :-shift] |= source[:, shift:]
        blank &= ~grown
    return found, blank, flat, float(np.median(rubin_bkg.background_rms))


def inflation_for_segment(
    offsets: np.ndarray, blank: np.ndarray, flat: np.ndarray, rms: float, rng
) -> float | None:
    """Translate one real footprint around blank sky and measure the scatter of its sums."""
    height, width = flat.shape
    dy = offsets[:, 0]
    dx = offsets[:, 1]
    span_y = int(dy.max() - dy.min()) + 1
    span_x = int(dx.max() - dx.min()) + 1
    if span_y >= height - 2 or span_x >= width - 2:
        return None
    base_y = dy - dy.min()
    base_x = dx - dx.min()
    sums = []
    for _ in range(PLACEMENTS * 5):
        if len(sums) >= PLACEMENTS:
            break
        oy = int(rng.integers(0, height - span_y))
        ox = int(rng.integers(0, width - span_x))
        ys = base_y + oy
        xs = base_x + ox
        if not blank[ys, xs].all():
            continue
        patch = flat[ys, xs]
        if not np.isfinite(patch).all():
            continue
        sums.append(float(patch.sum()))
    if len(sums) < 30:
        return None
    measured = float(np.std(sums, ddof=1))
    predicted = rms * np.sqrt(len(dy))
    if predicted <= 0:
        return None
    return float((measured / predicted) ** 2)


def measure_region(path: pathlib.Path, rng) -> list[tuple[int, float]]:
    with fits.open(path) as handle:
        names = {hdu.name for hdu in handle}
        if not {"RUBIN", "REFERENCE"} <= names:
            return []
        rubin = handle["RUBIN"].data.astype(float)
        reference = handle["REFERENCE"].data.astype(float)

    found, blank, flat, rms = segments_and_sky(rubin, reference)
    if blank.sum() < 0.05 * blank.size or not np.isfinite(rms) or rms <= 0:
        return []

    labels = found.labels
    if len(labels) > MAX_SEGMENTS:
        labels = rng.choice(labels, MAX_SEGMENTS, replace=False)

    out: list[tuple[int, float]] = []
    data = found.data
    for label in labels:
        ys, xs = np.nonzero(data == label)
        if ys.size < NPIXELS:
            continue
        offsets = np.column_stack([ys, xs])
        value = inflation_for_segment(offsets, blank, flat, rms, rng)
        if value is not None and np.isfinite(value):
            out.append((int(ys.size), value))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=pathlib.Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    regions = sorted(p for p in args.products.iterdir() if p.is_dir())
    if args.limit:
        regions = regions[: args.limit]

    rng = np.random.default_rng(20260816)
    samples: list[tuple[int, float]] = []
    measured_regions = 0
    for index, region in enumerate(regions, 1):
        products = sorted(region.glob("*.fits"))
        if not products:
            continue
        try:
            rows = measure_region(products[0], rng)
        except Exception as error:  # noqa: BLE001 - one bad region must not end the run
            print(f"  {region.name}: {type(error).__name__}: {error}")
            continue
        if rows:
            samples.extend(rows)
            measured_regions += 1
        if index % 10 == 0:
            print(f"  {index}/{len(regions)} regions, {len(samples)} segments")

    if not samples:
        raise SystemExit("no segments measured")

    areas = np.array([a for a, _ in samples], dtype=float)
    values = np.array([v for _, v in samples], dtype=float)

    by_area = []
    for lo, hi in zip(AREA_BINS[:-1], AREA_BINS[1:]):
        inside = (areas >= lo) & (areas < hi)
        if inside.sum() < 20:
            continue
        by_area.append(
            {
                "areaPixelsFrom": int(lo),
                "areaPixelsTo": None if hi > 10**8 else int(hi),
                "segments": int(inside.sum()),
                "medianAreaPixels": float(np.median(areas[inside])),
                "medianVarianceInflation": float(np.median(values[inside])),
                "medianErrorBarUnderstatedBy": float(np.sqrt(np.median(values[inside]))),
            }
        )

    payload = {
        "schemaVersion": "layers-segment-noise-inflation-v1",
        "question": (
            "The circular measurement said the error columns are about twice too small. To "
            "correct them we need the inflation on the catalogue's own segment shapes, across "
            "the areas it actually uses."
        ),
        "method": (
            "Reproduce the catalogue's detection (sum of both frames, 3 sigma on the quadrature "
            "background RMS, deblended), take each real segment footprint, and translate it to "
            "many random blank-sky positions. The scatter of those sums against sigma*sqrt(N) "
            "for the same footprint is that shape's inflation factor. No assumed geometry and "
            "no interpolation across shape."
        ),
        "regionsMeasured": measured_regions,
        "segmentsMeasured": len(samples),
        "placementsPerSegment": PLACEMENTS,
        "overall": {
            "medianVarianceInflation": float(np.median(values)),
            "medianErrorBarUnderstatedBy": float(np.sqrt(np.median(values))),
        },
        "byArea": by_area,
        "appliedToReleasedColumns": False,
        "note": (
            "Measured, not applied. This is the curve a correction needs; multiplying "
            "rubin_flux_err_njy and reference_flux_err_njy by sqrt of the inflation at each "
            "source's area, and dividing the _snr columns by it, would rewrite every row of the "
            "release and its checksums. That is a publishing decision, not a measurement."
        ),
        "reproduce": "python pipeline/measure_segment_noise_inflation.py",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"\nregions {measured_regions}, segments {len(samples)}")
    print(f"overall variance inflation x{np.median(values):.2f}"
          f"  -> error bars understated by x{np.sqrt(np.median(values)):.2f}\n")
    print(f"{'area (px)':>16s} {'n':>6s} {'variance':>10s} {'errors x':>10s}")
    for row in by_area:
        label = f"{row['areaPixelsFrom']}-{row['areaPixelsTo'] or '+'}"
        print(
            f"{label:>16s} {row['segments']:6d} {row['medianVarianceInflation']:9.2f}x"
            f" {row['medianErrorBarUnderstatedBy']:9.2f}x"
        )
    print(f"\nwrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
