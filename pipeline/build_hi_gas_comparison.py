#!/usr/bin/env python3
"""Compare H I gas content against optical light for Rubin-footprint detections.

This is the one comparison in the plan that the bandpass blocker does not gate.
It is not a photometric difference between two images of the same band; it asks
whether the neutral-gas mass and rotation implied by H I agree with the stellar
light seen in the optical. Getting the filter transfer wrong by a few hundredths
of a magnitude does not change that answer.

Two things about the sample are worth stating up front.

**The comparison target is the gas source, not the tract centre.** HIPASS has a
15.5 arcmin beam and its detections sit tens of arcmin from Rubin tract centres:
across the 29 acquired tracts with a detection, the nearest is 11.3 arcmin out
and the median is 38 arcmin. None fall inside a 4 arcmin tract-centred cutout.
Optical cutouts are therefore requested at the H I position.

**Footprint overlap is not a detection.** HIPASS covers the southern sky, so it
"overlaps" every Rubin tract; that is a statement about sky coverage, not about
data. The real sample is the 622 HICAT and NHICAT detections that fall inside a
DP2 tract bound with a finite line width, integrated flux, and a velocity high
enough for a Hubble-flow distance.

The measured quantity is the residual from the baryonic Tully-Fisher relation:
how far the gas-plus-stellar mass sits from what the rotation velocity predicts.
A departure is an observation about the baryon budget, not a claim about dark
matter.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests

warnings.filterwarnings("ignore")

from astropy.coordinates import SkyCoord
from astropy.table import Table, vstack
import astropy.units as u

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FOOTPRINT = ROOT / "public/data/coverage/rubin-dp2-footprint.json"
DEFAULT_CACHE = ROOT / "pipeline/results/hi-gas/cache"
DEFAULT_OUTPUT = ROOT / "pipeline/results/hi-gas"
DEFAULT_PUBLIC = ROOT / "public/data/layers/hi-gas/comparison.json"

VIZIER = "https://vizier.cds.unistra.fr/viz-bin/votable"
CATALOGS = ("VIII/73/hicat", "VIII/89/nhicat")

# H I mass: M_HI/Msun = 2.356e5 * D_Mpc^2 * Sint(Jy km/s), optically thin.
HI_MASS_COEFFICIENT = 2.356e5
# Helium and metals raise the gas mass above the neutral-hydrogen mass.
HELIUM_CORRECTION = 1.36
HUBBLE_CONSTANT_KM_S_MPC = 70.0
# Minimum recession velocity for a Hubble-flow distance to be meaningful; below
# this, peculiar velocities dominate and the distance is not usable.
MIN_VELOCITY_KM_S = 300.0

# McGaugh 2012 baryonic Tully-Fisher: M_bar = A * V^4, A ~ 47 Msun / (km/s)^4.
# Held fixed and declared rather than fitted, so the residual is measured against
# a published expectation instead of against this sample's own mean.
BTFR_NORMALISATION = 47.0
BTFR_SLOPE = 4.0
BTFR_INTRINSIC_SCATTER_DEX = 0.11

# Systematic budget for a single object, dominated by the unknown inclination
# when no optical axis ratio is available.
DISTANCE_SYSTEMATIC_DEX = 0.10


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_catalog(cache: Path, refresh: bool) -> Table:
    tables = []
    for catalog in CATALOGS:
        path = cache / (catalog.replace("/", "-") + ".vot")
        if refresh or not path.is_file():
            response = requests.get(
                VIZIER,
                params={"-source": catalog, "-out.all": "1", "-out.max": "unlimited"},
                timeout=300,
            )
            response.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
        table = Table.read(io.BytesIO(path.read_bytes()), format="votable")
        table["catalog"] = catalog
        keep = ["HIPASS", "RAJ2000", "DEJ2000", "RVsp", "W50max", "W20max", "Sint", "Speak", "catalog"]
        tables.append(table[[column for column in keep if column in table.colnames]])
    return vstack(tables, metadata_conflicts="silent")


def tract_lookup(footprint: dict[str, Any], coords: SkyCoord) -> np.ndarray:
    ra = coords.ra.deg
    dec = coords.dec.deg
    assigned = np.full(ra.size, -1, dtype=int)
    for row in footprint["tracts"]:
        tract, bounds = int(row[0]), row[2]
        if bounds["ra"]["wraps"]:
            in_ra = (ra >= bounds["ra"]["start"]) | (ra <= bounds["ra"]["end"])
        else:
            in_ra = (ra >= bounds["ra"]["start"]) & (ra <= bounds["ra"]["end"])
        inside = in_ra & (dec >= bounds["dec_min"]) & (dec <= bounds["dec_max"])
        assigned[inside & (assigned < 0)] = tract
    return assigned


def value(row: Any, column: str) -> float | None:
    if column not in row.colnames:
        return None
    raw = row[column]
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def build_record(row: Any, tract: int) -> dict[str, Any] | None:
    velocity = value(row, "RVsp")
    width = value(row, "W50max")
    integrated = value(row, "Sint")
    if velocity is None or width is None or integrated is None:
        return None
    if velocity < MIN_VELOCITY_KM_S or width <= 0 or integrated <= 0:
        return None

    distance_mpc = velocity / HUBBLE_CONSTANT_KM_S_MPC
    hi_mass = HI_MASS_COEFFICIENT * distance_mpc**2 * integrated
    gas_mass = HELIUM_CORRECTION * hi_mass

    # W50 is the observed line width, so it carries sin(i). Without an optical
    # axis ratio the inclination is unknown; the deprojected rotation velocity is
    # therefore a lower bound and is reported as such rather than assumed.
    rotation_lower_bound = width / 2.0

    predicted_bar_mass = BTFR_NORMALISATION * rotation_lower_bound**BTFR_SLOPE
    log_gas = math.log10(gas_mass)
    log_predicted = math.log10(predicted_bar_mass)

    return {
        "id": str(row["HIPASS"]).strip(),
        "catalog": str(row["catalog"]),
        "tract": tract,
        "position": {"raDeg": float(row["_ra_deg"]), "decDeg": float(row["_dec_deg"])},
        "observed": {
            "recessionVelocityKmS": velocity,
            "lineWidthW50KmS": width,
            "integratedFluxJyKmS": integrated,
            "peakFluxJy": value(row, "Speak"),
        },
        "derived": {
            "hubbleFlowDistanceMpc": distance_mpc,
            "hiMassMsun": hi_mass,
            "logHiMassMsun": math.log10(hi_mass),
            "gasMassMsun": gas_mass,
            "logGasMassMsun": log_gas,
            "rotationVelocityLowerBoundKmS": rotation_lower_bound,
        },
        "expectation": {
            "relation": "baryonic Tully-Fisher, M_bar = 47 * V^4 (McGaugh 2012)",
            "logPredictedBaryonicMassMsun": log_predicted,
            "intrinsicScatterDex": BTFR_INTRINSIC_SCATTER_DEX,
        },
        "gasOnlyResidualDex": log_gas - log_predicted,
        "stellarMassMeasured": False,
        "opticalCutoutRequired": True,
        "blockers": [
            "no optical luminosity measured yet, so the stellar term of the baryonic mass is missing",
            "no optical axis ratio, so the inclination correction to W50 is not applied and the "
            "rotation velocity is a lower bound",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--footprint", type=Path, default=DEFAULT_FOOTPRINT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    catalog = fetch_catalog(args.cache, args.refresh)
    coords = SkyCoord(catalog["RAJ2000"], catalog["DEJ2000"], unit=(u.hourangle, u.deg))
    catalog["_ra_deg"] = coords.ra.deg
    catalog["_dec_deg"] = coords.dec.deg

    footprint = json.loads(args.footprint.read_text(encoding="utf-8"))
    tracts = tract_lookup(footprint, coords)
    inside = tracts >= 0

    records = []
    rejected = 0
    for index in np.flatnonzero(inside):
        record = build_record(catalog[index], int(tracts[index]))
        if record is None:
            rejected += 1
            continue
        records.append(record)

    residuals = np.array([item["gasOnlyResidualDex"] for item in records])
    payload = {
        "schemaVersion": "layers-hi-gas-comparison-v1",
        "generatedAt": utc_now(),
        "comparisonKind": "scaling-relation residual",
        "gatedByBandpassTransfer": False,
        "catalogs": list(CATALOGS),
        "counts": {
            "catalogDetections": int(len(catalog)),
            "insideRubinFootprint": int(inside.sum()),
            "usable": len(records),
            "rejectedForMissingOrLowQualityValues": rejected,
            "distinctTracts": len({item["tract"] for item in records}),
        },
        "assumptions": {
            "hubbleConstantKmSMpc": HUBBLE_CONSTANT_KM_S_MPC,
            "minimumVelocityKmS": MIN_VELOCITY_KM_S,
            "heliumCorrection": HELIUM_CORRECTION,
            "hiMassCoefficient": HI_MASS_COEFFICIENT,
            "btfrNormalisation": BTFR_NORMALISATION,
            "btfrSlope": BTFR_SLOPE,
            "distanceSystematicDex": DISTANCE_SYSTEMATIC_DEX,
        },
        "gasOnlyResidual": {
            "medianDex": float(np.median(residuals)) if residuals.size else None,
            "scatterDex": float(np.std(residuals)) if residuals.size else None,
            "note": (
                "This is the gas mass alone against the relation, with no stellar term and no "
                "inclination correction. It is not yet a baryonic Tully-Fisher residual and must "
                "not be read as one. It exists so the sample and the arithmetic can be checked "
                "before optical photometry is attached."
            ),
        },
        "remainingWork": [
            "Acquire optical cutouts centred on each H I position, not on the tract centre.",
            "Measure stellar luminosity and convert to stellar mass with a declared M/L.",
            "Measure the optical axis ratio and apply the inclination correction to W50.",
            "Only then is the residual a baryonic Tully-Fisher residual.",
        ],
        "detections": records,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"catalog detections: {len(catalog)}")
    print(f"inside a Rubin DP2 tract: {int(inside.sum())}")
    print(f"usable for the gas comparison: {len(records)} across {payload['counts']['distinctTracts']} tracts")
    if residuals.size:
        print(f"gas-only residual: median {np.median(residuals):+.3f} dex, scatter {np.std(residuals):.3f} dex")
    print("stellar term and inclination correction are still missing; this is not yet a BTFR residual")


if __name__ == "__main__":
    main()
