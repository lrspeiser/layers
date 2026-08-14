#!/usr/bin/env python3
"""Independent structural and sampled-row validator for HSC/KiDS gap audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.io import fits
from mocpy import MOC


EXPECTED_BYTES = 17_712_469_440
EXPECTED_ROWS = 21_262_011
ROW_BYTES = 833
DATA_OFFSET = 169_920


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
        "--public-json", type=Path, default=Path("public/data/coverage/hsc-kids-gap-audit.json")
    )
    parser.add_argument(
        "--public-moc-dir", type=Path, default=Path("public/data/coverage/mocs-gap-audit")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("pipeline/results/coverage-gap-audit/validation.json")
    )
    args = parser.parse_args()

    document = json.loads(args.public_json.read_text(encoding="utf-8"))
    products = {item["surveyId"]: item for item in document["products"]}
    kids = products["kids-1000-lensing"]
    hsc = products["hsc-ssp-pdr3"]
    release_path = args.public_moc_dir / "kids-1000-gold-source-support.moc.fits"
    analysis_path = args.public_moc_dir / "kids-1000-analysis-source-support.moc.fits"
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, evidence: object) -> None:
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    check(
        "public summary distinguishes support from continuous footprint",
        document["summary"] == {
            "requestedProductCount": 2,
            "resolvedExactContinuousFootprintCount": 0,
            "resolvedExactCatalogueSupportCount": 1,
            "unresolvedCount": 1,
            "errorCount": 0,
        },
        document["summary"],
    )
    check(
        "KiDS source file has release byte count",
        args.catalogue.stat().st_size == EXPECTED_BYTES,
        args.catalogue.stat().st_size,
    )
    with fits.open(args.catalogue, memmap=True, lazy_load_hdus=False) as hdus:
        header_evidence = {
            "rows": int(hdus[1].header["NAXIS2"]),
            "rowBytes": int(hdus[1].header["NAXIS1"]),
            "dataOffset": int(hdus[1]._data_offset),
        }
    check(
        "KiDS FITS table structure matches release",
        header_evidence == {"rows": EXPECTED_ROWS, "rowBytes": ROW_BYTES, "dataOffset": DATA_OFFSET},
        header_evidence,
    )
    check(
        "full catalogue scan preserves imaging-versus-lensing tile distinction",
        kids["catalogueStatistics"]["uniqueTheliTilesWithGoldSources"] == 988
        and kids["catalogueStatistics"]["reportedKiDS1000SurveyTiles"] == 1_006
        and kids["catalogueStatistics"]["surveyMinusGoldSourceTileCount"] == 18,
        {
            "goldSourceTileNames": kids["catalogueStatistics"]["uniqueTheliTilesWithGoldSources"],
            "reportedSurveyTiles": kids["catalogueStatistics"]["reportedKiDS1000SurveyTiles"],
        },
    )
    source_sha = sha256_file(args.catalogue)
    check("KiDS source checksum matches manifest", source_sha == kids["source"]["sha256"], source_sha)
    release_sha = sha256_file(release_path)
    analysis_sha = sha256_file(analysis_path)
    check(
        "published MOC checksums match manifest",
        release_sha == kids["releasedGoldSupport"]["sha256"]
        and analysis_sha == kids["analysisSelectedSupport"]["sha256"],
        {"released": release_sha, "analysis": analysis_sha},
    )

    release_moc = MOC.from_fits(release_path)
    analysis_moc = MOC.from_fits(analysis_path)
    check(
        "analysis-selected support is contained by released-row support",
        analysis_moc.difference(release_moc).empty(),
        {
            "releasedSkyFraction": float(release_moc.sky_fraction),
            "analysisSkyFraction": float(analysis_moc.sky_fraction),
        },
    )

    dtype = np.dtype(
        {
            "names": ["ra", "dec", "mask", "sn", "weight"],
            "formats": [">f8", ">f8", ">i4", ">f4", ">f4"],
            "offsets": [89, 97, 665, 703, 829],
            "itemsize": ROW_BYTES,
        }
    )
    sample_indices = np.unique(np.linspace(0, EXPECTED_ROWS - 1, 4096, dtype=np.int64))
    released_inside = 0
    valid_sample_count = 0
    selected_inside = 0
    selected_count = 0
    with args.catalogue.open("rb") as handle:
        for index in sample_indices:
            handle.seek(DATA_OFFSET + int(index) * ROW_BYTES)
            row = np.frombuffer(handle.read(ROW_BYTES), dtype=dtype, count=1)[0]
            finite = bool(
                np.isfinite(row["ra"])
                and np.isfinite(row["dec"])
                and 0 <= row["ra"] < 360
                and -90 <= row["dec"] <= 90
            )
            if not finite:
                continue
            valid_sample_count += 1
            if bool(release_moc.contains_lonlat(float(row["ra"]) * u.deg, float(row["dec"]) * u.deg)):
                released_inside += 1
            selected = bool(row["mask"] == 0 and row["weight"] > 0 and row["sn"] > 0)
            if selected:
                selected_count += 1
                if bool(analysis_moc.contains_lonlat(float(row["ra"]) * u.deg, float(row["dec"]) * u.deg)):
                    selected_inside += 1
    check(
        "sampled catalogue positions are contained by their support MOCs",
        released_inside == valid_sample_count and selected_inside == selected_count,
        {
            "sampleRows": len(sample_indices),
            "validCoordinateRows": valid_sample_count,
            "releasedInside": released_inside,
            "analysisSelectedRows": selected_count,
            "analysisInside": selected_inside,
        },
    )
    check(
        "HSC remains unresolved after authenticated-service audit",
        hsc["status"] == "unresolved-authentication-required"
        and hsc["accessProbe"]["httpStatus"] in (401, 403),
        hsc["accessProbe"],
    )
    serialized = json.dumps(document)
    check(
        "public manifest is redacted",
        re.search(r"(?i)([A-Za-z]:\\|pipeline/results|password\s*[=:]|bearer\s+)", serialized) is None,
        "local-path and credential regex",
    )
    check(
        "guardrails reject proxy footprints",
        any("not substituted" in item for item in document["guardrails"])
        and kids["comparisonReady"] is False
        and hsc["comparisonReady"] is False,
        document["guardrails"],
    )

    result = {
        "schemaVersion": 1,
        "passed": all(item["passed"] for item in checks),
        "checkCount": len(checks),
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
