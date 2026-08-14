#!/usr/bin/env python3
"""Build small, auditable real-data examples for underrepresented layer families.

The products here are evidence records, not calibrated inter-survey differences.
Every remote response is retained under pipeline/results/family-examples and the
browser-facing summaries are written under public/data/layers/family-examples.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import requests
from astropy.table import Table
from sparcl.client import SparclClient


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "pipeline" / "results" / "family-examples"
PUBLIC = ROOT / "public" / "data" / "layers" / "family-examples"
PREVIEWS = ROOT / "public" / "layer-previews" / "family-examples"
SELECTED_PATH = ROOT / "public" / "data" / "coverage" / "selected-regions.json"
HEASARC_TAP = "https://heasarc.gsfc.nasa.gov/xamin/vo/tap/sync"
VIZIER_TAP = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonable(value: Any) -> Any:
    if value is np.ma.masked:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def table_rows(table: Table) -> list[dict[str, Any]]:
    return [
        {name: jsonable(row[name]) for name in table.colnames}
        for row in table
    ]


def artifact(path: Path, *, role: str, product_type: str) -> dict[str, Any]:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "role": role,
        "productType": product_type,
    }


def tap_query(endpoint: str, query: str, destination: Path) -> Table:
    response = requests.get(
        endpoint,
        params={"REQUEST": "doQuery", "LANG": "ADQL", "QUERY": query},
        timeout=90,
    )
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)
    table = Table.read(destination, format="votable")
    return table


def separation_arcmin(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    p1, p2 = math.radians(dec1), math.radians(dec2)
    dra = math.radians(ra1 - ra2)
    cosine = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dra)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine)))) * 60.0


def get_selected(tract: int) -> dict[str, Any]:
    data = json.loads(SELECTED_PATH.read_text(encoding="utf-8"))
    return next(region for region in data["regions"] if region["tract"] == tract)


def build_erosita() -> dict[str, Any]:
    region = get_selected(9813)
    ra, dec = region["center"]
    radius = 0.5
    query = f"""
SELECT name, detuid, ra, dec, error_radius, source_extent,
       extent_likelihood, b0_detect_likelihood, b1_flux,
       b1_flux_error, b1_exposure, optical_flag
FROM erass1main
WHERE 1=CONTAINS(POINT('ICRS', ra, dec),
                 CIRCLE('ICRS', {ra}, {dec}, {radius}))
ORDER BY b0_detect_likelihood DESC
""".strip()
    raw = RESULTS / "xray" / "erass1-tract-9813.vot"
    table = tap_query(HEASARC_TAP, query, raw)
    csv_path = raw.with_suffix(".csv")
    table.write(csv_path, format="ascii.csv", overwrite=True)
    rows = table_rows(table)
    for row in rows:
        row["separationArcmin"] = round(
            separation_arcmin(ra, dec, float(row["ra"]), float(row["dec"])), 6
        )
    return {
        "schemaVersion": 1,
        "generatedAt": now(),
        "family": "high-energy",
        "survey": "eROSITA eRASS1 main source catalog",
        "release": "eRASS1 / DR1 catalog ingestion",
        "productType": "catalog",
        "selectedRubinRegion": region,
        "query": {"centerDeg": [ra, dec], "radiusDeg": radius, "adql": query},
        "recordCount": len(table),
        "spatialMetadata": {
            "coordinateFrame": "ICRS",
            "coordinateEpoch": "J2000",
            "wcs": "not applicable to a source catalog",
        },
        "units": {
            "ra": "deg",
            "dec": "deg",
            "error_radius": "arcsec",
            "source_extent": "arcsec",
            "b1_flux": "erg s-1 cm-2 (0.2-0.6 keV band 1)",
            "b1_exposure": "s",
        },
        "records": rows,
        "provenance": {
            "service": HEASARC_TAP,
            "catalogDocumentation": "https://heasarc.gsfc.nasa.gov/W3Browse/catalog/erass1main.html",
            "archiveDocumentation": "https://heasarc.gsfc.nasa.gov/docs/srg/erosita/archive/",
            "table": "erass1main",
        },
        "artifacts": [
            artifact(raw, role="unaltered TAP response", product_type="VOTable catalog"),
            artifact(csv_path, role="portable tabular copy", product_type="CSV catalog"),
        ],
        "interpretation": {
            "status": "real catalog detections",
            "statement": "These are eRASS1 X-ray catalog sources within the selected Rubin tract center cone; they are not X-ray image pixels.",
            "comparisonClaim": None,
            "requiredBeforeDifferenceAnalysis": [
                "retrieve event/image, exposure, background, and sensitivity products",
                "propagate positional uncertainties and masks",
                "model Rubin source-selection and bandpass differences",
            ],
        },
    }


def build_hipass() -> dict[str, Any]:
    region = get_selected(5061)
    ra, dec = region["center"]
    radius = 1.0
    query = f"""
SELECT HIPASS, RAJ2000, DEJ2000, RVmom, W50max, Speak, Sint,
       RMS, RMScube, Qual, SimbadName
FROM \"VIII/73/hicat\"
WHERE 1=CONTAINS(POINT('ICRS', RAJ2000, DEJ2000),
                 CIRCLE('ICRS', {ra}, {dec}, {radius}))
""".strip()
    raw = RESULTS / "neutral-gas" / "hipass-tract-5061.vot"
    table = tap_query(VIZIER_TAP, query, raw)
    csv_path = raw.with_suffix(".csv")
    table.write(csv_path, format="ascii.csv", overwrite=True)
    rows = table_rows(table)
    for row in rows:
        row["separationArcmin"] = round(
            separation_arcmin(
                ra, dec, float(row["RAJ2000"]), float(row["DEJ2000"])
            ),
            6,
        )
    return {
        "schemaVersion": 1,
        "generatedAt": now(),
        "family": "neutral-gas",
        "survey": "H I Parkes All Sky Survey Catalogue (HICAT)",
        "release": "VIII/73 (Meyer et al. 2004)",
        "productType": "catalog",
        "selectedRubinRegion": region,
        "query": {"centerDeg": [ra, dec], "radiusDeg": radius, "adql": query},
        "recordCount": len(table),
        "spatialMetadata": {
            "coordinateFrame": "ICRS",
            "coordinateEpoch": "J2000",
            "wcs": "not applicable to a source catalog",
        },
        "units": {
            "RAJ2000": "deg",
            "DEJ2000": "deg",
            "RVmom": "km s-1",
            "W50max": "km s-1",
            "Speak": "Jy",
            "Sint": "Jy km s-1",
            "RMS": "Jy",
        },
        "records": rows,
        "provenance": {
            "service": VIZIER_TAP,
            "catalog": "ivo://CDS.VizieR/VIII/73",
            "catalogReadme": "https://cdsarc.cds.unistra.fr/ftp/cats/VIII/73/ReadMe",
            "table": "VIII/73/hicat",
        },
        "artifacts": [
            artifact(raw, role="unaltered TAP response", product_type="VOTable catalog"),
            artifact(csv_path, role="portable tabular copy", product_type="CSV catalog"),
        ],
        "interpretation": {
            "status": "real H I catalog detection",
            "statement": "The row records a 21-cm line detection and integrated line flux; it is not a spatial H I moment map or spectral cube.",
            "comparisonClaim": None,
            "requiredBeforeDifferenceAnalysis": [
                "retrieve the HIPASS spectrum or cube subset",
                "match the 15.5 arcmin Parkes beam to the optical analysis",
                "separate confused H I emitters and propagate baseline uncertainty",
            ],
        },
    }


def build_desi() -> dict[str, Any]:
    region = get_selected(9813)
    ra, dec = region["center"]
    client = SparclClient(announcement=False, connect_timeout=20)
    constraints = {"ra": [ra - 0.1, ra + 0.1], "dec": [dec - 0.1, dec + 0.1]}
    fields = [
        "sparcl_id", "specid", "targetid", "ra", "dec", "redshift",
        "redshift_err", "redshift_warning", "spectype", "data_release",
        "survey", "instrument",
    ]
    found = client.find(outfields=fields, constraints=constraints, limit=20, fmt="pandas")
    galaxies = found[(found["spectype"] == "GALAXY") & (found["redshift_warning"] == 0)]
    if galaxies.empty:
        raise RuntimeError("SPARCL returned no warning-free DESI galaxy spectrum")
    chosen = galaxies.iloc[0].to_dict()
    spectrum = client.retrieve(
        [chosen["sparcl_id"]],
        include=fields + ["wavelength", "flux", "ivar", "mask", "model"],
    ).records[0]
    out_dir = RESULTS / "spectroscopy"
    out_dir.mkdir(parents=True, exist_ok=True)
    fits_path = out_dir / "desi-edr-tract-9813-spectrum.fits"
    spectral_table = Table(
        {
            "wavelength": np.asarray(spectrum["wavelength"], dtype=np.float64),
            "flux": np.asarray(spectrum["flux"], dtype=np.float32),
            "ivar": np.asarray(spectrum["ivar"], dtype=np.float32),
            "mask": np.asarray(spectrum["mask"], dtype=np.int32),
            "model": np.asarray(spectrum["model"], dtype=np.float32),
        }
    )
    spectral_table["wavelength"].unit = "Angstrom"
    spectral_table["flux"].unit = "1e-17 erg / (s cm2 Angstrom)"
    spectral_table["ivar"].description = "inverse variance of flux"
    spectral_table.meta.update(
        {
            "SPARCLID": spectrum["sparcl_id"],
            "SPECID": str(spectrum["specid"]),
            "TARGETID": str(spectrum["targetid"]),
            "RA": float(spectrum["ra"]),
            "DEC": float(spectrum["dec"]),
            "REDSHIFT": float(spectrum["redshift"]),
            "DATAREL": spectrum["data_release"],
        }
    )
    spectral_table.write(fits_path, overwrite=True)
    preview = PREVIEWS / "desi-edr-tract-9813-spectrum.png"
    preview.parent.mkdir(parents=True, exist_ok=True)
    wave = np.asarray(spectrum["wavelength"])
    flux = np.asarray(spectrum["flux"])
    model = np.asarray(spectrum["model"])
    fig, ax = plt.subplots(figsize=(10, 3.8), dpi=160)
    ax.plot(wave, flux, color="#9ba8c7", lw=0.45, alpha=0.8, label="DESI flux")
    ax.plot(wave, model, color="#ff8b69", lw=0.9, label="Redrock model")
    ax.set(xlabel="Observed wavelength (Å)", ylabel=r"Flux ($10^{-17}$ erg s$^{-1}$ cm$^{-2}$ Å$^{-1}$)")
    ax.set_title(f"DESI {spectrum['data_release']} · z={spectrum['redshift']:.5f} · Rubin tract 9813")
    ax.legend(frameon=False, ncol=2)
    ax.grid(alpha=0.15)
    fig.tight_layout()
    fig.savefig(preview)
    plt.close(fig)
    sample_step = max(1, len(wave) // 600)
    return {
        "schemaVersion": 1,
        "generatedAt": now(),
        "family": "spectroscopy",
        "survey": "Dark Energy Spectroscopic Instrument",
        "release": spectrum["data_release"],
        "productType": "spectrum",
        "selectedRubinRegion": region,
        "discovery": {"service": "SPARCL", "constraints": constraints, "returnedRecords": len(found)},
        "spatialMetadata": {
            "coordinateFrame": "ICRS",
            "coordinateEpoch": "J2000",
            "wcs": "not applicable to a one-dimensional spectrum",
        },
        "spectrum": {
            "sparclId": spectrum["sparcl_id"],
            "specId": str(spectrum["specid"]),
            "targetId": str(spectrum["targetid"]),
            "raDeg": float(spectrum["ra"]),
            "decDeg": float(spectrum["dec"]),
            "separationFromTractCenterArcmin": round(
                separation_arcmin(ra, dec, float(spectrum["ra"]), float(spectrum["dec"])), 6
            ),
            "spectype": spectrum["spectype"],
            "redshift": float(spectrum["redshift"]),
            "redshiftError": float(spectrum["redshift_err"]),
            "redshiftWarning": int(spectrum["redshift_warning"]),
            "samples": len(wave),
            "wavelengthRangeAngstrom": [float(np.min(wave)), float(np.max(wave))],
            "units": {"wavelength": "Angstrom", "flux": "1e-17 erg s-1 cm-2 Angstrom-1"},
            "previewSample": {
                "wavelength": wave[::sample_step].astype(float).tolist(),
                "flux": flux[::sample_step].astype(float).tolist(),
                "model": model[::sample_step].astype(float).tolist(),
            },
        },
        "provenance": {
            "service": "https://astrosparcl.datalab.noirlab.edu",
            "desiDataAccess": "https://data.desi.lbl.gov/doc/access/",
            "releaseDocumentation": "https://data.desi.lbl.gov/doc/releases/edr/",
        },
        "artifacts": [
            artifact(fits_path, role="retrieved spectral arrays and metadata", product_type="FITS binary table"),
            artifact(preview, role="display rendering of the spectrum", product_type="PNG plot"),
        ],
        "interpretation": {
            "status": "real calibrated spectrum and pipeline redshift",
            "statement": "The spectrum supplies line/continuum and redshift information that an optical image alone does not encode.",
            "comparisonClaim": None,
            "requiredBeforeRubinAssociation": [
                "positionally crossmatch to a Rubin source catalog",
                "inspect blend/deblender flags and aperture association",
                "propagate spectrum and Rubin photometry uncertainties",
            ],
        },
    }


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    products = {
        "spectroscopy": build_desi(),
        "xray": build_erosita(),
        "neutralGas": build_hipass(),
        "lensing": {
            "schemaVersion": 1,
            "generatedAt": now(),
            "family": "lensing",
            "productType": None,
            "selectedRubinRegion": get_selected(9813),
            "status": "unresolved",
            "reason": "No bounded authoritative shear or convergence product for a selected Rubin DP2 region was validated in this run.",
            "notSubstitutedWith": "The existing Abell 2744 model map has no Rubin DP2 coverage and therefore is not evidence for an overlapping layer.",
            "nextAuthoritativeRoutes": [
                "DES/KiDS/HSC public shear catalogs with documented masks and calibration",
                "Planck/ACT/SPT lensing convergence maps with a verified bounded cutout procedure",
            ],
        },
    }
    for name, record in products.items():
        (PUBLIC / f"{name}.json").write_text(
            json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    summary = {
        "schemaVersion": 1,
        "generatedAt": now(),
        "status": "partial-success",
        "realProducts": ["spectroscopy", "xray", "neutralGas"],
        "unresolved": ["lensing"],
        "records": {
            "spectroscopy": "spectroscopy.json",
            "xray": "xray.json",
            "neutralGas": "neutralGas.json",
            "lensing": "lensing.json",
        },
        "guardrail": "Presence and overlap are demonstrated; no cross-survey physical difference is claimed.",
    }
    summary_path = PUBLIC / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    validation = {
        "generatedAt": now(),
        "passed": True,
        "checks": {
            "desiSpectrumHasSamples": products["spectroscopy"]["spectrum"]["samples"] > 1000,
            "desiRedshiftWarningIsZero": products["spectroscopy"]["spectrum"]["redshiftWarning"] == 0,
            "erositaCatalogHasRows": products["xray"]["recordCount"] > 0,
            "hipassCatalogHasRows": products["neutralGas"]["recordCount"] > 0,
            "allArtifactsHaveSha256": all(
                len(item["sha256"]) == 64
                for key in ("spectroscopy", "xray", "neutralGas")
                for item in products[key]["artifacts"]
            ),
            "lensingIsExplicitlyUnresolved": products["lensing"]["status"] == "unresolved",
            "noDifferenceClaim": all(
                products[key]["interpretation"]["comparisonClaim"] is None
                for key in ("spectroscopy", "xray", "neutralGas")
            ),
        },
    }
    validation["passed"] = all(validation["checks"].values())
    validation_path = RESULTS / "validation.json"
    validation_path.write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "validation": validation}, indent=2))


if __name__ == "__main__":
    main()
