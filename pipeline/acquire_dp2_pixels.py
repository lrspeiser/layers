#!/usr/bin/env python3
"""Acquire cache-first Rubin DP2 MaskedImage cutouts for selected regions.

The runner consumes the already-cached authenticated SIA discovery plan.  It
selects one preferred optical band per region and asks the Rubin SODA sync
service for every intersecting patch in that band using ``MaskedImage``
detail.  Those responses contain IMAGE, VARIANCE, and MASK planes.  Patch
cutouts are mosaicked locally onto a common 0.2 arcsec grid.

No token or signed URL is serialized.  The public manifest contains hashes,
byte counts, validation state, and derived preview paths only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests
from astropy.io import fits
from astropy.wcs import WCS

from download_dp2_matches import mosaic_band, output_wcs, sha256, write_preview
from fetch_region_layers import read_dotenv
from layer_connectors import parse_votable_rows


SODA_ENDPOINT = "https://data.lsst.cloud/api/cutout/sync"
PREFERRED_BANDS = ("r", "i", "g", "z", "y", "u")
RETRYABLE = {429, 500, 502, 503, 504}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def token_from_environment(env_path: Path) -> str:
    value = os.environ.get("RUBIN_RSP_TOKEN") or read_dotenv(env_path).get("RUBIN_RSP_TOKEN")
    if not value:
        raise SystemExit("Missing RUBIN_RSP_TOKEN")
    return value


def publisher_uuid(publisher_id: str) -> str:
    return publisher_id.rsplit("id=", 1)[-1]


def cache_path(cache_root: Path, region_id: str, band: str, publisher_id: str) -> Path:
    digest = hashlib.sha256(publisher_id.encode("utf-8")).hexdigest()[:12]
    return cache_root / region_id / band / f"{publisher_uuid(publisher_id)}-{digest}.fits"


def inspect_masked_image(path: Path) -> dict[str, Any]:
    try:
        with fits.open(path, memmap=False, checksum=True) as hdus:
            missing = [name for name in ("IMAGE", "VARIANCE", "MASK") if name not in hdus]
            if missing:
                return {"valid": False, "error": f"missing extensions: {', '.join(missing)}"}
            image = hdus["IMAGE"]
            variance = hdus["VARIANCE"]
            mask = hdus["MASK"]
            shapes = [tuple(item.data.shape) for item in (image, variance, mask)]
            wcs_present = all(WCS(item.header).has_celestial for item in (image, variance, mask))
            units = {
                "image": str(image.header.get("BUNIT", "")).strip(),
                "variance": str(variance.header.get("BUNIT", "")).strip(),
                "mask": str(mask.header.get("BUNIT", "")).strip(),
            }
            structure_valid = (
                len(set(shapes)) == 1
                and bool(shapes[0])
                and wcs_present
                and np.issubdtype(mask.data.dtype, np.integer)
            )
            has_finite_image = bool(np.isfinite(np.asarray(image.data)).any())
            has_positive_variance = bool((np.isfinite(np.asarray(variance.data)) & (np.asarray(variance.data) > 0)).any())
            return {
                # An all-NO_DATA patch is a valid archive response and useful
                # coverage evidence. It contributes no pixels to the mosaic,
                # but must be cached so retries do not repeatedly fetch it.
                "valid": bool(structure_valid),
                "hasUsablePixels": has_finite_image and has_positive_variance,
                "hduCount": len(hdus),
                "shape": list(shapes[0]),
                "planeShapesAgree": len(set(shapes)) == 1,
                "wcsPresent": wcs_present,
                "units": units,
                "imagePresent": True,
                "variancePresent": True,
                "maskPresent": True,
                "maskInteger": bool(np.issubdtype(mask.data.dtype, np.integer)),
            }
    except Exception as error:
        return {"valid": False, "error": f"{type(error).__name__}: {error}"}


class SodaClient:
    def __init__(self, token: str, requests_per_minute: int) -> None:
        self.token = token
        self.interval = 60.0 / requests_per_minute
        self.last_request = -1e9

    def _pace(self) -> None:
        elapsed = time.monotonic() - self.last_request
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self.last_request = time.monotonic()

    def acquire(
        self,
        *,
        publisher_id: str,
        ra_deg: float,
        dec_deg: float,
        radius_deg: float,
        destination: Path,
    ) -> tuple[str, str | None]:
        cached = inspect_masked_image(destination) if destination.is_file() else {"valid": False}
        if cached.get("valid"):
            return "cached", None
        destination.parent.mkdir(parents=True, exist_ok=True)
        last_error: str | None = None
        for attempt in range(1, 5):
            self._pace()
            try:
                with requests.post(
                    SODA_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Accept": "application/fits",
                        "User-Agent": "Layers-rubin-pixel-cache/0.1",
                    },
                    data={
                        "ID": publisher_id,
                        "CIRCLE": f"{ra_deg:.10f} {dec_deg:.10f} {radius_deg:.10f}",
                        "CUTOUTDETAIL": "MaskedImage",
                    },
                    stream=True,
                    timeout=(30, 600),
                ) as response:
                    if response.status_code in RETRYABLE:
                        last_error = f"HTTP {response.status_code}"
                        if attempt < 4:
                            delay = float(response.headers.get("Retry-After", 0) or 0)
                            time.sleep(max(delay, min(2**attempt, 30)))
                            continue
                    response.raise_for_status()
                    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as handle:
                        temporary = Path(handle.name)
                        for chunk in response.iter_content(1024 * 1024):
                            if chunk:
                                handle.write(chunk)
                validation = inspect_masked_image(temporary)
                if not validation.get("valid"):
                    temporary.unlink(missing_ok=True)
                    return "error", f"SODA response failed MaskedImage validation: {validation.get('error', validation)}"
                temporary.replace(destination)
                return "fetched", None
            except requests.RequestException as error:
                last_error = f"{type(error).__name__}: {error}"
                if attempt == 4:
                    break
                time.sleep(min(2**attempt, 30))
        return "error", last_error


def selected_regions(plan_path: Path, band_overrides: dict[str, str] | None = None) -> list[dict[str, Any]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    band_overrides = band_overrides or {}
    result = []
    for job in plan.get("jobs", []):
        if job.get("surveyId") != "rubin-dp2" or job.get("phase") != "discover":
            continue
        cache = job.get("cache", {})
        if job.get("status") not in {"cached", "fetched"} or not cache.get("path"):
            continue
        rows = parse_votable_rows(Path(cache["path"]).read_bytes())
        available = {row.get("lsst_band") for row in rows}
        region_id = job["region"]["id"]
        override = band_overrides.get(region_id)
        if override and override not in available:
            result.append({
                "region": job["region"],
                "band": None,
                "rows": [],
                "error": f"requested override band {override!r} is unavailable",
            })
            continue
        band = override or next((value for value in PREFERRED_BANDS if value in available), None)
        if not band:
            result.append({"region": job["region"], "band": None, "rows": [], "error": "no supported band"})
            continue
        chosen = []
        seen = set()
        for row in rows:
            publisher_id = row.get("obs_publisher_did", "")
            if row.get("lsst_band") != band or not publisher_id or publisher_id in seen:
                continue
            seen.add(publisher_id)
            chosen.append({
                "publisherId": publisher_id,
                "obsId": row.get("obs_id"),
                "tract": row.get("lsst_tract"),
                "patch": row.get("lsst_patch"),
                "band": band,
            })
        result.append({"region": job["region"], "band": band, "rows": chosen})
    return sorted(result, key=lambda item: item["region"]["id"])


def write_mosaic(path: Path, science: np.ndarray, variance: np.ndarray, mask: np.ndarray, wcs: WCS, region: dict[str, Any], band: str) -> None:
    common = wcs.to_header()
    common["OBJECT"] = region["id"]
    common["TRACT"] = int(region["tract"])
    common["BAND"] = band
    common["RELEASE"] = "DP2"
    common["PIXSCALE"] = abs(wcs.wcs.cdelt[0]) * 3600.0
    common["FLUXCONS"] = (True, "Pixel-area flux conservation applied")
    image_header = common.copy()
    image_header["BUNIT"] = "nJy"
    variance_header = common.copy()
    variance_header["BUNIT"] = "nJy2"
    mask_header = common.copy()
    mask_header["BUNIT"] = "bitmask"
    mask_header["REJECT"] = "NO_DATA,SATURATED"
    fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(science.astype(np.float32), header=image_header, name="IMAGE"),
        fits.ImageHDU(variance.astype(np.float32), header=variance_header, name="VARIANCE"),
        # Signed int32 avoids FITS BZERO scaling and matches Rubin's native
        # bitmask representation while retaining every mask bit.
        fits.ImageHDU(mask.astype(np.int32), header=mask_header, name="MASK"),
    ]).writeto(path, overwrite=True, checksum=True)


def inspect_mosaic(path: Path) -> dict[str, Any]:
    base = inspect_masked_image(path)
    if not base.get("valid"):
        return {**base, "scienceReady": False, "comparisonReady": False}
    with fits.open(path, memmap=False, checksum=True) as hdus:
        image = np.asarray(hdus["IMAGE"].data)
        variance = np.asarray(hdus["VARIANCE"].data)
        mask = np.asarray(hdus["MASK"].data)
        usable = np.isfinite(image) & np.isfinite(variance) & (variance > 0) & ((mask & ((1 << 0) | (1 << 3))) == 0)
        units = {
            "image": str(hdus["IMAGE"].header.get("BUNIT", "")),
            "variance": str(hdus["VARIANCE"].header.get("BUNIT", "")),
            "mask": str(hdus["MASK"].header.get("BUNIT", "")),
        }
        strict = (
            base["wcsPresent"]
            and base["imagePresent"]
            and base["variancePresent"]
            and base["maskPresent"]
            and base["maskInteger"]
            and units == {"image": "nJy", "variance": "nJy2", "mask": "bitmask"}
            and bool(usable.any())
        )
        return {
            **base,
            "units": units,
            "checksumVerified": True,
            "validPixelFraction": float(usable.mean()),
            "scienceReady": bool(strict),
            "comparisonReady": False,
            "comparisonBlockers": ["PSF matching", "bandpass transfer", "background matching", "common-mask and covariance QA"],
        }


def manifests(records: list[dict[str, Any]], plan_path: Path, cutout_size: float, requests_per_minute: int) -> tuple[dict[str, Any], dict[str, Any]]:
    complete = [record for record in records if record.get("status") == "complete"]
    science_ready = [record for record in complete if record["validation"].get("scienceReady")]
    detailed = {
        "schemaVersion": "layers-rubin-pixels-local-v1",
        "generatedAt": utc_now(),
        "sourcePlan": plan_path.as_posix(),
        "policy": {
            "sodaRequestsPerMinute": requests_per_minute,
            "accountVoCutoutsPerMinute": 35,
            "cacheBeforeRequest": True,
            "cutoutDetail": "MaskedImage",
            "cutoutSizeArcmin": cutout_size,
            "tokensSerialized": False,
            "signedUrlsSerialized": False,
            "wholePatchDownloads": False,
        },
        "summary": {
            "regionCount": len(records),
            "completeRegionCount": len(complete),
            "scienceReadyRegionCount": len(science_ready),
            "comparisonReadyRegionCount": 0,
            "sourceCutoutCount": sum(len(record.get("sources", [])) for record in records),
            "sourceBytes": sum(source.get("bytes", 0) for record in records for source in record.get("sources", [])),
            # Validation-failed mosaics are retained as honest evidence and
            # independently checksum-audited even though they are not science
            # ready.  Count every written mosaic, not only complete records.
            "mosaicBytes": sum(record.get("mosaic", {}).get("bytes", 0) for record in records),
        },
        "regions": records,
    }
    public_regions = []
    for record in records:
        preview = record.get("preview") or {}
        public_regions.append({
            "regionId": record["regionId"],
            "tract": record["tract"],
            "center": record["center"],
            "band": record.get("band"),
            "status": record.get("status"),
            "sourceCutoutCount": len(record.get("sources", [])),
            "sourceBytes": sum(source.get("bytes", 0) for source in record.get("sources", [])),
            "sourceSha256": [source["sha256"] for source in record.get("sources", []) if source.get("sha256")],
            "mosaicBytes": record.get("mosaic", {}).get("bytes"),
            "mosaicSha256": record.get("mosaic", {}).get("sha256"),
            "previewPath": preview.get("publicPath"),
            "previewSha256": preview.get("sha256"),
            "validation": record.get("validation"),
            "error": record.get("error"),
        })
    public = {
        "schemaVersion": "layers-rubin-pixels-public-v1",
        "generatedAt": detailed["generatedAt"],
        "summary": detailed["summary"],
        "honestStatus": (
            f"{len(complete)} of {len(records)} selected regions have cached Rubin DP2 MaskedImage mosaics; "
            f"{len(science_ready)} pass local IMAGE/VARIANCE/MASK/WCS/unit gates and none are comparison-ready before cross-survey QA."
        ),
        "policy": detailed["policy"],
        "regions": public_regions,
    }
    return detailed, public


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=root / "pipeline/results/acquisition-50-bounded/discovery/acquisition-plan.json")
    parser.add_argument("--env", type=Path, default=root / ".env")
    parser.add_argument("--output", type=Path, default=root / "pipeline/results/rubin-pixels-50")
    parser.add_argument("--public-manifest", type=Path, default=root / "public/data/coverage/rubin-pixels-50.json")
    parser.add_argument("--public-preview-root", type=Path, default=root / "public/data/coverage/rubin-pixels-50")
    parser.add_argument("--only-region", action="append", default=[])
    parser.add_argument(
        "--band-override",
        action="append",
        default=[],
        metavar="REGION=BAND",
        help="Select a specific available band for a region; repeat for multiple regions.",
    )
    parser.add_argument("--max-regions", type=int)
    parser.add_argument("--cutout-size-arcmin", type=float, default=4.0)
    parser.add_argument("--pixel-scale-arcsec", type=float, default=0.2)
    parser.add_argument("--requests-per-minute", type=int, default=30)
    args = parser.parse_args()
    args.plan = args.plan.resolve()
    args.env = args.env.resolve()
    args.output = args.output.resolve()
    args.public_manifest = args.public_manifest.resolve()
    args.public_preview_root = args.public_preview_root.resolve()
    if not 1 <= args.requests_per_minute <= 35:
        raise SystemExit("--requests-per-minute must be between 1 and the account limit of 35")
    if not 0 < args.cutout_size_arcmin <= 10:
        raise SystemExit("--cutout-size-arcmin must be in (0, 10]")
    if args.pixel_scale_arcsec < 0.2:
        raise SystemExit("--pixel-scale-arcsec cannot oversample Rubin's native pixels")

    band_overrides: dict[str, str] = {}
    for value in args.band_override:
        if "=" not in value:
            raise SystemExit(f"invalid --band-override {value!r}; expected REGION=BAND")
        region_id, band = (part.strip() for part in value.split("=", 1))
        if not region_id or band not in PREFERRED_BANDS:
            raise SystemExit(f"invalid --band-override {value!r}; band must be one of {','.join(PREFERRED_BANDS)}")
        band_overrides[region_id] = band

    regions = selected_regions(args.plan, band_overrides)
    only = set(args.only_region)
    if only:
        regions = [item for item in regions if item["region"]["id"] in only]
    if args.max_regions is not None:
        regions = regions[: args.max_regions]
    token = token_from_environment(args.env)
    client = SodaClient(token, args.requests_per_minute)
    cache_root = args.output / "cache"
    product_root = args.output / "products"
    local_manifest = args.output / "manifest.json"
    args.public_preview_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    radius_deg = args.cutout_size_arcmin / 120.0 * 2**0.5

    for item in regions:
        region = item["region"]
        region_id = region["id"]
        band = item.get("band")
        record: dict[str, Any] = {
            "regionId": region_id,
            "tract": region.get("tract"),
            "center": [region["ra_deg"], region["dec_deg"]],
            "band": band,
            "status": "running",
            "sources": [],
            "validation": {"scienceReady": False, "comparisonReady": False},
        }
        if not band or not item["rows"]:
            record.update({"status": "error", "error": item.get("error", "no SIA datasets")})
            records.append(record)
            continue
        failed = False
        for source in item["rows"]:
            path = cache_path(cache_root, region_id, band, source["publisherId"])
            status, error = client.acquire(
                publisher_id=source["publisherId"],
                ra_deg=region["ra_deg"],
                dec_deg=region["dec_deg"],
                radius_deg=radius_deg,
                destination=path,
            )
            source_record = {
                "publisherDatasetId": source["publisherId"],
                "obsId": source["obsId"],
                "patch": source["patch"],
                "band": band,
                "status": status,
                "path": path.relative_to(root).as_posix() if path.is_file() else None,
                "bytes": path.stat().st_size if path.is_file() else 0,
                "sha256": sha256(path) if path.is_file() else None,
                "validation": inspect_masked_image(path) if path.is_file() else {"valid": False},
                "error": error,
            }
            record["sources"].append(source_record)
            print(f"[{status}] {region_id} {band} patch={source['patch']}", flush=True)
            if status == "error":
                failed = True
                break
        if not failed:
            target = {
                "slug": region_id,
                "sparc_id": region_id,
                "ra_deg": region["ra_deg"],
                "dec_deg": region["dec_deg"],
                "field_width_arcmin": args.cutout_size_arcmin,
            }
            wcs, shape = output_wcs(target, args.pixel_scale_arcsec)
            paths = [root / source["path"] for source in record["sources"]]
            product_dir = product_root / region_id
            product_dir.mkdir(parents=True, exist_ok=True)
            mosaic_path = product_dir / f"rubin_dp2_{band}_{args.cutout_size_arcmin:g}arcmin.fits"
            existing = inspect_mosaic(mosaic_path) if mosaic_path.is_file() else {"valid": False}
            if existing.get("valid"):
                validation = existing
                with fits.open(mosaic_path, memmap=False) as hdus:
                    science = np.asarray(hdus["IMAGE"].data, dtype=np.float32)
                    variance = np.asarray(hdus["VARIANCE"].data, dtype=np.float32)
                    mask = np.asarray(hdus["MASK"].data)
                    good = np.isfinite(science) & np.isfinite(variance) & (variance > 0) & ((mask & ((1 << 0) | (1 << 3))) == 0)
            else:
                science, variance, mask, good = mosaic_band(paths, wcs, shape)
                write_mosaic(mosaic_path, science, variance, mask, wcs, region, band)
                validation = inspect_mosaic(mosaic_path)
            preview_path = args.public_preview_root / f"{region_id}-{band}.png"
            if good.any() and not preview_path.is_file():
                write_preview(preview_path, science)
            record.update({
                "status": "complete" if validation["scienceReady"] else "validation-failed",
                "mosaic": {
                    "path": mosaic_path.relative_to(root).as_posix(),
                    "bytes": mosaic_path.stat().st_size,
                    "sha256": sha256(mosaic_path),
                    "shape": list(shape),
                    "pixelScaleArcsec": args.pixel_scale_arcsec,
                },
                "preview": {
                    "path": preview_path.relative_to(root).as_posix(),
                    "publicPath": "/" + preview_path.relative_to(root / "public").as_posix(),
                    "bytes": preview_path.stat().st_size,
                    "sha256": sha256(preview_path),
                } if preview_path.is_file() else None,
                "validation": validation,
            })
        else:
            record.update({"status": "error", "error": "one or more SODA patch cutouts failed"})
        records.append(record)
        detailed, public = manifests(records, args.plan.relative_to(root), args.cutout_size_arcmin, args.requests_per_minute)
        atomic_json(local_manifest, detailed)
        atomic_json(args.public_manifest, public)

    detailed, public = manifests(records, args.plan.relative_to(root), args.cutout_size_arcmin, args.requests_per_minute)
    atomic_json(local_manifest, detailed)
    serialized_public = json.dumps(public)
    for forbidden in ("Authorization", "RUBIN_RSP_TOKEN", "X-Amz-", "Signature=", "Expires=", "http://", "https://", "C:\\\\"):
        if forbidden in serialized_public:
            raise SystemExit(f"Refusing to publish manifest containing {forbidden}")
    atomic_json(args.public_manifest, public)
    print(f"Wrote {len(records)} regions to {local_manifest}")


if __name__ == "__main__":
    main()
