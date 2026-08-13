#!/usr/bin/env python3
"""Export target-centered EDP2 deep-coadd mosaics from an authenticated RSP session.

Run this script inside the Rubin Science Platform. It deliberately creates no
public manifest: publication is a separate, validation-gated step.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
from astropy.io import fits
from astropy.visualization import ImageNormalize, PercentileInterval, AsinhStretch, make_lupton_rgb
from astropy.wcs import WCS
from lsst.daf.butler import Butler
from PIL import Image
from reproject import reproject_interp

BANDS = ("u", "g", "r", "i", "z", "y")


@dataclass(frozen=True)
class Target:
    slug: str
    name: str
    ra_deg: float
    dec_deg: float
    field_width_arcmin: float


def read_targets(path: Path, only: set[str]) -> list[Target]:
    with path.open(newline="", encoding="utf-8") as handle:
        targets = [
            Target(
                slug=row["slug"],
                name=row["name"],
                ra_deg=float(row["ra_deg"]),
                dec_deg=float(row["dec_deg"]),
                field_width_arcmin=float(row["field_width_arcmin"]),
            )
            for row in csv.DictReader(handle)
        ]
    return [target for target in targets if not only or target.slug in only]


def output_wcs(target: Target, pixel_scale_arcsec: float) -> tuple[WCS, tuple[int, int]]:
    pixels = math.ceil(target.field_width_arcmin * 60.0 / pixel_scale_arcsec)
    if pixels % 2:
        pixels += 1
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [(pixels + 1) / 2.0, (pixels + 1) / 2.0]
    wcs.wcs.cdelt = np.array([-pixel_scale_arcsec / 3600.0, pixel_scale_arcsec / 3600.0])
    wcs.wcs.crval = [target.ra_deg, target.dec_deg]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cunit = ["deg", "deg"]
    return wcs, (pixels, pixels)


def query_patch_refs(butler: Butler, target: Target, band: str, spacing_arcmin: float = 4.0):
    """Query a grid so fields wider than one coadd patch include every overlap."""
    half = target.field_width_arcmin / 2.0
    offsets = np.arange(-half, half + spacing_arcmin, spacing_arcmin)
    unique = {}
    cos_dec = max(math.cos(math.radians(target.dec_deg)), 0.1)
    for dy in offsets:
        for dx in offsets:
            if abs(dx) > half or abs(dy) > half:
                continue
            ra = (target.ra_deg + dx / 60.0 / cos_dec) % 360.0
            dec = target.dec_deg + dy / 60.0
            refs = butler.query_datasets(
                "deep_coadd",
                where="band.name = :band AND patch.region OVERLAPS POINT(:ra, :dec)",
                bind={"band": band, "ra": ra, "dec": dec},
            )
            for ref in refs:
                unique[str(ref.id)] = ref
    return list(unique.values())


def pixel_area_arcsec2(wcs: WCS) -> float:
    matrix = wcs.pixel_scale_matrix * 3600.0
    return float(abs(np.linalg.det(matrix)))


def mosaic_refs(butler: Butler, refs: Iterable, target_wcs: WCS, shape: tuple[int, int]):
    weighted_sum = np.zeros(shape, dtype=np.float64)
    weight_sum = np.zeros(shape, dtype=np.float64)
    output_mask = np.zeros(shape, dtype=np.uint32)

    for ref in refs:
        coadd = butler.get(ref)
        flux_scale = pixel_area_arcsec2(target_wcs) / pixel_area_arcsec2(coadd.astropy_wcs)
        image, footprint = reproject_interp(
            (np.asarray(coadd.image.array, dtype=np.float32), coadd.astropy_wcs),
            target_wcs,
            shape_out=shape,
            order="bilinear",
            return_footprint=True,
        )
        variance, _ = reproject_interp(
            (np.asarray(coadd.variance.array, dtype=np.float32), coadd.astropy_wcs),
            target_wcs,
            shape_out=shape,
            order="bilinear",
            return_footprint=True,
        )
        image *= flux_scale
        variance *= flux_scale**2
        mask, mask_footprint = reproject_interp(
            (np.asarray(coadd.mask.array, dtype=np.float32), coadd.astropy_wcs),
            target_wcs,
            shape_out=shape,
            order="nearest-neighbor",
            return_footprint=True,
        )
        valid = (footprint > 0) & np.isfinite(image) & np.isfinite(variance) & (variance > 0)
        weights = np.zeros(shape, dtype=np.float64)
        weights[valid] = footprint[valid] / variance[valid]
        weighted_sum[valid] += image[valid] * weights[valid]
        weight_sum[valid] += weights[valid]
        mask_valid = (mask_footprint > 0) & np.isfinite(mask)
        output_mask[mask_valid] |= mask[mask_valid].astype(np.uint32)

    science = np.full(shape, np.nan, dtype=np.float32)
    variance = np.full(shape, np.nan, dtype=np.float32)
    good = weight_sum > 0
    science[good] = (weighted_sum[good] / weight_sum[good]).astype(np.float32)
    variance[good] = (1.0 / weight_sum[good]).astype(np.float32)
    return science, variance, output_mask


def write_fits(path: Path, science: np.ndarray, variance: np.ndarray, mask: np.ndarray, wcs: WCS, target: Target, band: str):
    header = wcs.to_header()
    header["OBJECT"] = target.name
    header["BAND"] = band
    header["RELEASE"] = "EDP2"
    header["BUNIT"] = "nJy"
    header["FLUXCONS"] = (True, "Pixel-area flux conservation applied")
    hdus = fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(science, header=header, name="SCI"),
        fits.ImageHDU(variance, header=header, name="VAR"),
        fits.ImageHDU(mask, header=header, name="MASK"),
    ])
    hdus.writeto(path, overwrite=True, checksum=True)


def write_band_preview(path: Path, data: np.ndarray):
    finite = np.isfinite(data)
    if not finite.any():
        raise ValueError("Cannot preview an empty mosaic")
    normalized = ImageNormalize(data, interval=PercentileInterval(99.5), stretch=AsinhStretch(0.08), clip=True)(data)
    pixels = np.nan_to_num(normalized, nan=0.0)
    Image.fromarray(np.uint8(np.clip(pixels, 0, 1) * 255), mode="L").save(path, optimize=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_target(butler: Butler, target: Target, root: Path, pixel_scale: float, max_pixels: int):
    target_dir = root / target.slug
    target_dir.mkdir(parents=True, exist_ok=True)
    wcs, shape = output_wcs(target, pixel_scale)
    if max(shape) > max_pixels:
        raise ValueError(
            f"{target.slug} requests {shape[0]} px; raise --max-pixels intentionally or use a coarser --pixel-scale"
        )

    provenance = {
        "schemaVersion": 1,
        "release": "EDP2",
        "collection": "dp2",
        "datasetType": "deep_coadd",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "target": asdict(target),
        "pixelScaleArcsec": pixel_scale,
        "shape": list(shape),
        "bands": {},
        "sourceSha256": {},
    }
    arrays = {}

    for band in BANDS:
        refs = query_patch_refs(butler, target, band)
        if not refs:
            provenance["bands"][band] = {"coverage": "not-covered", "datasetIds": []}
            continue
        science, variance, mask = mosaic_refs(butler, refs, wcs, shape)
        if not np.isfinite(science).any():
            provenance["bands"][band] = {"coverage": "empty", "datasetIds": [str(ref.id) for ref in refs]}
            continue
        fits_path = target_dir / f"rubin_{band}.fits"
        png_path = target_dir / f"rubin_{band}.png"
        write_fits(fits_path, science, variance, mask, wcs, target, band)
        write_band_preview(png_path, science)
        provenance["bands"][band] = {"coverage": "covered", "datasetIds": sorted(str(ref.id) for ref in refs)}
        provenance["sourceSha256"][fits_path.name] = sha256(fits_path)
        provenance["sourceSha256"][png_path.name] = sha256(png_path)
        arrays[band] = science

    if all(band in arrays for band in ("g", "r", "i")):
        rgb = make_lupton_rgb(arrays["i"], arrays["r"], arrays["g"], stretch=1.0, Q=8)
        rgb_path = target_dir / "rubin_rgb.png"
        Image.fromarray(rgb).save(rgb_path, optimize=True)
        provenance["sourceSha256"][rgb_path.name] = sha256(rgb_path)

    (target_dir / "edp2_provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return provenance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=Path, default=Path(__file__).with_name("targets.csv"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("output"))
    parser.add_argument("--only", action="append", default=[], help="Target slug; repeat to select multiple")
    parser.add_argument("--pixel-scale", type=float, default=0.4, help="Output arcsec/pixel; choose explicitly for the science use case")
    parser.add_argument("--max-pixels", type=int, default=8000, help="Safety limit on one image dimension")
    args = parser.parse_args()

    targets = read_targets(args.targets, set(args.only))
    if not targets:
        raise SystemExit("No matching targets")
    butler = Butler("dp2", collections=["dp2"])
    summary = {}
    for target in targets:
        print(f"[{target.slug}] querying EDP2 deep_coadd patches")
        summary[target.slug] = process_target(butler, target, args.output, args.pixel_scale, args.max_pixels)
    (args.output / "coverage-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
