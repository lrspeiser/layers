#!/usr/bin/env python3
"""Normalise DES DR2 cutouts into the reference format the comparison stage reads.

The Legacy path supplies IMAGE, IVAR, and VALID_MASK planes. DES DR2 cutouts
arrive as a single calibrated image with no variance, so two things have to be
constructed, and both are constructed in a way that is visible rather than
implied:

* **Flux.** DES coadds carry a fixed 30.0 AB zeropoint, so ADU convert to nJy by
  a stated constant. Unlike the Legacy path there is no pixel-area factor to
  recover: the cutout service returns the coadd's own pixels at the coadd's own
  scale, so the values already belong to the pixels they sit on. That difference
  is the whole reason this comparison is worth having.
* **Variance.** There is none to propagate, so a uniform plane is written from
  the robust background scatter of the image itself. It is an estimate of the
  sky noise, not a propagated uncertainty, and it is recorded as such. Every
  quantitative stage downstream measures its own empirical noise anyway, which
  is why this is usable rather than disqualifying.
"""

from __future__ import annotations

import argparse
import hashlib
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_layer_registration import robust_sigma

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "pipeline/results/des-dr2/manifest.json"
DEFAULT_OUTPUT = ROOT / "pipeline/results/des-dr2/normalized"

DES_ZEROPOINT_AB = 30.0
AB_ZERO_POINT_NJY = 3.63078054770e12


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(source: Path, destination: Path) -> dict[str, Any] | None:
    with fits.open(source, memmap=False) as hdus:
        image = None
        header = None
        for hdu in hdus:
            data = getattr(hdu, "data", None)
            if data is not None and np.ndim(data) == 2:
                image = np.asarray(data, dtype=np.float64)
                header = hdu.header
                break
    if image is None:
        return None
    wcs = WCS(header).celestial
    if not wcs.has_celestial:
        return None

    # ADU -> nJy through the declared zeropoint. m = -2.5log10(ADU) + 30.0, and
    # f_nJy = 10^(-0.4*(m - 8.9)) * 1e9, which collapses to a constant factor.
    factor = 10 ** (-0.4 * (DES_ZEROPOINT_AB - 8.9)) * 1e9
    flux = image * factor

    valid = np.isfinite(flux)
    if valid.sum() < 0.5 * flux.size:
        return None
    background = robust_sigma(flux[valid])
    if not np.isfinite(background) or background <= 0:
        return None
    ivar = np.where(valid, 1.0 / background**2, 0.0).astype(np.float32)

    base = wcs.to_header(relax=True)
    image_header = base.copy()
    image_header["BUNIT"] = "nJy"
    image_header["ZPTAB"] = (DES_ZEROPOINT_AB, "DES DR2 coadd AB zeropoint")
    ivar_header = base.copy()
    ivar_header["BUNIT"] = "1/nJy2"
    ivar_header["IVARSRC"] = ("uniform sky estimate", "not a propagated variance plane")
    mask_header = base.copy()
    mask_header["MASKDEF"] = "1=finite science pixel"

    destination.parent.mkdir(parents=True, exist_ok=True)
    fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(flux.astype(np.float32), header=image_header, name="IMAGE"),
        fits.ImageHDU(ivar, header=ivar_header, name="IVAR"),
        fits.ImageHDU(valid.astype(np.uint8), header=mask_header, name="VALID_MASK"),
    ]).writeto(destination, overwrite=True, checksum=True)
    return {
        "shape": [int(flux.shape[0]), int(flux.shape[1])],
        "validPixelFraction": float(valid.mean()),
        "skyScatterNjy": float(background),
        "fluxFactorAduToNjy": factor,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    failed = 0
    for region in payload["regions"]:
        if not region.get("scienceReady") or not region.get("localFits"):
            continue
        source = ROOT / region["localFits"]["path"]
        destination = args.output / f"{region['regionId']}-des-dr2-{region['band']}.fits"
        try:
            result = normalize(source, destination)
        except Exception:
            result = None
        if result is None:
            failed += 1
            continue
        records.append({
            "regionId": region["regionId"],
            "tract": region["tract"],
            "center": region["center"],
            "band": region["band"],
            "unit": "nJy",
            "scienceReady": True,
            "referenceSurveyId": "des-dr2",
            "referenceSurvey": "Dark Energy Survey",
            "referenceRelease": "DR2",
            "normalizedFits": {
                "path": destination.relative_to(ROOT).as_posix(),
                "sha256": sha256(destination),
                "bytes": destination.stat().st_size,
            },
            **result,
        })
        print(f"[normalized] {region['regionId']} {region['band']}", flush=True)

    summary = {
        "schemaVersion": "layers-des-normalized-v1",
        "generatedAt": utc_now(),
        "zeropointAB": DES_ZEROPOINT_AB,
        "varianceNote": (
            "IVAR is a uniform plane from the image's own robust background scatter, not a propagated "
            "variance. DES cutouts ship without one. Every quantitative stage downstream measures its "
            "own empirical noise, so this affects weighting rather than any reported uncertainty."
        ),
        "pixelAreaNote": (
            "No pixel-area factor applies. The DES cutout service returns coadd pixels at the coadd "
            "scale, unlike the Legacy viewer, which rewrites the WCS while preserving 0.262 arcsec "
            "values and therefore needs one."
        ),
        "counts": {"normalized": len(records), "failed": failed},
        "regions": records,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nnormalized {len(records)} DES regions, {failed} failed")


if __name__ == "__main__":
    main()
