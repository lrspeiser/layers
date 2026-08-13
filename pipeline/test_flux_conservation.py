#!/usr/bin/env python3
"""Regression checks for per-pixel flux conservation during reprojection."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from astropy.wcs import WCS
from reproject import reproject_interp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from download_dp2_matches import pixel_area_arcsec2  # noqa: E402
from fetch_legacy_survey import NATIVE_COADD_PIXEL_SCALE_ARCSEC  # noqa: E402
from fetch_panstarrs import NATIVE_PIXEL_SCALE_ARCSEC, pixel_area_arcsec2 as ps1_pixel_area  # noqa: E402


def make_wcs(size: int, scale_arcsec: float) -> WCS:
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [(size + 1) / 2, (size + 1) / 2]
    wcs.wcs.cdelt = [-scale_arcsec / 3600, scale_arcsec / 3600]
    wcs.wcs.crval = [12.0, -4.0]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return wcs


def main() -> None:
    source_wcs = make_wcs(201, 0.2)
    target_wcs = make_wcs(101, 0.4)
    yy, xx = np.indices((201, 201), dtype=np.float64)
    source = np.exp(-0.5 * (((xx - 100) / 9) ** 2 + ((yy - 100) / 9) ** 2))
    variance = np.full(source.shape, 4.0)
    image, _ = reproject_interp((source, source_wcs), target_wcs, shape_out=(101, 101), order="bilinear")
    output_variance, _ = reproject_interp((variance, source_wcs), target_wcs, shape_out=(101, 101), order="bilinear")
    scale = pixel_area_arcsec2(target_wcs) / pixel_area_arcsec2(source_wcs)
    image *= scale
    output_variance *= scale**2
    relative_flux_error = abs(float(np.nansum(image) / np.sum(source)) - 1)
    if relative_flux_error > 0.005:
        raise SystemExit(f"flux conservation failed: {relative_flux_error:.6f}")
    if not math.isclose(float(np.nanmedian(output_variance)), 4.0 * scale**2, rel_tol=1e-6):
        raise SystemExit("variance area scaling failed")
    service_scale = 0.4**2 / NATIVE_COADD_PIXEL_SCALE_ARCSEC**2
    if not math.isclose(service_scale, 2.331, rel_tol=0.001):
        raise SystemExit("Legacy cutout pixel-area scaling failed")
    panstarrs_scale = ps1_pixel_area(target_wcs) / ps1_pixel_area(make_wcs(201, NATIVE_PIXEL_SCALE_ARCSEC))
    if not math.isclose(panstarrs_scale, 2.56, rel_tol=0.001):
        raise SystemExit("Pan-STARRS full-skycell pixel-area scaling failed")
    print(f"Flux-conserving reprojection passed: relative error {relative_flux_error:.6f}")


if __name__ == "__main__":
    main()
