#!/usr/bin/env python3
"""Publish a real strong+weak-lensing map for an appropriate cluster field."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from astropy.io import fits
from PIL import Image


KAPPA_URL = "https://archive.stsci.edu/pub/hlsp/frontier/abell2744/models/merten/v1/hlsp_frontier_model_abell2744_merten_v1_kappa.fits"
HST_URL = "https://archive.stsci.edu/pub/hla/hlsp/frontier/hst/acs-30mas/abell2744/hlsp_frontier_hst_acs-30mas_abell2744_f814w_v1.0-epoch2_f606w_v1.0-epoch2_f435w_v1.0-epoch2_512.jpg"
MODEL_INDEX = "https://archive.stsci.edu/prepds/frontier/lensmodels/"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, path: Path) -> None:
    if path.is_file() and path.stat().st_size > 0:
        return
    response = requests.get(url, timeout=120, headers={"User-Agent": "Layers/0.3"})
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)


def kappa_preview(source: Path, output: Path) -> dict:
    with fits.open(source, memmap=False) as hdul:
        data = np.asarray(hdul[0].data, dtype=np.float32)
        header = hdul[0].header
    while data.ndim > 2:
        data = data[0]
    valid = np.isfinite(data)
    low, high = np.nanpercentile(data[valid], [1, 99])
    scaled = np.clip((data - low) / max(high - low, 1e-8), 0, 1)
    # Perceptually ordered dark-blue -> cyan -> warm-yellow map.
    red = np.clip(1.8 * scaled - 0.55, 0, 1)
    green = np.clip(1.5 * scaled, 0, 1)
    blue = np.clip(0.22 + 1.35 * scaled - 1.2 * scaled**2, 0, 1)
    rgb = np.dstack((red, green, blue))
    rgb[~valid] = 0
    image = Image.fromarray(np.uint8(rgb * 255), mode="RGB")
    image = image.resize((768, 768), Image.Resampling.LANCZOS)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, quality=93, optimize=True)
    return {
        "shape": list(data.shape),
        "validPixelFraction": float(valid.mean()),
        "displayRange": [float(low), float(high)],
        "bunit": header.get("BUNIT"),
        "hasWcs": bool(header.get("CTYPE1") and header.get("CTYPE2")),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=root / "pipeline/cache/lensing/abell2744")
    parser.add_argument("--output", type=Path, default=root / "pipeline/output/lensing-fields")
    parser.add_argument("--public", type=Path, default=root / "public")
    args = parser.parse_args()

    kappa_path = args.cache / Path(KAPPA_URL).name
    hst_path = args.cache / Path(HST_URL).name
    download(KAPPA_URL, kappa_path)
    download(HST_URL, hst_path)
    preview_dir = args.public / "layer-previews/lensing"
    hst_public = preview_dir / "abell2744-hst.jpg"
    kappa_public = preview_dir / "abell2744-kappa.jpg"
    preview_dir.mkdir(parents=True, exist_ok=True)
    hst_public.write_bytes(hst_path.read_bytes())
    map_summary = kappa_preview(kappa_path, kappa_public)
    created = datetime.now(timezone.utc).isoformat()
    public_record = {
        "schemaVersion": 1,
        "product": "Layers gravitational-lensing map record",
        "createdAt": created,
        "targetId": "abell2744",
        "target": {"name": "Abell 2744", "raDeg": 3.588333, "decDeg": -30.39725, "redshift": 0.308},
        "rubinDp2Audit": {"queryRadiusDeg": 0.12, "deepCoaddRows": 0, "status": "not-covered"},
        "map": {
            "quantity": "dimensionless convergence kappa",
            "model": "Merten v1 SaWLens",
            "method": "non-parametric strong + weak lensing",
            "source": KAPPA_URL,
            "sha256": sha256(kappa_path),
            **map_summary,
        },
        "hst": {"source": HST_URL, "sha256": sha256(hst_path), "bands": ["F435W", "F606W", "F814W"]},
        "interpretation": {
            "status": "model-map",
            "statement": "Kappa is an inferred projected mass map constrained by lensing observations; it is not observed surface brightness and cannot be subtracted from an optical image.",
            "caveats": ["The map depends on the Merten v1 lens model, assumed cosmology, source redshifts, and reconstruction regularization.", "Rubin Early DP2 returned no deep-coadd records at this field, so no Rubin comparison is claimed."],
        },
    }
    record_dir = args.public / "data/layers/lensing"
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path = record_dir / "abell2744.json"
    record_path.write_text(json.dumps(public_record, indent=2), encoding="utf-8")
    layers = [
        {
            "id": "hst-frontier-fields",
            "survey": "Hubble Frontier Fields",
            "release": "Abell 2744 public mosaics",
            "instrument": "HST ACS",
            "kind": "image",
            "availability": "published",
            "renderMode": "image",
            "bands": ["F435W", "F606W", "F814W"],
            "datasetCount": 3,
            "datasetIds": [HST_URL],
            "units": {"preview": "display RGB"},
            "calibration": "MAST Hubble Frontier Fields high-level science mosaic",
            "hasVariance": False,
            "hasMask": False,
            "hasWcs": False,
            "note": "Authentic public HST color preview of the cluster field; the linked archive retains the calibrated mosaics.",
            "scienceRole": "Observed cluster light and strong-lensing arcs.",
            "provenance": {"service": "MAST", "collection": "Hubble Frontier Fields"},
            "assets": {"preview": "/layer-previews/lensing/abell2744-hst.jpg", "data": "/data/layers/lensing/abell2744.json"},
        },
        {
            "id": "hff-merten-v1-kappa",
            "survey": "Hubble Frontier Fields lens models",
            "release": "Merten v1",
            "instrument": "SaWLens reconstruction",
            "kind": "map",
            "availability": "published",
            "renderMode": "overlay",
            "bands": ["kappa"],
            "datasetCount": 1,
            "datasetIds": [KAPPA_URL],
            "units": {"convergence": "dimensionless kappa"},
            "calibration": "Merten non-parametric strong+weak-lensing model",
            "hasVariance": False,
            "hasMask": False,
            "hasWcs": map_summary["hasWcs"],
            "note": "A real public projected-mass model. Display color encodes kappa; it is not telescope color or detected optical light.",
            "scienceRole": "Compare inferred total projected mass with observed luminous structure in a scientifically appropriate cluster field.",
            "provenance": {"service": "MAST", "collection": "Frontier Fields lens models", "documentation": MODEL_INDEX},
            "assets": {"preview": "/layer-previews/lensing/abell2744-kappa.jpg", "data": "/data/layers/lensing/abell2744.json"},
            "linkedEvidence": {
                "status": "model-map",
                "headline": "Projected mass from strong + weak lensing",
                "summary": "This kappa map is model-dependent total-mass evidence. It belongs beside the HST light image, but it is not eligible for a raw pixel difference.",
                "facts": [
                    {"label": "QUANTITY", "value": "KAPPA", "unit": "dimensionless convergence"},
                    {"label": "METHOD", "value": "SaWLens", "unit": "strong + weak lensing"},
                    {"label": "CLUSTER Z", "value": "0.308", "unit": "redshift"},
                    {"label": "RUBIN DP2", "value": "0", "unit": "deep-coadd records"},
                ],
                "links": [{"label": "Open MAST lens models", "href": MODEL_INDEX}, {"label": "Download source kappa FITS", "href": KAPPA_URL}],
            },
        },
        {
            "id": "rubin-dp2-deep-coadd",
            "survey": "Vera C. Rubin Observatory",
            "release": "Early DP2",
            "instrument": "LSSTCam",
            "kind": "image",
            "availability": "not-covered",
            "renderMode": "metadata",
            "bands": [],
            "datasetCount": 0,
            "datasetIds": [],
            "units": {"image": "nJy"},
            "calibration": "Rubin Science Pipelines deep coadd",
            "hasVariance": False,
            "hasMask": False,
            "hasWcs": False,
            "note": "An authenticated DP2 SIA query returned zero deep-coadd datasets within 0.12 degrees of Abell 2744.",
            "scienceRole": "Future Rubin coverage check; no current Rubin pixel claim.",
            "provenance": {"service": "Rubin Science Platform SIA v2", "queryStatus": "OK"},
        },
    ]
    manifest = {
        "schemaVersion": 1,
        "adapterContract": "layers-external-layer-v1",
        "createdAt": created,
        "source": {"service": "MAST Frontier Fields"},
        "targets": [{
            "targetId": "abell2744",
            "target": {
                "id": "abell2744",
                "name": "Abell 2744",
                "identifiers": {"COMMON": "Pandora's Cluster", "SIMBAD": "ACO 2744"},
                "center": {"raDeg": 3.588333, "decDeg": -30.39725, "frame": "ICRS"},
                "region": {"shape": "square", "widthArcmin": 6.0},
                "selection": {"sample": "Lensing demonstration fields", "redshift": 0.308},
            },
            "layers": layers,
        }],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Published Abell 2744 HST light and Merten strong+weak-lensing kappa layers")


if __name__ == "__main__":
    main()
