#!/usr/bin/env python3
"""Acquire full Pan-STARRS skycells and build locally calibrated image layers.

The adapter is deliberately conservative.  It downloads the complete DR1
stack science, variance, and mask files selected by the official image-list
service, preserves those originals with hashes, converts the non-linear full
stack pixels back to linear flux, and only then reprojects them to the same
target WCS used by the Rubin adapter.  Products stay in the ignored local
layer store; this script does not publish a comparison.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.visualization import AsinhStretch, PercentileInterval
from astropy.wcs import WCS
from PIL import Image
from reproject import reproject_interp

IMAGE_LIST_ENDPOINT = "https://ps1images.stsci.edu/cgi-bin/ps1filenames.py"
FILE_ROOT = "https://ps1images.stsci.edu"
AB_ZERO_NJY_MAG = 2.5 * math.log10(3631.0e9)
NATIVE_PIXEL_SCALE_ARCSEC = 0.25


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


def query_image_list(ra_deg: float, dec_deg: float, bands: list[str]) -> tuple[str, list[dict]]:
    params = urllib.parse.urlencode(
        {
            "ra": f"{ra_deg:.10f}",
            "dec": f"{dec_deg:.10f}",
            "filters": "".join(bands),
            "type": "stack,stack.wt,stack.mask",
            "sep": ",",
        }
    )
    url = f"{IMAGE_LIST_ENDPOINT}?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "Layers-science/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        text = response.read().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    return url, rows


def valid_fits(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 2880:
        return False
    try:
        with fits.open(path, memmap=False) as hdus:
            return any(hdu.data is not None and getattr(hdu.data, "ndim", 0) == 2 for hdu in hdus)
    except OSError:
        return False


def download_full_skycell(filename: str, path: Path) -> tuple[str, str]:
    url = f"{FILE_ROOT}{filename}"
    if valid_fits(path):
        return url, "cache"
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Layers-science/0.1"})
    with urllib.request.urlopen(request, timeout=600) as response, tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, suffix=".part"
    ) as temporary:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            temporary.write(chunk)
        temporary_path = Path(temporary.name)
    if not valid_fits(temporary_path):
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f"Pan-STARRS returned an invalid FITS file: {url}")
    temporary_path.replace(path)
    return url, "network"


def image_plane(path: Path) -> tuple[np.ndarray, fits.Header]:
    with fits.open(path, memmap=False) as hdus:
        for hdu in hdus:
            if hdu.data is not None and getattr(hdu.data, "ndim", 0) == 2:
                return np.asarray(hdu.data).copy(), hdu.header.copy()
    raise RuntimeError(f"No 2D image plane in {path}")


def celestial_wcs(header: fits.Header) -> WCS:
    # Official PS1 documentation notes that full skycells omit RADESYS.
    # Supplying FK5 prevents consumers from silently interpreting B1950.
    if "RADESYS" not in header:
        header["RADESYS"] = "FK5"
    return WCS(header).celestial


def linear_stack_flux(data: np.ndarray, header: fits.Header) -> np.ndarray:
    values = np.asarray(data, dtype=np.float32)
    if "BSOFTEN" not in header or "BOFFSET" not in header:
        return values
    bsoften = np.float32(header["BSOFTEN"])
    boffset = np.float32(header["BOFFSET"])
    exponent = np.float32(0.4) * values
    return boffset + bsoften * (np.power(np.float32(10.0), exponent) - np.power(np.float32(10.0), -exponent))


def grayscale(data: np.ndarray, valid: np.ndarray) -> np.ndarray:
    low, high = PercentileInterval(99.7).get_limits(data[valid])
    scaled = np.clip((data - low) / max(high - low, np.finfo(np.float32).eps), 0, 1)
    return np.uint8(np.nan_to_num(AsinhStretch(0.05)(scaled, clip=True), nan=0.0) * 255)


def write_product(
    path: Path,
    image_njy: np.ndarray,
    variance_njy2: np.ndarray,
    mask: np.ndarray,
    wcs: WCS,
    target: dict,
    band: str,
) -> None:
    header = wcs.to_header()
    header["OBJECT"] = target["sparc_id"]
    header["SURVEY"] = "Pan-STARRS1"
    header["RELEASE"] = "DR1 3pi stack"
    header["BAND"] = band
    header["BUNIT"] = "nJy"
    header["PIXSCALE"] = abs(wcs.wcs.cdelt[0]) * 3600.0
    header["ABZPNJY"] = AB_ZERO_NJY_MAG
    header["PS1CAL"] = "per-skycell"
    fits.HDUList(
        [
            fits.PrimaryHDU(),
            fits.ImageHDU(image_njy.astype(np.float32), header=header, name="IMAGE"),
            fits.ImageHDU(variance_njy2.astype(np.float32), header=header, name="VARIANCE"),
            fits.ImageHDU(mask.astype(np.uint32), header=header, name="MASK"),
        ]
    ).writeto(path, overwrite=True, checksum=True)


def needed_targets(coverage: dict, rubin_manifest: list[dict], legacy_manifest: dict, threshold: float) -> list[tuple[dict, list[str]]]:
    target_by_slug = {target["slug"]: target for target in coverage["targets"]}
    legacy_by_slug = {record["target"]["slug"]: record for record in legacy_manifest.get("targets", [])}
    needed = []
    for rubin in rubin_manifest:
        if not rubin.get("science_coverage"):
            continue
        rubin_bands = [band for band, record in rubin.get("bands", {}).items() if record.get("science_coverage")]
        legacy = legacy_by_slug.get(rubin["target"]["slug"], {})
        common = [
            band
            for band in rubin_bands
            if legacy.get("bands", {}).get(band, {}).get("science_coverage")
            and legacy["bands"][band].get("valid_pixel_fraction", 0) >= threshold
        ]
        if not common:
            needed.append((target_by_slug[rubin["target"]["slug"]], rubin_bands))
    return needed


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline" / "results" / "dp2-sparc-coverage.json")
    parser.add_argument("--rubin-mosaics", type=Path, default=root / "pipeline" / "output" / "dp2-sparc" / "mosaic-summary.json")
    parser.add_argument("--legacy", type=Path, default=root / "pipeline" / "output" / "legacy-survey" / "manifest.json")
    parser.add_argument("--output", type=Path, default=root / "pipeline" / "output" / "panstarrs")
    parser.add_argument("--pixel-scale", type=float, default=0.4)
    parser.add_argument("--coverage-threshold", type=float, default=0.5)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()

    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    rubin_manifest = json.loads(args.rubin_mosaics.read_text(encoding="utf-8"))
    legacy_manifest = json.loads(args.legacy.read_text(encoding="utf-8")) if args.legacy.is_file() else {"targets": []}
    selected = {value.lower() for value in args.only}
    candidates = [
        (target, bands)
        for target, bands in needed_targets(coverage, rubin_manifest, legacy_manifest, args.coverage_threshold)
        if not selected or target["slug"].lower() in selected or target["sparc_id"].lower() in selected
    ]
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "survey": "Pan-STARRS1",
        "release": "DR1 3pi stacks",
        "image_list_endpoint": IMAGE_LIST_ENDPOINT,
        "documentation": "https://outerspace.stsci.edu/spaces/PANSTARRS/pages/298812251/PS1+Image+Cutout+Service",
        "targets": [],
    }
    for target, bands in candidates:
        print(f"[{target['sparc_id']}] querying Pan-STARRS for {','.join(bands)}", flush=True)
        target_dir = args.output / target["slug"]
        raw_dir = target_dir / "raw"
        target_wcs, shape = output_wcs(target, args.pixel_scale)
        query_urls = []
        rows_by_filename = {}
        # The image-list service returns the best skycell at a point, not every
        # skycell intersecting a large field.  Query a 3x3 grid and mosaic the
        # unique full skycells so field edges cannot silently become blank.
        for y in (0.0, (shape[0] - 1) / 2.0, float(shape[0] - 1)):
            for x in (0.0, (shape[1] - 1) / 2.0, float(shape[1] - 1)):
                ra, dec = target_wcs.pixel_to_world_values(x, y)
                query_url, point_rows = query_image_list(float(ra), float(dec), bands)
                query_urls.append(query_url)
                for row in point_rows:
                    rows_by_filename[row["filename"]] = row
        rows = list(rows_by_filename.values())
        band_records = {}
        for band in bands:
            cells = {}
            for row in rows:
                if row.get("filter") != band or row.get("badflag", "0") != "0":
                    continue
                cell = f"{int(row['projcell']):04d}.{int(row['subcell']):03d}"
                cells.setdefault(cell, {})[row["type"]] = row
            cells = {
                cell: matches
                for cell, matches in cells.items()
                if all(kind in matches for kind in ("stack", "stack.wt", "stack.mask"))
            }
            if not cells:
                band_records[band] = {"science_coverage": False, "reason": "Missing science, variance, or mask support product."}
                continue
            originals = []
            calibrations = []
            image_njy = np.full(shape, np.nan, dtype=np.float32)
            variance_njy2 = np.full(shape, np.nan, dtype=np.float32)
            output_mask = np.full(shape, np.uint32(2**31), dtype=np.uint32)
            valid = np.zeros(shape, dtype=bool)
            for cell, matches in sorted(cells.items()):
                local_paths = {}
                for kind in ("stack", "stack.wt", "stack.mask"):
                    row = matches[kind]
                    raw_path = raw_dir / row["shortname"]
                    url, source = download_full_skycell(row["filename"], raw_path)
                    local_paths[kind] = raw_path
                    originals.append(
                        {
                            "type": kind,
                            "filename": row["filename"],
                            "url": url,
                            "path": raw_path.as_posix(),
                            "bytes": raw_path.stat().st_size,
                            "sha256": sha256(raw_path),
                            "source": source,
                            "skycell": cell,
                        }
                    )
                    print(f"  {cell} {kind} {source} {raw_path.stat().st_size / 1024 / 1024:.1f} MiB", flush=True)

                science_data, science_header = image_plane(local_paths["stack"])
                variance_data, variance_header = image_plane(local_paths["stack.wt"])
                mask_data, mask_header = image_plane(local_paths["stack.mask"])
                linear_flux = linear_stack_flux(science_data, science_header)
                science_wcs = celestial_wcs(science_header)
                variance_wcs = celestial_wcs(variance_header)
                mask_wcs = celestial_wcs(mask_header)
                cell_image, image_footprint = reproject_interp(
                    (linear_flux, science_wcs), target_wcs, shape_out=shape, order="bilinear", return_footprint=True
                )
                # Nearest-neighbour variance propagation avoids claiming that
                # a bilinear average is an independent-pixel uncertainty model.
                cell_variance, variance_footprint = reproject_interp(
                    (np.asarray(variance_data, dtype=np.float32), variance_wcs),
                    target_wcs,
                    shape_out=shape,
                    order="nearest-neighbor",
                    return_footprint=True,
                )
                mask_values, mask_footprint = reproject_interp(
                    (np.asarray(mask_data, dtype=np.float32), mask_wcs),
                    target_wcs,
                    shape_out=shape,
                    order="nearest-neighbor",
                    return_footprint=True,
                )
                exptime = float(science_header["EXPTIME"])
                zero_point = 25.0 + 2.5 * math.log10(exptime)
                factor = 10 ** ((AB_ZERO_NJY_MAG - zero_point) / 2.5)
                cell_image = np.asarray(cell_image, dtype=np.float32) * np.float32(factor)
                cell_variance = np.asarray(cell_variance, dtype=np.float32) * np.float32(factor * factor)
                cell_valid = (
                    (image_footprint > 0)
                    & (variance_footprint > 0)
                    & (mask_footprint > 0)
                    & np.isfinite(cell_image)
                    & np.isfinite(cell_variance)
                    & (cell_variance > 0)
                    & np.isfinite(mask_values)
                    & (mask_values == 0)
                )
                # Overlapping skycells re-use observations, so inverse-variance
                # coaddition would double count correlated evidence.  Select
                # the unmasked sample with lower documented variance instead.
                replace = cell_valid & (~valid | (cell_variance < variance_njy2))
                image_njy[replace] = cell_image[replace]
                variance_njy2[replace] = cell_variance[replace]
                output_mask[replace] = 0
                valid[replace] = True
                calibrations.append(
                    {
                        "skycell": cell,
                        "exptime_seconds": exptime,
                        "zero_point": zero_point,
                        "nJy_per_data_unit": factor,
                    }
                )
            product_path = target_dir / f"panstarrs_{band}.fits"
            target_dir.mkdir(parents=True, exist_ok=True)
            write_product(product_path, image_njy, variance_njy2, output_mask, target_wcs, target, band)
            preview_path = target_dir / f"panstarrs_{band}.png"
            if valid.any():
                Image.fromarray(grayscale(image_njy, valid), mode="L").save(preview_path, optimize=True)
            else:
                preview_path.unlink(missing_ok=True)
            band_records[band] = {
                "science_coverage": bool(valid.any()),
                "valid_pixel_fraction": float(valid.mean()),
                "product": product_path.as_posix(),
                "product_sha256": sha256(product_path),
                "preview": preview_path.as_posix() if valid.any() else None,
                "preview_sha256": sha256(preview_path) if valid.any() else None,
                "calibrations": calibrations,
                "originals": originals,
                "variance_note": "PS1 stack.wt is documented as variance; nearest-neighbour reprojection does not model resampling covariance.",
            }
        manifest["targets"].append(
            {
                "target": target,
                "query_urls": query_urls,
                "pixel_scale_arcsec": args.pixel_scale,
                "shape": list(shape),
                "bands": band_records,
            }
        )
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Complete: {len(candidates)} Pan-STARRS layer sets in {args.output}", flush=True)


if __name__ == "__main__":
    main()
