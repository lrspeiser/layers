#!/usr/bin/env python3
"""Quota-aware DP2 deep-coadd coverage audit for the complete SPARC sample.

The script uses Rubin's authenticated SIAv2 endpoint only for metadata queries.
It never downloads image pixels and never writes the access token to output.
Successful per-target VOTables are cached so interrupted runs can resume without
spending the same SIA request twice.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

SIA_ENDPOINT = "https://data.lsst.cloud/api/sia/dp2/query"
SPARC_SIMBAD_ENDPOINT = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
SPARC_BIBCODE = "2016AJ....152..157L"

# The account currently allows 70 SIA requests per rolling minute.  A fixed
# interval is intentionally more conservative and easier to audit than bursts.
DEFAULT_SIA_REQUESTS_PER_MINUTE = 55
HARD_SIA_REQUESTS_PER_MINUTE = 60
VOTABLE_NAMESPACE = "{http://www.ivoa.net/xml/VOTable/v1.3}"


@dataclass(frozen=True)
class SparcTarget:
    sparc_id: str
    main_id: str
    slug: str
    ra_deg: float
    dec_deg: float
    major_axis_arcmin: float | None
    field_width_arcmin: float


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_env_value(path: Path, name: str) -> str:
    if value := os.environ.get(name):
        return value.strip()
    if not path.exists():
        raise SystemExit(f"Missing {name}; set it in the environment or {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    raise SystemExit(f"Missing {name} in {path}")


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def request_bytes(url: str, *, token: str | None = None, timeout: int = 180) -> bytes:
    headers = {
        "Accept": "application/x-votable+xml" if token else "text/csv",
        "User-Agent": "Rubin-Missing-Light-Atlas/0.2",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for {url.split('?', 1)[0]}")
        return response.read()


def fetch_sparc_coordinates(path: Path) -> None:
    query = (
        "SELECT h.ref_raw_id,b.main_id,b.ra,b.dec,b.otype_txt,"
        "b.galdim_majaxis,b.galdim_minaxis,b.galdim_angle "
        "FROM has_ref AS h "
        "JOIN ref AS r ON h.oidbibref=r.oidbib "
        "JOIN basic AS b ON h.oidref=b.oid "
        f"WHERE r.bibcode='{SPARC_BIBCODE}'"
    )
    params = urllib.parse.urlencode(
        {"request": "doQuery", "lang": "adql", "format": "csv", "maxrec": "1000", "query": query}
    )
    payload = request_bytes(f"{SPARC_SIMBAD_ENDPOINT}?{params}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def read_sparc_targets(path: Path) -> list[SparcTarget]:
    if not path.exists():
        fetch_sparc_coordinates(path)
    targets: list[SparcTarget] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            prefix = "table1:Name="
            raw_id = row["ref_raw_id"].strip()
            if not raw_id.startswith(prefix):
                continue
            sparc_id = raw_id.removeprefix(prefix).strip()
            major = float(row["galdim_majaxis"]) if row.get("galdim_majaxis", "").strip() else None
            # Include sky on both sides of the catalogued galaxy while avoiding
            # very large discovery queries. Detailed cutouts are sized later.
            field_width = max(12.0, min(36.0, 2.0 * major if major else 12.0))
            targets.append(
                SparcTarget(
                    sparc_id=sparc_id,
                    main_id=row["main_id"].strip(),
                    slug=slugify(sparc_id),
                    ra_deg=float(row["ra"]),
                    dec_deg=float(row["dec"]),
                    major_axis_arcmin=major,
                    field_width_arcmin=field_width,
                )
            )
    targets.sort(key=lambda target: target.sparc_id)
    if len(targets) != 175:
        raise RuntimeError(f"Expected 175 SPARC paper objects, found {len(targets)}")
    return targets


def parse_votable(payload: bytes) -> dict:
    root = ET.fromstring(payload)
    status_node = root.find(f".//{VOTABLE_NAMESPACE}INFO[@name='QUERY_STATUS']")
    status = status_node.attrib.get("value", "UNKNOWN") if status_node is not None else "UNKNOWN"
    fields = root.findall(f".//{VOTABLE_NAMESPACE}TABLE/{VOTABLE_NAMESPACE}FIELD")
    names = [field.attrib.get("name", "") for field in fields]
    rows = []
    for row_node in root.findall(f".//{VOTABLE_NAMESPACE}TABLEDATA/{VOTABLE_NAMESPACE}TR"):
        cells = [cell.text or "" for cell in row_node.findall(f"{VOTABLE_NAMESPACE}TD")]
        rows.append(dict(zip(names, cells, strict=False)))
    return {
        "query_status": status,
        "row_count": len(rows),
        "obs_ids": sorted({row.get("obs_id", "") for row in rows if row.get("obs_id")}),
        "publisher_ids": sorted(
            {row.get("obs_publisher_did", "") for row in rows if row.get("obs_publisher_did")}
        ),
        "access_formats": sorted({row.get("access_format", "") for row in rows if row.get("access_format")}),
        "wavelength_ranges_m": sorted(
            {
                (row.get("em_min", ""), row.get("em_max", ""))
                for row in rows
                if row.get("em_min") or row.get("em_max")
            }
        ),
    }


def sia_query_url(target: SparcTarget) -> str:
    radius_deg = target.field_width_arcmin / 60.0 / math.sqrt(2.0)
    params = {
        "POS": f"CIRCLE {target.ra_deg:.10f} {target.dec_deg:.10f} {radius_deg:.8f}",
        "CALIB": "3",
        "DPTYPE": "image",
        "DPSUBTYPE": "lsst.deep_coadd",
        "MAXREC": "10000",
    }
    return f"{SIA_ENDPOINT}?{urllib.parse.urlencode(params)}"


def cached_result(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        result = parse_votable(path.read_bytes())
    except (ET.ParseError, OSError):
        return None
    return result if result["query_status"] == "OK" else None


def query_with_retries(url: str, token: str, attempts: int = 4) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            return request_bytes(url, token=token)
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == attempts:
                raise
            retry_after = float(exc.headers.get("Retry-After", 0) or 0)
            time.sleep(max(retry_after, min(2**attempt, 30)))
        except urllib.error.URLError:
            if attempt == attempts:
                raise
            time.sleep(min(2**attempt, 30))
    raise AssertionError("unreachable")


def write_summary(path: Path, *, started_at: str, requests_made: int, targets: list[dict]) -> None:
    payload = {
        "schema_version": 1,
        "release": "DP2",
        "dataset_type": "lsst.deep_coadd",
        "sia_endpoint": SIA_ENDPOINT,
        "sparc_bibcode": SPARC_BIBCODE,
        "started_at": started_at,
        "updated_at": utc_now(),
        "quota_policy": {
            "account_sia_requests_per_minute": 70,
            "configured_requests_per_minute": DEFAULT_SIA_REQUESTS_PER_MINUTE,
            "hard_script_cap_requests_per_minute": HARD_SIA_REQUESTS_PER_MINUTE,
            "sequential": True,
            "cache_successful_responses": True,
        },
        "requests_made_this_run": requests_made,
        "targets_total": len(targets),
        "targets_with_deep_coadds": sum(item["deep_coadd_rows"] > 0 for item in targets),
        "targets": targets,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--env", type=Path, default=repo_root / ".env")
    parser.add_argument(
        "--sparc-coordinates",
        type=Path,
        default=Path(__file__).with_name("cache") / "sparc" / "simbad-sparc-paper-objects.csv",
    )
    parser.add_argument("--cache", type=Path, default=Path(__file__).with_name("cache") / "rubin" / "sparc-175")
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path(__file__).with_name("results") / "dp2-sparc-coverage.json",
    )
    parser.add_argument("--only", action="append", default=[], help="SPARC id or slug; repeat as needed")
    parser.add_argument("--refresh", action="store_true", help="Repeat successful cached SIA queries")
    parser.add_argument(
        "--requests-per-minute",
        type=int,
        default=DEFAULT_SIA_REQUESTS_PER_MINUTE,
        help=f"Conservative default; hard-capped at {HARD_SIA_REQUESTS_PER_MINUTE}",
    )
    args = parser.parse_args()

    if not 1 <= args.requests_per_minute <= HARD_SIA_REQUESTS_PER_MINUTE:
        raise SystemExit(f"--requests-per-minute must be between 1 and {HARD_SIA_REQUESTS_PER_MINUTE}")

    token = read_env_value(args.env, "RUBIN_RSP_TOKEN")
    targets = read_sparc_targets(args.sparc_coordinates)
    selected = {value.lower() for value in args.only}
    if selected:
        targets = [
            target
            for target in targets
            if target.sparc_id.lower() in selected or target.slug.lower() in selected
        ]
    if not targets:
        raise SystemExit("No matching SPARC targets")

    args.cache.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    requests_made = 0
    results: list[dict] = []
    request_interval = 60.0 / args.requests_per_minute
    next_request_at = time.monotonic()

    for index, target in enumerate(targets, start=1):
        cache_path = args.cache / f"{target.slug}-sia.xml"
        result = None if args.refresh else cached_result(cache_path)
        source = "cache"
        if result is None:
            delay = next_request_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            payload = query_with_retries(sia_query_url(target), token)
            cache_path.write_bytes(payload)
            result = parse_votable(payload)
            if result["query_status"] != "OK":
                raise RuntimeError(f"{target.sparc_id}: SIA status {result['query_status']}")
            requests_made += 1
            next_request_at = time.monotonic() + request_interval
            source = "network"

        item = {
            **asdict(target),
            "search_radius_deg": target.field_width_arcmin / 60.0 / math.sqrt(2.0),
            "deep_coadd_rows": result["row_count"],
            "obs_ids": result["obs_ids"],
            "publisher_ids": result["publisher_ids"],
            "access_formats": result["access_formats"],
            "wavelength_ranges_m": result["wavelength_ranges_m"],
            "response_file": cache_path.as_posix(),
            "source": source,
        }
        results.append(item)
        print(
            f"[{index:03d}/{len(targets):03d}] {target.sparc_id:<12} "
            f"rows={item['deep_coadd_rows']:<3} {source}"
        )
        write_summary(args.summary, started_at=started_at, requests_made=requests_made, targets=results)

    matches = [item for item in results if item["deep_coadd_rows"] > 0]
    print(f"Complete: {len(matches)}/{len(results)} SPARC targets have DP2 deep-coadd matches")
    for item in matches:
        print(f"  {item['sparc_id']}: {item['deep_coadd_rows']} rows")


if __name__ == "__main__":
    main()
