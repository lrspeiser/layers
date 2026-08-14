#!/usr/bin/env python3
"""Independent structural/provenance validator for UV/IR/time products."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from PIL import Image


REQUIRED_PRODUCT_KEYS = {
    "regionId", "tract", "surveyId", "surveyName", "family", "release", "productType", "status",
    "scienceReady", "displayReady", "comparisonReady", "bandOrObservable", "unit", "provenanceUrls", "checksum", "blockers",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_fits(path: Path) -> dict[str, Any]:
    with fits.open(path, checksum=True, memmap=False) as hdus:
        image_hdus = [hdu for hdu in hdus if getattr(hdu, "data", None) is not None and np.asarray(hdu.data).ndim == 2]
        if not image_hdus:
            raise ValueError("no 2-D image HDU")
        primary_image = next((hdu for hdu in image_hdus if WCS(hdu.header).has_celestial), image_hdus[0])
        data = np.asarray(primary_image.data)
        return {"hduCount": len(hdus), "shape": list(data.shape), "finitePixelCount": int(np.isfinite(data).sum()), "wcsPresent": bool(WCS(primary_image.header).has_celestial)}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--internal", type=Path, default=root / "pipeline/results/uv-ir-time-pixels/manifest.json")
    parser.add_argument("--public", type=Path, default=root / "public/data/layers/uv-ir-time/manifest.json")
    parser.add_argument("--report", type=Path, default=root / "pipeline/results/uv-ir-time-pixels/validation.json")
    args = parser.parse_args()
    internal = json.loads(args.internal.read_text(encoding="utf-8"))
    public = json.loads(args.public.read_text(encoding="utf-8"))
    failures: list[str] = []
    checks: list[dict[str, Any]] = []
    if len(public.get("regions", [])) != public.get("summary", {}).get("selectedRegionCount"):
        failures.append("public region count disagrees with summary")
    if public.get("summary", {}).get("comparisonReadyProductCount") != 0:
        failures.append("comparison-ready count must remain zero until cross-survey QA")
    products = public.get("products", [])
    for index, product in enumerate(products):
        missing = REQUIRED_PRODUCT_KEYS - set(product)
        if missing:
            failures.append(f"product {index} missing keys {sorted(missing)}")
        if product.get("comparisonReady"):
            failures.append(f"product {index} incorrectly claims comparisonReady")
        if not product.get("blockers"):
            failures.append(f"product {index} has no blockers")
        for preview_key in ("previewPath", "alignedRubinPreviewPath", "coveragePreviewPath", "overlayPreviewPath"):
            preview = product.get(preview_key)
            if preview:
                path = root / "public" / preview.lstrip("/")
                try:
                    with Image.open(path) as image:
                        image.verify()
                    checks.append({"kind": "preview", "role": preview_key, "path": preview, "sha256": sha256(path)})
                except Exception as error:
                    failures.append(f"preview {preview}: {error}")
    # Validate every local FITS referenced by filename in a rich survey record.
    product_root = root / "pipeline/results/uv-ir-time-pixels/products"
    for path in sorted(product_root.rglob("*.fits")) + sorted(product_root.rglob("*.fits.gz")):
        try:
            result = check_fits(path)
            if not result["finitePixelCount"] or not result["wcsPresent"]:
                raise ValueError(f"unusable image: {result}")
            checks.append({"kind": "fits", "path": path.relative_to(root).as_posix(), "sha256": sha256(path), **result})
        except Exception as error:
            failures.append(f"FITS {path.relative_to(root)}: {error}")
    public_text = args.public.read_text(encoding="utf-8")
    secret_patterns = [r"Authorization", r"Bearer\s+", r"RUBIN_RSP_TOKEN", r"[A-Za-z]:\\", r"pipeline/results/", r"pipeline/cache/"]
    for pattern in secret_patterns:
        if re.search(pattern, public_text, flags=re.IGNORECASE):
            failures.append(f"public manifest leaks forbidden pattern: {pattern}")
    survey_counts = Counter(product.get("surveyId") for product in products)
    for required in ("galex-gr6-7", "unwise", "2mass", "ztf-dr"):
        if survey_counts[required] == 0:
            failures.append(f"no validated product for required survey {required}")
    aligned_w1 = [
        product for product in products if product.get("surveyId") == "unwise" and product.get("bandOrObservable") == "W1"
        and all(product.get(key) for key in ("alignedRubinPreviewPath", "coveragePreviewPath", "overlayPreviewPath"))
    ]
    if len(aligned_w1) != 50:
        failures.append(f"expected 50 aligned unWISE W1 display products, found {len(aligned_w1)}")
    if len(internal.get("regions", [])) != len(public.get("regions", [])):
        failures.append("internal/public region counts differ")
    report = {
        "schemaVersion": "layers-uv-ir-time-validation-v1", "generatedAt": datetime.now(timezone.utc).isoformat(),
        "valid": not failures, "summary": {
            "regionCount": len(public.get("regions", [])), "productCount": len(products), "surveyProductCounts": dict(sorted(survey_counts.items())),
            "fitsChecked": sum(check["kind"] == "fits" for check in checks), "previewsChecked": sum(check["kind"] == "preview" for check in checks),
            "failureCount": len(failures),
        }, "failures": failures, "checks": checks,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
