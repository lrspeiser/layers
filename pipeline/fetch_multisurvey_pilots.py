#!/usr/bin/env python3
"""Acquire reproducible multi-survey evidence for three Rubin pilot fields.

The runner deliberately distinguishes archive coverage, catalogue detections,
and quantitative image products. Raw public-service responses are cached under
``pipeline/results/multisurvey-pilots/cache``. The public tree receives compact
manifests and derived previews only. Display products are never promoted to
science-ready inputs, and an empty cone search is recorded as a valid ``none``
result rather than fabricated evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import shutil
import tempfile
import time
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import requests
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from reproject import reproject_interp


FIELDS = (
    {"id": "ugc00191", "name": "UGC00191", "raDeg": 5.02167, "decDeg": 10.88},
    {"id": "ugc00634", "name": "UGC00634", "raDeg": 15.354878, "decDeg": 7.625991},
    {"id": "ugc00891", "name": "UGC00891", "raDeg": 20.32875, "decDeg": 12.41194},
)
USER_AGENT = "Layers-multisurvey-pilots/1.0 (+https://rubin-light-atlas.vercel.app/)"
CATALOG_RADIUS_DEG = 0.1
ZTF_RADIUS_DEG = 0.003
LOTSS_CUTOUT_ARCMIN = 5.0
SCHEMA = "layers-multisurvey-pilots-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def request_cached(
    session: requests.Session,
    path: Path,
    method: str,
    url: str,
    *,
    refresh: bool,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: int = 180,
    expected: tuple[str, ...] = (),
) -> tuple[bytes, dict[str, Any]]:
    request = requests.Request(method, url, params=params, data=data).prepare()
    final_url = request.url or url
    if path.is_file() and path.stat().st_size > 0 and not refresh:
        return path.read_bytes(), {
            "sourceUrl": final_url,
            "retrievedAt": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "cacheHit": True,
            "responseContentType": None,
        }
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = session.request(method, url, params=params, data=data, timeout=timeout)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if expected and not any(value in content_type.lower() for value in expected):
                snippet = response.content[:300].decode("utf-8", errors="replace")
                raise RuntimeError(f"unexpected content type {content_type!r}: {snippet}")
            if not response.content:
                raise RuntimeError("empty response")
            atomic_write(path, response.content)
            return response.content, {
                "sourceUrl": response.url,
                "retrievedAt": utc_now(),
                "cacheHit": False,
                "responseContentType": content_type,
            }
        except Exception as error:
            last_error = error
            if attempt < 3:
                time.sleep(min(2 ** (attempt + 1), 8))
    raise RuntimeError(f"{type(last_error).__name__}: {last_error}")


def artifact(root: Path, path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        **meta,
        "cachePath": relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def votable_rows(payload: bytes) -> Table:
    return Table.read(io.BytesIO(payload), format="votable")


def table_units(table: Table) -> dict[str, str]:
    result: dict[str, str] = {}
    for column in table.itercols():
        if column.unit is not None:
            result[column.name] = str(column.unit)
    return result


def gaia(session: requests.Session, root: Path, cache: Path, field: dict[str, Any], refresh: bool) -> dict[str, Any]:
    query = (
        "SELECT source_id,ra,dec,parallax,parallax_error,pmra,pmdec,ref_epoch,"
        "phot_g_mean_mag,phot_bp_mean_mag,phot_rp_mean_mag,ruwe "
        "FROM gaiadr3.gaia_source WHERE 1=CONTAINS(POINT('ICRS',ra,dec),"
        f"CIRCLE('ICRS',{field['raDeg']:.8f},{field['decDeg']:.8f},{CATALOG_RADIUS_DEG:.6f})) "
        "AND phot_g_mean_mag IS NOT NULL"
    )
    path = cache / "gaia-dr3.csv"
    endpoints = (
        "https://gea.esac.esa.int/tap-server/tap/sync",
        "https://gaia.ari.uni-heidelberg.de/tap/sync",
    )
    errors = []
    payload = b""
    meta: dict[str, Any] = {}
    used = ""
    for endpoint in endpoints:
        try:
            payload, meta = request_cached(
                session,
                path,
                "POST",
                endpoint,
                refresh=refresh,
                data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query},
                expected=("csv", "text/plain"),
            )
            used = endpoint if not meta["cacheHit"] else "cache (query provenance retained below)"
            break
        except Exception as error:
            errors.append(f"{endpoint}: {error}")
    if not payload:
        raise RuntimeError("; ".join(errors))
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    moving = sum(1 for row in rows if row.get("pmra") and row.get("pmdec"))
    return {
        "dataset": "gaia-dr3",
        "release": "Gaia DR3",
        "status": "available" if rows else "none",
        "coverage": True,
        "recordCount": len(rows),
        "sourcesWithProperMotion": moving,
        "query": query,
        "endpointUsed": used,
        "artifact": artifact(root, path, meta),
        "units": {"ra": "deg", "dec": "deg", "parallax": "mas", "pmra": "mas/yr", "pmdec": "mas/yr", "photometry": "mag"},
        "wcs": "ICRS catalogue coordinates; no raster WCS",
        "readiness": "catalogue evidence; requires epoch propagation and cross-match QA",
        "caveats": ["Gaia is not an image layer.", "A cone source is not automatically associated with the galaxy."],
        "documentation": ["https://www.cosmos.esa.int/web/gaia/dr3", "https://gea.esac.esa.int/archive/"],
    }


def ztf(session: requests.Session, root: Path, cache: Path, field: dict[str, Any], refresh: bool) -> dict[str, Any]:
    metadata_url = "https://irsa.ipac.caltech.edu/ibe/search/ztf/products/ref"
    params = {
        "POS": f"{field['raDeg']:.8f},{field['decDeg']:.8f}",
        "SIZE": f"{2 * CATALOG_RADIUS_DEG:.6f}",
        "INTERSECT": "OVERLAPS",
        "ct": "csv",
    }
    metadata_path = cache / "ztf-reference-metadata.csv"
    metadata_payload, metadata_meta = request_cached(
        session, metadata_path, "GET", metadata_url, refresh=refresh, params=params, expected=("csv",)
    )
    metadata_rows = list(csv.DictReader(io.StringIO(metadata_payload.decode("utf-8"))))
    lc_url = "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves"
    lc_params = {
        "POS": f"CIRCLE {field['raDeg']:.8f} {field['decDeg']:.8f} {ZTF_RADIUS_DEG:.6f}",
        "NOBS_MIN": "3",
        "BAD_CATFLAGS_MASK": "32768",
        "FORMAT": "csv",
    }
    lc_path = cache / "ztf-lightcurves.csv"
    lightcurve_error = None
    lightcurve_rows: list[dict[str, str]] = []
    lc_meta: dict[str, Any] | None = None
    try:
        lc_payload, lc_meta = request_cached(
            session, lc_path, "GET", lc_url, refresh=refresh, params=lc_params, timeout=240, expected=("csv",)
        )
        lightcurve_rows = list(csv.DictReader(io.StringIO(lc_payload.decode("utf-8"))))
    except Exception as error:
        lightcurve_error = str(error)
    object_ids = {row.get("oid") for row in lightcurve_rows if row.get("oid")}
    filters = Counter(row.get("filtercode", "unknown") for row in lightcurve_rows)
    dates = [float(row["mjd"]) for row in lightcurve_rows if row.get("mjd")]
    status = "available" if metadata_rows or lightcurve_rows else ("error" if lightcurve_error else "none")
    artifacts = {"referenceMetadata": artifact(root, metadata_path, metadata_meta)}
    if lc_meta is not None and lc_path.is_file():
        artifacts["lightCurves"] = artifact(root, lc_path, lc_meta)
    return {
        "dataset": "ztf-dr24",
        "release": "ZTF public archive; DR24 requested",
        "status": status,
        "coverage": bool(metadata_rows),
        "referenceImageRecordCount": len(metadata_rows),
        "recordCount": len(lightcurve_rows),
        "lightCurveMeasurementCount": len(lightcurve_rows),
        "lightCurveObjectCount": len(object_ids),
        "filters": dict(sorted(filters.items())),
        "mjdRange": [min(dates), max(dates)] if dates else None,
        "queries": {"referenceMetadata": params, "lightCurves": lc_params},
        "artifacts": artifacts,
        "units": {"mjd": "day", "mag": "mag", "magerr": "mag"},
        "wcs": "Reference-image metadata footprints are ICRS; this runner does not download ZTF pixels.",
        "readiness": "time-series evidence when rows exist; apply full ZTF quality masks before inference",
        "releaseProvenanceVerified": False,
        "caveats": [
            "The IRSA light-curve response does not stamp a data-release number; do not claim these rows are specifically DR24.",
            "The bounded light-curve cone is 10.8 arcsec radius and is not a complete galaxy-wide variability census.",
            *( [f"Light-curve query failed while coverage metadata succeeded: {lightcurve_error}"] if lightcurve_error else [] ),
        ],
        "documentation": [
            "https://irsa.ipac.caltech.edu/docs/program_interface/ztf_api.html",
            "https://irsa.ipac.caltech.edu/docs/program_interface/ztf_lightcurve_api.html",
            "https://irsa.ipac.caltech.edu/data/ZTF/docs/releases/dr24/ztf_release_notes_dr24.pdf",
        ],
    }


def erosita(session: requests.Session, root: Path, cache: Path, field: dict[str, Any], refresh: bool) -> dict[str, Any]:
    cone_url = "https://erosita.mpe.mpg.de/erodat/catalogue/SCS"
    params = {"CAT": "DR1_Main", "RA": field["raDeg"], "DEC": field["decDeg"], "SR": CATALOG_RADIUS_DEG, "VERB": 3}
    cone_path = cache / "erosita-erass1-main.vot"
    payload, meta = request_cached(
        session, cone_path, "GET", cone_url, refresh=refresh, params=params, expected=("xml", "votable")
    )
    table = votable_rows(payload)
    upper_url = "https://erosita.mpe.mpg.de/erodat/upperlimit/service"
    upper_params = {"ra": field["raDeg"], "dec": field["decDeg"], "band": "024", "dr_survey": "DR1_eRASS1"}
    upper_path = cache / "erosita-erass1-upper-limit.json"
    upper_payload, upper_meta = request_cached(
        session, upper_path, "GET", upper_url, refresh=refresh, params=upper_params, expected=("json",)
    )
    upper = json.loads(upper_payload)
    covered = bool(upper.get("de_sky"))
    status = "available" if len(table) else ("none" if covered else "none")
    return {
        "dataset": "erosita-erass1",
        "release": "eROSITA-DE eRASS1 DR1 Main v1.2",
        "status": status,
        "coverage": covered,
        "recordCount": len(table),
        "upperLimitBand": "024 (0.2-2.3 keV)",
        "upperLimit": upper,
        "queries": {"coneSearch": params, "upperLimit": upper_params},
        "artifacts": {"coneSearch": artifact(root, cone_path, meta), "upperLimit": artifact(root, upper_path, upper_meta)},
        "units": {
            key: value
            for key, value in table_units(table).items()
            if key in {"ra", "dec", "mjd", "ext", "ml_cts_1", "ml_rate_1", "ml_flux_1", "ml_exp_1"}
        },
        "wcs": "ICRS catalogue positions; upper-limit lookup at the exact field center",
        "readiness": "catalogue/upper-limit evidence; not an X-ray raster layer",
        "caveats": [
            "The public eROSITA-DE footprint covers only its released sky hemisphere; de_sky=false means no DR1 constraint here.",
            "No cone match is a non-detection, not proof of zero X-ray flux.",
        ],
        "documentation": ["https://erosita.mpe.mpg.de/erodat/apis/", "https://erosita.mpe.mpg.de/dr1/AllSkySurveyData_dr1/Catalogues_dr1/"],
    }


def vlass(session: requests.Session, root: Path, cache: Path, field: dict[str, Any], refresh: bool) -> dict[str, Any]:
    endpoint = "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus/sync"
    query = (
        "SELECT TOP 100 obs_collection,obs_id,obs_publisher_did,calib_level,access_url,"
        "access_format,s_fov,t_min,t_max FROM ivoa.ObsCore WHERE "
        f"INTERSECTS(s_region,CIRCLE('ICRS',{field['raDeg']:.8f},{field['decDeg']:.8f},{CATALOG_RADIUS_DEG:.6f}))=1 "
        "AND obs_collection='VLASS'"
    )
    path = cache / "vlass-obscore.csv"
    payload, meta = request_cached(
        session,
        path,
        "POST",
        endpoint,
        refresh=refresh,
        data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query, "MAXREC": "100"},
        timeout=240,
        expected=("csv",),
    )
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    levels = Counter(row.get("calib_level", "unknown") for row in rows)
    return {
        "dataset": "vlass",
        "release": "CADC public VLASS holdings queried live",
        "status": "available" if rows else "none",
        "coverage": bool(rows),
        "recordCount": len(rows),
        "calibrationLevels": dict(sorted(levels.items())),
        "query": query,
        "artifact": artifact(root, path, meta),
        "units": {"s_fov": "deg", "t_min": "MJD", "t_max": "MJD"},
        "wcs": "ObsCore s_region intersection in ICRS; DataLink URLs retained in response",
        "readiness": "coverage metadata only; resolve DataLink/SODA and validate a FITS cutout before pixel analysis",
        "caveats": [
            "Quick Look images are QA/transient-search products and are not suitable for general precision photometry.",
            "This runner records CADC ObsCore/DataLink metadata but does not mislabel it as a downloaded image.",
        ],
        "documentation": ["https://science.nrao.edu/vlass/vlass-data", "https://www4.cadc.hia.nrc.gc.ca/en/doc/tap/"],
    }


def lotss(session: requests.Session, root: Path, cache: Path, field: dict[str, Any], refresh: bool) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for release in ("DR3", "DR2"):
        slug = release.lower()
        cone_url = f"https://vo.astron.nl/lotss_{slug}/q/src_cone/scs.xml"
        cone_params = {"RA": field["raDeg"], "DEC": field["decDeg"], "SR": CATALOG_RADIUS_DEG, "VERB": 3, "MAXREC": 5000}
        cone_path = cache / f"lotss-{slug}-sources.vot"
        cutout_url = f"https://lofar-surveys.org/{slug}-cutout.fits"
        cutout_params = {"pos": f"{field['raDeg']:.8f} {field['decDeg']:.8f}", "size": LOTSS_CUTOUT_ARCMIN}
        cutout_path = cache / f"lotss-{slug}-cutout.fits"
        try:
            cone_payload, cone_meta = request_cached(
                session, cone_path, "GET", cone_url, refresh=refresh, params=cone_params, expected=("xml", "votable")
            )
            table = votable_rows(cone_payload)
            cutout_payload, cutout_meta = request_cached(
                session, cutout_path, "GET", cutout_url, refresh=refresh, params=cutout_params, timeout=240, expected=("fits",)
            )
            if not cutout_payload.startswith(b"SIMPLE"):
                raise RuntimeError("cutout response is not a primary FITS image")
            with fits.open(cutout_path, memmap=False) as hdul:
                header = hdul[0].header
                data = np.asarray(hdul[0].data)
                wcs_present = all(key in header for key in ("CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2"))
                bunit = header.get("BUNIT")
                shape = list(data.shape)
            return {
                "dataset": "lotss",
                "release": f"LoTSS {release}",
                "status": "available",
                "coverage": True,
                "recordCount": len(table),
                "queries": {"coneSearch": cone_params, "cutout": cutout_params},
                "artifacts": {"coneSearch": artifact(root, cone_path, cone_meta), "fitsCutout": artifact(root, cutout_path, cutout_meta)},
                "units": {**table_units(table), "rasterBunit": bunit},
                "wcs": {"present": wcs_present, "shape": shape, "ctype1": header.get("CTYPE1"), "ctype2": header.get("CTYPE2")},
                "readiness": "archive-native FITS candidate; WCS checked, quantitative use still requires beam/noise/mask QA",
                "caveats": [
                    "Catalogue associations require positional and morphology-aware cross-matching.",
                    "The cutout may be truncated at a mosaic edge; no claim of uniform coverage is made.",
                ],
                "documentation": [f"https://lofar-surveys.org/{slug}_release.html", "https://lofar-surveys.org/cutout_api_details.html"],
                "_previewFits": relative(root, cutout_path),
                "attempts": attempts,
            }
        except Exception as error:
            attempts.append({"release": release, "status": "error-or-no-coverage", "error": str(error)})
    return {
        "dataset": "lotss",
        "release": "LoTSS DR3/DR2",
        "status": "none",
        "coverage": False,
        "recordCount": 0,
        "units": None,
        "wcs": None,
        "readiness": "no authentic FITS cutout returned",
        "caveats": ["Both current DR3 and DR2 services returned no usable FITS cutout or errored; see attempts."],
        "documentation": ["https://lofar-surveys.org/cutout_api_details.html"],
        "attempts": attempts,
    }


def stretch_image(data: np.ndarray) -> np.ndarray:
    array = np.squeeze(np.asarray(data, dtype=float))
    while array.ndim > 2:
        array = array[0]
    valid = np.isfinite(array)
    if not np.any(valid):
        raise ValueError("FITS image contains no finite pixels")
    low, high = np.nanpercentile(array[valid], (1, 99.5))
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        low, high = float(np.nanmin(array[valid])), float(np.nanmax(array[valid]))
    scaled = np.clip((array - low) / max(high - low, np.finfo(float).eps), 0, 1)
    scaled = np.arcsinh(8 * scaled) / np.arcsinh(8)
    scaled[~valid] = 0
    return np.flipud((scaled * 255).astype(np.uint8))


def write_preview(root: Path, public_preview: Path, field: dict[str, Any], record: dict[str, Any]) -> str | None:
    source = record.pop("_previewFits", None)
    if not source:
        return None
    from PIL import Image

    source_path = root / source
    with fits.open(source_path, memmap=False) as hdul:
        preview = stretch_image(hdul[0].data)
    path = public_preview / f"{field['id']}-lotss-{record['release'].split()[-1].lower()}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(preview, mode="L").save(path, optimize=True)
    return "/" + relative(root / "public", path)


def write_fits_atomic(path: Path, hdul: fits.HDUList) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".fits", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        hdul.writeto(temporary, overwrite=True, checksum=True)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_gray_preview(path: Path, data: np.ndarray) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(stretch_image(data), mode="L").save(path, optimize=True)


def build_common_grids(
    root: Path,
    results_dir: Path,
    public_data: Path,
    public_preview: Path,
) -> dict[str, Any]:
    """Reproject Rubin i-band display pixels onto each native LoTSS DR3 grid.

    This is an astrometric/display operation only. Interpolation does not
    conserve Rubin flux per output pixel, the PSFs are not matched, and nJy and
    Jy/beam are deliberately never subtracted or ratioed.
    """

    records = []
    common_root = results_dir / "common-grid"
    for field in FIELDS:
        field_manifest_path = results_dir / f"{field['id']}.json"
        rubin_path = root / "pipeline" / "output" / "dp2-sparc" / field["id"] / "rubin_i.fits"
        rubin_provenance_path = rubin_path.parent / "edp2_provenance.json"
        blockers = []
        if not field_manifest_path.is_file():
            blockers.append(f"missing multisurvey manifest: {relative(root, field_manifest_path)}")
        if not rubin_path.is_file():
            blockers.append(f"missing Rubin i-band mosaic: {relative(root, rubin_path)}")
        if not rubin_provenance_path.is_file():
            blockers.append(f"missing Rubin provenance: {relative(root, rubin_provenance_path)}")
        lotss_record = None
        lotss_path = None
        if field_manifest_path.is_file():
            field_manifest = json.loads(field_manifest_path.read_text(encoding="utf-8"))
            lotss_record = next((item for item in field_manifest["datasets"] if item["dataset"] == "lotss"), None)
            if not lotss_record or lotss_record.get("status") != "available":
                blockers.append("no authentic available LoTSS FITS cutout in pilot manifest")
            else:
                lotss_path = root / lotss_record["artifacts"]["fitsCutout"]["cachePath"]
                if not lotss_path.is_file():
                    blockers.append(f"missing retained LoTSS FITS: {relative(root, lotss_path)}")
        if blockers:
            record = {"field": field, "status": "blocked", "blockers": blockers}
            records.append(record)
            continue

        rubin_provenance = json.loads(rubin_provenance_path.read_text(encoding="utf-8"))
        expected_rubin_hash = rubin_provenance.get("bands", {}).get("i", {}).get("mosaic_sha256")
        actual_rubin_hash = sha256(rubin_path)
        if expected_rubin_hash and actual_rubin_hash != expected_rubin_hash:
            raise RuntimeError(f"{field['id']}: Rubin i-band hash does not match retained DP2 provenance")

        with fits.open(rubin_path, memmap=False) as rubin_hdul, fits.open(lotss_path, memmap=False) as lotss_hdul:
            rubin_hdu = rubin_hdul["IMAGE"]
            rubin_data = np.asarray(rubin_hdu.data, dtype=np.float64)
            rubin_wcs = WCS(rubin_hdu.header).celestial
            lotss_hdu = lotss_hdul[0]
            lotss_data = np.squeeze(np.asarray(lotss_hdu.data, dtype=np.float64))
            while lotss_data.ndim > 2:
                lotss_data = lotss_data[0]
            lotss_wcs = WCS(lotss_hdu.header).celestial
            if not rubin_wcs.has_celestial or not lotss_wcs.has_celestial:
                raise RuntimeError(f"{field['id']}: one of the retained inputs lacks a celestial WCS")
            reprojected, footprint = reproject_interp(
                (rubin_data, rubin_wcs),
                lotss_wcs,
                shape_out=lotss_data.shape,
                order="bilinear",
                return_footprint=True,
            )
            common = (footprint > 0) & np.isfinite(reprojected) & np.isfinite(lotss_data)
            wcs_header = lotss_wcs.to_header(relax=True)
            rubin_bunit = rubin_hdu.header.get("BUNIT")
            lotss_bunit = lotss_hdu.header.get("BUNIT")
            input_shapes = {"rubin": list(rubin_data.shape), "lotss": list(lotss_data.shape)}
            pixel_scales = {
                "rubinArcsec": [float(value * 3600) for value in proj_plane_pixel_scales(rubin_wcs)],
                "lotssArcsec": [float(value * 3600) for value in proj_plane_pixel_scales(lotss_wcs)],
            }

        output_dir = common_root / field["id"]
        aligned_path = output_dir / "rubin-i-on-lotss-dr3.fits"
        coverage_path = output_dir / "common-coverage-mask.fits"
        aligned_header = wcs_header.copy()
        aligned_header["BUNIT"] = (rubin_bunit or "unknown", "Rubin input unit")
        aligned_header["SURVEY"] = "Rubin DP2"
        aligned_header["FILTER"] = "i"
        aligned_header["REFGRID"] = "LoTSS DR3"
        aligned_header["REPROJ"] = "bilinear"
        aligned_header["SCIENCE"] = False
        aligned_header.add_history("Display alignment only; no PSF or bandpass matching.")
        aligned_header.add_history("Do not use this interpolated raster for photometry or cross-band subtraction.")
        write_fits_atomic(aligned_path, fits.HDUList([fits.PrimaryHDU(reprojected.astype(np.float32), header=aligned_header)]))

        coverage_header = wcs_header.copy()
        coverage_header["MASKTYPE"] = "COMMON"
        coverage_header["MASKDEF"] = "1=finite Rubin and LoTSS support"
        coverage_header["SCIENCE"] = False
        write_fits_atomic(coverage_path, fits.HDUList([fits.PrimaryHDU(common.astype(np.uint8), header=coverage_header)]))

        rubin_preview = public_preview / f"{field['id']}-rubin-i-on-lotss-dr3.png"
        lotss_preview = public_preview / f"{field['id']}-lotss-dr3-common-grid.png"
        coverage_preview = public_preview / f"{field['id']}-rubin-lotss-common-coverage.png"
        overlay_preview = public_preview / f"{field['id']}-rubin-lotss-position-overlay.png"
        write_gray_preview(rubin_preview, reprojected)
        write_gray_preview(lotss_preview, np.where(common, lotss_data, np.nan))
        from PIL import Image

        Image.fromarray(np.flipud(common.astype(np.uint8) * 255), mode="L").save(coverage_preview, optimize=True)
        optical = stretch_image(reprojected).astype(np.float32) / 255.0
        radio = stretch_image(np.where(common, lotss_data, np.nan)).astype(np.float32) / 255.0
        overlay = np.stack((optical, 0.45 * optical + 0.65 * radio, radio), axis=-1)
        overlay[~np.flipud(common)] = 0
        Image.fromarray((np.clip(overlay, 0, 1) * 255).astype(np.uint8), mode="RGB").save(overlay_preview, optimize=True)

        output_artifacts = {
            "rubinAlignedFits": {"path": relative(root, aligned_path), "sha256": sha256(aligned_path), "bytes": aligned_path.stat().st_size},
            "coverageMaskFits": {"path": relative(root, coverage_path), "sha256": sha256(coverage_path), "bytes": coverage_path.stat().st_size},
        }
        previews = {
            "rubinAligned": "/" + relative(root / "public", rubin_preview),
            "lotssNativeCommonGrid": "/" + relative(root / "public", lotss_preview),
            "commonCoverage": "/" + relative(root / "public", coverage_preview),
            "positionOverlay": "/" + relative(root / "public", overlay_preview),
        }
        record = {
            "schemaVersion": SCHEMA,
            "generatedAt": utc_now(),
            "field": field,
            "status": "available",
            "operation": "Rubin DP2 i-band IMAGE bilinearly reprojected onto native LoTSS DR3 celestial grid",
            "readiness": "display-aligned; not photometrically comparable",
            "outputShape": list(lotss_data.shape),
            "commonCoveragePixelCount": int(common.sum()),
            "commonCoverageFraction": float(common.mean()),
            "supportFractions": {
                "rubinFiniteOnOutputGrid": float(np.isfinite(reprojected).mean()),
                "lotssFiniteOnOutputGrid": float(np.isfinite(lotss_data).mean()),
            },
            "pixelScales": pixel_scales,
            "inputs": {
                "rubin": {
                    "path": relative(root, rubin_path),
                    "sha256": actual_rubin_hash,
                    "hdu": "IMAGE",
                    "band": "i",
                    "unit": rubin_bunit,
                    "wcs": "RA---TAN / DEC--TAN",
                    "shape": input_shapes["rubin"],
                    "release": rubin_provenance.get("release"),
                    "datasetType": rubin_provenance.get("dataset_type"),
                    "publisherDatasetIds": rubin_provenance.get("input_dataset_ids", []),
                    "provenancePath": relative(root, rubin_provenance_path),
                },
                "lotss": {
                    "path": relative(root, lotss_path),
                    "sha256": sha256(lotss_path),
                    "unit": lotss_bunit,
                    "wcs": "RA---SIN / DEC--SIN",
                    "shape": input_shapes["lotss"],
                    "release": lotss_record["release"],
                    "sourceUrl": lotss_record["artifacts"]["fitsCutout"]["sourceUrl"],
                },
            },
            "outputs": output_artifacts,
            "previews": previews,
            "previewSemantics": {
                "rubinAligned": "independently stretched Rubin i-band display pixels",
                "lotssNativeCommonGrid": "independently stretched LoTSS 144 MHz display pixels restricted to common finite coverage",
                "commonCoverage": "white only where both retained rasters have finite pixel support",
                "positionOverlay": "orange/cyan positional overlay with independent nonlinear stretches; not a difference map",
            },
            "prohibitedClaims": [
                "No photometric difference, flux ratio, or missing-light claim is supported across nJy optical pixels and Jy/beam radio pixels.",
                "No PSF, beam, bandpass, pixel-area, or noise matching has been performed.",
                "Bilinear reprojection is not flux conserving and the aligned Rubin FITS must not be used for photometry.",
            ],
        }
        encoded = json.dumps(record, indent=2).encode("utf-8")
        atomic_write(output_dir / "manifest.json", encoded)
        atomic_write(public_data / f"{field['id']}-rubin-lotss-common-grid.json", encoded)
        records.append({
            "fieldId": field["id"],
            "status": record["status"],
            "commonCoverageFraction": record["commonCoverageFraction"],
            "manifest": f"/data/layers/multisurvey-pilots/{field['id']}-rubin-lotss-common-grid.json",
            "previews": previews,
        })

    summary = {
        "schemaVersion": SCHEMA,
        "generatedAt": utc_now(),
        "comparison": "Rubin DP2 i-band / LoTSS DR3 144 MHz common-grid display alignment",
        "availableCount": sum(item["status"] == "available" for item in records),
        "blockedCount": sum(item["status"] == "blocked" for item in records),
        "fields": records,
        "scienceClaimAllowed": False,
        "reason": "Astrometric co-display is valid; cross-wavelength pixel subtraction or photometric ranking is not.",
    }
    encoded = json.dumps(summary, indent=2).encode("utf-8")
    atomic_write(common_root / "summary.json", encoded)
    atomic_write(public_data / "rubin-lotss-common-grid-summary.json", encoded)
    return summary


def validate_common_grids(root: Path, results_dir: Path, public_data: Path) -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    counts = {"commonGridFieldsChecked": 0, "commonGridArtifactsChecked": 0, "commonGridWcsMatchesChecked": 0}
    summary_path = results_dir / "common-grid" / "summary.json"
    if not summary_path.is_file():
        return ["missing common-grid summary"], counts
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for item in summary.get("fields", []):
        if item.get("status") != "available":
            errors.append(f"{item.get('fieldId')}: common-grid build blocked")
            continue
        field_id = item["fieldId"]
        manifest_path = results_dir / "common-grid" / field_id / "manifest.json"
        public_path = public_data / f"{field_id}-rubin-lotss-common-grid.json"
        if not manifest_path.is_file() or not public_path.is_file():
            errors.append(f"{field_id}: missing common-grid manifest")
            continue
        counts["commonGridFieldsChecked"] += 1
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for artifact_record in manifest["outputs"].values():
            counts["commonGridArtifactsChecked"] += 1
            path = root / artifact_record["path"]
            if not path.is_file() or sha256(path) != artifact_record["sha256"]:
                errors.append(f"{field_id}: missing or changed common-grid artifact")
        aligned = root / manifest["outputs"]["rubinAlignedFits"]["path"]
        lotss = root / manifest["inputs"]["lotss"]["path"]
        try:
            with fits.open(aligned, checksum=True, memmap=False) as a, fits.open(lotss, checksum=True, memmap=False) as b:
                awcs = WCS(a[0].header).celestial
                bwcs = WCS(b[0].header).celestial
                shape = a[0].data.shape
                samples = np.array([[0.0, 0.0], [shape[1] / 2, shape[0] / 2], [shape[1] - 1.0, shape[0] - 1.0]])
                a_world = np.asarray(awcs.pixel_to_world_values(samples[:, 0], samples[:, 1])).T
                b_world = np.asarray(bwcs.pixel_to_world_values(samples[:, 0], samples[:, 1])).T
                if not np.allclose(a_world, b_world, atol=1e-10, rtol=0):
                    errors.append(f"{field_id}: aligned and LoTSS WCS disagree")
                else:
                    counts["commonGridWcsMatchesChecked"] += 1
        except Exception as error:
            errors.append(f"{field_id}: common-grid FITS validation failed: {error}")
    return errors, counts


FETCHERS: tuple[Callable[..., dict[str, Any]], ...] = (gaia, ztf, erosita, vlass, lotss)


def validate(root: Path, results_dir: Path, public_data: Path) -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    counts = {"fieldsChecked": 0, "datasetsChecked": 0, "artifactsChecksumChecked": 0, "fitsWcsChecked": 0}
    summary_path = results_dir / "summary.json"
    if not summary_path.is_file():
        return [f"missing {summary_path}"], counts
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("schemaVersion") != SCHEMA:
        errors.append("summary schemaVersion mismatch")
    if summary.get("fieldCount") != len(FIELDS):
        errors.append("summary field count mismatch")
    for field in FIELDS:
        result_path = results_dir / f"{field['id']}.json"
        public_path = public_data / f"{field['id']}.json"
        if not result_path.is_file() or not public_path.is_file():
            errors.append(f"{field['id']}: missing result or public manifest")
            continue
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        counts["fieldsChecked"] += 1
        if {item.get("dataset") for item in payload.get("datasets", [])} != {"gaia-dr3", "ztf-dr24", "erosita-erass1", "vlass", "lotss"}:
            errors.append(f"{field['id']}: dataset set mismatch")
        for dataset in payload.get("datasets", []):
            counts["datasetsChecked"] += 1
            if dataset.get("status") not in {"available", "none", "error"}:
                errors.append(f"{field['id']}/{dataset.get('dataset')}: invalid status")
            serialized = json.dumps(dataset)
            if "sourceUrl" not in serialized and dataset.get("status") != "error":
                errors.append(f"{field['id']}/{dataset.get('dataset')}: no retained source URL")
            for key in ("artifact", "artifacts"):
                entry = dataset.get(key)
                values = [entry] if key == "artifact" and entry else list((entry or {}).values())
                for item in values:
                    counts["artifactsChecksumChecked"] += 1
                    path = root / item["cachePath"]
                    if not path.is_file():
                        errors.append(f"{field['id']}/{dataset.get('dataset')}: missing cache artifact")
                    elif sha256(path) != item["sha256"]:
                        errors.append(f"{field['id']}/{dataset.get('dataset')}: checksum mismatch")
        lotss_record = next((item for item in payload["datasets"] if item["dataset"] == "lotss"), None)
        if lotss_record and lotss_record.get("status") == "available":
            fits_path = root / lotss_record["artifacts"]["fitsCutout"]["cachePath"]
            try:
                with fits.open(fits_path, checksum=True, memmap=False) as hdul:
                    counts["fitsWcsChecked"] += 1
                    header = hdul[0].header
                    if not all(key in header for key in ("CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2")):
                        errors.append(f"{field['id']}/lotss: FITS lacks WCS")
            except Exception as error:
                errors.append(f"{field['id']}/lotss: invalid FITS: {error}")
    return errors, counts


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    results_dir = root / "pipeline" / "results" / "multisurvey-pilots"
    cache_root = results_dir / "cache"
    public_data = root / "public" / "data" / "layers" / "multisurvey-pilots"
    public_preview = root / "public" / "layer-previews" / "multisurvey-pilots"
    for directory in (results_dir, cache_root, public_data, public_preview):
        directory.mkdir(parents=True, exist_ok=True)

    if not args.validate_only:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        selected = {value.lower() for value in args.only}
        fields = [field for field in FIELDS if not selected or field["id"] in selected or field["name"].lower() in selected]
        status_counts: Counter[str] = Counter()
        field_summaries = []
        for field in fields:
            field_cache = cache_root / field["id"]
            field_cache.mkdir(parents=True, exist_ok=True)
            datasets = []
            for fetcher in FETCHERS:
                try:
                    record = fetcher(session, root, field_cache, field, args.refresh)
                except Exception as error:
                    record = {
                        "dataset": fetcher.__name__.replace("_", "-"),
                        "release": None,
                        "status": "error",
                        "coverage": None,
                        "recordCount": None,
                        "readiness": "unavailable because acquisition failed",
                        "caveats": [str(error)],
                    }
                preview = write_preview(root, public_preview, field, record)
                if preview:
                    record["preview"] = preview
                    record["previewReadiness"] = "display-only rendering derived from the retained archive FITS"
                datasets.append(record)
                status_counts[record["status"]] += 1
                print(f"[{field['id']}] {record['dataset']}: {record['status']} ({record.get('recordCount')})", flush=True)
            manifest = {
                "schemaVersion": SCHEMA,
                "generatedAt": utc_now(),
                "field": field,
                "search": {"catalogRadiusDeg": CATALOG_RADIUS_DEG, "ztfLightCurveRadiusDeg": ZTF_RADIUS_DEG, "lotssCutoutArcmin": LOTSS_CUTOUT_ARCMIN},
                "datasets": datasets,
                "interpretation": "available means authentic returned coverage/data; none means a valid empty/no-coverage result; error means the request could not be established.",
            }
            encoded = json.dumps(manifest, indent=2, allow_nan=False).encode("utf-8")
            atomic_write(results_dir / f"{field['id']}.json", encoded)
            atomic_write(public_data / f"{field['id']}.json", encoded)
            field_summaries.append({"id": field["id"], "name": field["name"], "datasets": {item["dataset"]: item["status"] for item in datasets}})
        summary = {
            "schemaVersion": SCHEMA,
            "generatedAt": utc_now(),
            "fieldCount": len(fields),
            "datasetCount": len(fields) * len(FETCHERS),
            "statusCounts": dict(sorted(status_counts.items())),
            "fields": field_summaries,
            "provenancePolicy": "Raw responses are immutable checksum-addressed evidence; no display-only image is science-ready by implication.",
        }
        encoded = json.dumps(summary, indent=2).encode("utf-8")
        atomic_write(results_dir / "summary.json", encoded)
        atomic_write(public_data / "summary.json", encoded)

        common_grid_summary = build_common_grids(root, results_dir, public_data, public_preview)
        print(
            f"Common-grid display alignment: {common_grid_summary['availableCount']} available, "
            f"{common_grid_summary['blockedCount']} blocked.",
            flush=True,
        )

    errors, counts = validate(root, results_dir, public_data)
    common_errors, common_counts = validate_common_grids(root, results_dir, public_data)
    errors.extend(common_errors)
    counts.update(common_counts)
    validation = {
        "schemaVersion": SCHEMA,
        "validatedAt": utc_now(),
        "ok": not errors,
        **counts,
        "checks": [
            "manifest schema and field count",
            "dataset state vocabulary",
            "retained source URLs",
            "artifact existence",
            "SHA-256 checksums",
            "LoTSS FITS readability and celestial WCS",
            "Rubin/LoTSS common-grid artifact checksums",
            "aligned Rubin and native LoTSS WCS equality at three sampled pixels",
        ],
        "errors": errors,
    }
    atomic_write(results_dir / "validation.json", json.dumps(validation, indent=2).encode("utf-8"))
    atomic_write(public_data / "validation.json", json.dumps(validation, indent=2).encode("utf-8"))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("Validation passed: manifests, dataset states, checksums, and available LoTSS FITS WCS are consistent.")


if __name__ == "__main__":
    main()
