#!/usr/bin/env python3
"""Acquire bounded radio, X-ray, and H I evidence for the selected Rubin fields.

The runner is intentionally conservative about semantics:

* archive-native pixels, catalogue evidence, and valid non-detections are
  different product types;
* VLASS Quick Look pixels are display candidates, not precision photometry;
* the HIPASS HiPS plane is retained with its native frame number but is not
  assigned a velocity or flux unit that the HiPS metadata does not provide;
* cross-band products are positional co-displays only.  No flux subtraction,
  ratio, or "missing light" claim is produced.

Raw responses are cache-first under ``pipeline/results/radio-xray-hi``.  The
public manifest is redacted and contains stable provenance URLs, checksums, and
display previews, never CADC signed redirects or local cache paths.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import astropy.units as u
import numpy as np
import requests
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.nddata import Cutout2D
from astropy.table import Table, vstack
from astropy.wcs import WCS
from astropy_healpix import HEALPix
from reproject import reproject_interp


SCHEMA = "layers-radio-xray-hi-v1"
USER_AGENT = "Layers-radio-xray-hi/1.0 (+https://rubin-light-atlas.vercel.app/)"
FIELD_RADIUS_DEG = 2.0 / 60.0
HIPASS_DISPLAY_FOV_DEG = 2.0
HIPASS_FRAME = 512
HIPASS_URL = "https://alaskybis.cds.unistra.fr/HIPASS"
EROSITA_API = "https://erosita.mpe.mpg.de/erodat"
CADC_TAP = "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus/sync"
CADC_DATALINK = "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/caom2ops/datalink"
CADC_SODA = "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/caom2ops/sync"
VIZIER_VOTABLE = "https://vizier.cds.unistra.fr/viz-bin/votable"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_bytes(path, json.dumps(value, indent=2, allow_nan=False).encode("utf-8"))


def request_cached(
    session: requests.Session,
    path: Path,
    method: str,
    url: str,
    *,
    refresh: bool,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    json_body: Any = None,
    timeout: int = 300,
    expected: tuple[str, ...] = (),
) -> tuple[bytes, dict[str, Any]]:
    prepared = requests.Request(method, url, params=params, data=data, json=json_body).prepare()
    request_url = prepared.url or url
    if path.is_file() and path.stat().st_size > 0 and not refresh:
        return path.read_bytes(), {
            "requestUrl": request_url,
            "retrievedAt": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "cacheHit": True,
        }
    last: Exception | None = None
    for attempt in range(4):
        try:
            response = session.request(method, url, params=params, data=data, json=json_body, timeout=timeout)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if expected and not any(token in content_type for token in expected):
                snippet = response.content[:200].decode("utf-8", errors="replace")
                raise RuntimeError(f"unexpected content type {content_type!r}: {snippet}")
            if not response.content:
                raise RuntimeError("empty response")
            atomic_bytes(path, response.content)
            return response.content, {
                "requestUrl": request_url,
                "retrievedAt": now(),
                "cacheHit": False,
                "contentType": content_type,
            }
        except Exception as error:
            last = error
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"{type(last).__name__}: {last}")


def artifact(root: Path, path: Path, *, source_url: str, role: str) -> dict[str, Any]:
    return {
        "path": relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "sourceUrl": source_url,
        "role": role,
    }


def first_image_hdu(hdul: fits.HDUList) -> fits.ImageHDU | fits.PrimaryHDU:
    for hdu in hdul:
        if hdu.data is not None and np.asarray(hdu.data).size and np.asarray(hdu.data).ndim >= 2:
            return hdu
    raise ValueError("FITS contains no image HDU")


def image_2d(hdu: fits.ImageHDU | fits.PrimaryHDU) -> tuple[np.ndarray, WCS]:
    data = np.asarray(hdu.data, dtype=np.float64)
    while data.ndim > 2:
        data = data[0]
    wcs = WCS(hdu.header).celestial
    if data.ndim != 2 or not wcs.has_celestial:
        raise ValueError("image lacks a two-dimensional celestial WCS")
    return data, wcs


def write_hdul(path: Path, hdul: fits.HDUList) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".fits", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        hdul.writeto(temporary, overwrite=True, checksum=True)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def stretch(data: np.ndarray) -> np.ndarray:
    array = np.asarray(data, dtype=float)
    valid = np.isfinite(array)
    if not np.any(valid):
        return np.zeros(array.shape, dtype=np.uint8)
    low, high = np.nanpercentile(array[valid], (1.0, 99.5))
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        low, high = float(np.nanmin(array[valid])), float(np.nanmax(array[valid]))
    if high <= low:
        scaled = np.zeros(array.shape, dtype=float)
    else:
        scaled = np.clip((array - low) / (high - low), 0, 1)
        scaled = np.arcsinh(8 * scaled) / np.arcsinh(8)
    scaled[~valid] = 0
    return np.flipud((scaled * 255).astype(np.uint8))


def png(path: Path, array: np.ndarray, mode: str = "L") -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array, mode=mode).save(path, optimize=True)


def public_path(public_root: Path, path: Path) -> str:
    return "/" + relative(public_root, path)


def load_rubin(root: Path) -> dict[int, dict[str, Any]]:
    payload = json.loads((root / "pipeline/results/rubin-pixels-50/manifest.json").read_text(encoding="utf-8"))
    return {int(record["tract"]): record for record in payload["regions"] if record.get("status") == "complete"}


def display_alignment(
    root: Path,
    preview_root: Path,
    rubin: dict[str, Any],
    external_path: Path,
    *,
    region_id: str,
    survey_id: str,
    external_hdu: str | int = 0,
    external_mask_hdu: str | int | None = None,
) -> dict[str, Any]:
    rubin_path = root / rubin["mosaic"]["path"]
    with fits.open(rubin_path, memmap=False) as rh, fits.open(external_path, memmap=False) as eh:
        rubin_hdu = rh["IMAGE"]
        rubin_data, rubin_wcs = image_2d(rubin_hdu)
        try:
            ext_hdu = eh[external_hdu]
        except (KeyError, IndexError):
            ext_hdu = first_image_hdu(eh)
        ext_data, ext_wcs = image_2d(ext_hdu)
        aligned, footprint = reproject_interp(
            (rubin_data, rubin_wcs), ext_wcs, shape_out=ext_data.shape, order="bilinear", return_footprint=True
        )
        external_valid = np.isfinite(ext_data)
        if external_mask_hdu is not None:
            mask = np.asarray(eh[external_mask_hdu].data)
            while mask.ndim > 2:
                mask = mask[0]
            external_valid &= mask.astype(bool)
        common = external_valid & np.isfinite(aligned) & (footprint > 0)
    if not np.any(common):
        raise ValueError("no finite common Rubin/external support")
    basename = f"{region_id}-{survey_id}"
    ext_preview = preview_root / f"{basename}-external.png"
    rubin_preview = preview_root / f"{basename}-rubin-aligned.png"
    coverage_preview = preview_root / f"{basename}-coverage.png"
    overlay_preview = preview_root / f"{basename}-overlay.png"
    ext_gray = stretch(np.where(common, ext_data, np.nan))
    rubin_gray = stretch(np.where(common, aligned, np.nan))
    common_display = np.flipud(common)
    png(ext_preview, ext_gray)
    png(rubin_preview, rubin_gray)
    coverage_rgb = np.zeros((*common.shape, 3), dtype=np.uint8)
    r_support = np.flipud(np.isfinite(aligned) & (footprint > 0))
    e_support = np.flipud(external_valid)
    coverage_rgb[..., 0] = r_support.astype(np.uint8) * 220
    coverage_rgb[..., 2] = e_support.astype(np.uint8) * 220
    coverage_rgb[common_display] = (245, 245, 245)
    png(coverage_preview, coverage_rgb, "RGB")
    optical = rubin_gray.astype(np.float32) / 255.0
    external = ext_gray.astype(np.float32) / 255.0
    overlay = np.stack((optical, 0.48 * optical + 0.58 * external, external), axis=-1)
    overlay[~common_display] = 0
    png(overlay_preview, (np.clip(overlay, 0, 1) * 255).astype(np.uint8), "RGB")
    return {
        "previewPath": public_path(root / "public", ext_preview),
        "alignedRubinPreviewPath": public_path(root / "public", rubin_preview),
        "coveragePreviewPath": public_path(root / "public", coverage_preview),
        "overlayPreviewPath": public_path(root / "public", overlay_preview),
        "commonCoverageFraction": float(common.mean()),
        "commonCoveragePixelCount": int(common.sum()),
        "operation": "Rubin IMAGE bilinearly reprojected to external celestial grid for positional display only",
    }


def normalized_product(
    region: dict[str, Any],
    *,
    survey_id: str,
    survey_name: str,
    family: str,
    release: str,
    product_type: str,
    status: str,
    science_ready: bool,
    display_ready: bool,
    observable: str,
    unit: str | None,
    provenance: list[str],
    checksum: str | None,
    blockers: list[str],
    display: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product = {
        "regionId": region["id"],
        "tract": int(region["tract"]),
        "surveyId": survey_id,
        "surveyName": survey_name,
        "family": family,
        "release": release,
        "productType": product_type,
        "status": status,
        "scienceReady": bool(science_ready),
        "displayReady": bool(display_ready),
        "comparisonReady": False,
        "bandOrObservable": observable,
        "unit": unit,
        "provenanceUrls": provenance,
        "checksum": checksum,
        "blockers": blockers,
    }
    if display:
        product.update({key: value for key, value in display.items() if key.endswith("Path")})
        product["displayAlignment"] = {
            key: value for key, value in display.items() if not key.endswith("Path")
        }
    if details:
        product["details"] = details
    return product


def fetch_lotss(
    session: requests.Session,
    root: Path,
    cache: Path,
    preview_root: Path,
    regions: list[dict[str, Any]],
    rubin: dict[int, dict[str, Any]],
    refresh: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    products: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for region in [r for r in regions if "lotss-dr2" in r.get("confirmedSurveyIds", [])]:
        field_cache = cache / "lotss" / region["id"]
        attempts = []
        record = None
        for release in ("DR3", "DR2"):
            url = f"https://lofar-surveys.org/{release.lower()}-cutout.fits"
            params = {"pos": f"{region['center'][0]:.8f} {region['center'][1]:.8f}", "size": 6.0}
            path = field_cache / f"lotss-{release.lower()}-6arcmin.fits"
            try:
                _, meta = request_cached(session, path, "GET", url, refresh=refresh, params=params, expected=("fits",))
                with fits.open(path, memmap=False, checksum=True) as hdul:
                    hdu = first_image_hdu(hdul)
                    data, wcs = image_2d(hdu)
                    finite = float(np.isfinite(data).mean())
                    bunit = hdu.header.get("BUNIT")
                    beam = {key: hdu.header.get(key) for key in ("BMAJ", "BMIN", "BPA")}
                    if finite <= 0 or not wcs.has_celestial:
                        raise ValueError("no finite celestial raster")
                display = display_alignment(
                    root, preview_root, rubin[int(region["tract"])], path,
                    region_id=region["id"], survey_id=f"lotss-{release.lower()}"
                )
                art = artifact(root, path, source_url=meta["requestUrl"], role="archive-native LoTSS FITS cutout")
                record = {
                    "regionId": region["id"], "tract": region["tract"], "release": release,
                    "status": "available", "artifact": art, "finiteFraction": finite,
                    "unit": bunit, "beam": beam, "display": display,
                }
                products.append(normalized_product(
                    region, survey_id="lotss", survey_name="LOFAR Two-metre Sky Survey", family="radio",
                    release=f"LoTSS {release}", product_type="image", status="available",
                    science_ready=False, display_ready=True, observable="144 MHz continuum", unit=bunit,
                    provenance=[url, "https://lofar-surveys.org/cutout_api_details.html"], checksum=art["sha256"],
                    blockers=["noise map not returned by cutout API", "beam/PSF matching", "cross-band unit mismatch", "background QA"],
                    display=display, details={"finiteFraction": finite, "beam": beam},
                ))
                break
            except Exception as error:
                attempts.append({"release": release, "error": str(error)})
        if record is None:
            products.append(normalized_product(
                region, survey_id="lotss", survey_name="LOFAR Two-metre Sky Survey", family="radio",
                release="LoTSS DR3/DR2", product_type="image", status="error", science_ready=False,
                display_ready=False, observable="144 MHz continuum", unit=None,
                provenance=["https://lofar-surveys.org/cutout_api_details.html"], checksum=None,
                blockers=["archive returned no usable FITS cutout"], details={"attempts": attempts},
            ))
            record = {"regionId": region["id"], "tract": region["tract"], "status": "error", "attempts": attempts}
        evidence.append(record)
        print(f"[LoTSS] {region['id']}: {record['status']}", flush=True)
    return products, evidence


def vlass_metadata(
    session: requests.Session, root: Path, cache: Path, regions: list[dict[str, Any]], refresh: bool
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    eligible = [r for r in regions if "vlass" in r.get("confirmedSurveyIds", [])]
    spatial = " OR ".join(
        f"INTERSECTS(s_region,CIRCLE('ICRS',{r['center'][0]:.8f},{r['center'][1]:.8f},{FIELD_RADIUS_DEG:.8f}))=1"
        for r in eligible
    )
    query = (
        "SELECT obs_publisher_did,obs_id,s_ra,s_dec,s_fov,s_region,t_min,t_max,access_url "
        "FROM ivoa.ObsCore WHERE obs_collection='VLASS' AND dataproduct_type='image' "
        f"AND calib_level=2 AND ({spatial})"
    )
    path = cache / "vlass" / "selected-50-obscore.csv"
    payload, meta = request_cached(
        session, path, "POST", CADC_TAP, refresh=refresh,
        data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "MAXREC": "5000", "QUERY": query},
        timeout=600, expected=("csv",),
    )
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8")))), {
        "query": query, "artifact": artifact(root, path, source_url=CADC_TAP, role="VLASS ObsCore response"), **meta
    }


def latest_vlass_row(region: dict[str, Any], rows: list[dict[str, str]]) -> dict[str, str] | None:
    center = SkyCoord(region["center"][0] * u.deg, region["center"][1] * u.deg)
    candidates = []
    for row in rows:
        try:
            position = SkyCoord(float(row["s_ra"]) * u.deg, float(row["s_dec"]) * u.deg)
            separation = float(center.separation(position).deg)
            fov = float(row.get("s_fov") or 0)
        except Exception:
            continue
        if separation <= max(0.76, fov / 2 + FIELD_RADIUS_DEG):
            match = re.search(r"VLASS(\d+)\.(\d+)", row.get("obs_id", ""))
            epoch = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
            candidates.append((epoch, -separation, row))
    return max(candidates, default=(None, None, None))[2]


def datalink_soda(payload: bytes) -> tuple[str, str]:
    root = ET.fromstring(payload)
    ns = {"v": "http://www.ivoa.net/xml/VOTable/v1.3"}
    for resource in root.findall(".//v:RESOURCE[@type='meta']", ns):
        standard = resource.find("v:PARAM[@name='standardID']", ns)
        if standard is None or "SODA#sync" not in standard.attrib.get("value", ""):
            continue
        access = resource.find("v:PARAM[@name='accessURL']", ns)
        identifier = resource.find(".//v:GROUP[@name='inputParams']/v:PARAM[@name='ID']", ns)
        if access is not None and identifier is not None:
            return access.attrib["value"], identifier.attrib["value"]
    raise ValueError("DataLink response has no SODA sync descriptor")


def fetch_one_vlass(
    root: Path,
    cache: Path,
    preview_root: Path,
    region: dict[str, Any],
    row: dict[str, str] | None,
    rubin: dict[int, dict[str, Any]],
    refresh: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if row is None:
        product = normalized_product(
            region, survey_id="vlass", survey_name="VLA Sky Survey", family="radio", release="VLASS",
            product_type="image", status="none", science_ready=False, display_ready=False,
            observable="2–4 GHz Stokes I continuum", unit=None,
            provenance=["https://science.nrao.edu/vlass/vlass-data", CADC_TAP], checksum=None,
            blockers=["no matching CADC image holding returned"],
        )
        return product, {"regionId": region["id"], "status": "none"}
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    field_cache = cache / "vlass" / region["id"]
    datalink_path = field_cache / "datalink.vot"
    try:
        payload, _ = request_cached(
            session, datalink_path, "GET", CADC_DATALINK, refresh=refresh,
            params={"ID": row["obs_publisher_did"]}, timeout=300, expected=("xml", "votable"),
        )
        soda_url, dataset_id = datalink_soda(payload)
        cutout_path = field_cache / f"{row['obs_id']}-4arcmin.fits"
        _, _ = request_cached(
            session, cutout_path, "GET", soda_url, refresh=refresh,
            params={"ID": dataset_id, "CIRCLE": f"{region['center'][0]:.8f} {region['center'][1]:.8f} {FIELD_RADIUS_DEG:.8f}"},
            timeout=360, expected=("fits",),
        )
        with fits.open(cutout_path, memmap=False, checksum=True) as hdul:
            hdu = first_image_hdu(hdul)
            data, wcs = image_2d(hdu)
            bunit = hdu.header.get("BUNIT")
            finite = float(np.isfinite(data).mean())
            beam = {key: hdu.header.get(key) for key in ("BMAJ", "BMIN", "BPA")}
            center_x, center_y = wcs.world_to_pixel(SkyCoord(region["center"][0] * u.deg, region["center"][1] * u.deg))
            contains = -1 <= center_x <= data.shape[1] and -1 <= center_y <= data.shape[0]
            if finite <= 0 or not contains:
                raise ValueError("SODA raster has no finite support at requested center")
        display = display_alignment(
            root, preview_root, rubin[int(region["tract"])], cutout_path,
            region_id=region["id"], survey_id="vlass"
        )
        art = artifact(root, cutout_path, source_url=CADC_SODA, role="CADC SODA VLASS Quick Look cutout")
        release = row["obs_id"].split(".", 2)[0] + "." + row["obs_id"].split(".", 2)[1]
        blockers = [
            "VLASS Quick Look products are not recommended for precision photometry",
            "RMS/noise image not included in this bounded cutout",
            "beam/PSF matching", "cross-band unit mismatch", "background and deconvolution QA",
        ]
        product = normalized_product(
            region, survey_id="vlass", survey_name="VLA Sky Survey", family="radio", release=release,
            product_type="image", status="available", science_ready=False, display_ready=True,
            observable="2–4 GHz Stokes I continuum", unit=bunit,
            provenance=[CADC_TAP, CADC_DATALINK, CADC_SODA, "https://science.nrao.edu/vlass/vlass-data"],
            checksum=art["sha256"], blockers=blockers, display=display,
            details={"obsId": row["obs_id"], "finiteFraction": finite, "beam": beam, "datasetId": dataset_id},
        )
        evidence = {
            "regionId": region["id"], "tract": region["tract"], "status": "available", "obsCore": row,
            "datasetId": dataset_id, "artifact": art, "datalinkArtifact": artifact(root, datalink_path, source_url=CADC_DATALINK, role="IVOA DataLink response"),
            "unit": bunit, "beam": beam, "finiteFraction": finite, "display": display,
        }
        return product, evidence
    except Exception as error:
        product = normalized_product(
            region, survey_id="vlass", survey_name="VLA Sky Survey", family="radio", release="VLASS",
            product_type="image", status="error", science_ready=False, display_ready=False,
            observable="2–4 GHz Stokes I continuum", unit=None,
            provenance=[CADC_TAP, CADC_DATALINK, CADC_SODA, "https://science.nrao.edu/vlass/vlass-data"],
            checksum=None, blockers=[str(error)], details={"obsId": row.get("obs_id")},
        )
        return product, {"regionId": region["id"], "tract": region["tract"], "status": "error", "error": str(error), "obsCore": row}


def fetch_vlass(
    session: requests.Session,
    root: Path,
    cache: Path,
    preview_root: Path,
    regions: list[dict[str, Any]],
    rubin: dict[int, dict[str, Any]],
    refresh: bool,
    maximum: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    eligible = [r for r in regions if "vlass" in r.get("confirmedSurveyIds", [])]
    selected = eligible[:maximum]
    # This released holding is retained as a reproducible bootstrap so a slow
    # archive-wide spatial query cannot prevent the other families from being
    # published.  It was resolved from the same CADC ObsCore endpoint and its
    # DataLink/SODA descriptor is fetched and validated below.  Runs requesting
    # more than one region perform the live bounded spatial query.
    bootstrap_rows = [{
        "obs_publisher_did": "ivo://cadc.nrc.ca/VLASS?VLASS4.1.T04t06.J033113-273000/VLASS4.1.T04t06.J033113-273000.quicklook",
        "obs_id": "VLASS4.1.T04t06.J033113-273000",
        "s_ra": "52.80564950048382", "s_dec": "-27.49998897442771", "s_fov": "1.4617894985650903",
        "s_region": "Polygon ICRS 53.385592 -26.981969 53.391062 -28.015583 52.220235 -28.015582 52.225709 -26.981968",
        "t_min": "", "t_max": "", "access_url": CADC_DATALINK,
    }]
    if maximum <= 0:
        rows = []
        query_meta = {"mode": "not-requested", "artifact": None}
    elif maximum == 1 and selected and int(selected[0]["tract"]) == 5063:
        rows = bootstrap_rows
        query_meta = {
            "mode": "retained-CADC-ObsCore-bootstrap",
            "endpoint": CADC_TAP,
            "note": "Exact released holding is revalidated through live DataLink and SODA; no signed URL is retained.",
        }
    else:
        rows, query_meta = vlass_metadata(session, root, cache, selected, refresh)
    products: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        jobs = {
            executor.submit(fetch_one_vlass, root, cache, preview_root, region, latest_vlass_row(region, rows), rubin, refresh): region
            for region in selected
        }
        for future in as_completed(jobs):
            product, evidence = future.result()
            products.append(product)
            records.append(evidence)
            print(f"[VLASS] {product['regionId']}: {product['status']}", flush=True)
    for region in eligible[maximum:]:
        products.append(normalized_product(
            region, survey_id="vlass", survey_name="VLA Sky Survey", family="radio", release="VLASS",
            product_type="image", status="not-fetched", science_ready=False, display_ready=False,
            observable="2–4 GHz Stokes I continuum", unit=None,
            provenance=[CADC_TAP, CADC_DATALINK, "https://science.nrao.edu/vlass/vlass-data"], checksum=None,
            blockers=[f"bounded run limited pixel retrieval to {maximum} regions; coverage is known but pixels were not requested"],
        ))
    products.sort(key=lambda item: item["tract"])
    records.sort(key=lambda item: item.get("tract", 0))
    return products, {"obsCore": query_meta, "holdingCount": len(rows), "regions": records}


def tan_wcs(ra: float, dec: float, size: int, fov_deg: float) -> WCS:
    wcs = WCS(naxis=2)
    scale = fov_deg / size
    wcs.wcs.crpix = [(size + 1) / 2, (size + 1) / 2]
    wcs.wcs.cdelt = [-scale, scale]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cunit = ["deg", "deg"]
    return wcs


def fetch_hipass(
    session: requests.Session,
    root: Path,
    cache: Path,
    products_root: Path,
    preview_root: Path,
    regions: list[dict[str, Any]],
    rubin: dict[int, dict[str, Any]],
    refresh: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    catalog_tables = []
    catalog_artifacts = []
    for catalog in ("VIII/73/hicat", "VIII/89/nhicat"):
        slug = catalog.replace("/", "-")
        path = cache / "hipass" / f"{slug}.vot"
        payload, meta = request_cached(
            session, path, "GET", VIZIER_VOTABLE, refresh=refresh,
            # VizieR ignores -out.all with an empty value and silently returns its
            # default column subset. That dropped W50max/W20max, the H I line
            # widths, so the cached "full catalogue" carried 12 of 36 columns and
            # no rotation-velocity proxy at all. -out.all=1 returns every column.
            params={"-source": catalog, "-out.all": "1", "-out.max": "unlimited"},
            expected=("xml", "votable"),
        )
        table = Table.read(io.BytesIO(payload), format="votable")
        table["catalog"] = catalog
        catalog_tables.append(table)
        catalog_artifacts.append(artifact(root, path, source_url=meta["requestUrl"], role=f"VizieR {catalog} full catalogue"))
    catalog = vstack(catalog_tables, metadata_conflicts="silent")
    catalog_checksum = hashlib.sha256(
        "".join(sorted(item["sha256"] for item in catalog_artifacts)).encode("ascii")
    ).hexdigest()
    catalog_coords = SkyCoord(catalog["RAJ2000"], catalog["DEJ2000"], unit=(u.hourangle, u.deg))
    hp = HEALPix(nside=8, order="nested", frame="icrs")
    pixel_products: list[dict[str, Any]] = []
    catalog_products: list[dict[str, Any]] = []
    records = []
    tile_artifacts: dict[int, dict[str, Any]] = {}
    for region in regions:
        center = SkyCoord(region["center"][0] * u.deg, region["center"][1] * u.deg)
        separations = center.separation(catalog_coords)
        within = np.flatnonzero(separations.deg <= float(region.get("radiusArcmin", 72)) / 60.0)
        nearest = None
        if len(within):
            index = within[np.argmin(separations[within])]
            row = catalog[index]
            nearest = {
                "catalog": str(row["catalog"]), "hipass": str(row["HIPASS"]),
                "raDeg": float(catalog_coords[index].ra.deg), "decDeg": float(catalog_coords[index].dec.deg),
                "separationDeg": float(separations[index].deg),
                "radialVelocityKmS": float(row["RVsp"]) if not np.ma.is_masked(row["RVsp"]) else None,
                "peakFluxJy": float(row["Speak"]) if not np.ma.is_masked(row["Speak"]) else None,
                "integratedFluxJyKmS": float(row["Sint"]) if not np.ma.is_masked(row["Sint"]) else None,
            }
        catalog_status = "available" if len(within) else "none"
        catalog_products.append(normalized_product(
            region, survey_id="hipass", survey_name="H I Parkes All Sky Survey", family="neutral-gas",
            release="HICAT + NHICAT", product_type="catalog-evidence", status=catalog_status,
            science_ready=bool(len(within)), display_ready=False, observable="H I 21 cm detections",
            unit="Jy; Jy km/s; km/s", provenance=[
                "https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VIII/73/hicat",
                "https://vizier.cds.unistra.fr/viz-bin/VizieR?-source=VIII/89/nhicat",
                "https://data.csiro.au/collection/csiro:32333",
            ], checksum=(catalog_checksum if len(within) else None), blockers=([] if len(within) else ["no released HICAT/NHICAT detection within the Rubin tract"]),
            details={"detectionCount": int(len(within)), "nearestDetection": nearest},
        ))
        ipix = int(hp.skycoord_to_healpix(center))
        tile_path = cache / "hipass" / "tiles" / f"Npix{ipix}_{HIPASS_FRAME}.fits"
        tile_url = f"{HIPASS_URL}/Norder3/Dir{(ipix // 10000) * 10000}/Npix{ipix}_{HIPASS_FRAME}.fits"
        try:
            if ipix not in tile_artifacts or refresh:
                request_cached(session, tile_path, "GET", tile_url, refresh=refresh, expected=("fits",))
                tile_artifacts[ipix] = artifact(root, tile_path, source_url=tile_url, role=f"native HIPASS HiPS spectral plane {HIPASS_FRAME}")
            with fits.open(tile_path, memmap=False) as hdul:
                source_hdu = first_image_hdu(hdul)
                source_data, source_wcs = image_2d(source_hdu)
                target_wcs = tan_wcs(region["center"][0], region["center"][1], 256, HIPASS_DISPLAY_FOV_DEG)
                cutout, footprint = reproject_interp(
                    (source_data, source_wcs), target_wcs, shape_out=(256, 256), order="bilinear", return_footprint=True
                )
            valid = np.isfinite(cutout) & (footprint > 0)
            if not np.any(valid):
                raise ValueError("HIPASS tile has no finite support at selected field")
            header = target_wcs.to_header(relax=True)
            header["SURVEY"] = "HIPASS"
            header["HIPSFRAM"] = HIPASS_FRAME
            header["ORIGNPIX"] = ipix
            header["SCIENCE"] = False
            header.add_history("Display cutout reprojected from native HIPASS HiPS plane 512.")
            header.add_history("HiPS metadata does not encode this plane's velocity or BUNIT; do not infer either.")
            mask_header = target_wcs.to_header(relax=True)
            mask_header["MASKDEF"] = "1=finite source support"
            product_path = products_root / "hipass" / region["id"] / "hipass-frame512-display.fits"
            write_hdul(product_path, fits.HDUList([
                fits.PrimaryHDU(),
                fits.ImageHDU(cutout.astype(np.float32), header=header, name="IMAGE"),
                fits.ImageHDU(valid.astype(np.uint8), header=mask_header, name="VALID_MASK"),
            ]))
            display = display_alignment(
                root, preview_root, rubin[int(region["tract"])], product_path,
                region_id=region["id"], survey_id="hipass-frame512", external_hdu="IMAGE", external_mask_hdu="VALID_MASK"
            )
            art = artifact(root, product_path, source_url=tile_url, role="derived TAN display cutout from native HIPASS HiPS plane")
            pixel_products.append(normalized_product(
                region, survey_id="hipass", survey_name="H I Parkes All Sky Survey", family="neutral-gas",
                release="HIPASS HiPS (CDS/CSIRO source data)", product_type="spectral-plane-image", status="available",
                science_ready=False, display_ready=True, observable=f"H I 21 cm native cube plane {HIPASS_FRAME}", unit=None,
                provenance=[tile_url, f"{HIPASS_URL}/properties", "https://doi.org/10.25919/5c36de6d37141"],
                checksum=art["sha256"], blockers=[
                    "physical velocity/frequency mapping is absent from the HiPS tile metadata",
                    "BUNIT is absent from the HiPS tile metadata", "15.5 arcmin HIPASS beam not matched",
                    "display reprojection is not flux conserving", "cross-band unit mismatch",
                ], display=display, details={
                    "nativeTileIpix": ipix, "nativeFrame": HIPASS_FRAME,
                    "finiteFraction": float(valid.mean()), "beamFwhmArcminFromSurveyMetadata": 15.5,
                },
            ))
            records.append({
                "regionId": region["id"], "tract": region["tract"], "status": "available", "nativeTile": tile_artifacts[ipix],
                "displayArtifact": art, "catalogDetectionCount": int(len(within)), "nearestDetection": nearest, "display": display,
            })
        except Exception as error:
            pixel_products.append(normalized_product(
                region, survey_id="hipass", survey_name="H I Parkes All Sky Survey", family="neutral-gas",
                release="HIPASS HiPS", product_type="spectral-plane-image", status="error", science_ready=False,
                display_ready=False, observable=f"H I 21 cm native cube plane {HIPASS_FRAME}", unit=None,
                provenance=[tile_url, f"{HIPASS_URL}/properties", "https://doi.org/10.25919/5c36de6d37141"],
                checksum=None, blockers=[str(error)],
            ))
            records.append({"regionId": region["id"], "tract": region["tract"], "status": "error", "error": str(error)})
        print(f"[HIPASS] {region['id']}: pixels={records[-1]['status']} catalog={catalog_status} ({len(within)})", flush=True)
    return pixel_products + catalog_products, {
        "catalogArtifacts": catalog_artifacts, "catalogRowCount": len(catalog),
        "nativeTileCount": len(tile_artifacts), "regions": records,
    }


def decompress_fits_gz(source: Path, target: Path) -> None:
    if target.is_file() and target.stat().st_size > 0:
        return
    with gzip.open(source, "rb") as handle:
        atomic_bytes(target, handle.read())


def fetch_erosita_catalogs(
    session: requests.Session,
    root: Path,
    cache: Path,
    regions: list[dict[str, Any]],
    refresh: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    multi_path = cache / "erosita" / "upper-limits-50.json"
    query = [{"ra": r["center"][0], "dec": r["center"][1], "band": "024", "dr_survey": "DR1_eRASS1"} for r in regions]
    payload, meta = request_cached(
        session, multi_path, "POST", f"{EROSITA_API}/upperlimit/service_multi",
        refresh=refresh, json_body=query, expected=("json",),
    )
    limits = json.loads(payload).get("limits", [])
    if len(limits) != len(regions):
        raise RuntimeError(f"eROSITA multi upper-limit response length {len(limits)} != {len(regions)}")
    products = []
    records = []
    by_region: dict[str, dict[str, Any]] = {}
    for region, limit in zip(regions, limits, strict=True):
        covered = bool(limit.get("de_sky"))
        sources: list[dict[str, Any]] = []
        cone_art = None
        cone_error = None
        if covered:
            cone_path = cache / "erosita" / region["id"] / "erass1-main.vot"
            params = {"CAT": "DR1_Main", "RA": region["center"][0], "DEC": region["center"][1], "SR": FIELD_RADIUS_DEG, "VERB": 3}
            try:
                cone_payload, cone_meta = request_cached(
                    session, cone_path, "GET", f"{EROSITA_API}/catalogue/SCS", refresh=refresh,
                    params=params, expected=("xml", "votable"),
                )
                table = Table.read(io.BytesIO(cone_payload), format="votable")
                for row in table:
                    lower = {name.lower(): name for name in table.colnames}
                    def value(name: str) -> Any:
                        key = lower.get(name.lower())
                        if key is None or np.ma.is_masked(row[key]):
                            return None
                        item = row[key]
                        return item.item() if hasattr(item, "item") else item
                    sources.append({
                        "name": str(value("IAUNAME") or value("DETUID") or "eRASS1 source"),
                        "raDeg": value("RA"), "decDeg": value("DEC"), "detectionLikelihood": value("DET_LIKE_0"),
                        "flux": value("ML_FLUX_1"), "exposure": value("ML_EXP_1"),
                    })
                cone_art = artifact(root, cone_path, source_url=cone_meta["requestUrl"], role="eRASS1 Main cone search")
            except Exception as error:
                cone_error = str(error)
        status = "available" if covered else "none"
        blockers = [] if covered else ["position is outside the released eROSITA-DE eRASS1 hemisphere"]
        if cone_error:
            blockers.append(f"source cone search failed: {cone_error}")
        product = normalized_product(
            region, survey_id="erosita-erass1", survey_name="eROSITA", family="high-energy",
            release="eROSITA-DE eRASS1 DR1", product_type="catalog-upper-limit-evidence", status=status,
            science_ready=covered, display_ready=False, observable="0.2–2.3 keV sources and center upper limit",
            unit="erg s-1 cm-2; counts; seconds", provenance=[
                f"{EROSITA_API}/catalogue/SCS", f"{EROSITA_API}/upperlimit/service_multi",
                "https://erosita.mpe.mpg.de/erodat/apis/",
            ], checksum=(cone_art["sha256"] if cone_art else None), blockers=blockers,
            details={"covered": covered, "sourceCount": len(sources), "upperLimit": limit},
        )
        products.append(product)
        record = {
            "regionId": region["id"], "tract": region["tract"], "status": status, "covered": covered,
            "sourceCount": len(sources), "sources": sources, "upperLimit": limit,
            "coneArtifact": cone_art, "coneError": cone_error,
        }
        records.append(record)
        by_region[region["id"]] = record
        print(f"[eROSITA catalogue] {region['id']}: covered={covered} sources={len(sources)}", flush=True)
    return products, {
        "upperLimitArtifact": artifact(root, multi_path, source_url=meta["requestUrl"], role="eROSITA batch upper-limit response"),
        "regions": records,
    }, by_region


def crop_erosita_product(
    root: Path,
    products_root: Path,
    region: dict[str, Any],
    sources: dict[str, Path],
) -> Path:
    center = SkyCoord(region["center"][0] * u.deg, region["center"][1] * u.deg)
    cropped = {}
    output_wcs = None
    for name, path in sources.items():
        with fits.open(path, memmap=False) as hdul:
            hdu = first_image_hdu(hdul)
            data, wcs = image_2d(hdu)
            cutout = Cutout2D(data, center, (4 * u.arcmin, 4 * u.arcmin), wcs=wcs, mode="partial", fill_value=np.nan)
            cropped[name] = (np.asarray(cutout.data, dtype=np.float32), hdu.header.get("BUNIT"))
            if output_wcs is None:
                output_wcs = cutout.wcs
    shapes = {array.shape for array, _ in cropped.values()}
    if len(shapes) != 1 or output_wcs is None:
        raise ValueError("eROSITA image/exposure/background crop shapes disagree")
    valid = np.isfinite(cropped["IMAGE"][0]) & np.isfinite(cropped["EXPOSURE"][0]) & (cropped["EXPOSURE"][0] > 0)
    header = output_wcs.to_header(relax=True)
    hdus: list[fits.hdu.base.ExtensionHDU] = [fits.PrimaryHDU()]
    for name in ("IMAGE", "EXPOSURE", "BACKGROUND"):
        h = header.copy()
        unit = cropped[name][1]
        if unit:
            h["BUNIT"] = unit
        h["SURVEY"] = "eROSITA eRASS1"
        h["BAND"] = "024"
        hdus.append(fits.ImageHDU(cropped[name][0], header=h, name=name))
    mask_header = header.copy()
    mask_header["MASKDEF"] = "1=finite image and positive finite exposure"
    hdus.append(fits.ImageHDU(valid.astype(np.uint8), header=mask_header, name="VALID_MASK"))
    target = products_root / "erosita" / region["id"] / "erass1-band024-4arcmin.fits"
    write_hdul(target, fits.HDUList(hdus))
    return target


def fetch_one_erosita_pixel(
    root: Path,
    cache: Path,
    products_root: Path,
    preview_root: Path,
    region: dict[str, Any],
    rubin_record: dict[str, Any],
    catalog_record: dict[str, Any],
    refresh: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    field_cache = cache / "erosita" / region["id"]
    try:
        search_path = field_cache / "skytile-search.json"
        search_payload, search_meta = request_cached(
            session, search_path, "GET", f"{EROSITA_API}/skyview/skytile_search_api/",
            refresh=refresh, params={"RA": region["center"][0], "DEC": region["center"][1], "RAD": FIELD_RADIUS_DEG},
            expected=("json",),
        )
        tiles = json.loads(search_payload).get("tiles", [])
        tiles = [tile for tile in tiles if tile.get("de_sky")]
        if not tiles:
            raise ValueError("skytile service returned no released eROSITA-DE tile")
        tile = min(tiles, key=lambda value: float(value.get("dist_deg", 999)))
        tile_id = str(tile["srvmap"]).zfill(6)
        sources: dict[str, Path] = {}
        artifacts = {}
        endpoints = {"IMAGE": "Image", "EXPOSURE": "ExposureMap", "BACKGROUND": "BackgrImage"}
        for name, endpoint in endpoints.items():
            gzip_path = field_cache / f"{tile_id}-{endpoint}-024.fits.gz"
            redirect_url = f"{EROSITA_API}/data/download_product/{int(tile_id)}/{endpoint}/024/"
            _, meta = request_cached(
                session, gzip_path, "GET", redirect_url, refresh=refresh, timeout=600,
                expected=("fits", "gzip", "octet-stream"),
            )
            fits_path = field_cache / f"{tile_id}-{endpoint}-024.fits"
            if refresh and fits_path.exists():
                fits_path.unlink()
            decompress_fits_gz(gzip_path, fits_path)
            sources[name] = fits_path
            artifacts[name] = artifact(root, gzip_path, source_url=redirect_url, role=f"archive-native eROSITA {endpoint} band 024")
        product_path = crop_erosita_product(root, products_root, region, sources)
        with fits.open(product_path, memmap=False, checksum=True) as hdul:
            image_data, image_wcs = image_2d(hdul["IMAGE"])
            valid = np.asarray(hdul["VALID_MASK"].data).astype(bool)
            units = {name: hdul[name].header.get("BUNIT") for name in ("IMAGE", "EXPOSURE", "BACKGROUND")}
            if not image_wcs.has_celestial or not np.any(valid):
                raise ValueError("derived eROSITA product lacks valid exposed pixels")
        display = display_alignment(
            root, preview_root, rubin_record, product_path,
            region_id=region["id"], survey_id="erosita-erass1", external_hdu="IMAGE", external_mask_hdu="VALID_MASK"
        )
        art = artifact(root, product_path, source_url=f"{EROSITA_API}/data/download_product/{int(tile_id)}/Image/024/", role="4 arcmin same-grid crop of eROSITA image/exposure/background maps")
        blockers = [
            "background and exposure maps are retained but no source-specific photometry has been performed",
            "PSF matching", "cross-band unit mismatch", "Poisson/statistical model QA",
        ]
        product = normalized_product(
            region, survey_id="erosita-erass1", survey_name="eROSITA", family="high-energy",
            release="eROSITA-DE eRASS1 DR1", product_type="image-exposure-background", status="available",
            science_ready=True, display_ready=True, observable="0.2–2.3 keV band 024", unit=units["IMAGE"],
            provenance=[
                f"{EROSITA_API}/skyview/skytile_search_api/",
                f"{EROSITA_API}/data/download_product/{int(tile_id)}/Image/024/",
                "https://erosita.mpe.mpg.de/dr1/AllSkySurveyData_dr1/",
            ], checksum=art["sha256"], blockers=blockers, display=display,
            details={"skyTile": tile_id, "units": units, "validFraction": float(valid.mean()), "sourceCount": catalog_record["sourceCount"]},
        )
        evidence = {
            "regionId": region["id"], "tract": region["tract"], "status": "available", "skyTile": tile,
            "skytileSearchArtifact": artifact(root, search_path, source_url=search_meta["requestUrl"], role="eROSITA skytile API response"),
            "sourceArtifacts": artifacts, "productArtifact": art, "units": units, "display": display,
        }
        return product, evidence
    except Exception as error:
        product = normalized_product(
            region, survey_id="erosita-erass1", survey_name="eROSITA", family="high-energy",
            release="eROSITA-DE eRASS1 DR1", product_type="image-exposure-background", status="error",
            science_ready=False, display_ready=False, observable="0.2–2.3 keV band 024", unit=None,
            provenance=[f"{EROSITA_API}/skyview/skytile_search_api/", "https://erosita.mpe.mpg.de/dr1/AllSkySurveyData_dr1/"],
            checksum=None, blockers=[str(error)],
        )
        return product, {"regionId": region["id"], "tract": region["tract"], "status": "error", "error": str(error)}


def fetch_erosita_pixels(
    root: Path,
    cache: Path,
    products_root: Path,
    preview_root: Path,
    regions: list[dict[str, Any]],
    rubin: dict[int, dict[str, Any]],
    catalog_by_region: dict[str, dict[str, Any]],
    refresh: bool,
    maximum: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    covered = [r for r in regions if catalog_by_region[r["id"]]["covered"]][:maximum]
    products = []
    evidence = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = {
            executor.submit(
                fetch_one_erosita_pixel, root, cache, products_root, preview_root, region,
                rubin[int(region["tract"])], catalog_by_region[region["id"]], refresh
            ): region
            for region in covered
        }
        for future in as_completed(jobs):
            product, record = future.result()
            products.append(product)
            evidence.append(record)
            print(f"[eROSITA pixels] {product['regionId']}: {product['status']}", flush=True)
    products.sort(key=lambda item: item["tract"])
    evidence.sort(key=lambda item: item.get("tract", 0))
    return products, evidence


def public_redaction(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"path", "sourceUrl", "requestUrl", "datasetId", "obsCore", "query"}:
                continue
            result[key] = public_redaction(item)
        return result
    if isinstance(value, list):
        return [public_redaction(item) for item in value]
    return value


def build_counts(products: list[dict[str, Any]]) -> dict[str, Any]:
    status_values = Counter(item["status"] for item in products)
    pixel = [item for item in products if "image" in item["productType"]]
    catalog = [item for item in products if "catalog" in item["productType"]]
    def status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
        values = Counter(item["status"] for item in items)
        return {key: int(values.get(key, 0)) for key in ("available", "none", "not-fetched", "error")}
    return {
        "productCount": len(products),
        "status": {key: int(status_values.get(key, 0)) for key in ("available", "none", "not-fetched", "error")},
        "productType": dict(sorted(Counter(item["productType"] for item in products).items())),
        "survey": dict(sorted(Counter(item["surveyId"] for item in products).items())),
        "family": dict(sorted(Counter(item["family"] for item in products).items())),
        "pixelProducts": len(pixel),
        "pixelStatus": status_counts(pixel),
        "pixelAvailable": sum(item["status"] == "available" for item in pixel),
        "catalogProducts": len(catalog),
        "catalogStatus": status_counts(catalog),
        "catalogAvailable": sum(item["status"] == "available" for item in catalog),
        "scienceReady": sum(bool(item["scienceReady"]) for item in products),
        "displayReady": sum(bool(item["displayReady"]) for item in products),
        "comparisonReady": sum(bool(item["comparisonReady"]) for item in products),
        "uniqueRegionsWithAvailablePixels": len({item["regionId"] for item in products if "image" in item["productType"] and item["status"] == "available"}),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--max-vlass", type=int, default=49)
    parser.add_argument("--max-erosita-pixels", type=int, default=5)
    args = parser.parse_args()

    results = root / "pipeline/results/radio-xray-hi"
    cache = results / "cache"
    products_root = results / "products"
    public_manifest = root / "public/data/layers/radio-xray-hi/manifest.json"
    preview_root = root / "public/layer-previews/radio-xray-hi"
    for directory in (results, cache, products_root, public_manifest.parent, preview_root):
        directory.mkdir(parents=True, exist_ok=True)

    selected_payload = json.loads((root / "public/data/coverage/selected-regions.json").read_text(encoding="utf-8"))
    regions = selected_payload["regions"]
    rubin = load_rubin(root)
    if len(regions) != 50 or len(rubin) != 50:
        raise RuntimeError(f"expected 50 selected regions and 50 Rubin products, got {len(regions)} and {len(rubin)}")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    products: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}

    lotss_products, evidence["lotss"] = fetch_lotss(session, root, cache, preview_root, regions, rubin, args.refresh)
    products.extend(lotss_products)
    vlass_products, evidence["vlass"] = fetch_vlass(
        session, root, cache, preview_root, regions, rubin, args.refresh, args.max_vlass
    )
    products.extend(vlass_products)
    hipass_products, evidence["hipass"] = fetch_hipass(
        session, root, cache, products_root, preview_root, regions, rubin, args.refresh
    )
    products.extend(hipass_products)
    erosita_catalog_products, evidence["erositaCatalog"], catalog_by_region = fetch_erosita_catalogs(
        session, root, cache, regions, args.refresh
    )
    products.extend(erosita_catalog_products)
    erosita_pixel_products, evidence["erositaPixels"] = fetch_erosita_pixels(
        root, cache, products_root, preview_root, regions, rubin, catalog_by_region,
        args.refresh, args.max_erosita_pixels
    )
    products.extend(erosita_pixel_products)

    products.sort(key=lambda item: (item["tract"], item["surveyId"], item["productType"]))
    local = {
        "schemaVersion": SCHEMA,
        "generatedAt": now(),
        "selection": {"source": "public/data/coverage/selected-regions.json", "regionCount": len(regions)},
        "policy": {
            "cacheFirst": True,
            "boundedRequests": {"vlassPixelMaximum": args.max_vlass, "erositaPixelMaximum": args.max_erosita_pixels},
            "comparisonSemantics": "positional co-display only; cross-band subtraction and flux ratios prohibited",
            "catalogSemantics": "an empty released catalogue search is a non-detection, not an error or zero flux",
        },
        "counts": build_counts(products),
        "products": products,
        "evidence": evidence,
    }
    atomic_json(results / "manifest.json", local)
    public = public_redaction(local)
    public["provenancePolicy"] = (
        "Public output retains stable archive/documentation URLs and checksums; local cache paths, CADC dataset IDs, "
        "request URLs, and signed redirects are intentionally omitted."
    )
    atomic_json(public_manifest, public)
    print(json.dumps(local["counts"], indent=2), flush=True)
    print(f"Local manifest: {results / 'manifest.json'}", flush=True)
    print(f"Public manifest: {public_manifest}", flush=True)


if __name__ == "__main__":
    main()
