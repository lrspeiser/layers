#!/usr/bin/env python3
"""Fetch a bounded Gaia DR3 catalogue for one or more Rubin tract centers.

The output is catalogue evidence, never an image.  Screening counts are
deliberately descriptive: they identify sources worth foreground/motion review
but do not automatically classify a source or associate it with a galaxy.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ENDPOINTS = (
    "https://gea.esac.esa.int/tap-server/tap/sync",
    "https://gaia.ari.uni-heidelberg.de/tap/sync",
)
# A 4 arcmin square cutout has a 2.83 arcmin half-diagonal, so a 2 arcmin radius
# misses its corners. Fields at high galactic latitude are star-poor enough that
# the shortfall drops them below the minimum match count entirely.
DEFAULT_RADIUS_ARCMIN = 3.0
RADIUS_DEG = DEFAULT_RADIUS_ARCMIN / 60.0
USER_AGENT = "Layers Gaia bounded tract catalogue/1.0 (+https://rubin-light-atlas.vercel.app/)"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def number(row: dict[str, str], key: str) -> float | None:
    try:
        value = float(row.get(key, ""))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def query_region(session: requests.Session, region: dict[str, Any], output: Path) -> dict[str, Any]:
    region_id = str(region["id"])
    ra, dec = map(float, region["center"])
    query = (
        "SELECT source_id,ra,dec,parallax,parallax_error,pmra,pmra_error,pmdec,pmdec_error,ref_epoch,"
        "phot_g_mean_mag,phot_bp_mean_mag,phot_rp_mean_mag,ruwe "
        "FROM gaiadr3.gaia_source WHERE 1=CONTAINS(POINT('ICRS',ra,dec),"
        f"CIRCLE('ICRS',{ra:.8f},{dec:.8f},{RADIUS_DEG:.8f})) AND phot_g_mean_mag IS NOT NULL"
    )
    csv_path = output / region_id / "gaia-dr3.csv"
    endpoint_used = "cache"
    if not csv_path.is_file() or csv_path.stat().st_size == 0:
        last_error: Exception | None = None
        for endpoint in ENDPOINTS:
            for attempt in range(3):
                try:
                    response = session.post(
                        endpoint,
                        data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query},
                        timeout=180,
                    )
                    response.raise_for_status()
                    if b"source_id" not in response.content[:1000]:
                        raise RuntimeError("Gaia TAP response is not the requested CSV table")
                    write_atomic(csv_path, response.content)
                    endpoint_used = endpoint
                    break
                except Exception as error:
                    last_error = error
                    if attempt < 2:
                        time.sleep(2**attempt)
            if csv_path.is_file():
                break
        if not csv_path.is_file():
            raise RuntimeError(f"Gaia TAP query failed: {last_error}")
    rows = list(csv.DictReader(io.StringIO(csv_path.read_text(encoding="utf-8-sig", errors="replace"))))
    proper_motion_count = 0
    significant_motion_count = 0
    significant_parallax_count = 0
    screening_candidates: set[str] = set()
    elevated_ruwe_count = 0
    for row in rows:
        pmra, pmdec = number(row, "pmra"), number(row, "pmdec")
        pmra_error, pmdec_error = number(row, "pmra_error"), number(row, "pmdec_error")
        parallax, parallax_error = number(row, "parallax"), number(row, "parallax_error")
        ruwe = number(row, "ruwe")
        source_id = row.get("source_id", "")
        if pmra is not None and pmdec is not None:
            proper_motion_count += 1
        motion_sig = (
            math.hypot(pmra / pmra_error, pmdec / pmdec_error)
            if None not in (pmra, pmdec, pmra_error, pmdec_error) and pmra_error > 0 and pmdec_error > 0 else None
        )
        parallax_sig = abs(parallax / parallax_error) if parallax is not None and parallax_error and parallax_error > 0 else None
        if motion_sig is not None and motion_sig >= 5:
            significant_motion_count += 1
            screening_candidates.add(source_id)
        if parallax_sig is not None and parallax_sig >= 5:
            significant_parallax_count += 1
            screening_candidates.add(source_id)
        if ruwe is not None and ruwe > 1.4:
            elevated_ruwe_count += 1
    return {
        "regionId": region_id,
        "tract": int(region["tract"]),
        "center": [ra, dec],
        "radiusArcmin": RADIUS_DEG * 60,
        "surveyId": "gaia-dr3",
        "surveyName": "Gaia",
        "release": "DR3",
        "status": "available" if rows else "none",
        "recordCount": len(rows),
        "sourcesWithProperMotion": proper_motion_count,
        "significantProperMotionCount": significant_motion_count,
        "significantParallaxCount": significant_parallax_count,
        "foregroundScreeningCandidateCount": len(screening_candidates),
        "elevatedRuweCount": elevated_ruwe_count,
        "artifact": {"filename": csv_path.name, "bytes": csv_path.stat().st_size, "sha256": sha256(csv_path)},
        "localPath": csv_path.as_posix(),
        "endpointUsed": endpoint_used,
        "units": {"position": "deg ICRS", "parallax": "mas", "properMotion": "mas/yr", "photometry": "mag"},
        "readiness": "catalogue evidence for foreground/motion screening; epoch propagation and positional cross-match QA remain required",
        "caveats": [
            "Gaia is a source catalogue, not an image layer.",
            "Five-sigma parallax or proper-motion significance is a screening heuristic, not an automatic foreground classification.",
            "A cone source is not automatically associated with an extended Rubin object.",
            "RUWE above 1.4 is a diagnostic flag, not proof that the astrometric solution is invalid.",
        ],
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--radius-arcmin", type=float, default=DEFAULT_RADIUS_ARCMIN)
    args = parser.parse_args()
    regions = json.loads(args.regions.read_text(encoding="utf-8")).get("regions", [])
    if not regions:
        raise SystemExit("No regions supplied")
    global RADIUS_DEG
    RADIUS_DEG = args.radius_arcmin / 60.0
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    records = [query_region(session, region, args.output) for region in regions]
    manifest = {
        "schemaVersion": "layers-gaia-tract-catalog-v1",
        "generatedAt": utc_now(),
        "documentation": ["https://www.cosmos.esa.int/web/gaia/dr3", "https://gea.esac.esa.int/archive/"],
        "regions": records,
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"regions": len(records), "rows": sum(item["recordCount"] for item in records), "screeningCandidates": sum(item["foregroundScreeningCandidateCount"] for item in records)}, sort_keys=True))


if __name__ == "__main__":
    main()
