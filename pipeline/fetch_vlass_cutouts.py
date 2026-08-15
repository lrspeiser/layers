#!/usr/bin/env python3
"""Acquire VLASS radio cutouts, the missing half of the counterpart operator.

The counterpart goal named VLASS and eROSITA. Only eROSITA was delivered, so
every radio overlap in the index remained an untested claim about coverage
rather than a measurement.

Getting real VLASS pixels takes three hops, and the first two obvious routes do
not work:

* The CIRADA cutout service registered for this survey is unreachable.
* CADC's SIA rows point at datalink documents, not files, and the plane
  identifier they carry is rejected by the cutout service.

The route that works reads the SODA service descriptor inside the datalink
document, which carries the *artifact* identifier
(``nrao:VLASS/...image.pbcor.tt0.subim.fits``) rather than the plane identifier.
That is what the cutout endpoint accepts. Recorded here because the failure was
silent: the plane identifier returns a 400 that names the identifier back, which
reads like a missing file rather than a wrong key.

VLASS quick-look images carry known flux-scale caveats at the few-percent level
and are not primary-beam corrected uniformly across epochs, so these support
association and morphology, not precision radio photometry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
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
DEFAULT_OUTPUT = ROOT / "pipeline/results/vlass"
DEFAULT_PUBLIC = ROOT / "public/data/layers/selected-regions/vlass.json"

SIA = "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/sia/v2query"
CUTOUT_ARCMIN = 4.0
SEARCH_RADIUS_DEG = 0.03
REQUEST_PAUSE_SECONDS = 0.4

SERVICE_BLOCK = re.compile(r"<RESOURCE[^>]*type=\"meta\"[^>]*>(.*?)</RESOURCE>", re.S)
ACCESS_URL = re.compile(r"name=\"accessURL\"[^>]*value=\"([^\"]+)\"")
ARTIFACT_ID = re.compile(r"name=\"ID\"[^>]*value=\"([^\"]+)\"")
STANDARD_ID = re.compile(r"name=\"standardID\"[^>]*value=\"([^\"]+)\"")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def soda_target(cache: Path, datalink_url: str, key: str) -> tuple[str, str] | None:
    """Return the synchronous SODA endpoint and artifact id from a datalink doc."""
    path = cache / f"{key}-datalink.vot"
    if not path.is_file():
        response = requests.get(datalink_url, timeout=180)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        time.sleep(REQUEST_PAUSE_SECONDS)
    text = path.read_text(encoding="utf-8", errors="replace")
    for block in SERVICE_BLOCK.findall(text):
        standard = STANDARD_ID.search(block)
        if not standard or "sync" not in standard.group(1):
            continue
        access = ACCESS_URL.search(block)
        artifact = ARTIFACT_ID.search(block)
        if access and artifact and artifact.group(1):
            return access.group(1), artifact.group(1)
    return None


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
        if "vlass" not in region.get("confirmedSurveyIds", []):
            records.append({"regionId": region_id, "tract": region["tract"], "status": "outside-vlass-footprint"})
            continue

        sia_path = cache / f"{region_id}-sia.vot"
        try:
            if not sia_path.is_file():
                response = requests.get(
                    SIA, params={"POS": f"CIRCLE {ra} {dec} {SEARCH_RADIUS_DEG}", "COLLECTION": "VLASS"}, timeout=180
                )
                response.raise_for_status()
                sia_path.parent.mkdir(parents=True, exist_ok=True)
                sia_path.write_bytes(response.content)
                time.sleep(REQUEST_PAUSE_SECONDS)
            table = parse_votable(str(sia_path)).get_first_table().to_table()
        except Exception as error:
            records.append({"regionId": region_id, "tract": region["tract"], "status": "discovery-failed",
                            "error": f"{type(error).__name__}: {error}"})
            continue

        rows = [row for row in table if str(row["dataproduct_type"]) == "image"]
        if not rows:
            records.append({"regionId": region_id, "tract": region["tract"], "status": "no-vlass-image"})
            continue
        # Prefer the highest calibration level: those are the combined-epoch
        # quick-look products rather than single-epoch planes.
        rows.sort(key=lambda row: -int(row["calib_level"]))

        saved = None
        for row in rows[:3]:
            try:
                target = soda_target(cache, str(row["access_url"]), f"{region_id}-{row['obs_id']}")
            except Exception:
                continue
            if target is None:
                continue
            endpoint, artifact = target
            path = products / region_id / "vlass.fits"
            if not path.is_file():
                try:
                    response = requests.get(
                        endpoint,
                        params={"ID": artifact, "CIRCLE": f"{ra} {dec} {CUTOUT_ARCMIN / 120.0:.5f}"},
                        timeout=300,
                    )
                    if response.status_code != 200 or len(response.content) < 5000:
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(response.content)
                except Exception:
                    continue
                finally:
                    time.sleep(REQUEST_PAUSE_SECONDS)
            saved = (path, str(row["obs_id"]), artifact)
            break

        if saved is None:
            records.append({"regionId": region_id, "tract": region["tract"], "status": "cutout-failed"})
            continue

        path, obs_id, artifact = saved
        try:
            with fits.open(path, memmap=False) as hdus:
                data = np.squeeze(np.asarray(hdus[0].data, dtype=np.float64))
                wcs = WCS(hdus[0].header).celestial
            scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
            finite = float(np.isfinite(data).mean())
            valid = finite > 0.5 and data.ndim == 2 and scale > 0
        except Exception as error:
            records.append({"regionId": region_id, "tract": region["tract"], "status": "validation-failed",
                            "error": f"{type(error).__name__}: {error}"})
            continue

        records.append({
            "regionId": region_id,
            "tract": region["tract"],
            "center": region["center"],
            "status": "validated-science-input" if valid else "validation-failed",
            "observation": obs_id,
            "artifact": artifact,
            "unit": "Jy/beam",
            "band": "3 GHz",
            "localFits": {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path),
                          "bytes": path.stat().st_size},
            "validation": {
                "shape": [int(x) for x in data.shape],
                "pixelScaleArcsec": scale,
                "fieldArcmin": round(data.shape[-1] * scale / 60.0, 3),
                "finitePixelFraction": finite,
            },
            "scienceReady": bool(valid),
            "comparisonReady": False,
            "comparisonBlockers": [
                "quick-look flux scale is uncertain at the few-percent level",
                "primary-beam correction is not uniform across epochs",
                "radio and optical measure different emission; this supports association, not photometry",
            ],
        })
        print(f"[{records[-1]['status']}] {region_id}", flush=True)

    ready = [item for item in records if item.get("scienceReady")]
    summary = {
        "schemaVersion": "layers-vlass-v1",
        "generatedAt": utc_now(),
        "survey": "VLASS",
        "access": "CADC SIA v2 discovery, datalink service descriptor, SODA sync cutout",
        "accessNote": (
            "The registered CIRADA cutout host is unreachable, and CADC's SIA rows carry a plane "
            "identifier the cutout service rejects with a 400 that names the identifier back. The "
            "artifact identifier inside the datalink SODA descriptor is what works."
        ),
        "counts": {
            "regions": len(records),
            "scienceReady": len(ready),
            "outsideFootprint": sum(1 for item in records if item["status"] == "outside-vlass-footprint"),
            "noImage": sum(1 for item in records if item["status"] == "no-vlass-image"),
            "failed": sum(1 for item in records if item["status"] in
                          {"discovery-failed", "cutout-failed", "validation-failed"}),
        },
        "caveats": [
            "VLASS quick-look images carry known flux-scale uncertainty at the few-percent level.",
            "Radio and optical trace different emission, so a radio source without an optical "
            "counterpart is an association result, not a photometric difference.",
        ],
        "regions": records,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nVLASS: {len(ready)} science-ready of {len(records)} regions")
    for key, value in summary["counts"].items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
