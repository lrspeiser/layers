#!/usr/bin/env python3
"""Test whether Rubin optical flux agrees with the SED implied by 2MASS and WISE.

The third comparison operator. Rubin and the infrared surveys observe different
wavelengths, so this is not a difference of two images of the same thing: the
question is whether one spectral energy distribution can describe all of the
measurements at once, and how far the Rubin point sits from what the infrared
alone predicts.

**Catalogues, not images, on the infrared side.** The cached 2MASS and unWISE
cutouts carry raw DN with no zeropoint, no uncertainty plane, and no mask, and
turning those into calibrated flux means rebuilding a photometric chain per
survey. This session already found two separate places where exactly that kind
of chain was silently wrong by factors of 2.3 and 4. The published catalogues are
calibrated, carry per-source uncertainties, and remove that whole class of error.
Images remain the right product for morphology and diffuse light; they are the
wrong one for an integrated SED.

**The expectation is a fitted power law across the infrared only.** Fitting the
Rubin point too would guarantee agreement and measure nothing. The infrared
points define log f_nu = a + b*log(nu), that line is extrapolated to the Rubin
effective frequency, and the residual is the departure.

A departure is not a discovery. A galaxy is not a power law, so a real SED
curves, and the sign and size of the expected curvature depend on stellar
population, dust, and redshift. The residual is therefore ranked against the
*observed* scatter of the sample rather than against an asserted uncertainty,
which is the same correction the H I comparison needed.
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
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
import astropy.units as u

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_layer_registration import robust_sigma

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUBIN = ROOT / "pipeline/results/rubin-pixels-200/manifest.json"
DEFAULT_CACHE = ROOT / "pipeline/results/sed/cache"
DEFAULT_OUTPUT = ROOT / "pipeline/results/sed"
DEFAULT_PUBLIC = ROOT / "public/data/layers/sed/consistency.json"

VIZIER = "https://vizier.cds.unistra.fr/viz-bin/votable"
TWOMASS_PSC = "II/246/out"
ALLWISE = "II/328/allwise"

MATCH_RADIUS_ARCSEC = 3.0
# 2MASS is the shallow partner here: a 3 arcmin field holds 8 to 12 of its
# sources against roughly 100 AllWISE, and only 2 to 8 of those fall inside the
# 4 arcmin Rubin footprint. A per-region minimum of 5 discarded most regions
# outright. The statistics that matter are sample-wide, so the per-region gate
# is set low and the aggregate carries the analysis.
MIN_SOURCES_PER_REGION = 2
SEARCH_RADIUS_ARCMIN = 3.0
RUBIN_APERTURE_ARCSEC = 2.0

# Effective wavelengths in micron and the zero-magnitude flux in Jy that converts
# each catalogue magnitude to a flux density. 2MASS and WISE are Vega systems.
BANDS = {
    "J": {"wavelengthUm": 1.235, "zeroPointJy": 1594.0, "catalog": "2mass"},
    "H": {"wavelengthUm": 1.662, "zeroPointJy": 1024.0, "catalog": "2mass"},
    "Ks": {"wavelengthUm": 2.159, "zeroPointJy": 666.7, "catalog": "2mass"},
    "W1": {"wavelengthUm": 3.353, "zeroPointJy": 309.54, "catalog": "allwise"},
    "W2": {"wavelengthUm": 4.603, "zeroPointJy": 171.787, "catalog": "allwise"},
}
RUBIN_BAND_WAVELENGTH_UM = {"u": 0.3671, "g": 0.4827, "r": 0.6223, "i": 0.7546, "z": 0.8691, "y": 0.9712}
SPEED_OF_LIGHT_UM_HZ = 2.99792458e14
AB_ZERO_POINT_NJY = 3.63078054770e12
JY_TO_NJY = 1e9


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_catalog(cache: Path, catalog: str, ra: float, dec: float, name: str) -> Table | None:
    path = cache / f"{name}-{catalog.replace('/', '-')}.vot"
    if not path.is_file():
        try:
            response = requests.get(
                VIZIER,
                params={
                    "-source": catalog,
                    "-c": f"{ra:.7f} {dec:+.7f}",
                    "-c.rm": f"{SEARCH_RADIUS_ARCMIN:.2f}",
                    "-out.max": "5000",
                    "-out.all": "1",
                },
                timeout=180,
            )
            response.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
        except Exception:
            return None
    try:
        return Table.read(io.BytesIO(path.read_bytes()), format="votable")
    except Exception:
        return None


def magnitude_to_njy(magnitude: float, zero_point_jy: float) -> float | None:
    if magnitude is None or not np.isfinite(magnitude):
        return None
    return zero_point_jy * (10 ** (-0.4 * magnitude)) * JY_TO_NJY



def rubin_aperture_flux(
    image: np.ndarray, wcs: WCS, pixel_scale: float, ra: float, dec: float
) -> float | None:
    """Sum Rubin flux in a fixed sky aperture at a catalogue position."""
    x, y = wcs.world_to_pixel_values(ra, dec)
    if not (np.isfinite(x) and np.isfinite(y)):
        return None
    radius = RUBIN_APERTURE_ARCSEC / pixel_scale
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


def fit_power_law(frequencies: np.ndarray, fluxes: np.ndarray) -> tuple[float, float] | None:
    positive = fluxes > 0
    if positive.sum() < 3:
        return None
    x = np.log10(frequencies[positive])
    y = np.log10(fluxes[positive])
    design = np.column_stack((np.ones(x.size), x))
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(coefficients[0]), float(coefficients[1])


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

    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for region in regions:
        region_id = region["regionId"]
        ra, dec = region["center"]
        band = region["band"]
        rubin_wavelength = RUBIN_BAND_WAVELENGTH_UM.get(band)
        if rubin_wavelength is None:
            skipped.append({"regionId": region_id, "reason": f"no effective wavelength for band {band}"})
            continue

        mosaic_path = ROOT / region["mosaic"]["path"] if region.get("mosaic") else None
        if mosaic_path is None or not mosaic_path.is_file():
            skipped.append({"regionId": region_id, "reason": "no Rubin mosaic on disk"})
            continue
        with fits.open(mosaic_path, memmap=False) as hdus:
            rubin_image = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
            rubin_mask = np.asarray(hdus["MASK"].data)
            rubin_wcs = WCS(hdus["IMAGE"].header).celestial
        rubin_scale = float(np.mean(proj_plane_pixel_scales(rubin_wcs)) * 3600.0)
        rubin_image = np.where((rubin_mask & ((1 << 0) | (1 << 3))) == 0, rubin_image, np.nan)

        twomass = fetch_catalog(args.cache, TWOMASS_PSC, ra, dec, region_id)
        allwise = fetch_catalog(args.cache, ALLWISE, ra, dec, region_id)
        if twomass is None or allwise is None or not len(twomass) or not len(allwise):
            skipped.append({"regionId": region_id, "reason": "no infrared catalogue rows returned"})
            continue

        try:
            two_coords = SkyCoord(twomass["RAJ2000"], twomass["DEJ2000"], unit=u.deg)
            wise_coords = SkyCoord(allwise["RAJ2000"], allwise["DEJ2000"], unit=u.deg)
        except Exception as error:
            skipped.append({"regionId": region_id, "reason": f"coordinate parse failed: {error}"})
            continue

        index, separation, _ = two_coords.match_to_catalog_sky(wise_coords)
        paired = separation.arcsec <= MATCH_RADIUS_ARCSEC

        sources = []
        for position in np.flatnonzero(paired):
            two_row = twomass[int(position)]
            wise_row = allwise[int(index[int(position)])]
            fluxes = {}
            for name, meta in BANDS.items():
                source_row = two_row if meta["catalog"] == "2mass" else wise_row
                column = {"J": "Jmag", "H": "Hmag", "Ks": "Kmag", "W1": "W1mag", "W2": "W2mag"}[name]
                if column not in source_row.colnames:
                    continue
                try:
                    magnitude = float(source_row[column])
                except (TypeError, ValueError):
                    continue
                flux = magnitude_to_njy(magnitude, meta["zeroPointJy"])
                if flux and flux > 0:
                    fluxes[name] = flux
            if len(fluxes) < 3:
                continue
            names = list(fluxes)
            frequencies = np.array([SPEED_OF_LIGHT_UM_HZ / BANDS[n]["wavelengthUm"] for n in names])
            values = np.array([fluxes[n] for n in names])
            fit = fit_power_law(frequencies, values)
            if fit is None:
                continue
            intercept, slope = fit
            rubin_frequency = SPEED_OF_LIGHT_UM_HZ / rubin_wavelength
            predicted = 10 ** (intercept + slope * math.log10(rubin_frequency))
            source_ra = float(two_coords[int(position)].ra.deg)
            source_dec = float(two_coords[int(position)].dec.deg)
            observed = rubin_aperture_flux(rubin_image, rubin_wcs, rubin_scale, source_ra, source_dec)
            if observed is None or observed <= 0:
                continue
            # A power law from 1.2 to 4.6 micron extrapolated to the optical is a
            # deliberately crude SED, so the absolute ratio carries large
            # curvature. What is informative is how each source departs from the
            # ratio the rest of the sample shows at the same infrared colour.
            colour = None
            if "J" in fluxes and "W1" in fluxes:
                colour = -2.5 * math.log10(fluxes["J"] / fluxes["W1"])
            sources.append({
                "raDeg": source_ra,
                "decDeg": source_dec,
                "infraredBands": names,
                "infraredFluxNjy": {n: fluxes[n] for n in names},
                "powerLawSlope": slope,
                "predictedRubinFluxNjy": predicted,
                "predictedRubinMagAB": -2.5 * math.log10(predicted / AB_ZERO_POINT_NJY),
                "observedRubinFluxNjy": observed,
                "logObservedOverPredicted": math.log10(observed / predicted),
                "infraredColourJminusW1": colour,
            })

        if len(sources) < MIN_SOURCES_PER_REGION:
            skipped.append({"regionId": region_id, "reason": f"only {len(sources)} SED-usable sources"})
            continue

        slopes = np.array([item["powerLawSlope"] for item in sources])
        records.append({
            "regionId": region_id,
            "tract": region["tract"],
            "rubinBand": band,
            "rubinEffectiveWavelengthUm": rubin_wavelength,
            "counts": {
                "twomassRows": int(len(twomass)),
                "allwiseRows": int(len(allwise)),
                "matchedPairs": int(paired.sum()),
                "sedUsable": len(sources),
            },
            "infraredSlope": {"median": float(np.median(slopes)), "scatter": float(robust_sigma(slopes))},
            "sources": sources,
        })
        print(f"[sed] {region_id} {len(sources)} sources, median slope {np.median(slopes):+.2f}", flush=True)

    all_sources = [source for item in records for source in item["sources"]]
    all_slopes = np.array([source["powerLawSlope"] for source in all_sources])

    # The absolute optical-to-infrared ratio is dominated by SED curvature between
    # 4.6 and 0.6 micron, which a power law cannot represent, so it is not itself
    # informative. What is informative is how far a source sits from the ratio the
    # rest of the sample shows at the same infrared colour. That relation is fitted
    # on the sample and departures are ranked against the observed scatter, never
    # an asserted uncertainty.
    ratios = np.array([source["logObservedOverPredicted"] for source in all_sources], dtype=float)
    colours = np.array(
        [source["infraredColourJminusW1"] if source["infraredColourJminusW1"] is not None else np.nan
         for source in all_sources],
        dtype=float,
    )
    usable = np.isfinite(ratios) & np.isfinite(colours)
    colour_relation = None
    residual_scatter = float("nan")
    colour_correlation = float("nan")
    if usable.sum() >= 20:
        design = np.column_stack((np.ones(int(usable.sum())), colours[usable]))
        coefficients, *_ = np.linalg.lstsq(design, ratios[usable], rcond=None)
        model = coefficients[0] + coefficients[1] * colours
        residual = ratios - model
        residual_scatter = float(robust_sigma(residual[usable]))
        colour_correlation = float(np.corrcoef(residual[usable], colours[usable])[0, 1])
        colour_relation = {
            "intercept": float(coefficients[0]),
            "colourTerm": float(coefficients[1]),
            "sources": int(usable.sum()),
            "residualScatterDex": residual_scatter,
            "residualVersusColourCorrelation": colour_correlation,
        }
        for source, value, keep in zip(all_sources, residual, usable):
            if not keep or not np.isfinite(residual_scatter) or residual_scatter <= 0:
                source["colourRelationResidualDex"] = None
                source["significanceSigma"] = None
                source["classification"] = "uncalibrated"
                continue
            source["colourRelationResidualDex"] = float(value)
            source["significanceSigma"] = float(value / residual_scatter)
            source["classification"] = (
                "noteworthy" if abs(value) > 3 * residual_scatter else "expected"
            )
    # Field-level concentration. A region that produces far more departures than
    # its peers is carrying a field systematic, not that many discoveries. On the
    # 50-region set one region supplied 24 of 35 departures, 22% of its own
    # sources, while every other contributing region supplied exactly one.
    by_region: dict[str, dict[str, int]] = {}
    for item in records:
        flagged = sum(1 for source in item["sources"] if source.get("classification") == "noteworthy")
        by_region[item["regionId"]] = {"noteworthy": flagged, "total": len(item["sources"])}
    rates = np.array(
        [entry["noteworthy"] / entry["total"] for entry in by_region.values() if entry["total"] >= 5],
        dtype=float,
    )
    rate_cut = (
        float(np.median(rates) + 3.0 * max(robust_sigma(rates), 0.02)) if rates.size >= 5 else float("inf")
    )
    crowded = {
        region
        for region, entry in by_region.items()
        if entry["total"] >= 5 and entry["noteworthy"] / entry["total"] > rate_cut
    }
    for item in records:
        if item["regionId"] not in crowded:
            continue
        for source in item["sources"]:
            if source.get("classification") == "noteworthy":
                source["classification"] = "field-systematic"
                source["demotedBecause"] = (
                    f"region yields {by_region[item['regionId']]['noteworthy']} departures in "
                    f"{by_region[item['regionId']]['total']} sources, above the {rate_cut:.3f} rate cutoff"
                )

    noteworthy = [source for source in all_sources if source.get("classification") == "noteworthy"]
    summary = {
        "schemaVersion": "layers-sed-consistency-v1",
        "generatedAt": utc_now(),
        "comparisonKind": "spectral energy distribution consistency",
        "gatedByBandpassTransfer": False,
        "method": (
            "2MASS PSC and AllWISE photometry are matched within 3 arcsec, a power law is fitted to the "
            "infrared points only, and that law is extrapolated to the Rubin effective frequency. The "
            "Rubin measurement is deliberately excluded from the fit so the prediction is independent."
        ),
        "counts": {
            "regionsMeasured": len(records),
            "regionsSkipped": len(skipped),
            "sedSources": int(all_slopes.size),
        },
        "infraredSlope": {
            "median": float(np.median(all_slopes)) if all_slopes.size else None,
            "scatter": float(robust_sigma(all_slopes)) if all_slopes.size > 3 else None,
        },
        "status": "measured" if colour_relation else "prediction-only",
        "colourRelation": colour_relation,
        "fieldSystematics": {
            "demotedRegions": sorted(crowded),
            "rateCutoff": rate_cut if np.isfinite(rate_cut) else None,
        },
        "noteworthy": {
            "count": len(noteworthy),
            "threshold": "3 x the observed residual scatter of the fitted colour relation",
            "sources": sorted(noteworthy, key=lambda item: -abs(item["significanceSigma"]))[:40],
        },
        "remainingWork": [
            "Rank departures against the observed scatter of the sample, not an asserted uncertainty.",
            "A power law is a deliberately crude SED; real curvature between the near and mid infrared "
            "sets a floor on the residual that must be characterised before any departure is called an "
            "anomaly.",
        ],
        "skipped": skipped,
        "regions": [{key: value for key, value in item.items() if key != "sources"} for item in records],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (args.output / "sources.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nmeasured {len(records)} regions, {len(skipped)} skipped, {all_slopes.size} SED sources")
    if all_slopes.size:
        print(f"infrared power-law slope: median {np.median(all_slopes):+.3f}, scatter {robust_sigma(all_slopes):.3f}")
    if colour_relation:
        print(
            f"colour relation on {colour_relation['sources']} sources: "
            f"slope {colour_relation['colourTerm']:+.3f}, residual scatter {residual_scatter:.3f} dex, "
            f"residual-vs-colour correlation {colour_correlation:+.3f}"
        )
        if crowded:
            print(f"demoted {len(crowded)} region(s) as field-systematic: {', '.join(sorted(crowded))}")
        print(f"noteworthy departures (>3 observed sigma): {len(noteworthy)}")
    else:
        print("too few sources with both a Rubin flux and an infrared colour to fit the relation")


if __name__ == "__main__":
    main()
