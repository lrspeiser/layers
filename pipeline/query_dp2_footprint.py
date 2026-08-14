#!/usr/bin/env python3
"""Build a complete, resumable inventory of the Rubin DP2 coadd footprint.

The DP2 ``CoaddPatches`` TAP table contains one row per tract/patch with an
ICRS center and exact STC-S polygon.  This script downloads the table in small,
cached tract ranges, writes a detailed gzip-compressed JSON Lines inventory for
local spatial work, and publishes a compact tract index for the web client.

The Rubin access token is read from ``RUBIN_RSP_TOKEN`` or ``.env``.  It is
used only in the Authorization header and is never written to logs or output.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astropy.io.votable import parse_single_table


TAP_ENDPOINT = "https://data.lsst.cloud/api/tap"
TABLE_NAME = "dp2.CoaddPatches"
RELEASE = "DP2"
USER_AGENT = "Rubin-Light-Atlas/0.3 (+https://github.com/lrspeiser/rubin-light-atlas)"

# The account allows 1000 TAP requests per rolling minute.  The script is
# sequential and deliberately defaults far below that limit.  A full uncached
# run normally needs only 24 requests (stats, tract IDs, and 22 data chunks).
ACCOUNT_TAP_REQUESTS_PER_MINUTE = 1000
DEFAULT_REQUESTS_PER_MINUTE = 120
HARD_REQUESTS_PER_MINUTE = 600
DEFAULT_TRACTS_PER_CHUNK = 100

OFFICIAL_DEEP_COADD_DATASETS = 925_460
OFFICIAL_PRELIMINARY_TRACTS = 2_193
OFFICIAL_DEEP_COADD_DOC = "https://dp2.lsst.io/products/images/deep_coadd.html"
OFFICIAL_TRACT_TUTORIAL = "https://dp2.lsst.io/tutorials/notebook/notebook-102.html"

VOTABLE_NS = "{http://www.ivoa.net/xml/VOTable/v1.3}"
POLYGON_RE = re.compile(r"^POLYGON\s+ICRS\s+(.+)$", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_env_value(path: Path, name: str) -> str:
    if value := os.environ.get(name):
        return value.strip()
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    raise SystemExit(f"Missing {name}; set it in the environment or {path}")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def atomic_write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    if compact:
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    else:
        text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    atomic_write(path, text.encode("utf-8"))


def tap_sync_url() -> str:
    return f"{TAP_ENDPOINT}/sync"


def query_payload(query: str, *, maxrec: int) -> bytes:
    return urllib.parse.urlencode(
        {
            "REQUEST": "doQuery",
            "LANG": "ADQL",
            "MAXREC": str(maxrec),
            "QUERY": query,
        }
    ).encode("utf-8")


def request_votable(query: str, token: str, *, maxrec: int, attempts: int = 5) -> bytes:
    body = query_payload(query, maxrec=maxrec)
    request = urllib.request.Request(
        tap_sync_url(),
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/x-votable+xml",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = response.read()
                if response.status != 200:
                    raise RuntimeError(f"TAP returned HTTP {response.status}")
                assert_query_ok(payload)
                return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == attempts:
                detail = exc.read(1000).decode("utf-8", errors="replace")
                raise RuntimeError(f"TAP HTTP {exc.code}: {detail}") from exc
            retry_after = float(exc.headers.get("Retry-After", "0") or 0)
            time.sleep(max(retry_after, min(2**attempt, 30)))
        except urllib.error.URLError:
            if attempt == attempts:
                raise
            time.sleep(min(2**attempt, 30))
    raise AssertionError("unreachable")


def votable_info(payload: bytes) -> dict[str, str]:
    root = ET.fromstring(payload)
    info: dict[str, str] = {}
    for node in root.findall(f".//{VOTABLE_NS}INFO"):
        name = node.attrib.get("name")
        if name:
            info[name] = node.attrib.get("value", node.text or "")
    return info


def assert_query_ok(payload: bytes) -> None:
    info = votable_info(payload)
    if info.get("QUERY_STATUS") != "OK":
        raise RuntimeError(f"TAP query status: {info.get('QUERY_STATUS', 'missing')}")


def rows_from_votable(payload: bytes) -> list[dict[str, Any]]:
    table = parse_single_table(io.BytesIO(payload)).to_table(use_names_over_ids=True)
    rows: list[dict[str, Any]] = []
    for source in table:
        row: dict[str, Any] = {}
        for name in table.colnames:
            value = source[name]
            if hasattr(value, "mask") and value.mask:
                row[name] = None
            elif hasattr(value, "item"):
                row[name] = value.item()
            else:
                row[name] = value
        rows.append(row)
    return rows


class QuotaAwareTap:
    def __init__(
        self,
        token: str,
        cache_dir: Path,
        *,
        refresh: bool,
        requests_per_minute: int,
    ) -> None:
        self.token = token
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.request_interval = 60.0 / requests_per_minute
        self.next_request_at = time.monotonic()
        self.requests_made = 0
        self.records: list[dict[str, Any]] = []

    def fetch(self, name: str, query: str, *, maxrec: int) -> bytes:
        cache_path = self.cache_dir / f"{name}.vot.gz"
        payload: bytes | None = None
        source = "cache"
        if cache_path.exists() and not self.refresh:
            try:
                payload = gzip.decompress(cache_path.read_bytes())
                assert_query_ok(payload)
            except (OSError, ET.ParseError, RuntimeError):
                payload = None
        if payload is None:
            delay = self.next_request_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            payload = request_votable(query, self.token, maxrec=maxrec)
            atomic_write(cache_path, gzip.compress(payload, compresslevel=9, mtime=0))
            self.requests_made += 1
            self.next_request_at = time.monotonic() + self.request_interval
            source = "network"

        info = votable_info(payload)
        self.records.append(
            {
                "name": name,
                "source": source,
                "cache_file": cache_path.as_posix(),
                "query": query,
                "maxrec": maxrec,
                "response_sha256": sha256(payload),
                "response_bytes": len(payload),
                "query_timestamp": info.get("QUERY_TIMESTAMP"),
            }
        )
        return payload


def parse_polygon(region: str) -> list[list[float]]:
    match = POLYGON_RE.match(region.strip())
    if not match:
        raise ValueError(f"Unsupported s_region value: {region[:80]}")
    numbers = [float(value) for value in match.group(1).split()]
    if len(numbers) < 6 or len(numbers) % 2:
        raise ValueError(f"Invalid polygon coordinate count: {len(numbers)}")
    return [[round(numbers[index] % 360.0, 6), round(numbers[index + 1], 6)] for index in range(0, len(numbers), 2)]


def circular_mean_deg(values: list[float]) -> float:
    sine = sum(math.sin(math.radians(value)) for value in values)
    cosine = sum(math.cos(math.radians(value)) for value in values)
    return math.degrees(math.atan2(sine, cosine)) % 360.0


def minimal_ra_interval(values: list[float]) -> dict[str, float | bool]:
    points = sorted(value % 360.0 for value in values)
    if len(points) == 1:
        return {"start": points[0], "end": points[0], "width": 0.0, "wraps": False}
    gaps = [points[index + 1] - points[index] for index in range(len(points) - 1)]
    gaps.append(points[0] + 360.0 - points[-1])
    largest_index = max(range(len(gaps)), key=gaps.__getitem__)
    start = points[(largest_index + 1) % len(points)]
    end = points[largest_index]
    width = (end - start) % 360.0
    return {
        "start": round(start, 6),
        "end": round(end, 6),
        "width": round(width, 6),
        "wraps": start > end,
    }


def build_tract_summaries(patches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for patch in patches:
        grouped[patch["tract"]].append(patch)

    summaries: list[dict[str, Any]] = []
    for tract, tract_patches in sorted(grouped.items()):
        tract_patches.sort(key=lambda item: item["patch"])
        ras = [coordinate[0] for item in tract_patches for coordinate in item["polygon"]]
        decs = [coordinate[1] for item in tract_patches for coordinate in item["polygon"]]
        center_ras = [item["center"][0] for item in tract_patches]
        center_decs = [item["center"][1] for item in tract_patches]
        summaries.append(
            {
                "tract": tract,
                "center": [
                    round(circular_mean_deg(center_ras), 6),
                    round(sum(center_decs) / len(center_decs), 6),
                ],
                "bounds": {
                    "ra": minimal_ra_interval(ras),
                    "dec_min": round(min(decs), 6),
                    "dec_max": round(max(decs), 6),
                },
                "patch_count": len(tract_patches),
                "patches": [item["patch"] for item in tract_patches],
            }
        )
    return summaries


def write_jsonl_gzip(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, compresslevel=9, mtime=0) as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
                handle.write(b"\n")
    temporary.replace(path)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=repo_root / ".env")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=repo_root / "pipeline" / "results" / "coverage",
    )
    parser.add_argument(
        "--public-output",
        type=Path,
        default=repo_root / "public" / "data" / "coverage" / "rubin-dp2-footprint.json",
    )
    parser.add_argument("--tracts-per-chunk", type=int, default=DEFAULT_TRACTS_PER_CHUNK)
    parser.add_argument("--requests-per-minute", type=int, default=DEFAULT_REQUESTS_PER_MINUTE)
    parser.add_argument("--refresh", action="store_true", help="Repeat successful cached TAP queries")
    args = parser.parse_args()

    if not 1 <= args.requests_per_minute <= HARD_REQUESTS_PER_MINUTE:
        raise SystemExit(f"--requests-per-minute must be 1..{HARD_REQUESTS_PER_MINUTE}")
    if not 1 <= args.tracts_per_chunk <= 500:
        raise SystemExit("--tracts-per-chunk must be 1..500")

    token = read_env_value(args.env, "RUBIN_RSP_TOKEN")
    started_at = utc_now()
    cache_dir = args.results_dir / "cache"
    tap = QuotaAwareTap(
        token,
        cache_dir,
        refresh=args.refresh,
        requests_per_minute=args.requests_per_minute,
    )

    schema_query = (
        "SELECT column_name,datatype,description FROM TAP_SCHEMA.columns "
        f"WHERE table_name='{TABLE_NAME}' ORDER BY column_index"
    )
    schema_rows = rows_from_votable(tap.fetch("catalog-schema", schema_query, maxrec=100))
    required_columns = {"lsst_tract", "lsst_patch", "s_ra", "s_dec", "s_region"}
    returned_columns = {str(row["column_name"]) for row in schema_rows}
    if not required_columns.issubset(returned_columns):
        raise RuntimeError(f"{TABLE_NAME} is missing required columns: {sorted(required_columns - returned_columns)}")

    stats_query = f"SELECT COUNT(*) AS row_count FROM {TABLE_NAME}"
    stats_rows = rows_from_votable(tap.fetch("catalog-stats", stats_query, maxrec=10))
    expected_rows = int(stats_rows[0]["row_count"])

    tracts_query = f"SELECT DISTINCT lsst_tract FROM {TABLE_NAME} ORDER BY lsst_tract"
    tract_rows = rows_from_votable(tap.fetch("tract-ids", tracts_query, maxrec=10000))
    tract_ids = [int(row["lsst_tract"]) for row in tract_rows]
    print(f"Catalog reports {expected_rows:,} patches in {len(tract_ids):,} tracts")

    patches: list[dict[str, Any]] = []
    total_chunks = math.ceil(len(tract_ids) / args.tracts_per_chunk)
    for offset in range(0, len(tract_ids), args.tracts_per_chunk):
        selected = tract_ids[offset : offset + args.tracts_per_chunk]
        chunk_index = offset // args.tracts_per_chunk + 1
        first, last = selected[0], selected[-1]
        query = (
            "SELECT lsst_tract,lsst_patch,s_ra,s_dec,s_region "
            f"FROM {TABLE_NAME} WHERE lsst_tract BETWEEN {first} AND {last} "
            "ORDER BY lsst_tract,lsst_patch"
        )
        payload = tap.fetch(
            f"patches-{chunk_index:03d}-{first}-{last}",
            query,
            maxrec=args.tracts_per_chunk * 100 + 100,
        )
        source_rows = rows_from_votable(payload)
        selected_set = set(selected)
        unexpected = sorted({int(row["lsst_tract"]) for row in source_rows} - selected_set)
        if unexpected:
            raise RuntimeError(f"Chunk {chunk_index} returned unexpected tracts: {unexpected[:5]}")
        for row in source_rows:
            region = str(row["s_region"])
            patches.append(
                {
                    "tract": int(row["lsst_tract"]),
                    "patch": int(row["lsst_patch"]),
                    "center": [round(float(row["s_ra"]) % 360.0, 10), round(float(row["s_dec"]), 10)],
                    "polygon": parse_polygon(region),
                    "s_region": region,
                }
            )
        print(
            f"[{chunk_index:02d}/{total_chunks:02d}] tracts {first}-{last}: "
            f"{len(source_rows):,} patches ({tap.records[-1]['source']})"
        )
        progress = {
            "schema_version": 1,
            "release": RELEASE,
            "table": TABLE_NAME,
            "started_at": started_at,
            "updated_at": utc_now(),
            "chunks_complete": chunk_index,
            "chunks_total": total_chunks,
            "patches_loaded": len(patches),
            "expected_patches": expected_rows,
            "requests_made_this_run": tap.requests_made,
        }
        atomic_write_json(args.results_dir / "progress.json", progress)

    patches.sort(key=lambda item: (item["tract"], item["patch"]))
    duplicate_keys = len(patches) - len({(item["tract"], item["patch"]) for item in patches})
    if len(patches) != expected_rows or duplicate_keys:
        raise RuntimeError(
            f"Incomplete footprint: downloaded={len(patches)}, expected={expected_rows}, duplicates={duplicate_keys}"
        )
    if sorted({item["tract"] for item in patches}) != tract_ids:
        raise RuntimeError("Downloaded tract set differs from the catalog tract query")

    tracts = build_tract_summaries(patches)
    completed_at = utc_now()
    validation = {
        "tap_row_count_matches_download": len(patches) == expected_rows,
        "tap_tract_set_matches_download": len(tracts) == len(tract_ids),
        "duplicate_tract_patch_keys": duplicate_keys,
        "tract_patch_count_min": min(item["patch_count"] for item in tracts),
        "tract_patch_count_max": max(item["patch_count"] for item in tracts),
        "full_100_patch_tracts": sum(item["patch_count"] == 100 for item in tracts),
        "official_preliminary_tract_count": OFFICIAL_PRELIMINARY_TRACTS,
        "catalog_minus_preliminary_tracts": len(tracts) - OFFICIAL_PRELIMINARY_TRACTS,
        "official_preliminary_count_note": (
            "The official tutorial labels 2,193 as preliminary and warns that initially processed tracts "
            "might not survive final validation. The live DP2 CoaddPatches table is authoritative here."
        ),
        "official_deep_coadd_butler_datasets": OFFICIAL_DEEP_COADD_DATASETS,
        "dataset_count_note": (
            "Butler datasets are patch-band images; CoaddPatches rows are unique spatial tract/patch footprints."
        ),
    }
    detailed_path = args.results_dir / "dp2-coadd-patches.jsonl.gz"
    write_jsonl_gzip(detailed_path, patches)

    # Keep the browser payload intentionally tract-level. Exact patch polygons
    # remain in the detailed local artifact and are used for spatial indexing.
    public_payload = {
        "schemaVersion": 1,
        "release": RELEASE,
        "sourceTable": TABLE_NAME,
        "generatedAt": completed_at,
        "counts": {"tracts": len(tracts), "patches": len(patches)},
        "fields": ["tract", "center", "bounds", "patchCount", "patches"],
        "tracts": [
            [item["tract"], item["center"], item["bounds"], item["patch_count"], item["patches"]]
            for item in tracts
        ],
        "provenance": {
            "tapEndpoint": TAP_ENDPOINT,
            "officialDeepCoaddDocumentation": OFFICIAL_DEEP_COADD_DOC,
            "exactPatchGeometry": "pipeline/results/coverage/dp2-coadd-patches.jsonl.gz",
        },
        "validation": {
            "completeAgainstLiveTable": True,
            "catalogMinusPreliminaryTracts": len(tracts) - OFFICIAL_PRELIMINARY_TRACTS,
            "note": validation["official_preliminary_count_note"],
        },
    }
    atomic_write_json(args.public_output, public_payload, compact=True)

    manifest = {
        "schema_version": 1,
        "release": RELEASE,
        "catalog_table": TABLE_NAME,
        "catalog_schema": schema_rows,
        "tap_endpoint": TAP_ENDPOINT,
        "started_at": started_at,
        "completed_at": completed_at,
        "quota_policy": {
            "account_tap_requests_per_minute": ACCOUNT_TAP_REQUESTS_PER_MINUTE,
            "configured_requests_per_minute": args.requests_per_minute,
            "hard_script_cap_requests_per_minute": HARD_REQUESTS_PER_MINUTE,
            "sequential": True,
            "tracts_per_chunk": args.tracts_per_chunk,
            "successful_responses_cached": True,
        },
        "counts": {"tracts": len(tracts), "patches": len(patches)},
        "validation": validation,
        "products": {
            "detailed_patches": {
                "path": detailed_path.as_posix(),
                "bytes": detailed_path.stat().st_size,
                "sha256": sha256(detailed_path.read_bytes()),
            },
            "public_tract_index": {
                "path": args.public_output.as_posix(),
                "bytes": args.public_output.stat().st_size,
                "sha256": sha256(args.public_output.read_bytes()),
            },
        },
        "official_sources": [OFFICIAL_DEEP_COADD_DOC, OFFICIAL_TRACT_TUTORIAL],
        "requests_made_this_run": tap.requests_made,
        "queries": tap.records,
    }
    atomic_write_json(args.results_dir / "dp2-footprint-manifest.json", manifest)
    atomic_write_json(
        args.results_dir / "progress.json",
        {
            "schema_version": 1,
            "release": RELEASE,
            "table": TABLE_NAME,
            "started_at": started_at,
            "completed_at": completed_at,
            "status": "complete",
            "chunks_complete": total_chunks,
            "chunks_total": total_chunks,
            "patches_loaded": len(patches),
            "expected_patches": expected_rows,
            "requests_made_this_run": tap.requests_made,
        },
    )

    print(f"Complete: {len(patches):,} exact patches across {len(tracts):,} tracts")
    print(f"Detailed: {detailed_path}")
    print(f"Public:   {args.public_output}")


if __name__ == "__main__":
    main()
