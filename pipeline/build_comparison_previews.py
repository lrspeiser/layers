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


def shared_render(
    left: np.ndarray,
    right: np.ndarray,
    common: np.ndarray,
    left_valid: np.ndarray,
    right_valid: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    samples = np.concatenate((left[common], right[common]))
    _, _, noise = sigma_clipped_stats(samples, sigma=3.0, maxiters=6)
    scale = max(float(noise) * 1.6, 1e-6)
    positive = samples[samples > 0]
    high = max(float(np.percentile(positive, 99.85)) if positive.size else scale * 40, scale * 20)
    output = []
    # Give each survey a distinct invalid-pixel hatch. A saturated-star mask
    # at the same sky coordinate must not look like one layer showing through.
    invalid_palettes = (
        (np.array([50, 25, 31], dtype=np.uint8), np.array([91, 43, 52], dtype=np.uint8)),
        (np.array([23, 34, 45], dtype=np.uint8), np.array([39, 68, 91], dtype=np.uint8)),
    )
    for layer_index, (image, valid) in enumerate(((left, left_valid), (right, right_valid))):
        values = np.nan_to_num(
            np.arcsinh(np.clip(image, 0, None) / scale) / np.arcsinh(high / scale),
            nan=0.0,
            posinf=1.0,
            neginf=0.0,
        )
        gray = np.uint8(np.clip(values, 0, 1) ** 0.88 * 255)
        rgb = np.empty((*gray.shape, 3), dtype=np.uint8)
        yy, xx = np.indices(gray.shape)
        hatch = ((xx + yy) // 9 % 2).astype(bool)
        dark, light = invalid_palettes[layer_index]
        rgb[:] = np.where(hatch[..., None], light, dark)
        rgb[valid] = np.stack((gray[valid], gray[valid], gray[valid]), axis=-1)
        output.append(rgb)
    return output[0], output[1]


def save_rgb(path: Path, data: np.ndarray) -> None:
    Image.fromarray(data, mode="RGB").save(path, quality=92, optimize=True, progressive=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparisons", type=Path, default=root / "pipeline/output/comparisons")
    parser.add_argument("--output", type=Path, default=root / "public/rubin-data")
    parser.add_argument("--index", type=Path, default=root / "public/data/comparison-previews.json")
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    selected = {item.lower() for item in args.only}
    manifests = []
    for reconciliation_path in sorted(args.comparisons.glob("*/reconciliation.json")):
        reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
        comparison_key = reconciliation.get("comparisonKey", reconciliation_path.parent.name)
        object_id = reconciliation["objectId"]
        if selected and object_id.lower() not in selected and comparison_key.lower() not in selected:
            continue
        pair_path = Path(reconciliation.get("products", {}).get("matchedPair", ""))
        if reconciliation.get("status") == "blocked" or not pair_path.is_file():
            continue
        with fits.open(pair_path, memmap=False) as hdus:
            rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
            reference = np.asarray(hdus["COMPARISON"].data, dtype=np.float64)
            common = np.asarray(hdus["COMMON_MASK"].data, dtype=bool)
            rubin_valid = np.asarray(hdus["RUBIN_MASK"].data, dtype=bool)
            reference_valid = np.asarray(hdus["COMPARISON_MASK"].data, dtype=bool)
        rubin_rgb, reference_rgb = shared_render(rubin, reference, common, rubin_valid, reference_valid)
        output_dir = args.output / comparison_key
        output_dir.mkdir(parents=True, exist_ok=True)
        rubin_path = output_dir / "rubin-matched.jpg"
        reference_path = output_dir / "reference-matched.jpg"
        coverage_path = output_dir / "common-coverage.png"
        save_rgb(rubin_path, rubin_rgb)
        save_rgb(reference_path, reference_rgb)
        rubin_only = rubin_valid & ~reference_valid
        reference_only = reference_valid & ~rubin_valid
        neither = ~rubin_valid & ~reference_valid
        rgba = np.zeros((*common.shape, 4), dtype=np.uint8)
        rgba[rubin_only] = [239, 69, 85, 145]
        rgba[reference_only] = [57, 145, 255, 145]
        rgba[neither] = [255, 174, 79, 115]
        Image.fromarray(rgba, mode="RGBA").save(coverage_path, optimize=True)
        manifest = {
            "schemaVersion": 1,
            "createdAt": reconciliation["createdAt"],
            "objectId": object_id,
            "comparisonKey": comparison_key,
            "layerIds": reconciliation["layerIds"],
            "band": reconciliation["band"],
            "shape": list(common.shape),
            "commonValidPixelFraction": float(common.mean()),
            "coverageFractions": {
                "rubinOnly": float(rubin_only.mean()),
                "referenceOnly": float(reference_only.mean()),
                "neither": float(neither.mean()),
            },
            "analysisProductSha256": sha256(pair_path),
            "assets": {
                "rubin": {"path": f"/rubin-data/{comparison_key}/{rubin_path.name}", "sha256": sha256(rubin_path)},
                "reference": {"path": f"/rubin-data/{comparison_key}/{reference_path.name}", "sha256": sha256(reference_path)},
                "commonCoverage": {"path": f"/rubin-data/{comparison_key}/{coverage_path.name}", "sha256": sha256(coverage_path)},
            },
            "notice": "Display stretch only; calibrated matched FITS is the analysis input. Red hatching marks invalid Rubin pixels and blue hatching marks invalid comparison-survey pixels; these are masks, not sky features. Coverage colors show Rubin-only (red), reference-only (blue), and neither usable (amber); uncolored pixels form the shared analysis mask. A preview does not authorize a scientific difference claim.",
        }
        manifest_path = output_dir / "comparison-preview.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifests.append(manifest)
        print(f"[{comparison_key}] {reconciliation['band']}-band matched preview; {common.mean():.3f} common valid")
    args.index.parent.mkdir(parents=True, exist_ok=True)
    args.index.write_text(
        json.dumps({"schemaVersion": 1, "comparisons": manifests}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
