#!/usr/bin/env python3
"""Audit compact public coverage MOCs for surveys unresolved by the main index.

This deliberately writes only beneath ``pipeline/results/coverage/resolution-audit``.
It does not modify the production overlap index.  Catalog-derived MOCs are labelled
as conservative detection subsets and must not be presented as full survey
footprints.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord
from mocpy import FrequencyMOC, MOC, SFMOC


USER_AGENT = "Rubin-Light-Atlas/0.3 (+https://github.com/lrspeiser/rubin-light-atlas)"
MOC_ORDER = 10
MOCSERVER = "https://alasky.cds.unistra.fr/MocServer/query"


SOURCES: dict[str, dict[str, Any]] = {
    "wallaby-pdr2": {
        "coverageSemantics": "release-matched-product-footprint",
        "eligibleAsFullRegistryFootprint": True,
        "components": [
            {
                "id": "CDS/C/ASKAP/WALLABY-Pilot-DR2",
                "url": "https://alasky.cds.unistra.fr/HIPS3D/ASKAP/CDS_C_ASKAP_WALLABY-Pilot-DR2/Moc.fits",
                "title": "WALLABY Pilot Survey DR2 H I 21-cm HiPS",
            }
        ],
        "note": "Release-matched WALLABY Pilot DR2 HiPS footprint; coverage is not a guarantee of unmasked voxels.",
    },
    "alfalfa-alpha100": {
        "coverageSemantics": "release-matched-detection-catalog-subset",
        "eligibleAsFullRegistryFootprint": False,
        "components": [
            {
                "id": "CDS/J/ApJ/861/49/table2",
                "url": None,
                "title": "ALFALFA alpha.100 extragalactic H I source catalog",
            }
        ],
        "note": "Exact VizieR catalog-position MOC. It proves released detections only, not ALFALFA non-detection footprint coverage.",
    },
    "resolved-hi-archives": {
        "coverageSemantics": "release-matched-target-catalog-subset",
        "eligibleAsFullRegistryFootprint": False,
        "components": [
            {
                "id": "CDS/J/AJ/136/2563/table1",
                "url": None,
                "title": "THINGS nearby-galaxy H I survey catalog",
            },
            {
                "id": "CDS/J/AJ/144/134/galaxies",
                "url": None,
                "title": "LITTLE THINGS nearby dwarf-galaxy H I survey catalog",
            },
        ],
        "note": "Union of exact target-catalog MOCs. Cube/moment-map footprints extend beyond catalog positions and require archive-level materialization.",
    },
    "alma": {
        "coverageSemantics": "release-matched-named-program-subset",
        "eligibleAsFullRegistryFootprint": False,
        "components": [
            {
                "id": "CDS/C/PHANGS-ALMA",
                "url": "https://alasky.cds.unistra.fr/HIPS3D/PHANGS/CDS_C_PHANGS-ALMA/Moc.fits",
                "title": "PHANGS-ALMA CO(2-1) integrated-intensity HiPS",
            }
        ],
        "note": "Exact public PHANGS-ALMA product footprint. It is a conservative subset of the much larger ALMA Science Archive.",
    },
}


UNRESOLVED: dict[str, dict[str, Any]] = {
    "hsc-ssp-pdr3": {
        "blocker": "CDS exposes compact HSC PDR2 imaging MOCs, not PDR3. The PDR3 archive requires registration and no release-matched public MOC was identified.",
        "nextAction": "Materialize official PDR3 tract/patch polygons after authenticated archive access; do not substitute PDR2.",
        "evidenceUrls": [
            "https://hsc-release.mtk.nao.ac.jp/doc/index.php/sample-page/pdr3/",
            "https://hsc-release.mtk.nao.ac.jp/datasearch/",
        ],
    },
    "sdss-dr19": {
        "blocker": "DR19 spectroscopy is public, but its release-matched footprint must be derived from SkyServer/ALLSPEC/multiplex rows or the 985 MB compressed spAll summary; no compact DR19 MOC is published.",
        "nextAction": "Query distinct DR19 field centers/plate geometry in SkyServer or stream the coordinate columns from spAll, then build separate optical/APOGEE/IFU MOCs.",
        "evidenceUrls": [
            "https://www.sdss.org/dr19/data_access/get_data/",
            "https://www.sdss.org/dr19/data_access/allspec/",
        ],
    },
    "des-y3-lensing": {
        "blocker": "A compact official DES Y3 shear mask was not identified in the release index during this audit; the broader DES imaging footprint is not a valid substitute.",
        "nextAction": "Fetch a released Y3 metacalibration/mass-map mask and convert nonzero HEALPix pixels to a MOC.",
        "evidenceUrls": ["https://des.ncsa.illinois.edu/releases/y3a2"],
    },
    "kids-1000-lensing": {
        "blocker": "The official KiDS-1000 SOM-gold shear catalog is a 16 GB FITS table. Compact CDS MOCs found in this audit describe KiDS DR5 imaging, not the KiDS-1000 lensing selection.",
        "nextAction": "Locate the KiDS-1000 binary analysis mask or stream only RA/Dec/weight columns from the official catalog; do not substitute DR5 imaging.",
        "evidenceUrls": [
            "https://kids.strw.leidenuniv.nl/DR4/KiDS-1000_shearcatalogue.php",
            "https://kids.strw.leidenuniv.nl/DR4/lensing.php",
        ],
    },
    "hsc-lensing": {
        "blocker": "The HSC-Y3 page says weak-lensing products, including the shape catalog, will be released after the key papers are accepted; no exact public product mask was available for validation.",
        "nextAction": "Recheck the official release page and ingest the calibrated shape-catalog mask when published/authorized.",
        "evidenceUrls": [
            "https://hsc-release.mtk.nao.ac.jp/doc/index.php/hsc-weak-lensing-y3-results/"
        ],
    },
    "act-dr6": {
        "blocker": "NASA LAMBDA publishes the exact ACT DR6 lensing mask, but the NSIDE=4096 FITS product is about 1.5 GB and was intentionally not downloaded in this bounded audit.",
        "nextAction": "Stream or download the official baseline mask once, threshold using the release guidance, and persist a degraded MOC with the source hash.",
        "evidenceUrls": [
            "https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_maps_info.html",
            "https://portal.nersc.gov/project/act/dr6_lensing_v1/maps/baseline/",
        ],
    },
    "spt-3g": {
        "blocker": "SPT products have release-specific masks and the registry currently groups multiple SPT releases. No single product mask was selected and materialized in this audit.",
        "nextAction": "Choose a named SPT-SZ or SPT-3G map release, download its accompanying pixel mask, and create a product-specific MOC instead of a generic SPT footprint.",
        "evidenceUrls": [
            "https://pole.uchicago.edu/public/data/",
            "https://lambda.gsfc.nasa.gov/product/spt/spt_sz_comp_maps_info.html",
        ],
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, path: Path, refresh: bool) -> None:
    if path.exists() and not refresh:
        return
    request = urllib.request.Request(url, headers={"Accept": "application/fits", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = response.read()
    if not payload.startswith(b"SIMPLE"):
        raise RuntimeError(f"Non-FITS payload from {url}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def mocserver_url(identifier: str) -> str:
    return MOCSERVER + "?" + urllib.parse.urlencode(
        {"ID": identifier, "get": "smoc", "order": str(MOC_ORDER), "fmt": "fits"}
    )


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
        patch_mocs = MOC.from_polygons(polygons, max_depth=MOC_ORDER)
        result[tract] = patch_mocs[0] if len(patch_mocs) == 1 else patch_mocs[0].union(*patch_mocs[1:])
    return result


def component_filename(identifier: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in identifier).strip("-") + ".moc.fits"


def load_spatial_moc(path: Path) -> tuple[MOC, str]:
    """Load an S-MOC, or project the full frequency span of an SF-MOC."""
    try:
        return MOC.load(path, format="fits"), "S-MOC"
    except OSError as exc:
        if "Actual: SF-MOC" not in str(exc):
            raise
    sfmoc = SFMOC.load(path, format="fits")
    full_frequency_span = FrequencyMOC.from_frequency_ranges(
        MOC_ORDER,
        u.Quantity([sfmoc.min_frequency]),
        u.Quantity([sfmoc.max_frequency]),
    )
    return sfmoc.query_by_frequency(full_frequency_span), "SF-MOC projected over its full frequency span"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--patches",
        type=Path,
        default=Path("pipeline/results/coverage/dp2-coadd-patches.jsonl.gz"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("pipeline/results/coverage/resolution-audit"),
    )
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = args.output_dir / "mocs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    tract_mocs = load_tract_mocs(args.patches)
    if len(tract_mocs) != 2191:
        raise RuntimeError(f"Expected 2,191 Rubin tracts; found {len(tract_mocs):,}")

    overlaps: dict[str, list[int]] = {}
    audits: list[dict[str, Any]] = []
    for survey_id, source in SOURCES.items():
        component_mocs: list[MOC] = []
        evidence: list[dict[str, Any]] = []
        for component in source["components"]:
            url = component["url"] or mocserver_url(component["id"])
            path = raw_dir / component_filename(component["id"])
            fetch(url, path, args.refresh)
            moc, source_moc_type = load_spatial_moc(path)
            if moc.empty():
                raise RuntimeError(f"Empty MOC: {component['id']}")
            if moc.max_order > MOC_ORDER:
                moc = moc.degrade_to_order(MOC_ORDER)
            component_mocs.append(moc)
            evidence.append(
                {
                    "id": component["id"],
                    "title": component["title"],
                    "sourceUrl": url,
                    "cacheFile": path.as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                    "mocOrder": int(moc.max_order),
                    "skyFraction": float(moc.sky_fraction),
                    "sourceMocType": source_moc_type,
                }
            )
        survey_moc = component_mocs[0] if len(component_mocs) == 1 else component_mocs[0].union(*component_mocs[1:])
        output_moc = raw_dir / f"{survey_id}.union.moc.fits"
        survey_moc.save(output_moc, format="fits", overwrite=True)
        tract_ids = [tract for tract, tract_moc in tract_mocs.items() if not survey_moc.intersection(tract_moc).empty()]
        overlaps[survey_id] = tract_ids
        audits.append(
            {
                "surveyId": survey_id,
                "auditStatus": "resolved-full" if source["eligibleAsFullRegistryFootprint"] else "resolved-conservative-subset",
                "coverageSemantics": source["coverageSemantics"],
                "eligibleAsFullRegistryFootprint": source["eligibleAsFullRegistryFootprint"],
                "confirmedRubinTractCount": len(tract_ids),
                "confirmedRubinTractIds": tract_ids,
                "unionMoc": {
                    "cacheFile": output_moc.as_posix(),
                    "bytes": output_moc.stat().st_size,
                    "sha256": sha256(output_moc),
                    "mocOrder": int(survey_moc.max_order),
                    "skyFraction": float(survey_moc.sky_fraction),
                },
                "components": evidence,
                "note": source["note"],
            }
        )

    result = {
        "schemaVersion": 1,
        "generatedAt": utc_now(),
        "rubinIndex": {
            "source": args.patches.as_posix(),
            "tractCount": len(tract_mocs),
            "intersectionOrder": MOC_ORDER,
            "method": "MOC intersection against the union of exact dp2.CoaddPatches polygons per tract",
        },
        "audited": audits,
        "unresolved": [dict(surveyId=survey_id, auditStatus="unresolved", **value) for survey_id, value in UNRESOLVED.items()],
        "cautions": [
            "MOC overlap is archive/collection coverage, not proof of valid unmasked science pixels.",
            "Detection-catalog MOCs are intentionally conservative subsets and cannot measure non-detection footprint coverage.",
            "A named-program ALMA MOC cannot be promoted to the full heterogeneous ALMA Science Archive footprint.",
        ],
    }
    (args.output_dir / "resolution-audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "tract-overlaps.json").write_text(json.dumps(overlaps, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({item["surveyId"]: item["confirmedRubinTractCount"] for item in audits}, indent=2))


if __name__ == "__main__":
    main()
