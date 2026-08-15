#!/usr/bin/env python3
"""Reduce the operator manifests to one small file the site can import.

The full manifests are analysis products and are large: the 190-region
reconciliation alone is 1.4 MB. Importing that into a page is how every tract
page broke earlier in this session, when a 525 KB module pushed a dynamic route's
worker chunk past what the runtime would load.

So the site never imports an analysis manifest. It imports this, which carries
only the fields the pages actually draw: the counts each operator card shows, and
one row per region holding its cleared gates. Everything else stays in the
manifests for analysis.

Re-run this after any operator re-runs, or the site will show the previous
numbers while the manifests hold the new ones.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ROOT / "public/data/layers"
DEFAULT_OUTPUT = LAYERS / "site-summary.json"


def load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", default="200", help="Region set suffix, or 'none' for the original files")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    suffix = "" if args.set in {"none", ""} else f"-{args.set}"
    regions_dir = LAYERS / "selected-regions"

    reconciliation = load(regions_dir / f"rubin-reference-reconciliation{suffix}.json")
    recovery = load(regions_dir / f"region-diffuse-recovery{suffix}.json")
    bandpass = load(regions_dir / f"bandpass-transfer{suffix}.json")
    anomalies = load(regions_dir / f"region-anomalies{suffix}.json")
    gaia = load(LAYERS / "gaia-crossmatch/comparison.json")
    gas = load(LAYERS / "hi-gas/comparison.json")
    btfr = load(LAYERS / "hi-gas/baryonic-tully-fisher.json")
    sed = load(LAYERS / "sed/consistency.json")
    lensing = load(LAYERS / "lensing-light/correlation.json")
    xray = load(LAYERS / "xray-counterparts/comparison.json")
    radio = load(LAYERS / "radio-counterparts/comparison.json")
    register = load(LAYERS / "anomaly-register.json")
    des = load(regions_dir / "des-dr2.json")
    desRecon = load(regions_dir / "rubin-des-reconciliation.json")
    crossCheck = load(regions_dir / "reference-cross-check.json")
    ps1Recon = load(regions_dir / "rubin-ps1-reconciliation.json")
    curve = load(regions_dir / "curve-of-growth.json")

    # Injection/recovery and the covariance measurement are a separate stage from
    # reconciliation, so a region that cleared them is only visible if both are
    # merged here. Showing reconciliation alone would understate every region.
    recovered = {item["regionId"] for item in (recovery or {}).get("regions", [])}
    gates = []
    for region in (reconciliation or {}).get("regions", []):
        cleared = list(region.get("clearedBlockers", []))
        if region["regionId"] in recovered:
            cleared += ["injection/recovery QA", "resampling covariance"]
        gates.append({"regionId": region["regionId"], "tract": region["tract"], "cleared": cleared})

    payload = {
        "schemaVersion": "layers-site-summary-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "regionSet": args.set,
        "reconciliation": (reconciliation or {}).get("counts"),
        "recovery": {
            **((recovery or {}).get("counts") or {}),
            "medianEmpiricalToFormalNoiseRatio": ((recovery or {}).get("resamplingCovariance") or {}).get(
                "medianRubinEmpiricalToFormalNoiseRatio"
            ),
        },
        "bandpass": {
            **((bandpass or {}).get("counts") or {}),
            "universality": (bandpass or {}).get("universality"),
        },
        "anomalies": (anomalies or {}).get("counts"),
        "gaia": {
            **((gaia or {}).get("counts") or {}),
            "epoch": (gaia or {}).get("epochConsistency"),
            "astrometry": (gaia or {}).get("astrometry"),
        },
        "gas": {
            **((gas or {}).get("counts") or {}),
            "tullyFisher": (btfr or {}).get("counts"),
            "residual": (btfr or {}).get("residual"),
        },
        "sed": {**((sed or {}).get("counts") or {}), "colourRelation": (sed or {}).get("colourRelation")},
        "lensing": {**((lensing or {}).get("counts") or {}), "surveys": (lensing or {}).get("surveys")},
        "xray": (xray or {}).get("counts"),
        "radio": (radio or {}).get("counts"),
        "des": {
            **((des or {}).get("counts") or {}),
            "reconciled": ((desRecon or {}).get("counts") or {}).get("reconciled"),
            "matched": ((desRecon or {}).get("counts") or {}).get("matched"),
        },
        "ps1": {
            "reconciled": ((ps1Recon or {}).get("counts") or {}).get("reconciled"),
            "matched": ((ps1Recon or {}).get("counts") or {}).get("matched"),
        },
        "crossCheck": {
            **((crossCheck or {}).get("counts") or {}),
            "pairs": (crossCheck or {}).get("pairs"),
            "findings": (crossCheck or {}).get("findings"),
            # Without this the site shows PS1's scale beside two verified ones
            # with no sign that its flux chain was never checked.
            "unverifiedChainFlags": (crossCheck or {}).get("unverifiedChainFlags"),
        },
        "curveOfGrowth": {
            "headline": (curve or {}).get("headline"),
            "attribution": (curve or {}).get("attribution"),
            "flatPairings": (curve or {}).get("flatPairings"),
            "dissentingPairings": (curve or {}).get("dissentingPairings"),
            "pairings": {
                name: {
                    "fields": value.get("fields"),
                    "sources": value.get("totalIsolatedSources"),
                    "curve": value.get("medianScaleByRadiusArcsec"),
                    "gain": value.get("medianGainAnchorToWidest"),
                    "interval": value.get("gainBootstrap95Interval"),
                    "verdict": value.get("verdict"),
                }
                for name, value in ((curve or {}).get("pairings") or {}).items()
                if value.get("sufficient")
            },
        },
        "register": {
            **((register or {}).get("counts") or {}),
            "comparisonsEvaluated": ((register or {}).get("comparisonsEvaluated") or {}).get("total"),
        },
        "gates": gates,
        "topAnomalies": ((anomalies or {}).get("topCandidates") or [])[:40],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    size = args.output.stat().st_size / 1024
    missing = [
        name
        for name, value in (
            ("reconciliation", reconciliation), ("recovery", recovery), ("bandpass", bandpass),
            ("anomalies", anomalies), ("gaia", gaia), ("gas", gas), ("sed", sed),
            ("lensing", lensing), ("xray", xray),
        )
        if value is None
    ]
    print(f"wrote {args.output.relative_to(ROOT).as_posix()}  {size:.0f} KB  {len(gates)} regions")
    if missing:
        print(f"not yet available, cards will read as pending: {', '.join(missing)}")


if __name__ == "__main__":
    main()
