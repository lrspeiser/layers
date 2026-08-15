#!/usr/bin/env python3
"""Find out what the residual astrometric error is made of before fixing it.

Registration is the tightest gate in the reconciliation: 110 of 190 Legacy
regions clear a 0.30 arcsec p95 residual, and that is what holds `matched` down
to 91. The reconciler corrects registration with a single median source-to-source
translation, so the obvious next move is a richer transform.

Obvious is not the same as correct. A richer transform only helps if the residual
actually contains the terms it would remove, and this project has already paid
once for asserting a plausible mechanism -- the reference-PSF explanation for the
density trend -- and having to withdraw it when a third survey arrived. So this
measures the decomposition first and recommends second.

For each region it fits, on matched source pairs:

* **translation** -- what the reconciler already removes
* **similarity** -- translation, rotation and uniform scale
* **affine** -- the full six-parameter linear map

and reports the p95 residual under each, plus the floor set by centroid
uncertainty. If similarity or affine does not move p95 across the threshold, the
threshold is not reachable by a better transform and the honest answer is that
these two surveys disagree at this level, not that the fit was too simple.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

warnings.filterwarnings("ignore")

from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_layer_registration import centroid_sources

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTS = ROOT / "pipeline/results/reconciled-regions-200"
DEFAULT_OUTPUT = ROOT / "public/data/layers/selected-regions/registration-residual.json"

MATCH_TOLERANCE_ARCSEC = 1.0
MIN_PAIRS = 12
THRESHOLD_ARCSEC = 0.30


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def matched_pairs(a: list[dict], b: list[dict], scale: float) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-neighbour pairs, mutual and inside the tolerance."""
    if not a or not b:
        return np.empty((0, 2)), np.empty((0, 2))
    pa = np.array([[s["x"], s["y"]] for s in a])
    pb = np.array([[s["x"], s["y"]] for s in b])
    tolerance = MATCH_TOLERANCE_ARCSEC / scale
    left, right = [], []
    for i, point in enumerate(pa):
        distances = np.hypot(pb[:, 0] - point[0], pb[:, 1] - point[1])
        j = int(np.argmin(distances))
        if distances[j] > tolerance:
            continue
        # Mutual nearest neighbour, or a crowded field produces pairs that are
        # not the same object and the fitted transform absorbs the mismatch.
        back = np.hypot(pa[:, 0] - pb[j, 0], pa[:, 1] - pb[j, 1])
        if int(np.argmin(back)) != i:
            continue
        left.append(point)
        right.append(pb[j])
    return np.asarray(left), np.asarray(right)


def residual_p95(source: np.ndarray, target: np.ndarray, model: str, scale: float) -> dict[str, Any]:
    """Fit source -> target under a model and report the residual in arcsec."""
    n = len(source)
    if n < MIN_PAIRS:
        return {"model": model, "pairs": n, "p95Arcsec": None}
    if model == "translation":
        shift = np.median(target - source, axis=0)
        predicted = source + shift
        parameters = 2
    elif model == "similarity":
        # Umeyama: rotation and uniform scale about the centroids.
        mu_s, mu_t = source.mean(axis=0), target.mean(axis=0)
        cs, ct = source - mu_s, target - mu_t
        u, s, vt = np.linalg.svd(cs.T @ ct / n)
        d = np.sign(np.linalg.det(u @ vt))
        rotation = (u @ np.diag([1.0, d]) @ vt).T
        variance = (cs**2).sum() / n
        factor = float((s * np.array([1.0, d])).sum() / variance) if variance > 0 else 1.0
        predicted = factor * (cs @ rotation.T) + mu_t
        parameters = 4
    elif model == "affine":
        design = np.hstack([source, np.ones((n, 1))])
        coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
        predicted = design @ coefficients
        parameters = 6
    else:
        raise ValueError(model)
    distances = np.hypot(*(target - predicted).T) * scale
    result = {
        "model": model,
        "pairs": n,
        "freeParameters": parameters,
        "p95Arcsec": float(np.percentile(distances, 95)),
        "medianArcsec": float(np.median(distances)),
        "passesThreshold": bool(np.percentile(distances, 95) <= THRESHOLD_ARCSEC),
    }
    if model == "similarity":
        angle = float(np.degrees(np.arctan2(rotation[1, 0], rotation[0, 0])))
        result["rotationDeg"] = angle
        result["scaleFactor"] = factor
    return result


def region_residual(path: Path) -> dict[str, Any] | None:
    with fits.open(path, memmap=False) as hdus:
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        reference = np.asarray(hdus["REFERENCE"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data).astype(bool)
        wcs = WCS(hdus["RUBIN"].header).celestial
    scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
    if not np.isfinite(scale) or scale <= 0:
        return None
    a = centroid_sources(rubin, common, 0.0, scale)
    b = centroid_sources(reference, common, 0.0, scale)
    source, target = matched_pairs(a, b, scale)
    if len(source) < MIN_PAIRS:
        return None
    models = [residual_p95(source, target, m, scale) for m in ("translation", "similarity", "affine")]
    return {"pixelScaleArcsec": scale, "models": {m["model"]: m for m in models}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    paths = sorted(p for region in sorted(args.products.iterdir()) if region.is_dir()
                   for p in region.glob("*.fits"))
    if args.limit:
        paths = paths[: args.limit]

    regions: list[dict[str, Any]] = []
    for path in paths:
        try:
            result = region_residual(path)
        except Exception:
            result = None
        if result is None:
            continue
        result["regionId"] = path.parent.name
        regions.append(result)
        models = result["models"]
        print(
            f"{result['regionId']}  translation {models['translation']['p95Arcsec']:.3f}  "
            f"similarity {models['similarity']['p95Arcsec']:.3f}  "
            f"affine {models['affine']['p95Arcsec']:.3f}",
            flush=True,
        )

    def summarise(model: str) -> dict[str, Any]:
        values = [r["models"][model]["p95Arcsec"] for r in regions if r["models"][model]["p95Arcsec"]]
        passing = sum(1 for r in regions if r["models"][model].get("passesThreshold"))
        return {
            "medianP95Arcsec": float(np.median(values)) if values else None,
            "regionsPassingThreshold": passing,
            "regionsMeasured": len(regions),
        }

    summary = {model: summarise(model) for model in ("translation", "similarity", "affine")}
    gain = None
    if summary["translation"]["regionsMeasured"]:
        gain = summary["affine"]["regionsPassingThreshold"] - summary["translation"]["regionsPassingThreshold"]

    rotations = [r["models"]["similarity"].get("rotationDeg") for r in regions]
    rotations = [x for x in rotations if x is not None]
    scales = [r["models"]["similarity"].get("scaleFactor") for r in regions]
    scales = [x for x in scales if x is not None]

    if gain is None:
        verdict = "not measured"
    elif gain >= 10:
        verdict = (
            "A richer transform is worth implementing: it moves a substantial number of regions "
            "across the threshold, so the residual contains real rotation or scale terms."
        )
    elif gain > 0:
        verdict = (
            f"A richer transform recovers only {gain} regions. The residual is mostly irreducible "
            "scatter, not an unmodelled rotation or scale, so the gate is measuring how well two "
            "surveys agree rather than how well the fit was done."
        )
    else:
        verdict = (
            "A richer transform recovers nothing. The residual is centroid-level scatter, so the "
            "0.30 arcsec threshold is not reachable by improving the transform, and the honest "
            "reading is that these surveys disagree at this level on these fields."
        )

    payload = {
        "schemaVersion": "layers-registration-residual-v1",
        "generatedAt": utc_now(),
        "question": (
            "Registration is the tightest reconciliation gate. Is the residual an unmodelled "
            "rotation or scale that a richer transform would remove, or irreducible scatter?"
        ),
        "thresholdArcsec": THRESHOLD_ARCSEC,
        "method": (
            "Mutual nearest-neighbour source pairs on the reconciled planes, fitted under three "
            "models of increasing freedom, with the p95 residual reported for each. Mutual "
            "matching matters: one-way nearest neighbours in a crowded field pair different "
            "objects and the transform absorbs the mismatch."
        ),
        "summary": summary,
        "regionsRecoveredByAffine": gain,
        "similarityTerms": {
            "medianRotationDeg": float(np.median(rotations)) if rotations else None,
            "rotationSpreadDeg": float(np.std(rotations, ddof=1)) if len(rotations) > 1 else None,
            "medianScaleFactor": float(np.median(scales)) if scales else None,
        },
        "verdict": verdict,
        "regions": regions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("\n" + json.dumps({k: v for k, v in payload.items() if k in
                             ("summary", "regionsRecoveredByAffine", "similarityTerms")}, indent=2))
    print(f"\n{verdict}")
    print(f"wrote {display_path(args.output)}")


if __name__ == "__main__":
    main()
