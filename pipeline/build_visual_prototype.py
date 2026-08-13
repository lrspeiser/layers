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
from scipy.ndimage import binary_erosion, gaussian_filter, label

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


def shared_grayscale_pair(
    left: tuple[np.ndarray, np.ndarray],
    right: tuple[np.ndarray, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """Render two calibrated planes with one shared physical-flux stretch."""
    left_image, left_valid = left
    right_image, right_valid = right
    common = left_valid & right_valid
    samples = np.concatenate([left_image[common], right_image[common]])
    _, _, noise = sigma_clipped_stats(samples, sigma=3.0, maxiters=6)
    scale = max(float(noise) * 1.6, 1e-6)
    positive = samples[samples > 0]
    high = float(np.percentile(positive, 99.85)) if positive.size else scale * 40
    high = max(high, scale * 20)

    rendered = []
    for image, valid in (left, right):
        normalized = np.arcsinh(np.clip(image, 0, None) / scale) / np.arcsinh(high / scale)
        gray = np.uint8(np.clip(normalized, 0, 1) ** 0.88 * 255)
        rgb = np.empty((*gray.shape, 3), dtype=np.uint8)
        rgb[:] = [3, 7, 14]
        rgb[valid] = np.stack([gray[valid], gray[valid], gray[valid]], axis=-1)
        rendered.append(rgb)
    return rendered[0], rendered[1]


def coverage_overlay(path: Path, valid: np.ndarray) -> None:
    """Encode real reference-layer validity as a cyan translucent mask."""
    edge = valid & ~binary_erosion(valid, iterations=3)
    rgba = np.zeros((*valid.shape, 4), dtype=np.uint8)
    rgba[valid] = [54, 228, 210, 24]
    rgba[edge] = [108, 255, 233, 196]
    Image.fromarray(rgba, mode="RGBA").save(path, optimize=True)


def invalid_pixel_overlay(path: Path, valid: np.ndarray) -> None:
    """Make missing/invalid pixels unmistakable without inventing replacements."""
    invalid = ~valid
    edge = invalid & ~binary_erosion(invalid, iterations=2)
    y, x = np.indices(valid.shape)
    hatch = invalid & (((x + y) % 18) < 3)
    rgba = np.zeros((*valid.shape, 4), dtype=np.uint8)
    rgba[hatch] = [255, 183, 94, 76]
    rgba[edge] = [255, 196, 118, 205]
    Image.fromarray(rgba, mode="RGBA").save(path, optimize=True)


def coverage_difference_overlay(path: Path, rubin_valid: np.ndarray, comparison_valid: np.ndarray) -> None:
    """Show exact, non-photometric coverage differences between two layers."""
    rubin_only = rubin_valid & ~comparison_valid
    comparison_only = comparison_valid & ~rubin_valid
    rgba = np.zeros((*rubin_valid.shape, 4), dtype=np.uint8)
    rgba[rubin_only] = [239, 72, 83, 205]
    rgba[comparison_only] = [66, 139, 255, 205]
    Image.fromarray(rgba, mode="RGBA").save(path, optimize=True)


def candidate_difference_overlay(
    path: Path,
    matched_pair: Path,
    zeropoint_offset_mag: float,
) -> tuple[list[dict], dict]:
    """Render empirical-significance candidates, explicitly not science claims.

    The stellar zeropoint offset is applied globally, then the residual is
    smoothed and standardized by the robust scatter measured in the outer
    field.  This deliberately avoids claiming independent-pixel Gaussian
    significance after resampling and PSF convolution.
    """
    with fits.open(matched_pair, memmap=False) as hdus:
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        comparison = np.asarray(hdus["COMPARISON"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data, dtype=bool)

    comparison_scale = 10 ** (-0.4 * zeropoint_offset_mag)
    residual = rubin - comparison * comparison_scale
    weight = gaussian_filter(common.astype(np.float32), sigma=3.0)
    smooth = np.divide(
        gaussian_filter(np.where(common, residual, 0.0), sigma=3.0),
        weight,
        out=np.zeros_like(residual),
        where=weight > 0.98,
    )
    height, width = residual.shape
    yy, xx = np.indices(residual.shape)
    outer = common & (weight > 0.98) & (np.hypot(xx - width / 2, yy - height / 2) > width * 0.22)
    center = float(np.median(smooth[outer]))
    scatter = float(1.4826 * np.median(np.abs(smooth[outer] - center)))
    score = np.divide(smooth - center, scatter, out=np.zeros_like(smooth), where=scatter > 0)
    candidate = common & (weight > 0.98) & (np.abs(score) >= 4.0)

    rgba = np.zeros((*common.shape, 4), dtype=np.uint8)
    strength = np.uint8(np.clip((np.abs(score) - 4.0) / 5.0, 0, 1) * 145 + 80)
    positive = candidate & (score > 0)
    negative = candidate & (score < 0)
    rgba[positive, :3] = [239, 72, 83]
    rgba[negative, :3] = [66, 139, 255]
    rgba[candidate, 3] = strength[candidate]

    components, count = label(candidate)
    regions = []
    for component_id in range(1, count + 1):
        component = components == component_id
        pixel_count = int(component.sum())
        if pixel_count < 24:
            continue
        component_scores = score[component]
        peak_index = int(np.argmax(np.abs(component_scores)))
        y_values, x_values = np.nonzero(component)
        peak_score = float(component_scores[peak_index])
        regions.append(
            {
                "id": f"candidate-{component_id}",
                "xPercent": float(x_values[peak_index] / (width - 1) * 100),
                "yPercent": float(y_values[peak_index] / (height - 1) * 100),
                "pixelCount": pixel_count,
                "peakEmpiricalSigma": peak_score,
                "direction": "rubin-excess" if peak_score > 0 else "comparison-excess",
            }
        )
    regions.sort(key=lambda item: abs(item["peakEmpiricalSigma"]), reverse=True)
    regions = regions[:8]
    Image.fromarray(rgba, mode="RGBA").save(path, optimize=True)
    return regions, {
        "thresholdEmpiricalSigma": 4.0,
        "outerFieldRobustScatterNjy": scatter,
        "stellarZeropointOffsetMag": zeropoint_offset_mag,
        "comparisonFluxScale": comparison_scale,
        "interpretation": "candidate QA residuals only; extended-source filter transfer and injection/recovery pending",
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="ugc00191")
    parser.add_argument("--rubin-root", type=Path, default=root / "pipeline" / "output" / "dp2-sparc")
    parser.add_argument("--legacy-root", type=Path, default=root / "pipeline" / "output" / "legacy-survey")
    parser.add_argument("--comparison-root", type=Path, default=root / "pipeline" / "output" / "comparisons")
    parser.add_argument("--output", type=Path, default=root / "public" / "private-preview")
    parser.add_argument("--public-data", type=Path, default=root / "public" / "data" / "prototype-science.json")
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
    rubin_z, legacy_z = shared_grayscale_pair(rubin_planes["z"], legacy_planes["z"])
    save_rgb(output_dir / "rubin-dp2.jpg", rubin_rgb)
    save_rgb(output_dir / "legacy-dr10.jpg", legacy_rgb)
    save_rgb(output_dir / "rubin-dp2-z.jpg", rubin_z)
    save_rgb(output_dir / "legacy-dr10-z.jpg", legacy_z)
    coverage_overlay(output_dir / "legacy-coverage.png", legacy_valid)
    invalid_pixel_overlay(output_dir / "rubin-mask.png", rubin_valid)
    invalid_pixel_overlay(output_dir / "legacy-mask.png", legacy_valid)
    invalid_pixel_overlay(output_dir / "rubin-z-mask.png", rubin_planes["z"][1])
    invalid_pixel_overlay(output_dir / "legacy-z-mask.png", legacy_planes["z"][1])
    coverage_difference_overlay(
        output_dir / "coverage-difference.png",
        rubin_planes["z"][1],
        legacy_planes["z"][1],
    )
    filter_audit = json.loads(
        (args.comparison_root / args.target / "filter-response-audit.json").read_text(encoding="utf-8")
    )
    candidate_regions, difference_method = candidate_difference_overlay(
        output_dir / "candidate-difference.png",
        args.comparison_root / args.target / "matched-pair.fits",
        float(filter_audit["model"]["interceptMag"]),
    )

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
            "rubinZ": "rubin-dp2-z.jpg",
            "legacyZ": "legacy-dr10-z.jpg",
            "coverage": "legacy-coverage.png",
            "rubinMask": "rubin-mask.png",
            "legacyMask": "legacy-mask.png",
            "rubinZMask": "rubin-z-mask.png",
            "legacyZMask": "legacy-z-mask.png",
            "coverageDifference": "coverage-difference.png",
            "candidateDifference": "candidate-difference.png",
        },
        "candidateRegions": candidate_regions,
        "differenceMethod": difference_method,
        "notice": "Display stretches only. Original calibrated FITS products remain the analysis inputs.",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    args.public_data.parent.mkdir(parents=True, exist_ok=True)
    args.public_data.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "target": args.target,
                "candidateRegions": candidate_regions,
                "differenceMethod": difference_method,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
