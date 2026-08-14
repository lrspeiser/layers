#!/usr/bin/env python3
"""Ingest public GALEX GR6/GR7 image layers for Rubin-matched fields.

The adapter queries MAST by sky position, selects the longest public GALEX
observation that actually contains the target, retains calibrated intensity,
exposure, response, mask, WCS, source URIs, and checksums, and publishes only
compact display previews plus a machine-readable provenance record.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import numpy as np
import requests
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.nddata import Cutout2D
from astropy.wcs import WCS
from PIL import Image
from reproject import reproject_interp


MAST_INVOKE = "https://mast.stsci.edu/api/v0/invoke"
MAST_DOWNLOAD = "https://mast.stsci.edu/api/v0.1/Download/file?uri="
AB_ZERO_NJY_MAG = 31.4
ZERO_POINTS_AB = {"FUV": 18.82, "NUV": 20.08}
PRODUCT_CODES = {"FUV": "fd", "NUV": "nd"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mast_request(session: requests.Session, service: str, params: dict, cache: Path) -> dict:
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    payload = {"service": service, "params": params, "format": "json", "pagesize": 5000, "page": 1}
    response = session.post(MAST_INVOKE, data={"request": json.dumps(payload)}, timeout=120)
    response.raise_for_status()
    result = response.json()
    if result.get("status") != "COMPLETE":
        raise RuntimeError(f"MAST {service} failed: {result.get('msg')}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def download(session: requests.Session, uri: str, destination: Path) -> Path:
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    response = session.get(f"{MAST_DOWNLOAD}{quote(uri, safe=':/')}", timeout=180)
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)
    return destination


def read_gzip_fits(path: Path) -> tuple[np.ndarray, fits.Header]:
    with gzip.open(path, "rb") as handle:
        payload = handle.read()
    with fits.open(BytesIO(payload), memmap=False) as hdul:
        return np.asarray(hdul[0].data, dtype=np.float32), hdul[0].header.copy()


def crop(data: np.ndarray, header: fits.Header, ra: float, dec: float, width_arcmin: float) -> tuple[np.ndarray, WCS]:
    wcs = WCS(header)
    scale_arcsec = math.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))) * 3600.0
    pixels = max(16, int(round(width_arcmin * 60.0 / scale_arcsec)))
    cutout = Cutout2D(
        data,
        SkyCoord(ra, dec, unit="deg", frame="icrs"),
        (pixels, pixels),
        wcs=wcs,
        mode="partial",
        fill_value=np.nan,
    )
    return np.asarray(cutout.data, dtype=np.float32), cutout.wcs


def display_stretch(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    result = np.zeros(values.shape, dtype=np.float32)
    sample = values[valid]
    if sample.size == 0:
        return result
    low, high = np.nanpercentile(sample, [1.0, 99.7])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return result
    scaled = np.clip((values - low) / (high - low), 0, 1)
    result[valid] = np.arcsinh(8.0 * scaled[valid]) / np.arcsinh(8.0)
    return result


def save_preview(path: Path, band: str, image: np.ndarray, valid: np.ndarray) -> None:
    stretch = display_stretch(image, valid)
    if band == "FUV":
        rgb = np.dstack((0.18 * stretch, 0.42 * stretch, stretch))
    else:
        rgb = np.dstack((stretch, 0.43 * stretch, 0.78 * stretch))
    Image.fromarray(np.uint8(np.clip(rgb, 0, 1) * 255), mode="RGB").save(path, quality=91, optimize=True)


def save_composite(path: Path, bands: dict[str, dict]) -> None:
    fuv = display_stretch(bands["FUV"]["image"], bands["FUV"]["valid"]) if "FUV" in bands else None
    nuv = display_stretch(bands["NUV"]["image"], bands["NUV"]["valid"]) if "NUV" in bands else None
    if fuv is None:
        fuv = np.zeros_like(nuv)
    if nuv is None:
        nuv = np.zeros_like(fuv)
    rgb = np.dstack((nuv, 0.18 * nuv + 0.24 * fuv, 0.56 * nuv + fuv))
    Image.fromarray(np.uint8(np.clip(rgb, 0, 1) * 255), mode="RGB").save(path, quality=92, optimize=True)


def product_rows(rows: list[dict], band: str) -> dict[str, dict]:
    code = PRODUCT_CODES[band]
    suffixes = {"science": f"-{code}-int.fits.gz", "exposure": f"-{code}-exp.fits.gz", "response": f"-{code}-rrhr.fits.gz"}
    selected: dict[str, dict] = {}
    for role, suffix in suffixes.items():
        choices = [row for row in rows if row.get("productFilename", "").endswith(suffix) and "/01-main/" in row.get("dataURI", "")]
        if choices:
            selected[role] = sorted(choices, key=lambda item: item.get("productFilename", ""))[0]
    return selected


def observation_contains(row: dict, ra: float, dec: float) -> bool:
    """Preflight GALEX circular footprints so non-overlapping files are never downloaded."""
    tokens = str(row.get("s_region") or "").split()
    if len(tokens) >= 5 and tokens[0].upper() == "CIRCLE":
        center = SkyCoord(float(tokens[2]), float(tokens[3]), unit="deg", frame="icrs")
        return center.separation(SkyCoord(ra, dec, unit="deg", frame="icrs")).deg <= float(tokens[4])
    if row.get("s_ra") is None or row.get("s_dec") is None:
        return False
    center = SkyCoord(float(row["s_ra"]), float(row["s_dec"]), unit="deg", frame="icrs")
    return center.separation(SkyCoord(ra, dec, unit="deg", frame="icrs")).deg <= 0.6


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline/results/dp2-sparc-coverage.json")
    parser.add_argument("--cache", type=Path, default=root / "pipeline/cache/galex")
    parser.add_argument("--output", type=Path, default=root / "pipeline/output/galex")
    parser.add_argument("--public", type=Path, default=root / "public")
    args = parser.parse_args()

    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    targets = [item for item in coverage["targets"] if int(item.get("deep_coadd_rows", 0)) > 0]
    session = requests.Session()
    session.headers["User-Agent"] = "Layers science comparison prototype (contact via github.com/lrspeiser/rubin-light-atlas)"
    manifest = {
        "schemaVersion": 1,
        "adapterContract": "layers-image-layer-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": {"service": "MAST CAOM", "collection": "GALEX", "release": "GR6/GR7"},
        "targets": [],
    }

    preview_dir = args.public / "layer-previews/galex"
    record_dir = args.public / "data/layers/galex"
    preview_dir.mkdir(parents=True, exist_ok=True)
    record_dir.mkdir(parents=True, exist_ok=True)

    for target in targets:
        slug = target["slug"]
        ra, dec = float(target["ra_deg"]), float(target["dec_deg"])
        cone = mast_request(session, "Mast.Caom.Cone", {"ra": ra, "dec": dec, "radius": 0.75}, args.cache / slug / "cone.json")
        observations = [
            row for row in cone.get("data", [])
            if row.get("obs_collection") == "GALEX" and row.get("dataproduct_type") == "image" and row.get("dataRights") == "PUBLIC"
        ]
        candidates: dict[str, list[dict]] = {"FUV": [], "NUV": []}
        for row in observations:
            band = str(row.get("filters", "")).upper()
            if band in candidates:
                candidates[band].append(row)

        selected_bands: dict[str, dict] = {}
        band_records: dict[str, dict] = {}
        sources: dict[str, dict] = {}
        for band in ["FUV", "NUV"]:
            containing = [row for row in candidates[band] if observation_contains(row, ra, dec)]
            ranked = sorted(containing, key=lambda row: float(row.get("t_exptime") or 0), reverse=True)
            for observation in ranked:
                obsid = str(observation["obsid"])
                products = mast_request(session, "Mast.Caom.Products", {"obsid": obsid}, args.cache / slug / f"products-{obsid}.json")
                selected = product_rows(products.get("data", []), band)
                if set(selected) != {"science", "exposure", "response"}:
                    continue
                local: dict[str, Path] = {}
                for role, product in selected.items():
                    local[role] = download(session, product["dataURI"], args.cache / slug / product["productFilename"])
                science, science_header = read_gzip_fits(local["science"])
                exposure, exposure_header = read_gzip_fits(local["exposure"])
                response, response_header = read_gzip_fits(local["response"])
                try:
                    science_cut, cutout_wcs = crop(science, science_header, ra, dec, target["field_width_arcmin"])
                    exposure_cut, _ = reproject_interp(
                        (exposure, WCS(exposure_header)),
                        cutout_wcs,
                        shape_out=science_cut.shape,
                        order="bilinear",
                    )
                    response_cut, _ = reproject_interp(
                        (response, WCS(response_header)),
                        cutout_wcs,
                        shape_out=science_cut.shape,
                        order="bilinear",
                    )
                except Exception as error:
                    print(f"[{slug}] skipped GALEX {band} observation {obsid}: {type(error).__name__}", flush=True)
                    continue
                valid = np.isfinite(science_cut) & np.isfinite(exposure_cut) & (exposure_cut > 0) & np.isfinite(response_cut) & (response_cut > 0)
                if not valid.any():
                    continue
                nJy_per_cps = 10 ** ((AB_ZERO_NJY_MAG - ZERO_POINTS_AB[band]) / 2.5)
                image_njy = science_cut * np.float32(nJy_per_cps)
                mask = np.where(valid, 0, 1).astype(np.uint8)
                output_dir = args.output / slug
                output_dir.mkdir(parents=True, exist_ok=True)
                fits_path = output_dir / f"galex_{band.lower()}.fits"
                header = cutout_wcs.to_header()
                header["BUNIT"] = "nJy"
                header["SURVEY"] = "GALEX"
                header["RELEASE"] = "GR6/GR7"
                header["FILTER"] = band
                header["ABMAGZP"] = ZERO_POINTS_AB[band]
                header["CALNOTE"] = "GALEX CPS converted to AB nJy"
                hdul = fits.HDUList([
                    fits.PrimaryHDU(image_njy.astype(np.float32), header=header),
                    fits.ImageHDU(exposure_cut.astype(np.float32), name="EXPOSURE"),
                    fits.ImageHDU(response_cut.astype(np.float32), name="RESPONSE"),
                    fits.ImageHDU(mask, name="MASK"),
                ])
                hdul.writeto(fits_path, overwrite=True, checksum=True)
                preview_path = preview_dir / f"{slug}-{band.lower()}.jpg"
                save_preview(preview_path, band, image_njy, valid)
                selected_bands[band] = {"image": image_njy, "valid": valid}
                sources[band] = {
                    "obsid": obsid,
                    "obsId": observation.get("obs_id"),
                    "targetName": observation.get("target_name"),
                    "exposureSeconds": observation.get("t_exptime"),
                    "products": {
                        role: {"uri": product["dataURI"], "filename": product["productFilename"], "sha256": sha256(local[role])}
                        for role, product in selected.items()
                    },
                }
                band_records[band] = {
                    "validPixelFraction": float(valid.mean()),
                    "nJyPerCountPerSecond": nJy_per_cps,
                    "standardProductSha256": sha256(fits_path),
                    "previewSha256": sha256(preview_path),
                }
                break

        if not selected_bands:
            continue
        composite_path = preview_dir / f"{slug}-uv.jpg"
        save_composite(composite_path, selected_bands)
        public_record = {
            "schemaVersion": 1,
            "product": "Layers external image-layer record",
            "targetId": slug,
            "survey": "GALEX",
            "release": "GR6/GR7",
            "center": {"raDeg": ra, "decDeg": dec, "frame": "ICRS"},
            "region": {"widthArcmin": target["field_width_arcmin"]},
            "bands": band_records,
            "sources": sources,
            "display": {"preview": f"/layer-previews/galex/{composite_path.name}", "stretch": "per-band asinh; color encodes wavelength only"},
            "scienceGate": {"status": "evidence-only", "reason": "GALEX and Rubin filters, PSFs, units, and epochs have not been reconciled for pixel subtraction."},
        }
        record_path = record_dir / f"{slug}.json"
        record_path.write_text(json.dumps(public_record, indent=2), encoding="utf-8")
        layer = {
            "id": "galex-gr6-7",
            "survey": "GALEX",
            "release": "GR6/GR7",
            "instrument": "GALEX FUV/NUV imagers",
            "kind": "image",
            "availability": "published",
            "renderMode": "image",
            "bands": list(selected_bands),
            "bandCoverage": {band: data["validPixelFraction"] for band, data in band_records.items()},
            "datasetCount": sum(len(item["products"]) for item in sources.values()),
            "datasetIds": [product["uri"] for item in sources.values() for product in item["products"].values()],
            "units": {"image": "nJy", "exposure": "seconds", "response": "relative response"},
            "calibration": "GALEX GR6/GR7 count-rate maps converted with mission AB zero points",
            "hasVariance": False,
            "hasMask": True,
            "hasWcs": True,
            "note": "Authentic public ultraviolet pixels. The color preview maps FUV to blue and NUV to magenta; it is not a Rubin difference image.",
            "scienceRole": "Recent star formation and UV-bright structures that can distinguish young populations from old stellar mass.",
            "provenance": {"service": "MAST CAOM", "collection": "GALEX", "documentation": "https://galex.stsci.edu/gr6/"},
            "assets": {
                "preview": f"/layer-previews/galex/{composite_path.name}",
                "bands": {band: f"/layer-previews/galex/{slug}-{band.lower()}.jpg" for band in selected_bands},
                "data": f"/data/layers/galex/{slug}.json",
            },
        }
        manifest["targets"].append({"targetId": slug, "target": public_record["center"], "layer": layer})
        print(f"[{slug}] GALEX {'+'.join(selected_bands)} published", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Complete: {len(manifest['targets'])}/{len(targets)} Rubin fields have public GALEX pixels", flush=True)


if __name__ == "__main__":
    main()
