#!/usr/bin/env python3
"""Correlate CMB and weak-lensing convergence against Rubin optical light.

The fourth comparison operator, and the one whose stated shape had to change
before it could be built honestly.

**Why this is not a per-field ratio.** The goal asked for a convergence-to-light
ratio ranked field by field. That is not a physically interpretable quantity
here, for two independent reasons:

* CMB lensing convergence integrates every mass along the line of sight out to
  z ~ 1100 and peaks near z ~ 2. The optical light inside a 4 arcmin Rubin cutout
  is a low-redshift foreground that did not produce most of that convergence, so
  their ratio in one field is not a measurement of anything.
* Planck's lensing reconstruction is coarser than the entire 4 arcmin cutout. The
  64x64 grids here are sampled from that reconstruction, not resolved by it, so a
  single field contributes roughly one independent number and no map structure.

Ranking fields by that ratio would produce a list ordered mostly by reconstruction
noise, and every entry would look like a departure.

**What is defensible** is the statistic the mismatch permits: across many fields,
do the ones with more optical light show higher convergence? That is the standard
galaxy-density by CMB-lensing cross-correlation in its crudest form. It is one
number per survey with a bootstrap uncertainty, it is weak with a few dozen
fields, and it is honest.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
import astropy.units as u

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LENSING = ROOT / "public/data/layers/lensing-cmb/manifest.json"
DEFAULT_PRODUCTS = ROOT / "pipeline/results/lensing-cmb-pixels/products"
DEFAULT_RUBIN = ROOT / "pipeline/results/rubin-pixels-200/manifest.json"
DEFAULT_OUTPUT = ROOT / "pipeline/results/lensing-light"
DEFAULT_PUBLIC = ROOT / "public/data/layers/lensing-light/correlation.json"

MIN_FIELDS_PER_SURVEY = 10
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20260814


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mean_convergence(path: Path) -> float | None:
    with fits.open(path, memmap=False) as hdus:
        if "SCIENCE" not in hdus:
            return None
        science = np.asarray(hdus["SCIENCE"].data, dtype=np.float64)
        coverage = (
            np.asarray(hdus["COVERAGE"].data, dtype=np.float64) if "COVERAGE" in hdus else np.ones_like(science)
        )
    valid = np.isfinite(science) & (coverage > 0)
    if valid.sum() < 0.5 * science.size:
        return None
    return float(np.mean(science[valid]))


def integrated_light(path: Path) -> float | None:
    """Total unmasked Rubin flux in the field, in nJy."""
    with fits.open(path, memmap=False) as hdus:
        image = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
        mask = np.asarray(hdus["MASK"].data)
        variance = np.asarray(hdus["VARIANCE"].data, dtype=np.float64)
    good = (
        np.isfinite(image)
        & np.isfinite(variance)
        & (variance > 0)
        & ((mask & ((1 << 0) | (1 << 3))) == 0)
    )
    if good.sum() < 0.5 * image.size:
        return None
    # Subtract a robust sky so the total measures sources rather than the pedestal.
    values = image[good]
    sky = float(np.median(values))
    return float(np.sum(values - sky))



def linear_residual(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    design = np.column_stack((np.ones(x.size), x))
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    return y - design @ coefficients


def contamination_check(
    light: np.ndarray, kappa: np.ndarray, ra: np.ndarray, dec: np.ndarray
) -> dict[str, float]:
    """Test whether Galactic latitude drives the correlation.

    Integrated flux in a small field counts foreground stars as readily as
    extragalactic light, and stellar density rises toward the Galactic plane. If
    the light proxy tracks |b|, it is partly a star counter, and any correlation
    with a lensing map that also varies with |b| is confounded.
    """
    latitude = np.abs(SkyCoord(ra * u.deg, dec * u.deg).galactic.b.deg)
    return {
        "lightVersusGalacticLatitude": float(np.corrcoef(light, latitude)[0, 1]),
        "convergenceVersusGalacticLatitude": float(np.corrcoef(kappa, latitude)[0, 1]),
        "partialCorrelationControllingLatitude": float(
            np.corrcoef(linear_residual(light, latitude), linear_residual(kappa, latitude))[0, 1]
        ),
    }


def bootstrap_correlation(x: np.ndarray, y: np.ndarray, seed: int) -> dict[str, float]:
    generator = np.random.default_rng(seed)
    observed = float(np.corrcoef(x, y)[0, 1])
    draws = []
    for _ in range(BOOTSTRAP_SAMPLES):
        pick = generator.integers(0, x.size, x.size)
        if np.std(x[pick]) == 0 or np.std(y[pick]) == 0:
            continue
        draws.append(np.corrcoef(x[pick], y[pick])[0, 1])
    array = np.asarray(draws)
    # A null built by shuffling one axis says how large a correlation this many
    # fields produce by chance, which is the number that decides significance.
    null = []
    for _ in range(BOOTSTRAP_SAMPLES):
        null.append(np.corrcoef(x, generator.permutation(y))[0, 1])
    null_array = np.asarray(null)
    return {
        "correlation": observed,
        "bootstrap16": float(np.percentile(array, 16)) if array.size else float("nan"),
        "bootstrap84": float(np.percentile(array, 84)) if array.size else float("nan"),
        "nullScatter": float(np.std(null_array)),
        "significanceSigma": float(observed / np.std(null_array)) if np.std(null_array) > 0 else float("nan"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lensing", type=Path, default=DEFAULT_LENSING)
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--rubin-manifest", type=Path, default=DEFAULT_RUBIN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    args = parser.parse_args()

    rubin = {
        item["regionId"]: item
        for item in json.loads(args.rubin_manifest.read_text(encoding="utf-8"))["regions"]
        if item.get("validation", {}).get("scienceReady") and item.get("mosaic")
    }
    lensing = json.loads(args.lensing.read_text(encoding="utf-8"))["products"]

    light_cache: dict[str, float | None] = {}
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for product in lensing:
        if product.get("status") != "available":
            continue
        region_id = product["regionId"]
        survey = product["surveyId"]
        if region_id not in rubin:
            skipped.append({"regionId": region_id, "surveyId": survey, "reason": "no Rubin mosaic"})
            continue
        path = args.products / f"{region_id}-{survey}" / f"{region_id}-{survey}.fits"
        if not path.is_file():
            skipped.append({"regionId": region_id, "surveyId": survey, "reason": "no local lensing product"})
            continue
        kappa = mean_convergence(path)
        if kappa is None:
            skipped.append({"regionId": region_id, "surveyId": survey, "reason": "insufficient lensing coverage"})
            continue
        if region_id not in light_cache:
            light_cache[region_id] = integrated_light(ROOT / rubin[region_id]["mosaic"]["path"])
        light = light_cache[region_id]
        if light is None or light <= 0:
            skipped.append({"regionId": region_id, "surveyId": survey, "reason": "no usable Rubin light"})
            continue
        rows.append({
            "regionId": region_id,
            "tract": product["tract"],
            "raDeg": float(rubin[region_id]["center"][0]),
            "decDeg": float(rubin[region_id]["center"][1]),
            "surveyId": survey,
            "meanConvergence": kappa,
            "integratedLightNjy": light,
            "logIntegratedLight": float(np.log10(light)),
            "rubinBand": rubin[region_id]["band"],
        })

    surveys: dict[str, Any] = {}
    for survey in sorted({row["surveyId"] for row in rows}):
        subset = [row for row in rows if row["surveyId"] == survey]
        if len(subset) < MIN_FIELDS_PER_SURVEY:
            surveys[survey] = {"fields": len(subset), "status": "too-few-fields"}
            continue
        kappa = np.array([row["meanConvergence"] for row in subset])
        light = np.array([row["logIntegratedLight"] for row in subset])
        ra = np.array([row["raDeg"] for row in subset])
        dec = np.array([row["decDeg"] for row in subset])
        result = bootstrap_correlation(light, kappa, BOOTSTRAP_SEED)
        contamination = contamination_check(light, kappa, ra, dec)
        surveys[survey] = {
            "fields": len(subset),
            "status": "measured",
            **result,
            "contamination": contamination,
            "interpretation": (
                "Negative. Galaxies trace mass, so a physical signal would be positive; a negative "
                "correlation points at the light proxy or the lensing reconstruction, not at physics."
                if result["correlation"] < 0
                else "Positive, in the direction expected if the light proxy traces mass."
            ),
        }
        print(
            f"[{survey}] {len(subset)} fields  r={result['correlation']:+.3f} "
            f"[{result['bootstrap16']:+.3f}, {result['bootstrap84']:+.3f}]  "
            f"{result['significanceSigma']:+.2f} sigma against a shuffled null",
            flush=True,
        )
        print(
            f"           light vs |b| {contamination['lightVersusGalacticLatitude']:+.3f}  "
            f"kappa vs |b| {contamination['convergenceVersusGalacticLatitude']:+.3f}  "
            f"partial {contamination['partialCorrelationControllingLatitude']:+.3f}",
            flush=True,
        )

    summary = {
        "schemaVersion": "layers-lensing-light-correlation-v1",
        "generatedAt": utc_now(),
        "comparisonKind": "mass versus light, correlation across fields",
        "gatedByBandpassTransfer": False,
        "statistic": "Pearson correlation of mean convergence against log integrated Rubin flux, across fields",
        "perFieldRankingOffered": False,
        "whyNoPerFieldRanking": (
            "CMB lensing convergence integrates mass to z ~ 1100 and peaks near z ~ 2, so the optical "
            "light in a 4 arcmin low-redshift cutout did not produce it. Planck's reconstruction is "
            "also coarser than the whole cutout, so one field carries about one independent number. "
            "A per-field ratio would rank reconstruction noise and every entry would look anomalous."
        ),
        "counts": {"pairs": len(rows), "skipped": len(skipped), "surveys": len(surveys)},
        "surveys": surveys,
        "caveats": [
            "Integrated Rubin flux in a 4 arcmin field is a crude light proxy: it mixes foreground "
            "stars, unrelated galaxies, and any real structure without redshift information.",
            "A few dozen fields cannot measure a cross-correlation competitively; this establishes the "
            "estimator and its null, not a cosmological result.",
            "A significant correlation here would mean galaxies trace mass, which is expected. Its "
            "absence would more likely indicate a problem with the light proxy than with physics.",
            "Measured on the 50-region set, the light proxy correlates with Galactic latitude in every "
            "survey, so it is partly counting foreground stars rather than extragalactic light. No "
            "correlation reported here should be read as a mass-versus-light result until the proxy "
            "is replaced by a star-subtracted galaxy measurement.",
        ],
        "skipped": skipped,
        "fields": rows,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(rows)} lensing-by-light pairs across {len(surveys)} surveys, {len(skipped)} skipped")


if __name__ == "__main__":
    main()
