#!/usr/bin/env python3
"""Ingest public AllWISE W1 Atlas cutouts through the Layers image contract.

The original IRSA cutouts are retained with checksums. A standardized local
FITS product converts locally background-referenced W1 DN to nJy using the
archive MAGZP and carries variance, coverage, WCS, and a validity mask. The
browser JPEG is a display stretch only and never replaces the FITS product.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import astropy.units as u
import numpy as np
import pyvo
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from PIL import Image


SIA_URL = "https://irsa.ipac.caltech.edu/SIA"
COLLECTION = "wise_allwise"
LAYER_ID = "wise-allwise-atlas"
W1_ZERO_POINT_JY = 306.682


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, path: Path) -> None:
    if path.is_file() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Layers-science/0.1"})
    with urllib.request.urlopen(request, timeout=180) as response, path.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def cutout_url(access_url: str, ra: float, dec: float, width_arcmin: float) -> str:
    query = urllib.parse.urlencode(
        {
            "center": f"{ra:.9f},{dec:.9f}deg",
            "size": f"{width_arcmin:g}arcmin",
            "gzip": "false",
        },
        safe=",",
    )
    return f"{access_url}?{query}"


def select_products(service: pyvo.dal.sia2.SIA2Service, target: dict) -> dict[str, dict]:
    table = service.search(
        pos=(target["ra_deg"], target["dec_deg"], 0.01 * u.deg),
        collection=COLLECTION,
    ).to_table()
    candidates = []
    for row in table:
        if str(row["energy_bandpassname"]) != "W1":
            continue
        subtype = str(row["dataproduct_subtype"])
        if subtype not in {"science", "noise", "weight"}:
            continue
        separation = math.hypot(
            (float(row["s_ra"]) - target["ra_deg"]) * math.cos(math.radians(target["dec_deg"])),
            float(row["s_dec"]) - target["dec_deg"],
        )
        candidates.append((separation, str(row["obs_id"]), subtype, row))
    science = min((item for item in candidates if item[2] == "science"), default=None)
    if science is None:
        raise RuntimeError(f"{target['slug']}: no AllWISE W1 science product")
    obs_id = science[1]
    selected = {}
    for subtype in ("science", "noise", "weight"):
        match = min(
            (item for item in candidates if item[1] == obs_id and item[2] == subtype),
            default=None,
        )
        if match is None:
            raise RuntimeError(f"{target['slug']}: AllWISE {obs_id} lacks W1 {subtype}")
        row = match[3]
        selected[subtype] = {
            "obsId": obs_id,
            "publisherDid": str(row["obs_publisher_did"]),
            "accessUrl": str(row["access_url"]),
            "pixelScaleArcsec": float(row["s_pixel_scale"]),
            "resolutionArcsec": float(row["s_resolution"]),
        }
    return selected


def build_standard_product(sources: dict[str, Path], output: Path, target: dict) -> dict:
    with fits.open(sources["science"], memmap=False) as science_hdus, fits.open(
        sources["noise"], memmap=False
    ) as noise_hdus, fits.open(sources["weight"], memmap=False) as weight_hdus:
        science = np.asarray(science_hdus[0].data, dtype=np.float64)
        uncertainty = np.asarray(noise_hdus[0].data, dtype=np.float64)
        coverage = np.asarray(weight_hdus[0].data, dtype=np.float64)
        header = science_hdus[0].header.copy()
        if science.shape != uncertainty.shape or science.shape != coverage.shape:
            raise RuntimeError(f"{target['slug']}: AllWISE ancillary shapes disagree")
        magzp = float(header["MAGZP"])
        magzp_unc = float(header["MAGZPUNC"])
    n_jy_per_dn = W1_ZERO_POINT_JY * 1e9 * 10 ** (-0.4 * magzp)
    image = science * n_jy_per_dn
    variance = np.square(uncertainty * n_jy_per_dn)
    valid = np.isfinite(image) & np.isfinite(variance) & (variance > 0) & np.isfinite(coverage) & (coverage > 0)
    output.parent.mkdir(parents=True, exist_ok=True)
    primary = fits.PrimaryHDU()
    primary.header["OBJECT"] = target["slug"]
    primary.header["LAYERID"] = LAYER_ID
    primary.header["SURVEY"] = "AllWISE"
    primary.header["BAND"] = "W1"
    primary.header["CALTYPE"] = ("LOCALBG", "Photometry requires local background")
    image_header = header.copy()
    image_header["BUNIT"] = "nJy"
    image_header["DN2NJY"] = (n_jy_per_dn, "Archive W1 DN to nJy factor")
    variance_header = header.copy()
    variance_header["BUNIT"] = "nJy^2"
    fits.HDUList(
        [
            primary,
            fits.ImageHDU(image.astype(np.float32), header=image_header, name="IMAGE"),
            fits.ImageHDU(variance.astype(np.float32), header=variance_header, name="VARIANCE"),
            fits.ImageHDU(coverage.astype(np.float32), header=header, name="COVERAGE"),
            fits.ImageHDU(valid.astype(np.uint8), name="VALID_MASK"),
        ]
    ).writeto(output, overwrite=True, checksum=True)
    return {
        "shape": list(image.shape),
        "validPixelFraction": float(valid.mean()),
        "magZeroPointVega": magzp,
        "magZeroPointUncertainty": magzp_unc,
        "w1ZeroPointJy": W1_ZERO_POINT_JY,
        "nJyPerDn": n_jy_per_dn,
    }


def make_preview(product: Path, destination: Path) -> None:
    with fits.open(product, memmap=False) as hdus:
        image = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
        valid = np.asarray(hdus["VALID_MASK"].data, dtype=bool)
    values = image[valid]
    _, median, noise = sigma_clipped_stats(values, sigma=3, maxiters=6)
    scale = max(float(noise) * 1.5, 1e-6)
    high = max(float(np.percentile(values, 99.8) - median), scale * 20)
    stretched = np.arcsinh(np.clip(image - median, 0, None) / scale) / np.arcsinh(high / scale)
    gray = np.uint8(np.clip(stretched, 0, 1) ** 0.9 * 255)
    rgb = np.stack((gray, gray, gray), axis=-1)
    yy, xx = np.indices(gray.shape)
    checker = ((xx // 10 + yy // 10) % 2).astype(bool)
    rgb[~valid] = np.where(checker[~valid][:, None], [55, 42, 29], [25, 28, 30])
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, mode="RGB").save(destination, quality=92, optimize=True, progressive=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline/results/dp2-sparc-coverage.json")
    parser.add_argument("--cache", type=Path, default=root / "pipeline/cache/wise-allwise")
    parser.add_argument("--output", type=Path, default=root / "pipeline/output/wise-allwise")
    parser.add_argument("--previews", type=Path, default=root / "public/layer-previews/wise-allwise")
    parser.add_argument("--public-records", type=Path, default=root / "public/data/layers/wise-allwise")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    selected = set(args.only)
    # By default the first-release fetch remains scoped to Rubin pilots.  An
    # explicit --only target may select any SPARC object, which is useful for
    # validation against the published WISE/SPARC aperture-photometry cohort.
    targets = [
        item for item in coverage["targets"]
        if (item.get("deep_coadd_rows", 0) > 0 or item["slug"] in selected)
        and (not selected or item["slug"] in selected)
    ]
    service = pyvo.dal.sia2.SIA2Service(SIA_URL)
    records = []
    for target in targets:
        slug = target["slug"]
        products = select_products(service, target)
        source_dir = args.cache / slug
        source_records = {}
        for subtype, record in products.items():
            source_path = source_dir / f"allwise_w1_{subtype}.fits"
            url = cutout_url(
                record["accessUrl"], target["ra_deg"], target["dec_deg"], target["field_width_arcmin"]
            )
            download(url, source_path)
            source_records[subtype] = {
                **record,
                "cutoutUrl": url,
                "localPath": str(source_path.resolve()),
                "sha256": sha256(source_path),
            }
        standard_path = args.output / slug / "allwise_w1.fits"
        qa = build_standard_product(
            {name: Path(record["localPath"]) for name, record in source_records.items()},
            standard_path,
            target,
        )
        preview_path = args.previews / f"{slug}-w1.jpg"
        make_preview(standard_path, preview_path)
        layer = {
            "id": LAYER_ID,
            "survey": "WISE",
            "release": "AllWISE Atlas",
            "instrument": "WISE W1",
            "kind": "image",
            "availability": "published",
            "renderMode": "image",
            "bands": ["W1"],
            "bandCoverage": {"W1": qa["validPixelFraction"]},
            "datasetCount": 3,
            "datasetIds": [source_records[name]["cutoutUrl"] for name in ("science", "noise", "weight")],
            "units": {"image": "nJy", "variance": "nJy^2", "coverage": "exposures"},
            "calibration": "AllWISE W1 Atlas MAGZP; locally background-referenced Vega photometry converted to nJy",
            "hasVariance": True,
            "hasMask": True,
            "hasWcs": True,
            "note": "Authentic public W1 science, uncertainty, and coverage cutouts. The JPEG is display-only; absolute surface brightness and stellar-mass claims require a validated local-background and extended-source model.",
            "provenance": {
                "service": "IRSA SIAv2 + IBE FITS cutout service",
                "collection": COLLECTION,
                "doi": "10.26131/IRSA1",
                "documentation": "https://irsa.ipac.caltech.edu/data/WISE/docs/release/AllWISE/expsup/sec4_1b.html",
                "standardProductSha256": sha256(standard_path),
            },
            "assets": {
                "preview": f"/layer-previews/wise-allwise/{preview_path.name}",
                "data": f"/data/layers/wise-allwise/{slug}.json",
            },
        }
        record = {
                "targetId": slug,
                "target": {
                    "center": {"raDeg": target["ra_deg"], "decDeg": target["dec_deg"], "frame": "ICRS"},
                    "region": {"shape": "square", "widthArcmin": target["field_width_arcmin"]},
                },
                "layer": layer,
                "sources": source_records,
                "standardProduct": {
                    "path": str(standard_path.resolve()),
                    "sha256": sha256(standard_path),
                    **qa,
                },
                "preview": {"path": str(preview_path.resolve()), "sha256": sha256(preview_path)},
                "scienceGate": {
                    "status": "blocked",
                    "reason": "No validated extended-source W1-to-SPARC 3.6 micron aperture/background transfer exists for this target.",
                    "unsupportedClaims": ["outer-light difference", "stellar-mass change", "baryonic-mass change", "delta g_bar"],
                },
            }
        args.public_records.mkdir(parents=True, exist_ok=True)
        public_path = args.public_records / f"{slug}.json"
        public_record = {
            "schemaVersion": 1,
            "product": "Layers external image-layer provenance",
            "targetId": slug,
            "target": record["target"],
            "layer": layer,
            "sources": {
                name: {key: value for key, value in source.items() if key != "localPath"}
                for name, source in source_records.items()
            },
            "standardProduct": {key: value for key, value in record["standardProduct"].items() if key != "path"},
            "preview": {"path": layer["assets"]["preview"], "sha256": sha256(preview_path)},
            "scienceGate": record["scienceGate"],
        }
        public_path.write_text(json.dumps(public_record, indent=2), encoding="utf-8")
        record["publicRecord"] = {"path": str(public_path.resolve()), "sha256": sha256(public_path)}
        records.append(record)
        print(f"[{slug}] AllWISE W1 ingested; {qa['validPixelFraction']:.3f} valid")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schemaVersion": 1,
        "adapterContract": "layers-image-layer-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": {"service": SIA_URL, "collection": COLLECTION},
        "targets": records,
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    expected_public = {f"{item['targetId']}.json" for item in records}
    for path in args.public_records.glob("*.json"):
        if path.name not in expected_public:
            path.unlink()
    print(f"Wrote {manifest_path}")


if __name__ == "__main__":
    main()
