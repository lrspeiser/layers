#!/usr/bin/env python3
"""Ask whether anything changed between the ZTF baseline and the Rubin epoch.

The sixth comparison operator, and the only one where the bandpass blocker is
structurally irrelevant rather than merely tolerable.

**Why bandpass does not gate this.** Variability is measured inside ZTF, as the
chi-square of a light curve about a constant, which never leaves one photometric
system. The Rubin comparison then adds a single epoch whose offset from the ZTF
mean contains an unknown ZTF-to-Rubin bandpass term, but that term is the *same*
for every object in the field. Subtracting the field median removes it, exactly
as the H I and SED operators subtract theirs, so what survives is how one object
moved relative to its neighbours.

Two distinct questions are answered and reported separately, because they fail
differently:

* *Is this object variable in ZTF alone?* A well-sampled chi-square test, with
  the caveat that ZTF error bars are known to be optimistic, so the threshold is
  set from the observed chi-square distribution of the field rather than from the
  theoretical one.
* *Did it change by the Rubin epoch?* A single-epoch departure from the ZTF mean
  after the field offset is removed. One epoch cannot distinguish a real change
  from a bad measurement, so this is a candidate list and says so.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests

warnings.filterwarnings("ignore")

from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_layer_registration import robust_sigma

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUBIN = ROOT / "pipeline/results/rubin-pixels-200/manifest.json"
DEFAULT_CACHE = ROOT / "pipeline/results/ztf-variability/cache"
DEFAULT_OUTPUT = ROOT / "pipeline/results/ztf-variability"
DEFAULT_PUBLIC = ROOT / "public/data/layers/ztf-variability/comparison.json"

LIGHTCURVE_URL = "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves"
SEARCH_RADIUS_DEG = 0.033  # ~2 arcmin, the Rubin cutout half-width
BAND_BY_RUBIN = {"r": "r", "i": "r", "z": "r", "g": "g", "u": "g", "y": "r"}

MIN_EPOCHS = 20
MIN_OBJECTS_PER_REGION = 5
APERTURE_ARCSEC = 2.0
AB_ZERO_POINT_NJY = 3.63078054770e12
REQUEST_PAUSE_SECONDS = 0.4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_lightcurves(cache: Path, ra: float, dec: float, band: str, name: str) -> list[dict[str, str]] | None:
    path = cache / f"{name}-{band}.csv"
    if not path.is_file():
        try:
            response = requests.get(
                LIGHTCURVE_URL,
                params={
                    "POS": f"CIRCLE {ra:.7f} {dec:+.7f} {SEARCH_RADIUS_DEG:.5f}",
                    "BANDNAME": band,
                    "FORMAT": "CSV",
                },
                timeout=180,
            )
            response.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
        except Exception:
            return None
        finally:
            time.sleep(REQUEST_PAUSE_SECONDS)
    try:
        return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))))
    except Exception:
        return None


def aperture(image: np.ndarray, x: float, y: float, radius: float) -> float | None:
    height, width = image.shape
    lo_y, hi_y = max(0, int(y - radius) - 1), min(height, int(y + radius) + 2)
    lo_x, hi_x = max(0, int(x - radius) - 1), min(width, int(x + radius) + 2)
    if hi_y - lo_y < 3 or hi_x - lo_x < 3:
        return None
    yy, xx = np.mgrid[lo_y:hi_y, lo_x:hi_x]
    inside = np.hypot(xx - x, yy - y) <= radius
    values = image[lo_y:hi_y, lo_x:hi_x][inside]
    finite = np.isfinite(values)
    if finite.sum() < 0.8 * inside.sum():
        return None
    return float(values[finite].sum())


def summarize_object(rows: list[dict[str, str]]) -> dict[str, Any] | None:
    magnitudes, errors = [], []
    ra_values, dec_values = [], []
    for row in rows:
        try:
            if int(row.get("catflags") or 0) != 0:
                continue
            mag = float(row["mag"])
            err = float(row["magerr"])
            if not (np.isfinite(mag) and np.isfinite(err)) or err <= 0:
                continue
            magnitudes.append(mag)
            errors.append(err)
            ra_values.append(float(row["ra"]))
            dec_values.append(float(row["dec"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(magnitudes) < MIN_EPOCHS:
        return None
    mag = np.asarray(magnitudes)
    err = np.asarray(errors)
    weights = 1.0 / err**2
    mean = float(np.sum(weights * mag) / np.sum(weights))
    chi2 = float(np.sum(((mag - mean) / err) ** 2))
    dof = mag.size - 1
    return {
        "epochs": int(mag.size),
        "meanMag": mean,
        "reducedChiSquare": chi2 / max(dof, 1),
        "robustScatterMag": float(robust_sigma(mag)),
        "medianErrorMag": float(np.median(err)),
        "raDeg": float(np.median(ra_values)),
        "decDeg": float(np.median(dec_values)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubin-manifest", type=Path, default=DEFAULT_RUBIN)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    payload = json.loads(args.rubin_manifest.read_text(encoding="utf-8"))
    regions = [item for item in payload["regions"] if item.get("validation", {}).get("scienceReady")]
    if args.limit:
        regions = regions[: args.limit]

    objects: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for region in regions:
        region_id = region["regionId"]
        ra, dec = region["center"]
        band = BAND_BY_RUBIN.get(region["band"], "r")
        rows = fetch_lightcurves(args.cache, ra, dec, band, region_id)
        if rows is None:
            skipped.append({"regionId": region_id, "reason": "ZTF light-curve query failed"})
            continue
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            grouped.setdefault(row.get("oid", ""), []).append(row)

        summaries = []
        for oid, epochs in grouped.items():
            record = summarize_object(epochs)
            if record:
                summaries.append({"oid": oid, **record})
        if len(summaries) < MIN_OBJECTS_PER_REGION:
            skipped.append({"regionId": region_id, "reason": f"only {len(summaries)} ZTF objects with enough epochs"})
            continue

        with fits.open(ROOT / region["mosaic"]["path"], memmap=False) as hdus:
            image = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
            mask = np.asarray(hdus["MASK"].data)
            wcs = WCS(hdus["IMAGE"].header).celestial
        pixel_scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
        image = np.where((mask & ((1 << 0) | (1 << 3))) == 0, image, np.nan)
        radius = APERTURE_ARCSEC / pixel_scale

        for record in summaries:
            x, y = wcs.world_to_pixel_values(record["raDeg"], record["decDeg"])
            flux = None
            if np.isfinite(x) and np.isfinite(y):
                flux = aperture(image, float(x), float(y), radius)
            rubin_mag = (
                -2.5 * math.log10(flux / AB_ZERO_POINT_NJY) if flux and flux > 0 else None
            )
            objects.append({
                "regionId": region_id,
                "tract": region["tract"],
                "ztfBand": band,
                "rubinBand": region["band"],
                **record,
                "rubinApertureFluxNjy": flux,
                "rubinMagAB": rubin_mag,
                "rubinMinusZtfMag": (rubin_mag - record["meanMag"]) if rubin_mag is not None else None,
            })
        print(f"[ztf] {region_id} {len(summaries)} objects", flush=True)

    # ZTF error bars are known to be optimistic, so a reduced chi-square of 1 is
    # not the right variability threshold. It is taken from this sample's own
    # distribution instead.
    chi = np.array([item["reducedChiSquare"] for item in objects], dtype=float)
    chi_threshold = float(np.percentile(chi, 99)) if chi.size > 20 else float("nan")

    # The Rubin-minus-ZTF offset carries a constant bandpass term for the whole
    # field. Removing the field median leaves how one object moved relative to
    # its neighbours, which is the only part that means anything here.
    for region_id in {item["regionId"] for item in objects}:
        subset = [item for item in objects if item["regionId"] == region_id and item["rubinMinusZtfMag"] is not None]
        if len(subset) < MIN_OBJECTS_PER_REGION:
            continue
        offsets = np.array([item["rubinMinusZtfMag"] for item in subset])
        median = float(np.median(offsets))
        scatter = float(robust_sigma(offsets))
        for item in subset:
            item["fieldOffsetMag"] = median
            item["relativeChangeMag"] = item["rubinMinusZtfMag"] - median
            item["fieldScatterMag"] = scatter
            item["changeSignificance"] = (
                (item["rubinMinusZtfMag"] - median) / scatter if scatter > 0 else None
            )

    variable = [item for item in objects if np.isfinite(chi_threshold) and item["reducedChiSquare"] > chi_threshold]
    changed = [
        item
        for item in objects
        if item.get("changeSignificance") is not None and abs(item["changeSignificance"]) > 4.0
    ]

    # Sign balance, the same test the pixel scanner needed. Real change and
    # measurement noise both send objects brighter about as often as fainter.
    # A one-sided tail means the Rubin-minus-ZTF comparison is biased, not that
    # a population of objects all moved the same way.
    positive = sum(1 for item in changed if item["changeSignificance"] > 0)
    negative = len(changed) - positive
    imbalance = (
        abs(positive - len(changed) / 2) / math.sqrt(len(changed) / 4) if len(changed) >= 20 else 0.0
    )
    sign_dominated = imbalance > 3.0
    if sign_dominated:
        for item in changed:
            item["populationSystematic"] = (
                "sign imbalance of %.1f sigma across the changed population (%d of %d one way); the "
                "Rubin-to-ZTF comparison is biased rather than these objects having moved"
                % (imbalance, max(positive, negative), len(changed))
            )

    summary = {
        "schemaVersion": "layers-ztf-variability-v1",
        "generatedAt": utc_now(),
        "comparisonKind": "static versus variable",
        "gatedByBandpassTransfer": False,
        "whyNotGated": (
            "Variability is measured inside ZTF as a chi-square about a constant, which never leaves "
            "one photometric system. The Rubin epoch adds an offset carrying an unknown ZTF-to-Rubin "
            "bandpass term, but it is the same for every object in a field, so the field median "
            "removes it."
        ),
        "counts": {
            "regionsMeasured": len({item["regionId"] for item in objects}),
            "regionsSkipped": len(skipped),
            "objects": len(objects),
            "withRubinPhotometry": sum(1 for item in objects if item["rubinMagAB"] is not None),
            "variableInZtf": len(variable),
            "changedByRubinEpoch": len(changed),
        },
        "signBalance": {
            "brighterOrFainterSplit": [positive, negative],
            "imbalanceSigma": imbalance,
            "populationSystematicDetected": sign_dominated,
            "note": (
                "Real change and measurement noise both move objects each way about equally. A "
                "one-sided tail means the Rubin-to-ZTF comparison carries a bias the field median did "
                "not remove, most likely at the faint end where the Rubin aperture collects "
                "neighbours or sky."
            ),
        },
        "thresholds": {
            "reducedChiSquare99thPercentile": chi_threshold,
            "note": (
                "ZTF error bars are optimistic, so a reduced chi-square of 1 is not the variability "
                "threshold. It is taken from the 99th percentile of this sample's own distribution."
            ),
            "changeSigma": 4.0,
        },
        "caveats": [
            "A single Rubin epoch cannot separate a real change from a bad measurement. These are "
            "candidates for follow-up, not detected transients.",
            "The Rubin aperture magnitude is uncalibrated against ZTF beyond the field median, so only "
            "relative departures within a field mean anything.",
            "Objects are grouped by ZTF oid, which is per filter and per field, so the same physical "
            "source can appear more than once.",
        ],
        "skipped": skipped,
        "mostVariable": sorted(variable, key=lambda item: -item["reducedChiSquare"])[:40],
        "mostChanged": sorted(changed, key=lambda item: -abs(item["changeSignificance"]))[:40],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (args.output / "objects.json").write_text(json.dumps(objects, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(objects)} ZTF objects across {summary['counts']['regionsMeasured']} regions")
    print(f"  variable in ZTF alone:      {len(variable)}")
    print(f"  changed by the Rubin epoch: {len(changed)}")
    if sign_dominated:
        print(
            f"  POPULATION SYSTEMATIC: {max(positive, negative)} of {len(changed)} point the same way "
            f"({imbalance:.1f} sigma); these are not {len(changed)} transients"
        )


if __name__ == "__main__":
    main()
