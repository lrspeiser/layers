#!/usr/bin/env python3
"""Cache Gaia DR3 astrometry for epoch-aware image registration.

Gaia is an acquisition dependency, not part of the deterministic release
pipeline.  The exact ADQL query, endpoint, response hash, and downloaded CSV
are retained locally so later registration runs do not depend on the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ENDPOINTS = (
    "https://gea.esac.esa.int/tap-server/tap/sync",
    "https://gaia.ari.uni-heidelberg.de/tap/sync",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(query: str) -> tuple[bytes, str]:
    body = urllib.parse.urlencode(
        {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query}
    ).encode()
    errors = []
    for endpoint in ENDPOINTS:
        for attempt in range(2):
            try:
                request = urllib.request.Request(
                    endpoint,
                    data=body,
                    headers={"User-Agent": "Layers-science/0.1"},
                )
                with urllib.request.urlopen(request, timeout=180) as response:
                    payload = response.read()
                if not payload.startswith(b"source_id,"):
                    raise RuntimeError(payload[:500].decode(errors="replace"))
                return payload, endpoint
            except Exception as error:  # one public mirror may be temporarily slow
                errors.append(f"{endpoint} attempt {attempt + 1}: {error}")
                time.sleep(2)
    raise RuntimeError("Gaia TAP acquisition failed:\n" + "\n".join(errors))


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--coverage",
        type=Path,
        default=root / "pipeline" / "results" / "dp2-sparc-coverage.json",
    )
    parser.add_argument(
        "--output", type=Path, default=root / "pipeline" / "cache" / "gaia-dr3"
    )
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    targets = json.loads(args.coverage.read_text(encoding="utf-8"))["targets"]
    selected = {value.lower() for value in args.only}
    if selected:
        targets = [
            target
            for target in targets
            if target["slug"].lower() in selected
            or target["sparc_id"].lower() in selected
        ]
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for target in targets:
        path = args.output / f"{target['slug']}.csv"
        query = (
            "SELECT source_id,ra,dec,pmra,pmdec,ref_epoch,phot_g_mean_mag,ruwe "
            "FROM gaiadr3.gaia_source "
            "WHERE 1=CONTAINS(POINT('ICRS',ra,dec),"
            f"CIRCLE('ICRS',{target['ra_deg']},{target['dec_deg']},0.16)) "
            "AND pmra IS NOT NULL AND pmdec IS NOT NULL "
            "AND phot_g_mean_mag < 21"
        )
        if path.is_file() and not args.refresh:
            endpoint = "cache"
        else:
            payload, endpoint = download(query)
            path.write_bytes(payload)
        rows = max(0, len(path.read_text(encoding="utf-8").splitlines()) - 1)
        records.append(
            {
                "objectId": target["slug"],
                "source": "Gaia DR3 gaia_source",
                "tapEndpoint": endpoint,
                "adql": query,
                "path": path.as_posix(),
                "rows": rows,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "license": "ESA/Gaia/DPAC; https://gea.esac.esa.int/archive/documentation/GDR3/Miscellaneous/sec_credit_and_citation_instructions/",
            }
        )
        print(f"[{target['slug']}] {rows} Gaia DR3 sources ({endpoint})", flush=True)
    manifest = {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "release": "Gaia DR3",
        "targets": records,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
