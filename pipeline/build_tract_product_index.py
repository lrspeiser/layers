#!/usr/bin/env python3
"""Normalize every cached tract product into one UI-safe, redacted index."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "data" / "layers" / "tract-product-index.json"


def load(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def optional(relative: str):
    path = ROOT / relative
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def sha_for_public(path: str | None):
    if not path or not path.startswith("/"):
        return None
    local = ROOT / "public" / path.lstrip("/")
    return hashlib.sha256(local.read_bytes()).hexdigest() if local.is_file() else None


def normalize_optional(product: dict, source: str):
    previews = product.get("previews") or {}
    reference = product.get("previewPath") or previews.get("reference") or previews.get("science")
    rubin = product.get("alignedRubinPreviewPath") or previews.get("rubin") or previews.get("rubinAligned")
    coverage = product.get("coveragePreviewPath") or previews.get("coverage") or previews.get("commonCoverage")
    overlay = product.get("overlayPreviewPath") or previews.get("overlay") or previews.get("positionOverlay")
    paths = [rubin, reference, coverage, overlay]
    viewer_ready = bool(product.get("displayReady")) and all(isinstance(path, str) and path.startswith("/") for path in paths)
    return {
        "id": product.get("id") or f"{product.get('regionId', 'unknown')}-{product.get('surveyId', 'unknown')}-{product.get('bandOrObservable', 'product')}",
        "regionId": product.get("regionId"),
        "tract": product.get("tract"),
        "surveyId": product.get("surveyId"),
        "surveyName": product.get("surveyName") or product.get("surveyId"),
        "family": product.get("family"),
        "release": product.get("release"),
        "productType": product.get("productType"),
        "status": product.get("status"),
        "scienceReady": bool(product.get("scienceReady")),
        "displayReady": bool(product.get("displayReady")),
        "viewerReady": viewer_ready,
        "comparisonReady": bool(product.get("comparisonReady")),
        "rubinBand": product.get("rubinBand") or "display",
        "referenceBand": product.get("bandOrObservable") or product.get("referenceBand") or "archive product",
        "referenceUnit": product.get("unit") or product.get("referenceUnit") or "publisher unit",
        "commonCoverageFraction": product.get("commonCoverageFraction") if product.get("commonCoverageFraction") is not None else product.get("validPixelFraction", 0),
        "rubinImage": rubin,
        "referenceImage": reference,
        "coverageImage": coverage,
        "overlayImage": overlay,
        "interpretation": product.get("interpretation") or "Archive pixels may be inspected at the Rubin sky position.",
        "blockers": product.get("blockers") or [],
        "provenanceUrls": product.get("provenanceUrls") or [],
        "checksum": product.get("checksum") or sha_for_public(reference),
        "sourceManifest": source,
    }


def main() -> None:
    optical = load("public/data/layers/selected-regions/rubin-reference-comparisons.json")
    pilots = load("public/data/layers/multisurvey-pilots/rubin-lotss-common-grid-summary.json")
    rubin_pixels = load("public/data/coverage/rubin-pixels-50.json")
    rubin_band_by_tract = {row["tract"]: row["band"] for row in rubin_pixels["regions"] if row.get("status") == "complete"}
    products = []
    for row in optical["regions"]:
        products.append({
            "id": f"{row['regionId']}-{row['referenceSurveyId']}", "regionId": row["regionId"], "tract": row["tract"],
            "surveyId": row["referenceSurveyId"], "surveyName": row["referenceSurvey"], "family": "optical",
            "release": row["referenceRelease"], "productType": "image", "status": row["status"],
            "scienceReady": row["inputs"]["reference"]["scienceReady"], "displayReady": row["displayAlignmentAllowed"],
            "viewerReady": True, "comparisonReady": row["comparisonReady"], "rubinBand": row["rubinBand"],
            "referenceBand": row["referenceBand"], "referenceUnit": row["inputs"]["reference"]["unit"],
            "commonCoverageFraction": row["commonCoverageFraction"], "rubinImage": row["previews"]["rubin"]["path"],
            "referenceImage": row["previews"]["reference"]["path"], "coverageImage": row["previews"]["coverage"]["path"],
            "overlayImage": row["previews"]["positionOverlay"]["path"],
            "interpretation": "The images share a celestial grid and common finite-pixel mask, so structures can be inspected at the same sky position.",
            "blockers": row["comparisonBlockers"], "provenanceUrls": [], "checksum": row["previews"]["reference"]["sha256"],
            "sourceManifest": "public/data/layers/selected-regions/rubin-reference-comparisons.json",
        })
    pilot_tracts = {"ugc00191": 11162, "ugc00634": 10689, "ugc00891": 11411}
    for field in pilots["fields"]:
        if field["status"] != "available":
            continue
        tract = pilot_tracts[field["fieldId"]]
        products.append({
            "id": f"dp2-tract-{tract}-lotss-dr3", "regionId": f"dp2-tract-{tract}", "tract": tract,
            "surveyId": "lotss-dr2", "surveyName": "LoTSS", "family": "radio", "release": "DR3",
            "productType": "image", "status": "display-aligned", "scienceReady": True, "displayReady": True,
            "viewerReady": True, "comparisonReady": False, "rubinBand": "i", "referenceBand": "144 MHz",
            "referenceUnit": "Jy/beam", "commonCoverageFraction": field["commonCoverageFraction"],
            "rubinImage": field["previews"]["rubinAligned"], "referenceImage": field["previews"]["lotssNativeCommonGrid"],
            "coverageImage": field["previews"]["commonCoverage"], "overlayImage": field["previews"]["positionOverlay"],
            "interpretation": "Astrometric co-display can reveal optical structures that coincide with radio emission.",
            "blockers": ["beam and PSF matching", "cross-wavelength source model", "background and noise model", "selection-function QA"],
            "provenanceUrls": [], "checksum": sha_for_public(field["previews"]["lotssNativeCommonGrid"]),
            "sourceManifest": "public/data/layers/multisurvey-pilots/rubin-lotss-common-grid-summary.json",
        })
    optional_paths = [
        "public/data/layers/uv-ir-time/manifest.json",
        "public/data/layers/radio-xray-hi/manifest.json",
        "public/data/layers/lensing-cmb/manifest.json",
    ]
    source_status = []
    for relative in optional_paths:
        manifest = optional(relative)
        source_status.append({"path": relative, "present": manifest is not None, "productCount": len(manifest.get("products", [])) if manifest else 0})
        if manifest:
            for product in manifest.get("products", []):
                normalized = normalize_optional(product, relative)
                normalized["rubinBand"] = product.get("rubinBand") or rubin_band_by_tract.get(product.get("tract"), "display")
                products.append(normalized)

    family_order = {"optical": 0, "uv-ir": 1, "radio": 2, "x-ray": 3, "high-energy": 3, "gas": 4, "neutral-gas": 4, "time-domain": 5, "lensing": 6, "cmb-large-scale-structure": 6}
    products.sort(key=lambda product: (product.get("tract") or -1, family_order.get(product.get("family") or "", 99), product.get("surveyId") or "", product["id"]))
    family_counts = Counter(product.get("family") or "unknown" for product in products if product["displayReady"])
    survey_counts = Counter(product.get("surveyId") or "unknown" for product in products if product["displayReady"])
    output = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "productCount": len(products), "scienceReadyCount": sum(product["scienceReady"] for product in products),
            "displayReadyCount": sum(product["displayReady"] for product in products),
            "viewerReadyCount": sum(product["viewerReady"] for product in products),
            "comparisonReadyCount": sum(product["comparisonReady"] for product in products),
            "tractCount": len({product["tract"] for product in products if product.get("tract") is not None}),
            "familyCounts": dict(sorted(family_counts.items())), "surveyCounts": dict(sorted(survey_counts.items())),
        },
        "sourceManifests": source_status,
        "products": products,
        "policy": {
            "viewerReadyRequiresFourPreviews": ["Rubin", "reference", "coverage", "position overlay"],
            "displayReadyIsNotComparisonReady": True,
            "scienceClaimAllowed": False,
        },
    }
    serialized = json.dumps(output, indent=2) + "\n"
    if any(secret in serialized for secret in ("Authorization", "X-Amz-Signature", "RUBIN_RSP_TOKEN")):
        raise RuntimeError("refusing to publish credential or signed URL material")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(serialized, encoding="utf-8")
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()
