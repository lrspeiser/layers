#!/usr/bin/env python3
"""Put HSC PDR2 cutouts into this project's normalized reference form, in nJy.

`build_selected_region_comparisons.py` consumes references as IMAGE / IVAR /
VALID_MASK with a self-identifying `referenceSurveyId`, and `acquire_hsc_pdr2.py`
writes the archive's raw three-HDU cutout. This is the step between.

Flux is converted here rather than in the reconcile stage, matching what
`normalize_des_cutouts.py` does for DES: the pixels arrive on an absolute scale,
so downstream needs no per-survey chain and cannot get one wrong. That failure
has already happened once in this repository -- a DES reference was relabelled
Legacy and had the nanomaggy chain applied to pixels already in nJy, a factor of
about 3,400 -- and the fewer places a unit conversion can live, the fewer places
it can be applied twice or not at all.

The conversion is

    nJy = count * 3.63078e12 / FLUXMAG0

read from each cutout's own header. Measured across the fetched set FLUXMAG0 is
63095734448.0194 everywhere, an AB zeropoint of exactly 27.0 mag and 57.544 nJy
per count, but it is read per region because a constant that happens to be
uniform is still a constant somebody assumed.

Planes are located by EXTTYPE (IMAGE, MASK, VARIANCE) rather than by position or
EXTNAME. The cutouts carry no EXTNAME at all, which already caused a
variance-plane check keyed on names to report "no variance plane" for files that
have one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "public/data/layers/hsc-pdr2/manifest.json"
DEFAULT_CACHE = ROOT / "pipeline/results/hsc-pdr2"
DEFAULT_PRODUCTS = ROOT / "pipeline/results/selected-region-comparisons"
DEFAULT_OUTPUT = ROOT / "public/data/layers/hsc-pdr2/normalized.json"

AB_ZERO_POINT_NJY = 3.63078054770e12  # 3631 Jy in nJy

# HSC mask bits that make a pixel unusable for photometry. Anything flagged bad,
# saturated, or with no data contributes nothing trustworthy to a comparison.
BAD_MASK_BITS = 0  # 0 means "require a clean mask"; see valid_mask().


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def planes(handle: fits.HDUList) -> dict[str, Any]:
    """Locate IMAGE, MASK and VARIANCE by EXTTYPE.

    These cutouts carry EXTTYPE but no EXTNAME, so astropy reports every
    extension's name as the empty string. Keying on position would work today
    and break silently the first time the service reorders them.
    """
    found: dict[str, Any] = {}
    for hdu in handle:
        kind = str(hdu.header.get("EXTTYPE", "")).strip().upper()
        if kind in ("IMAGE", "MASK", "VARIANCE") and hdu.data is not None:
            found[kind] = hdu
    missing = {"IMAGE", "VARIANCE"} - set(found)
    if missing:
        raise ValueError(f"missing {sorted(missing)} plane(s)")
    return found


def normalize(source: Path, destination: Path) -> dict[str, Any]:
    with fits.open(source, memmap=False) as handle:
        found = planes(handle)
        image_hdu = found["IMAGE"]
        header = image_hdu.header
        flux_mag0 = header.get("FLUXMAG0")
        if not flux_mag0:
            for hdu in handle:
                if hdu.header.get("FLUXMAG0"):
                    flux_mag0 = hdu.header["FLUXMAG0"]
                    break
        if not flux_mag0:
            raise ValueError("no FLUXMAG0; cannot place counts on an absolute scale")

        scale = AB_ZERO_POINT_NJY / float(flux_mag0)
        image = np.asarray(image_hdu.data, dtype=np.float64) * scale
        variance = np.asarray(found["VARIANCE"].data, dtype=np.float64) * scale**2
        mask = (
            np.asarray(found["MASK"].data, dtype=np.int64)
            if "MASK" in found
            else np.zeros(image.shape, dtype=np.int64)
        )

        valid = np.isfinite(image) & np.isfinite(variance) & (variance > 0) & (mask == 0)
        ivar = np.zeros(image.shape, dtype=np.float32)
        ivar[valid] = (1.0 / variance[valid]).astype(np.float32)

        wcs_header = header.copy()
        image_header = wcs_header.copy(); image_header["BUNIT"] = "nJy"
        image_header["FLUXMAG0"] = float(flux_mag0)
        ivar_header = wcs_header.copy(); ivar_header["BUNIT"] = "1/nJy^2"
        mask_header = wcs_header.copy()
        mask_header["MASKDEF"] = "1=finite science, positive variance, no HSC mask bit set"

        destination.parent.mkdir(parents=True, exist_ok=True)
        fits.HDUList([
            fits.PrimaryHDU(),
            fits.ImageHDU(image.astype(np.float32), header=image_header, name="IMAGE"),
            fits.ImageHDU(ivar, header=ivar_header, name="IVAR"),
            fits.ImageHDU(valid.astype(np.uint8), header=mask_header, name="VALID_MASK"),
        ]).writeto(destination, overwrite=True, checksum=True)

        return {
            "fluxMag0": float(flux_mag0),
            "njyPerCount": scale,
            "validPixelFraction": float(valid.mean()),
            "shape": [int(n) for n in image.shape],
            "filter": str(header.get("FILTER", "")).strip() or None,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    regions = [r for r in manifest["regions"] if r.get("sourcePixelsValidated")]
    if args.limit:
        regions = regions[: args.limit]

    out_dir = args.products / "hsc-normalized"
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for index, region in enumerate(regions, 1):
        region_id = region["regionId"]
        band = region.get("band", "HSC-R")
        source = args.cache / f"{region_id}-{band}.fits"
        if not source.is_file():
            failures.append({"regionId": region_id, "reason": "cutout missing from cache"})
            continue
        destination = out_dir / f"{region_id}-hsc-r.fits"
        try:
            checks = normalize(source, destination)
        except Exception as error:  # noqa: BLE001 - one region must not end the run
            failures.append({"regionId": region_id, "reason": f"{type(error).__name__}: {error}"})
            continue
        records.append(
            {
                "regionId": region_id,
                "tract": region.get("tract"),
                "band": checks["filter"] or "r",
                "unit": "nJy",
                "scienceReady": True,
                # Self-identifying, so the comparison builder does not relabel it.
                # A reference that inherits the Legacy label also inherits the
                # nanomaggy chain, which is a factor of about 3,400.
                "referenceSurveyId": "hsc-ssp-pdr2",
                "referenceSurvey": "HSC-SSP",
                "referenceRelease": "PDR2",
                "normalizedFits": {
                    "path": relative(destination),
                    "sha256": sha256(destination),
                    "bytes": destination.stat().st_size,
                },
                **{k: v for k, v in checks.items() if k != "filter"},
            }
        )
        if index % 25 == 0:
            print(f"  {index}/{len(regions)} normalized")

    payload = {
        "schemaVersion": "layers-hsc-normalized-v1",
        "generatedAt": utc_now(),
        "surveyId": "hsc-ssp-pdr2",
        "unit": "nJy",
        "conversion": "nJy = count * 3.63078e12 / FLUXMAG0, read per region from the cutout header",
        "regionsNormalized": len(records),
        "regions": records,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"\nnormalized {len(records)} of {len(regions)} regions into nJy")
    if failures:
        print(f"failed {len(failures)}: {failures[0]['reason'][:80]}")
    try:
        shown = args.output.relative_to(ROOT)
    except ValueError:
        shown = args.output
    print(f"wrote {shown}")


if __name__ == "__main__":
    main()
