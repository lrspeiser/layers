"""Test this project's Pan-STARRS flux chain against Pan-STARRS's own published magnitudes.

§28 found that Legacy and HSC independently put Rubin about 7-9% faint while
Pan-STARRS puts it 16% *bright*. PS1 is the outlier, and PS1 is also the only
reference here whose absolute flux chain has never been checked: it rests on

    m = -2.5 log10(DN) + 25 + 2.5 log10(EXPTIME)

taken from a header convention, and `reconcile_selected_regions.py` has always
labelled it `verified: false` for that reason. So either Rubin really is bright
against PS1 and faint against everyone else, or the chain is wrong.

That is directly testable and needs no credentials. PS1 DR2 publishes mean PSF
magnitudes through MAST. This measures compact sources in the same normalized
PS1 pixels the reconciliation used, converts them with the same chain, and
compares to the catalogue for the same stars. If the chain is right the
difference is zero; if it is wrong the offset is the error, in magnitudes.

This decides whether 188 reconciled pairs -- the largest single block in the
project -- carry a zeropoint error.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import urllib.parse
import urllib.request

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from photutils.background import Background2D, MedianBackground
from photutils.segmentation import SourceCatalog, detect_sources

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_COMPARISONS = ROOT / "pipeline/results/comparisons-ps1/manifest.json"
OUTPUT = ROOT / "public/data/layers/selected-regions/ps1-flux-chain-verification.json"

MAST = "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/mean.json"
AB_ZERO_POINT_NJY = 3.63078054770e12

MATCH_ARCSEC = 1.0
MIN_STARS = 8
DETECT_SIGMA = 5.0
# A zeropoint needs bright, isolated, unsaturated stars. Measured on everything a
# 5-sigma detection finds, the scatter is 0.5-0.7 mag: segment flux is not PSF
# magnitude, and the faint end is biased because only upward noise excursions get
# detected. That sample can identify which filter the pixels are, since the r-i
# difference is common to every star, but it cannot measure a zeropoint.
BRIGHT_MAG = 20.0
SATURATION_MAG = 15.0
ISOLATION_ARCSEC = 5.0


def catalogue(ra: float, dec: float, radius_deg: float = 0.04) -> list[dict]:
    """PS1 DR2 mean PSF magnitudes near a position. Public, no credentials."""
    query = urllib.parse.urlencode(
        {"ra": f"{ra:.6f}", "dec": f"{dec:.6f}", "radius": f"{radius_deg:.4f}",
         "nDetections.gt": 5, "pagesize": 4000}
    )
    request = urllib.request.Request(f"{MAST}?{query}",
                                     headers={"User-Agent": "Rubin-Light-Atlas/0.3"})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    names = [c["name"] if isinstance(c, dict) else str(c) for c in payload.get("info", [])]
    rows = payload.get("data", [])
    out = []
    for row in rows:
        record = dict(zip(names, row))
        mag = record.get("rMeanPSFMag")
        mag_i = record.get("iMeanPSFMag")
        ra_v, dec_v = record.get("raMean"), record.get("decMean")
        try:
            mag, ra_v, dec_v = float(mag), float(ra_v), float(dec_v)
            mag_i = float(mag_i)
        except (TypeError, ValueError):
            continue
        if not (5 < mag_i < 30):
            continue
        # -999 is PS1's null.
        if mag < 5 or mag > 30 or ra_v < -900 or dec_v < -900:
            continue
        out.append({"ra": ra_v, "dec": dec_v, "rMeanPSFMag": mag, "iMeanPSFMag": mag_i})
    return out


def measure(path: pathlib.Path, chain_scale: float) -> tuple[list[dict], WCS]:
    """Compact-source aperture flux in nJy from the normalized PS1 pixels."""
    with fits.open(path, memmap=False) as handle:
        image = np.asarray(handle["IMAGE"].data, dtype=float)
        wcs = WCS(handle["IMAGE"].header)
        valid = np.asarray(handle["VALID_MASK"].data, dtype=bool)
    working = np.where(valid, image, np.nan)
    background = Background2D(working, 64, filter_size=3,
                              bkg_estimator=MedianBackground(), mask=~valid)
    flat = working - background.background
    segments = detect_sources(flat, DETECT_SIGMA * background.background_rms, npixels=6)
    if segments is None:
        return [], wcs
    catalogue_obj = SourceCatalog(flat, segments, wcs=wcs)
    out = []
    for row in catalogue_obj:
        flux = float(row.segment_flux)
        sky = row.sky_centroid
        if flux <= 0 or sky is None:
            continue
        # Reject anything obviously extended; the chain is a point-source test.
        if float(row.area.value) > 200:
            continue
        out.append({"ra": float(sky.ra.deg), "dec": float(sky.dec.deg),
                    "fluxNjy": flux * chain_scale})
    return out, wcs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparisons", type=pathlib.Path, default=DEFAULT_COMPARISONS)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    parser.add_argument("--regions", type=int, default=8)
    args = parser.parse_args()

    recon = json.loads(
        (ROOT / "public/data/layers/selected-regions/rubin-ps1-reconciliation.json")
        .read_text(encoding="utf-8"))
    chains = {
        r["regionId"]: ((r.get("units") or {}).get("documentedChain") or {}).get("scale")
        for r in recon.get("regions", [])
    }
    manifest = json.loads(args.comparisons.read_text(encoding="utf-8"))
    records = [r for r in manifest.get("regions", [])
               if r.get("referenceSurveyId") == "panstarrs-dr2"]
    per_region = []

    for record in records:
        if len(per_region) >= args.regions:
            break
        # The comparisons manifest records only a checksum for the reference, so
        # the normalized file is located by the builder's own naming convention.
        # The "-i" in that name is hardcoded by the builder and says nothing
        # about the actual filter -- which is exactly what this script is here
        # to determine.
        path = (args.comparisons.parent / "panstarrs-normalized"
                / f"{record['regionId']}-panstarrs-i.fits")
        scale = chains.get(record["regionId"])
        if not path.is_file() or not scale:
            continue
        centre = record.get("center")
        try:
            ours, _ = measure(path, float(scale))
            theirs = catalogue(float(centre[0]), float(centre[1]))
        except Exception as error:  # noqa: BLE001
            print(f"  {record['regionId']}: {type(error).__name__}: {str(error)[:70]}")
            continue
        if not ours or not theirs:
            continue

        cat_ra = np.array([t["ra"] for t in theirs])
        cat_dec = np.array([t["dec"] for t in theirs])
        cat_mag = np.array([t["rMeanPSFMag"] for t in theirs])
        cat_mag_i = np.array([t["iMeanPSFMag"] for t in theirs])
        # Compare against BOTH r and i. Whichever the pixels actually are, that
        # band's offset is the small one, so this identifies the filter and
        # measures the zeropoint in a single pass.
        offsets_r, offsets_i = [], []
        for source in ours:
            d = np.hypot((cat_ra - source["ra"]) * np.cos(np.radians(source["dec"])),
                         cat_dec - source["dec"]) * 3600.0
            j = int(np.argmin(d))
            if d[j] > MATCH_ARCSEC:
                continue
            if not (SATURATION_MAG < cat_mag[j] < BRIGHT_MAG):
                continue
            # Isolated: no other catalogue star within ISOLATION_ARCSEC.
            near = np.sort(d)[1] if d.size > 1 else 1e9
            if near < ISOLATION_ARCSEC:
                continue
            ours_mag = -2.5 * np.log10(source["fluxNjy"] / AB_ZERO_POINT_NJY)
            offsets_r.append(ours_mag - cat_mag[j])
            offsets_i.append(ours_mag - cat_mag_i[j])
        if len(offsets_r) < MIN_STARS:
            continue

        def clipped(values: list[float]) -> np.ndarray:
            array = np.asarray(values)
            spread = np.std(array) or 1.0
            return array[np.abs(array - np.median(array)) < 3 * spread]

        clean_r, clean_i = clipped(offsets_r), clipped(offsets_i)
        per_region.append({
            "regionId": record["regionId"],
            "matchedStars": int(clean_r.size),
            "medianOffsetMagVsR": float(np.median(clean_r)),
            "medianOffsetMagVsI": float(np.median(clean_i)),
            "scatterMagVsR": float(np.std(clean_r)),
            "scatterMagVsI": float(np.std(clean_i)),
        })
        print(f"  {record['regionId']:22s} n={clean_r.size:4d}  "
              f"vs PS1 r {np.median(clean_r):+.3f} (sd {np.std(clean_r):.3f})  "
              f"vs PS1 i {np.median(clean_i):+.3f} (sd {np.std(clean_i):.3f})")

    if not per_region:
        raise SystemExit("no region produced a usable comparison")

    med = float(np.median([r["medianOffsetMagVsR"] for r in per_region]))
    med_i = float(np.median([r["medianOffsetMagVsI"] for r in per_region]))
    ratio = 10 ** (-0.4 * med)
    band = "r" if abs(med) < abs(med_i) else "i"
    payload = {
        "schemaVersion": "layers-ps1-flux-chain-verification-v1",
        "question": (
            "Section 28 found Pan-STARRS the lone outlier, putting Rubin 16% bright while "
            "Legacy and HSC put it 7-9% faint. PS1 is also the only reference whose absolute "
            "flux chain was never verified. Is the chain wrong?"
        ),
        "method": (
            "Measure compact sources in the same normalized PS1 pixels the reconciliation used, "
            "convert with the same documented chain, and compare to PS1 DR2 rMeanPSFMag from "
            "MAST for the same stars within 1 arcsec. A correct chain gives zero offset."
        ),
        "regions": per_region,
        "medianOffsetMagVsR": med,
        "medianOffsetMagVsI": med_i,
        "bandTheDataMatches": band,
        "impliedFluxRatio": ratio,
        "chainVerified": bool(abs(med) < 0.05),
        "reading": (
            f"Our chain reproduces PS1's own magnitudes to {med:+.3f} mag "
            f"(flux ratio {ratio:.4f}). "
            + ("That is within tolerance, so the chain is sound and section 28's PS1 outlier "
               "needs another explanation."
               if abs(med) < 0.05 else
               "That is a real zeropoint error in this project's PS1 conversion, and it "
               "propagates into all 188 PS1 reconciled pairs.")
        ),
        "reproduce": "python pipeline/verify_ps1_flux_chain.py",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nmedian offset {med:+.3f} mag  (flux ratio {ratio:.4f})")
    print(f"wrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
