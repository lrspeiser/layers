#!/usr/bin/env python3
"""Give anomaly candidates an independent-resolution verdict from Euclid Q1.

The morphology-validation goal named HST, JWST and Euclid Q1. The HST/JWST pass
returned one verifiable candidate out of 34, and the reason is worth repeating
because it is the trap this operator is built to avoid: MAST's pointing table
listed 25 "observations" overlapping candidate positions, and loading the frames
showed the nearest was 24.1 arcsec outside. An archive saying a position is in
its footprint is not the same as pixels existing there.

So containment is asked of the data, not of a circle. IRSA publishes Euclid Q1
through ObsCore with a real ``s_region`` polygon per product, and the query here
is ``CONTAINS(POINT(candidate), s_region) = 1``. A candidate that does not pass
that is reported as not covered, and no cutout is attempted.

Which Euclid products count as independent matters too. The MER mosaics carry
DECam ancillary bands reprojected onto the Euclid grid alongside the VIS and
NISP pixels. DECam is the same camera behind both Legacy Survey and DES, so a
"confirmation" from a DECam layer would be the reference this project is already
comparing against, wearing a different name. Only VIS and NISP are used.

The verdict is measured against the empirical scatter of blank apertures in the
same Euclid image, never against the RMS plane. Formal uncertainties in this
project have understated the truth by a median factor of about seven, and the
whole point of an independent check is that it not inherit the first
measurement's optimism.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTER = ROOT / "public/data/layers/anomaly-register.json"
DEFAULT_OUTPUT = ROOT / "pipeline/results/euclid-followup"
DEFAULT_PUBLIC = ROOT / "public/data/layers/highres-followup/euclid-verdicts.json"

TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
IBE_CUTOUT = "https://irsa.ipac.caltech.edu/ibe/cutout"
CUTOUT_ARCSEC = 40.0
APERTURE_ARCSEC = 1.0
BLANK_APERTURES = 400
DETECTION_SIGMA = 5.0
REQUEST_PAUSE = 0.4

# VIS and NISP are Euclid's own cameras. The DECam layers inside a MER mosaic are
# the same instrument behind Legacy Survey and DES, so they cannot corroborate a
# comparison made against those surveys.
INDEPENDENT_INSTRUMENTS = ("VIS", "NISP")
AB_ZERO_POINT_NJY = 3.63078054770e12


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def tap_query(adql: str) -> list[dict[str, str]]:
    response = requests.get(TAP, params={"QUERY": adql, "FORMAT": "csv", "LANG": "ADQL"}, timeout=300)
    response.raise_for_status()
    return list(csv.DictReader(io.StringIO(response.text)))


def containing_products(ra: float, dec: float) -> list[dict[str, str]]:
    """Products whose actual footprint polygon contains the position."""
    return tap_query(
        "SELECT obs_collection, obs_id, energy_bandpassname, s_resolution, access_url "
        "FROM ivoa.obscore "
        f"WHERE obs_collection='euclid_DpdMerBksMosaic' "
        f"AND CONTAINS(POINT('ICRS',{ra},{dec}), s_region)=1"
    )


def datalink_images(access_url: str) -> dict[str, str]:
    """The archive-relative *path* of the science image, not its download URL.

    IBE's cutout service takes ra/dec/size/path, where path is column 12 of the
    datalink table. The obvious guess -- swapping /ibe/data/ for /ibe/cutout/ in
    the download URL -- returns 404, so the path column is what to read.
    """
    response = requests.get(access_url, timeout=200)
    response.raise_for_status()
    out: dict[str, str] = {}
    for row in re.finditer(r"<TR>(.*?)</TR>", response.text, re.S):
        cells = [c.strip() for c in re.findall(r"<TD>(.*?)</TD>", row.group(1), re.S)]
        if len(cells) < 13:
            continue
        semantics, path = cells[4], cells[12]
        if not path or path.startswith("http"):
            continue
        if semantics in {"#this", "#cutout"}:
            out.setdefault("science", path)
        elif semantics == "#noise":
            out.setdefault("noise", path)
    return out


def cutout(path_in_archive: str, ra: float, dec: float, destination: Path) -> Path | None:
    """IBE cutout: ra, dec, size in degrees, and the archive-relative path."""
    if destination.is_file() and destination.stat().st_size > 2880:
        return destination
    params = {"ra": ra, "dec": dec, "size": CUTOUT_ARCSEC / 3600.0, "path": path_in_archive}
    try:
        response = requests.get(IBE_CUTOUT, params=params, timeout=300)
        if response.status_code != 200 or len(response.content) < 2880:
            return None
        if b"SIMPLE" not in response.content[:30]:
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination
    except Exception:
        return None
    finally:
        time.sleep(REQUEST_PAUSE)


def measure(path: Path, ra: float, dec: float) -> dict[str, Any] | None:
    with fits.open(path, memmap=False) as hdus:
        hdu = next((h for h in hdus if getattr(h, "data", None) is not None and np.ndim(h.data) == 2), None)
        if hdu is None:
            return None
        data = np.asarray(hdu.data, dtype=np.float64)
        header = hdu.header
        wcs = WCS(header).celestial
    if not wcs.has_celestial:
        return None
    scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
    x, y = wcs.world_to_pixel_values(ra, dec)
    if not (0 <= x < data.shape[1] and 0 <= y < data.shape[0]):
        return None

    radius = max(1.5, APERTURE_ARCSEC / scale)
    yy, xx = np.indices(data.shape)
    aperture = np.hypot(xx - x, yy - y) <= radius
    finite = np.isfinite(data)
    if not (aperture & finite).any():
        return None
    counts = int((aperture & finite).sum())
    target_sum = float(np.nansum(data[aperture & finite]))

    # A MER tile is zero-filled outside its real coverage, and zero is finite.
    # The ObsCore polygon can therefore contain a position the tile has no pixels
    # for -- which is the footprint-is-not-data trap surviving a CONTAINS query
    # against the true footprint. Only loading the pixels settles it.
    nonzero = float(np.count_nonzero(data[finite])) / max(int(finite.sum()), 1)
    if nonzero < 0.01:
        return {"emptyCutout": True, "nonzeroPixelFraction": nonzero,
                "note": ("Every pixel in this cutout is zero. The product's ObsCore footprint "
                         "polygon contains the position, but the tile carries no data there.")}

    # Empirical null: the same aperture dropped on blank positions in this very
    # image. Never the RMS plane -- an independent check that inherits the first
    # measurement's optimism is not independent.
    rng = np.random.default_rng(20260815)
    margin = int(np.ceil(radius)) + 2
    blanks = []
    for _ in range(BLANK_APERTURES * 4):
        if len(blanks) >= BLANK_APERTURES:
            break
        bx = rng.uniform(margin, data.shape[1] - margin)
        by = rng.uniform(margin, data.shape[0] - margin)
        if np.hypot(bx - x, by - y) < 4 * radius:
            continue
        mask = np.hypot(xx - bx, yy - by) <= radius
        if not (mask & finite).any():
            continue
        blanks.append(float(np.nansum(data[mask & finite])))
    if len(blanks) < 30:
        return None
    blanks_array = np.asarray(blanks)
    centre = float(np.median(blanks_array))
    sigma = float(np.percentile(blanks_array, 84.13) - np.percentile(blanks_array, 15.87)) / 2.0
    if not np.isfinite(sigma) or sigma <= 0:
        return None
    significance = (target_sum - centre) / sigma
    return {
        "pixelScaleArcsec": scale,
        "apertureRadiusArcsec": round(radius * scale, 3),
        "aperturePixels": counts,
        "apertureSum": target_sum,
        "blankApertureMedian": centre,
        "blankApertureSigma": sigma,
        "blankAperturesUsed": len(blanks),
        "significance": float(significance),
        "detected": bool(significance >= DETECTION_SIGMA),
        "noiseModel": "empirical blank-aperture scatter in this Euclid image",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    args = parser.parse_args()

    register = json.loads(args.register.read_text(encoding="utf-8"))
    candidates = [
        {**item, "operator": operator}
        for operator, items in (register.get("byOperator") or {}).items()
        for item in (items or [])
    ]

    verdicts: list[dict[str, Any]] = []
    for candidate in candidates:
        position = candidate.get("position") or {}
        ra, dec = position.get("raDeg"), position.get("decDeg")
        record: dict[str, Any] = {
            "operator": candidate["operator"],
            "what": candidate.get("what"),
            "regionId": candidate.get("regionId"),
            "position": position,
        }
        if ra is None or dec is None:
            record.update({"status": "no-position", "verdict": None})
            verdicts.append(record)
            continue
        try:
            products = containing_products(float(ra), float(dec))
        except Exception as error:
            record.update({"status": "query-failed", "error": f"{type(error).__name__}: {error}"})
            verdicts.append(record)
            continue

        independent = [
            row for row in products
            if any(tag in (row.get("obs_id") or "") for tag in INDEPENDENT_INSTRUMENTS)
        ]
        record["productsContainingPosition"] = len(products)
        record["independentProducts"] = len(independent)
        if not independent:
            record.update({
                "status": "not-covered",
                "verdict": None,
                "note": (
                    "No Euclid VIS or NISP product's footprint polygon contains this position. "
                    "Any DECam layers present in a MER mosaic are the same instrument as the "
                    "Legacy and DES references and cannot corroborate a comparison against them."
                    if products else
                    "No Euclid Q1 product's footprint polygon contains this position."
                ),
            })
            verdicts.append(record)
            print(f"[not-covered] {record['operator']} {ra:.5f} {dec:+.5f}", flush=True)
            continue

        measurements: list[dict[str, Any]] = []
        empty: list[dict[str, Any]] = []
        for row in independent:
            band = row.get("energy_bandpassname") or "?"
            try:
                images = datalink_images(row["access_url"])
            except Exception:
                continue
            if "science" not in images:
                continue
            destination = args.output / "cutouts" / f"{ra:.5f}{dec:+.5f}-{band}.fits"
            path = cutout(images["science"], float(ra), float(dec), destination)
            if path is None:
                continue
            result = measure(path, float(ra), float(dec))
            if result is None:
                continue
            if result.get("emptyCutout"):
                empty.append({"band": band, "obsId": row.get("obs_id"), **result})
                print(f"  [{band}] empty cutout: polygon contains the position, pixels do not", flush=True)
                continue
            result.update({
                "band": band,
                "obsId": row.get("obs_id"),
                "instrument": next((t for t in INDEPENDENT_INSTRUMENTS if t in (row.get("obs_id") or "")), None),
                "localFits": display_path(path),
            })
            measurements.append(result)
            print(f"  [{band}] significance {result['significance']:+.2f} detected={result['detected']}", flush=True)

        if not measurements:
            record.update({
                "status": "footprint-contains-but-no-pixels" if empty else "covered-but-no-usable-cutout",
                "verdict": None,
                "emptyBands": [e["band"] for e in empty],
                "note": (
                    "Every independent Euclid product whose footprint polygon contains this "
                    "position returns an all-zero cutout there. Containment in ObsCore is a "
                    "statement about a polygon, not about pixels."
                ) if empty else None,
            })
            verdicts.append(record)
            print(f"[no-pixels] {record['operator']} {ra:.5f} {dec:+.5f}", flush=True)
            continue

        detections = [m for m in measurements if m["detected"]]
        claim_is_absence = "no optical counterpart" in (candidate.get("what") or "").lower()
        if claim_is_absence:
            verdict = "refuted" if detections else "survives"
            reading = (
                f"Euclid detects a source at this position in {len(detections)} of "
                f"{len(measurements)} independent bands, so the claim that nothing optical sits "
                "here does not hold at Euclid resolution."
                if detections else
                f"Euclid sees nothing above {DETECTION_SIGMA} sigma in any of {len(measurements)} "
                "independent bands, so the absence survives an independent look at higher resolution."
            )
        else:
            verdict = "detected" if detections else "not-detected"
            reading = (
                f"Euclid detects flux at this position in {len(detections)} of {len(measurements)} "
                "independent bands. This confirms something is there; it does not confirm the "
                "departure that flagged it, which is a photometric statement."
                if detections else
                f"Euclid sees nothing above {DETECTION_SIGMA} sigma in {len(measurements)} bands."
            )
        record.update({
            "status": "verdict-delivered",
            "verdict": verdict,
            "reading": reading,
            "measurements": measurements,
        })
        verdicts.append(record)
        print(f"[{verdict}] {record['operator']} {ra:.5f} {dec:+.5f}", flush=True)

    delivered = [v for v in verdicts if v.get("status") == "verdict-delivered"]
    payload = {
        "schemaVersion": "layers-euclid-followup-v1",
        "generatedAt": utc_now(),
        "survey": "Euclid Q1 (MER background-subtracted mosaics)",
        "access": "IRSA ObsCore TAP for containment, datalink for image URLs, IBE for cutouts",
        "method": {
            "containment": (
                "CONTAINS(POINT(candidate), s_region) = 1 against each product's real footprint "
                "polygon. The HST/JWST pass trusted a pointing table and found the nearest frame "
                "24.1 arcsec outside; this asks the data instead."
            ),
            "independence": (
                "Only Euclid VIS and NISP products are used. MER mosaics also carry DECam layers, "
                "and DECam is the camera behind both Legacy Survey and DES, so those cannot "
                "corroborate a comparison made against those surveys."
            ),
            "detectionThresholdSigma": DETECTION_SIGMA,
            "noise": "empirical blank-aperture scatter in the same image, never the RMS plane",
        },
        "counts": {
            "candidates": len(verdicts),
            "positionsTested": sum(1 for v in verdicts if v.get("productsContainingPosition") is not None),
            "coveredByIndependentEuclid": sum(1 for v in verdicts if v.get("independentProducts")),
            "verdictsDelivered": len(delivered),
            "refuted": sum(1 for v in delivered if v["verdict"] == "refuted"),
            "survives": sum(1 for v in delivered if v["verdict"] == "survives"),
            "detected": sum(1 for v in delivered if v["verdict"] == "detected"),
            "notDetected": sum(1 for v in delivered if v["verdict"] == "not-detected"),
            "notCovered": sum(1 for v in verdicts if v.get("status") == "not-covered"),
            "footprintContainsButNoPixels": sum(
                1 for v in verdicts if v.get("status") == "footprint-contains-but-no-pixels"
            ),
        },
        "caveats": [
            "A Euclid detection refutes an absence claim; it does not by itself validate or refute "
            "a photometric departure, which needs matched photometry rather than a detection.",
            "Euclid VIS is a single broad optical band. Agreement in it does not transfer to the "
            "Rubin band the candidate was measured in.",
            "ObsCore containment is necessary but not sufficient. A MER tile is zero-filled "
            "outside its real coverage and zero is a finite value, so a position can sit inside "
            "the published footprint polygon and still have no pixels. That is counted separately "
            "and never as a verdict.",
        ],
        "candidates": verdicts,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    # The public manifest must not carry local filesystem paths. localFits names
    # a file under pipeline/results, which is gitignored precisely because it
    # holds pixels; publishing its path advertises a layout nobody outside can
    # use and breaks the rule every other public manifest here follows.
    public = json.loads(json.dumps(payload))
    for candidate in public.get("candidates", []):
        for measurement in candidate.get("measurements") or []:
            measurement.pop("localFits", None)
    public["localProductsNote"] = (
        "Cutouts stay local. This manifest carries the measurements and verdicts; the pixels are "
        "reproducible from the obsId and position with pipeline/check_euclid_followup.py."
    )
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    print("\n" + json.dumps(payload["counts"], indent=2))
    print(f"wrote {display_path(args.public_manifest)}")


if __name__ == "__main__":
    main()
