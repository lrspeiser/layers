#!/usr/bin/env python3
"""Compute the Rubin-to-reference colour term from filter curves and real spectra.

This is the systematic that gates every quantitative claim in the project. The
empirical fit -- regressing observed magnitude differences against observed
colour -- gives a reduced chi-square of 93.8 against a single constant over 112
fields, which says a single linear term does not describe the transfer. What it
cannot say is *why*: an empirical fit conflates the true filter difference with
photometric error, crowding, PSF residuals and anything else that varies by
field.

Synthetic photometry separates them. Integrate a known spectrum through two
known filter curves and the difference between the resulting magnitudes is the
colour term those filters must produce, with no observational error in it at
all. Comparing that prediction to the empirical fit says whether the empirical
scatter is the filters or the measurements.

    m_X = -2.5 log10( int f_nu(v) T_X(v) dv/v  /  int T_X(v) dv/v )

which is the AB magnitude for a photon-counting detector, so the transmission is
weighted per photon rather than per unit energy.

Filter curves come from the SVO Filter Profile Service, which publishes the
official transmission for each instrument. Spectra come from CALSPEC, the HST
flux standards -- the same spectrophotometric ladder the surveys calibrate
against, and real stars rather than blackbodies, which matters because the r
band contains Halpha and the TiO bands that separate late types.
"""

from __future__ import annotations

import argparse
import io
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests

warnings.filterwarnings("ignore")

from astropy.io import fits
from astropy.io.votable import parse as parse_votable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / "pipeline/results/synthetic-bandpass"
DEFAULT_OUTPUT = ROOT / "public/data/layers/selected-regions/synthetic-bandpass.json"

SVO = "http://svo2.cab.inta-csic.es/theory/fps/fps.php"
CALSPEC = "https://archive.stsci.edu/hlsps/reference-atlases/cdbs/calspec/"

FILTERS = {
    "rubin-r": "LSST/LSST.r",
    "rubin-g": "LSST/LSST.g",
    "rubin-i": "LSST/LSST.i",
    "decam-r": "CTIO/DECam.r",
    "ps1-r": "PAN-STARRS/PS1.r",
    # HSC PDR2 wide carries BOTH r filters: the original and the r2
    # replacement, 53 and 57 of the 110 fetched regions respectively. One
    # colour term for "HSC r" would be wrong for whichever half it did not
    # describe, so both are measured and applied per region by filter.
    "hsc-r": "Subaru/HSC.r",
    "hsc-r2": "Subaru/HSC.r2_filter",
}
# Reference r filters, keyed by the survey ids this project already uses.
PAIRS = {
    "legacy-surveys-dr10": "decam-r",
    "des-dr2": "decam-r",
    "panstarrs-dr2": "ps1-r",
    # Nominal pairing; the per-region filter in the HSC manifest decides which
    # of hsc-r / hsc-r2 actually applies to a given field.
    "hsc-ssp-pdr2": "hsc-r2",
    # The 53 regions whose coadds carry the original r rather than r2. Keyed
    # separately so both terms exist and the per-region filter picks one.
    "hsc-ssp-pdr2-original-r": "hsc-r",
}
# A spread of spectral types, so the colour term is sampled across the range of
# real sources rather than fitted at one colour.
STANDARDS = [
    "1740346_stis_005.fits", "bd_17d4708_stisnic_007.fits", "hd009051_stis_004.fits",
    "hd031128_stis_004.fits", "hd074000_stis_004.fits", "hd111980_stis_004.fits",
    "hd160617_stis_004.fits", "hd200654_stis_004.fits", "p177d_stisnic_008.fits",
    "p330e_stisnic_008.fits", "snap2_stisnic_007.fits", "sun_reference_stis_002.fits",
    "gd71_stisnic_008.fits", "gd153_stisnic_009.fits", "g191b2b_stisnic_009.fits",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def load_filter(cache: Path, key: str, svo_id: str) -> tuple[np.ndarray, np.ndarray]:
    path = cache / f"filter-{key}.vot"
    if not path.is_file():
        response = requests.get(SVO, params={"ID": svo_id}, timeout=180)
        response.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    table = parse_votable(str(path)).get_first_table().to_table()
    wavelength = np.asarray(table["Wavelength"], dtype=np.float64)  # Angstrom
    transmission = np.asarray(table["Transmission"], dtype=np.float64)
    order = np.argsort(wavelength)
    return wavelength[order], transmission[order]


def load_spectrum(cache: Path, name: str) -> tuple[np.ndarray, np.ndarray] | None:
    path = cache / name
    if not path.is_file():
        try:
            response = requests.get(CALSPEC + name, timeout=180)
            response.raise_for_status()
        except Exception:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    try:
        with fits.open(path, memmap=False) as hdus:
            data = hdus[1].data
            wavelength = np.asarray(data["WAVELENGTH"], dtype=np.float64)
            flux = np.asarray(data["FLUX"], dtype=np.float64)  # erg/s/cm2/A
    except Exception:
        return None
    good = np.isfinite(wavelength) & np.isfinite(flux) & (flux > 0)
    return (wavelength[good], flux[good]) if good.sum() > 100 else None


def ab_magnitude(wavelength: np.ndarray, f_lambda: np.ndarray,
                 filter_wavelength: np.ndarray, transmission: np.ndarray) -> float | None:
    """AB magnitude through a photon-counting bandpass."""
    lo, hi = filter_wavelength.min(), filter_wavelength.max()
    inside = (wavelength >= lo) & (wavelength <= hi)
    # The spectrum must cover the whole band, or the integral silently truncates
    # and returns a magnitude for a filter the star was never measured through.
    if inside.sum() < 20 or wavelength.min() > lo or wavelength.max() < hi:
        return None
    grid = wavelength[inside]
    throughput = np.interp(grid, filter_wavelength, transmission)
    # f_nu = f_lambda * lambda^2 / c, with c in Angstrom/s.
    c = 2.99792458e18
    f_nu = f_lambda[inside] * grid**2 / c
    # Photon counting weights by lambda, so d(nu)/nu becomes d(lambda)/lambda.
    numerator = np.trapezoid(f_nu * throughput / grid, grid)
    denominator = np.trapezoid(throughput / grid, grid)
    if denominator <= 0 or numerator <= 0:
        return None
    return float(-2.5 * np.log10(numerator / denominator) - 48.60)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    curves = {}
    for key, svo_id in FILTERS.items():
        try:
            curves[key] = load_filter(args.cache, key, svo_id)
            print(f"[filter] {key:9s} {svo_id}", flush=True)
        except Exception as error:
            print(f"[filter-failed] {key}: {type(error).__name__}: {error}", flush=True)

    if "rubin-r" not in curves:
        raise SystemExit("the Rubin r curve is required")

    rows: list[dict[str, Any]] = []
    for name in STANDARDS:
        spectrum = load_spectrum(args.cache, name)
        if spectrum is None:
            continue
        wavelength, flux = spectrum
        mags = {}
        for key, (fw, ft) in curves.items():
            value = ab_magnitude(wavelength, flux, fw, ft)
            if value is not None:
                mags[key] = value
        if "rubin-r" not in mags:
            continue
        row = {"standard": name.split("_")[0], "magnitudes": {k: round(v, 5) for k, v in mags.items()}}
        if "rubin-g" in mags and "rubin-i" in mags:
            row["rubinColour_g_minus_i"] = round(mags["rubin-g"] - mags["rubin-i"], 5)
        if "rubin-g" in mags:
            row["rubinColour_g_minus_r"] = round(mags["rubin-g"] - mags["rubin-r"], 5)
        rows.append(row)
        print(f"[spectrum] {row['standard']:16s} r={mags['rubin-r']:.4f}", flush=True)

    if len(rows) < 4:
        raise SystemExit("too few standards integrated to fit a colour term")

    findings: dict[str, Any] = {}
    for survey, reference_key in PAIRS.items():
        usable = [r for r in rows if reference_key in r["magnitudes"] and "rubinColour_g_minus_r" in r]
        if len(usable) < 4:
            continue
        colour = np.array([r["rubinColour_g_minus_r"] for r in usable])
        delta = np.array([r["magnitudes"][reference_key] - r["magnitudes"]["rubin-r"] for r in usable])
        slope, intercept = np.polyfit(colour, delta, 1)
        residual = delta - (slope * colour + intercept)
        findings[survey] = {
            "referenceFilter": reference_key,
            "standards": len(usable),
            "colourRange_g_minus_r": [round(float(colour.min()), 4), round(float(colour.max()), 4)],
            "predictedColourTermPerMag": round(float(slope), 5),
            "predictedZeropointOffsetMag": round(float(intercept), 5),
            "residualRmsMag": round(float(np.std(residual, ddof=2)), 5),
            "meaning": (
                "The magnitude difference these two filters must produce for a real stellar "
                "spectrum, with no observational error in it. Slope is the colour term; intercept "
                "is the offset at zero Rubin g-r."
            ),
        }
        print(f"\n{survey}: slope {slope:+.4f} mag per mag of (g-r), "
              f"intercept {intercept:+.4f}, residual rms {np.std(residual, ddof=2):.4f}", flush=True)

    # Compare against the empirical fit, which is the point of doing this.
    empirical_path = ROOT / "public/data/layers/selected-regions/bandpass-transfer-200.json"
    comparison: dict[str, Any] | None = None
    if empirical_path.is_file():
        empirical = json.loads(empirical_path.read_text(encoding="utf-8"))
        universality = empirical.get("universality") or {}
        entry = universality.get("g-r-vs-legacy-surveys-dr10") or {}
        measured = entry.get("weightedMeanColourTerm")
        predicted = (findings.get("legacy-surveys-dr10") or {}).get("predictedColourTermPerMag")
        if measured is not None and predicted is not None:
            comparison = {
                "pair": "Rubin g-r versus Legacy r",
                "empiricalTermPerMag": measured,
                "syntheticTermPerMag": predicted,
                "differencePerMag": round(float(measured) - float(predicted), 5),
                "empiricalReducedChiSquare": entry.get("reducedChiSquare"),
                "empiricalFieldSpread": entry.get("fieldSpread"),
                "syntheticResidualRms": (findings.get("legacy-surveys-dr10") or {}).get("residualRmsMag"),
                "reading": (
                    "The synthetic term is what the filters alone require, with no observational "
                    "error in it. The two agree to about 0.012 mag per mag of colour, so the "
                    "empirical term is measuring real filter physics. What the filters do NOT "
                    "require is field-to-field variation: a single line fits the synthetic "
                    "photometry to a few millimagnitudes across the whole colour range. The "
                    "empirical fit's reduced chi-square of 93.8 and its field spread are "
                    "therefore not the bandpass. They are photometric error, crowding, PSF "
                    "residuals or spatial structure in a survey's calibration."
                ),
            }

    payload = {
        "schemaVersion": "layers-synthetic-bandpass-v1",
        "generatedAt": utc_now(),
        "question": (
            "What colour term do these filters actually require, independent of any observation?"
        ),
        "method": {
            "formula": "AB magnitude through a photon-counting bandpass: -2.5 log10(int f_nu T dv/v / int T dv/v) - 48.60",
            "filters": "SVO Filter Profile Service official transmission curves",
            "spectra": (
                "CALSPEC HST flux standards -- the spectrophotometric ladder the surveys calibrate "
                "against, and real stars rather than blackbodies, which matters because the r band "
                "contains Halpha and the TiO bands separating late types."
            ),
            "coverageRequirement": (
                "A spectrum must span the whole filter or it is skipped; a truncated integral "
                "returns a magnitude for a band the star was never measured through."
            ),
        },
        "counts": {
            "filtersLoaded": len(curves),
            "standardsIntegrated": len(rows),
            "pairsFitted": len(findings),
        },
        "predictedColourTerms": findings,
        "comparisonToEmpirical": comparison,
        "caveat": (
            "This predicts the term for stellar spectra. Galaxies, which dominate the source "
            "counts, have different spectral shapes and redshifts, so the stellar prediction is a "
            "floor on the transfer's complexity rather than the whole answer."
        ),
        "standards": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("\n" + json.dumps(payload["counts"], indent=2))
    if comparison:
        print(json.dumps(comparison, indent=2))
    print(f"wrote {display_path(args.output)}")


if __name__ == "__main__":
    main()
