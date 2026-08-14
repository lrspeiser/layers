#!/usr/bin/env python3
"""Fetch bounded GALEX, unWISE, 2MASS, and ZTF products for 50 Rubin regions.

The fetcher is deliberately cache-first and does not download whole archives.
It keeps archive-native FITS products (plus uncertainty/coverage products when
the archive supplies them), creates display-only previews, and publishes a
redacted manifest.  Cross-survey quantitative comparison is *never* asserted:
PSF, bandpass, background, astrometric, and injection/recovery QA remain gates.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import shutil
import tarfile
import tempfile
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import numpy as np
import requests
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.nddata import Cutout2D
from astropy.wcs import WCS
from PIL import Image
from reproject import reproject_interp


USER_AGENT = "Layers science atlas bounded acquisition/0.1 (github.com/lrspeiser/rubin-light-atlas)"
CUTOUT_ARCMIN = 4.0
UNWISE_PIXEL_SCALE_ARCSEC = 2.75
UNWISE_SIZE_PIXELS = 96
MAST_INVOKE = "https://mast.stsci.edu/api/v0/invoke"
MAST_DOWNLOAD = "https://mast.stsci.edu/api/v0.1/Download/file?uri="
IRSA_SIA = "https://irsa.ipac.caltech.edu/SIA"
ZTF_REF_SEARCH = "https://irsa.ipac.caltech.edu/ibe/search/ztf/products/ref"
ZTF_REF_DATA = "https://irsa.ipac.caltech.edu/ibe/data/ztf/products/ref"
ZTF_LIGHTCURVES = "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves"
DOCS = {
    "galex": [
        "https://archive.stsci.edu/missions-and-data/galex",
        "https://galex.stsci.edu/gr6/?page=ddfaq",
    ],
    "unwise": ["https://unwise.me/imgsearch/", "https://unwise.me/static/unwise-bw.pdf"],
    "2mass": [
        "https://irsa.ipac.caltech.edu/applications/2MASS/IM/",
        "https://irsa.ipac.caltech.edu/data/2MASS/docs/releases/allsky/faq.html",
    ],
    "ztf": [
        "https://irsa.ipac.caltech.edu/docs/program_interface/ztf_api.html",
        "https://irsa.ipac.caltech.edu/docs/program_interface/ztf_lightcurve_api.html",
        "https://irsa.ipac.caltech.edu/data/ZTF/docs/releases/dr24/ztf_release_notes_dr24.pdf",
    ],
}
COMMON_BLOCKERS = [
    "PSF matching is not complete",
    "bandpass/color-term transfer is not complete",
    "background and surface-brightness transfer are not complete",
    "cross-survey astrometric residual QA is not complete",
    "correlated-noise and mask propagation are not complete",
    "injection/recovery validation is not complete",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class Fetcher:
    def __init__(self, session: requests.Session, refresh: bool) -> None:
        self.session = session
        self.refresh = refresh

    def get(self, url: str, path: Path, *, params: dict[str, Any] | None = None, timeout: int = 240) -> tuple[Path, dict[str, Any]]:
        if path.is_file() and path.stat().st_size > 0 and not self.refresh:
            return path, {"status": "cached", "url": url, "bytes": path.stat().st_size, "sha256": sha256(path)}
        path.parent.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None
        for attempt in range(4):
            try:
                response = self.session.get(url, params=params, timeout=timeout, stream=True)
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as output:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            output.write(chunk)
                    temporary = Path(output.name)
                if temporary.stat().st_size == 0:
                    temporary.unlink(missing_ok=True)
                    raise RuntimeError("empty response")
                os.replace(temporary, path)
                return path, {
                    "status": "fetched",
                    "url": response.url,
                    "contentType": response.headers.get("content-type"),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            except Exception as error:
                last = error
                if attempt < 3:
                    time.sleep(2**attempt)
        raise RuntimeError(f"GET failed after 4 attempts: {url}: {last}")

    def post_json(self, service: str, params: dict[str, Any], path: Path) -> dict[str, Any]:
        if path.is_file() and path.stat().st_size > 0 and not self.refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        request = {"service": service, "params": params, "format": "json", "pagesize": 5000, "page": 1}
        last: Exception | None = None
        for attempt in range(4):
            try:
                response = self.session.post(MAST_INVOKE, data={"request": json.dumps(request)}, timeout=240)
                response.raise_for_status()
                result = response.json()
                if result.get("status") != "COMPLETE":
                    raise RuntimeError(result.get("msg") or "MAST request incomplete")
                write_json(path, result)
                return result
            except Exception as error:
                last = error
                if attempt < 3:
                    time.sleep(2**attempt)
        raise RuntimeError(f"MAST {service} failed after 4 attempts: {last}")


def load_regions(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    regions = payload["regions"]
    if not regions:
        raise ValueError("Expected at least one Rubin region")
    return [
        {
            "regionId": item["id"],
            "tract": int(item["tract"]),
            "raDeg": float(item["center"][0]),
            "decDeg": float(item["center"][1]),
            "confirmedSurveyIds": item.get("confirmedSurveyIds", []),
        }
        for item in regions
    ]


def votable_rows(path: Path) -> list[dict[str, str]]:
    root = ET.fromstring(path.read_bytes())
    table = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "TABLE"), None)
    if table is None:
        return []
    fields: list[str] = []
    for node in table.iter():
        local = node.tag.rsplit("}", 1)[-1]
        if local == "FIELD":
            fields.append(node.attrib.get("name") or node.attrib.get("ID") or f"col_{len(fields)}")
        elif local == "DATA":
            break
    rows = []
    for tr in table.iter():
        if tr.tag.rsplit("}", 1)[-1] != "TR":
            continue
        values = [(td.text or "") for td in tr if td.tag.rsplit("}", 1)[-1] == "TD"]
        rows.append(dict(zip(fields, values, strict=False)))
    return rows


def wcs_ok(header: fits.Header) -> bool:
    try:
        return bool(WCS(header).has_celestial)
    except Exception:
        return False


def stretch(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    out = np.zeros(values.shape, dtype=np.float32)
    sample = values[valid & np.isfinite(values)]
    if sample.size < 10:
        return out
    low, high = np.nanpercentile(sample, [1.0, 99.7])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return out
    scaled = np.clip((values - low) / (high - low), 0, 1)
    out[valid] = np.arcsinh(8 * scaled[valid]) / np.arcsinh(8)
    return out


def save_preview(path: Path, image: np.ndarray, valid: np.ndarray, tint: tuple[float, float, float]) -> None:
    lum = stretch(image, valid)
    rgb = np.dstack(tuple(channel * lum for channel in tint))
    yy, xx = np.indices(valid.shape)
    checker = (xx // 8 + yy // 8) % 2 == 0
    rgb[~valid] = np.where(checker[~valid, None], [0.15, 0.11, 0.09], [0.04, 0.05, 0.06])
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.uint8(np.clip(rgb, 0, 1) * 255), mode="RGB").save(path, quality=92, optimize=True)


def public_path(root: Path, path: Path) -> str:
    return "/" + path.resolve().relative_to((root / "public").resolve()).as_posix()


def normalized_product(
    region: dict[str, Any], survey_id: str, survey_name: str, family: str, release: str,
    product_type: str, status: str, band: str, unit: str | None, checksum: str | None,
    provenance_urls: list[str], preview_path: str | None = None, science_ready: bool = False,
    display_ready: bool = False, blockers: list[str] | None = None,
) -> dict[str, Any]:
    record = {
        "regionId": region["regionId"], "tract": region["tract"], "surveyId": survey_id,
        "surveyName": survey_name, "family": family, "release": release,
        "productType": product_type, "status": status, "scienceReady": science_ready,
        "displayReady": display_ready, "comparisonReady": False, "bandOrObservable": band,
        "unit": unit, "provenanceUrls": provenance_urls, "checksum": checksum,
        "blockers": blockers if blockers is not None else list(COMMON_BLOCKERS),
    }
    if preview_path:
        record["previewPath"] = preview_path
    return record


def extract_unwise(root: Path, fetcher: Fetcher, region: dict[str, Any], cache: Path, products: Path, previews: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    params = {
        "version": "allwise", "ra": f"{region['raDeg']:.8f}", "dec": f"{region['decDeg']:.8f}",
        "size": str(UNWISE_SIZE_PIXELS), "bands": "12",
    }
    query_url = "https://unwise.me/cutout_fits?" + urllib.parse.urlencode(params)
    archive, transfer = fetcher.get(query_url, cache / region["regionId"] / "unwise-allwise.tar.gz")
    destination = products / region["regionId"] / "unwise"
    destination.mkdir(parents=True, exist_ok=True)
    members: dict[str, Path] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            if not name or not member.isfile() or not name.endswith((".fits", ".fits.gz")):
                continue
            output = destination / name
            if not output.is_file() or fetcher.refresh:
                source = tar.extractfile(member)
                if source is None:
                    continue
                output.write_bytes(source.read())
            members[name] = output
    bands: dict[str, Any] = {}
    normalized = []
    for band in ("w1", "w2"):
        image_path = next((path for name, path in members.items() if f"-{band}-img-m.fits" in name), None)
        invvar_path = next((path for name, path in members.items() if f"-{band}-invvar-m.fits" in name), None)
        coverage_path = next((path for name, path in members.items() if f"-{band}-n-m.fits" in name), None)
        std_path = next((path for name, path in members.items() if f"-{band}-std-m.fits" in name), None)
        if not all((image_path, invvar_path, coverage_path, std_path)):
            raise RuntimeError(f"{region['regionId']} unWISE {band} archive is incomplete")
        with fits.open(image_path, memmap=False) as hdus:
            image = np.asarray(hdus[0].data, dtype=np.float32)
            header = hdus[0].header.copy()
        with fits.open(invvar_path, memmap=False) as hdus:
            invvar = np.asarray(hdus[0].data, dtype=np.float32)
        with fits.open(coverage_path, memmap=False) as hdus:
            coverage = np.asarray(hdus[0].data, dtype=np.float32)
        valid = np.isfinite(image) & np.isfinite(invvar) & (invvar > 0) & np.isfinite(coverage) & (coverage > 0)
        if image.shape != invvar.shape or image.shape != coverage.shape or not valid.any() or not wcs_ok(header):
            raise RuntimeError(f"{region['regionId']} unWISE {band} failed structural validation")
        preview = previews / region["regionId"] / f"unwise-{band}.jpg"
        save_preview(preview, image, valid, (1.0, 0.44, 0.12) if band == "w1" else (0.84, 0.20, 1.0))
        band_record = {
            "band": band.upper(), "unit": "Vega nanomaggies/pixel", "magZeroPointVega": float(header.get("MAGZP", 22.5)),
            "shape": list(image.shape), "validPixelFraction": float(valid.mean()), "wcsPresent": True,
            "assets": {
                "image": {"filename": image_path.name, "bytes": image_path.stat().st_size, "sha256": sha256(image_path)},
                "inverseVariance": {"filename": invvar_path.name, "bytes": invvar_path.stat().st_size, "sha256": sha256(invvar_path)},
                "coverage": {"filename": coverage_path.name, "bytes": coverage_path.stat().st_size, "sha256": sha256(coverage_path)},
                "sampleStdDev": {"filename": std_path.name, "bytes": std_path.stat().st_size, "sha256": sha256(std_path)},
                "preview": {"path": public_path(root, preview), "sha256": sha256(preview)},
            },
        }
        bands[band.upper()] = band_record
        normalized.append(normalized_product(
            region, "unwise", "unWISE", "uv-ir", "AllWISE unblurred coadds", "image", "available",
            band.upper(), band_record["unit"], band_record["assets"]["image"]["sha256"], [query_url, *DOCS["unwise"]],
            band_record["assets"]["preview"]["path"], True, True,
        ))
    return {
        "surveyId": "unwise", "status": "available", "query": query_url, "transfer": transfer,
        "archiveSha256": sha256(archive), "bands": bands,
    }, normalized


def acquire_2mass(root: Path, fetcher: Fetcher, region: dict[str, Any], cache: Path, products: Path, previews: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    params = {"COLLECTION": "twomass_allsky", "POS": f"CIRCLE {region['raDeg']:.8f} {region['decDeg']:.8f} 0.001", "MAXREC": "50"}
    query_url = IRSA_SIA + "?" + urllib.parse.urlencode(params)
    metadata_path, metadata_transfer = fetcher.get(IRSA_SIA, cache / region["regionId"] / "2mass-sia.xml", params=params)
    rows = [row for row in votable_rows(metadata_path) if row.get("dataproduct_subtype") == "science"]
    normalized = []
    bands: dict[str, Any] = {}
    for band in ("J", "H", "K"):
        candidates = [row for row in rows if row.get("energy_bandpassname") == band and "/ibe/data/" in row.get("access_url", "")]
        if not candidates:
            continue
        source = candidates[0]
        cutout_url = source["access_url"] + "?" + urllib.parse.urlencode({
            "center": f"{region['raDeg']:.8f},{region['decDeg']:.8f}", "size": f"{CUTOUT_ARCMIN:g}arcmin", "gzip": "false"
        }, safe=",")
        output, transfer = fetcher.get(cutout_url, products / region["regionId"] / "2mass" / f"2mass-{band.lower()}-atlas.fits")
        with fits.open(output, memmap=False) as hdus:
            image = np.asarray(hdus[0].data, dtype=np.float32)
            header = hdus[0].header.copy()
        valid = np.isfinite(image)
        if image.ndim != 2 or not valid.any() or not wcs_ok(header) or header.get("MAGZP") is None:
            raise RuntimeError(f"{region['regionId']} 2MASS {band} failed Atlas validation")
        preview = previews / region["regionId"] / f"2mass-{band.lower()}.jpg"
        tint = {"J": (0.32, 0.56, 1.0), "H": (0.22, 1.0, 0.44), "K": (1.0, 0.36, 0.16)}[band]
        save_preview(preview, image, valid, tint)
        record = {
            "band": "Ks" if band == "K" else band, "unit": "DN/pixel", "magZeroPoint": float(header["MAGZP"]),
            "skySigmaDn": float(header["SKYSIG"]) if header.get("SKYSIG") is not None else None,
            "shape": list(image.shape), "validPixelFraction": float(valid.mean()), "wcsPresent": True,
            "obsId": source.get("obs_id"), "sourceUrl": source["access_url"], "cutoutUrl": cutout_url,
            "assets": {
                "image": {"filename": output.name, "bytes": output.stat().st_size, "sha256": sha256(output)},
                "preview": {"path": public_path(root, preview), "sha256": sha256(preview)},
            }, "transfer": transfer,
        }
        bands[record["band"]] = record
        normalized.append(normalized_product(
            region, "2mass", "2MASS", "uv-ir", "All-Sky Atlas", "image", "available", record["band"],
            record["unit"], record["assets"]["image"]["sha256"], [source["access_url"], cutout_url, *DOCS["2mass"]],
            record["assets"]["preview"]["path"], True, True,
            ["No per-pixel uncertainty or artifact mask is supplied by this Atlas cutout", *COMMON_BLOCKERS],
        ))
    status = "available" if bands else "none"
    return {"surveyId": "2mass", "status": status, "query": query_url, "metadataTransfer": metadata_transfer, "recordCount": len(rows), "bands": bands}, normalized


def ztf_reference_url(row: dict[str, str], suffix: str) -> str:
    field = int(row["field"])
    ccd = int(row["ccdid"])
    qid = int(row["qid"])
    filt = row["filtercode"]
    field6 = f"{field:06d}"
    return f"{ZTF_REF_DATA}/{field6[:3]}/field{field6}/{filt}/ccd{ccd:02d}/q{qid}/ztf_{field6}_{filt}_c{ccd:02d}_q{qid}_{suffix}.fits"


def acquire_ztf(
    root: Path, fetcher: Fetcher, region: dict[str, Any], cache: Path, products: Path,
    previews: Path, acquire_ancillary: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata_params = {"POS": f"{region['raDeg']:.8f},{region['decDeg']:.8f}", "mcen": "", "ct": "csv"}
    metadata_path, metadata_transfer = fetcher.get(ZTF_REF_SEARCH, cache / region["regionId"] / "ztf-reference-metadata.csv", params=metadata_params)
    metadata_rows = list(csv.DictReader(metadata_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()))
    preference = {"zr": 0, "zg": 1, "zi": 2}
    metadata_rows.sort(key=lambda row: preference.get(row.get("filtercode", ""), 9))
    normalized = []
    reference: dict[str, Any] | None = None
    if metadata_rows:
        selected = metadata_rows[0]
        source_paths: dict[str, Path] = {}
        transfers: dict[str, Any] = {}
        source_urls: dict[str, str] = {}
        roles = [("image", "refimg")]
        if acquire_ancillary:
            roles += [("uncertainty", "refunc"), ("coverage", "refcov")]
        for role, suffix in roles:
            source_url = ztf_reference_url(selected, suffix)
            cutout_url = source_url + "?" + urllib.parse.urlencode({
                "center": f"{region['raDeg']:.8f},{region['decDeg']:.8f}", "size": f"{CUTOUT_ARCMIN:g}arcmin", "gzip": "false"
            }, safe=",")
            source_path = cache / region["regionId"] / "ztf-source" / f"ztf-{selected['filtercode']}-{suffix}.fits"
            # Migrate artifacts created by the pre-v1 development runner so a
            # resumable run does not re-download a full ancillary frame.
            legacy_path = products / region["regionId"] / "ztf" / source_path.name
            if legacy_path.is_file() and not source_path.is_file():
                source_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_path, source_path)
            source_path, transfer = fetcher.get(cutout_url, source_path)
            source_paths[role], transfers[role], source_urls[role] = source_path, source_url, source_url
            transfers[role] = transfer
        arrays = {}
        headers = {}
        for role, path in source_paths.items():
            with fits.open(path, memmap=False) as hdus:
                arrays[role] = np.asarray(hdus[0].data, dtype=np.float32)
                headers[role] = hdus[0].header.copy()
        if arrays["image"].ndim != 2 or not np.isfinite(arrays["image"]).any() or not wcs_ok(headers["image"]):
            raise RuntimeError(f"{region['regionId']} ZTF reference product failed structural validation")
        # The IBE service currently returns bounded refimg cutouts but may
        # return full-quadrant refunc/refcov products. Reproject ancillary data
        # locally onto the exact reference cutout grid and retain source hashes.
        for role, order in (("uncertainty", "bilinear"), ("coverage", "nearest-neighbor")):
            if role in arrays and arrays[role].shape != arrays["image"].shape:
                arrays[role], _ = reproject_interp(
                    (arrays[role], WCS(headers[role])), WCS(headers["image"]),
                    shape_out=arrays["image"].shape, order=order,
                )
        valid = np.isfinite(arrays["image"])
        if "uncertainty" in arrays:
            valid &= np.isfinite(arrays["uncertainty"]) & (arrays["uncertainty"] > 0)
        if "coverage" in arrays:
            valid &= np.isfinite(arrays["coverage"]) & (arrays["coverage"] > 0)
        if not valid.any():
            raise RuntimeError(f"{region['regionId']} ZTF reference has no usable pixels")
        output_dir = products / region["regionId"] / "ztf"
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}
        output_roles = [("image", "refimg")]
        if "uncertainty" in arrays:
            output_roles.append(("uncertainty", "refunc"))
        if "coverage" in arrays:
            output_roles.append(("coverage", "refcov"))
        for role, suffix in output_roles:
            path = output_dir / f"ztf-{selected['filtercode']}-{suffix}.fits"
            fits.PrimaryHDU(np.asarray(arrays[role], dtype=np.float32), header=headers["image"]).writeto(path, overwrite=True, checksum=True)
            paths[role] = path
        preview = previews / region["regionId"] / f"ztf-{selected['filtercode']}-reference.jpg"
        save_preview(preview, arrays["image"], valid, (0.94, 0.76, 0.26))
        unit = str(headers["image"].get("BUNIT") or "archive reference-image DN")
        reference = {
            "band": selected["filtercode"], "unit": unit, "shape": list(arrays["image"].shape),
            "validPixelFraction": float(valid.mean()), "wcsPresent": True,
            "metadata": {key: selected.get(key) for key in ("field", "ccdid", "qid", "rfid", "nframes", "maglimit", "startobsdate", "endobsdate")},
            "hasUncertainty": "uncertainty" in arrays, "hasCoverage": "coverage" in arrays,
            "sourceUrls": source_urls,
            "assets": {
                role: {
                    "filename": path.name, "bytes": path.stat().st_size, "sha256": sha256(path),
                    "sourceSha256": sha256(source_paths[role]), "sourceBytes": source_paths[role].stat().st_size,
                } for role, path in paths.items()
            } | {"preview": {"path": public_path(root, preview), "sha256": sha256(preview)}},
            "transfers": transfers,
        }
        normalized.append(normalized_product(
            region, "ztf-dr", "Zwicky Transient Facility", "time-domain", "Public archive reference products",
            "reference-image", "available", selected["filtercode"], unit, reference["assets"]["image"]["sha256"],
            [*source_urls.values(), *DOCS["ztf"]], reference["assets"]["preview"]["path"], True, True,
            [
                "Reference coadd is a static image; variability claims require the separate epoch table",
                *( [] if acquire_ancillary else ["Per-pixel uncertainty and coverage were not cached for this bounded demonstration"] ),
                *COMMON_BLOCKERS,
            ],
        ))
    lc_params = {
        "POS": f"CIRCLE {region['raDeg']:.8f} {region['decDeg']:.8f} 0.003000",
        "NOBS_MIN": "3", "BAD_CATFLAGS_MASK": "32768", "FORMAT": "csv",
    }
    lightcurve_error = None
    lightcurve_transfer = None
    lightcurve_rows: list[dict[str, str]] = []
    lightcurve_path = cache / region["regionId"] / "ztf-lightcurves.csv"
    try:
        lightcurve_path, lightcurve_transfer = fetcher.get(ZTF_LIGHTCURVES, lightcurve_path, params=lc_params)
        text = lightcurve_path.read_text(encoding="utf-8-sig", errors="replace")
        if "<html" in text[:500].lower():
            raise RuntimeError("ZTF light-curve service returned HTML instead of CSV")
        lightcurve_rows = list(csv.DictReader(text.splitlines()))
    except Exception as error:
        lightcurve_error = f"{type(error).__name__}: {error}"
    object_ids = sorted({row.get("oid") for row in lightcurve_rows if row.get("oid")})
    mjds = [float(row["mjd"]) for row in lightcurve_rows if row.get("mjd")]
    time_series_status = "available" if lightcurve_rows else ("error" if lightcurve_error else "none")
    time_series = {
        "status": time_series_status, "query": urllib.parse.urlencode(lc_params), "measurementCount": len(lightcurve_rows),
        "objectCount": len(object_ids), "filters": dict(sorted(Counter(row.get("filtercode", "unknown") for row in lightcurve_rows).items())),
        "mjdRange": [min(mjds), max(mjds)] if mjds else None, "artifact": ({"filename": lightcurve_path.name, "bytes": lightcurve_path.stat().st_size, "sha256": sha256(lightcurve_path)} if lightcurve_path.is_file() else None),
        "transfer": lightcurve_transfer, "error": lightcurve_error,
        "releaseProvenanceVerified": False,
        "note": "The endpoint response does not stamp a release number; these rows are current public-archive evidence, not asserted specifically as DR24.",
    }
    if lightcurve_rows:
        normalized.append(normalized_product(
            region, "ztf-dr", "Zwicky Transient Facility", "time-domain", "Public light-curve service (release unstamped)",
            "time-series-catalog", "available", "multi-epoch photometry", "mag", sha256(lightcurve_path),
            [ZTF_LIGHTCURVES + "?" + urllib.parse.urlencode(lc_params), *DOCS["ztf"]], None, True, False,
            ["This catalog is not an image and must not be rendered as one", "Release provenance is not stamped in the response", "Apply complete ZTF epoch-quality filtering before inference"],
        ))
    status = "available" if reference else "none"
    return {
        "surveyId": "ztf-dr", "status": status, "referenceRecordCount": len(metadata_rows), "metadataTransfer": metadata_transfer,
        "reference": reference, "timeSeries": time_series,
    }, normalized


def galex_contains(row: dict[str, Any], region: dict[str, Any]) -> bool:
    tokens = str(row.get("s_region") or "").split()
    if len(tokens) >= 5 and tokens[0].upper() == "CIRCLE":
        center = SkyCoord(float(tokens[2]), float(tokens[3]), unit="deg", frame="icrs")
        target = SkyCoord(region["raDeg"], region["decDeg"], unit="deg", frame="icrs")
        return center.separation(target).deg <= float(tokens[4])
    return True


def galex_product_rows(rows: list[dict[str, Any]], band: str) -> dict[str, dict[str, Any]]:
    code = {"FUV": "fd", "NUV": "nd"}[band]
    suffixes = {"science": f"-{code}-int.fits.gz", "exposure": f"-{code}-exp.fits.gz", "response": f"-{code}-rrhr.fits.gz"}
    result = {}
    for role, suffix in suffixes.items():
        choices = [row for row in rows if str(row.get("productFilename", "")).endswith(suffix) and "/01-main/" in str(row.get("dataURI", ""))]
        if choices:
            result[role] = sorted(choices, key=lambda row: row["productFilename"])[0]
    return result


def read_gzip_fits(path: Path) -> tuple[np.ndarray, fits.Header]:
    with gzip.open(path, "rb") as handle:
        payload = handle.read()
    with fits.open(io.BytesIO(payload), memmap=False) as hdus:
        return np.asarray(hdus[0].data, dtype=np.float32), hdus[0].header.copy()


def galex_crop(data: np.ndarray, header: fits.Header, region: dict[str, Any]) -> tuple[np.ndarray, WCS]:
    wcs = WCS(header)
    scale = math.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))) * 3600
    pixels = max(16, int(round(CUTOUT_ARCMIN * 60 / scale)))
    cutout = Cutout2D(data, SkyCoord(region["raDeg"], region["decDeg"], unit="deg"), (pixels, pixels), wcs=wcs, mode="partial", fill_value=np.nan)
    return np.asarray(cutout.data, dtype=np.float32), cutout.wcs


def acquire_galex(root: Path, fetcher: Fetcher, region: dict[str, Any], cache: Path, products: Path, previews: Path, acquire_pixels: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cone = fetcher.post_json("Mast.Caom.Cone", {"ra": region["raDeg"], "dec": region["decDeg"], "radius": 0.05}, cache / region["regionId"] / "galex-cone.json")
    observations = [
        row for row in cone.get("data", []) if row.get("obs_collection") == "GALEX" and row.get("dataproduct_type") == "image"
        and row.get("dataRights") == "PUBLIC" and galex_contains(row, region)
    ]
    normalized = []
    bands: dict[str, Any] = {}
    for band in ("FUV", "NUV"):
        candidates = sorted([row for row in observations if str(row.get("filters", "")).upper() == band], key=lambda row: float(row.get("t_exptime") or 0), reverse=True)
        band_record: dict[str, Any] = {"status": "none", "candidateObservationCount": len(candidates)}
        for observation in candidates:
            obsid = str(observation["obsid"])
            product_result = fetcher.post_json("Mast.Caom.Products", {"obsid": obsid}, cache / "galex-products" / f"{obsid}.json")
            selected = galex_product_rows(product_result.get("data", []), band)
            if set(selected) != {"science", "exposure", "response"}:
                continue
            band_record = {
                "status": "available-not-cached", "mastProductId": obsid, "obsId": observation.get("obs_id"),
                "exposureSeconds": observation.get("t_exptime"), "productUris": {role: value["dataURI"] for role, value in selected.items()},
            }
            if not acquire_pixels:
                break
            source_paths = {}
            source_assets = {}
            for role, product in selected.items():
                uri = product["dataURI"]
                url = MAST_DOWNLOAD + urllib.parse.quote(uri, safe=":/")
                path, transfer = fetcher.get(url, cache / "galex-source" / obsid / product["productFilename"], timeout=480)
                source_paths[role] = path
                source_assets[role] = {"uri": uri, "filename": path.name, "bytes": path.stat().st_size, "sha256": sha256(path), "transfer": transfer["status"]}
            science, science_header = read_gzip_fits(source_paths["science"])
            exposure, exposure_header = read_gzip_fits(source_paths["exposure"])
            response, response_header = read_gzip_fits(source_paths["response"])
            science_cut, cutout_wcs = galex_crop(science, science_header, region)
            exposure_cut, _ = reproject_interp((exposure, WCS(exposure_header)), cutout_wcs, shape_out=science_cut.shape, order="bilinear")
            response_cut, _ = reproject_interp((response, WCS(response_header)), cutout_wcs, shape_out=science_cut.shape, order="bilinear")
            valid = np.isfinite(science_cut) & np.isfinite(exposure_cut) & (exposure_cut > 0) & np.isfinite(response_cut) & (response_cut > 0)
            if not valid.any():
                continue
            zero_point = {"FUV": 18.82, "NUV": 20.08}[band]
            njy_per_cps = 10 ** ((31.4 - zero_point) / 2.5)
            image_njy = science_cut * np.float32(njy_per_cps)
            output = products / region["regionId"] / "galex" / f"galex-{band.lower()}.fits"
            output.parent.mkdir(parents=True, exist_ok=True)
            header = cutout_wcs.to_header()
            header["BUNIT"] = "nJy"
            header["SURVEY"] = "GALEX"
            header["RELEASE"] = "GR6/GR7"
            header["FILTER"] = band
            header["ABMAGZP"] = zero_point
            fits.HDUList([
                fits.PrimaryHDU(), fits.ImageHDU(image_njy.astype(np.float32), header=header, name="IMAGE"),
                fits.ImageHDU(np.asarray(exposure_cut, dtype=np.float32), name="EXPOSURE"),
                fits.ImageHDU(np.asarray(response_cut, dtype=np.float32), name="RESPONSE"),
                fits.ImageHDU(valid.astype(np.uint8), name="VALID_MASK"),
            ]).writeto(output, overwrite=True, checksum=True)
            preview = previews / region["regionId"] / f"galex-{band.lower()}.jpg"
            save_preview(preview, image_njy, valid, (0.15, 0.35, 1.0) if band == "FUV" else (0.76, 0.18, 1.0))
            band_record |= {
                "status": "available", "unit": "nJy", "shape": list(image_njy.shape), "validPixelFraction": float(valid.mean()),
                "wcsPresent": True, "sourceAssets": source_assets,
                "standardProduct": {"filename": output.name, "bytes": output.stat().st_size, "sha256": sha256(output)},
                "preview": {"path": public_path(root, preview), "sha256": sha256(preview)},
            }
            normalized.append(normalized_product(
                region, "galex-gr6-7", "GALEX", "uv-ir", "GR6/GR7", "image", "available", band, "nJy",
                sha256(output), [*(MAST_DOWNLOAD + urllib.parse.quote(value["dataURI"], safe=":/") for value in selected.values()), *DOCS["galex"]],
                public_path(root, preview), True, True,
                ["GALEX photon-counting masks and low-response edge behavior need survey-specific QA", *COMMON_BLOCKERS],
            ))
            break
        bands[band] = band_record
    status = "available" if any(record["status"] in {"available", "available-not-cached"} for record in bands.values()) else "none"
    return {"surveyId": "galex-gr6-7", "status": status, "observationCount": len(observations), "pixelAcquisitionSelected": acquire_pixels, "bands": bands}, normalized


def build_unwise_rubin_alignments(
    root: Path, output_regions: list[dict[str, Any]], normalized: list[dict[str, Any]],
    products: Path, previews: Path, rubin_manifest_path: Path,
) -> int:
    """Create display-only common-grid products for each cached W1 cutout.

    The reference WCS is the native unWISE W1 cutout.  Rubin is resampled onto
    that grid only for navigation/display.  No PSF convolution, flux transfer,
    or difference statistic is performed, so comparisonReady remains false.
    """
    if not rubin_manifest_path.is_file():
        return 0
    rubin_manifest = json.loads(rubin_manifest_path.read_text(encoding="utf-8"))
    rubin_by_region = {record["regionId"]: record for record in rubin_manifest.get("regions", [])}
    normalized_by_region = {
        item["regionId"]: item for item in normalized
        if item["surveyId"] == "unwise" and item["bandOrObservable"] == "W1"
    }
    completed = 0
    for region in output_regions:
        survey = region.get("surveys", {}).get("unwise")
        rubin_record = rubin_by_region.get(region["regionId"])
        product_record = normalized_by_region.get(region["regionId"])
        if not survey or survey.get("status") != "available" or not rubin_record or not product_record:
            continue
        w1_record = survey["bands"]["W1"]
        w1_dir = products / region["regionId"] / "unwise"
        w1_image_path = w1_dir / w1_record["assets"]["image"]["filename"]
        w1_invvar_path = w1_dir / w1_record["assets"]["inverseVariance"]["filename"]
        w1_coverage_path = w1_dir / w1_record["assets"]["coverage"]["filename"]
        rubin_path = root / rubin_record["mosaic"]["path"]
        if not all(path.is_file() for path in (w1_image_path, w1_invvar_path, w1_coverage_path, rubin_path)):
            continue
        with fits.open(w1_image_path, memmap=False) as hdus:
            reference = np.asarray(hdus[0].data, dtype=np.float32)
            reference_header = hdus[0].header.copy()
        with fits.open(w1_invvar_path, memmap=False) as hdus:
            invvar = np.asarray(hdus[0].data, dtype=np.float32)
        with fits.open(w1_coverage_path, memmap=False) as hdus:
            exposure_count = np.asarray(hdus[0].data, dtype=np.float32)
        with fits.open(rubin_path, memmap=False) as hdus:
            rubin = np.asarray(hdus["IMAGE"].data, dtype=np.float32)
            rubin_header = hdus["IMAGE"].header.copy()
            rubin_mask = np.asarray(hdus["MASK"].data)
        reference_valid = np.isfinite(reference) & np.isfinite(invvar) & (invvar > 0) & np.isfinite(exposure_count) & (exposure_count > 0)
        # The Rubin mosaic mask is an integer bitmask; only zero-valued pixels
        # are unflagged for this generic display alignment.
        rubin_valid_native = np.isfinite(rubin) & (rubin_mask == 0)
        aligned_rubin, footprint = reproject_interp(
            (rubin, WCS(rubin_header)), WCS(reference_header), shape_out=reference.shape, order="bilinear"
        )
        aligned_valid_float, _ = reproject_interp(
            (rubin_valid_native.astype(np.float32), WCS(rubin_header)), WCS(reference_header),
            shape_out=reference.shape, order="nearest-neighbor",
        )
        rubin_valid = np.isfinite(aligned_rubin) & np.isfinite(footprint) & (footprint > 0) & (aligned_valid_float > 0.5)
        common = rubin_valid & reference_valid
        aligned_path = previews / region["regionId"] / "rubin-r-on-unwise-w1.jpg"
        save_preview(aligned_path, np.asarray(aligned_rubin, dtype=np.float32), rubin_valid, (0.92, 0.92, 0.92))
        coverage_rgb = np.zeros((*reference.shape, 3), dtype=np.float32)
        coverage_rgb[rubin_valid & ~reference_valid] = [0.95, 0.12, 0.08]
        coverage_rgb[reference_valid & ~rubin_valid] = [0.10, 0.35, 1.0]
        coverage_rgb[common] = [0.95, 0.95, 0.95]
        coverage_path = previews / region["regionId"] / "rubin-unwise-w1-common-coverage.png"
        coverage_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.uint8(np.clip(coverage_rgb, 0, 1) * 255), mode="RGB").save(coverage_path, optimize=True)
        rubin_display = stretch(np.asarray(aligned_rubin, dtype=np.float32), rubin_valid)
        reference_display = stretch(reference, reference_valid)
        overlay_rgb = np.dstack((
            np.clip(rubin_display + 0.10 * reference_display, 0, 1),
            np.clip(0.46 * rubin_display + 0.90 * reference_display, 0, 1),
            np.clip(0.04 * rubin_display + reference_display, 0, 1),
        ))
        overlay_rgb[~(rubin_valid | reference_valid)] = 0
        overlay_path = previews / region["regionId"] / "rubin-unwise-w1-position-overlay.jpg"
        Image.fromarray(np.uint8(np.clip(overlay_rgb, 0, 1) * 255), mode="RGB").save(overlay_path, quality=93, optimize=True)
        alignment = {
            "grid": "native unWISE W1 TAN cutout", "shape": list(reference.shape),
            "rubinBand": rubin_record.get("band", "r"), "referenceBand": "W1",
            "commonValidPixelFraction": float(common.mean()),
            "displayOnly": True, "comparisonReady": False,
            "method": "Rubin bilinear reprojection and nearest-neighbor validity reprojection; no PSF or photometric transfer",
            "alignedRubinPreviewPath": public_path(root, aligned_path),
            "coveragePreviewPath": public_path(root, coverage_path),
            "overlayPreviewPath": public_path(root, overlay_path),
        }
        w1_record["alignment"] = alignment
        product_record.update({
            "alignedRubinPreviewPath": alignment["alignedRubinPreviewPath"],
            "coveragePreviewPath": alignment["coveragePreviewPath"],
            "overlayPreviewPath": alignment["overlayPreviewPath"],
        })
        completed += 1
    return completed


def build_2mass_rubin_alignments(
    root: Path, output_regions: list[dict[str, Any]], normalized: list[dict[str, Any]],
    products: Path, previews: Path, rubin_manifest_path: Path,
) -> int:
    """Create display-only Rubin/2MASS Ks products on the native Atlas grid.

    2MASS Atlas cutouts provide calibrated image pixels and a magnitude zero
    point, but this service response does not provide a registered uncertainty
    or artifact-mask plane.  Finite pixels therefore define display support;
    the products remain explicitly unsuitable for quantitative subtraction.
    """
    if not rubin_manifest_path.is_file():
        return 0
    rubin_manifest = json.loads(rubin_manifest_path.read_text(encoding="utf-8"))
    rubin_by_region = {record["regionId"]: record for record in rubin_manifest.get("regions", [])}
    normalized_by_region = {
        item["regionId"]: item for item in normalized
        if item["surveyId"] == "2mass" and item["bandOrObservable"] == "Ks"
    }
    completed = 0
    for region in output_regions:
        survey = region.get("surveys", {}).get("2mass")
        rubin_record = rubin_by_region.get(region["regionId"])
        product_record = normalized_by_region.get(region["regionId"])
        if not survey or survey.get("status") != "available" or not rubin_record or not product_record:
            continue
        ks_record = survey.get("bands", {}).get("Ks")
        if not ks_record:
            continue
        ks_dir = products / region["regionId"] / "2mass"
        ks_image_path = ks_dir / ks_record["assets"]["image"]["filename"]
        rubin_path = root / rubin_record["mosaic"]["path"]
        if not all(path.is_file() for path in (ks_image_path, rubin_path)):
            continue
        with fits.open(ks_image_path, memmap=False) as hdus:
            reference = np.asarray(hdus[0].data, dtype=np.float32)
            reference_header = hdus[0].header.copy()
        with fits.open(rubin_path, memmap=False) as hdus:
            rubin = np.asarray(hdus["IMAGE"].data, dtype=np.float32)
            rubin_header = hdus["IMAGE"].header.copy()
            rubin_mask = np.asarray(hdus["MASK"].data)
        reference_valid = np.isfinite(reference)
        rubin_valid_native = np.isfinite(rubin) & (rubin_mask == 0)
        aligned_rubin, footprint = reproject_interp(
            (rubin, WCS(rubin_header)), WCS(reference_header), shape_out=reference.shape, order="bilinear"
        )
        aligned_valid_float, _ = reproject_interp(
            (rubin_valid_native.astype(np.float32), WCS(rubin_header)), WCS(reference_header),
            shape_out=reference.shape, order="nearest-neighbor",
        )
        rubin_valid = np.isfinite(aligned_rubin) & np.isfinite(footprint) & (footprint > 0) & (aligned_valid_float > 0.5)
        common = rubin_valid & reference_valid
        aligned_path = previews / region["regionId"] / "rubin-r-on-2mass-ks.jpg"
        save_preview(aligned_path, np.asarray(aligned_rubin, dtype=np.float32), rubin_valid, (0.92, 0.92, 0.92))
        coverage_rgb = np.zeros((*reference.shape, 3), dtype=np.float32)
        coverage_rgb[rubin_valid & ~reference_valid] = [0.95, 0.12, 0.08]
        coverage_rgb[reference_valid & ~rubin_valid] = [0.10, 0.35, 1.0]
        coverage_rgb[common] = [0.95, 0.95, 0.95]
        coverage_path = previews / region["regionId"] / "rubin-2mass-ks-common-coverage.png"
        coverage_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.uint8(np.clip(coverage_rgb, 0, 1) * 255), mode="RGB").save(coverage_path, optimize=True)
        rubin_display = stretch(np.asarray(aligned_rubin, dtype=np.float32), rubin_valid)
        reference_display = stretch(reference, reference_valid)
        overlay_rgb = np.dstack((
            np.clip(rubin_display + reference_display, 0, 1),
            np.clip(0.34 * rubin_display + 0.55 * reference_display, 0, 1),
            np.clip(0.06 * rubin_display + 0.12 * reference_display, 0, 1),
        ))
        overlay_rgb[~(rubin_valid | reference_valid)] = 0
        overlay_path = previews / region["regionId"] / "rubin-2mass-ks-position-overlay.jpg"
        Image.fromarray(np.uint8(np.clip(overlay_rgb, 0, 1) * 255), mode="RGB").save(overlay_path, quality=93, optimize=True)
        alignment = {
            "grid": "native 2MASS Ks Atlas cutout", "shape": list(reference.shape),
            "rubinBand": rubin_record.get("band", "r"), "referenceBand": "Ks",
            "commonValidPixelFraction": float(common.mean()),
            "displayOnly": True, "comparisonReady": False,
            "method": "Rubin bilinear reprojection and nearest-neighbor validity reprojection; 2MASS support is finite pixels; no PSF or photometric transfer",
            "alignedRubinPreviewPath": public_path(root, aligned_path),
            "coveragePreviewPath": public_path(root, coverage_path),
            "overlayPreviewPath": public_path(root, overlay_path),
        }
        ks_record["alignment"] = alignment
        product_record.update({
            "alignedRubinPreviewPath": alignment["alignedRubinPreviewPath"],
            "coveragePreviewPath": alignment["coveragePreviewPath"],
            "overlayPreviewPath": alignment["overlayPreviewPath"],
        })
        completed += 1
    return completed


def build_ztf_rubin_alignments(
    root: Path, output_regions: list[dict[str, Any]], normalized: list[dict[str, Any]],
    products: Path, previews: Path, rubin_manifest_path: Path,
) -> int:
    """Align Rubin to each native ZTF reference grid for display only."""
    if not rubin_manifest_path.is_file():
        return 0
    rubin_payload = json.loads(rubin_manifest_path.read_text(encoding="utf-8"))
    rubin_by_region = {record["regionId"]: record for record in rubin_payload.get("regions", [])}
    products_by_region = {
        item["regionId"]: item for item in normalized
        if item["surveyId"] == "ztf-dr" and item["productType"] == "reference-image"
    }
    completed = 0
    for region in output_regions:
        survey = region.get("surveys", {}).get("ztf-dr")
        reference_record = survey.get("reference") if survey else None
        rubin_record = rubin_by_region.get(region["regionId"])
        product_record = products_by_region.get(region["regionId"])
        if not reference_record or not rubin_record or not product_record:
            continue
        ztf_dir = products / region["regionId"] / "ztf"
        image_path = ztf_dir / reference_record["assets"]["image"]["filename"]
        rubin_path = root / rubin_record["mosaic"]["path"]
        if not image_path.is_file() or not rubin_path.is_file():
            continue
        with fits.open(image_path, memmap=False) as hdus:
            reference = np.asarray(hdus[0].data, dtype=np.float32)
            reference_header = hdus[0].header.copy()
        reference_valid = np.isfinite(reference)
        for role in ("uncertainty", "coverage"):
            asset = reference_record["assets"].get(role)
            if not asset:
                continue
            with fits.open(ztf_dir / asset["filename"], memmap=False) as hdus:
                ancillary = np.asarray(hdus[0].data, dtype=np.float32)
            reference_valid &= np.isfinite(ancillary) & (ancillary > 0)
        with fits.open(rubin_path, memmap=False) as hdus:
            rubin = np.asarray(hdus["IMAGE"].data, dtype=np.float32)
            rubin_header = hdus["IMAGE"].header.copy()
            rubin_mask = np.asarray(hdus["MASK"].data)
        native_valid = np.isfinite(rubin) & (rubin_mask == 0)
        aligned, footprint = reproject_interp((rubin, WCS(rubin_header)), WCS(reference_header), shape_out=reference.shape, order="bilinear")
        aligned_valid, _ = reproject_interp((native_valid.astype(np.float32), WCS(rubin_header)), WCS(reference_header), shape_out=reference.shape, order="nearest-neighbor")
        rubin_valid = np.isfinite(aligned) & (footprint > 0) & (aligned_valid > 0.5)
        common = rubin_valid & reference_valid
        aligned_path = previews / region["regionId"] / "rubin-on-ztf-reference.jpg"
        save_preview(aligned_path, aligned, rubin_valid, (0.92, 0.92, 0.92))
        coverage_rgb = np.zeros((*reference.shape, 3), dtype=np.float32)
        coverage_rgb[rubin_valid & ~reference_valid] = [0.95, 0.12, 0.08]
        coverage_rgb[reference_valid & ~rubin_valid] = [0.10, 0.35, 1.0]
        coverage_rgb[common] = [0.95, 0.95, 0.95]
        coverage_path = previews / region["regionId"] / "rubin-ztf-common-coverage.png"
        coverage_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.uint8(coverage_rgb * 255), mode="RGB").save(coverage_path, optimize=True)
        rd, zd = stretch(aligned, rubin_valid), stretch(reference, reference_valid)
        overlay = np.dstack((np.clip(rd + zd, 0, 1), np.clip(0.45 * rd + 0.75 * zd, 0, 1), np.clip(0.08 * rd + 0.2 * zd, 0, 1)))
        overlay[~(rubin_valid | reference_valid)] = 0
        overlay_path = previews / region["regionId"] / "rubin-ztf-position-overlay.jpg"
        Image.fromarray(np.uint8(overlay * 255), mode="RGB").save(overlay_path, quality=93, optimize=True)
        alignment = {
            "grid": "native ZTF reference cutout", "shape": list(reference.shape),
            "rubinBand": rubin_record.get("band", "r"), "referenceBand": reference_record["band"],
            "commonValidPixelFraction": float(common.mean()), "displayOnly": True, "comparisonReady": False,
            "method": "Rubin bilinear reprojection; validity nearest-neighbor; no temporal, PSF, or photometric transfer",
            "alignedRubinPreviewPath": public_path(root, aligned_path), "coveragePreviewPath": public_path(root, coverage_path),
            "overlayPreviewPath": public_path(root, overlay_path),
        }
        reference_record["alignment"] = alignment
        product_record.update({"alignedRubinPreviewPath": alignment["alignedRubinPreviewPath"], "coveragePreviewPath": alignment["coveragePreviewPath"], "overlayPreviewPath": alignment["overlayPreviewPath"]})
        completed += 1
    return completed


def build_galex_rubin_alignments(
    root: Path, output_regions: list[dict[str, Any]], normalized: list[dict[str, Any]],
    products: Path, previews: Path, rubin_manifest_path: Path,
) -> int:
    """Create display-only common grids using NUV, or FUV when NUV is absent."""
    if not rubin_manifest_path.is_file():
        return 0
    rubin_payload = json.loads(rubin_manifest_path.read_text(encoding="utf-8"))
    rubin_by_region = {record["regionId"]: record for record in rubin_payload.get("regions", [])}
    normalized_by_key = {
        (item["regionId"], item["bandOrObservable"]): item for item in normalized
        if item["surveyId"] == "galex-gr6-7" and item["productType"] == "image"
    }
    completed = 0
    for region in output_regions:
        survey = region.get("surveys", {}).get("galex-gr6-7")
        rubin_record = rubin_by_region.get(region["regionId"])
        if not survey or not rubin_record:
            continue
        band = next((candidate for candidate in ("NUV", "FUV") if survey.get("bands", {}).get(candidate, {}).get("status") == "available"), None)
        if not band:
            continue
        band_record = survey["bands"][band]
        product_record = normalized_by_key.get((region["regionId"], band))
        reference_path = products / region["regionId"] / "galex" / band_record["standardProduct"]["filename"]
        rubin_path = root / rubin_record["mosaic"]["path"]
        if not product_record or not reference_path.is_file() or not rubin_path.is_file():
            continue
        with fits.open(reference_path, memmap=False) as hdus:
            reference = np.asarray(hdus["IMAGE"].data, dtype=np.float32)
            reference_header = hdus["IMAGE"].header.copy()
            exposure = np.asarray(hdus["EXPOSURE"].data, dtype=np.float32)
            response = np.asarray(hdus["RESPONSE"].data, dtype=np.float32)
            valid_plane = np.asarray(hdus["VALID_MASK"].data) > 0
        reference_valid = np.isfinite(reference) & np.isfinite(exposure) & (exposure > 0) & np.isfinite(response) & (response > 0) & valid_plane
        with fits.open(rubin_path, memmap=False) as hdus:
            rubin = np.asarray(hdus["IMAGE"].data, dtype=np.float32)
            rubin_header = hdus["IMAGE"].header.copy()
            rubin_mask = np.asarray(hdus["MASK"].data)
        native_valid = np.isfinite(rubin) & (rubin_mask == 0)
        aligned, footprint = reproject_interp((rubin, WCS(rubin_header)), WCS(reference_header), shape_out=reference.shape, order="bilinear")
        aligned_valid, _ = reproject_interp((native_valid.astype(np.float32), WCS(rubin_header)), WCS(reference_header), shape_out=reference.shape, order="nearest-neighbor")
        rubin_valid = np.isfinite(aligned) & (footprint > 0) & (aligned_valid > 0.5)
        common = rubin_valid & reference_valid
        aligned_path = previews / region["regionId"] / f"rubin-on-galex-{band.lower()}.jpg"
        save_preview(aligned_path, aligned, rubin_valid, (0.92, 0.92, 0.92))
        coverage_rgb = np.zeros((*reference.shape, 3), dtype=np.float32)
        coverage_rgb[rubin_valid & ~reference_valid] = [0.95, 0.12, 0.08]
        coverage_rgb[reference_valid & ~rubin_valid] = [0.10, 0.35, 1.0]
        coverage_rgb[common] = [0.95, 0.95, 0.95]
        coverage_path = previews / region["regionId"] / f"rubin-galex-{band.lower()}-common-coverage.png"
        coverage_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.uint8(coverage_rgb * 255), mode="RGB").save(coverage_path, optimize=True)
        rd, gd = stretch(aligned, rubin_valid), stretch(reference, reference_valid)
        overlay = np.dstack((np.clip(rd + 0.75 * gd, 0, 1), np.clip(0.3 * rd + 0.18 * gd, 0, 1), np.clip(0.55 * rd + gd, 0, 1)))
        overlay[~(rubin_valid | reference_valid)] = 0
        overlay_path = previews / region["regionId"] / f"rubin-galex-{band.lower()}-position-overlay.jpg"
        Image.fromarray(np.uint8(overlay * 255), mode="RGB").save(overlay_path, quality=93, optimize=True)
        alignment = {
            "grid": f"native GALEX {band} cutout", "shape": list(reference.shape),
            "rubinBand": rubin_record.get("band", "r"), "referenceBand": band,
            "commonValidPixelFraction": float(common.mean()), "displayOnly": True, "comparisonReady": False,
            "method": "Rubin bilinear reprojection; GALEX support requires finite science, positive exposure/response, and VALID_MASK; no PSF or bandpass transfer",
            "alignedRubinPreviewPath": public_path(root, aligned_path), "coveragePreviewPath": public_path(root, coverage_path),
            "overlayPreviewPath": public_path(root, overlay_path),
        }
        band_record["alignment"] = alignment
        product_record.update({"alignedRubinPreviewPath": alignment["alignedRubinPreviewPath"], "coveragePreviewPath": alignment["coveragePreviewPath"], "overlayPreviewPath": alignment["overlayPreviewPath"]})
        completed += 1
    return completed


def survey_counts(records: list[dict[str, Any]], survey_id: str) -> dict[str, int]:
    statuses = [region["surveys"][survey_id]["status"] for region in records]
    return dict(sorted(Counter(statuses).items()))


def merge_existing_public(
    public_manifest_path: Path, current_regions: list[dict[str, Any]], current_products: list[dict[str, Any]],
    current_errors: list[dict[str, str]], selected_surveys: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    """Union a partial survey/region run with the last atomic public release.

    This prevents an operator's bounded retry (for example one ZTF tract) from
    collapsing the canonical manifest and gives downstream index builders a
    stable, monotonically richer release surface.
    """
    if not public_manifest_path.is_file():
        return current_regions, current_products, current_errors
    try:
        existing = json.loads(public_manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return current_regions, current_products, current_errors
    current_ids = {region["regionId"] for region in current_regions}
    region_map = {region["regionId"]: region for region in existing.get("regions", [])}
    for current in current_regions:
        previous = region_map.get(current["regionId"], {})
        merged_surveys = {
            survey_id: survey for survey_id, survey in previous.get("surveys", {}).items()
            if survey_id not in selected_surveys
        }
        merged_surveys.update(current.get("surveys", {}))
        merged = {key: current[key] for key in ("regionId", "tract", "raDeg", "decDeg")}
        merged["surveys"] = merged_surveys
        region_map[current["regionId"]] = merged
    regions = sorted(region_map.values(), key=lambda region: (int(region["tract"]), region["regionId"]))
    retained_products = [
        product for product in existing.get("products", [])
        if product.get("surveyId") not in selected_surveys or product.get("regionId") not in current_ids
    ]
    products = retained_products + current_products
    retained_errors = [
        error for error in existing.get("errors", [])
        if error.get("surveyId") not in selected_surveys or error.get("regionId") not in current_ids
    ]
    errors = retained_errors + current_errors
    return regions, products, errors


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--regions", type=Path, default=root / "pipeline/results/coverage/selected-regions.json")
    parser.add_argument("--work", type=Path, default=root / "pipeline/results/uv-ir-time-pixels")
    parser.add_argument("--public-manifest", type=Path, default=root / "public/data/layers/uv-ir-time/manifest.json")
    parser.add_argument("--previews", type=Path, default=root / "public/layer-previews/uv-ir-time")
    parser.add_argument("--rubin-manifest", type=Path, default=root / "pipeline/results/rubin-pixels-50/manifest.json")
    parser.add_argument("--galex-pixel-regions", type=int, default=3, help="Bound full GALEX source downloads; all 50 still get exact MAST discovery.")
    parser.add_argument("--ztf-ancillary-regions", type=int, default=3, help="Bound full ZTF ancillary-frame downloads; all covered regions get reference pixels.")
    parser.add_argument("--only-survey", action="append", choices=["galex-gr6-7", "unwise", "2mass", "ztf-dr"], default=[])
    parser.add_argument("--only-region", action="append", default=[])
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    regions = load_regions(args.regions)
    if args.only_region:
        regions = [region for region in regions if region["regionId"] in set(args.only_region)]
    selected_surveys = set(args.only_survey or ["galex-gr6-7", "unwise", "2mass", "ztf-dr"])
    cache, products = args.work / "cache", args.work / "products"
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    fetcher = Fetcher(session, args.refresh)
    output_regions: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    galex_pixel_regions_used = 0
    ztf_ancillary_regions_used = 0
    for index, region in enumerate(regions, start=1):
        print(f"[{index:02d}/{len(regions):02d}] {region['regionId']}", flush=True)
        record = {key: region[key] for key in ("regionId", "tract", "raDeg", "decDeg")}
        record["surveys"] = {}
        jobs = [("unwise", extract_unwise), ("2mass", acquire_2mass)]
        for survey_id, function in jobs:
            if survey_id not in selected_surveys:
                continue
            try:
                survey_record, survey_products = function(root, fetcher, region, cache, products, args.previews)
                record["surveys"][survey_id] = survey_record
                normalized.extend(survey_products)
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                record["surveys"][survey_id] = {"surveyId": survey_id, "status": "error", "error": message}
                errors.append({"regionId": region["regionId"], "surveyId": survey_id, "error": message})
                print(f"  {survey_id}: ERROR {message}", flush=True)
        if "ztf-dr" in selected_surveys:
            acquire_ancillary = ztf_ancillary_regions_used < args.ztf_ancillary_regions
            try:
                survey_record, survey_products = acquire_ztf(root, fetcher, region, cache, products, args.previews, acquire_ancillary)
                record["surveys"]["ztf-dr"] = survey_record
                normalized.extend(survey_products)
                if acquire_ancillary and survey_record.get("reference") and survey_record["reference"].get("hasUncertainty"):
                    ztf_ancillary_regions_used += 1
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                record["surveys"]["ztf-dr"] = {"surveyId": "ztf-dr", "status": "error", "error": message}
                errors.append({"regionId": region["regionId"], "surveyId": "ztf-dr", "error": message})
                print(f"  ztf-dr: ERROR {message}", flush=True)
        if "galex-gr6-7" in selected_surveys:
            acquire_pixels = galex_pixel_regions_used < args.galex_pixel_regions
            try:
                survey_record, survey_products = acquire_galex(root, fetcher, region, cache, products, args.previews, acquire_pixels)
                record["surveys"]["galex-gr6-7"] = survey_record
                normalized.extend(survey_products)
                if acquire_pixels and any(item.get("status") == "available" for item in survey_record["bands"].values()):
                    galex_pixel_regions_used += 1
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                record["surveys"]["galex-gr6-7"] = {"surveyId": "galex-gr6-7", "status": "error", "error": message}
                errors.append({"regionId": region["regionId"], "surveyId": "galex-gr6-7", "error": message})
                print(f"  galex-gr6-7: ERROR {message}", flush=True)
        output_regions.append(record)
        internal_checkpoint = {
            "schemaVersion": "layers-uv-ir-time-internal-v1", "generatedAt": now(), "sourceRegions": str(args.regions),
            "policy": {"cutoutArcmin": CUTOUT_ARCMIN, "cacheFirst": True, "wholeArchiveDownload": False, "galexPixelRegionLimit": args.galex_pixel_regions, "ztfAncillaryRegionLimit": args.ztf_ancillary_regions},
            "regions": output_regions, "products": normalized, "errors": errors,
        }
        write_json(args.work / "manifest.json", internal_checkpoint)
    output_regions, normalized, errors = merge_existing_public(
        args.public_manifest, output_regions, normalized, errors, selected_surveys
    )
    aligned_unwise_count = build_unwise_rubin_alignments(root, output_regions, normalized, products, args.previews, args.rubin_manifest)
    aligned_2mass_count = build_2mass_rubin_alignments(root, output_regions, normalized, products, args.previews, args.rubin_manifest)
    aligned_ztf_count = build_ztf_rubin_alignments(root, output_regions, normalized, products, args.previews, args.rubin_manifest)
    aligned_galex_count = build_galex_rubin_alignments(root, output_regions, normalized, products, args.previews, args.rubin_manifest)
    active_surveys = sorted({survey_id for region in output_regions for survey_id in region.get("surveys", {})})
    summary = {
        "selectedRegionCount": len(regions), "normalizedProductCount": len(normalized),
        "scienceReadyProductCount": sum(bool(item["scienceReady"]) for item in normalized),
        "displayReadyProductCount": sum(bool(item["displayReady"]) for item in normalized),
        "comparisonReadyProductCount": sum(bool(item["comparisonReady"]) for item in normalized),
        "alignedUnwiseW1DisplayCount": aligned_unwise_count,
        "aligned2MassKsDisplayCount": aligned_2mass_count,
        "alignedZtfReferenceDisplayCount": aligned_ztf_count,
        "alignedGalexDisplayCount": aligned_galex_count,
        "errorCount": len(errors),
        "surveyRegionStatusCounts": {
            survey_id: survey_counts(output_regions, survey_id) for survey_id in active_surveys
            if all(survey_id in region["surveys"] for region in output_regions)
        },
        "surveyProductCounts": dict(sorted(Counter(item["surveyId"] for item in normalized).items())),
    }
    internal = {
        "schemaVersion": "layers-uv-ir-time-internal-v1", "generatedAt": now(), "sourceRegions": str(args.regions),
        "policy": {"cutoutArcmin": CUTOUT_ARCMIN, "cacheFirst": True, "wholeArchiveDownload": False, "galexPixelRegionLimit": args.galex_pixel_regions, "ztfAncillaryRegionLimit": args.ztf_ancillary_regions},
        "regions": output_regions, "products": normalized, "errors": errors, "summary": summary,
    }
    write_json(args.work / "manifest.json", internal)
    public_regions = []
    for region in output_regions:
        public_region = {key: region[key] for key in ("regionId", "tract", "raDeg", "decDeg")}
        public_region["surveys"] = {}
        for survey_id, survey in region["surveys"].items():
            # Rich records already contain only filenames, public preview paths,
            # immutable source identifiers/URLs, checksums, and response facts.
            public_region["surveys"][survey_id] = survey
        public_regions.append(public_region)
    public = {
        "schemaVersion": "layers-uv-ir-time-public-v1", "generatedAt": now(),
        "title": "Archive-native UV, infrared, and time-domain products for selected Rubin DP2 regions",
        "policy": {
            "cutoutArcmin": CUTOUT_ARCMIN, "cacheFirst": True, "wholeArchiveDownload": False,
            "galexPixelRegionLimit": args.galex_pixel_regions,
            "ztfAncillaryRegionLimit": args.ztf_ancillary_regions,
            "readinessRule": "scienceReady means validated native pixels/catalog rows; comparisonReady additionally requires all cross-survey QA gates and is false here.",
        },
        "summary": summary, "documentation": DOCS, "products": normalized, "regions": public_regions,
        "errors": errors,
    }
    write_json(args.public_manifest, public)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
