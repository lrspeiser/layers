#!/usr/bin/env python3
"""Publish bounded family evidence assets and a tract-to-route manifest."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from astropy.table import Table


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pipeline" / "results" / "family-examples"
PUBLIC = ROOT / "public" / "data" / "layers" / "family-examples"
RAW = PUBLIC / "raw"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def describe(path: Path, href: str, product_type: str) -> dict[str, object]:
    return {
        "href": href,
        "productType": product_type,
        "bytes": path.stat().st_size,
        "sha256": digest(path),
    }


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    xray_source = SOURCE / "xray" / "erass1-tract-9813.csv"
    hi_source = SOURCE / "neutral-gas" / "hipass-tract-5061.csv"
    spec_source = SOURCE / "spectroscopy" / "desi-edr-tract-9813-spectrum.fits"
    xray_public = RAW / xray_source.name
    hi_public = RAW / hi_source.name
    spec_public = RAW / "desi-edr-tract-9813-spectrum.csv"
    shutil.copy2(xray_source, xray_public)
    shutil.copy2(hi_source, hi_public)
    spectrum = Table.read(spec_source)
    spectrum.write(spec_public, format="ascii.csv", overwrite=True)

    manifest = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "product": "Layers tract family evidence routes",
        "routes": [
            {
                "tract": 9813,
                "href": "/tract/9813/evidence",
                "centerDeg": [150.247906, 2.231355],
                "families": ["spectroscopy", "high-energy"],
                "evidence": [
                    {
                        "id": "desi-edr-spectrum",
                        "family": "spectroscopy",
                        "status": "real-product",
                        "productType": "spectrum",
                        "jsonHref": "/data/layers/family-examples/spectroscopy.json",
                        "previewHref": "/layer-previews/family-examples/desi-edr-tract-9813-spectrum.png",
                        "downloads": [describe(spec_public, "/data/layers/family-examples/raw/desi-edr-tract-9813-spectrum.csv", "CSV spectrum")],
                    },
                    {
                        "id": "erosita-erass1-catalog",
                        "family": "high-energy",
                        "status": "real-product",
                        "productType": "catalog",
                        "jsonHref": "/data/layers/family-examples/xray.json",
                        "downloads": [describe(xray_public, "/data/layers/family-examples/raw/erass1-tract-9813.csv", "CSV catalog")],
                    },
                ],
                "unresolved": [
                    {
                        "family": "lensing",
                        "status": "unresolved",
                        "jsonHref": "/data/layers/family-examples/lensing.json",
                    }
                ],
            },
            {
                "tract": 5061,
                "href": "/tract/5061/evidence",
                "centerDeg": [49.769554, -27.519591],
                "families": ["neutral-gas"],
                "evidence": [
                    {
                        "id": "hipass-hicat-detection",
                        "family": "neutral-gas",
                        "status": "real-product",
                        "productType": "catalog",
                        "jsonHref": "/data/layers/family-examples/neutralGas.json",
                        "downloads": [describe(hi_public, "/data/layers/family-examples/raw/hipass-tract-5061.csv", "CSV catalog")],
                    }
                ],
                "unresolved": [],
            },
        ],
        "guardrail": "These routes demonstrate real overlapping products. They do not claim a calibrated Rubin-versus-reference difference.",
    }
    output = PUBLIC / "tract-manifest.json"
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "routes": len(manifest["routes"])}, indent=2))


if __name__ == "__main__":
    main()
