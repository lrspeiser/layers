#!/usr/bin/env python3
"""Download and mosaic real DP2 deep coadds for SPARC overlap targets.

Coverage discovery is read from ``dp2-sparc-coverage.json``.  This command
uses one batched DataLink request to obtain time-limited, signed links for the
selected full deep-coadd patches, then downloads those immutable FITS files
directly from object storage.  It does not consume SODA cutout quota.

The final products retain calibrated image (nJy), variance (nJy^2), integer
mask, WCS, dataset identifiers, source hashes, and a web preview.  They remain
private pipeline artifacts until the separate registration QA gate passes.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from astropy.io import fits
from astropy.visualization import AsinhStretch, PercentileInterval, make_lupton_rgb
from astropy.wcs import WCS
from PIL import Image
from reproject import reproject_interp

VOTABLE_NS = "{http://www.ivoa.net/xml/VOTable/v1.3}"
DATALINK_ENDPOINT = "https://data.lsst.cloud/api/datalink/links"
BAND_CENTERS_M = {
    "u": 0.367e-6,
    "g": 0.482e-6,
    "r": 0.622e-6,
    "i": 0.755e-6,
    "z": 0.869e-6,
    "y": 0.971e-6,
}


@dataclass(frozen=True)
class Dataset:
    target_slug: str
    sparc_id: str
    obs_id: str
    publisher_id: str
    band: str
    s_region: str
    expected_size: int | None = None
    access_url: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_env_value(path: Path, name: str) -> str:
    if value := os.environ.get(name):
        return value.strip()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    raise SystemExit(f"Missing {name} in environment or {path}")


def parse_votable_table(path: Path) -> list[dict[str, str]]:
    root = ET.parse(path).getroot()
    fields = root.findall(f".//{VOTABLE_NS}RESOURCE[@type='results']/{VOTABLE_NS}TABLE/{VOTABLE_NS}FIELD")
    if not fields:
        fields = root.findall(f".//{VOTABLE_NS}TABLE/{VOTABLE_NS}FIELD")
    names = [field.attrib.get("name", "") for field in fields]
    rows = []
    for row in root.findall(f".//{VOTABLE_NS}RESOURCE[@type='results']//{VOTABLE_NS}TABLEDATA/{VOTABLE_NS}TR"):
        values = [cell.text or "" for cell in row.findall(f"{VOTABLE_NS}TD")]
        rows.append(dict(zip(names, values, strict=False)))
    if not rows:
        for row in root.findall(f".//{VOTABLE_NS}TABLEDATA/{VOTABLE_NS}TR"):
            values = [cell.text or "" for cell in row.findall(f"{VOTABLE_NS}TD")]
            rows.append(dict(zip(names, values, strict=False)))
    return rows


def band_from_wavelength(em_min: str, em_max: str) -> str:
    midpoint = (float(em_min) + float(em_max)) / 2.0
    return min(BAND_CENTERS_M, key=lambda name: abs(BAND_CENTERS_M[name] - midpoint))


def polygon_bbox(region: str) -> tuple[float, float, float, float]:
    values = [float(value) for value in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?", region)]
    if len(values) < 6:
        raise ValueError(f"Unsupported SIA region: {region}")
    ras, decs = values[0::2], values[1::2]
    return min(ras), max(ras), min(decs), max(decs)


def overlaps_target_square(row: dict[str, str], target: dict) -> bool:
    half_dec = target["field_width_arcmin"] / 120.0
    half_ra = half_dec / max(math.cos(math.radians(target["dec_deg"])), 0.1)
    target_bbox = (
        target["ra_deg"] - half_ra,
        target["ra_deg"] + half_ra,
        target["dec_deg"] - half_dec,
        target["dec_deg"] + half_dec,
    )
    patch_bbox = polygon_bbox(row["s_region"])
    return not (
        patch_bbox[1] < target_bbox[0]
        or patch_bbox[0] > target_bbox[1]
        or patch_bbox[3] < target_bbox[2]
        or patch_bbox[2] > target_bbox[3]
    )


def selected_datasets(coverage_path: Path, sia_cache: Path, only: set[str]) -> tuple[list[dict], list[Dataset]]:
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    targets = []
    datasets = []
    for target in coverage["targets"]:
        if target["deep_coadd_rows"] <= 0:
            continue
        if only and target["slug"].lower() not in only and target["sparc_id"].lower() not in only:
            continue
        rows = parse_votable_table(sia_cache / f"{target['slug']}-sia.xml")
        chosen = [row for row in rows if overlaps_target_square(row, target)]
        if not chosen:
            raise RuntimeError(f"{target['sparc_id']}: coverage was reported but no patch overlaps the output field")
        targets.append(target)
        for row in chosen:
            datasets.append(
                Dataset(
                    target_slug=target["slug"],
                    sparc_id=target["sparc_id"],
                    obs_id=row["obs_id"],
                    publisher_id=row["obs_publisher_did"],
                    band=band_from_wavelength(row["em_min"], row["em_max"]),
                    s_region=row["s_region"],
                )
            )
    return targets, datasets


def datalink_batch(datasets: list[Dataset], token: str, cache_path: Path) -> dict[str, tuple[str, int | None]]:
    response = requests.post(
        DATALINK_ENDPOINT,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/x-votable+xml;content=datalink"},
        data=[("ID", dataset.publisher_id) for dataset in datasets],
        timeout=300,
    )
    response.raise_for_status()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(response.content)
    links = {}
    for row in parse_votable_table(cache_path):
        if row.get("semantics") != "#this" or not row.get("access_url"):
            continue
        size = int(row["content_length"]) if row.get("content_length", "").strip() else None
        links[row["ID"]] = (row["access_url"], size)
    missing = sorted({dataset.publisher_id for dataset in datasets} - links.keys())
    if missing:
        raise RuntimeError(f"DataLink did not return primary FITS links for {len(missing)} datasets")
    return links


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(dataset: Dataset) -> str:
    uuid = dataset.publisher_id.rsplit("id=", 1)[-1]
    return f"{dataset.obs_id}_{dataset.band}_{uuid}.fits"


def valid_cached_fits(path: Path, expected_size: int | None) -> bool:
    if not path.exists() or path.stat().st_size < 2880:
        return False
    if expected_size is not None and path.stat().st_size != expected_size:
        return False
    try:
        with fits.open(path, memmap=True) as hdus:
            return all(name in hdus for name in ("IMAGE", "VARIANCE", "MASK"))
    except OSError:
        return False


def download_one(dataset: Dataset, root: Path) -> dict:
    assert dataset.access_url is not None
    target_dir = root / dataset.target_slug / "patches" / dataset.band
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / safe_filename(dataset)
    if not valid_cached_fits(path, dataset.expected_size):
        with requests.get(dataset.access_url, stream=True, timeout=(30, 600)) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(dir=target_dir, delete=False, suffix=".part") as temporary:
                temp_path = Path(temporary.name)
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        temporary.write(chunk)
        if dataset.expected_size is not None and temp_path.stat().st_size != dataset.expected_size:
            temp_path.unlink(missing_ok=True)
            raise RuntimeError(f"{dataset.publisher_id}: expected {dataset.expected_size} bytes")
        if not valid_cached_fits(temp_path, dataset.expected_size):
            temp_path.unlink(missing_ok=True)
            raise RuntimeError(f"{dataset.publisher_id}: downloaded file failed FITS validation")
        os.replace(temp_path, path)
        source = "network"
    else:
        source = "cache"
    return {
        "target_slug": dataset.target_slug,
        "sparc_id": dataset.sparc_id,
        "obs_id": dataset.obs_id,
        "publisher_id": dataset.publisher_id,
        "band": dataset.band,
        "path": path.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "source": source,
    }


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


def serialized_image_metadata(hdus: fits.HDUList) -> dict:
    """Return the LSST image metadata embedded in a modern Exposure FITS.

    Rubin serializes each patch array with a non-zero ``yx0`` origin.  The
    FITS IMAGE/VARIANCE/MASK headers already express their WCS in *local array
    coordinates*: their CRPIX values have been shifted by that origin.  The
    JSON origin is therefore provenance and a consistency check, not another
    offset to apply.  Applying it twice moves a patch by many degrees.
    """

    if "JSON" not in hdus:
        return {}
    payload = bytes(hdus["JSON"].data[0]["JSON"]).decode("utf-8")
    metadata = json.loads(payload)
    origins = {
        plane: tuple(metadata.get(plane, {}).get("yx0", []))
        for plane in ("image", "variance", "mask")
    }
    declared = {origin for origin in origins.values() if origin}
    if len(declared) > 1:
        raise ValueError(f"Rubin plane origins disagree: {origins}")
    return {
        "yx0": list(next(iter(declared))) if declared else None,
        "schema_version": metadata.get("schema_version"),
    }


def mosaic_band(paths: list[Path], target_wcs: WCS, shape: tuple[int, int]):
    weighted_sum = np.zeros(shape, dtype=np.float64)
    weight_sum = np.zeros(shape, dtype=np.float64)
    combined_mask = np.zeros(shape, dtype=np.uint32)
    rejected_bits = (1 << 0) | (1 << 3)  # NO_DATA and SATURATED from Rubin mask schema order.

    for path in paths:
        with fits.open(path, memmap=True) as hdus:
            image_hdu = hdus["IMAGE"]
            variance_hdu = hdus["VARIANCE"]
            mask_hdu = hdus["MASK"]
            serialized_image_metadata(hdus)
            source_wcs = WCS(image_hdu.header)
            image, footprint = reproject_interp(
                (np.asarray(image_hdu.data, dtype=np.float32), source_wcs),
                target_wcs,
                shape_out=shape,
                order="bilinear",
                return_footprint=True,
            )
            variance, variance_footprint = reproject_interp(
                (np.asarray(variance_hdu.data, dtype=np.float32), WCS(variance_hdu.header)),
                target_wcs,
                shape_out=shape,
                order="bilinear",
                return_footprint=True,
            )
            mask, mask_footprint = reproject_interp(
                (np.asarray(mask_hdu.data, dtype=np.float32), WCS(mask_hdu.header)),
                target_wcs,
                shape_out=shape,
                order="nearest-neighbor",
                return_footprint=True,
            )
        mask_values = np.nan_to_num(mask, nan=rejected_bits).astype(np.uint32)
        valid = (
            (footprint > 0)
            & (variance_footprint > 0)
            & np.isfinite(image)
            & np.isfinite(variance)
            & (variance > 0)
            & ((mask_values & rejected_bits) == 0)
        )
        weights = np.zeros(shape, dtype=np.float64)
        weights[valid] = np.minimum(footprint[valid], variance_footprint[valid]) / variance[valid]
        weighted_sum[valid] += image[valid] * weights[valid]
        weight_sum[valid] += weights[valid]
        mask_valid = (mask_footprint > 0) & np.isfinite(mask)
        combined_mask[mask_valid] |= mask_values[mask_valid]

    science = np.full(shape, np.nan, dtype=np.float32)
    variance = np.full(shape, np.nan, dtype=np.float32)
    good = weight_sum > 0
    science[good] = (weighted_sum[good] / weight_sum[good]).astype(np.float32)
    variance[good] = (1.0 / weight_sum[good]).astype(np.float32)
    return science, variance, combined_mask, good


def write_mosaic(path: Path, science, variance, mask, wcs: WCS, target: dict, band: str) -> None:
    header = wcs.to_header()
    header["OBJECT"] = target["sparc_id"]
    header["BAND"] = band
    header["RELEASE"] = "DP2"
    header["BUNIT"] = "nJy"
    header["PIXSCALE"] = abs(wcs.wcs.cdelt[0]) * 3600.0
    fits.HDUList(
        [
            fits.PrimaryHDU(),
            fits.ImageHDU(science, header=header, name="IMAGE"),
            fits.ImageHDU(variance, header=header, name="VARIANCE"),
            fits.ImageHDU(mask, header=header, name="MASK"),
        ]
    ).writeto(path, overwrite=True, checksum=True)


def write_preview(path: Path, data: np.ndarray) -> None:
    finite = np.isfinite(data)
    if not finite.any():
        raise ValueError(f"Cannot preview empty mosaic {path}")
    low, high = PercentileInterval(99.7).get_limits(data[finite])
    if not np.isfinite(high - low) or high <= low:
        raise ValueError(f"Cannot preview flat mosaic {path}")
    scaled = np.clip((data - low) / (high - low), 0.0, 1.0)
    norm = AsinhStretch(0.05)(scaled, clip=True)
    pixels = np.nan_to_num(norm, nan=0.0)
    Image.fromarray(np.uint8(np.clip(pixels, 0, 1) * 255), mode="L").save(path, optimize=True)


def make_web_composite(arrays: dict[str, np.ndarray]) -> tuple[np.ndarray, str]:
    if all(band in arrays for band in ("g", "r", "i")):
        return make_lupton_rgb(arrays["i"], arrays["r"], arrays["g"], stretch=1.0, Q=8), "i/r/g"
    if all(band in arrays for band in ("r", "i", "z")):
        return make_lupton_rgb(arrays["z"], arrays["i"], arrays["r"], stretch=1.0, Q=8), "z/i/r"
    preferred = next((band for band in ("i", "z", "r", "g", "y", "u") if band in arrays), None)
    if preferred is None:
        raise ValueError("No band arrays available")
    data = arrays[preferred]
    finite = np.isfinite(data)
    low, high = PercentileInterval(99.7).get_limits(data[finite])
    if not np.isfinite(high - low) or high <= low:
        raise ValueError(f"Cannot compose flat {preferred}-band mosaic")
    scaled = np.clip((data - low) / (high - low), 0.0, 1.0)
    norm = AsinhStretch(0.05)(scaled, clip=True)
    gray = np.uint8(np.nan_to_num(norm, nan=0.0).clip(0, 1) * 255)
    return np.dstack([gray, gray, gray]), f"{preferred}-band grayscale"


def build_mosaics(targets: list[dict], download_records: list[dict], root: Path, pixel_scale: float) -> list[dict]:
    products = []
    for target in targets:
        print(f"[{target['sparc_id']}] mosaicking calibrated patches")
        target_dir = root / target["slug"]
        target_dir.mkdir(parents=True, exist_ok=True)
        target_wcs, shape = output_wcs(target, pixel_scale)
        target_records = [record for record in download_records if record["target_slug"] == target["slug"]]
        arrays = {}
        band_products = {}
        for band in sorted({record["band"] for record in target_records}):
            paths = [Path(record["path"]) for record in target_records if record["band"] == band]
            science, variance, mask, good = mosaic_band(paths, target_wcs, shape)
            mosaic_path = target_dir / f"rubin_{band}.fits"
            preview_path = target_dir / f"rubin_{band}.png"
            write_mosaic(mosaic_path, science, variance, mask, target_wcs, target, band)
            usable = bool(good.any())
            if usable:
                write_preview(preview_path, science)
                arrays[band] = science
            else:
                preview_path.unlink(missing_ok=True)
            band_products[band] = {
                "patches": len(paths),
                "science_coverage": usable,
                "valid_pixel_fraction": float(good.mean()),
                "mosaic": mosaic_path.as_posix(),
                "mosaic_sha256": sha256(mosaic_path),
                "preview": preview_path.as_posix() if usable else None,
                "preview_sha256": sha256(preview_path) if usable else None,
            }
        rgb_path = target_dir / "rubin_rgb.png"
        if arrays:
            composite, mapping = make_web_composite(arrays)
            Image.fromarray(composite).save(rgb_path, optimize=True)
            rgb_sha256 = sha256(rgb_path)
        else:
            mapping = None
            rgb_path.unlink(missing_ok=True)
            rgb_sha256 = None
        provenance = {
            "schema_version": 1,
            "created_at": utc_now(),
            "release": "DP2",
            "dataset_type": "deep_coadd",
            "target": target,
            "pixel_scale_arcsec": pixel_scale,
            "shape": list(shape),
            "mask_rejection": ["NO_DATA", "SATURATED"],
            "science_coverage": bool(arrays),
            "coverage_note": (
                None
                if arrays
                else "SIA footprint intersects the requested field, but every intersecting Rubin pixel is masked NO_DATA."
            ),
            "web_composite_mapping": mapping,
            "web_composite": rgb_path.as_posix() if arrays else None,
            "web_composite_sha256": rgb_sha256,
            "bands": band_products,
            "input_dataset_ids": [record["publisher_id"] for record in target_records],
        }
        provenance_path = target_dir / "edp2_provenance.json"
        provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        products.append(provenance)
    return products


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=repo_root / ".env")
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path(__file__).with_name("results") / "dp2-sparc-coverage.json",
    )
    parser.add_argument(
        "--sia-cache",
        type=Path,
        default=Path(__file__).with_name("cache") / "rubin" / "sparc-175",
    )
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("output") / "dp2-sparc")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--pixel-scale", type=float, default=0.4)
    parser.add_argument(
        "--mosaic-only",
        action="store_true",
        help="Rebuild mosaics from the verified local download manifest without any network requests",
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        raise SystemExit("--workers must be between 1 and 8")
    if args.pixel_scale < 0.2:
        raise SystemExit("--pixel-scale below Rubin native sampling is not supported")

    only = {value.lower() for value in args.only}
    targets, datasets = selected_datasets(args.coverage, args.sia_cache, only)
    if not datasets:
        raise SystemExit("No DP2-overlapping datasets selected")
    manifest_path = args.output / "download-manifest.json"
    if args.mosaic_only:
        if not manifest_path.is_file():
            raise SystemExit(f"Missing local download manifest: {manifest_path}")
        download_records = json.loads(manifest_path.read_text(encoding="utf-8"))["records"]
        allowed = {target["slug"] for target in targets}
        download_records = [record for record in download_records if record["target_slug"] in allowed]
        missing = [record["path"] for record in download_records if not Path(record["path"]).is_file()]
        if missing:
            raise SystemExit(f"Local download manifest references {len(missing)} missing FITS file(s)")
        print(f"Reusing {len(download_records)} verified local FITS patches; API requests: 0")
    else:
        token = read_env_value(args.env, "RUBIN_RSP_TOKEN")
        print(f"Selected {len(datasets)} patch-band datasets for {len(targets)} SPARC galaxies")
        batch_cache = args.output / "datalink-response.xml"
        links = datalink_batch(datasets, token, batch_cache)
        datasets_with_links = [
            Dataset(**{**dataset.__dict__, "access_url": links[dataset.publisher_id][0], "expected_size": links[dataset.publisher_id][1]})
            for dataset in datasets
        ]
        download_records = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(download_one, dataset, args.output): dataset for dataset in datasets_with_links}
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                record = future.result()
                download_records.append(record)
                print(
                    f"[{index:02d}/{len(datasets_with_links):02d}] {record['sparc_id']:<12} "
                    f"{record['band']} {record['bytes'] / 1024 / 1024:6.1f} MiB {record['source']}"
                )
        download_records.sort(key=lambda item: (item["target_slug"], item["band"], item["publisher_id"]))
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "created_at": utc_now(),
                    "datalink_endpoint": DATALINK_ENDPOINT,
                    "api_requests": {"datalink": 1, "sia": 0, "cutout": 0},
                    "download_mode": "signed full-patch FITS",
                    "records": download_records,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    products = build_mosaics(targets, download_records, args.output, args.pixel_scale)
    (args.output / "mosaic-summary.json").write_text(json.dumps(products, indent=2), encoding="utf-8")
    print(f"Complete: {len(products)} real DP2/SPARC mosaic sets in {args.output}")


if __name__ == "__main__":
    main()
