#!/usr/bin/env python3
"""Acquire and extract released lensing/CMB pixels for the 50 selected DP2 regions.

The products made here are positional display products. They deliberately do not
subtract optical flux from convergence or CMB temperature maps. Product masks are
stored in a separate FITS plane and rendered separately from the science field.

Inputs are official release archives. Downloads are cached under the results tree;
the public manifest exposes publisher URLs and checksums, never local cache paths.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import shutil
import tarfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from astropy_healpix import HEALPix
from ducc0.sht.experimental import synthesis_general
from matplotlib import colormaps
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SELECTED = ROOT / "public/data/coverage/selected-regions.json"
RESOLUTION = ROOT / "public/data/coverage/large-footprint-resolution.json"
RUBIN_MANIFEST = ROOT / "pipeline/results/rubin-pixels-50/manifest.json"
ACT_MASK = ROOT / "pipeline/results/coverage/resolution-large/mask_act_dr6_lensing_v1_healpix_nside_4096_baseline.fits"

RESULTS = ROOT / "pipeline/results/lensing-cmb-pixels"
SOURCES = RESULTS / "sources"
PRODUCTS = RESULTS / "products"
PUBLIC_PREVIEWS = ROOT / "public/layer-previews/lensing-cmb"
PUBLIC_MANIFEST = ROOT / "public/data/layers/lensing-cmb/manifest.json"
DETAILED_MANIFEST = RESULTS / "manifest.json"

# The external products have native resolutions of roughly arcminutes, not
# Rubin's 0.2 arcsec.  A 64-pixel grid across four arcminutes retains their
# actual information without implying invented Rubin-scale resolution.
SIZE = 64
WIDTH_DEG = 4.0 / 60.0

SOURCES_SPEC = {
    "act-alm": {
        "file": "kappa_alm_data_act_dr6_lensing_v1_baseline.fits",
        "url": "https://portal.nersc.gov/project/act/dr6_lensing_v1/maps/baseline/kappa_alm_data_act_dr6_lensing_v1_baseline.fits",
        "expectedBytes": 96079680,
    },
    "planck": {
        "file": "COM_Lensing_4096_R3.00.tgz",
        "url": "https://irsa.ipac.caltech.edu/data/Planck/release_3/all-sky-maps/maps/component-maps/lensing/COM_Lensing_4096_R3.00.tgz",
        "expectedBytes": 723481156,
    },
    "des": {
        "file": "desy3_karmma_maps.zip",
        "url": "https://zenodo.org/api/records/10672062/files/desy3_karmma_maps.zip/content",
        "expectedBytes": 239642621,
    },
    "spt-map": {
        "file": "coadd_map_SFL_150GHz.fits.gz",
        "url": "https://pole.uchicago.edu/public/data/edfs25/coadd_map_SFL_150GHz.fits.gz",
        "expectedBytes": 35616936,
    },
    "spt-mask": {
        "file": "apod_mask_SFL_150GHz.fits.gz",
        "url": "https://pole.uchicago.edu/public/data/edfs25/apod_mask_SFL_150GHz.fits.gz",
        "expectedBytes": 7650767,
    },
    "act-mask": {
        "file": "mask_act_dr6_lensing_v1_healpix_nside_4096_baseline.fits",
        "url": "https://portal.nersc.gov/project/act/dr6_lensing_v1/maps/baseline/mask_act_dr6_lensing_v1_healpix_nside_4096_baseline.fits",
        "existing": ACT_MASK,
        "expectedBytes": 1610619840,
    },
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, path: Path, expected: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size == expected:
        return
    partial = path.with_suffix(path.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Rubin-Layers/1.0 (bounded public release acquisition)"})
    with urllib.request.urlopen(request, timeout=180) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, 8 * 1024 * 1024)
    if partial.stat().st_size != expected:
        raise RuntimeError(f"short download for {path.name}: {partial.stat().st_size} != {expected}")
    partial.replace(path)


def ensure_sources() -> dict[str, dict]:
    records: dict[str, dict] = {}
    for key, spec in SOURCES_SPEC.items():
        path = Path(spec.get("existing", SOURCES / spec["file"]))
        if not path.exists():
            download(spec["url"], path, spec["expectedBytes"])
        if path.stat().st_size != spec["expectedBytes"]:
            raise RuntimeError(f"unexpected bytes for {path}: {path.stat().st_size}")
        records[key] = {
            "fileName": spec["file"],
            "publisherUrl": spec["url"],
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        }
    return records


def tan_wcs(ra: float, dec: float) -> WCS:
    w = WCS(naxis=2)
    w.wcs.crpix = [(SIZE + 1) / 2, (SIZE + 1) / 2]
    w.wcs.cdelt = [-WIDTH_DEG / SIZE, WIDTH_DEG / SIZE]
    w.wcs.crval = [ra, dec]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.cunit = ["deg", "deg"]
    return w


def grid_lonlat(w: WCS) -> tuple[np.ndarray, np.ndarray]:
    yy, xx = np.indices((SIZE, SIZE), dtype=np.float64)
    lon, lat = w.pixel_to_world_values(xx, yy)
    return np.mod(lon, 360.0), lat


def flatten_table_map(path: Path, field: int = 0) -> tuple[np.ndarray, fits.Header]:
    with fits.open(path, memmap=True) as hdul:
        hdu = hdul[1]
        data = np.asarray(hdu.data.field(field)).reshape(-1)
        return data, hdu.header.copy()


def sample_healpix(data: np.ndarray, nside: int, lon: np.ndarray, lat: np.ndarray, order: str = "ring") -> np.ndarray:
    hp = HEALPix(nside=nside, order=order, frame="icrs")
    coord = SkyCoord(lon.ravel(), lat.ravel(), unit="deg", frame="icrs")
    idx = hp.skycoord_to_healpix(coord)
    return np.asarray(data[idx]).reshape(lon.shape)


def alm_from_fits(path: Path) -> tuple[np.ndarray, int]:
    with fits.open(path, memmap=True) as hdul:
        table = hdul[1].data
        real = np.asarray(table.field(1), dtype=np.float64)
        imag = np.asarray(table.field(2), dtype=np.float64)
    alm = np.nan_to_num(real + 1j * imag)
    n = len(alm)
    lmax = int((math.sqrt(8 * n + 1) - 3) / 2)
    if (lmax + 1) * (lmax + 2) // 2 != n:
        raise RuntimeError(f"cannot infer triangular alm lmax from {n}")
    return alm, lmax


def synthesize_alm(alm: np.ndarray, lmax: int, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    loc = np.column_stack((np.deg2rad(90.0 - lat.ravel()), np.deg2rad(lon.ravel())))
    values = synthesis_general(
        alm=alm[np.newaxis, :], spin=0, lmax=lmax, mmax=lmax,
        loc=loc, epsilon=1e-7, nthreads=0,
    )[0]
    return np.asarray(values).reshape(lon.shape)


def filter_alm(alm: np.ndarray, lmax: int, lmin: int, lmax_use: int, fwhm_arcmin: float) -> np.ndarray:
    """Apply an explicit harmonic band and Gaussian display beam.

    FITS alm tables use healpy's contiguous m-major packing.  Filtering the
    actual harmonic coefficients (instead of blurring a PNG) keeps the derived
    FITS product reproducible and physically interpretable.
    """
    out = alm.copy()
    ell = np.arange(lmax + 1)
    weights = np.zeros(lmax + 1, dtype=np.float64)
    use = (ell >= lmin) & (ell <= lmax_use)
    sigma = np.deg2rad(fwhm_arcmin / 60.0) / np.sqrt(8.0 * np.log(2.0))
    weights[use] = np.exp(-0.5 * ell[use] * (ell[use] + 1) * sigma * sigma)
    for m in range(lmax + 1):
        start = m * (2 * lmax - m + 3) // 2
        count = lmax - m + 1
        out[start:start + count] *= weights[m:lmax + 1]
    return out


def extract_planck() -> tuple[Path, Path]:
    archive = SOURCES / SOURCES_SPEC["planck"]["file"]
    data_path = SOURCES / "planck-pr3-mv-dat_klm.fits"
    mean_path = SOURCES / "planck-pr3-mv-mf_klm.fits"
    mask_path = SOURCES / "planck-pr3-mask.fits.gz"
    members = {
        "COM_Lensing_4096_R3.00/MV/dat_klm.fits": data_path,
        "COM_Lensing_4096_R3.00/MV/mf_klm.fits": mean_path,
        "COM_Lensing_4096_R3.00/mask.fits.gz": mask_path,
    }
    if not all(p.exists() for p in members.values()):
        with tarfile.open(archive, "r:gz") as tar:
            for name, target in members.items():
                source = tar.extractfile(name)
                if source is None:
                    raise RuntimeError(f"missing {name} in Planck archive")
                with target.open("wb") as out:
                    shutil.copyfileobj(source, out)
    return data_path, mean_path


def load_planck_alm() -> tuple[np.ndarray, int, np.ndarray, fits.Header]:
    dat, mf = extract_planck()
    data_alm, lmax = alm_from_fits(dat)
    mean_alm, mlmax = alm_from_fits(mf)
    if mlmax != lmax:
        raise RuntimeError("Planck data/mean-field lmax mismatch")
    mask_path = SOURCES / "planck-pr3-mask.fits.gz"
    mask, mask_header = flatten_table_map(mask_path)
    return data_alm - mean_alm, lmax, mask, mask_header


def load_des_mean() -> tuple[np.ndarray, np.ndarray]:
    """Return tomographic-bin-4 posterior mean and the mass-map release mask.

    The compact ``karmma_data.zip`` mask used for the tract-level footprint is
    the shear-input mask.  The 100 mass-map samples ship their own, slightly
    smaller mask; extraction must use that map-specific mask.
    """
    archive = SOURCES / SOURCES_SPEC["des"]["file"]
    acc = None
    count = 0
    with zipfile.ZipFile(archive) as zf:
        with fits.open(io.BytesIO(zf.read("desy3_karmma_maps/mask.fits"))) as hdul:
            accepted = np.asarray(hdul[1].data.field(0)).reshape(-1).astype(bool)
        names = sorted(
            (n for n in zf.namelist() if n.startswith("desy3_karmma_maps/map_") and n.endswith(".npy")),
            key=lambda n: int(Path(n).stem.split("_")[-1]),
        )
        for name in names:
            # Shape is (4 source-redshift bins, accepted mask pixels).
            # Bin 4 is retained explicitly rather than averaging physically
            # different lensing kernels.
            sample = np.load(io.BytesIO(zf.read(name))).astype(np.float64)[3]
            if acc is None:
                acc = np.zeros_like(sample, dtype=np.float64)
            acc += sample
            count += 1
    if acc is None or count != 100:
        raise RuntimeError(f"expected 100 DES samples, found {count}")
    mean_masked = acc / count
    if mean_masked.size != accepted.sum():
        raise RuntimeError(f"DES masked map length {mean_masked.size} != mask pixels {accepted.sum()}")
    full = np.full(accepted.size, np.nan, dtype=np.float64)
    full[accepted] = mean_masked.reshape(-1)
    return full, accepted.astype(np.uint8)


def read_spt() -> tuple[np.ndarray, fits.Header, np.ndarray, fits.Header]:
    with fits.open(SOURCES / SOURCES_SPEC["spt-map"]["file"], memmap=False) as h:
        science = np.asarray(h[0].data, dtype=np.float64)
        sheader = h[0].header.copy()
    with fits.open(SOURCES / SOURCES_SPEC["spt-mask"]["file"], memmap=False) as h:
        mask_hdu = next(x for x in h if x.data is not None)
        mask = np.asarray(mask_hdu.data, dtype=np.float64)
        mheader = mask_hdu.header.copy()
    return science, sheader, mask, mheader


def sample_wcs(data: np.ndarray, header: fits.Header, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    w = WCS(header).celestial
    x, y = w.world_to_pixel_values(lon, lat)
    xi = np.rint(x).astype(int)
    yi = np.rint(y).astype(int)
    out = np.full(lon.shape, np.nan, dtype=np.float64)
    good = (xi >= 0) & (yi >= 0) & (xi < data.shape[1]) & (yi < data.shape[0])
    out[good] = data[yi[good], xi[good]]
    return out


def robust_norm(data: np.ndarray, mask: np.ndarray) -> np.ndarray:
    good = np.isfinite(data) & (mask > 0)
    if not np.any(good):
        return np.zeros(data.shape, dtype=np.float32)
    lo, hi = np.nanpercentile(data[good], [2, 98])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(np.nanmin(data[good])), float(np.nanmax(data[good]))
    if hi <= lo:
        return np.zeros(data.shape, dtype=np.float32)
    return np.clip((data - lo) / (hi - lo), 0, 1).astype(np.float32)


def save_png(array: np.ndarray, path: Path, cmap: str, mask: np.ndarray | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mask is None:
        mask = np.isfinite(array).astype(np.uint8)
    norm = robust_norm(array, mask)
    rgba = (colormaps[cmap](norm) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(mask > 0, 255, 0).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)
    return sha256(path)


def save_mask_png(mask: np.ndarray, path: Path) -> str:
    rgba = np.zeros((*mask.shape, 4), dtype=np.uint8)
    rgba[..., :3] = np.where(mask[..., None] > 0, np.array([245, 196, 81], dtype=np.uint8), np.array([20, 25, 35], dtype=np.uint8))
    rgba[..., 3] = 255
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)
    return sha256(path)


def save_overlay(science: np.ndarray, mask: np.ndarray, rubin_preview: Path, path: Path, cmap: str) -> str:
    # Preserve enough Rubin detail for a useful visual alignment while the
    # external field remains honestly limited to its 64-pixel science grid.
    display_size = 512
    rubin = Image.open(rubin_preview).convert("RGB").resize((display_size, display_size), Image.Resampling.LANCZOS)
    base = np.asarray(rubin, dtype=np.float32) / 255
    gray = np.mean(base, axis=2, keepdims=True)
    norm = robust_norm(science, mask)
    heat_small = (colormaps[cmap](norm)[..., :3] * 255).astype(np.uint8)
    heat = np.asarray(Image.fromarray(heat_small, "RGB").resize((display_size, display_size), Image.Resampling.BICUBIC), dtype=np.float32) / 255
    mask_large = np.asarray(Image.fromarray((mask > 0).astype(np.uint8) * 255, "L").resize((display_size, display_size), Image.Resampling.NEAREST)) > 0
    alpha = (0.38 * mask_large)[..., None]
    out = np.clip(gray * (1 - alpha) + heat * alpha, 0, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((out * 255).astype(np.uint8), "RGB").save(path, optimize=True)
    return sha256(path)


def write_fits(path: Path, science: np.ndarray, mask: np.ndarray, wcs: WCS, *, survey: str, release: str,
               observable: str, unit: str, source_sha: str, processing: dict | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = wcs.to_header()
    header["BUNIT"] = unit
    header["BTYPE"] = observable
    header["SURVEY"] = survey
    header["RELEASE"] = release
    header["SRC_SHA"] = source_sha
    if processing:
        if "multipoleRange" in processing:
            header["LMIN"] = processing["multipoleRange"][0]
            header["LMAX"] = processing["multipoleRange"][1]
        if "gaussianFwhmArcmin" in processing:
            header["FWHMAM"] = processing["gaussianFwhmArcmin"]
    header["COMMENT"] = "Positional display grid only; do not subtract from Rubin optical pixels."
    primary = fits.PrimaryHDU()
    image = fits.ImageHDU(science.astype(np.float32), header=header, name="SCIENCE")
    mheader = wcs.to_header()
    mheader["BUNIT"] = "1"
    mheader["BTYPE"] = "released product mask / valid coverage"
    mask_hdu = fits.ImageHDU(mask.astype(np.uint8), header=mheader, name="COVERAGE")
    hdul = fits.HDUList([primary, image, mask_hdu])
    for hdu in hdul:
        hdu.add_checksum()
    hdul.writeto(path, overwrite=True, checksum=True)
    return sha256(path)


def public_path(path: Path) -> str:
    return "/" + str(path.relative_to(ROOT / "public")).replace("\\", "/")


def source_public(record: dict) -> dict:
    return {k: record[k] for k in ("fileName", "publisherUrl", "bytes", "sha256")}


def embedded_release_evidence() -> dict:
    """Checksum release members actually used after archive extraction."""
    planck_members = []
    for name in ("planck-pr3-mv-dat_klm.fits", "planck-pr3-mv-mf_klm.fits", "planck-pr3-mask.fits.gz"):
        path = SOURCES / name
        planck_members.append({"fileName": name, "bytes": path.stat().st_size, "sha256": sha256(path)})
    archive = SOURCES / SOURCES_SPEC["des"]["file"]
    with zipfile.ZipFile(archive) as zf:
        mask_bytes = zf.read("desy3_karmma_maps/mask.fits")
        sample_names = [n for n in zf.namelist() if n.startswith("desy3_karmma_maps/map_") and n.endswith(".npy")]
    return {
        "planckArchiveMembers": planck_members,
        "desArchiveMembers": {
            "massMapSampleCount": len(sample_names),
            "massMapMask": {"fileName": "desy3_karmma_maps/mask.fits", "bytes": len(mask_bytes),
                            "sha256": hashlib.sha256(mask_bytes).hexdigest()},
            "sampleSemantics": "Each map_N.npy contains four tomographic convergence maps on the released mass-map mask; this extraction uses source bin 4 and averages all 100 samples.",
        },
    }


def product_record(*, region: dict, survey_id: str, survey_name: str, release: str, product_type: str,
                   observable: str, unit: str, fits_path: Path, fits_sha: str, preview: Path, preview_sha: str,
                   coverage: Path, coverage_sha: str, overlay: Path, overlay_sha: str, rubin_preview: str,
                   provenance: list[str], source_checksums: list[str], valid_fraction: float,
                   blockers: list[str]) -> dict:
    return {
        "regionId": region["id"], "tract": region["tract"], "surveyId": survey_id,
        "surveyName": survey_name, "family": "cmb-large-scale-structure", "release": release,
        "productType": product_type, "status": "available", "scienceReady": True,
        "displayReady": True, "comparisonReady": False, "bandOrObservable": observable,
        "unit": unit, "previewPath": public_path(preview),
        "alignedRubinPreviewPath": rubin_preview, "coveragePreviewPath": public_path(coverage),
        "overlayPreviewPath": public_path(overlay), "provenanceUrls": provenance,
        "checksum": fits_sha, "previewSha256": preview_sha, "coveragePreviewSha256": coverage_sha,
        "overlayPreviewSha256": overlay_sha, "sourceChecksums": source_checksums,
        "validPixelFraction": round(valid_fraction, 8),
        "blockers": blockers,
        "localScienceProduct": str(fits_path.relative_to(ROOT)).replace("\\", "/"),
        "notes": "External field sampled on the Rubin-centered display grid; coverage is a separate plane; no cross-field subtraction.",
    }


def make_product(region: dict, survey: dict, science: np.ndarray, mask: np.ndarray, w: WCS,
                 rubin_preview: str, source_records: list[dict]) -> dict:
    stem = f"{region['id']}-{survey['id']}"
    fitspath = PRODUCTS / stem / f"{stem}.fits"
    preview = PUBLIC_PREVIEWS / f"{stem}-science.png"
    coverage = PUBLIC_PREVIEWS / f"{stem}-coverage.png"
    overlay = PUBLIC_PREVIEWS / f"{stem}-rubin-overlay.png"
    mask = (np.asarray(mask) > 0).astype(np.uint8)
    science = np.where(mask > 0, science, np.nan)
    src_hashes = [x["sha256"] for x in source_records]
    fits_sha = write_fits(fitspath, science, mask, w, survey=survey["name"], release=survey["release"],
                          observable=survey["observable"], unit=survey["unit"], source_sha=src_hashes[0],
                          processing=survey.get("processing"))
    preview_sha = save_png(science, preview, survey["cmap"], mask)
    coverage_sha = save_mask_png(mask, coverage)
    overlay_sha = save_overlay(science, mask, ROOT / "public" / rubin_preview.lstrip("/"), overlay, survey["cmap"])
    record = product_record(
        region=region, survey_id=survey["id"], survey_name=survey["name"], release=survey["release"],
        product_type=survey["productType"], observable=survey["observable"], unit=survey["unit"],
        fits_path=fitspath, fits_sha=fits_sha, preview=preview, preview_sha=preview_sha,
        coverage=coverage, coverage_sha=coverage_sha, overlay=overlay, overlay_sha=overlay_sha,
        rubin_preview=rubin_preview, provenance=survey["provenance"], source_checksums=src_hashes,
        valid_fraction=float(mask.mean()), blockers=survey["blockers"],
    )
    if "processing" in survey:
        record["processing"] = survey["processing"]
    return record


def clean_stale(products: list[dict]) -> None:
    """Remove only unreferenced artifacts inside this pipeline's dedicated roots."""
    keep_fits = {(ROOT / p["localScienceProduct"]).resolve() for p in products}
    keep_previews = set()
    for p in products:
        for key in ("previewPath", "coveragePreviewPath", "overlayPreviewPath"):
            keep_previews.add((ROOT / "public" / p[key].lstrip("/")).resolve())
    for file in PRODUCTS.rglob("*"):
        if file.is_file() and file.resolve() not in keep_fits:
            file.unlink()
    for directory in sorted((x for x in PRODUCTS.rglob("*") if x.is_dir()), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()
    for file in PUBLIC_PREVIEWS.rglob("*"):
        if file.is_file() and file.resolve() not in keep_previews:
            file.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-download", action="store_true", help="Require already-cached source files")
    args = parser.parse_args()
    SOURCES.mkdir(parents=True, exist_ok=True)
    PRODUCTS.mkdir(parents=True, exist_ok=True)
    PUBLIC_PREVIEWS.mkdir(parents=True, exist_ok=True)
    PUBLIC_MANIFEST.parent.mkdir(parents=True, exist_ok=True)

    if args.skip_download:
        for spec in SOURCES_SPEC.values():
            path = Path(spec.get("existing", SOURCES / spec["file"]))
            if not path.exists():
                raise FileNotFoundError(path)
    source_records = ensure_sources()

    selected_doc = json.loads(SELECTED.read_text())
    resolution_doc = json.loads(RESOLUTION.read_text())
    overlap = {x["surveyId"]: set(x["confirmedRubinTractIds"]) for x in resolution_doc["resolved"]}
    rubin_doc = json.loads(RUBIN_MANIFEST.read_text())
    rubin_by_tract = {x["tract"]: x for x in rubin_doc["regions"]}

    act_alm, act_lmax = alm_from_fits(SOURCES / SOURCES_SPEC["act-alm"]["file"])
    act_alm = filter_alm(act_alm, act_lmax, 40, 2000, 5.0)
    planck_alm, planck_lmax, planck_mask, planck_mask_header = load_planck_alm()
    planck_alm = filter_alm(planck_alm, planck_lmax, 8, 2048, 5.0)
    act_mask, act_mask_header = flatten_table_map(ACT_MASK)
    des_mean, des_mask = load_des_mean()
    spt_map, spt_header, spt_mask, spt_mask_header = read_spt()

    survey_defs = {
        "act-dr6": {
            "id": "act-dr6", "name": "ACT DR6 CMB lensing", "release": "DR6 lensing v1 baseline",
            "productType": "CMB lensing convergence map", "observable": "CMB lensing convergence kappa",
            "unit": "dimensionless", "cmap": "magma",
            "provenance": [SOURCES_SPEC["act-alm"]["url"], SOURCES_SPEC["act-mask"]["url"],
                           "https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_maps_info.html"],
            "blockers": ["different physical observable from optical flux", "published filtering and mask-squared guidance", "no cross-field subtraction"],
            "processing": {"multipoleRange": [40, 2000], "gaussianFwhmArcmin": 5.0},
        },
        "planck-2018": {
            "id": "planck-2018", "name": "Planck PR3 CMB lensing", "release": "Planck 2018 PR3 MV",
            "productType": "CMB lensing convergence reconstruction", "observable": "minimum-variance convergence kappa (mean-field subtracted)",
            "unit": "dimensionless", "cmap": "viridis",
            "provenance": [SOURCES_SPEC["planck"]["url"], "https://irsa.ipac.caltech.edu/data/Planck/release_3/docs/README.html"],
            "blockers": ["different physical observable from optical flux", "native lensing resolution is much coarser than Rubin", "no cross-field subtraction"],
            "processing": {"multipoleRange": [8, 2048], "gaussianFwhmArcmin": 5.0, "meanFieldSubtracted": True},
        },
        "des-y3-lensing": {
            "id": "des-y3-lensing", "name": "DES Y3 KaRMMa", "release": "Zenodo 10672062",
            "productType": "weak-lensing convergence posterior mean", "observable": "source-bin-4 mean convergence kappa across 100 released KaRMMa samples",
            "unit": "dimensionless", "cmap": "plasma",
            "provenance": ["https://zenodo.org/records/10672062", SOURCES_SPEC["des"]["url"]],
            "blockers": ["different physical observable from optical flux", "posterior samples share a lognormal prior", "no cross-field subtraction"],
        },
        "spt-3g": {
            "id": "spt-3g", "name": "SPT-3G EDFS", "release": "Archipley et al. EDFS 2025 (files refreshed 2026-02)",
            "productType": "CMB temperature map", "observable": "150 GHz CMB temperature fluctuation",
            "unit": "uK_CMB", "cmap": "coolwarm",
            "provenance": ["https://pole.uchicago.edu/public/data/edfs25/", SOURCES_SPEC["spt-map"]["url"], SOURCES_SPEC["spt-mask"]["url"]],
            "blockers": ["temperature map is not a lensing convergence map", "different physical observable from optical flux", "no cross-field subtraction"],
        },
    }

    products: list[dict] = []
    expected = {"act-dr6": 0, "des-y3-lensing": 0, "planck-2018": 0, "spt-3g": 0}
    errors: list[dict] = []
    for region in selected_doc["regions"]:
        tract = region["tract"]
        rubin = rubin_by_tract.get(tract)
        if not rubin or rubin.get("status") != "complete":
            errors.append({"regionId": region["id"], "tract": tract, "surveyId": "all", "error": "Rubin aligned preview missing"})
            continue
        rubin_preview = rubin["preview"]["publicPath"]
        w = tan_wcs(*region["center"])
        lon, lat = grid_lonlat(w)

        candidates: list[tuple[str, np.ndarray, np.ndarray, list[dict]]] = []
        # Planck is all-sky and is explicitly confirmed for every selected region.
        expected["planck-2018"] += 1
        try:
            # Planck harmonic products and mask use Galactic coordinates.
            gal = SkyCoord(lon.ravel(), lat.ravel(), unit="deg", frame="icrs").galactic
            glon = np.asarray(gal.l.deg).reshape(lon.shape)
            glat = np.asarray(gal.b.deg).reshape(lat.shape)
            pscience = synthesize_alm(planck_alm, planck_lmax, glon, glat)
            pmask = sample_healpix(planck_mask, int(planck_mask_header["NSIDE"]), glon, glat,
                                   str(planck_mask_header["ORDERING"]).lower()) > 0
            if np.any(pmask):
                candidates.append(("planck-2018", pscience, pmask, [source_records["planck"]]))
        except Exception as exc:
            errors.append({"regionId": region["id"], "tract": tract, "surveyId": "planck-2018", "error": str(exc)})

        if tract in overlap.get("act-dr6", set()):
            expected["act-dr6"] += 1
            try:
                ascience = synthesize_alm(act_alm, act_lmax, lon, lat)
                amask = sample_healpix(act_mask, int(act_mask_header["NSIDE"]), lon, lat, str(act_mask_header["ORDERING"]).lower()) >= 0.99
                if np.any(amask):
                    candidates.append(("act-dr6", ascience, amask, [source_records["act-alm"], source_records["act-mask"]]))
            except Exception as exc:
                errors.append({"regionId": region["id"], "tract": tract, "surveyId": "act-dr6", "error": str(exc)})

        if tract in overlap.get("des-y3-lensing", set()):
            expected["des-y3-lensing"] += 1
            try:
                dscience = sample_healpix(des_mean, 256, lon, lat, "ring")
                dmask = sample_healpix(des_mask, 256, lon, lat, "ring") > 0
                if np.any(dmask):
                    candidates.append(("des-y3-lensing", dscience, dmask, [source_records["des"]]))
            except Exception as exc:
                errors.append({"regionId": region["id"], "tract": tract, "surveyId": "des-y3-lensing", "error": str(exc)})

        if tract in overlap.get("spt-3g", set()):
            expected["spt-3g"] += 1
            try:
                sscience = sample_wcs(spt_map, spt_header, lon, lat)
                smask = sample_wcs(spt_mask, spt_mask_header, lon, lat) > 0
                if np.any(smask):
                    candidates.append(("spt-3g", sscience, smask, [source_records["spt-map"], source_records["spt-mask"]]))
            except Exception as exc:
                errors.append({"regionId": region["id"], "tract": tract, "surveyId": "spt-3g", "error": str(exc)})

        for survey_id, science, mask, sources in candidates:
            try:
                products.append(make_product(region, survey_defs[survey_id], science, mask, w, rubin_preview, sources))
            except Exception as exc:
                errors.append({"regionId": region["id"], "tract": tract, "surveyId": survey_id, "error": str(exc)})
        print(f"{region['id']}: {len(candidates)} candidates, {len(products)} cumulative products", flush=True)

    counts = {}
    for survey_id in expected:
        made = sum(p["surveyId"] == survey_id for p in products)
        err = sum(e["surveyId"] == survey_id for e in errors)
        counts[survey_id] = {"expectedOverlapRegionCount": expected[survey_id], "availableCount": made,
                             "noneCount": 50 - made - err, "errorCount": err}
    summary = {
        "selectedRegionCount": len(selected_doc["regions"]),
        "tractOverlapCandidateCount": sum(expected.values()),
        "availableProductCount": len(products),
        "noneCount": 4 * len(selected_doc["regions"]) - len(products) - len(errors),
        "errorCount": len(errors),
        "scienceReadyCount": sum(bool(p["scienceReady"]) for p in products),
        "displayReadyCount": sum(bool(p["displayReady"]) for p in products),
        "comparisonReadyCount": sum(bool(p["comparisonReady"]) for p in products),
        "bySurvey": counts,
    }
    public_sources = {k: source_public(v) for k, v in source_records.items()}
    clean_stale(products)
    public_products = [{k: v for k, v in p.items() if k != "localScienceProduct"} for p in products]
    product_keys = {(p["regionId"], p["surveyId"]) for p in products}
    error_by_key = {(e["regionId"], e["surveyId"]): e["error"] for e in errors}
    availability = []
    for region in selected_doc["regions"]:
        for survey_id in ("act-dr6", "des-y3-lensing", "planck-2018", "spt-3g"):
            key = (region["id"], survey_id)
            tract_candidate = survey_id == "planck-2018" or region["tract"] in overlap.get(survey_id, set())
            if key in product_keys:
                status, reason = "available", "valid released science pixels at selected cutout"
            elif key in error_by_key:
                status, reason = "error", error_by_key[key]
            elif tract_candidate:
                status, reason = "none", "tract overlaps product, but the selected 4 arcmin cutout has no valid released mask pixels"
            else:
                status, reason = "none", "no exact tract-level overlap with this released product"
            availability.append({"regionId": region["id"], "tract": region["tract"], "surveyId": survey_id,
                                 "status": status, "reason": reason})
    common = {
        "schemaVersion": "layers-lensing-cmb-pixels-v1", "generatedAt": utcnow(), "summary": summary,
        "method": {
            "displayGrid": f"{SIZE}x{SIZE} TAN, Rubin-centered, {WIDTH_DEG * 60:.1f} arcmin wide",
            "healpixSampling": "nearest native HEALPix pixel; no invented super-resolution",
            "harmonicSampling": "official convergence alm evaluated at display-grid coordinates with ducc0",
            "alignment": "positional display only; external fields are never subtracted from optical flux",
            "maskSemantics": "COVERAGE is a separate released product mask/validity plane, not a science signal",
        },
        "sources": public_sources, "embeddedReleaseEvidence": embedded_release_evidence(),
        "products": public_products, "availabilityAudit": availability,
        "errors": errors,
        "unresolved": [
            {"surveyId": "kids-1000-lensing", "status": "unresolved-no-exact-public-spatial-mask"},
            {"surveyId": "hsc-lensing", "status": "unresolved-public-product-withheld"},
        ],
    }
    PUBLIC_MANIFEST.write_text(json.dumps(common, indent=2) + "\n")
    detailed = dict(common)
    detailed["sources"] = source_records
    detailed["products"] = products
    DETAILED_MANIFEST.write_text(json.dumps(detailed, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
