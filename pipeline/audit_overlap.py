#!/usr/bin/env python3
"""Audit SPARC profile, public Spitzer image, and Rubin EDP2 overlap.

The audit reports availability and spatial support. It does not call an absent
Rubin image a comparison and does not turn coverage into a scientific result.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


def read_profile(path: Path):
    points = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        columns = line.split()
        if len(columns) < 4:
            continue
        points.append({
            "radiusArcsec": float(columns[0]),
            "surfaceBrightnessMagArcsec2": float(columns[1]),
            "accepted": int(columns[2]) == 1,
            "uncertaintyMag": float(columns[3]),
        })
    return points


def annulus_coverage(path: Path, ra_deg: float, dec_deg: float, radius_arcsec: float):
    with fits.open(path, memmap=True) as hdul:
        data = np.asarray(hdul[0].data)
        wcs = WCS(hdul[0].header)
        x0, y0 = wcs.world_to_pixel_values(ra_deg, dec_deg)
        pixel_scale = abs(float(hdul[0].header.get("CDELT1", -0.6 / 3600.0))) * 3600.0
        radius_px = radius_arcsec / pixel_scale
        yy, xx = np.ogrid[: data.shape[0], : data.shape[1]]
        rr = np.hypot(xx - x0, yy - y0)
        half_width = max(3.0, 6.0 / pixel_scale)
        annulus = (rr >= radius_px - half_width) & (rr <= radius_px + half_width)
        return float(np.isfinite(data[annulus]).sum() / max(annulus.sum(), 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=Path, default=Path(__file__).with_name("targets.csv"))
    parser.add_argument("--legacy-cache", type=Path, default=Path(__file__).with_name("cache"))
    parser.add_argument("--edp2-summary", type=Path, default=Path(__file__).with_name("output") / "coverage-summary.json")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("results") / "public-legacy-overlap.json")
    args = parser.parse_args()

    legacy_manifest = json.loads((args.legacy_cache / "public-legacy-manifest.json").read_text(encoding="utf-8"))
    edp2 = json.loads(args.edp2_summary.read_text(encoding="utf-8")) if args.edp2_summary.exists() else {}
    with args.targets.open(newline="", encoding="utf-8") as handle:
        targets = list(csv.DictReader(handle))

    audit = {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "meaning": "Availability and spatial-overlap audit; not a Rubin-versus-SPARC measurement",
        "rubinCoverageActuallyQueried": bool(edp2),
        "targets": {},
    }

    for target in targets:
        slug = target["slug"]
        ra = float(target["ra_deg"])
        dec = float(target["dec_deg"])
        field_width = float(target["field_width_arcmin"])
        target_dir = args.legacy_cache / "targets" / slug
        profile = read_profile(target_dir / "sparc_surface_brightness.sfb")
        accepted = [point for point in profile if point["accepted"]]
        max_radius = max(point["radiusArcsec"] for point in accepted)
        legacy = legacy_manifest["targets"][slug]
        legacy_science = target_dir / "spitzer_irac1_science.fits"
        rubin_bands = []
        if slug in edp2:
            rubin_bands = sorted(
                band for band, state in edp2[slug].get("bands", {}).items()
                if state.get("coverage") == "covered"
            )

        record = {
            "sparcId": target["sparc_id"],
            "sparcProfile": {
                "available": True,
                "acceptedPoints": len(accepted),
                "maxAcceptedRadiusArcsec": max_radius,
                "fitsInsideDeclaredField": max_radius <= field_width * 30.0,
            },
            "spitzerSeip": {
                "coverage": legacy["seipCoverage"],
                "scienceFiniteFraction": legacy.get("coverageFraction", {}).get("science"),
                "profileEdgeAnnulusFiniteFraction": annulus_coverage(legacy_science, ra, dec, max_radius) if legacy_science.exists() else None,
                "sourceProducts": legacy["seipProducts"],
            },
            "rubinEdp2": {
                "coverageQueried": slug in edp2,
                "coveredBands": rubin_bands,
            },
        }
        if legacy["seipCoverage"] != "covered":
            record["analysisState"] = "legacy-image-not-covered"
        elif slug not in edp2:
            record["analysisState"] = "awaiting-authenticated-rubin-coverage-query"
        elif not rubin_bands:
            record["analysisState"] = "rubin-not-covered"
        else:
            record["analysisState"] = "ready-for-registration-and-psf-sky-matching"
        audit["targets"][slug] = record

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
    for slug, record in audit["targets"].items():
        print(
            f"{slug:16} {record['analysisState']:48} "
            f"SPARC rmax={record['sparcProfile']['maxAcceptedRadiusArcsec']:.1f}\" "
            f"SEIP edge={record['spitzerSeip']['profileEdgeAnnulusFiniteFraction']}"
        )


if __name__ == "__main__":
    main()
