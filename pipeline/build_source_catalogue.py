#!/usr/bin/env python3
"""Measure a source catalogue, so the comparison produces rows and not just maps.

Everything this project has produced so far is an image or a summary statistic.
Neither is analysable by anyone else: a scientist cannot cross-match a PNG, and
cannot ask "is this source variable" of a median. A catalogue is the object the
rest of astronomy actually consumes -- one row per source, with a position, a
flux, an uncertainty, and flags saying what to distrust.

This measures both frames of each reconciled pair on the same segmentation, so
every row carries Rubin and the reference for the *same* pixels. Measuring each
frame independently and matching afterwards would fold a matching error into
every flux difference, which is the error this project has spent its time
avoiding elsewhere.

Uses photutils, the standard implementation: Background2D for the sky,
detect_sources plus deblend_sources for segmentation, SourceCatalog for the
measurements. Writing my own would be a worse version of a well-tested library.

Uncertainties are the empirical background RMS from Background2D propagated over
each source's aperture, not the propagated variance planes: those understate the
truth on these products by a median factor of about seven, measured, and a
catalogue that quoted them would be quoting an error bar known to be wrong.

Output is Parquet and VOTable rather than JSON. Parquet because the catalogue is
columnar and large; VOTable because that is what TOPCAT, pyvo and astroquery
open without being asked twice.
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
from astropy.table import Table
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from photutils.background import Background2D, MedianBackground
from photutils.segmentation import SourceCatalog, deblend_sources, detect_sources

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTS = ROOT / "pipeline/results/reconciled-regions-200"
DEFAULT_OUTPUT = ROOT / "pipeline/results/source-catalogue"
DEFAULT_SUMMARY = ROOT / "public/data/layers/selected-regions/source-catalogue.json"

DETECT_SIGMA = 3.0
MIN_PIXELS = 5
DEBLEND_LEVELS = 32
DEBLEND_CONTRAST = 0.001
BACKGROUND_BOX = 64
AB_ZERO_POINT_NJY = 3.63078054770e12


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def to_ab(flux_njy: np.ndarray) -> np.ndarray:
    """nJy to AB magnitude, with non-positive fluxes left as NaN rather than clipped."""
    out = np.full(flux_njy.shape, np.nan)
    positive = flux_njy > 0
    out[positive] = -2.5 * np.log10(flux_njy[positive] / AB_ZERO_POINT_NJY)
    return out


def measure_region(path: Path) -> tuple[Table, dict[str, Any]] | None:
    with fits.open(path, memmap=False) as hdus:
        names = {hdu.name for hdu in hdus}
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        reference = np.asarray(hdus["REFERENCE"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data).astype(bool)
        header = hdus["RUBIN"].header
        difference = (
            np.asarray(hdus["KERNEL_DIFFERENCE"].data, dtype=np.float64)
            if "KERNEL_DIFFERENCE" in names
            else np.asarray(hdus["DIFFERENCE"].data, dtype=np.float64)
        )
        difference_plane = "KERNEL_DIFFERENCE" if "KERNEL_DIFFERENCE" in names else "DIFFERENCE"

    wcs = WCS(header).celestial
    pixel_scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
    invalid = ~common | ~np.isfinite(rubin) | ~np.isfinite(reference)
    if invalid.all():
        return None

    background = Background2D(
        rubin,
        box_size=BACKGROUND_BOX,
        filter_size=3,
        mask=invalid,
        bkg_estimator=MedianBackground(),
    )
    detection_image = rubin - background.background
    threshold = DETECT_SIGMA * background.background_rms

    segments = detect_sources(detection_image, threshold, npixels=MIN_PIXELS, mask=invalid)
    if segments is None:
        return None
    # Deblending matters here: a catalogue that merges a pair of neighbours
    # reports one wrong flux instead of two right ones, and crowded fields are
    # exactly where this project has already measured a scale dependence.
    segments = deblend_sources(
        detection_image, segments, npixels=MIN_PIXELS,
        nlevels=DEBLEND_LEVELS, contrast=DEBLEND_CONTRAST, progress_bar=False,
    )

    rubin_catalogue = SourceCatalog(
        detection_image, segments, wcs=wcs, background=background.background,
        error=background.background_rms, mask=invalid,
    )
    # The same segmentation on the reference frame, so a row's two fluxes come
    # from identical pixels and no cross-match error enters the difference.
    reference_catalogue = SourceCatalog(
        reference, segments, wcs=wcs, error=background.background_rms, mask=invalid,
    )
    difference_catalogue = SourceCatalog(
        difference, segments, wcs=wcs, error=background.background_rms, mask=invalid,
    )

    sky = rubin_catalogue.sky_centroid
    rows = Table()
    rows["region_id"] = [path.parent.name] * len(rubin_catalogue)
    rows["source_id"] = [f"{path.parent.name}-{label}" for label in rubin_catalogue.labels]
    rows["ra_deg"] = np.asarray(sky.ra.deg, dtype=np.float64)
    rows["dec_deg"] = np.asarray(sky.dec.deg, dtype=np.float64)
    rows["x_pixel"] = np.asarray(rubin_catalogue.xcentroid, dtype=np.float64)
    rows["y_pixel"] = np.asarray(rubin_catalogue.ycentroid, dtype=np.float64)
    rows["area_pixels"] = np.asarray(rubin_catalogue.area.value, dtype=np.float64)
    rows["semimajor_arcsec"] = np.asarray(rubin_catalogue.semimajor_sigma.value, dtype=np.float64) * pixel_scale
    rows["semiminor_arcsec"] = np.asarray(rubin_catalogue.semiminor_sigma.value, dtype=np.float64) * pixel_scale
    rows["ellipticity"] = np.asarray(rubin_catalogue.ellipticity, dtype=np.float64)

    rubin_flux = np.asarray(rubin_catalogue.segment_flux, dtype=np.float64)
    reference_flux = np.asarray(reference_catalogue.segment_flux, dtype=np.float64)
    difference_flux = np.asarray(difference_catalogue.segment_flux, dtype=np.float64)
    rubin_error = np.asarray(rubin_catalogue.segment_fluxerr, dtype=np.float64)
    reference_error = np.asarray(reference_catalogue.segment_fluxerr, dtype=np.float64)

    rows["rubin_flux_njy"] = rubin_flux
    rows["rubin_flux_err_njy"] = rubin_error
    rows["reference_flux_njy"] = reference_flux
    rows["reference_flux_err_njy"] = reference_error
    rows["difference_flux_njy"] = difference_flux
    rows["rubin_mag_ab"] = to_ab(rubin_flux)
    rows["reference_mag_ab"] = to_ab(reference_flux)

    combined_error = np.hypot(rubin_error, reference_error)
    with np.errstate(invalid="ignore", divide="ignore"):
        rows["difference_significance"] = np.where(
            combined_error > 0, difference_flux / combined_error, np.nan
        )
        rows["flux_ratio"] = np.where(reference_flux > 0, rubin_flux / reference_flux, np.nan)

    # The whole field is offset: Rubin measures about 7% less flux than these
    # references, so at high signal-to-noise every bright source is many sigma
    # from zero and "difference_significance" mostly measures the zeropoint.
    # The useful question is which sources differ by more than their own field
    # does, so the ratio is referred to the region's own median.
    finite_ratio = np.isfinite(rows["flux_ratio"]) & (rubin_flux > 0) & (reference_flux > 0)
    if finite_ratio.sum() >= 10:
        field_ratio = float(np.median(np.asarray(rows["flux_ratio"])[finite_ratio]))
    else:
        field_ratio = np.nan
    with np.errstate(invalid="ignore", divide="ignore"):
        expected = reference_flux * field_ratio
        departure = rubin_flux - expected
        # The propagated error is background RMS only. photutils' segment_fluxerr
        # does not include the source's own Poisson noise, so it understates the
        # uncertainty on exactly the bright sources whose departures look most
        # dramatic -- the same failure the variance planes had, measured at a
        # factor of about seven. Kept for reference, and clearly named.
        propagated_error = np.hypot(rubin_error, reference_error * abs(field_ratio))
        rows["field_flux_ratio"] = np.full(len(rows), field_ratio)
        rows["expected_rubin_flux_njy"] = expected
        rows["departure_njy"] = departure
        rows["departure_significance_propagated"] = np.where(
            propagated_error > 0, departure / propagated_error, np.nan
        )

        # The significance that is actually usable comes from the measured
        # distribution: how far a source's flux ratio sits from the field's own
        # median, in units of the field's own scatter. No error model to be wrong.
        log_ratio = np.full(len(rows), np.nan)
        usable = (rubin_flux > 0) & (reference_flux > 0)
        log_ratio[usable] = np.log10(rubin_flux[usable] / reference_flux[usable])
        rows["log_flux_ratio"] = log_ratio
        good = np.isfinite(log_ratio)
        if good.sum() >= 20:
            centre = float(np.median(log_ratio[good]))
            lo, hi = np.percentile(log_ratio[good], [15.865, 84.135])
            spread = float(hi - lo) / 2.0
        else:
            centre, spread = np.nan, np.nan
        rows["field_log_ratio_median"] = np.full(len(rows), centre)
        rows["field_log_ratio_scatter"] = np.full(len(rows), spread)
        rows["departure_significance"] = (
            (log_ratio - centre) / spread if np.isfinite(spread) and spread > 0
            else np.full(len(rows), np.nan)
        )

    # Flags, not filtering. A row the catalogue would rather you distrusted is
    # more useful than a row silently removed, because the reader can then
    # decide -- and can count what was excluded.
    height, width = rubin.shape
    edge = (
        (rows["x_pixel"] < 10) | (rows["x_pixel"] > width - 11)
        | (rows["y_pixel"] < 10) | (rows["y_pixel"] > height - 11)
    )
    rows["flag_near_edge"] = edge
    rows["flag_negative_reference"] = reference_flux <= 0
    rows["flag_blended"] = np.asarray(rubin_catalogue.area.value, dtype=np.float64) > 500

    # Signal-to-noise is a column, not a flag. The obvious flag -- S/N below 5 --
    # never fired once across 50,233 sources, because detection at 3 sigma over
    # at least 5 pixels already guarantees a higher integrated ratio. A flag that
    # cannot trigger advertises a check that is not happening, so the number is
    # published instead and the reader picks the cut.
    with np.errstate(invalid="ignore", divide="ignore"):
        rows["rubin_snr"] = np.where(rubin_error > 0, rubin_flux / rubin_error, np.nan)
        rows["reference_snr"] = np.where(reference_error > 0, reference_flux / reference_error, np.nan)

    meta = {
        "regionId": path.parent.name,
        "sources": len(rows),
        "pixelScaleArcsec": round(pixel_scale, 4),
        "differencePlane": difference_plane,
        "backgroundRmsMedianNjy": float(np.median(background.background_rms)),
        "detectionSigma": DETECT_SIGMA,
        "flaggedNearEdge": int(edge.sum()),
        "medianRubinSnr": float(np.nanmedian(np.asarray(rows["rubin_snr"], dtype=np.float64))),
    }
    return rows, meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    paths = sorted(p for region in sorted(args.products.iterdir()) if region.is_dir()
                   for p in region.glob("*.fits"))
    if args.limit:
        paths = paths[: args.limit]

    tables: list[Table] = []
    per_region: list[dict[str, Any]] = []
    for path in paths:
        try:
            result = measure_region(path)
        except Exception as error:
            print(f"[failed] {path.parent.name}: {type(error).__name__}: {error}", flush=True)
            continue
        if result is None:
            continue
        rows, meta = result
        tables.append(rows)
        per_region.append(meta)
        print(f"{meta['regionId']:18s} {meta['sources']:5d} sources  ({meta['differencePlane']})", flush=True)

    if not tables:
        raise SystemExit("no region produced a catalogue")

    from astropy.table import vstack

    catalogue = vstack(tables)
    args.output.mkdir(parents=True, exist_ok=True)
    parquet_path = args.output / "rubin-reference-sources.parquet"
    votable_path = args.output / "rubin-reference-sources.vot"
    catalogue.write(parquet_path, format="parquet", overwrite=True)
    catalogue.write(votable_path, format="votable", overwrite=True)

    significance = np.asarray(catalogue["difference_significance"], dtype=np.float64)
    departure = np.asarray(catalogue["departure_significance"], dtype=np.float64)
    clean = ~(
        np.asarray(catalogue["flag_near_edge"])
        | np.asarray(catalogue["flag_negative_reference"])
    )
    finite = np.isfinite(significance)
    summary = {
        "schemaVersion": "layers-source-catalogue-v1",
        "generatedAt": utc_now(),
        "purpose": (
            "One row per detected source with Rubin and reference flux measured on identical "
            "pixels, so the comparison produces something that can be cross-matched, queried and "
            "checked rather than only looked at."
        ),
        "method": {
            "detection": f"photutils detect_sources at {DETECT_SIGMA} sigma over a Background2D sky, "
                         f"npixels {MIN_PIXELS}, then deblend_sources",
            "photometry": "segment flux on a shared segmentation, so both frames measure the same pixels",
            "uncertainty": (
                "empirical Background2D RMS propagated over each segment, never the propagated "
                "variance planes: those understate the truth on these products by a median factor "
                "of about seven"
            ),
            "whichSignificanceToUse": (
                "departure_significance is the one to use. It measures how far a source's flux "
                "ratio sits from its own field's median, in units of the field's own measured "
                "scatter, so no error model can be wrong. difference_significance measures "
                "departure from zero and is dominated by the roughly 7% offset between Rubin and "
                "these references, so it flags most bright sources. "
                "departure_significance_propagated divides by photutils' segment_fluxerr, which is "
                "background RMS only and omits the source's own Poisson noise; it is kept for "
                "comparison and understates the uncertainty on bright sources."
            ),
            "flagsNotFiltering": (
                "Rows that should be distrusted are flagged and kept. A filtered catalogue hides "
                "how much was removed; a flagged one lets the reader choose and count. "
                "Signal-to-noise is published as a column rather than a flag, because the obvious "
                "flag never fired: detection at 3 sigma over 5 pixels already guarantees a higher "
                "integrated ratio."
            ),
        },
        "counts": {
            "regions": len(per_region),
            "sources": int(len(catalogue)),
            "clean": int(clean.sum()),
            "flaggedNearEdge": int(np.asarray(catalogue["flag_near_edge"]).sum()),
            "medianRubinSnr": float(np.nanmedian(np.asarray(catalogue["rubin_snr"], dtype=np.float64))),
            "flaggedNegativeReference": int(np.asarray(catalogue["flag_negative_reference"]).sum()),
            "cleanAbove5SigmaFromZero": int((clean & finite & (np.abs(significance) >= 5)).sum()),
            "cleanAbove5SigmaFromFieldRatio": int(
                (clean & np.isfinite(departure) & (np.abs(departure) >= 5)).sum()
            ),
        },
        "products": {
            # Filenames only. The full paths point into pipeline/results, which is
            # gitignored because it holds access-restricted pixels, and a public
            # manifest that prints them describes one machine's filesystem to
            # readers who cannot use it.
            "parquetFile": parquet_path.name,
            "votableFile": votable_path.name,
            "parquetBytes": parquet_path.stat().st_size,
            "votableBytes": votable_path.stat().st_size,
            "published": False,
            "note": (
                "These files are not published in this repository: they are analysis products "
                "built from access-restricted Rubin pixels. Rebuild them with "
                "pipeline/build_source_catalogue.py. VOTable opens directly in TOPCAT, pyvo and "
                "astroquery; Parquet is the columnar form for bulk work."
            ),
        },
        "caveat": (
            "A large departure is not a detection. The filter colour term is measured at -0.080 "
            "mag per mag of Rubin g-r against DECam and +0.007 against PS1, linear to under 4 "
            "millimagnitudes, so colour alone moves a source very little. What remains unexplained "
            "is the empirical field-to-field scatter, which is forty times larger than the filters "
            "permit and is therefore photometric error, crowding, PSF residuals or calibration "
            "structure."
        ),
        "regions": per_region,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("\n" + json.dumps(summary["counts"], indent=2))
    print(f"parquet {parquet_path.stat().st_size / 1e6:.1f} MB, votable {votable_path.stat().st_size / 1e6:.1f} MB")
    print(f"wrote {display_path(args.summary)}")


if __name__ == "__main__":
    main()
