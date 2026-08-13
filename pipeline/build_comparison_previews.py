#!/usr/bin/env python3
"""Build display-only assets for every authentic matched image comparison.

Analysis always uses the calibrated FITS products. These deterministic preview
stretches let authorized/local deployments expose stationary swipe and exact
coverage views without treating rendered pixels as measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from PIL import Image


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shared_render(left: np.ndarray, right: np.ndarray, common: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    samples = np.concatenate((left[common], right[common]))
    _, _, noise = sigma_clipped_stats(samples, sigma=3.0, maxiters=6)
    scale = max(float(noise) * 1.6, 1e-6)
    positive = samples[samples > 0]
    high = max(float(np.percentile(positive, 99.85)) if positive.size else scale * 40, scale * 20)
    output = []
    for image in (left, right):
        values = np.nan_to_num(
            np.arcsinh(np.clip(image, 0, None) / scale) / np.arcsinh(high / scale),
            nan=0.0,
            posinf=1.0,
            neginf=0.0,
        )
        gray = np.uint8(np.clip(values, 0, 1) ** 0.88 * 255)
        rgb = np.empty((*gray.shape, 3), dtype=np.uint8)
        rgb[:] = [3, 7, 14]
        rgb[common] = np.stack((gray[common], gray[common], gray[common]), axis=-1)
        output.append(rgb)
    return output[0], output[1]


def save_rgb(path: Path, data: np.ndarray) -> None:
    Image.fromarray(data, mode="RGB").save(path, quality=92, optimize=True, progressive=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparisons", type=Path, default=root / "pipeline/output/comparisons")
    parser.add_argument("--output", type=Path, default=root / "public/private-preview")
    parser.add_argument("--index", type=Path, default=root / "public/data/comparison-previews.json")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    selected = {item.lower() for item in args.only}
    manifests = []
    for reconciliation_path in sorted(args.comparisons.glob("*/reconciliation.json")):
        slug = reconciliation_path.parent.name
        if selected and slug.lower() not in selected:
            continue
        reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
        pair_path = Path(reconciliation.get("products", {}).get("matchedPair", ""))
        if reconciliation.get("status") == "blocked" or not pair_path.is_file():
            continue
        with fits.open(pair_path, memmap=False) as hdus:
            rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
            reference = np.asarray(hdus["COMPARISON"].data, dtype=np.float64)
            common = np.asarray(hdus["COMMON_MASK"].data, dtype=bool)
        rubin_rgb, reference_rgb = shared_render(rubin, reference, common)
        output_dir = args.output / slug
        output_dir.mkdir(parents=True, exist_ok=True)
        rubin_path = output_dir / "rubin-matched.jpg"
        reference_path = output_dir / "reference-matched.jpg"
        coverage_path = output_dir / "common-coverage.png"
        save_rgb(rubin_path, rubin_rgb)
        save_rgb(reference_path, reference_rgb)
        rgba = np.zeros((*common.shape, 4), dtype=np.uint8)
        rgba[~common] = [255, 174, 79, 100]
        Image.fromarray(rgba, mode="RGBA").save(coverage_path, optimize=True)
        manifest = {
            "schemaVersion": 1,
            "createdAt": reconciliation["createdAt"],
            "objectId": slug,
            "layerIds": reconciliation["layerIds"],
            "band": reconciliation["band"],
            "shape": list(common.shape),
            "commonValidPixelFraction": float(common.mean()),
            "analysisProductSha256": sha256(pair_path),
            "assets": {
                "rubin": {"path": f"/private-preview/{slug}/{rubin_path.name}", "sha256": sha256(rubin_path)},
                "reference": {"path": f"/private-preview/{slug}/{reference_path.name}", "sha256": sha256(reference_path)},
                "commonCoverage": {"path": f"/private-preview/{slug}/{coverage_path.name}", "sha256": sha256(coverage_path)},
            },
            "notice": "Display stretch only; calibrated matched FITS is the analysis input. A preview does not authorize a scientific difference claim.",
        }
        manifest_path = output_dir / "comparison-preview.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifests.append(manifest)
        print(f"[{slug}] {reconciliation['band']}-band matched preview; {common.mean():.3f} common valid")
    args.index.parent.mkdir(parents=True, exist_ok=True)
    args.index.write_text(
        json.dumps({"schemaVersion": 1, "comparisons": manifests}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
