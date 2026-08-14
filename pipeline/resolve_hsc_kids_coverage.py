#!/usr/bin/env python3
"""Resolve HSC PDR3 and KiDS-1000 coverage gaps without proxy footprints.

The KiDS result is deliberately a MOC of positions in the released weak-
lensing source catalogue.  It is not a continuous observing footprint or an
analysis mask.  HSC remains unresolved unless authenticated, release-matched
machine-readable coverage can be queried; the public tract grid is audited but
never substituted for released image pixels.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy_healpix import HEALPix
from mocpy import MOC


KIDS_URL = (
    "https://kids.strw.leidenuniv.nl/DR4/data_files/"
    "KiDS_DR4.1_ugriZYJHKs_SOM_gold_WL_cat.fits"
)
KIDS_RELEASE_PAGE = "https://kids.strw.leidenuniv.nl/DR4/KiDS-1000_shearcatalogue.php"
KIDS_EXPECTED_BYTES = 17_712_469_440
KIDS_EXPECTED_ROWS = 21_262_011
KIDS_EXPECTED_ROW_BYTES = 833
KIDS_DATA_OFFSET = 169_920
MOC_ORDER = 10
SKY_SQ_DEG = 41_252.96124941927

HSC_API = "https://hsc-release.mtk.nao.ac.jp/datasearch/api/catalog_jobs/preview"
HSC_RELEASE_PAGE = "https://hsc-release.mtk.nao.ac.jp/doc/index.php/available-data__pdr3/"
HSC_ACCESS_PAGE = "https://hsc-release.mtk.nao.ac.jp/doc/index.php/data-access__pdr3/"
HSC_FUNCTIONS_PAGE = "https://hsc-release.mtk.nao.ac.jp/rsrc/pdr3/stored_functions.html"
HSC_FAQ_PAGE = "https://hsc-release.mtk.nao.ac.jp/doc/index.php/faq__pdr3/"
HSC_SQL = """WITH released AS (
  SELECT DISTINCT 'wide' AS layer, m.skymap_id, m.filter01, h.hpx11_id
  FROM pdr3_wide.mosaic AS m
  JOIN pdr3_wide.mosaic_hpx11 AS h USING (skymap_id)
  UNION
  SELECT DISTINCT 'dud_rev' AS layer, m.skymap_id, m.filter01, h.hpx11_id
  FROM pdr3_dud_rev.mosaic AS m
  JOIN pdr3_dud_rev.mosaic_hpx11 AS h USING (skymap_id)
)
SELECT layer, skymap_id, filter01, hpx11_id
FROM released ORDER BY layer, filter01, skymap_id, hpx11_id
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def moc_from_bitmap(bitmap: np.ndarray) -> MOC:
    cells = np.flatnonzero(bitmap).astype(np.uint64)
    depths = np.full(cells.shape, MOC_ORDER, dtype=np.uint8)
    return MOC.from_healpix_cells(cells, depths, max_depth=MOC_ORDER)


def overlaps(moc: MOC, tracts: dict[int, MOC]) -> list[int]:
    return [tract for tract, tract_moc in tracts.items() if not moc.intersection(tract_moc).empty()]


def assert_kids_header(path: Path) -> int:
    if path.stat().st_size != KIDS_EXPECTED_BYTES:
        raise RuntimeError(
            f"KiDS catalogue byte count is {path.stat().st_size:,}; expected {KIDS_EXPECTED_BYTES:,}"
        )
    with fits.open(path, memmap=True, lazy_load_hdus=False) as hdus:
        header = hdus[1].header
        data_offset = int(hdus[1]._data_offset)
        expected_columns = {
            18: ("RAJ2000", "1D"),
            19: ("DECJ2000", "1D"),
            154: ("MASK", "1J"),
            161: ("model_SNratio", "1E"),
            193: ("weight", "1E"),
        }
        for index, expected in expected_columns.items():
            actual = (header[f"TTYPE{index}"], header[f"TFORM{index}"])
            if actual != expected:
                raise RuntimeError(f"Unexpected KiDS column {index}: {actual!r} != {expected!r}")
        if int(header["NAXIS1"]) != KIDS_EXPECTED_ROW_BYTES:
            raise RuntimeError("Unexpected KiDS FITS row width")
        if int(header["NAXIS2"]) != KIDS_EXPECTED_ROWS:
            raise RuntimeError("Unexpected KiDS FITS row count")
        if data_offset != KIDS_DATA_OFFSET:
            raise RuntimeError(f"Unexpected KiDS data offset: {data_offset}")
    return data_offset


def scan_kids_catalogue(path: Path, chunk_rows: int) -> tuple[MOC, MOC, dict[str, Any]]:
    data_offset = assert_kids_header(path)
    dtype = np.dtype(
        {
            "names": ["ra", "dec", "mask", "tile", "sn", "weight"],
            "formats": [">f8", ">f8", ">i4", "S16", ">f4", ">f4"],
            "offsets": [89, 97, 665, 669, 703, 829],
            "itemsize": KIDS_EXPECTED_ROW_BYTES,
        }
    )
    pixel_count = 12 * 4**MOC_ORDER
    released_cells = np.zeros(pixel_count, dtype=np.bool_)
    analysis_cells = np.zeros(pixel_count, dtype=np.bool_)
    hp = HEALPix(nside=2**MOC_ORDER, order="nested", frame="icrs")
    valid_rows = 0
    analysis_rows = 0
    mask_zero_rows = 0
    positive_weight_rows = 0
    positive_sn_rows = 0
    tile_names: set[str] = set()

    with path.open("rb") as handle:
        handle.seek(data_offset)
        remaining = KIDS_EXPECTED_ROWS
        while remaining:
            count = min(chunk_rows, remaining)
            block = handle.read(count * KIDS_EXPECTED_ROW_BYTES)
            if len(block) != count * KIDS_EXPECTED_ROW_BYTES:
                raise RuntimeError("Unexpected EOF while streaming KiDS rows")
            rows = np.frombuffer(block, dtype=dtype, count=count)
            finite = (
                np.isfinite(rows["ra"])
                & np.isfinite(rows["dec"])
                & (rows["ra"] >= 0)
                & (rows["ra"] < 360)
                & (rows["dec"] >= -90)
                & (rows["dec"] <= 90)
            )
            mask_zero = rows["mask"] == 0
            positive_weight = np.isfinite(rows["weight"]) & (rows["weight"] > 0)
            positive_sn = np.isfinite(rows["sn"]) & (rows["sn"] > 0)
            selected = finite & mask_zero & positive_weight & positive_sn

            valid_rows += int(np.count_nonzero(finite))
            mask_zero_rows += int(np.count_nonzero(mask_zero))
            positive_weight_rows += int(np.count_nonzero(positive_weight))
            positive_sn_rows += int(np.count_nonzero(positive_sn))
            analysis_rows += int(np.count_nonzero(selected))
            tile_names.update(
                value.decode("ascii", errors="replace").strip()
                for value in np.unique(rows["tile"])
                if value.strip()
            )

            if np.any(finite):
                cells = hp.lonlat_to_healpix(rows["ra"][finite] * u.deg, rows["dec"][finite] * u.deg)
                released_cells[np.asarray(cells, dtype=np.int64)] = True
            if np.any(selected):
                cells = hp.lonlat_to_healpix(
                    rows["ra"][selected] * u.deg, rows["dec"][selected] * u.deg
                )
                analysis_cells[np.asarray(cells, dtype=np.int64)] = True
            remaining -= count

    released_moc = moc_from_bitmap(released_cells)
    analysis_moc = moc_from_bitmap(analysis_cells)
    stats = {
        "rows": KIDS_EXPECTED_ROWS,
        "validCoordinateRows": valid_rows,
        "maskZeroRows": mask_zero_rows,
        "positiveWeightRows": positive_weight_rows,
        "positiveModelSnrRows": positive_sn_rows,
        "analysisSelectedRows": analysis_rows,
        "uniqueTheliTilesWithGoldSources": len(tile_names),
        "reportedKiDS1000SurveyTiles": 1_006,
        "surveyMinusGoldSourceTileCount": 1_006 - len(tile_names),
        "tileCountCaution": "The release page says the data set encompasses 1,006 survey tiles; the gold catalogue rows contain 988 distinct nonblank THELI_NAME values. This is further reason not to substitute the 1,006-tile imaging footprint for lensing-source support.",
        "selection": "finite RAJ2000/DECJ2000 AND MASK = 0 AND weight > 0 AND model_SNratio > 0",
        "releasedSupportOrder10CellCount": int(np.count_nonzero(released_cells)),
        "analysisSupportOrder10CellCount": int(np.count_nonzero(analysis_cells)),
    }
    return released_moc, analysis_moc, stats


def hsc_probe() -> dict[str, Any]:
    username = os.environ.get("HSC_SSP_USERNAME", "")
    password = os.environ.get("HSC_SSP_PASSWORD", "")
    payload = {
        "credential": {"account_name": username, "password": password},
        "catalog_job": {"sql": HSC_SQL, "release_version": "pdr3"},
    }
    request = urllib.request.Request(
        HSC_API,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Rubin-Light-Atlas/0.5"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as error:
        body = error.read(4096).decode("utf-8", errors="replace")
        status = int(error.code)
    except OSError as error:
        return {
            "checkedAt": utc_now(),
            "endpoint": HSC_API,
            "httpStatus": None,
            "result": "network-error",
            "detail": str(error),
            "credentialsPresent": bool(username and password),
        }
    body = re.sub(r"(?i)(password|token)[^,}\n]*", r"\1:[redacted]", body)
    return {
        "checkedAt": utc_now(),
        "endpoint": HSC_API,
        "httpStatus": status,
        "result": "authenticated" if 200 <= status < 300 else "authentication-required",
        "responseExcerpt": body[:512],
        "credentialsPresent": bool(username and password),
    }


def moc_record(moc: MOC, output: Path, public_href: str, tracts: dict[int, MOC]) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    moc.save(output, format="fits", overwrite=True)
    tract_ids = overlaps(moc, tracts)
    return {
        "href": public_href,
        "bytes": output.stat().st_size,
        "sha256": sha256_file(output),
        "order": int(moc.max_order),
        "skyFraction": float(moc.sky_fraction),
        "cellAreaSqDegAtOrder": float(SKY_SQ_DEG / (12 * 4**MOC_ORDER)),
        "rubinOverlapTractCount": len(tract_ids),
        "rubinOverlapTractIds": tract_ids,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalogue",
        type=Path,
        default=Path(
            "pipeline/results/coverage-gap-audit/sources/"
            "KiDS_DR4.1_ugriZYJHKs_SOM_gold_WL_cat.fits"
        ),
    )
    parser.add_argument(
        "--patches", type=Path, default=Path("pipeline/results/coverage/dp2-coadd-patches.jsonl.gz")
    )
    parser.add_argument(
        "--private-json", type=Path, default=Path("pipeline/results/coverage-gap-audit/manifest.json")
    )
    parser.add_argument(
        "--public-json", type=Path, default=Path("public/data/coverage/hsc-kids-gap-audit.json")
    )
    parser.add_argument(
        "--public-moc-dir", type=Path, default=Path("public/data/coverage/mocs-gap-audit")
    )
    parser.add_argument("--chunk-rows", type=int, default=100_000)
    args = parser.parse_args()

    tract_mocs = load_tract_mocs(args.patches)
    if len(tract_mocs) != 2_191:
        raise RuntimeError(f"Expected 2,191 Rubin tracts; found {len(tract_mocs):,}")

    released_moc, analysis_moc, stats = scan_kids_catalogue(args.catalogue, args.chunk_rows)
    catalogue_sha256 = sha256_file(args.catalogue)
    release_output = args.public_moc_dir / "kids-1000-gold-source-support.moc.fits"
    analysis_output = args.public_moc_dir / "kids-1000-analysis-source-support.moc.fits"
    release_record = moc_record(
        released_moc,
        release_output,
        f"/data/coverage/mocs-gap-audit/{release_output.name}",
        tract_mocs,
    )
    analysis_record = moc_record(
        analysis_moc,
        analysis_output,
        f"/data/coverage/mocs-gap-audit/{analysis_output.name}",
        tract_mocs,
    )
    probe = hsc_probe()

    public = {
        "schemaVersion": 1,
        "generatedAt": utc_now(),
        "purpose": "Release-matched machine-readable evidence audit for HSC PDR3 imaging and KiDS-1000 lensing",
        "summary": {
            "requestedProductCount": 2,
            "resolvedExactContinuousFootprintCount": 0,
            "resolvedExactCatalogueSupportCount": 1,
            "unresolvedCount": 1,
            "errorCount": 0,
        },
        "products": [
            {
                "surveyId": "kids-1000-lensing",
                "surveyName": "KiDS-1000",
                "family": "weak-lensing",
                "release": "KiDS DR4.1 SOM-gold weak-lensing catalogue (2020-12-07)",
                "productType": "released-source-positional-support",
                "status": "resolved-exact-catalogue-positional-support",
                "scienceReady": False,
                "displayReady": True,
                "comparisonReady": False,
                "bandOrObservable": "SOM-gold galaxy shear-source positions",
                "unit": "presence of released catalogue sources per order-10 HEALPix cell",
                "coverageSemantics": "Exact positional support of released catalogue rows at order 10; not a continuous observing footprint, pixel mask, or selection function.",
                "source": {
                    "url": KIDS_URL,
                    "releasePage": KIDS_RELEASE_PAGE,
                    "bytes": args.catalogue.stat().st_size,
                    "sha256": catalogue_sha256,
                    "rows": KIDS_EXPECTED_ROWS,
                    "fitsRowBytes": KIDS_EXPECTED_ROW_BYTES,
                },
                "releasedGoldSupport": release_record,
                "analysisSelectedSupport": analysis_record,
                "catalogueStatistics": stats,
                "blockers": [
                    "No author-published continuous KiDS-1000 SOM-gold spatial analysis mask was found.",
                    "Source-position support must not be treated as valid science pixels between sources.",
                    "Cross-field subtraction against Rubin is not scientifically defined by this geometry product.",
                ],
                "provenanceUrls": [KIDS_RELEASE_PAGE, KIDS_URL],
            },
            {
                "surveyId": "hsc-ssp-pdr3",
                "surveyName": "HSC-SSP",
                "family": "optical-imaging",
                "release": "PDR3 plus revised Deep/UltraDeep processing",
                "productType": "released-image-pixel-coverage",
                "status": "unresolved-authentication-required",
                "scienceReady": False,
                "displayReady": False,
                "comparisonReady": False,
                "bandOrObservable": "g,r,i,z,y and PDR3 narrow bands",
                "unit": "released HEALPix order-11 coverage cells",
                "coverageSemantics": "Would use distinct released mosaic_hpx11 rows, not the broader tract grid envelope.",
                "authoritativeQuery": HSC_SQL,
                "accessProbe": probe,
                "blockers": [
                    "The official SQL service requires a valid HSC account; no HSC credentials were available to this run.",
                    "The public tract/patch corner files span the skymap grid envelope and are rejected as a substitute for released image pixels.",
                    "Per-filter footprint plot images are not machine-readable coverage evidence.",
                ],
                "provenanceUrls": [
                    HSC_RELEASE_PAGE,
                    HSC_ACCESS_PAGE,
                    HSC_FUNCTIONS_PAGE,
                    HSC_FAQ_PAGE,
                ],
            },
        ],
        "rubinIntersection": {
            "release": "Rubin DP2",
            "tractCount": len(tract_mocs),
            "method": "MOC intersection with the union of exact dp2.CoaddPatches polygons per tract",
        },
        "guardrails": [
            "Broader KiDS DR4 imaging coverage was not substituted for KiDS-1000 lensing support.",
            "HSC skymap grid envelopes were not substituted for released PDR3 pixels.",
            "No Rubin-minus-lensing or Rubin-minus-CMB pixel subtraction is represented.",
        ],
    }
    serialized = json.dumps(public)
    if re.search(r"(?i)([A-Za-z]:\\|pipeline/results|password\s*[=:]|bearer\s+)", serialized):
        raise RuntimeError("Public manifest contains a local path or credential-like text")

    private = {
        "schemaVersion": 1,
        "generatedAt": public["generatedAt"],
        "publicManifest": str(args.public_json),
        "catalogueLocalPath": str(args.catalogue),
        "patchIndexLocalPath": str(args.patches),
        "catalogueSha256": catalogue_sha256,
        "catalogueStatistics": stats,
        "hscAccessProbe": probe,
        "publicSummary": public["summary"],
    }
    args.public_json.parent.mkdir(parents=True, exist_ok=True)
    args.private_json.parent.mkdir(parents=True, exist_ok=True)
    args.public_json.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    args.private_json.write_text(json.dumps(private, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(public["summary"], indent=2))


if __name__ == "__main__":
    main()
