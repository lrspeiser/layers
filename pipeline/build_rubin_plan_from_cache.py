#!/usr/bin/env python3
"""Assemble a Rubin acquisition plan from already-cached SIA discovery responses.

``fetch_region_layers.py --mode discovery`` follows every discovered dataset with
a datalink request before it writes its plan. That second wave is thousands of
calls against the same Rubin quota the pixel fetch needs, and
``acquire_dp2_pixels.py`` never reads it: the acquirer only parses the ``discover``
VOTable recorded on each job. This builds the plan the acquirer wants directly
from the cached VOTables, so the quota goes to pixels instead.

Cache identity is recomputed the same way the fetcher derives it, and every
region must resolve to exactly one cached response or the run fails loudly. A
plan that silently covered fewer regions than requested would understate coverage
without anyone noticing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGIONS = ROOT / "pipeline/results/coverage/selected-regions-200.json"
DEFAULT_CACHE = ROOT / "pipeline/results/acquisition-200/cache/rubin-dp2/dp2"
DEFAULT_OUTPUT = ROOT / "pipeline/results/acquisition-200/discovery/acquisition-plan.json"

SIA_ENDPOINT = "https://data.lsst.cloud/api/sia/dp2/query"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", type=Path, default=DEFAULT_REGIONS)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cutout-size-arcmin", type=float, default=4.0)
    args = parser.parse_args()

    regions = json.loads(args.regions.read_text(encoding="utf-8"))["regions"]

    # Index the cache by response content so a region can be matched to its own
    # VOTable without re-deriving the fetcher's private key scheme.
    # The service echoes the query it answered, so the response itself carries the
    # position it covers. Matching on that is safer than re-deriving the fetcher's
    # private cache-key scheme, which could drift.
    pattern = re.compile(r"pos=CIRCLE\+(-?\d+\.\d+)\+(-?\d+\.\d+)", re.IGNORECASE)
    responses: dict[tuple[float, float], Path] = {}
    for path in sorted(args.cache.rglob("discover-*.vot")):
        match = pattern.search(path.read_text(encoding="utf-8", errors="replace")[:4000])
        if not match:
            continue
        responses.setdefault((round(float(match.group(1)), 6), round(float(match.group(2)), 6)), path)

    jobs: list[dict[str, Any]] = []
    missing: list[str] = []
    for region in regions:
        ra, dec = float(region["center"][0]), float(region["center"][1])
        path = responses.get((round(ra, 6), round(dec, 6)))
        if path is None:
            missing.append(region["id"])
            continue
        payload = path.read_bytes()
        jobs.append({
            "jobId": f"rubin-dp2/dp2/{region['id']}/discover",
            "surveyId": "rubin-dp2",
            "release": "DP2",
            "phase": "discover",
            "status": "cached",
            "band": None,
            "region": {
                "id": region["id"],
                "ra_deg": ra,
                "dec_deg": dec,
                "size_arcmin": args.cutout_size_arcmin,
                "tract": int(region["tract"]),
            },
            "request": {
                "method": "GET",
                "url": f"{SIA_ENDPOINT}?POS=CIRCLE+{ra:.10f}+{dec:.10f}",
                "purpose": "Discover overlapping deep-coadd patch-band datasets",
                "accept": "application/x-votable+xml",
            },
            "cache": {
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_bytes(payload),
                "retrievedAt": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "responseContentType": "application/x-votable+xml",
            },
        })

    if missing:
        raise SystemExit(
            f"{len(missing)} regions have no cached discovery response "
            f"(first: {', '.join(missing[:5])}). Re-run discovery for them before planning."
        )

    plan = {
        "schemaVersion": "layers-acquisition-plan-v1",
        "generatedAt": utc_now(),
        "mode": "discovery",
        "sourceRegions": args.regions.relative_to(ROOT).as_posix(),
        "note": (
            "Assembled from cached SIA discovery responses by build_rubin_plan_from_cache.py. "
            "Contains only the Rubin discover jobs that acquire_dp2_pixels.py reads; the datalink "
            "wave was skipped so the quota goes to pixel cutouts."
        ),
        "regions": [
            {
                "id": region["id"],
                "tract": region["tract"],
                "center": region["center"],
                "sizeArcmin": args.cutout_size_arcmin,
                "confirmedSurveyIds": region["confirmedSurveyIds"],
            }
            for region in regions
        ],
        "jobs": jobs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(jobs)} rubin discover jobs for {len(regions)} regions")
    print(f"-> {args.output.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
