#!/usr/bin/env python3
"""Build local-only display assets for the real-pixel Layers prototype.

The output is deliberately ignored by Git and deployment uploads: Rubin DP2
access is authenticated and its redistribution terms still need confirmation.
The PNG/JPEG products are display stretches, never analysis inputs.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from PIL import Image
from scipy.ndimage import binary_erosion, gaussian_filter

LEGACY_NANOMAGGY_TO_NJY = 3630.780547701


def read_rubin(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with fits.open(path, memmap=False) as hdus:
        image = np.asarray(hdus["IMAGE"].data, dtype=np.float32)
        variance = np.asarray(hdus["VARIANCE"].data, dtype=np.float32)
    valid = np.isfinite(image) & np.isfinite(variance) & (variance > 0)
    return image, valid


def read_legacy(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with fits.open(path, memmap=False) as hdus:
        image = np.asarray(hdus["IMAGE"].data, dtype=np.float32) * LEGACY_NANOMAGGY_TO_NJY
        inverse_variance = np.asarray(hdus["IVAR"].data, dtype=np.float32) / LEGACY_NANOMAGGY_TO_NJY**2
    valid = np.isfinite(image) & np.isfinite(inverse_variance) & (inverse_variance > 0)
    return image, valid


def display_plane(image: np.ndarray, valid: np.ndarray, sigma: float = 1.1) -> tuple[np.ndarray, np.ndarray]:
    """Sky-subtract and smooth with a validity-aware normalized convolution."""
    _, sky, _ = sigma_clipped_stats(image[valid], sigma=3.0, maxiters=6)
    signal = np.where(valid, image - sky, 0.0)
    weight = gaussian_filter(valid.astype(np.float32), sigma=sigma)
    smoothed = gaussian_filter(signal, sigma=sigma)
    normalized = np.divide(smoothed, weight, out=np.zeros_like(smoothed), where=weight > 0.35)
    display_valid = weight > 0.72
    return normalized, display_valid


def color_composite(planes: dict[str, tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Produce an honest, shared-shape RGB display stretch from z/r/g planes."""
    z, z_valid = planes["z"]
    r, r_valid = planes["r"]
    g, g_valid = planes["g"]
    valid = z_valid & r_valid & g_valid

    # Keep the stretch deterministic and interpretable: every channel uses the
    # same noise-derived scale and the same asinh curve.  Channel mixing only
    # maps longer wavelengths to red and shorter wavelengths to blue.
    noise_samples = np.concatenate([z[valid], r[valid], g[valid]])
    _, _, noise = sigma_clipped_stats(noise_samples, sigma=3.0, maxiters=5)
    scale = max(float(noise) * 2.0, 1e-6)
    rgb_linear = np.stack(
        [0.95 * z + 0.20 * r, 0.75 * r + 0.18 * g, 0.82 * g + 0.16 * r],
        axis=-1,
    )
    rgb = np.arcsinh(np.clip(rgb_linear, 0, None) / scale) / np.arcsinh(55.0)
    rgb = np.clip(rgb, 0, 1) ** 0.84

    # Invalid pixels remain explicit instead of being inpainted.  A faint
    # blue-black background keeps mask boundaries visible without pure-black
    # visual spikes dominating the scene.
    background = np.array([3, 7, 14], dtype=np.uint8)
    output = np.empty((*valid.shape, 3), dtype=np.uint8)
    output[:] = background
    output[valid] = np.uint8(rgb[valid] * 255)
    return output, valid


def save_rgb(path: Path, rgb: np.ndarray) -> None:
    Image.fromarray(rgb, mode="RGB").save(path, quality=92, optimize=True, progressive=True)


def coverage_overlay(path: Path, valid: np.ndarray) -> None:
    """Encode real reference-layer validity as a cyan translucent mask."""
    edge = valid & ~binary_erosion(valid, iterations=3)
    rgba = np.zeros((*valid.shape, 4), dtype=np.uint8)
    rgba[valid] = [54, 228, 210, 24]
    rgba[edge] = [108, 255, 233, 196]
    Image.fromarray(rgba, mode="RGBA").save(path, optimize=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="ugc00191")
    parser.add_argument("--rubin-root", type=Path, default=root / "pipeline" / "output" / "dp2-sparc")
    parser.add_argument("--legacy-root", type=Path, default=root / "pipeline" / "output" / "legacy-survey")
    parser.add_argument("--output", type=Path, default=root / "public" / "private-preview")
    args = parser.parse_args()

    output_dir = args.output / args.target
    output_dir.mkdir(parents=True, exist_ok=True)
    rubin_planes: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    legacy_planes: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for band in ("g", "r", "z"):
        rubin_image, rubin_valid = read_rubin(args.rubin_root / args.target / f"rubin_{band}.fits")
        legacy_image, legacy_valid = read_legacy(args.legacy_root / args.target / f"legacy_{band}.fits")
        rubin_planes[band] = display_plane(rubin_image, rubin_valid)
        legacy_planes[band] = display_plane(legacy_image, legacy_valid)

    rubin_rgb, rubin_valid = color_composite(rubin_planes)
    legacy_rgb, legacy_valid = color_composite(legacy_planes)
    save_rgb(output_dir / "rubin-dp2.jpg", rubin_rgb)
    save_rgb(output_dir / "legacy-dr10.jpg", legacy_rgb)
    coverage_overlay(output_dir / "legacy-coverage.png", legacy_valid)

    manifest = {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "target": args.target,
        "fieldArcmin": 12.0,
        "pixelScaleArcsec": 0.4,
        "shape": list(rubin_valid.shape),
        "rubinValidFraction": float(rubin_valid.mean()),
        "legacyValidFraction": float(legacy_valid.mean()),
        "commonValidFraction": float((rubin_valid & legacy_valid).mean()),
        "assets": {
            "rubin": "rubin-dp2.jpg",
            "legacy": "legacy-dr10.jpg",
            "coverage": "legacy-coverage.png",
        },
        "notice": "Display stretches only. Original calibrated FITS products remain the analysis inputs.",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
