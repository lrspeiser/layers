#!/usr/bin/env python3
"""Decide whether the Rubin flux deficit is an aperture effect or a zeropoint.

Two independent references agree that Rubin measures about 9% less flux than
they do in a fixed 1.5 arcsec aperture, and the field-to-field variation in that
scale is shared between the two pairings. Both findings point at the Rubin side,
but neither separates the two explanations that matter:

* **An aperture effect.** Rubin light sits outside the small aperture, because
  the PSF match left the wings unmatched. The measured scale should then climb
  toward 1 as the aperture grows, and the shape of that climb is the size of the
  residual wing mismatch.
* **A zeropoint difference.** The calibration differs by a constant. The scale
  should then be flat with radius, because a constant factor does not care how
  much of the source is enclosed.

This measures the ratio at a ladder of radii on the already reconciled,
PSF-matched, sky-subtracted planes and reports which of those two the curve
looks like. It is a difference of shape, not of value, so it survives not
knowing either survey's absolute calibration.

Two confounds decide whether the answer means anything, and both are handled
rather than mentioned:

* **Blending.** A larger aperture swallows neighbours. Neighbour flux enters
  both sides and pushes any ratio toward 1, which is exactly the signature the
  aperture hypothesis predicts. Only sources with no detected neighbour inside
  three times the largest aperture are used.
* **Sky.** A wrong background is a constant per pixel, so it grows as the
  aperture area and can manufacture a trend on its own. The local sky is
  re-measured in an annulus outside the largest aperture and subtracted per
  source, on each side independently.
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
from audit_layer_registration import centroid_sources, robust_sigma

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "public/data/layers/selected-regions/curve-of-growth.json"

RADII_ARCSEC = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
# The reference radius the reconciliation already reports, so the curve is
# anchored to the number the rest of the project quotes.
ANCHOR_ARCSEC = 1.5
ISOLATION_FACTOR = 3.0
SKY_ANNULUS_ARCSEC = (6.0, 9.0)
MATCH_TOLERANCE_PIXELS = 3.0
MIN_SOURCES_PER_FIELD = 8
MIN_FIELDS = 20


def parse_pairing(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=DIR")
    name, _, path = value.partition("=")
    return name.strip(), Path(path.strip())


def display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise.

    A bare .relative_to(ROOT) raises for any output written outside the repo,
    and it does so in the final print, after the work is done and the file is
    written -- an exit code that says the run failed when it succeeded.
    """
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def field_curve(path: Path) -> dict[str, Any] | None:
    with fits.open(path, memmap=False) as hdus:
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        reference = np.asarray(hdus["REFERENCE"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data).astype(bool)
        wcs = WCS(hdus["RUBIN"].header).celestial
    scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
    if not np.isfinite(scale) or scale <= 0:
        return None

    rubin_sources = centroid_sources(rubin, common, 0.0, scale)
    reference_sources = centroid_sources(reference, common, 0.0, scale)
    if not rubin_sources or not reference_sources:
        return None
    reference_positions = np.array([[item["x"], item["y"]] for item in reference_sources])
    rubin_positions = np.array([[item["x"], item["y"]] for item in rubin_sources])

    radii = np.array(RADII_ARCSEC) / scale
    isolation = ISOLATION_FACTOR * radii.max()
    inner, outer = np.array(SKY_ANNULUS_ARCSEC) / scale
    yy, xx = np.indices(rubin.shape)
    height, width = rubin.shape

    per_radius: dict[float, list[float]] = {value: [] for value in RADII_ARCSEC}
    used = 0
    for source in rubin_sources:
        x, y = source["x"], source["y"]
        # Every aperture and the sky annulus must sit fully inside the frame,
        # or the ratio at large radius is measured on a clipped source.
        if min(x, y) < outer + 1 or x > width - outer - 2 or y > height - outer - 2:
            continue
        separations = np.hypot(reference_positions[:, 0] - x, reference_positions[:, 1] - y)
        if not separations.size or separations.min() > MATCH_TOLERANCE_PIXELS:
            continue
        own = np.hypot(rubin_positions[:, 0] - x, rubin_positions[:, 1] - y)
        if np.sum(own < isolation) > 1:
            continue
        neighbours = np.sum(separations < isolation)
        if neighbours > 1:
            continue

        distance = np.hypot(xx - x, yy - y)
        annulus = (distance >= inner) & (distance < outer) & common
        if annulus.sum() < 40:
            continue
        rubin_sky = float(np.nanmedian(rubin[annulus]))
        reference_sky = float(np.nanmedian(reference[annulus]))
        if not np.isfinite(rubin_sky) or not np.isfinite(reference_sky):
            continue

        ok = True
        values: dict[float, float] = {}
        for arcsec, radius in zip(RADII_ARCSEC, radii):
            aperture = (distance <= radius) & common
            count = int(aperture.sum())
            if count < 4:
                ok = False
                break
            rubin_flux = float(np.nansum(rubin[aperture])) - rubin_sky * count
            reference_flux = float(np.nansum(reference[aperture])) - reference_sky * count
            if rubin_flux <= 0 or reference_flux <= 0:
                ok = False
                break
            values[arcsec] = rubin_flux / reference_flux
        if not ok:
            continue
        used += 1
        for arcsec, value in values.items():
            per_radius[arcsec].append(value)

    if used < MIN_SOURCES_PER_FIELD:
        return None
    curve = {}
    for arcsec, ratios in per_radius.items():
        logs = np.log10(np.asarray(ratios))
        keep = np.abs(logs - np.median(logs)) < 3.0 * max(robust_sigma(logs), 1e-6)
        kept = np.asarray(ratios)[keep] if keep.sum() >= MIN_SOURCES_PER_FIELD else np.asarray(ratios)
        curve[arcsec] = float(np.median(kept))
    return {"pixelScaleArcsec": scale, "isolatedSources": used, "curve": curve}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pairing",
        type=parse_pairing,
        action="append",
        metavar="NAME=DIR",
        help=(
            "Repeatable reconciled-product directory, NAME=DIR. Defaults to the Legacy, DES and "
            "Pan-STARRS sets. Each pairing is measured independently, because the whole value of "
            "the test is that separate references can disagree."
        ),
    )
    parser.add_argument("--limit", type=int, help="fields per pairing, for a quick pass")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    requested = args.pairing or [
        ("rubin-vs-legacy", ROOT / "pipeline/results/reconciled-regions-200"),
        ("rubin-vs-des", ROOT / "pipeline/results/reconciled-regions-des"),
        ("rubin-vs-ps1", ROOT / "pipeline/results/reconciled-regions-ps1"),
    ]

    pairings: dict[str, Any] = {}
    for name, root in requested:
        if not root.is_dir():
            continue
        paths = sorted(p for region in sorted(root.iterdir()) if region.is_dir() for p in region.glob("*.fits"))
        if args.limit:
            paths = paths[: args.limit]
        fields: list[dict[str, Any]] = []
        for path in paths:
            try:
                result = field_curve(path)
            except Exception:
                result = None
            if result is None:
                continue
            result["regionId"] = path.parent.name
            fields.append(result)
            print(
                f"[{name}] {result['regionId']}  n={result['isolatedSources']}  "
                f"1.5\"={result['curve'][1.5]:.4f}  5.0\"={result['curve'][5.0]:.4f}",
                flush=True,
            )
        if len(fields) < MIN_FIELDS:
            pairings[name] = {"fields": len(fields), "sufficient": False,
                              "note": f"below the {MIN_FIELDS}-field threshold; no verdict"}
            continue

        medians = {
            arcsec: float(np.median([field["curve"][arcsec] for field in fields]))
            for arcsec in RADII_ARCSEC
        }
        # Per field, how much the scale moves from the anchor radius to the
        # widest. A paired quantity, so it does not depend on the field-to-field
        # spread that the cross-check already showed is large.
        gains = np.array([
            field["curve"][max(RADII_ARCSEC)] / field["curve"][ANCHOR_ARCSEC] for field in fields
        ])
        rng = np.random.default_rng(20260814)
        draws = rng.integers(0, gains.size, size=(4000, gains.size))
        boot = np.median(gains[draws], axis=1)
        interval = [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))]
        median_gain = float(np.median(gains))
        flat = bool(interval[0] <= 1.0 <= interval[1])
        # A deficit of d at the anchor is fully explained by aperture only if the
        # gain recovers it: gain * anchor ~ 1.
        recovered = float((median_gain * medians[ANCHOR_ARCSEC] - medians[ANCHOR_ARCSEC])
                          / (1.0 - medians[ANCHOR_ARCSEC])) if medians[ANCHOR_ARCSEC] < 1 else None

        pairings[name] = {
            "fields": len(fields),
            "sufficient": True,
            "totalIsolatedSources": int(sum(field["isolatedSources"] for field in fields)),
            "medianScaleByRadiusArcsec": {str(k): v for k, v in medians.items()},
            "anchorRadiusArcsec": ANCHOR_ARCSEC,
            "widestRadiusArcsec": max(RADII_ARCSEC),
            "medianGainAnchorToWidest": median_gain,
            "gainBootstrap95Interval": interval,
            "curveIsFlat": flat,
            "deficitFractionRecovered": recovered,
            "verdict": (
                "zeropoint-like: the ratio does not change with aperture"
                if flat
                else (
                    "aperture-like: the ratio climbs with aperture, so Rubin flux sits outside "
                    "the small aperture"
                    if median_gain > 1
                    else "the ratio falls with aperture, which neither hypothesis predicts"
                )
            ),
        }

    measured = {k: v for k, v in pairings.items() if v.get("sufficient")}
    flat = {k for k, v in measured.items() if v["curveIsFlat"]}
    dissenting = sorted(set(measured) - flat)
    # Rubin is common to every pairing, so the same attribution logic applies to
    # a shape as to a value: a trend that appears in one pairing and not the
    # others belongs to that pairing's reference or to its PSF match, not to
    # Rubin. A majority is not a vote here, it is which side Rubin sits on.
    if len(measured) >= 2 and not dissenting:
        headline = (
            f"All {len(measured)} pairings are flat: the deficit is zeropoint-like, not an "
            "aperture effect"
        )
        attribution = (
            "Rubin is common to every pairing, so a shape they all share is Rubin's or the "
            "aperture method's."
        )
    elif len(flat) >= 2 and dissenting:
        headline = (
            f"{len(flat)} of {len(measured)} pairings are flat; {', '.join(dissenting)} dissents"
        )
        attribution = (
            f"The flat result stands for the pairings that show it: against those references the "
            f"deficit is zeropoint-like, not an aperture effect. Rubin is common to every pairing, "
            f"so a radial trend appearing only in {', '.join(dissenting)} belongs to that "
            "reference or to its PSF match rather than to Rubin, and that pairing cannot testify "
            "about shape until it is resolved."
        )
    elif measured:
        headline = (
            f"No two pairings agree on a shape ({len(measured)} measured)"
            if len(measured) > 1
            else f"One pairing measured, which cannot attribute a shape: {list(measured.values())[0]['verdict']}"
        )
        attribution = (
            "Attribution needs at least two pairings that agree, because Rubin being the shared "
            "term is the whole method."
        )
    else:
        headline = "No pairing reached the field threshold"
        attribution = None

    payload = {
        "schemaVersion": "layers-curve-of-growth-v1",
        "generatedAt": utc_now(),
        "question": (
            "Is the Rubin flux deficit an aperture effect left by incomplete PSF matching, or a "
            "zeropoint difference? The two predict different shapes for scale against radius."
        ),
        "method": {
            "radiiArcsec": list(RADII_ARCSEC),
            "planes": "already reconciled, PSF-matched, sky-subtracted RUBIN and REFERENCE planes",
            "isolation": (
                f"no detected neighbour within {ISOLATION_FACTOR}x the largest aperture, on either "
                "side; blending would push any ratio toward 1 and imitate the aperture signature"
            ),
            "skySubtraction": (
                f"local median in a {SKY_ANNULUS_ARCSEC[0]}-{SKY_ANNULUS_ARCSEC[1]} arcsec annulus, "
                "per source and per side; a sky error grows as the aperture area and could "
                "manufacture a trend"
            ),
            "significance": "bootstrap 95% interval on the per-field median gain",
        },
        "headline": headline,
        "attribution": attribution,
        "flatPairings": sorted(flat),
        "dissentingPairings": dissenting,
        "pairings": pairings,
        "caveats": [
            "This measures the shape of the ratio against radius, not either survey's absolute "
            "calibration. A flat curve rules out an aperture explanation; it does not say which "
            "survey's zeropoint is right.",
            "Sources are compact detections. The result does not transfer to extended emission.",
            "The planes are PSF-matched already, so a radius-dependent ratio is a statement about "
            "what the PSF match left behind, not about the delivered PSFs themselves.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"\n{headline}")
    for name, result in pairings.items():
        if not result.get("sufficient"):
            print(f"  {name}: {result.get('note')}")
            continue
        curve = result["medianScaleByRadiusArcsec"]
        print(f"  {name}: {result['fields']} fields, {result['totalIsolatedSources']} isolated sources")
        print("    " + "  ".join(f"{k}\"={float(v):.4f}" for k, v in curve.items()))
        print(
            f"    gain {result['medianGainAnchorToWidest']:.4f} "
            f"[{result['gainBootstrap95Interval'][0]:.4f}, {result['gainBootstrap95Interval'][1]:.4f}]"
            f" -> {result['verdict']}"
        )
    print(f"\nwrote {display_path(args.output)}")


if __name__ == "__main__":
    main()
