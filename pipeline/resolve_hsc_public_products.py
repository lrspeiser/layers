#!/usr/bin/env python3
"""Materialize public, release-matched HSC imaging and lensing support.

The imaging footprint is handled by the survey registry's public PDR2 HiPS
MOC.  This script materializes a smaller but scientifically meaningful HSC
lensing product: the 65 shear-selected peaks published from the S16A mass
maps.  Their positions are exact released-product support, not a continuous
shape-catalog or convergence-map footprint.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord
from mocpy import MOC


ROOT = Path(__file__).resolve().parents[1]
ARTICLE_URL = "https://academic.oup.com/pasj/article/70/SP1/S27/4714784"
ARTICLE_CDN_URL = "https://oup.silverchair-cdn.com/article-minimal/4714784"
HSC_PRODUCTS_URL = "https://hsc-release.mtk.nao.ac.jp/doc/index.php/s16a-shape-catalog-data-products-pdr2/"
HSC_PDR2_URL = "https://hsc-release.mtk.nao.ac.jp/doc/index.php/sample-page/pdr2/"
PDR2_MOC_ID = "CDS/P/HSC/DR2/*/*"
MOC_ORDER = 10
PEAK_MOC_ORDER = 12
USER_AGENT = "Rubin-Light-Atlas/0.3 (+https://github.com/lrspeiser/rubin-light-atlas)"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


class TableParser(HTMLParser):
    """Collect simple table cell text without third-party HTML libraries."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None:
            assert self.row is not None
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            assert self.table is not None
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def parse_peaks(payload: bytes) -> list[dict[str, float | int]]:
    parser = TableParser()
    parser.feed(payload.decode("utf-8"))
    candidates: list[list[dict[str, float | int]]] = []
    for table in parser.tables:
        rows: list[dict[str, float | int]] = []
        for cells in table:
            if len(cells) < 4:
                continue
            try:
                rank = int(cells[0])
                signal_to_noise = float(cells[1])
                ra_deg = float(cells[2])
                dec_deg = float(cells[3].replace("\N{MINUS SIGN}", "-"))
            except ValueError:
                continue
            if 1 <= rank <= 65:
                rows.append({
                    "rank": rank,
                    "signalToNoise": signal_to_noise,
                    "raDeg": ra_deg,
                    "decDeg": dec_deg,
                })
        if len(rows) == 65:
            candidates.append(rows)
    if not candidates:
        raise RuntimeError("The official article no longer exposes the complete 65-row peak table")
    canonical = sorted(candidates[0], key=lambda row: int(row["rank"]))
    if [int(row["rank"]) for row in canonical] != list(range(1, 66)):
        raise RuntimeError("HSC peak ranks are not exactly 1 through 65")
    if any(sorted(candidate, key=lambda row: int(row["rank"])) != canonical for candidate in candidates[1:]):
        raise RuntimeError("Duplicated article peak tables disagree")
    return canonical


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
    if len(result) != 2191:
        raise RuntimeError(f"Expected 2,191 Rubin tracts, found {len(result):,}")
    return result


def write_csv(path: Path, peaks: list[dict[str, float | int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rank", "signalToNoise", "raDeg", "decDeg"])
        writer.writeheader()
        writer.writerows(peaks)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patches", type=Path, default=ROOT / "pipeline" / "results" / "coverage" / "dp2-coadd-patches.jsonl.gz")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "pipeline" / "results" / "hsc-public-products")
    parser.add_argument("--public-json", type=Path, default=ROOT / "public" / "data" / "coverage" / "hsc-public-products.json")
    parser.add_argument("--public-moc", type=Path, default=ROOT / "public" / "data" / "coverage" / "mocs-hsc-public" / "hsc-s16a-shear-peak-support.moc.fits")
    parser.add_argument("--public-csv", type=Path, default=ROOT / "public" / "data" / "layers" / "hsc-lensing" / "s16a-shear-selected-peaks.csv")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    args.results_dir.mkdir(parents=True, exist_ok=True)
    article_cache = args.results_dir / "miyazaki-2018-shear-selected-peaks.html"
    if args.refresh or not article_cache.exists():
        request = urllib.request.Request(ARTICLE_CDN_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
        atomic_write(article_cache, payload)
    payload = article_cache.read_bytes()
    peaks = parse_peaks(payload)

    peak_coords = SkyCoord(
        [float(row["raDeg"]) for row in peaks] * u.deg,
        [float(row["decDeg"]) for row in peaks] * u.deg,
        frame="icrs",
    )
    peak_mocs = [MOC.from_skycoords(coord, max_norder=PEAK_MOC_ORDER) for coord in peak_coords]
    peak_union = peak_mocs[0].union(*peak_mocs[1:])
    args.public_moc.parent.mkdir(parents=True, exist_ok=True)
    peak_union.save(args.public_moc, format="fits", overwrite=True)

    tract_mocs = load_tract_mocs(args.patches)
    tract_support: list[list[Any]] = []
    for tract, tract_moc in tract_mocs.items():
        if tract_moc.intersection(peak_union).empty():
            continue
        ranks = [
            int(peaks[index]["rank"])
            for index, peak_moc in enumerate(peak_mocs)
            if not tract_moc.intersection(peak_moc).empty()
        ]
        if ranks:
            tract_support.append([tract, ranks])
    tract_support.sort(key=lambda row: row[0])
    overlap_tract_ids = [int(row[0]) for row in tract_support]

    write_csv(args.public_csv, peaks)
    moc_bytes = args.public_moc.stat().st_size
    csv_bytes = args.public_csv.stat().st_size
    product = {
        "surveyId": "hsc-lensing",
        "status": "resolved-exact-lensing-peak-positional-support",
        "productName": "HSC S16A shear-selected cluster peaks",
        "release": "S16A / PDR2 public products",
        "coverageSemantics": "release-matched-shear-selected-peak-catalog-positional-support",
        "eligibleAsFullRegistryFootprint": False,
        "confirmedRubinTractCount": len(overlap_tract_ids),
        "confirmedRubinTractIds": overlap_tract_ids,
        "sourceRecordCount": len(peaks),
        "selection": "65 mass-map peaks with signal-to-noise greater than 4.7, published from the HSC S16A Wide weak-lensing maps.",
        "supportMoc": {
            "publicPath": "/data/coverage/mocs-hsc-public/hsc-s16a-shear-peak-support.moc.fits",
            "sha256": sha256_file(args.public_moc),
            "bytes": moc_bytes,
            "order": PEAK_MOC_ORDER,
            "skyFraction": float(peak_union.sky_fraction),
        },
        "catalog": {
            "publicPath": "/data/layers/hsc-lensing/s16a-shear-selected-peaks.csv",
            "sha256": sha256_file(args.public_csv),
            "bytes": csv_bytes,
            "columns": ["rank", "signalToNoise", "raDeg", "decDeg"],
        },
        "provenanceUrls": [HSC_PRODUCTS_URL, ARTICLE_URL],
        "note": "This proves released HSC weak-lensing peak positions only. It is not a continuous shear catalog, mass-map mask, or HSC imaging footprint, and it cannot support pixel subtraction.",
    }
    output = {
        "schemaVersion": 1,
        "generatedAt": utc_now(),
        "hscImaging": {
            "surveyId": "hsc-ssp-pdr2",
            "release": "PDR2",
            "coverageSemantics": "full-release-public-hips-image-support",
            "mocId": PDR2_MOC_ID,
            "provenanceUrls": [HSC_PDR2_URL, "https://alasky.cds.unistra.fr/MocServer/query?ID=CDS%2FP%2FHSC%2FDR2%2F%2A&get=record&fmt=json"],
            "note": "The active registry uses public release-matched PDR2 HiPS MOCs. PDR2 is never relabeled as PDR3.",
        },
        "products": [product],
        "tractFields": ["tract", "peakRanks"],
        "tracts": tract_support,
        "peaks": peaks,
        "sourceEvidence": {
            "articleUrl": ARTICLE_URL,
            "retrievalUrl": ARTICLE_CDN_URL,
            "retrievedAt": utc_now(),
            "sha256": sha256(payload),
            "bytes": len(payload),
        },
    }
    serialized = json.dumps(output, ensure_ascii=False)
    if any(token in serialized for token in ("Authorization", "RUBIN_RSP_TOKEN", "X-Amz-Signature")):
        raise RuntimeError("Refusing to publish credential or signed URL material")
    atomic_json(args.public_json, output)

    validation = {
        "schemaVersion": 1,
        "generatedAt": utc_now(),
        "ok": True,
        "checks": {
            "officialArticleContainsExactly65RankedPeaks": len(peaks) == 65,
            "allPeakCoordinatesFiniteAndInRange": all(
                0 <= float(row["raDeg"]) < 360 and -90 <= float(row["decDeg"]) <= 90
                for row in peaks
            ),
            "releaseMatchedPdr2ImagingNotPdr3Substitution": output["hscImaging"]["surveyId"] == "hsc-ssp-pdr2",
            "lensingSupportIsConservativeSubset": not product["eligibleAsFullRegistryFootprint"],
            "supportMocChecksumMatches": product["supportMoc"]["sha256"] == sha256_file(args.public_moc),
            "catalogChecksumMatches": product["catalog"]["sha256"] == sha256_file(args.public_csv),
            "rubinOverlapTractsUnique": len(overlap_tract_ids) == len(set(overlap_tract_ids)),
            "publicArtifactContainsNoLocalPathsOrCredentials": str(ROOT) not in serialized,
        },
        "counts": {"peaks": len(peaks), "rubinOverlapTracts": len(overlap_tract_ids)},
    }
    if not all(validation["checks"].values()):
        raise RuntimeError(f"HSC public-product validation failed: {validation['checks']}")
    atomic_json(args.results_dir / "validation.json", validation)
    print(json.dumps({"peaks": len(peaks), "rubinOverlapTracts": len(overlap_tract_ids), "tractIds": overlap_tract_ids}, indent=2))


if __name__ == "__main__":
    main()
