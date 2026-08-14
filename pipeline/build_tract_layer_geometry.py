#!/usr/bin/env python3
"""Measure where each tract preview image actually sits on the sky.

The tract page can only overlay layers on top of each other if it knows where
each one is. That is not safe to assume: the cached previews come from different
pipelines and are not a common product.

Measured on tract 10079, relative to a 4 arcmin Rubin-centred frame:

    unWISE W1/W2   4.40 x 4.40 arcmin, centre offset 0.02 arcmin
    ZTF zi         4.02 x 4.02 arcmin, centre offset 0.01 arcmin
    2MASS J/H/Ks   2.80 x 2.08 arcmin, centre offset 1.13 arcmin

Stretching all of those to fill the same square would put 2MASS more than an
arcminute from where it belongs and distort its aspect ratio, while looking
perfectly aligned. Since the geometry is recorded in each product's WCS, the
honest fix is to measure it and let the viewer place each layer correctly.

``CRVAL`` is not the answer: it is the projection reference, which for a cutout
taken from a larger tile can sit far outside the cutout. Reading it directly
suggested unWISE was 48 arcmin off when its centre is 0.02 arcmin off. The centre
is computed from the middle pixel instead.

Emits, per preview, the rectangle it occupies in the base frame as percentages,
so the browser can position it without repeating any astronomy.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

warnings.filterwarnings("ignore")

from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "public/data/layers/tract-product-index.json"
DEFAULT_REGIONS = ROOT / "pipeline/results/coverage/selected-regions-200.json"
DEFAULT_OUTPUT = ROOT / "public/data/layers/tract-layer-geometry.json"

# The frame every layer is placed into is the Rubin-versus-reference display
# grid, because that is the image the tract page actually renders as the base.
# It is 512 px at 0.4 arcsec, so 3.41 arcmin, not the 4.0 arcmin the cutouts
# were requested at. Using the requested size would shrink the base image to
# 85% of its own frame and push every other layer outward with it.
FALLBACK_FRAME_ARCMIN = 4.0
BASE_SURVEY_PRIORITY = ("legacy-surveys-dr10", "panstarrs-dr2")

# Where each survey's source pixels live, searched per region.
PRODUCT_ROOTS = {
    "2mass": "pipeline/results/uv-ir-time-pixels/products/{region}/2mass",
    "unwise": "pipeline/results/uv-ir-time-pixels/products/{region}/unwise",
    "ztf-dr": "pipeline/results/uv-ir-time-pixels/products/{region}/ztf",
    "hipass": "pipeline/results/radio-xray-hi/products/{region}",
    "act-dr6": "pipeline/results/lensing-cmb-pixels/products/{region}-act-dr6",
    "planck-2018": "pipeline/results/lensing-cmb-pixels/products/{region}-planck-2018",
    "des-y3-lensing": "pipeline/results/lensing-cmb-pixels/products/{region}-des-y3-lensing",
    "legacy-surveys-dr10": "pipeline/results/selected-region-comparisons/{region}",
}

# A layer whose centre sits further than this from the frame centre, or whose
# field differs from the frame by more than this fraction, is placed but marked
# so the viewer can say the overlay is approximate rather than pretending.
CENTRE_TOLERANCE_ARCMIN = 0.2
SCALE_TOLERANCE_FRACTION = 0.15


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def measure(path: Path, ra: float, dec: float) -> dict[str, Any] | None:
    """Return the sky footprint of the first 2-D image plane in a file."""
    try:
        with fits.open(path, memmap=False) as hdus:
            for hdu in hdus:
                data = getattr(hdu, "data", None)
                if data is None or np.ndim(data) < 2:
                    continue
                wcs = WCS(hdu.header).celestial
                if not wcs.has_celestial:
                    continue
                shape = data.shape[-2:]
                scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
                if not np.isfinite(scale) or scale <= 0:
                    continue
                centre_ra, centre_dec = wcs.pixel_to_world_values(
                    (shape[1] - 1) / 2.0, (shape[0] - 1) / 2.0
                )
                cos_dec = math.cos(math.radians(dec))
                offset_ra = (float(centre_ra) - ra) * cos_dec * 60.0
                offset_dec = (float(centre_dec) - dec) * 60.0
                return {
                    "shape": [int(shape[0]), int(shape[1])],
                    "pixelScaleArcsec": scale,
                    "heightArcmin": shape[0] * scale / 60.0,
                    "widthArcmin": shape[1] * scale / 60.0,
                    "centreOffsetRaArcmin": offset_ra,
                    "centreOffsetDecArcmin": offset_dec,
                    "centreOffsetArcmin": math.hypot(offset_ra, offset_dec),
                }
    except Exception:
        return None
    return None


def placement(geometry: dict[str, Any], frame_arcmin: float) -> dict[str, Any]:
    """Convert a sky footprint into a CSS rectangle inside the base frame.

    Percentages are relative to the base frame box. RA increases to the east,
    which is left on sky images, so a positive RA offset moves the layer left.
    """
    width = 100.0 * geometry["widthArcmin"] / frame_arcmin
    height = 100.0 * geometry["heightArcmin"] / frame_arcmin
    left = 50.0 - width / 2.0 - 100.0 * geometry["centreOffsetRaArcmin"] / frame_arcmin
    top = 50.0 - height / 2.0 - 100.0 * geometry["centreOffsetDecArcmin"] / frame_arcmin
    aligned = (
        geometry["centreOffsetArcmin"] <= CENTRE_TOLERANCE_ARCMIN
        and abs(geometry["widthArcmin"] - frame_arcmin) / frame_arcmin <= SCALE_TOLERANCE_FRACTION
        and abs(geometry["heightArcmin"] - frame_arcmin) / frame_arcmin <= SCALE_TOLERANCE_FRACTION
    )
    # Placement is trusted for every layer whose WCS parsed: the rectangle is
    # computed from it, and TAN distortion across a few arcmin is negligible.
    # `identicalFraming` only says whether a transform was needed at all, and
    # must not be read as a quality flag: a layer covering a different field is
    # placed correctly precisely because the rectangle accounts for it.
    return {
        "leftPercent": left,
        "topPercent": top,
        "widthPercent": width,
        "heightPercent": height,
        "identicalFraming": aligned,
        "requiresRepositioning": not aligned,
        "placementFromWcs": True,
        "note": (
            "Same field as the base image; drawn without a transform."
            if aligned
            else "Covers a different field or centre; positioned and scaled by its own WCS rather than "
            "stretched to fill the frame."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--regions", type=Path, default=DEFAULT_REGIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    regions = {
        item["id"]: item for item in json.loads(args.regions.read_text(encoding="utf-8"))["regions"]
    }
    products = json.loads(args.index.read_text(encoding="utf-8"))["products"]

    entries: list[dict[str, Any]] = []
    unmeasured: list[dict[str, Any]] = []
    cache: dict[tuple[str, str], dict[str, Any] | None] = {}
    for product in products:
        image = product.get("referenceImage") or product.get("rubinImage")
        if not image:
            continue
        region_id = product["regionId"]
        survey = product["surveyId"]
        region = regions.get(region_id)
        if region is None:
            unmeasured.append({"regionId": region_id, "surveyId": survey, "reason": "region not in the selection"})
            continue
        ra, dec = region["center"]

        key = (region_id, survey)
        if key not in cache:
            pattern = PRODUCT_ROOTS.get(survey)
            found = None
            if pattern:
                directory = ROOT / pattern.format(region=region_id)
                if directory.is_dir():
                    for candidate in sorted(directory.rglob("*.fits*")):
                        # Skip whole-tile ancillary planes, which cover far more
                        # sky than the cutout and would place the layer wrongly.
                        result = measure(candidate, ra, dec)
                        if result and result["widthArcmin"] <= 3 * FALLBACK_FRAME_ARCMIN:
                            found = result
                            break
            cache[key] = found
        geometry = cache[key]
        if geometry is None:
            unmeasured.append({"regionId": region_id, "surveyId": survey, "reason": "no local FITS with usable WCS"})
            continue
        entries.append({
            "regionId": region_id,
            "tract": product["tract"],
            "surveyId": survey,
            "surveyName": product.get("surveyName"),
            "family": product.get("family"),
            "band": product.get("bandOrObservable") or product.get("referenceBand"),
            "image": image,
            "geometry": geometry,
        })

    # The frame is whatever the base optical layer actually covers in that region.
    frames: dict[str, float] = {}
    for region_id in {item["regionId"] for item in entries}:
        candidates = [item for item in entries if item["regionId"] == region_id]
        chosen = next(
            (
                item
                for survey in BASE_SURVEY_PRIORITY
                for item in candidates
                if item["surveyId"] == survey
            ),
            None,
        )
        frames[region_id] = (
            chosen["geometry"]["widthArcmin"] if chosen else FALLBACK_FRAME_ARCMIN
        )
    for item in entries:
        item["baseFrameArcmin"] = frames[item["regionId"]]
        item["placement"] = placement(item["geometry"], frames[item["regionId"]])

    aligned = [item for item in entries if item["placement"]["identicalFraming"]]
    payload = {
        "schemaVersion": "layers-tract-layer-geometry-v1",
        "generatedAt": utc_now(),
        "baseFrameArcminByRegion": frames,
        "fallbackFrameArcmin": FALLBACK_FRAME_ARCMIN,
        "method": (
            "Each preview's sky footprint is measured from the WCS of its source FITS: field size from "
            "the pixel scale and shape, centre from the middle pixel. CRVAL is deliberately not used, "
            "because for a cutout taken from a larger tile it is the projection reference and can sit "
            "far outside the image."
        ),
        "tolerances": {
            "centreArcmin": CENTRE_TOLERANCE_ARCMIN,
            "scaleFraction": SCALE_TOLERANCE_FRACTION,
        },
        "counts": {
            "measured": len(entries),
            "placeable": len(entries),
            "identicalFraming": len(aligned),
            "repositioned": len(entries) - len(aligned),
            "unmeasured": len(unmeasured),
        },
        "placementSemantics": (
            "Every measured layer is placeable: its rectangle comes from its own WCS. Needing a "
            "transform is not a defect, and a repositioned layer is no less correctly registered than "
            "one that happened to share the base framing."
        ),
        "unmeasured": unmeasured,
        "layers": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # The tract route is a dynamic page that already imports about ten large
    # JSON files. Adding the verbose record to it pushed that route's worker
    # chunk past its size limit and every tract page failed to render, so the
    # viewer gets a compact positional-array file with only the drawn fields.
    compact = {
        "schemaVersion": "layers-tract-layer-geometry-compact-v1",
        "generatedAt": payload["generatedAt"],
        "fields": ["tract", "surveyId", "surveyName", "family", "band", "image",
                   "left", "top", "width", "height", "repositioned",
                   "widthArcmin", "heightArcmin", "offsetArcmin"],
        "layers": [
            [
                item["tract"], item["surveyId"], item["surveyName"], item["family"],
                str(item["band"])[:48], item["image"],
                round(item["placement"]["leftPercent"], 3),
                round(item["placement"]["topPercent"], 3),
                round(item["placement"]["widthPercent"], 3),
                round(item["placement"]["heightPercent"], 3),
                1 if item["placement"]["requiresRepositioning"] else 0,
                round(item["geometry"]["widthArcmin"], 3),
                round(item["geometry"]["heightArcmin"], 3),
                round(item["geometry"]["centreOffsetArcmin"], 3),
            ]
            for item in entries
        ],
    }
    # Merge the placement onto the product index the tract page already imports.
    # Adding a separate import to that route broke every tract page: it is a
    # dynamic route already carrying about ten large JSON files, and the extra
    # module pushed its worker chunk past what the runtime would load. Attaching
    # eight numbers to records the page reads anyway costs nothing.
    index_payload = json.loads(args.index.read_text(encoding="utf-8"))
    by_key = {(item["regionId"], item["surveyId"], str(item["band"])): item for item in entries}
    attached = 0
    for product in index_payload["products"]:
        key = (
            product["regionId"],
            product["surveyId"],
            str(product.get("bandOrObservable") or product.get("referenceBand")),
        )
        match = by_key.get(key)
        if match is None:
            continue
        product["skyPlacement"] = {
            "leftPercent": round(match["placement"]["leftPercent"], 3),
            "topPercent": round(match["placement"]["topPercent"], 3),
            "widthPercent": round(match["placement"]["widthPercent"], 3),
            "heightPercent": round(match["placement"]["heightPercent"], 3),
            "requiresRepositioning": match["placement"]["requiresRepositioning"],
            "widthArcmin": round(match["geometry"]["widthArcmin"], 3),
            "heightArcmin": round(match["geometry"]["heightArcmin"], 3),
            "centreOffsetArcmin": round(match["geometry"]["centreOffsetArcmin"], 3),
        }
        attached += 1
    args.index.write_text(json.dumps(index_payload, indent=2) + chr(10), encoding="utf-8")
    print(f"attached skyPlacement to {attached} products in the tract product index")

    compact_path = args.output.with_name("tract-layer-geometry-compact.json")
    compact_path.write_text(json.dumps(compact, separators=(",", ":")) + chr(10), encoding="utf-8")

    print(f"compact viewer file: {compact_path.stat().st_size / 1024:.0f} KB")
    print(f"measured {len(entries)} layer previews across {len({item['regionId'] for item in entries})} regions")
    print(f"  drawn without a transform: {len(aligned)}")
    print(f"  repositioned by their own WCS: {len(entries) - len(aligned)}")
    print(f"  unmeasured: {len(unmeasured)}")


if __name__ == "__main__":
    main()
