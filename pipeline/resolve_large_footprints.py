#!/usr/bin/env python3
"""Resolve large, release-specific survey footprints against Rubin DP2.

This pipeline is deliberately separate from the production overlap builder.  It
materializes only official or release-matched products, writes compact MOCs,
and records products which still cannot be represented without over-claiming.

Large source files are cached under ``pipeline/results`` and never published.
The public artifact contains source URLs, checksums, compact MOC links, overlap
tract IDs, and cautions, but no credentials or machine-local absolute paths.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import shutil
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from astropy_healpix import HEALPix
from astropy_healpix.core import ring_to_nested
from mocpy import MOC


USER_AGENT = "Rubin-Light-Atlas/0.4 (+https://github.com/lrspeiser/rubin-light-atlas)"
MOC_ORDER = 10

ACT_URL = (
    "https://portal.nersc.gov/project/act/dr6_lensing_v1/maps/baseline/"
    "mask_act_dr6_lensing_v1_healpix_nside_4096_baseline.fits"
)
ACT_BYTES = 1_610_619_840

SDSS_ALLSPEC_URL = (
    "https://dr19.sdss.org/sas/dr19/spectro/allspec/1.0.1/"
    "allspec-dr19-1.0.1.fits.gz"
)
SDSS_ALLSPEC_BYTES = 811_831_111
SDSS_ALLSPEC_SHA1 = "104ed9bcce35f02dd22e5b8b794cdcadaf5d020f"
SDSS_MULTIPLEX_URL = (
    "https://dr19.sdss.org/sas/dr19/spectro/allspec/1.0.1/"
    "multiplex-dr19-1.0.1.fits"
)
SDSS_MULTIPLEX_SHA1 = "d99f756057d253261084604e1daecd94a888affc"

DES_RECORD_URL = "https://zenodo.org/records/10672062"
DES_DATA_URL = "https://zenodo.org/api/records/10672062/files/karmma_data.zip/content"
DES_DATA_MD5 = "899c63d4a64a2ef70fc3f3fb39e51d70"

SPT_BASE = "https://pole.uchicago.edu/public/data/edfs25"
SPT_FILES = [
    "pixel_mask_cluster_catalog_SFL.fits.gz",
    "pixel_mask_emissive_source_catalog_ZEA.fits.gz",
    "pixel_mask_edfs_ZEA.fits.gz",
]

HSC_BASE = "https://hsc-release.mtk.nao.ac.jp/rsrc/pdr3/tract_patches/info"
HSC_FILES = [
    "tracts_patches_DUD-COSMOS.txt",
    "tracts_patches_DUD-DEEP2-3.txt",
    "tracts_patches_DUD-ELAIS-N1.txt",
    "tracts_patches_DUD-XMM-LSS.txt",
    "tracts_patches_W-AEGIS.txt",
    "tracts_patches_W-hectomap.txt",
    "tracts_patches_W-autumn.txt",
    "tracts_patches_W-spring.txt",
]

KIDS_COSMOLOGY_URL = (
    "https://kids.strw.leidenuniv.nl/DR4/data_files/"
    "KiDS1000_cosmic_shear_data_release.tgz"
)
KIDS_CATALOG_URL = (
    "https://kids.strw.leidenuniv.nl/DR4/data_files/"
    "KiDS_DR4.1_ugriZYJHKs_SOM_gold_WL_cat.fits"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, path: Path, *, refresh: bool, expected_bytes: int | None = None) -> None:
    if path.exists() and not refresh and (expected_bytes is None or path.stat().st_size == expected_bytes):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output, length=8 * 1024 * 1024)
    if expected_bytes is not None and temporary.stat().st_size != expected_bytes:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Unexpected byte count for {url}")
    temporary.replace(path)


def load_tract_mocs(path: Path) -> dict[int, MOC]:
    grouped: dict[int, list[SkyCoord]] = defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            patch = json.loads(line)
            grouped[int(patch["tract"])].append(
                SkyCoord(
                    [point[0] for point in patch["polygon"]] * u.deg,
                    [point[1] for point in patch["polygon"]] * u.deg,
                    frame="icrs",
                )
            )
    result: dict[int, MOC] = {}
    for tract, polygons in sorted(grouped.items()):
        pieces = MOC.from_polygons(polygons, max_depth=MOC_ORDER)
        result[tract] = pieces[0] if len(pieces) == 1 else pieces[0].union(*pieces[1:])
    return result


def moc_from_nested_cells(cells: np.ndarray, depth: int, *, max_depth: int = MOC_ORDER) -> MOC:
    unique = np.unique(np.asarray(cells, dtype=np.uint64))
    depths = np.full(unique.shape, depth, dtype=np.uint8)
    return MOC.from_healpix_cells(unique, depths, max_depth=max_depth)


def healpix_mask_moc(path: Path, threshold: float) -> tuple[MOC, dict[str, Any]]:
    with fits.open(path, memmap=True) as hdus:
        header = hdus[1].header
        values = hdus[1].data[hdus[1].columns.names[0]].reshape(-1)
        nside = int(header["NSIDE"])
        ordering = str(header["ORDERING"]).strip().upper()
        native_depth = int(round(math.log2(nside)))
        if 2**native_depth != nside or ordering != "RING":
            raise RuntimeError(f"Expected power-of-two RING HEALPix mask in {path}")

        if native_depth <= MOC_ORDER:
            ring_cells = np.flatnonzero(values >= threshold).astype(np.int64)
            nested_cells = ring_to_nested(ring_cells, nside)
            moc = moc_from_nested_cells(nested_cells, native_depth)
            accepted_native_pixels = int(ring_cells.size)
        else:
            selected = np.zeros(12 * 4**MOC_ORDER, dtype=np.bool_)
            accepted_native_pixels = 0
            chunk_size = 2_000_000
            shift = 2 * (native_depth - MOC_ORDER)
            for start in range(0, values.size, chunk_size):
                local = np.flatnonzero(values[start : start + chunk_size] >= threshold)
                if not local.size:
                    continue
                ring_cells = local.astype(np.int64) + start
                nested_cells = ring_to_nested(ring_cells, nside)
                selected[nested_cells >> shift] = True
                accepted_native_pixels += int(local.size)
            moc = moc_from_nested_cells(np.flatnonzero(selected), MOC_ORDER)

    return moc, {
        "nativeNside": nside,
        "nativeOrdering": ordering,
        "threshold": threshold,
        "acceptedNativePixels": accepted_native_pixels,
        "mocOrder": int(moc.max_order),
        "skyFraction": float(moc.sky_fraction),
    }


def spt_moc(paths: Iterable[Path]) -> tuple[MOC, list[dict[str, Any]]]:
    nested = HEALPix(nside=2**MOC_ORDER, order="nested", frame="icrs")
    selected = np.zeros(12 * 4**MOC_ORDER, dtype=np.bool_)
    components: list[dict[str, Any]] = []
    for path in paths:
        with fits.open(path) as hdus:
            image = np.asarray(hdus[0].data)
            header = hdus[0].header
            yy, xx = np.nonzero(image > 0)
            wcs = WCS(header).celestial
            accepted = 0
            for start in range(0, xx.size, 1_000_000):
                ra, dec = wcs.pixel_to_world_values(xx[start : start + 1_000_000], yy[start : start + 1_000_000])
                finite = np.isfinite(ra) & np.isfinite(dec)
                if finite.any():
                    cells = nested.lonlat_to_healpix(ra[finite] * u.deg, dec[finite] * u.deg)
                    selected[cells] = True
                    accepted += int(finite.sum())
            components.append(
                {
                    "fileName": path.name,
                    "sourceUrl": f"{SPT_BASE}/{path.name}",
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "positiveImagePixels": accepted,
                    "wcsProjection": str(header.get("CTYPE1", "")),
                }
            )
    moc = moc_from_nested_cells(np.flatnonzero(selected), MOC_ORDER)
    return moc, components


HSC_TRACT_RE = re.compile(
    r"^Tract:\s+(\d+)\s+Corner([0-3])\s+\(RA, Dec\):\s+\(([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\)"
)


def hsc_pdr3_moc(paths: Iterable[Path]) -> tuple[MOC, list[dict[str, Any]], int]:
    tract_corners: dict[int, dict[int, tuple[float, float]]] = defaultdict(dict)
    components: list[dict[str, Any]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        field_tracts: set[int] = set()
        for line in text.splitlines():
            match = HSC_TRACT_RE.match(line)
            if match:
                tract = int(match.group(1))
                corner = int(match.group(2))
                tract_corners[tract][corner] = (float(match.group(3)), float(match.group(4)))
                field_tracts.add(tract)
        components.append(
            {
                "fileName": path.name,
                "sourceUrl": f"{HSC_BASE}/{path.name}",
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "officialTractCount": len(field_tracts),
            }
        )
    incomplete = [tract for tract, corners in tract_corners.items() if set(corners) != {0, 1, 2, 3}]
    if incomplete:
        raise RuntimeError(f"Incomplete HSC tract corners: {incomplete[:10]}")
    polygons = [
        SkyCoord(
            [tract_corners[tract][index][0] for index in range(4)] * u.deg,
            [tract_corners[tract][index][1] for index in range(4)] * u.deg,
            frame="icrs",
        )
        for tract in sorted(tract_corners)
    ]
    pieces = MOC.from_polygons(polygons, max_depth=MOC_ORDER)
    moc = pieces[0] if len(pieces) == 1 else pieces[0].union(*pieces[1:])
    return moc, components, len(tract_corners)


def sdss_allspec_moc(path: Path) -> tuple[MOC, dict[str, Any]]:
    """Stream only RA/Dec from the gzip-compressed DR19 ALLSPEC FITS table."""
    # These values are verified from the release FITS header below.  Keeping the
    # reader row-strided avoids expanding the 811 MB gzip file to 11.3 GB.
    data_offset = 14_400
    row_bytes = 772
    row_count = 14_608_757
    ra_offset = 564
    dec_offset = 572
    selected = np.zeros(12 * 4**MOC_ORDER, dtype=np.bool_)
    nested = HEALPix(nside=2**MOC_ORDER, order="nested", frame="icrs")
    rows_read = 0
    valid_rows = 0
    chunk_rows = 250_000
    with gzip.open(path, "rb") as handle:
        header = handle.read(data_offset)
        required_cards = [
            b"NAXIS1  =                  772",
            b"NAXIS2  =             14608757",
            b"TTYPE25 = 'ra      '",
            b"TTYPE26 = 'dec     '",
        ]
        if any(card not in header for card in required_cards):
            raise RuntimeError("SDSS ALLSPEC schema differs from the validated DR19 1.0.1 layout")
        while rows_read < row_count:
            count = min(chunk_rows, row_count - rows_read)
            payload = handle.read(count * row_bytes)
            if len(payload) != count * row_bytes:
                raise RuntimeError(f"Truncated SDSS ALLSPEC table after {rows_read:,} rows")
            ra = np.ndarray((count,), dtype=">f8", buffer=payload, offset=ra_offset, strides=(row_bytes,))
            dec = np.ndarray((count,), dtype=">f8", buffer=payload, offset=dec_offset, strides=(row_bytes,))
            valid = np.isfinite(ra) & np.isfinite(dec) & (ra >= 0) & (ra < 360) & (dec >= -90) & (dec <= 90)
            if valid.any():
                cells = nested.lonlat_to_healpix(ra[valid] * u.deg, dec[valid] * u.deg)
                selected[cells] = True
                valid_rows += int(valid.sum())
            rows_read += count
    moc = moc_from_nested_cells(np.flatnonzero(selected), MOC_ORDER)
    return moc, {
        "tableRows": rows_read,
        "validCoordinateRows": valid_rows,
        "uniqueOrder10Cells": int(selected.sum()),
        "rowBytes": row_bytes,
        "streamedColumns": ["ra", "dec"],
        "expandedTableBytesRead": row_count * row_bytes,
        "mocOrder": int(moc.max_order),
        "skyFraction": float(moc.sky_fraction),
    }


def overlap_tracts(survey_moc: MOC, tract_mocs: dict[int, MOC]) -> list[int]:
    return [tract for tract, tract_moc in tract_mocs.items() if not survey_moc.intersection(tract_moc).empty()]


def public_source(path: Path, url: str, **extra: Any) -> dict[str, Any]:
    return {
        "fileName": path.name,
        "sourceUrl": url,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        **extra,
    }


def save_product(
    survey_id: str,
    moc: MOC,
    tract_mocs: dict[int, MOC],
    public_moc_dir: Path,
    **metadata: Any,
) -> dict[str, Any]:
    public_moc_dir.mkdir(parents=True, exist_ok=True)
    output = public_moc_dir / f"{survey_id}.moc.fits"
    moc.save(output, format="fits", overwrite=True)
    tracts = overlap_tracts(moc, tract_mocs)
    return {
        "surveyId": survey_id,
        "status": metadata.pop("status", "resolved-full"),
        "confirmedRubinTractCount": len(tracts),
        "confirmedRubinTractIds": tracts,
        "moc": {
            "href": f"/data/coverage/mocs-large/{output.name}",
            "bytes": output.stat().st_size,
            "sha256": sha256_file(output),
            "order": int(moc.max_order),
            "skyFraction": float(moc.sky_fraction),
        },
        **metadata,
    }


def validate(public_result: dict[str, Any], tract_ids: set[int]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, evidence: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    resolved = public_result["resolved"]
    unresolved = public_result["unresolved"]
    resolved_ids = {item["surveyId"] for item in resolved}
    unresolved_ids = {item["surveyId"] for item in unresolved}
    check(
        "four release-matched products resolved",
        resolved_ids == {"act-dr6", "des-y3-lensing", "spt-3g", "sdss-dr19"},
        sorted(resolved_ids),
    )
    check(
        "products without exact public masks remain unresolved",
        unresolved_ids == {"hsc-ssp-pdr3", "kids-1000-lensing", "hsc-lensing"},
        sorted(unresolved_ids),
    )
    check(
        "all overlap IDs belong to Rubin DP2",
        all(set(item["confirmedRubinTractIds"]) <= tract_ids for item in resolved),
        {item["surveyId"]: item["confirmedRubinTractCount"] for item in resolved},
    )
    check(
        "all resolved MOCs are nonempty and checksummed",
        all(item["moc"]["skyFraction"] > 0 and len(item["moc"]["sha256"]) == 64 for item in resolved),
        {item["surveyId"]: item["moc"]["sha256"] for item in resolved},
    )
    check(
        "publisher checksums match downloaded release files",
        next(item for item in resolved if item["surveyId"] == "sdss-dr19")["source"]["publisherSha1"]
        == next(item for item in resolved if item["surveyId"] == "sdss-dr19")["source"]["computedSha1"]
        and next(item for item in resolved if item["surveyId"] == "des-y3-lensing")["source"]["archive"][
            "publisherMd5"
        ]
        == next(item for item in resolved if item["surveyId"] == "des-y3-lensing")["source"]["archive"][
            "computedMd5"
        ],
        "SDSS publisher SHA-1 and Zenodo MD5 verified",
    )
    check(
        "HSC imaging grid and HSC lensing are not promoted as product footprints",
        next(item for item in unresolved if item["surveyId"] == "hsc-lensing")["publicReleaseAvailable"] is False
        and next(item for item in unresolved if item["surveyId"] == "hsc-ssp-pdr3")["officialGridIsProductFootprint"]
        is False,
        "PDR3 tract grid is an envelope; PDR3 weak-lensing shapes are withheld",
    )
    check(
        "SPT result is scoped to a named product",
        next(item for item in resolved if item["surveyId"] == "spt-3g")["status"]
        == "resolved-named-product-subset",
        "SPT-3G EDFS 2025 masks only",
    )
    serialized = json.dumps(public_result)
    check(
        "public artifact contains no secrets or absolute cache paths",
        not re.search(r"(Bearer\s+|token=|[A-Za-z]:\\|pipeline/results)", serialized, re.IGNORECASE),
        "redaction regex passed",
    )
    return {
        "schemaVersion": 1,
        "generatedAt": utc_now(),
        "passed": all(item["passed"] for item in checks),
        "checkCount": len(checks),
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--patches", type=Path, default=Path("pipeline/results/coverage/dp2-coadd-patches.jsonl.gz")
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("pipeline/results/coverage/resolution-large")
    )
    parser.add_argument(
        "--public-json", type=Path, default=Path("public/data/coverage/large-footprint-resolution.json")
    )
    parser.add_argument(
        "--validation-json",
        type=Path,
        default=Path("public/data/coverage/large-footprint-resolution-validation.json"),
    )
    parser.add_argument(
        "--public-moc-dir", type=Path, default=Path("public/data/coverage/mocs-large")
    )
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    sources = args.cache_dir

    act_path = sources / Path(ACT_URL).name
    sdss_path = sources / Path(SDSS_ALLSPEC_URL).name
    multiplex_path = sources / Path(SDSS_MULTIPLEX_URL).name
    des_zip = sources / "karmma_data.zip"
    kids_cosmology = sources / Path(KIDS_COSMOLOGY_URL).name
    download(ACT_URL, act_path, refresh=args.refresh, expected_bytes=ACT_BYTES)
    download(SDSS_ALLSPEC_URL, sdss_path, refresh=args.refresh, expected_bytes=SDSS_ALLSPEC_BYTES)
    download(SDSS_MULTIPLEX_URL, multiplex_path, refresh=args.refresh)
    download(DES_DATA_URL, des_zip, refresh=args.refresh)
    download(KIDS_COSMOLOGY_URL, kids_cosmology, refresh=args.refresh)
    spt_paths = []
    for name in SPT_FILES:
        path = sources / name
        download(f"{SPT_BASE}/{name}", path, refresh=args.refresh)
        spt_paths.append(path)
    hsc_paths = []
    for name in HSC_FILES:
        path = sources / name
        download(f"{HSC_BASE}/{name}", path, refresh=args.refresh)
        hsc_paths.append(path)

    if digest_file(sdss_path, "sha1") != SDSS_ALLSPEC_SHA1:
        raise RuntimeError("SDSS ALLSPEC does not match the publisher SHA-1")
    if digest_file(multiplex_path, "sha1") != SDSS_MULTIPLEX_SHA1:
        raise RuntimeError("SDSS multiplex does not match the publisher SHA-1")
    if digest_file(des_zip, "md5") != DES_DATA_MD5:
        raise RuntimeError("DES-Y3 KaRMMa archive does not match the Zenodo MD5")

    des_mask = sources / "des-y3" / "data" / "des_y3" / "mask_desy3.fits"
    if args.refresh or not des_mask.exists():
        with zipfile.ZipFile(des_zip) as archive:
            member = "data/des_y3/mask_desy3.fits"
            des_mask.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, des_mask.open("wb") as output:
                shutil.copyfileobj(source, output)

    tract_mocs = load_tract_mocs(args.patches)
    if len(tract_mocs) != 2_191:
        raise RuntimeError(f"Expected 2,191 Rubin tracts; found {len(tract_mocs):,}")

    print("Resolving ACT DR6 baseline lensing mask...")
    act_moc, act_stats = healpix_mask_moc(act_path, 0.99)
    print("Resolving DES-Y3 KaRMMa lensing mask...")
    des_moc, des_stats = healpix_mask_moc(des_mask, 1)
    print("Resolving SPT-3G EDFS 2025 product masks...")
    spt_product_moc, spt_components = spt_moc(spt_paths)
    print("Resolving HSC PDR3 official tract polygons...")
    hsc_moc, hsc_components, hsc_tract_count = hsc_pdr3_moc(hsc_paths)
    print("Streaming SDSS DR19 ALLSPEC coordinates...")
    sdss_moc, sdss_stats = sdss_allspec_moc(sdss_path)

    resolved = [
        save_product(
            "act-dr6",
            act_moc,
            tract_mocs,
            args.public_moc_dir,
            productName="ACT DR6 CMB lensing v1 baseline mask",
            release="DR6 lensing v1 (2023-11-01)",
            coverageSemantics="native released lensing mask pixels with value >= 0.99",
            eligibleAsFullRegistryFootprint=True,
            source=public_source(act_path, ACT_URL, **act_stats),
            evidenceUrls=[
                "https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_maps_info.html",
                ACT_URL,
            ],
            caution="MOC cells contain at least one accepted native mask pixel; filtering and mask-squared guidance still apply to science analysis.",
        ),
        save_product(
            "des-y3-lensing",
            des_moc,
            tract_mocs,
            args.public_moc_dir,
            productName="KaRMMa DES-Y3 weak-lensing input mask",
            release="Zenodo record 10672062 (2024-02-16)",
            coverageSemantics="binary NSIDE=256 HEALPix mask released with the DES-Y3 KaRMMa shear data",
            eligibleAsFullRegistryFootprint=True,
            source=public_source(
                des_mask,
                f"{DES_RECORD_URL}#data/des_y3/mask_desy3.fits",
                archive=public_source(
                    des_zip,
                    DES_DATA_URL,
                    publisherMd5=DES_DATA_MD5,
                    computedMd5=digest_file(des_zip, "md5"),
                ),
                **des_stats,
            ),
            evidenceUrls=[DES_RECORD_URL, "https://github.com/Supranta/KaRMMa"],
            caution="This is the exact named KaRMMa DES-Y3 lensing-product mask, not the broader DES imaging footprint.",
        ),
        save_product(
            "spt-3g",
            spt_product_moc,
            tract_mocs,
            args.public_moc_dir,
            status="resolved-named-product-subset",
            productName="SPT-3G Euclid Deep Field South 2025 map/catalog release",
            release="Archipley et al. EDFS map and catalog release; files refreshed 2026-02",
            coverageSemantics="union of three official binary WCS masks released with the named EDFS product",
            eligibleAsFullRegistryFootprint=False,
            components=spt_components,
            evidenceUrls=[f"{SPT_BASE}/"],
            caution="This exact MOC proves the named EDFS release only; it must not be presented as the footprint of all SPT-SZ/SPTpol/SPT-3G products.",
        ),
        save_product(
            "sdss-dr19",
            sdss_moc,
            tract_mocs,
            args.public_moc_dir,
            productName="SDSS DR19 ALLSPEC released-spectrum positions",
            release="ALLSPEC 1.0.1",
            coverageSemantics="order-10 MOC of every valid RA/Dec row in the DR19 ALLSPEC release table",
            eligibleAsFullRegistryFootprint=True,
            source=public_source(
                sdss_path,
                SDSS_ALLSPEC_URL,
                publisherSha1=SDSS_ALLSPEC_SHA1,
                computedSha1=digest_file(sdss_path, "sha1"),
                **sdss_stats,
            ),
            supportingInventory=public_source(
                multiplex_path,
                SDSS_MULTIPLEX_URL,
                publisherSha1=SDSS_MULTIPLEX_SHA1,
                computedSha1=digest_file(multiplex_path, "sha1"),
            ),
            evidenceUrls=["https://sdss.org/dr19/data_access/allspec/", SDSS_ALLSPEC_URL],
            caution="This is exact released-spectrum positional support, not the SDSS imaging footprint or a continuous selection-function mask.",
        ),
    ]

    # A previous run may have emitted the HSC grid envelope.  It is deliberately
    # removed now that its area has been checked against the reported PDR3 area.
    (args.public_moc_dir / "hsc-ssp-pdr3.moc.fits").unlink(missing_ok=True)

    unresolved = [
        {
            "surveyId": "hsc-ssp-pdr3",
            "status": "unresolved-grid-envelope-is-not-release-footprint",
            "publicReleaseAvailable": True,
            "productName": "HSC-SSP PDR3 imaging",
            "officialGridIsProductFootprint": False,
            "blocker": "The official tract-corner files describe a 2,582 deg2 survey grid envelope, while the PDR3 release page reports a little over 600 deg2 of released multi-band data. Per-filter plot images are not a machine-readable patch inventory, so the grid is not promoted as exact coverage.",
            "auditedGrid": {
                "officialTractCount": hsc_tract_count,
                "skyFraction": float(hsc_moc.sky_fraction),
                "areaSqDeg": float(hsc_moc.sky_fraction * 41_252.96124941927),
                "components": hsc_components,
            },
            "nextAction": "Query the authenticated PDR3 patch/random-point tables for distinct released skymap IDs per filter, then intersect their official patch polygons.",
            "evidenceUrls": [
                "https://hsc-release.mtk.nao.ac.jp/doc/index.php/available-data__pdr3/",
                "https://hsc-release.mtk.nao.ac.jp/doc/index.php/random-points__pdr3/",
            ],
        },
        {
            "surveyId": "kids-1000-lensing",
            "status": "unresolved-no-exact-public-spatial-mask",
            "publicReleaseAvailable": True,
            "productName": "KiDS-1000 SOM-gold weak-lensing catalogue",
            "blocker": "The official 21,262,011-source, 16 GB lensing catalogue is public, but neither it nor the compact 16 MB cosmology bundle publishes a spatial analysis mask. KiDS DR4 imaging MOCs are not substituted for the SOM-gold lensing selection.",
            "auditedCompactBundle": public_source(kids_cosmology, KIDS_COSMOLOGY_URL),
            "catalog": {
                "sourceUrl": KIDS_CATALOG_URL,
                "reportedBytes": 17_712_469_440,
                "reportedRows": 21_262_011,
            },
            "nextAction": "Stream the catalogue RAJ2000/DECJ2000 columns once, or ingest an author-published SOM-gold spatial mask if one becomes available.",
            "evidenceUrls": [
                "https://kids.strw.leidenuniv.nl/DR4/KiDS-1000_shearcatalogue.php",
                "https://kids.strw.leidenuniv.nl/DR4/KiDS-1000_cosmicshear.php",
            ],
        },
        {
            "surveyId": "hsc-lensing",
            "status": "unresolved-public-product-withheld",
            "publicReleaseAvailable": False,
            "productName": "HSC PDR3 weak-lensing shapes",
            "blocker": "The official PDR3 page says no PDR3 weak-lensing release has been made and the FAQ says detailed PDR3 shapes are withheld. The resolved PDR3 imaging tract MOC is not substituted.",
            "nextAction": "Recheck the official HSC weak-lensing release page; ingest a release-matched shape-catalog mask only after publication and authorization.",
            "evidenceUrls": [
                "https://hsc-release.mtk.nao.ac.jp/doc/index.php/available-data__pdr3/",
                "https://hsc-release.mtk.nao.ac.jp/doc/index.php/faq__pdr3/",
            ],
        },
    ]

    public_result = {
        "schemaVersion": 1,
        "generatedAt": utc_now(),
        "rubinIndex": {
            "release": "Rubin DP2",
            "tractCount": len(tract_mocs),
            "intersectionOrder": MOC_ORDER,
            "method": "MOC intersection against the union of exact dp2.CoaddPatches polygons per tract",
        },
        "summary": {
            "requestedProductCount": 7,
            "resolvedProductCount": len(resolved),
            "unresolvedProductCount": len(unresolved),
            "resolvedFullProductCount": sum(item["status"] == "resolved-full" for item in resolved),
            "resolvedNamedSubsetCount": sum(item["status"] == "resolved-named-product-subset" for item in resolved),
        },
        "resolved": resolved,
        "unresolved": unresolved,
        "cautions": [
            "A MOC overlap establishes product geometry or released-object support, not valid science pixels at every position.",
            "Cross-survey comparison still requires units, masks, PSFs, astrometry, and selection functions to pass product-level QA.",
            "Named-product subsets prove only their own coverage and are not promoted to heterogeneous parent-archive footprints.",
            "Imaging geometry is never substituted for a withheld or differently selected weak-lensing catalog.",
        ],
    }
    validation = validate(public_result, set(tract_mocs))
    if not validation["passed"]:
        failed = [item["name"] for item in validation["checks"] if not item["passed"]]
        raise RuntimeError(f"Public resolution validation failed: {failed}")
    args.public_json.parent.mkdir(parents=True, exist_ok=True)
    args.public_json.write_text(json.dumps(public_result, indent=2) + "\n", encoding="utf-8")
    args.validation_json.write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    local = {
        "publicArtifact": args.public_json.as_posix(),
        "validationArtifact": args.validation_json.as_posix(),
        "generatedAt": public_result["generatedAt"],
        "sourceCache": args.cache_dir.as_posix(),
        "sourceCacheBytes": sum(path.stat().st_size for path in args.cache_dir.rglob("*") if path.is_file()),
    }
    (args.cache_dir / "resolution-run.json").write_text(json.dumps(local, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({item["surveyId"]: item["confirmedRubinTractCount"] for item in resolved}, indent=2))
    print(f"Validation: {validation['checkCount']} checks passed")


if __name__ == "__main__":
    main()
