#!/usr/bin/env python3
"""Acquire DES DR2 coadd cutouts as a second independent optical reference.

Every optical comparison in this project so far has been Rubin against Legacy
Survey. That makes it impossible to tell whether the field-dependent effects
found in it belong to Rubin, to Legacy, or to the comparison: a colour term with
a reduced chi-square of 443 against a single constant, a flux scale that varies
with field crowding, and a 5.75 sigma sign imbalance in the residual population
all have two possible owners and one measurement.

A second reference breaks that degeneracy. If Rubin-minus-DES shows the same
field dependence as Rubin-minus-Legacy, it belongs to Rubin. If it does not, it
belongs to Legacy.

DES DR2 is served publicly by NOIRLab Astro Data Lab over SIA2, with a cutout
service that returns real coadd pixels rather than display tiles. HSC PDR2, the
other survey named in this goal, publishes only HiPS tiles without credentials;
those are display products and cannot support a photometric comparison, which is
recorded here rather than papered over.

DES coadds are calibrated to a fixed 30.0 AB magnitude zeropoint, so the flux
conversion is a stated constant rather than a per-image header lookup.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests

warnings.filterwarnings("ignore")

from astropy.io import fits
from astropy.io.votable import parse as parse_votable
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGIONS = ROOT / "pipeline/results/coverage/selected-regions-200.json"
DEFAULT_OUTPUT = ROOT / "pipeline/results/des-dr2"
DEFAULT_PUBLIC = ROOT / "public/data/layers/selected-regions/des-dr2.json"

SIA = "https://datalab.noirlab.edu/sia/des_dr2"
CUTOUT_ARCMIN = 4.0
PREFERRED_BANDS = ("r", "i", "g", "z")
# DES DR2 coadds carry a fixed zeropoint of 30.0 AB.
DES_ZEROPOINT_AB = 30.0
AB_ZERO_POINT_NJY = 3.63078054770e12
REQUEST_PAUSE_SECONDS = 0.3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover(cache: Path, ra: float, dec: float, name: str) -> list[dict[str, Any]]:
    path = cache / f"{name}-sia.vot"
    size = CUTOUT_ARCMIN / 60.0
    if not path.is_file():
        response = requests.get(
            SIA, params={"POS": f"{ra},{dec}", "SIZE": f"{size:.5f}", "FORMAT": "image/fits"}, timeout=180
        )
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        time.sleep(REQUEST_PAUSE_SECONDS)
    try:
        table = parse_votable(str(path)).get_first_table().to_table()
    except Exception:
        return []
    rows = []
    for row in table:
        band = str(row["obs_bandpass"]).strip()
        if band not in PREFERRED_BANDS:
            continue
        title = str(row["obs_title"]) if "obs_title" in table.colnames else ""
        # "nobkg" coadds have the background already removed, which is what a
        # surface-brightness comparison wants; prefer them when both exist.
        rows.append({
            "band": band,
            "url": str(row["access_url"]),
            "title": title,
            "backgroundSubtracted": "nobkg" in str(row["access_url"]),
        })
    return rows


def choose(rows: list[dict[str, Any]], band: str) -> dict[str, Any] | None:
    matching = [row for row in rows if row["band"] == band]
    if not matching:
        return None
    matching.sort(key=lambda row: (not row["backgroundSubtracted"],))
    return matching[0]


def validate(path: Path) -> dict[str, Any]:
    try:
        with fits.open(path, memmap=False) as hdus:
            for hdu in hdus:
                data = getattr(hdu, "data", None)
                if data is None or np.ndim(data) != 2:
                    continue
                wcs = WCS(hdu.header).celestial
                if not wcs.has_celestial:
                    continue
                scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
                finite = float(np.isfinite(data).mean())
                return {
                    "valid": finite > 0.5 and scale > 0,
                    "shape": [int(data.shape[0]), int(data.shape[1])],
                    "pixelScaleArcsec": scale,
                    "finitePixelFraction": finite,
                    "fieldArcmin": round(data.shape[0] * scale / 60.0, 3),
                    "wcsPresent": True,
                }
    except Exception as error:
        return {"valid": False, "error": f"{type(error).__name__}: {error}"}
    return {"valid": False, "error": "no 2-D plane with celestial WCS"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", type=Path, default=DEFAULT_REGIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    regions = json.loads(args.regions.read_text(encoding="utf-8"))["regions"]
    if args.limit:
        regions = regions[: args.limit]
    cache = args.output / "cache"
    products = args.output / "products"

    records: list[dict[str, Any]] = []
    for region in regions:
        region_id = region["id"]
        ra, dec = region["center"]
        if "des-dr2" not in region.get("confirmedSurveyIds", []):
            records.append({"regionId": region_id, "tract": region["tract"], "status": "outside-des-footprint"})
            continue
        try:
            rows = discover(cache, ra, dec, region_id)
        except Exception as error:
            records.append({"regionId": region_id, "tract": region["tract"], "status": "discovery-failed",
                            "error": f"{type(error).__name__}: {error}"})
            continue
        if not rows:
            records.append({"regionId": region_id, "tract": region["tract"], "status": "no-des-image"})
            continue

        chosen = next((choose(rows, band) for band in PREFERRED_BANDS if choose(rows, band)), None)
        if chosen is None:
            records.append({"regionId": region_id, "tract": region["tract"], "status": "no-preferred-band"})
            continue

        path = products / region_id / f"des-dr2-{chosen['band']}.fits"
        if not path.is_file():
            try:
                response = requests.get(chosen["url"], timeout=300)
                response.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response.content)
            except Exception as error:
                records.append({"regionId": region_id, "tract": region["tract"], "status": "download-failed",
                                "error": f"{type(error).__name__}: {error}"})
                continue
            finally:
                time.sleep(REQUEST_PAUSE_SECONDS)

        check = validate(path)
        records.append({
            "regionId": region_id,
            "tract": region["tract"],
            "center": region["center"],
            "status": "validated-science-input" if check.get("valid") else "validation-failed",
            "band": chosen["band"],
            "backgroundSubtracted": chosen["backgroundSubtracted"],
            "unit": "DES coadd ADU",
            "zeropointAB": DES_ZEROPOINT_AB,
            "fluxConversion": "nJy = ADU * 10^(-0.4*(30.0 - 8.9)) * 1e9",
            "localFits": {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path),
                          "bytes": path.stat().st_size} if path.is_file() else None,
            "validation": check,
            "scienceReady": bool(check.get("valid")),
            "comparisonReady": False,
            "comparisonBlockers": [
                "PSF matching", "background matching", "flux-unit transfer",
                "bandpass transfer", "resampling covariance", "injection/recovery QA",
            ],
        })
        print(f"[{records[-1]['status']}] {region_id} {chosen['band']}", flush=True)

    ready = [item for item in records if item.get("scienceReady")]
    summary = {
        "schemaVersion": "layers-des-dr2-v1",
        "generatedAt": utc_now(),
        "survey": "DES DR2",
        "access": "NOIRLab Astro Data Lab SIA2 and cutout service, public, no credentials",
        "purpose": (
            "A second independent optical reference. Every optical comparison so far has been Rubin "
            "against Legacy alone, which cannot say whether the field-dependent colour term, the "
            "crowding-dependent flux scale, and the residual sign imbalance belong to Rubin or to "
            "Legacy. Rubin-minus-DES answers that."
        ),
        "hscNote": (
            "HSC PDR2, the other survey named in this goal, publishes only HiPS tiles without "
            "credentials. Those are display products with no calibrated flux or variance plane and "
            "cannot support a photometric comparison, so no HSC science pixels are claimed here."
        ),
        "counts": {
            "regions": len(records),
            "scienceReady": len(ready),
            "outsideFootprint": sum(1 for item in records if item["status"] == "outside-des-footprint"),
            "noImage": sum(1 for item in records if item["status"] == "no-des-image"),
            "failed": sum(1 for item in records if item["status"] in {"discovery-failed", "download-failed", "validation-failed"}),
        },
        "regions": records,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    # The public manifest must not carry a path into pipeline/results: that tree
    # is gitignored because it holds pixels, some access-restricted, and a path
    # names where they sit on one machine. Checksums and byte counts stay, since
    # they verify a reproduced file without describing this filesystem.
    public = json.loads(json.dumps(summary))
    for region in public.get("regions", []):
        if isinstance(region.get("localFits"), dict):
            region["localFits"].pop("path", None)
    public["localPathPolicy"] = (
        "Local paths are not published. Checksums and byte counts remain so a file reproduced "
        "from the recorded source can be verified."
    )
    args.public_manifest.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    print(f"\nDES DR2: {len(ready)} science-ready of {len(records)} regions")
    for key, value in summary["counts"].items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
