#!/usr/bin/env python3
"""Ask which anomaly candidates can be checked at higher resolution.

The seventh operator, and the only one whose output is about the candidates
rather than about the sky. Rubin's PSF here is around 2 arcsec; HST and JWST
resolve tens of milliarcseconds. A residual that survives every internal check
is still only as good as the resolution it was found at, and the cheapest way to
kill a spurious one is to look at the same position with an instrument that
resolves it.

This does not fetch or analyse those images. It answers the prior question: for
each candidate in the register, does independent high-resolution imaging exist at
all, and what instrument and filter is it? A candidate with coverage is
verifiable now. A candidate without it cannot be confirmed or refuted this way,
and saying so is more useful than silence, because it separates "unconfirmed
because nobody looked" from "unconfirmed because the check was not possible".

Coverage is queried from the MAST CAOM archive over TAP. The query was validated
against COSMOS and the Hubble Ultra Deep Field, which return HST and JWST rows,
so a zero here is an absence of observations rather than a broken query.
"""

from __future__ import annotations

import argparse
import io
import json
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

warnings.filterwarnings("ignore")

from astropy.table import Table

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTER = ROOT / "public/data/layers/anomaly-register.json"
DEFAULT_CACHE = ROOT / "pipeline/results/highres-followup/cache"
DEFAULT_OUTPUT = ROOT / "pipeline/results/highres-followup"
DEFAULT_PUBLIC = ROOT / "public/data/layers/highres-followup/coverage.json"

TAP = "https://mast.stsci.edu/vo-tap/api/v0.1/caom/sync"
COLLECTIONS = ("HST", "JWST")
SEARCH_RADIUS_DEG = 0.02  # 72 arcsec, comfortably larger than any candidate scale
REQUEST_PAUSE_SECONDS = 0.5


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def query(cache: Path, ra: float, dec: float, key: str) -> Table | None:
    path = cache / f"{key}.vot"
    if not path.is_file():
        collections = ",".join(f"'{item}'" for item in COLLECTIONS)
        adql = (
            "SELECT TOP 200 obs_collection,instrument_name,filters,t_exptime,dataproduct_type,"
            "calib_level,s_ra,s_dec FROM dbo.ObsPointing WHERE "
            f"CONTAINS(POINT('ICRS',s_ra,s_dec),CIRCLE('ICRS',{ra:.7f},{dec:.7f},{SEARCH_RADIUS_DEG}))=1 "
            f"AND obs_collection IN ({collections})"
        )
        try:
            response = requests.post(
                TAP,
                data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable", "QUERY": adql},
                timeout=180,
            )
            response.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
        except Exception:
            return None
        finally:
            time.sleep(REQUEST_PAUSE_SECONDS)
    try:
        return Table.read(io.BytesIO(path.read_bytes()), format="votable")
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    args = parser.parse_args()

    register = json.loads(args.register.read_text(encoding="utf-8"))
    candidates = [row for rows in register.get("byOperator", {}).values() for row in rows]
    if not candidates:
        raise SystemExit("register holds no candidates; run build_anomaly_register.py first")

    records: list[dict[str, Any]] = []
    failed = 0
    for index, candidate in enumerate(candidates):
        ra = candidate["position"]["raDeg"]
        dec = candidate["position"]["decDeg"]
        key = f"{candidate['operator']}-{ra:.5f}{dec:+.5f}"
        table = query(args.cache, ra, dec, key)
        if table is None:
            failed += 1
            continue
        instruments = sorted({str(row["instrument_name"]) for row in table}) if len(table) else []
        filters = sorted({str(row["filters"]) for row in table})[:8] if len(table) else []
        collections = sorted({str(row["obs_collection"]) for row in table}) if len(table) else []
        records.append({
            **{k: candidate[k] for k in ("operator", "what", "regionId", "tract", "significance")},
            "position": candidate["position"],
            "highResolutionObservations": int(len(table)),
            "collections": collections,
            "instruments": instruments[:8],
            "filters": filters,
            "verifiable": bool(len(table)),
            "verdict": (
                "high-resolution imaging exists at this position and can confirm or refute it"
                if len(table)
                else "no HST or JWST observation covers this position, so this check cannot be run"
            ),
        })
        if (index + 1) % 10 == 0:
            print(f"  {index + 1}/{len(candidates)} checked", flush=True)

    verifiable = [item for item in records if item["verifiable"]]
    summary = {
        "schemaVersion": "layers-highres-followup-v1",
        "generatedAt": utc_now(),
        "archive": "MAST CAOM over TAP",
        "collections": list(COLLECTIONS),
        "searchRadiusArcsec": SEARCH_RADIUS_DEG * 3600,
        "queryValidation": (
            "The same query returns HST and JWST rows at COSMOS and the Hubble Ultra Deep Field, so a "
            "zero here is an absence of observations rather than a broken query."
        ),
        "counts": {
            "candidatesChecked": len(records),
            "queriesFailed": failed,
            "withHighResolutionCoverage": len(verifiable),
            "withoutCoverage": len(records) - len(verifiable),
        },
        "interpretation": (
            "This says which candidates can be checked, not whether any survived. A candidate without "
            "coverage is not refuted and not confirmed; it separates unconfirmed-because-nobody-looked "
            "from unconfirmed-because-the-check-was-impossible."
        ),
        "byOperator": {
            operator: {
                "candidates": sum(1 for item in records if item["operator"] == operator),
                "verifiable": sum(1 for item in records if item["operator"] == operator and item["verifiable"]),
            }
            for operator in sorted({item["operator"] for item in records})
        },
        "candidates": sorted(records, key=lambda item: (-item["highResolutionObservations"], item["operator"])),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nchecked {len(records)} candidates, {failed} queries failed")
    print(f"  with HST or JWST coverage: {len(verifiable)}")
    print(f"  without:                   {len(records) - len(verifiable)}")
    for operator, counts in summary["byOperator"].items():
        print(f"    {operator:20s} {counts['verifiable']}/{counts['candidates']} verifiable")


if __name__ == "__main__":
    main()
