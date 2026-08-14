#!/usr/bin/env python3
"""Independent validation for the radio/X-ray/H I acquisition lane."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


SCHEMA = "layers-radio-xray-hi-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk_artifacts(value: Any):
    if isinstance(value, dict):
        if {"path", "sha256", "bytes", "role"} <= value.keys():
            yield value
        for item in value.values():
            yield from walk_artifacts(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_artifacts(item)


def first_image(hdul: fits.HDUList):
    for hdu in hdul:
        if hdu.data is not None and np.asarray(hdu.data).ndim >= 2 and np.asarray(hdu.data).size:
            return hdu
    raise ValueError("no image HDU")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    local_path = root / "pipeline/results/radio-xray-hi/manifest.json"
    public_path = root / "public/data/layers/radio-xray-hi/manifest.json"
    errors: list[str] = []
    checks = Counter()
    if not local_path.is_file() or not public_path.is_file():
        print("FAIL: missing local or public manifest", file=sys.stderr)
        return 1
    local = json.loads(local_path.read_text(encoding="utf-8"))
    public = json.loads(public_path.read_text(encoding="utf-8"))
    if local.get("schemaVersion") != SCHEMA or public.get("schemaVersion") != SCHEMA:
        errors.append("schemaVersion mismatch")
    products = local.get("products", [])
    public_products = public.get("products", [])
    if len(products) != len(public_products):
        errors.append("local/public product count mismatch")
    if local.get("selection", {}).get("regionCount") != 50:
        errors.append("selection is not the expected 50 regions")
    expected_keys = {
        "regionId", "tract", "surveyId", "surveyName", "family", "release", "productType", "status",
        "scienceReady", "displayReady", "comparisonReady", "bandOrObservable", "unit", "provenanceUrls",
        "checksum", "blockers",
    }
    valid_status = {"available", "none", "not-fetched", "error"}
    for product in products:
        checks["products"] += 1
        missing = expected_keys - product.keys()
        if missing:
            errors.append(f"{product.get('regionId')}/{product.get('surveyId')}: missing keys {sorted(missing)}")
        if product.get("status") not in valid_status:
            errors.append(f"{product.get('regionId')}/{product.get('surveyId')}: invalid status")
        if product.get("comparisonReady"):
            errors.append(f"{product.get('regionId')}/{product.get('surveyId')}: cross-band comparison improperly ready")
        if product.get("displayReady"):
            for key in ("previewPath", "alignedRubinPreviewPath", "coveragePreviewPath", "overlayPreviewPath"):
                candidate = product.get(key)
                if not candidate or not (root / "public" / candidate.lstrip("/")).is_file():
                    errors.append(f"{product.get('regionId')}/{product.get('surveyId')}: missing {key}")
                else:
                    checks["previews"] += 1
        if "image" in str(product.get("productType")) and product.get("status") == "available" and not product.get("checksum"):
            errors.append(f"{product.get('regionId')}/{product.get('surveyId')}: available image lacks checksum")
        if product.get("scienceReady") and product.get("status") != "available":
            errors.append(f"{product.get('regionId')}/{product.get('surveyId')}: scienceReady without available status")
    for item in walk_artifacts(local.get("evidence")):
        checks["artifacts"] += 1
        path = root / item["path"]
        if not path.is_file():
            errors.append(f"missing artifact {item['path']}")
        elif path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            errors.append(f"changed artifact {item['path']}")
        else:
            checks["checksums"] += 1
    # Validate every retained derived/pixel FITS referenced by an available display product.
    local_artifacts_by_hash = {item["sha256"]: item for item in walk_artifacts(local.get("evidence"))}
    for product in products:
        if not product.get("displayReady"):
            continue
        item = local_artifacts_by_hash.get(product.get("checksum"))
        if not item:
            errors.append(f"{product['regionId']}/{product['surveyId']}: no local artifact for product checksum")
            continue
        path = root / item["path"]
        try:
            with fits.open(path, memmap=False, checksum=True) as hdul:
                hdu = hdul["IMAGE"] if "IMAGE" in hdul else first_image(hdul)
                data = np.asarray(hdu.data)
                while data.ndim > 2:
                    data = data[0]
                wcs = WCS(hdu.header).celestial
                if data.ndim != 2 or not wcs.has_celestial or not np.any(np.isfinite(data)):
                    raise ValueError("not a finite 2D celestial raster")
                if product["surveyId"] == "erosita-erass1" and product["productType"] == "image-exposure-background":
                    required = {"IMAGE", "EXPOSURE", "BACKGROUND", "VALID_MASK"}
                    if not required <= {h.name for h in hdul}:
                        raise ValueError("eROSITA product lacks image/exposure/background/mask planes")
                    exposure = np.asarray(hdul["EXPOSURE"].data)
                    mask = np.asarray(hdul["VALID_MASK"].data)
                    if not np.any(np.isfinite(exposure) & (exposure > 0) & mask.astype(bool)):
                        raise ValueError("eROSITA product has no positive exposed support")
                if product["surveyId"] == "vlass" and product.get("unit") != "Jy/beam":
                    raise ValueError("VLASS cutout unit is not preserved as Jy/beam")
                checks["fitsWcs"] += 1
        except Exception as error:
            errors.append(f"{product['regionId']}/{product['surveyId']}: FITS validation failed: {error}")
    public_text = public_path.read_text(encoding="utf-8")
    forbidden = [
        r"pipeline/results", r"cache/", r"X-Amz-", r"Authorization", r"Bearer\s+", r"canfar\.net/minoc/files",
        r'"datasetId"', r'"obsCore"', r'"requestUrl"', r'"sourceUrl"',
    ]
    for pattern in forbidden:
        if re.search(pattern, public_text, flags=re.IGNORECASE):
            errors.append(f"public manifest leaks forbidden pattern {pattern}")
    all_status = Counter(item["status"] for item in products)
    recomputed = {
        "productCount": len(products),
        "status": {key: int(all_status.get(key, 0)) for key in ("available", "none", "not-fetched", "error")},
        "pixelAvailable": sum("image" in item["productType"] and item["status"] == "available" for item in products),
        "catalogAvailable": sum("catalog" in item["productType"] and item["status"] == "available" for item in products),
        "scienceReady": sum(bool(item["scienceReady"]) for item in products),
        "displayReady": sum(bool(item["displayReady"]) for item in products),
        "comparisonReady": sum(bool(item["comparisonReady"]) for item in products),
    }
    for key, expected in recomputed.items():
        if local.get("counts", {}).get(key) != expected:
            errors.append(f"aggregate count mismatch for {key}: manifest={local.get('counts', {}).get(key)} actual={expected}")
    report = {
        "schemaVersion": SCHEMA,
        "validatedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "status": "pass" if not errors else "fail",
        "checks": dict(checks),
        "counts": local.get("counts"),
        "errors": errors,
    }
    report_path = root / "pipeline/results/radio-xray-hi/validation.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if errors:
        print(f"FAIL: {len(errors)} errors", file=sys.stderr)
        for error in errors[:100]:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        f"PASS: {checks['products']} products, {local['counts']['pixelAvailable']} available pixel products, "
        f"{local['counts']['catalogAvailable']} available catalogue products, {checks['fitsWcs']} FITS/WCS checks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
