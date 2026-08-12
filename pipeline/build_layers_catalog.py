#!/usr/bin/env python3
"""Build the public, survey-neutral Layers target and layer index.

The catalog intentionally contains metadata and provenance only.  Restricted
Rubin pixels remain in the local layer store until a publication policy and
comparison QA explicitly allow an image product to be published.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def mosaic_state(summary_path: Path) -> dict[str, dict]:
    if not summary_path.is_file():
        return {}
    return {item["target"]["slug"]: item for item in load_json(summary_path)}


def rubin_layer(target: dict, mosaic: dict | None, selected_dataset_ids: list[str]) -> dict:
    rows = int(target["deep_coadd_rows"])
    if rows == 0:
        availability = "not-covered"
        note = "Authenticated DP2 SIA query returned no deep-coadd datasets."
    elif mosaic is None:
        availability = "metadata-match"
        note = "SIA footprint match exists; calibrated pixel coverage has not been validated."
    elif mosaic.get("science_coverage"):
        availability = "available-local"
        note = "Calibrated image, variance, and mask mosaics exist in the local layer store; pixels are not public."
    else:
        availability = "no-valid-pixels"
        note = mosaic.get("coverage_note") or "Footprint metadata matched, but no usable science pixels intersect the field."

    bands = []
    if mosaic:
        bands = [name for name, product in mosaic.get("bands", {}).items() if product.get("science_coverage")]
    return {
        "id": "rubin-dp2-deep-coadd",
        "survey": "Vera C. Rubin Observatory",
        "release": "DP2",
        "instrument": "LSSTCam",
        "kind": "image",
        "availability": availability,
        "renderMode": "image" if availability == "published" else "metadata",
        "bands": bands,
        "datasetCount": len(selected_dataset_ids),
        "datasetIds": selected_dataset_ids,
        "units": {"image": "nJy", "variance": "nJy^2"},
        "calibration": "Rubin Science Pipelines deep coadd",
        "hasVariance": availability == "available-local",
        "hasMask": availability == "available-local",
        "hasWcs": rows > 0,
        "note": note,
        "provenance": {
            "service": "Rubin Science Platform SIA v2 + DataLink",
            "datasetType": "lsst.deep_coadd",
            "queryStatus": "OK",
        },
    }


def sparc_layer(target: dict, bibcode: str) -> dict:
    return {
        "id": "sparc-2016",
        "survey": "SPARC",
        "release": "2016 master sample",
        "instrument": "Spitzer photometry + published rotation curves",
        "kind": "profile",
        "availability": "available",
        "renderMode": "plot",
        "bands": ["3.6um"],
        "units": {"surfaceBrightness": "mag arcsec^-2", "velocity": "km s^-1"},
        "calibration": "SPARC published tables",
        "hasVariance": False,
        "hasMask": False,
        "hasWcs": False,
        "note": "A radial photometry and rotation-curve layer; it must be plotted or overlaid, never treated as a sky image.",
        "provenance": {"bibcode": bibcode, "sampleId": target["sparc_id"]},
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline" / "results" / "dp2-sparc-coverage.json")
    parser.add_argument("--mosaics", type=Path, default=root / "pipeline" / "output" / "dp2-sparc" / "mosaic-summary.json")
    parser.add_argument("--downloads", type=Path, default=root / "pipeline" / "output" / "dp2-sparc" / "download-manifest.json")
    parser.add_argument("--output", type=Path, default=root / "public" / "data" / "layers-catalog.json")
    args = parser.parse_args()

    coverage = load_json(args.coverage)
    mosaics = mosaic_state(args.mosaics)
    selected_ids: dict[str, list[str]] = {}
    if args.downloads.is_file():
        for record in load_json(args.downloads).get("records", []):
            selected_ids.setdefault(record["target_slug"], []).append(record["publisher_id"])
    targets = []
    for source in coverage["targets"]:
        mosaic = mosaics.get(source["slug"])
        layers = [sparc_layer(source, coverage["sparc_bibcode"]), rubin_layer(source, mosaic, selected_ids.get(source["slug"], []))]
        targets.append(
            {
                "id": source["slug"],
                "name": source["sparc_id"],
                "identifiers": {"SPARC": source["sparc_id"], "SIMBAD": source["main_id"]},
                "center": {"raDeg": source["ra_deg"], "decDeg": source["dec_deg"], "frame": "ICRS"},
                "region": {"shape": "square", "widthArcmin": source["field_width_arcmin"]},
                "selection": {
                    "sample": "SPARC 2016 master sample",
                    "bibcode": coverage["sparc_bibcode"],
                    "majorAxisArcmin": source["major_axis_arcmin"],
                },
                "layers": layers,
                "comparisons": [],
            }
        )

    usable = sum(
        any(layer["id"] == "rubin-dp2-deep-coadd" and layer["availability"] == "available-local" for layer in target["layers"])
        for target in targets
    )
    footprint_only = sum(
        any(layer["id"] == "rubin-dp2-deep-coadd" and layer["availability"] == "no-valid-pixels" for layer in target["layers"])
        for target in targets
    )
    catalog = {
        "schemaVersion": 1,
        "product": "Layers",
        "release": "SPARC x Rubin DP2 pilot",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "targetSelection": {
            "name": "SPARC 2016 master sample",
            "count": len(targets),
            "complete": len(targets) == coverage["targets_total"],
        },
        "summary": {
            "targets": len(targets),
            "rubinSiaMatches": coverage["targets_with_deep_coadds"],
            "rubinUsableLocal": usable,
            "rubinFootprintFalsePositives": footprint_only,
            "publishedComparisons": 0,
        },
        "targets": targets,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print(
        f"Wrote {len(targets)} targets to {args.output} "
        f"({usable} usable local Rubin, {footprint_only} footprint-only)"
    )


if __name__ == "__main__":
    main()
