#!/usr/bin/env python3
"""Cross-match every Rubin pilot field against public H I survey catalogs."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pyvo


TAP_URL = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap"
SEARCH_RADIUS_DEG = 0.1
SURVEYS = [
    {
        "id": "alfalfa-alpha100",
        "name": "ALFALFA",
        "release": "alpha.100",
        "table": "J/ApJ/861/49/table2",
        "ra": "RAJ2000",
        "dec": "DEJ2000",
        "key": "AGC",
        "documentation": "https://cdsarc.cds.unistra.fr/viz-bin/cat/J/ApJ/861/49",
    },
    {
        "id": "hipass-bright-galaxy",
        "name": "HIPASS",
        "release": "Bright Galaxy Catalog",
        "table": "J/AJ/128/16/table2",
        "ra": "RAJ2000",
        "dec": "DEJ2000",
        "key": "HIPASS",
        "documentation": "https://cdsarc.cds.unistra.fr/viz-bin/cat/J/AJ/128/16",
    },
    {
        "id": "wallaby-public-catalog",
        "name": "WALLABY",
        "release": "public catalog search",
        "table": "J/MNRAS/510/1716/tablea1",
        "ra": "_RA",
        "dec": "_DE",
        "key": "WALLABY",
        "documentation": "https://wallaby-survey.org/data/",
    },
]


def scalar(value):
    if value is None:
        return None
    if hasattr(value, "mask") and value.mask:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def query_catalog(service: pyvo.dal.TAPService, survey: dict, ra: float, dec: float) -> list[dict]:
    query = f"""
        SELECT TOP 25 *,
        DISTANCE(POINT('ICRS', {survey['ra']}, {survey['dec']}), POINT('ICRS', {ra}, {dec})) AS sep_deg
        FROM \"{survey['table']}\"
        WHERE 1=CONTAINS(
            POINT('ICRS', {survey['ra']}, {survey['dec']}),
            CIRCLE('ICRS', {ra}, {dec}, {SEARCH_RADIUS_DEG})
        )
        ORDER BY sep_deg
    """
    result = service.search(query)
    return [{name: scalar(row[name]) for name in result.fieldnames} for row in result]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline/results/dp2-sparc-coverage.json")
    parser.add_argument("--output", type=Path, default=root / "pipeline/output/hi-crossmatches")
    parser.add_argument("--public", type=Path, default=root / "public/data/layers/hi-crossmatches")
    args = parser.parse_args()

    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    targets = [item for item in coverage["targets"] if int(item.get("deep_coadd_rows", 0)) > 0]
    service = pyvo.dal.TAPService(TAP_URL)
    created = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schemaVersion": 1,
        "adapterContract": "layers-external-layer-v1",
        "createdAt": created,
        "source": {"service": "CDS VizieR TAP", "endpoint": TAP_URL},
        "targets": [],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    args.public.mkdir(parents=True, exist_ok=True)

    for target in targets:
        slug = target["slug"]
        ra, dec = float(target["ra_deg"]), float(target["dec_deg"])
        searches = []
        for survey in SURVEYS:
            matches = query_catalog(service, survey, ra, dec)
            searches.append({
                "survey": survey["name"],
                "release": survey["release"],
                "catalog": survey["table"],
                "searchRadiusArcmin": SEARCH_RADIUS_DEG * 60,
                "matchCount": len(matches),
                "documentation": survey["documentation"],
                "matches": matches,
            })

        alfalfa = next(item for item in searches if item["survey"] == "ALFALFA")
        detection = alfalfa["matches"][0] if alfalfa["matches"] else None
        if detection:
            agc = int(detection["AGC"])
            facts = [
                {"label": "H I MASS", "value": f"10^{float(detection['logMHI']):.2f}", "unit": "solar masses"},
                {"label": "21-CM FLUX", "value": f"{float(detection['HIflux']):.2f}", "unit": "Jy km/s"},
                {"label": "W50", "value": f"{float(detection['W50']):.0f}", "unit": "km/s"},
                {"label": "VELOCITY", "value": f"{float(detection['Vhel']):.0f}", "unit": "km/s"},
                {"label": "OFFSET", "value": f"{float(detection['sep_deg']) * 60:.2f}", "unit": "arcmin"},
            ]
            headline = f"ALFALFA detects AGC {agc} at this Rubin field"
            summary = "A real 21-cm catalog measurement supplies neutral-gas mass and velocity width. It is linked evidence, not a resolved H I map."
            availability = "published"
            dataset_ids = [f"ALFALFA AGC {agc}"]
        else:
            facts = [{"label": "SEARCHED", "value": "3", "unit": "public H I catalogs"}]
            headline = "No H I catalog counterpart found within 6 arcminutes"
            summary = "This is a catalog non-match, not proof that neutral hydrogen is absent or that every survey observed the field."
            availability = "metadata-match"
            dataset_ids = []

        public_record = {
            "schemaVersion": 1,
            "product": "Layers H I survey cross-match",
            "createdAt": created,
            "targetId": slug,
            "center": {"raDeg": ra, "decDeg": dec, "frame": "ICRS"},
            "searches": searches,
            "interpretation": {
                "status": "linked-evidence",
                "statement": summary,
                "caveats": [
                    "Catalog proximity alone does not establish source identity; the sub-arcminute ALFALFA counterpart should be checked against velocity and morphology.",
                    "A catalog non-match is not a gas-mass upper limit unless survey footprint, sensitivity, and line-width completeness are modeled.",
                ],
            },
        }
        record_path = args.public / f"{slug}.json"
        record_path.write_text(json.dumps(public_record, indent=2), encoding="utf-8")
        layer = {
            "id": "hi-survey-crossmatch",
            "survey": "ALFALFA + HIPASS + WALLABY",
            "release": "public H I cross-match",
            "instrument": "21-cm neutral-hydrogen surveys",
            "kind": "catalog",
            "availability": availability,
            "renderMode": "table",
            "bands": ["H I 21 cm"],
            "datasetCount": sum(item["matchCount"] for item in searches),
            "datasetIds": dataset_ids,
            "units": {"lineFlux": "Jy km/s", "velocity": "km/s", "mass": "log10 solar masses"},
            "calibration": "Published survey catalog quantities; no cross-survey recalibration applied",
            "hasVariance": bool(detection and detection.get("e_HIflux") is not None),
            "hasMask": False,
            "hasWcs": True,
            "note": "Every Rubin pilot field was queried against three public H I catalogs. Only actual detections are presented as gas measurements.",
            "scienceRole": "Neutral-gas mass and velocity width for baryonic-mass and dynamical checks.",
            "provenance": {"service": "CDS VizieR TAP", "catalogs": ", ".join(item["catalog"] for item in searches)},
            "assets": {"data": f"/data/layers/hi-crossmatches/{slug}.json"},
            "linkedEvidence": {
                "status": "detection" if detection else "no-catalog-match",
                "headline": headline,
                "summary": summary,
                "facts": facts,
                "links": [
                    {"label": "ALFALFA catalog", "href": SURVEYS[0]["documentation"]},
                    {"label": "WALLABY public data", "href": SURVEYS[2]["documentation"]},
                ],
            },
        }
        manifest["targets"].append({"targetId": slug, "layers": [layer]})
        print(f"[{slug}] ALFALFA={alfalfa['matchCount']} HIPASS={searches[1]['matchCount']} WALLABY={searches[2]['matchCount']}")

    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Complete: cross-matched {len(targets)} Rubin fields against {len(SURVEYS)} H I catalogs")


if __name__ == "__main__":
    main()
