"""Measure how far resampling correlates neighbouring pixels, and what that does to every error bar.

`resampling covariance` is one of three blockers retained on all 190 reconciled
regions, and the only one never measured. It is not bookkeeping. Bringing two
surveys onto a common pixel grid interpolates, and interpolation makes each
output pixel a weighted sum of several input pixels -- so adjacent output pixels
share input noise and are no longer independent.

Everything downstream assumes they are. The catalogue's `rubin_flux_err_njy` is
the background RMS propagated over a source's aperture as sigma * sqrt(N), which
is the variance of a sum of N *independent* pixels. If the pixels are correlated
the true variance is larger by a factor

    F = sum over the aperture of the noise autocorrelation
      ~ 1 + 2 * sum_k rho_k   for a compact aperture,

and every quoted uncertainty is too small by sqrt(F). This project has twice
found that the wrong uncertainty produces the wrong answer -- variance planes
understating by about seven, and segment_fluxerr omitting source Poisson noise
and turning 763 anomalies into 39. This is the same question asked of the
resampling step.

Method, empirical rather than modelled. Take blank sky -- pixels far from any
detected source -- and measure the noise autocorrelation directly from the
image, then compare the measured scatter of real aperture sums on blank sky
against sigma * sqrt(N). The second is the number that matters: it is the
inflation factor as it actually applies to the catalogue's apertures, with no
assumption that the autocorrelation is isotropic or separable.

Rubin and the reference are treated separately, as a control: the reference is
the frame resampled onto Rubin's grid, so if the effect were resampling the
reference should show more of it.

Over 190 regions it does, but only just. Lag-1 noise autocorrelation is 0.759 in
the reference against 0.682 in Rubin, and the reference's aperture inflation is
slightly worse at every radius. So resampling does add correlation -- and is not
close to being the whole of it. Rubin's own 0.682 was there before this project
touched anything: DP2 coadds are themselves warped and stacked from many
exposures, so their noise arrived correlated. The blocker named "resampling
covariance" is real, and it understates the problem by attributing to
reconciliation something the inputs already had.

Worth recording how easily this went the other way. An eight-region pilot gave
Rubin 0.881 against the reference's 0.680 -- the control inverted, and the
conclusion written from it ("the blocker's name is wrong, this is not resampling")
was the opposite of what 190 regions say. The number that survived the pilot was
not the ordering but the magnitude.

Two effects are separated here, because blank sky is not empty. Tightening the
source mask from 2.0 sigma to 1.5 sigma drops the aperture inflation (Rubin x10.2
to x6.5 at r=3), so part of it is undetected sources rather than pixel
correlation. What does not move is the autocorrelation itself (0.881, 0.875,
0.879 across three mask settings), which says the correlation is a property of
the images rather than an artefact of masking. Both inflate a real aperture sum,
so both belong in an honest error bar; only the first would be removed by better
masking.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
from astropy.io import fits
from photutils.background import Background2D, MedianBackground
from photutils.segmentation import detect_sources

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTS = ROOT / "pipeline/results/reconciled-regions-200"
DEFAULT_OUTPUT = ROOT / "public/data/layers/selected-regions/resampling-covariance.json"

BOX = 64
FILTER = 3
# Mask anything within this many sigma of the background before calling a pixel
# "sky". A source in the aperture would inflate the scatter for reasons that have
# nothing to do with resampling.
SOURCE_SIGMA = 2.0
GROW = 5
# Overridden by --mask-sigma/--grow, so the result can be tested for sensitivity
# to how aggressively sources are masked. If the inflation is pixel correlation it
# barely moves; if it is undetected sources it falls as the mask tightens.
# Aperture radii to test, in pixels. The catalogue's segments run from a handful
# of pixels to a few hundred, so this brackets them.
RADII = (1.5, 2.0, 3.0, 4.0, 6.0)
TRIALS = 400
LAGS = 6


def sky_mask(image: np.ndarray, sigma: float = SOURCE_SIGMA, grow: int = GROW) -> tuple[np.ndarray, float]:
    """Boolean mask of usable blank sky, and the background RMS."""
    finite = np.isfinite(image)
    if finite.sum() < 0.2 * image.size:
        raise ValueError("too few finite pixels")
    background = Background2D(
        image,
        BOX,
        filter_size=FILTER,
        bkg_estimator=MedianBackground(),
        mask=~finite,
    )
    flat = image - background.background
    segments = detect_sources(flat, sigma * background.background_rms, npixels=5)
    blank = finite.copy()
    if segments is not None:
        source = segments.data > 0
        # Grow the source mask: a profile's wings sit below the detection
        # threshold and would otherwise be counted as sky.
        grown = source.copy()
        for shift in range(1, grow + 1):
            grown[shift:, :] |= source[:-shift, :]
            grown[:-shift, :] |= source[shift:, :]
            grown[:, shift:] |= source[:, :-shift]
            grown[:, :-shift] |= source[:, shift:]
        blank &= ~grown
    return blank, flat, float(np.median(background.background_rms))


def autocorrelation(flat: np.ndarray, blank: np.ndarray, lags: int = LAGS) -> list[float]:
    """Noise autocorrelation at integer pixel lags along x, measured on sky only."""
    values = np.where(blank, flat, np.nan)
    centred = values - np.nanmean(values)
    variance = np.nanvar(centred)
    if not np.isfinite(variance) or variance <= 0:
        raise ValueError("no sky variance")
    out = []
    for lag in range(lags + 1):
        a = centred[:, : centred.shape[1] - lag] if lag else centred
        b = centred[:, lag:] if lag else centred
        pair = a * b
        finite = np.isfinite(pair)
        out.append(float(np.nanmean(pair[finite]) / variance) if finite.any() else np.nan)
    return out


def aperture_inflation(
    flat: np.ndarray, blank: np.ndarray, rms: float, rng: np.random.Generator
) -> dict[str, dict[str, float]]:
    """Measured scatter of blank-sky aperture sums against the independent-pixel prediction.

    This is the number that matters. sigma*sqrt(N) is what the catalogue quotes;
    the measured standard deviation of real sums over real sky is the truth.
    """
    height, width = flat.shape
    yy, xx = np.mgrid[0:height, 0:width]
    result = {}
    for radius in RADII:
        edge = int(np.ceil(radius)) + 1
        sums = []
        npix = None
        for _ in range(TRIALS * 4):
            if len(sums) >= TRIALS:
                break
            cy = rng.integers(edge, height - edge)
            cx = rng.integers(edge, width - edge)
            window = (slice(cy - edge, cy + edge + 1), slice(cx - edge, cx + edge + 1))
            disc = (yy[window] - cy) ** 2 + (xx[window] - cx) ** 2 <= radius**2
            if not blank[window][disc].all():
                continue
            patch = flat[window][disc]
            if not np.isfinite(patch).all():
                continue
            sums.append(float(patch.sum()))
            npix = int(disc.sum())
        if len(sums) < 50 or not npix:
            continue
        measured = float(np.std(sums, ddof=1))
        predicted = rms * np.sqrt(npix)
        result[f"r{radius}"] = {
            "radiusPixels": radius,
            "aperturePixels": npix,
            "samples": len(sums),
            "measuredSigma": measured,
            "independentPixelSigma": float(predicted),
            "varianceInflation": float((measured / predicted) ** 2) if predicted > 0 else np.nan,
            "errorBarUnderstatedBy": float(measured / predicted) if predicted > 0 else np.nan,
        }
    return result


def measure(path: pathlib.Path, rng: np.random.Generator, sigma: float = SOURCE_SIGMA, grow: int = GROW) -> dict | None:
    with fits.open(path) as handle:
        available = {hdu.name for hdu in handle}
        if not {"RUBIN", "REFERENCE"} <= available:
            return None
        frames = {name: handle[name].data.astype(float) for name in ("RUBIN", "REFERENCE")}
    out: dict[str, dict] = {}
    for name, image in frames.items():
        try:
            blank, flat, rms = sky_mask(image, sigma, grow)
            if blank.sum() < 0.05 * image.size:
                continue
            out[name.lower()] = {
                "backgroundRms": rms,
                "skyFraction": float(blank.mean()),
                "autocorrelation": autocorrelation(flat, blank),
                "apertures": aperture_inflation(flat, blank, rms, rng),
            }
        except (ValueError, TypeError):
            continue
    return out or None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=pathlib.Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mask-sigma", type=float, default=SOURCE_SIGMA)
    parser.add_argument("--grow", type=int, default=GROW)
    args = parser.parse_args()

    regions = sorted(p for p in args.products.iterdir() if p.is_dir())
    if args.limit:
        regions = regions[: args.limit]

    rng = np.random.default_rng(20260816)
    measured: dict[str, dict] = {}
    for index, region in enumerate(regions, 1):
        products = sorted(region.glob("*.fits"))
        if not products:
            continue
        try:
            result = measure(products[0], rng, args.mask_sigma, args.grow)
        except Exception as error:  # noqa: BLE001 - one bad region must not kill the run
            print(f"  {region.name}: {type(error).__name__}: {error}")
            continue
        if result:
            measured[region.name] = result
        if index % 25 == 0:
            print(f"  {index}/{len(regions)} regions")

    summary = {}
    for frame in ("rubin", "reference"):
        rows = [v[frame] for v in measured.values() if frame in v]
        if not rows:
            continue
        lag1 = [r["autocorrelation"][1] for r in rows if len(r["autocorrelation"]) > 1]
        per_radius = {}
        for key in (f"r{r}" for r in RADII):
            infl = [r["apertures"][key]["varianceInflation"] for r in rows if key in r["apertures"]]
            infl = [v for v in infl if np.isfinite(v)]
            if infl:
                per_radius[key] = {
                    "regions": len(infl),
                    "medianVarianceInflation": float(np.median(infl)),
                    "medianErrorBarUnderstatedBy": float(np.sqrt(np.median(infl))),
                }
        summary[frame] = {
            "regions": len(rows),
            "medianLag1Autocorrelation": float(np.median(lag1)) if lag1 else None,
            "byRadius": per_radius,
        }

    rubin_lag1 = (summary.get("rubin") or {}).get("medianLag1Autocorrelation")
    reference_lag1 = (summary.get("reference") or {}).get("medianLag1Autocorrelation")
    payload = {
        "schemaVersion": "layers-resampling-covariance-v1",
        "question": (
            "Resampling makes each output pixel a weighted sum of several input pixels, so "
            "neighbouring pixels share noise. The catalogue quotes sigma*sqrt(N), which assumes "
            "they do not. By how much is every error bar wrong?"
        ),
        "method": (
            "Empirical. Mask every detected source and grow the mask, then measure the noise "
            "autocorrelation on the remaining sky and the scatter of real aperture sums placed "
            "on blank sky, against the sigma*sqrt(N) the catalogue assumes. Rubin is the control: "
            "it was not resampled onto anything, so it should show less."
        ),
        "regionsMeasured": len(measured),
        "maskSigma": args.mask_sigma,
        "maskGrowPixels": args.grow,
        "summary": summary,
        "control": {
            "rubinLag1": rubin_lag1,
            "referenceLag1": reference_lag1,
            "interpretation": (
                "The reference frame is the one resampled onto Rubin's grid. If its "
                "autocorrelation exceeds Rubin's, the excess is resampling rather than a "
                "generic property of astronomical images."
            ),
        },
        "reproduce": "python pipeline/measure_resampling_covariance.py",
        "regions": measured,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"\nregions measured: {len(measured)}")
    for frame, block in summary.items():
        print(f"\n{frame}: lag-1 autocorrelation {block['medianLag1Autocorrelation']}")
        for key, value in block["byRadius"].items():
            print(
                f"  {key:6s} n={value['regions']:3d}  variance x{value['medianVarianceInflation']:.2f}"
                f"   error bars understated by x{value['medianErrorBarUnderstatedBy']:.2f}"
            )
    print(f"\nwrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
