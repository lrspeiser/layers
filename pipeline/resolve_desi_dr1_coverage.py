#!/usr/bin/env python3
"""Resolve the DESI DR1 released HEALPix-product footprint against Rubin DP2.

DESI's checksummed tilepix.fits records only survey/program/NSIDE=64 nested
HEALPix combinations that produced good-data DR1 products.  This is the full
release-product envelope, not a claim of continuous target sampling inside a
cell and not a substitute for per-target spectrum discovery.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from mocpy import MOC

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "pipeline/results/desi-dr1-coverage"
PUBLIC = ROOT / "public/data/coverage"
SOURCE = RESULTS / "tilepix.fits"
TRACTS = ROOT / "pipeline/results/coverage/dp2-coadd-patches.jsonl.gz"
OUTPUT = PUBLIC / "desi-dr1-resolution.json"
MOC_OUTPUT = PUBLIC / "mocs-desi/desi-dr1-good-data-healpix.moc.fits"
URL = "https://data.desi.lbl.gov/public/dr1/spectro/redux/iron/healpix/tilepix.fits"
README_URL = "https://data.desi.lbl.gov/public/dr1/spectro/redux/iron/healpix/README-tilepix"
CHECKSUM_URL = "https://data.desi.lbl.gov/public/dr1/spectro/redux/iron/healpix/redux_iron_healpix.sha256sum"
EXPECTED_SHA256 = "cbfcc85ffecc78e3338e022c7fdc013c0efea1603a039a0381047e85e0f6e5ff"
MOC_ORDER = 10


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    if SOURCE.exists() and sha256(SOURCE) == EXPECTED_SHA256:
        return
    temporary = SOURCE.with_suffix(".fits.tmp")
    request = urllib.request.Request(URL, headers={"User-Agent": "Layers/1.0 science coverage audit"})
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output, length=8 * 1024 * 1024)
    if sha256(temporary) != EXPECTED_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("DESI tilepix checksum mismatch")
    temporary.replace(SOURCE)


def load_tract_mocs() -> dict[int, MOC]:
    grouped: dict[int, list[SkyCoord]] = defaultdict(list)
    with gzip.open(TRACTS, "rt", encoding="utf-8") as handle:
        for line in handle:
            patch = json.loads(line)
            grouped[int(patch["tract"])].append(SkyCoord(
                [point[0] for point in patch["polygon"]] * u.deg,
                [point[1] for point in patch["polygon"]] * u.deg,
                frame="icrs",
            ))
    output: dict[int, MOC] = {}
    for tract, polygons in grouped.items():
        pieces = MOC.from_polygons(polygons, max_depth=MOC_ORDER)
        output[tract] = pieces[0] if len(pieces) == 1 else pieces[0].union(*pieces[1:])
    return output


def main() -> None:
    download()
    with fits.open(SOURCE, memmap=True, checksum=True) as hdus:
        table = hdus["TILEPIX"].data
        cells = np.unique(np.asarray(table["HEALPIX"], dtype=np.uint64))
        depths = np.full(cells.shape, 6, dtype=np.uint8)
        product_moc = MOC.from_healpix_cells(cells, depths, max_depth=MOC_ORDER)
        pair_counts = Counter(
            f"{str(survey).strip()}/{str(program).strip()}"
            for survey, program in zip(table["SURVEY"], table["PROGRAM"], strict=True)
        )
        row_count = len(table)

    tract_mocs = load_tract_mocs()
    overlaps = sorted(tract for tract, moc in tract_mocs.items() if not moc.intersection(product_moc).empty())
    if len(tract_mocs) != 2191 or not overlaps or 9813 not in overlaps:
        raise RuntimeError("DESI/Rubin intersection failed completeness or known-positive checks")

    MOC_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    product_moc.save(MOC_OUTPUT, format="fits", overwrite=True)
    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "surveyId": "desi-dr1",
        "status": "resolved-full-release-product-envelope",
        "productName": "DESI DR1 good-data HEALPix spectral products",
        "release": "DR1 / iron / zcatalog v1",
        "coverageSemantics": "Exact union of released nested NSIDE=64 HEALPix product cells that DESI records as having good DR1 data; not continuous target sampling or a spectroscopic selection function.",
        "eligibleAsFullRegistryFootprint": True,
        "confirmedRubinTractCount": len(overlaps),
        "confirmedRubinTractIds": overlaps,
        "source": {
            "url": URL,
            "sha256": EXPECTED_SHA256,
            "bytes": SOURCE.stat().st_size,
            "rows": row_count,
            "uniqueHealpixCells": int(cells.size),
            "healpixOrder": 6,
            "ordering": "NESTED",
            "surveyProgramRowCounts": dict(sorted(pair_counts.items())),
        },
        "moc": {
            "href": "/data/coverage/mocs-desi/desi-dr1-good-data-healpix.moc.fits",
            "sha256": sha256(MOC_OUTPUT),
            "bytes": MOC_OUTPUT.stat().st_size,
            "maxOrder": MOC_ORDER,
            "skyFraction": float(product_moc.sky_fraction),
        },
        "rubinIntersection": {
            "release": "Rubin DP2",
            "tractCount": len(tract_mocs),
            "method": "MOC intersection against the union of exact dp2.CoaddPatches polygons per tract",
            "knownPositiveTract": 9813,
        },
        "guardrails": [
            "A released DESI HEALPix product can contain sparse spectra; it does not imply a spectrum at every sky coordinate inside the cell.",
            "A tract overlap is discovery eligibility, not a successful per-position spectrum retrieval.",
            "No spectral inference or Rubin-minus-DESI pixel subtraction is represented.",
        ],
        "provenanceUrls": [URL, README_URL, CHECKSUM_URL, "https://data.desi.lbl.gov/doc/releases/dr1/"],
    }
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rubinTracts": len(tract_mocs), "desiCells": int(cells.size), "overlapTracts": len(overlaps)}, indent=2))


if __name__ == "__main__":
    main()
