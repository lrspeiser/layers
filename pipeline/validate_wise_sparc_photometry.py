#!/usr/bin/env python3
"""Validate the automated WISE/SPARC aperture transfer against published controls.

The controls are a fixed, target-independent subset of the Duey et al. (2024)
WISE--Spitzer table spanning aperture size and axis ratio.  Publication is
allowed only when at least six controls have precise published photometry and
the unchanged automated reduction reproduces both the W1 aperture magnitude
and the SPARC-profile integral within predeclared ensemble tolerances.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from audit_wise_sparc_transfer import (
    canonical_name,
    ellipse_coordinates,
    integrate_sparc,
    load_geometry,
    measure_profile,
    robust_sigma,
    sha256,
    sky_models,
)


CONTROL_IDS = (
    "ngc1705", "ugc02023", "ugc05721", "ugc05918", "ugc06446",
    "ugc06614", "ugc07690", "ugc09037", "ugc11820",
)
MAX_PUBLISHED_UNCERTAINTY_MAG = 0.10
MAX_MEASURED_UNCERTAINTY_MAG = 0.15
MIN_QUALIFIED_CONTROLS = 6
THRESHOLDS = {
    "maximumAbsoluteWiseMedianBiasMag": 0.05,
    "maximumWiseRobustScatterMag": 0.10,
    "maximumWiseRmsResidualMag": 0.10,
    "maximumAbsoluteSparcMedianBiasMag": 0.10,
    "maximumSparcRobustScatterMag": 0.15,
    "maximumSparcRmsResidualMag": 0.20,
}


def load_published_rows(path: Path) -> dict[str, dict]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    rows = list(csv.DictReader([lines[0], *lines[3:]], delimiter="\t"))
    return {canonical_name(row["Name"]): row for row in rows}


def ensemble(values: list[float]) -> dict:
    array = np.asarray(values, dtype=np.float64)
    return {
        "medianBiasMag": float(np.median(array)),
        "robustScatterMag": robust_sigma(array),
        "rmsResidualMag": float(np.sqrt(np.mean(array**2))),
        "central68PercentRangeMag": [float(value) for value in np.quantile(array, [0.16, 0.84])],
    }


def stable_created_at(path: Path) -> str:
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8")).get("createdAt")
        if existing:
            return existing
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--wise-root", type=Path, default=root / "pipeline/output/wise-validation-controls")
    parser.add_argument("--profiles", type=Path, default=root / "public/data/sparc-profiles")
    parser.add_argument("--coordinates", type=Path, default=root / "pipeline/cache/sparc/simbad-sparc-paper-objects.csv")
    parser.add_argument("--cohort", type=Path, default=root / "pipeline/cache/wise-spitzer-photometry-table1.tsv")
    parser.add_argument("--output", type=Path, default=root / "pipeline/output/wise-sparc-photometry-validation.json")
    args = parser.parse_args()

    geometry = load_geometry(args.coordinates)
    published = load_published_rows(args.cohort)
    controls = []
    wise_residuals: list[float] = []
    sparc_residuals: list[float] = []

    for target_id in CONTROL_IDS:
        profile_path = args.profiles / f"{target_id}.json"
        product_path = args.wise_root / target_id / "allwise_w1.fits"
        profile_record = json.loads(profile_path.read_text(encoding="utf-8"))["target"]
        key = canonical_name(profile_record["sparcId"])
        row = published[key]
        target_geometry = geometry[key]
        aperture_radius = float(row["Rad"])
        published_wise_unc = float(row["e_W1mag"])
        published_sparc_unc = float(row["e_IRACmag"])

        with fits.open(product_path, memmap=False) as hdus:
            image = np.asarray(hdus["IMAGE"].data, dtype=np.float64)
            variance = np.asarray(hdus["VARIANCE"].data, dtype=np.float64)
            valid = np.asarray(hdus["VALID_MASK"].data, dtype=bool)
            wcs = WCS(hdus["IMAGE"].header)
        pixel_scale = math.sqrt(abs(float(np.linalg.det(wcs.pixel_scale_matrix)))) * 3600.0
        radius, _, _ = ellipse_coordinates(image.shape, wcs, target_geometry)
        sky = sky_models(image, valid, radius, aperture_radius)
        measured = measure_profile(
            image, variance, valid, radius, profile_record["surfaceBrightness"],
            aperture_radius, pixel_scale**2, sky,
        )
        sparc = integrate_sparc(
            profile_record["surfaceBrightness"], aperture_radius,
            target_geometry["axisRatio"], seed=sum(map(ord, target_id)),
        )
        wise_residual = measured["apertureMagnitudeVega"] - float(row["W1mag"])
        sparc_residual = sparc["apertureMagnitudeVega"] - float(row["IRACmag"])
        qualification = {
            "minimum30ArcsecAperture": aperture_radius >= 30.0,
            "publishedW1Uncertainty": published_wise_unc <= MAX_PUBLISHED_UNCERTAINTY_MAG,
            "publishedSpitzerUncertainty": published_sparc_unc <= MAX_PUBLISHED_UNCERTAINTY_MAG,
            "measuredW1Uncertainty": measured["apertureMagnitudeUncertaintyMag"] <= MAX_MEASURED_UNCERTAINTY_MAG,
            "cataloguedGeometry": target_geometry["axisRatio"] is not None and target_geometry["positionAngleDegEastOfNorth"] is not None,
        }
        qualified = all(qualification.values())
        if qualified:
            wise_residuals.append(wise_residual)
            sparc_residuals.append(sparc_residual)
        controls.append({
            "targetId": target_id,
            "sparcId": profile_record["sparcId"],
            "qualified": qualified,
            "qualification": qualification,
            "apertureRadiusArcsec": aperture_radius,
            "axisRatio": target_geometry["axisRatio"],
            "publishedWiseW1MagnitudeVega": float(row["W1mag"]),
            "measuredWiseW1MagnitudeVega": measured["apertureMagnitudeVega"],
            "measuredWiseW1UncertaintyMag": measured["apertureMagnitudeUncertaintyMag"],
            "wiseResidualMag": wise_residual,
            "publishedSpitzer36MagnitudeVega": float(row["IRACmag"]),
            "integratedSparc36MagnitudeVega": sparc["apertureMagnitudeVega"],
            "sparcIntegrationResidualMag": sparc_residual,
            "retainedSkyBoxes": sky["models"]["plane"]["retainedBoxes"],
            "wiseProductSha256": sha256(product_path),
            "sparcProfileSha256": sha256(profile_path),
        })

    wise = ensemble(wise_residuals)
    sparc = ensemble(sparc_residuals)
    gates = {
        "minimumQualifiedControls": len(wise_residuals) >= MIN_QUALIFIED_CONTROLS,
        "wiseMedianBias": abs(wise["medianBiasMag"]) <= THRESHOLDS["maximumAbsoluteWiseMedianBiasMag"],
        "wiseRobustScatter": wise["robustScatterMag"] <= THRESHOLDS["maximumWiseRobustScatterMag"],
        "wiseRmsResidual": wise["rmsResidualMag"] <= THRESHOLDS["maximumWiseRmsResidualMag"],
        "sparcMedianBias": abs(sparc["medianBiasMag"]) <= THRESHOLDS["maximumAbsoluteSparcMedianBiasMag"],
        "sparcRobustScatter": sparc["robustScatterMag"] <= THRESHOLDS["maximumSparcRobustScatterMag"],
        "sparcRmsResidual": sparc["rmsResidualMag"] <= THRESHOLDS["maximumSparcRmsResidualMag"],
    }
    result = {
        "schemaVersion": 1,
        "product": "Layers WISE/SPARC external photometry validation",
        "createdAt": stable_created_at(args.output),
        "status": "pass" if all(gates.values()) else "qa-failed",
        "method": "Blind reproduction of published Duey et al. W1 aperture magnitudes and SPARC 3.6-micron profile integrals at the published aperture radius.",
        "selection": {
            "fixedControlIds": list(CONTROL_IDS),
            "qualifiedControls": len(wise_residuals),
            "minimumQualifiedControls": MIN_QUALIFIED_CONTROLS,
            "maximumPublishedUncertaintyMag": MAX_PUBLISHED_UNCERTAINTY_MAG,
            "maximumMeasuredUncertaintyMag": MAX_MEASURED_UNCERTAINTY_MAG,
        },
        "wiseW1": wise,
        "sparc36ProfileIntegration": sparc,
        "combinedColorSystematicMag": math.hypot(wise["rmsResidualMag"], sparc["rmsResidualMag"]),
        "thresholds": THRESHOLDS,
        "gates": gates,
        "controls": controls,
        "provenance": {
            "publishedTableSha256": sha256(args.cohort),
            "publishedTable": "https://cdsarc.cds.unistra.fr/viz-bin/ReadMe/J/AJ/168/19?format=html&tex=true",
            "methodPaper": "https://arxiv.org/abs/2404.02339",
        },
        "limitations": [
            "The validation controls the aperture zeropoint and profile integration, not every possible galaxy morphology.",
            "The empirical RMS values are propagated as systematics rather than removed as fitted corrections.",
            "Controls with published uncertainty above 0.10 mag remain reported but do not set the publication gate.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"WISE/SPARC external validation {result['status']}: {len(wise_residuals)}/{len(CONTROL_IDS)} qualified; "
        f"W1 RMS={wise['rmsResidualMag']:.3f} mag; SPARC-integral RMS={sparc['rmsResidualMag']:.3f} mag"
    )
    if result["status"] != "pass":
        raise SystemExit("Failed gates: " + ", ".join(name for name, passed in gates.items() if not passed))


if __name__ == "__main__":
    main()
