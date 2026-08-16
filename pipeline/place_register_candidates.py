#!/usr/bin/env python3
"""Put the multi-wavelength candidates onto the Rubin frames they fall in.

The difference explorer shows optical disagreement: Rubin against Legacy, DES or
Pan-STARRS, drawn over the sky image. The other operators -- X-ray, radio and
SED -- produce the register's candidates, and those carry a sky position but no
picture. An eRASS1 source with no optical counterpart at Rubin depth is exactly
the kind of thing worth seeing *on* the star image, and until now it existed only
as a row in a table.

This converts each candidate's sky position into a fractional position inside its
region's frame, using the WCS of the reconciled product the previews were drawn
from, so the marker lands where the source is rather than where a table says it
is. Candidates whose region is not recorded are matched by asking which frame
actually contains the position.

A marker here means an operator flagged this position. It does not mean anything
is there: the register holds 34 candidates out of 12,954 comparisons, none is
confirmed by a second operator, and each carries the tests that would rule it
out. Their value on the image is context -- whether a flagged position sits on a
source, on the edge of the frame, or on nothing at all.
"""

from __future__ import annotations

import argparse
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

warnings.filterwarnings("ignore")

from astropy.io import fits
from astropy.wcs import WCS

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ROOT / "public/data/layers"
DEFAULT_PRODUCTS = ROOT / "pipeline/results/reconciled-regions-200"
DEFAULT_REGISTER = LAYERS / "anomaly-register.json"
DEFAULT_OUTPUT = LAYERS / "selected-regions/register-placements.json"

# A marker outside the frame is worse than no marker: it implies coverage that
# does not exist. Positions must land inside with a little margin.
EDGE_MARGIN_PIXELS = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def frame_wcs(products: Path) -> dict[str, tuple[WCS, tuple[int, int]]]:
    """The WCS and shape of every rendered region, keyed by region id."""
    out: dict[str, tuple[WCS, tuple[int, int]]] = {}
    for region in sorted(products.iterdir()):
        if not region.is_dir():
            continue
        for path in region.glob("*.fits"):
            try:
                with fits.open(path, memmap=False) as hdus:
                    header = hdus["RUBIN"].header
                    shape = np.asarray(hdus["RUBIN"].data).shape
                out[region.name] = (WCS(header).celestial, (int(shape[0]), int(shape[1])))
            except Exception:
                continue
            break
    return out


def place(wcs: WCS, shape: tuple[int, int], ra: float, dec: float) -> dict[str, float] | None:
    height, width = shape
    x, y = wcs.world_to_pixel_values(ra, dec)
    if not np.isfinite(x) or not np.isfinite(y):
        return None
    if not (EDGE_MARGIN_PIXELS <= x <= width - 1 - EDGE_MARGIN_PIXELS):
        return None
    if not (EDGE_MARGIN_PIXELS <= y <= height - 1 - EDGE_MARGIN_PIXELS):
        return None
    # Fractional, origin top-left, matching how the browser positions a marker
    # over the rendered image.
    return {"x": round(float(x) / (width - 1), 5), "y": round(1.0 - float(y) / (height - 1), 5)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    register = json.loads(args.register.read_text(encoding="utf-8"))
    candidates = [
        {**item, "operator": operator}
        for operator, items in (register.get("byOperator") or {}).items()
        for item in (items or [])
    ]
    frames = frame_wcs(args.products)

    placed: dict[str, list[dict[str, Any]]] = {}
    unplaced: list[dict[str, Any]] = []
    for candidate in candidates:
        position = candidate.get("position") or {}
        ra, dec = position.get("raDeg"), position.get("decDeg")
        if ra is None or dec is None:
            unplaced.append({"operator": candidate["operator"], "reason": "no position recorded"})
            continue

        region_id = candidate.get("regionId")
        # The region is not always recorded, so the fallback asks which frame
        # actually contains the position rather than guessing from a tract id.
        search = [region_id] if region_id in frames else list(frames)
        hit = None
        for key in search:
            wcs, shape = frames[key]
            spot = place(wcs, shape, float(ra), float(dec))
            if spot:
                hit = (key, spot)
                break
        if hit is None:
            unplaced.append({
                "operator": candidate["operator"],
                "regionId": region_id,
                "position": position,
                "reason": "no rendered frame contains this position",
            })
            continue

        key, spot = hit
        placed.setdefault(key, []).append({
            "operator": candidate["operator"],
            "what": candidate.get("what"),
            "detail": candidate.get("detail"),
            "significance": candidate.get("significance"),
            "sky": {"raDeg": float(ra), "decDeg": float(dec)},
            **spot,
        })

    payload = {
        "schemaVersion": "layers-register-placements-v1",
        "generatedAt": utc_now(),
        "purpose": (
            "Multi-wavelength register candidates placed on the Rubin frame they fall inside, so a "
            "flagged position can be seen on the sky image rather than read from a table."
        ),
        "method": (
            "Sky position converted through the WCS of the reconciled product the previews were "
            "drawn from. Candidates without a recorded region are matched by asking which frame "
            "contains the position."
        ),
        "meaning": (
            "A marker means an operator flagged this position, not that anything is there. The "
            "register holds 34 candidates out of 12,954 comparisons and none is confirmed by a "
            "second operator."
        ),
        "counts": {
            "candidates": len(candidates),
            "placed": sum(len(v) for v in placed.values()),
            "regionsWithACandidate": len(placed),
            "unplaced": len(unplaced),
        },
        "unplaced": unplaced,
        "byRegion": placed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], indent=2))
    for region_id, items in sorted(placed.items()):
        kinds = ", ".join(sorted({item["operator"] for item in items}))
        print(f"  {region_id:18s} {len(items):2d}  {kinds}")
    print(f"size {args.output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
