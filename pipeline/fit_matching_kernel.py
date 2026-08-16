#!/usr/bin/env python3
"""Replace the Gaussian PSF match with a fitted convolution kernel.

The reconciliation matches point spread functions by convolving both frames to a
common circular-Gaussian FWHM. Real PSFs are not circular Gaussians, so the cores
never cancel, and the difference maps show it: the median peak across the 190
regions is over 1000 sigma and 2234 of 2264 peaks sit on sources. Those are
subtraction residuals, not sky.

This fits the kernel instead, in the Alard & Lupton (1998) form used by every
serious difference-imaging pipeline. The kernel is expanded on a basis of
Gaussians multiplied by low-order polynomials, and the coefficients come from a
linear least-squares fit on bright isolated stars:

    reference (*) K  ~  rubin

Linear least squares is the whole point of the Alard-Lupton basis: the kernel
enters the model linearly, so there is no optimisation to get stuck and the
solution is the same every run.

Both directions are fitted and the one with the smaller residual is kept, since
which frame is sharper varies by region and by band.

This does not attempt spatial variation of the kernel across the frame. That is
the next refinement, and the residual measured here is what will show whether it
is needed.

Nothing is claimed to be better without measurement: the script reports the
residual at star positions before and after, per region, and refuses to write a
plane that is worse than the Gaussian match it replaces.
"""

from __future__ import annotations

import argparse
import json
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
from scipy.signal import fftconvolve

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_layer_registration import centroid_sources

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTS = ROOT / "pipeline/results/reconciled-regions-200"
DEFAULT_OUTPUT = ROOT / "public/data/layers/selected-regions/kernel-matching.json"

# Alard & Lupton's standard three-Gaussian basis: a narrow component with the
# most polynomial freedom, a broad one with the least.
BASIS = ((0.7, 2), (1.5, 1), (3.0, 0))
KERNEL_HALF = 7
STAMP_HALF = 12
MIN_STARS = 8
MAX_STARS = 40


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def kernel_basis() -> np.ndarray:
    """Gaussian times polynomial basis images, each KERNEL_HALF-padded."""
    size = 2 * KERNEL_HALF + 1
    y, x = np.mgrid[-KERNEL_HALF : KERNEL_HALF + 1, -KERNEL_HALF : KERNEL_HALF + 1]
    images = []
    for sigma, order in BASIS:
        gauss = np.exp(-(x**2 + y**2) / (2.0 * sigma**2))
        for i in range(order + 1):
            for j in range(order + 1 - i):
                images.append(gauss * (x**i) * (y**j))
    stack = np.array(images, dtype=np.float64)
    # Normalising each component keeps the design matrix well conditioned; the
    # fitted coefficients absorb the scale.
    for index in range(stack.shape[0]):
        norm = np.abs(stack[index]).sum()
        if norm > 0:
            stack[index] /= norm
    return stack.reshape(len(images), size, size)


def star_stamps(image: np.ndarray, sources: list[dict], valid: np.ndarray) -> list[tuple[int, int]]:
    """Bright, isolated, fully-contained star positions."""
    height, width = image.shape
    positions = np.array([[s["x"], s["y"]] for s in sources]) if sources else np.empty((0, 2))
    keep: list[tuple[int, int]] = []
    for source in sources:
        x, y = int(round(source["x"])), int(round(source["y"]))
        if not (STAMP_HALF <= x < width - STAMP_HALF and STAMP_HALF <= y < height - STAMP_HALF):
            continue
        # Isolated: nothing else within two stamp widths, or the fit learns a
        # kernel that also has to explain a neighbour.
        distances = np.hypot(positions[:, 0] - source["x"], positions[:, 1] - source["y"])
        if np.sum(distances < 2 * STAMP_HALF) > 1:
            continue
        window = valid[y - STAMP_HALF : y + STAMP_HALF + 1, x - STAMP_HALF : x + STAMP_HALF + 1]
        if not window.all():
            continue
        keep.append((x, y))
    keep.sort(key=lambda pos: -float(image[pos[1], pos[0]]))
    return keep[:MAX_STARS]


def fit_kernel(
    source_image: np.ndarray, target_image: np.ndarray, stamps: list[tuple[int, int]], basis: np.ndarray
) -> np.ndarray | None:
    """Least-squares coefficients for source (*) K ~ target on the stamps."""
    rows, values = [], []
    for x, y in stamps:
        cut = (slice(y - STAMP_HALF, y + STAMP_HALF + 1), slice(x - STAMP_HALF, x + STAMP_HALF + 1))
        target = target_image[cut]
        # Each basis image convolved with the source stamp is one column.
        columns = []
        wide = (
            slice(y - STAMP_HALF - KERNEL_HALF, y + STAMP_HALF + KERNEL_HALF + 1),
            slice(x - STAMP_HALF - KERNEL_HALF, x + STAMP_HALF + KERNEL_HALF + 1),
        )
        patch = source_image[wide]
        if patch.shape != (2 * (STAMP_HALF + KERNEL_HALF) + 1,) * 2:
            continue
        for component in basis:
            convolved = fftconvolve(patch, component, mode="valid")
            columns.append(convolved.ravel())
        rows.append(np.array(columns).T)
        values.append(target.ravel())
    if len(rows) < MIN_STARS:
        return None
    design = np.vstack(rows)
    observed = np.concatenate(values)
    finite = np.isfinite(design).all(axis=1) & np.isfinite(observed)
    if finite.sum() < design.shape[1] * 10:
        return None
    coefficients, *_ = np.linalg.lstsq(design[finite], observed[finite], rcond=None)
    return np.tensordot(coefficients, basis, axes=(0, 0))


def residual_at_stars(difference: np.ndarray, stamps: list[tuple[int, int]], scatter: float) -> float:
    """Median peak |difference| at star positions, in units of the sky scatter."""
    if not stamps or not np.isfinite(scatter) or scatter <= 0:
        return float("nan")
    peaks = []
    for x, y in stamps:
        cut = difference[y - 3 : y + 4, x - 3 : x + 4]
        if cut.size:
            peaks.append(float(np.nanmax(np.abs(cut))))
    return float(np.median(peaks) / scatter) if peaks else float("nan")


def robust_sigma(values: np.ndarray) -> float:
    if values.size < 16:
        return float("nan")
    lo, hi = np.percentile(values, [15.865, 84.135])
    return float(hi - lo) / 2.0


def process(path: Path, basis: np.ndarray) -> dict[str, Any] | None:
    with fits.open(path, memmap=False) as hdus:
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        reference = np.asarray(hdus["REFERENCE"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data).astype(bool)
        header = hdus["RUBIN"].header
    valid = common & np.isfinite(rubin) & np.isfinite(reference)
    if valid.sum() < 0.2 * rubin.size:
        return None
    scale = float(np.mean(proj_plane_pixel_scales(WCS(header).celestial)) * 3600.0)

    sources = centroid_sources(rubin, valid, 0.0, scale)
    stamps = star_stamps(rubin, sources, valid)
    if len(stamps) < MIN_STARS:
        return None

    filled_rubin = np.where(valid, rubin, 0.0)
    filled_reference = np.where(valid, reference, 0.0)
    baseline = np.where(valid, rubin - reference, np.nan)
    scatter = robust_sigma(baseline[valid])
    before = residual_at_stars(baseline, stamps, scatter)

    best: dict[str, Any] | None = None
    for label, source_image, target_image, sign in (
        ("reference-convolved", filled_reference, filled_rubin, 1.0),
        ("rubin-convolved", filled_rubin, filled_reference, -1.0),
    ):
        kernel = fit_kernel(source_image, target_image, stamps, basis)
        if kernel is None:
            continue
        matched = fftconvolve(source_image, kernel, mode="same")
        difference = np.where(valid, sign * (target_image - matched), np.nan)
        after = residual_at_stars(difference, stamps, scatter)
        if not np.isfinite(after):
            continue
        if best is None or after < best["residualAfter"]:
            best = {
                "direction": label,
                "residualAfter": after,
                "kernelSum": float(kernel.sum()),
                "difference": difference,
            }
    if best is None:
        return None

    improved = np.isfinite(before) and best["residualAfter"] < before
    return {
        "stars": len(stamps),
        "pixelScaleArcsec": round(scale, 4),
        "basisComponents": int(basis.shape[0]),
        "kernelHalfWidthPixels": KERNEL_HALF,
        "direction": best["direction"],
        "kernelSum": round(best["kernelSum"], 4),
        "starResidualBeforeSigma": round(before, 1) if np.isfinite(before) else None,
        "starResidualAfterSigma": round(best["residualAfter"], 1),
        "improvementFactor": round(before / best["residualAfter"], 2)
        if improved and best["residualAfter"] > 0
        else None,
        "improved": bool(improved),
        "difference": best["difference"] if improved else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--write-plane", action="store_true",
                        help="Add a KERNEL_DIFFERENCE extension to each product that improved.")
    args = parser.parse_args()

    basis = kernel_basis()
    paths = sorted(p for region in sorted(args.products.iterdir()) if region.is_dir()
                   for p in region.glob("*.fits"))
    if args.limit:
        paths = paths[: args.limit]

    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            result = process(path, basis)
        except Exception as error:
            print(f"[failed] {path.parent.name}: {type(error).__name__}: {error}", flush=True)
            continue
        if result is None:
            continue
        difference = result.pop("difference")
        result["regionId"] = path.parent.name
        records.append(result)
        if args.write_plane and difference is not None:
            with fits.open(path, mode="update", memmap=False) as hdus:
                if "KERNEL_DIFFERENCE" in [h.name for h in hdus]:
                    del hdus["KERNEL_DIFFERENCE"]
                hdu = fits.ImageHDU(difference.astype(np.float32), name="KERNEL_DIFFERENCE")
                hdu.header["COMMENT"] = "Alard-Lupton fitted-kernel difference; see fit_matching_kernel.py"
                hdu.header["KDIRECT"] = result["direction"]
                hdus.append(hdu)
                hdus.flush()
        print(
            f"{result['regionId']:18s} stars {result['stars']:3d}  "
            f"{result['starResidualBeforeSigma']} -> {result['starResidualAfterSigma']} sigma  "
            f"{'improved' if result['improved'] else 'NOT improved'}",
            flush=True,
        )

    improved = [r for r in records if r["improved"]]
    factors = [r["improvementFactor"] for r in improved if r["improvementFactor"]]
    payload = {
        "schemaVersion": "layers-kernel-matching-v1",
        "generatedAt": utc_now(),
        "method": (
            "Alard & Lupton (1998) kernel expanded on three Gaussians with polynomial "
            "modulation, fitted by linear least squares on bright isolated stars. Both "
            "convolution directions are tried and the smaller residual is kept."
        ),
        "notAttempted": (
            "The kernel is constant across the frame. Spatial variation is the next refinement, "
            "and the residual measured here is what shows whether it is needed."
        ),
        "measuredAgainst": (
            "Median peak absolute difference at star positions, in units of the frame's own "
            "difference scatter, before and after. A region is only counted as improved if that "
            "number falls."
        ),
        "counts": {
            "regionsFitted": len(records),
            "improved": len(improved),
            "notImproved": len(records) - len(improved),
            "medianResidualBeforeSigma": float(np.median([r["starResidualBeforeSigma"] for r in records
                                                          if r["starResidualBeforeSigma"]])) if records else None,
            "medianResidualAfterSigma": float(np.median([r["starResidualAfterSigma"] for r in records])) if records else None,
            "medianImprovementFactor": float(np.median(factors)) if factors else None,
        },
        "regions": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("\n" + json.dumps(payload["counts"], indent=2))
    print(f"wrote {display_path(args.output)}")


if __name__ == "__main__":
    main()
