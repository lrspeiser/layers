#!/usr/bin/env python3
"""Ask whether each eROSITA X-ray source has an optical counterpart in Rubin.

The fifth comparison operator. It is not a difference of two images: an X-ray
detection and an optical image measure different physics, so the question is
whether a source exists at that position at all, and if not, how deep the
non-detection is. Neither half needs the bandpass transfer.

**Catalogued positions, never the query position.** The cached eROSITA records in
this project carry an ``upperLimit`` block whose ``ra``/``dec`` is the position
the upper limit was *requested* at, which for these regions is exactly the tract
centre. Matching anomaly candidates against that field would manufacture an X-ray
association for anything near a tract centre, with a separation encoding only the
offset from that centre. This pulls the eRASS1 main catalogue instead
(``J/A+A/682/A34/erass1-m``), which carries a position per detection.

**A non-detection is a measurement only if it has a depth.** Reporting "no
optical counterpart" without saying how faint a counterpart could have been and
still been missed is not a result. The limiting flux comes from the empirical
blank-position scatter measured by the injection/recovery stage, not from the
per-pixel variance planes, which understate the true uncertainty on these
products by a median factor of about seven.

An X-ray source with no optical counterpart down to a stated depth is a genuinely
interesting object class. It is also what a spurious X-ray detection looks like,
so the eRASS1 detection likelihood is carried through for every match.
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
DEFAULT_RECOVERY = ROOT / "pipeline/results/region-recovery-200"
DEFAULT_CACHE = ROOT / "pipeline/results/xray-counterparts/cache"
DEFAULT_OUTPUT = ROOT / "pipeline/results/xray-counterparts"
DEFAULT_PUBLIC = ROOT / "public/data/layers/xray-counterparts/comparison.json"

VIZIER = "https://vizier.cds.unistra.fr/viz-bin/votable"
ERASS1 = "J/A+A/682/A34/erass1-m"

# eROSITA's positional uncertainty is a few arcsec; this is generous enough to
# catch a counterpart and tight enough that a random field source rarely lands
# inside it. The chance-coincidence rate is measured per field rather than assumed.
MATCH_RADIUS_ARCSEC = 10.0
APERTURE_ARCSEC = 2.0
DETECTION_SIGMA = 5.0
AB_ZERO_POINT_NJY = 3.63078054770e12


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_sources(cache: Path, ra: float, dec: float, radius_arcmin: float, name: str) -> Table | None:
    path = cache / f"{name}-erass1.vot"
    if not path.is_file():
        try:
            response = requests.get(
                VIZIER,
                params={
                    "-source": ERASS1,
                    "-c": f"{ra:.7f} {dec:+.7f}",
                    "-c.rm": f"{radius_arcmin:.2f}",
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


def empirical_aperture_noise(
    image: np.ndarray, valid: np.ndarray, radius: float, seed: int, trials: int = 200
) -> float:
    """Scatter of the same aperture sum at random blank positions.

    This is the depth of a non-detection. It is measured with the identical
    estimator used on the source positions, so the threshold applies to them.
    """
    generator = np.random.default_rng(seed)
    height, width = image.shape
    margin = int(radius) + 2
    draws = []
    for _ in range(trials * 6):
        if len(draws) >= trials:
            break
        x = float(generator.integers(margin, width - margin))
        y = float(generator.integers(margin, height - margin))
        patch = valid[int(y) - margin : int(y) + margin, int(x) - margin : int(x) + margin]
        if patch.size == 0 or patch.mean() < 0.95:
            continue
        value = aperture(image, x, y, radius)
        if value is not None and np.isfinite(value):
            draws.append(value)
    if len(draws) < 20:
        return float("nan")
    return float(robust_sigma(np.asarray(draws)))


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

    matches: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    covered = 0
    with_rows = 0
    catalogued = 0
    for region in regions:
        region_id = region["regionId"]
        ra, dec = region["center"]
        table = fetch_sources(args.cache, ra, dec, 3.0, region_id)
        if table is None:
            skipped.append({"regionId": region_id, "reason": "eRASS1 query failed"})
            continue
        covered += 1
        if len(table) and "RA_ICRS" in table.colnames:
            with_rows += 1
            catalogued += len(table)
        else:
            # A region inside the eRASS1 sky with no catalogued source is a real
            # result about that field, not a failure, and is counted separately
            # from a region the survey never observed.
            continue

        with fits.open(ROOT / region["mosaic"]["path"], memmap=False) as hdus:
            image = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
            mask = np.asarray(hdus["MASK"].data)
            variance = np.asarray(hdus["VARIANCE"].data, dtype=np.float64)
            wcs = WCS(hdus["IMAGE"].header).celestial
        pixel_scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
        valid = (
            np.isfinite(image)
            & np.isfinite(variance)
            & (variance > 0)
            & ((mask & ((1 << 0) | (1 << 3))) == 0)
        )
        radius = APERTURE_ARCSEC / pixel_scale
        noise = empirical_aperture_noise(image, valid, radius, seed=abs(hash(region_id)) % (2**31))
        if not np.isfinite(noise) or noise <= 0:
            skipped.append({"regionId": region_id, "reason": "could not measure blank-aperture noise"})
            continue
        threshold = DETECTION_SIGMA * noise
        limiting_mag = -2.5 * math.log10(threshold / AB_ZERO_POINT_NJY) if threshold > 0 else None

        coords = SkyCoord(table["RA_ICRS"], table["DE_ICRS"], unit=u.deg)
        for index, source in enumerate(table):
            x, y = wcs.world_to_pixel_values(coords[index].ra.deg, coords[index].dec.deg)
            if not (np.isfinite(x) and np.isfinite(y)):
                continue
            if not (radius + 2 <= x < image.shape[1] - radius - 2 and radius + 2 <= y < image.shape[0] - radius - 2):
                continue
            flux = aperture(image, float(x), float(y), radius)
            if flux is None:
                continue
            detected = flux >= threshold
            likelihood = None
            for column in ("DET_LIKE_0", "DET_LIKE", "MLLike"):
                if column in table.colnames:
                    try:
                        likelihood = float(source[column])
                    except (TypeError, ValueError):
                        likelihood = None
                    break
            matches.append({
                "regionId": region_id,
                "tract": region["tract"],
                "xrayName": str(source["IAUName"]) if "IAUName" in table.colnames else f"src-{index}",
                "position": {"raDeg": float(coords[index].ra.deg), "decDeg": float(coords[index].dec.deg)},
                "rubinBand": region["band"],
                "apertureFluxNjy": flux,
                "apertureRadiusArcsec": round(radius * pixel_scale, 3),
                "empiricalNoiseNjy": noise,
                "detectionThresholdNjy": threshold,
                "significance": flux / noise,
                "opticalCounterpart": bool(detected),
                "limitingMagAB": limiting_mag,
                "xrayDetectionLikelihood": likelihood,
            })

    detections = [item for item in matches if item["opticalCounterpart"]]
    blanks = [item for item in matches if not item["opticalCounterpart"]]
    summary = {
        "schemaVersion": "layers-xray-counterparts-v1",
        "generatedAt": utc_now(),
        "comparisonKind": "counterpart association",
        "gatedByBandpassTransfer": False,
        "catalog": ERASS1,
        "positionSource": (
            "eRASS1 catalogued detection positions. The cached eROSITA records in this project carry "
            "the position an upper limit was requested at, which is the tract centre, and matching "
            "against that would manufacture an association for anything near a tract centre."
        ),
        "method": {
            "matchRadiusArcsec": MATCH_RADIUS_ARCSEC,
            "apertureArcsec": APERTURE_ARCSEC,
            "detectionSigma": DETECTION_SIGMA,
            "noise": (
                "Scatter of the identical aperture sum at random blank positions in the same image, "
                "not the per-pixel variance planes, which understate the true uncertainty here."
            ),
        },
        "counts": {
            "regionsQueried": covered,
            "regionsWithAnyCataloguedSource": with_rows,
            "cataloguedSourcesInCone": catalogued,
            "regionsSkipped": len(skipped),
            "xraySourcesInsideRubinPixels": len(matches),
            "withOpticalCounterpart": len(detections),
            "withoutOpticalCounterpart": len(blanks),
        },
        "limitingMagnitude": {
            "median": float(np.median([item["limitingMagAB"] for item in matches if item["limitingMagAB"]]))
            if matches
            else None,
            "note": "5 sigma in a 2 arcsec aperture, AB, from the measured blank-aperture scatter.",
        },
        "caveats": [
            "An X-ray source with no optical counterpart is interesting and is also what a spurious "
            "X-ray detection looks like; the eRASS1 detection likelihood is carried on every row so "
            "that can be checked rather than assumed.",
            "A counterpart here means flux above threshold at the position, not an identification. No "
            "redshift, colour, or morphology test has been applied.",
            "Footprint overlap is not detection. 55 of the selected tracts sit inside the eRASS1 "
            "sky, but only a handful hold a catalogued X-ray source within the search cone, so the "
            "sample size here is set by where eROSITA actually detected something rather than by "
            "where it looked.",
            "Chance coincidence is not yet quantified. With a 10 arcsec radius in a crowded field a "
            "random source can fall inside the error circle, so a per-field random-position rate must "
            "be measured before any association is called secure.",
        ],
        "skipped": skipped,
        "sources": matches,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"queried {covered} regions, {len(skipped)} skipped")
    print(f"regions with any catalogued eRASS1 source within the cone: {with_rows}")
    print(f"  catalogued sources in those cones: {catalogued}")
    print(f"X-ray sources inside Rubin pixels: {len(matches)}")
    print(f"  with an optical counterpart:    {len(detections)}")
    print(f"  without, to a stated depth:     {len(blanks)}")
    if summary["limitingMagnitude"]["median"]:
        print(f"median limiting magnitude: {summary['limitingMagnitude']['median']:.2f} AB")


if __name__ == "__main__":
    main()
