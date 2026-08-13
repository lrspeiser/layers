#!/usr/bin/env python3
"""Download and mosaic Legacy Survey DR10 image layers for Rubin matches.

The public cutout service currently caps a request at 512 pixels.  This adapter
therefore requests a deterministic overlapping tile grid, retains the original
FITS responses, and mosaics science and inverse-variance planes onto exactly
the same target WCS used by the Rubin adapter.  It never treats a JPEG as
science data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.visualization import AsinhStretch, PercentileInterval, make_lupton_rgb
from astropy.wcs import WCS
from PIL import Image
from reproject import reproject_interp

CUTOUT_ENDPOINT = "https://www.legacysurvey.org/viewer/fits-cutout"
LAYER = "ls-dr10"
BANDS = "griz"
TILE_SIZE = 512
# DR10 coadd bricks are sampled at 0.262 arcsec/pixel.  The viewer cutout
# service changes its output WCS when pixscale= is requested but preserves the
# coadd pixel values, so flux-per-output-pixel needs this explicit area factor.
NATIVE_COADD_PIXEL_SCALE_ARCSEC = 0.262


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output_wcs(target: dict, pixel_scale_arcsec: float) -> tuple[WCS, tuple[int, int]]:
    pixels = math.ceil(target["field_width_arcmin"] * 60.0 / pixel_scale_arcsec)
    if pixels % 2:
        pixels += 1
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [(pixels + 1) / 2.0, (pixels + 1) / 2.0]
    wcs.wcs.cdelt = np.array([-pixel_scale_arcsec / 3600.0, pixel_scale_arcsec / 3600.0])
    wcs.wcs.crval = [target["ra_deg"], target["dec_deg"]]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cunit = ["deg", "deg"]
    return wcs, (pixels, pixels)


def pixel_area_arcsec2(wcs: WCS) -> float:
    matrix = wcs.pixel_scale_matrix * 3600.0
    return float(abs(np.linalg.det(matrix)))


def tile_centers(wcs: WCS, shape: tuple[int, int]) -> list[tuple[int, int, float, float]]:
    count_x = max(1, math.ceil(shape[1] / (TILE_SIZE - 32)))
    count_y = max(1, math.ceil(shape[0] / (TILE_SIZE - 32)))
    centers = []
    for row in range(count_y):
        for column in range(count_x):
            x = (column + 0.5) * shape[1] / count_x - 0.5
            y = (row + 0.5) * shape[0] / count_y - 0.5
            ra, dec = wcs.pixel_to_world_values(x, y)
            centers.append((row, column, float(ra), float(dec)))
    return centers


def valid_tile(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 2880:
        return False
    try:
        with fits.open(path, memmap=True) as hdus:
            return len(hdus) >= 2 and hdus[0].data is not None and hdus[1].data is not None
    except OSError:
        return False


def download_tile(path: Path, ra: float, dec: float, pixel_scale: float) -> tuple[str, str]:
    params = urllib.parse.urlencode(
        {
            "ra": f"{ra:.10f}",
            "dec": f"{dec:.10f}",
            "size": TILE_SIZE,
            "layer": LAYER,
            "pixscale": pixel_scale,
            "bands": BANDS,
            "invvar": "",
        }
    )
    url = f"{CUTOUT_ENDPOINT}?{params}"
    if not valid_tile(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "Layers-science/0.1"})
        with urllib.request.urlopen(request, timeout=300) as response, tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".part") as temporary:
            temporary.write(response.read())
            temporary_path = Path(temporary.name)
        if not valid_tile(temporary_path):
            temporary_path.unlink(missing_ok=True)
            raise RuntimeError(f"Legacy Survey returned an invalid FITS tile for {ra}, {dec}")
        temporary_path.replace(path)
        source = "network"
    else:
        source = "cache"
    return url, source


def mosaic_tiles(tile_paths: list[Path], target_wcs: WCS, shape: tuple[int, int]) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    images: dict[str, np.ndarray] = {}
    inverse_variances: dict[str, np.ndarray] = {}
    for path in tile_paths:
        with fits.open(path, memmap=True) as hdus:
            science_cube = np.asarray(hdus[0].data, dtype=np.float32)
            inverse_variance_cube = np.asarray(hdus[1].data, dtype=np.float32)
            tile_wcs = WCS(hdus[0].header).celestial
            service_scale = pixel_area_arcsec2(tile_wcs) / NATIVE_COADD_PIXEL_SCALE_ARCSEC**2
            reprojection_scale = pixel_area_arcsec2(target_wcs) / pixel_area_arcsec2(tile_wcs)
            flux_scale = service_scale * reprojection_scale
            bands = [str(hdus[0].header[f"BAND{index}"]).strip() for index in range(science_cube.shape[0])]
            for index, band in enumerate(bands):
                image, image_footprint = reproject_interp((science_cube[index], tile_wcs), target_wcs, shape_out=shape, order="bilinear", return_footprint=True)
                ivar, ivar_footprint = reproject_interp((inverse_variance_cube[index], tile_wcs), target_wcs, shape_out=shape, order="bilinear", return_footprint=True)
                image *= flux_scale
                ivar /= flux_scale**2
                valid = (image_footprint > 0) & (ivar_footprint > 0) & np.isfinite(image) & np.isfinite(ivar) & (ivar > 0)
                images.setdefault(band, np.full(shape, np.nan, dtype=np.float32))
                inverse_variances.setdefault(band, np.zeros(shape, dtype=np.float32))
                # Tiles are overlapping cutouts from the same released coadd,
                # not independent exposures.  Select the better-variance
                # resampled value instead of double-counting the same evidence.
                replace = valid & (ivar > inverse_variances[band])
                images[band][replace] = image[replace].astype(np.float32)
                inverse_variances[band][replace] = ivar[replace].astype(np.float32)
    return images, inverse_variances


def write_product(path: Path, image: np.ndarray, inverse_variance: np.ndarray, wcs: WCS, target: dict, band: str) -> None:
    header = wcs.to_header()
    header["OBJECT"] = target["sparc_id"]
    header["SURVEY"] = "DESI Legacy Imaging Surveys"
    header["RELEASE"] = "DR10"
    header["BAND"] = band
    header["BUNIT"] = "nanomaggy"
    header["PIXSCALE"] = abs(wcs.wcs.cdelt[0]) * 3600.0
    header["FLUXCONS"] = (True, "Pixel-area flux conservation applied")
    header["NATPIXS"] = (NATIVE_COADD_PIXEL_SCALE_ARCSEC, "DR10 coadd sampling, arcsec")
    no_data = np.uint8(inverse_variance <= 0)
    fits.HDUList(
        [
            fits.PrimaryHDU(),
            fits.ImageHDU(image, header=header, name="IMAGE"),
            fits.ImageHDU(inverse_variance, header=header, name="IVAR"),
            fits.ImageHDU(no_data, header=header, name="MASK"),
        ]
    ).writeto(path, overwrite=True, checksum=True)


def grayscale(data: np.ndarray) -> np.ndarray:
    finite = np.isfinite(data)
    low, high = PercentileInterval(99.7).get_limits(data[finite])
    scaled = np.clip((data - low) / (high - low), 0, 1)
    return np.uint8(np.nan_to_num(AsinhStretch(0.05)(scaled, clip=True), nan=0.0) * 255)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline" / "results" / "dp2-sparc-coverage.json")
    parser.add_argument("--rubin-mosaics", type=Path, default=root / "pipeline" / "output" / "dp2-sparc" / "mosaic-summary.json")
    parser.add_argument("--output", type=Path, default=root / "pipeline" / "output" / "legacy-survey")
    parser.add_argument("--pixel-scale", type=float, default=0.4)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()

    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    rubin = {item["target"]["slug"]: item for item in json.loads(args.rubin_mosaics.read_text(encoding="utf-8"))}
    selected = {value.lower() for value in args.only}
    targets = [
        target for target in coverage["targets"]
        if rubin.get(target["slug"], {}).get("science_coverage")
        and (not selected or target["slug"].lower() in selected or target["sparc_id"].lower() in selected)
    ]
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "survey": "DESI Legacy Imaging Surveys",
        "release": "DR10",
        "layer": LAYER,
        "cutout_endpoint": CUTOUT_ENDPOINT,
        "units": "nanomaggy",
        "native_coadd_pixel_scale_arcsec": NATIVE_COADD_PIXEL_SCALE_ARCSEC,
        "targets": [],
    }
    for target in targets:
        print(f"[{target['sparc_id']}] fetching Legacy Survey tiles", flush=True)
        target_dir = args.output / target["slug"]
        tile_dir = target_dir / "tiles"
        target_wcs, shape = output_wcs(target, args.pixel_scale)
        tile_records = []
        for row, column, ra, dec in tile_centers(target_wcs, shape):
            tile_path = tile_dir / f"tile-{row:02d}-{column:02d}.fits"
            url, source = download_tile(tile_path, ra, dec, args.pixel_scale)
            tile_records.append({"row": row, "column": column, "ra_deg": ra, "dec_deg": dec, "path": tile_path.as_posix(), "url": url, "source": source, "bytes": tile_path.stat().st_size, "sha256": sha256(tile_path)})
            print(f"  tile {row},{column} {source} {tile_path.stat().st_size / 1024 / 1024:.1f} MiB", flush=True)
        images, inverse_variances = mosaic_tiles([Path(record["path"]) for record in tile_records], target_wcs, shape)
        band_records = {}
        usable_images = {}
        for band in BANDS:
            image = images.get(band)
            ivar = inverse_variances.get(band)
            if image is None or ivar is None:
                continue
            valid = np.isfinite(image) & (ivar > 0)
            product_path = target_dir / f"legacy_{band}.fits"
            write_product(product_path, image, ivar, target_wcs, target, band)
            preview_path = target_dir / f"legacy_{band}.png"
            if valid.any():
                Image.fromarray(grayscale(image), mode="L").save(preview_path, optimize=True)
                usable_images[band] = image
            band_records[band] = {
                "science_coverage": bool(valid.any()),
                "valid_pixel_fraction": float(valid.mean()),
                "product": product_path.as_posix(),
                "product_sha256": sha256(product_path),
                "preview": preview_path.as_posix() if valid.any() else None,
                "preview_sha256": sha256(preview_path) if valid.any() else None,
            }
        rgb_path = target_dir / "legacy_rgb.png"
        if all(band in usable_images for band in ("g", "r", "z")):
            rgb = make_lupton_rgb(usable_images["z"], usable_images["r"], usable_images["g"], stretch=0.5, Q=8)
            Image.fromarray(np.nan_to_num(rgb, nan=0).astype(np.uint8)).save(rgb_path, optimize=True)
            rgb_hash = sha256(rgb_path)
        else:
            rgb_path.unlink(missing_ok=True)
            rgb_hash = None
        manifest["targets"].append(
            {
                "target": target,
                "pixel_scale_arcsec": args.pixel_scale,
                "shape": list(shape),
                "tiles": tile_records,
                "bands": band_records,
                "web_composite": rgb_path.as_posix() if rgb_hash else None,
                "web_composite_sha256": rgb_hash,
            }
        )
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Complete: {len(targets)} Legacy Survey layer sets in {args.output}", flush=True)


if __name__ == "__main__":
    main()
