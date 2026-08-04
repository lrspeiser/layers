#!/usr/bin/env python3
"""Publish one object only after external registration QA has passed."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str):
    if not condition:
        raise SystemExit(f"REFUSED: {message}")


def copy_asset(source: Path, target_dir: Path) -> tuple[str, str]:
    require(source.is_file(), f"missing asset {source}")
    target = target_dir / source.name
    shutil.copy2(source, target)
    return f"/atlas/{target_dir.name}/{target.name}", sha256(target)


def existing_rubin_hashes(public_root: Path, skip_slug: str) -> set[str]:
    hashes = set()
    for manifest_path in public_root.glob("*/manifest.json"):
        if manifest_path.parent.name == skip_slug:
            continue
        manifest = load_json(manifest_path)
        hashes.update(manifest.get("provenance", {}).get("sourceSha256", {}).values())
    return hashes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--edp2", type=Path, required=True, help="Directory created by edp2_export.py")
    parser.add_argument("--qa", type=Path, required=True, help="Registration QA JSON")
    parser.add_argument("--public-root", type=Path, default=Path(__file__).parents[1] / "public" / "atlas")
    args = parser.parse_args()

    provenance = load_json(args.edp2 / "edp2_provenance.json")
    qa = load_json(args.qa)
    target = provenance["target"]
    slug = target["slug"]
    require(qa.get("objectId") == slug, "QA objectId does not match EDP2 export")
    require(qa.get("commonWcs") is True, "common-WCS check did not pass")
    require(qa.get("psfMatched") is True, "PSF matching is not documented")
    require(qa.get("skyMatched") is True, "sky matching is not documented")
    residual = float(qa.get("maxResidualArcsec", 999))
    threshold = float(qa.get("qaThresholdArcsec", 0))
    require(threshold > 0 and residual <= threshold, "astrometric residual exceeds the declared threshold")

    output_dir = args.public_root / slug
    output_dir.mkdir(parents=True, exist_ok=True)
    checksums = {}
    rubin_rgb, checksums["rubin_rgb"] = copy_asset(args.edp2 / "rubin_rgb.png", output_dir)
    legacy_rgb, checksums["legacy_rgb"] = copy_asset(Path(qa["legacyRgb"]), output_dir)
    require(checksums["rubin_rgb"] != checksums["legacy_rgb"], "Rubin and legacy comparison images are byte-identical")
    require(checksums["rubin_rgb"] not in existing_rubin_hashes(args.public_root, slug), "Rubin preview duplicates another published object")

    rubin_bands = {}
    for band in ("u", "g", "r", "i", "z", "y"):
        source = args.edp2 / f"rubin_{band}.png"
        if source.exists():
            rubin_bands[band], checksums[f"rubin_{band}"] = copy_asset(source, output_dir)

    rubin_diffuse = None
    if qa.get("rubinDiffuse"):
        rubin_diffuse, checksums["rubin_diffuse"] = copy_asset(Path(qa["rubinDiffuse"]), output_dir)

    dataset_ids = sorted({dataset_id for band in provenance["bands"].values() for dataset_id in band.get("datasetIds", [])})
    require(dataset_ids, "no Butler dataset UUIDs were recorded")
    manifest = {
        "schemaVersion": 1,
        "objectId": slug,
        "release": "EDP2",
        "verified": True,
        "center": {"raDeg": target["ra_deg"], "decDeg": target["dec_deg"]},
        "field": {
            "widthArcmin": target["field_width_arcmin"],
            "heightArcmin": target["field_width_arcmin"],
            "pixelScaleArcsec": provenance["pixelScaleArcsec"],
        },
        "images": {
            "rubin": {"rgb": rubin_rgb, "diffuse": rubin_diffuse, "bands": rubin_bands},
            "legacy": {"rgb": legacy_rgb, "bands": qa.get("legacyBands", {})},
        },
        "registration": {
            "commonWcs": True,
            "psfMatched": True,
            "skyMatched": True,
            "maxResidualArcsec": residual,
            "qaThresholdArcsec": threshold,
        },
        "provenance": {
            "datasetType": "deep_coadd",
            "collection": "dp2",
            "butlerDatasetIds": dataset_ids,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "sourceSha256": checksums,
        },
        "metrics": qa.get("metrics", []),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Published verified manifest: {output_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
