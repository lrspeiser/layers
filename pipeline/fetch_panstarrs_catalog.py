#!/usr/bin/env python3
"""Cache calibrated Pan-STARRS DR2 stellar photometry for filter audits.

This is an acquisition step, not part of the deterministic release.  It keeps
the exact MAST query response and a checksum-addressed manifest so downstream
audits can run without network access or silently changing catalog rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ENDPOINT = "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/mean.csv"
COLUMNS = (
    "objID",
    "raMean",
    "decMean",
    "epochMean",
    "nDetections",
    "nr",
    "ni",
    "rMeanPSFMag",
    "rMeanPSFMagErr",
    "rMeanKronMag",
    "iMeanPSFMag",
    "iMeanPSFMagErr",
    "iMeanKronMag",
    "qualityFlag",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def query_url(target: dict, radius: float) -> str:
    params = {
        "ra": f"{target['ra_deg']:.10f}",
        "dec": f"{target['dec_deg']:.10f}",
        "radius": f"{radius:.6f}",
        "pagesize": "10000",
        "nDetections.gte": "5",
        "nr.gte": "2",
        "ni.gte": "2",
        "columns": "[" + ",".join(COLUMNS) + "]",
    }
    return f"{ENDPOINT}?{urllib.parse.urlencode(params)}"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--coverage",
        type=Path,
        default=root / "pipeline" / "results" / "dp2-sparc-coverage.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "pipeline" / "cache" / "panstarrs-dr2-mean",
    )
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--radius-deg", type=float, default=0.16)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    targets = json.loads(args.coverage.read_text(encoding="utf-8"))["targets"]
    selected = {value.lower() for value in args.only}
    if selected:
        targets = [
            target
            for target in targets
            if target["slug"].lower() in selected or target["sparc_id"].lower() in selected
        ]
    args.output.mkdir(parents=True, exist_ok=True)
    previous_path = args.output / "manifest.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.is_file() else {"targets": []}
    previous_by_id = {record["objectId"]: record for record in previous.get("targets", [])}
    records = [] if not selected else [record for record in previous.get("targets", []) if record["objectId"] not in {target["slug"] for target in targets}]

    for target in targets:
        path = args.output / f"{target['slug']}.csv"
        url = query_url(target, args.radius_deg)
        source = "cache"
        if args.refresh or not path.is_file() or previous_by_id.get(target["slug"], {}).get("queryUrl") != url:
            request = urllib.request.Request(url, headers={"User-Agent": "Layers-science/0.1"})
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = response.read()
            first_line = payload.splitlines()[0].decode("utf-8") if payload else ""
            if first_line != ",".join(COLUMNS):
                raise RuntimeError(f"Unexpected Pan-STARRS catalog response: {payload[:500]!r}")
            path.write_bytes(payload)
            source = "network"
        rows = max(0, len(path.read_text(encoding="utf-8").splitlines()) - 1)
        records.append(
            {
                "objectId": target["slug"],
                "source": "Pan-STARRS DR2 MeanObjectView",
                "service": "MAST Catalogs API",
                "queryUrl": url,
                "radiusDeg": args.radius_deg,
                "columns": list(COLUMNS),
                "path": path.as_posix(),
                "rows": rows,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "retrieval": source,
                "documentation": "https://catalogs.mast.stsci.edu/docs/panstarrs.html",
            }
        )
        print(f"[{target['slug']}] {rows} Pan-STARRS DR2 mean objects ({source})", flush=True)

    manifest = {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "release": "Pan-STARRS DR2",
        "catalog": "MeanObjectView",
        "targets": sorted(records, key=lambda record: record["objectId"]),
    }
    previous_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
