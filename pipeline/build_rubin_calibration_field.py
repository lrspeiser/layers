#!/usr/bin/env python3
"""Build a wider Rubin calibration-only mosaic from retained DP2 patches.

The target-centered science comparison stays at its declared field size.  This
product uses the already downloaded, checksum-addressed full coadd patches to
provide more off-galaxy stars for a field-specific color calibration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from download_dp2_matches import mosaic_band, output_wcs, write_mosaic


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline/results/dp2-sparc-coverage.json")
    parser.add_argument("--downloads", type=Path, default=root / "pipeline/output/dp2-sparc/download-manifest.json")
    parser.add_argument("--output-root", type=Path, default=root / "pipeline/output/dp2-sparc")
    parser.add_argument("--only", action="append", required=True)
    parser.add_argument("--band", default="i")
    parser.add_argument("--field-width-arcmin", type=float, default=18.0)
    parser.add_argument("--pixel-scale", type=float, default=0.4)
    args = parser.parse_args()

    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))["targets"]
    downloads = json.loads(args.downloads.read_text(encoding="utf-8"))["records"]
    selected = {value.lower() for value in args.only}
    targets = [target for target in coverage if target["slug"].lower() in selected or target["sparc_id"].lower() in selected]
    if not targets:
        raise SystemExit("No matching covered target")

    for original_target in targets:
        target = {**original_target, "field_width_arcmin": args.field_width_arcmin}
        records = [
            record for record in downloads
            if record["target_slug"] == target["slug"] and record["band"] == args.band
        ]
        paths = [Path(record["path"]) for record in records]
        if not paths or any(not path.is_file() for path in paths):
            raise RuntimeError(f"{target['sparc_id']}: retained {args.band}-band patches are incomplete")
        wcs, shape = output_wcs(target, args.pixel_scale)
        science, variance, mask, valid = mosaic_band(paths, wcs, shape)
        target_dir = args.output_root / target["slug"]
        target_dir.mkdir(parents=True, exist_ok=True)
        product = target_dir / f"rubin_{args.band}_calibration_{args.field_width_arcmin:g}arcmin.fits"
        write_mosaic(product, science, variance, mask, wcs, target, args.band)
        manifest = {
            "schemaVersion": 1,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "purpose": "off-galaxy point-source color calibration only; not the target science comparison field",
            "objectId": target["slug"],
            "band": args.band,
            "fieldWidthArcmin": args.field_width_arcmin,
            "pixelScaleArcsec": args.pixel_scale,
            "shape": list(shape),
            "validPixelFraction": float(valid.mean()),
            "sourcePatches": [
                {"datasetId": record["publisher_id"], "sha256": record["sha256"], "path": record["path"]}
                for record in records
            ],
            "product": str(product.resolve()),
            "productSha256": sha256(product),
        }
        manifest_path = target_dir / f"rubin_{args.band}_calibration_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(
            f"[{target['slug']}] {args.field_width_arcmin:g} arcmin {args.band}-band calibration field; "
            f"{len(records)} patches, {valid.mean():.3f} valid"
        )


if __name__ == "__main__":
    main()
