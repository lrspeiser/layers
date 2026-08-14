#!/usr/bin/env python3
"""Normalize cached Legacy DR10 cutouts into explicit science-input FITS.

The Legacy viewer returns a band cube followed by its inverse-variance cube,
but the extensions are unnamed and do not carry BUNIT.  This script preserves
those pixels, selects one declared band, and writes an auditable FITS with
IMAGE, IVAR, VALID_MASK, and COVERAGE extensions.  It does not claim that the
result is comparison-ready; PSF, filter, sky, and common-grid QA are separate.
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
from astropy.wcs import WCS
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = ROOT / "pipeline/results/acquisition-50-bounded/science-legacy/acquisition-plan.json"
DEFAULT_OUTPUT = ROOT / "pipeline/results/acquisition-50-bounded/legacy-normalized"
DEFAULT_DETAILED = DEFAULT_OUTPUT / "manifest.json"
DEFAULT_PUBLIC = ROOT / "public/data/layers/selected-regions/legacy-dr10.json"
DEFAULT_PREVIEWS = ROOT / "public/layer-previews/selected-regions"


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


def display_uint8(data: np.ndarray, valid: np.ndarray) -> np.ndarray:
    finite = data[valid]
    if not finite.size:
        return np.zeros(data.shape, dtype=np.uint8)
    low, high = np.nanpercentile(finite, [1.0, 99.7])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low, high = float(np.nanmin(finite)), float(np.nanmax(finite))
    scale = max(high - low, np.finfo(float).eps)
    normalized = np.clip((data - low) / scale, 0, 1)
    stretched = np.arcsinh(8 * normalized) / np.arcsinh(8)
    output = np.zeros(data.shape, dtype=np.uint8)
    output[valid] = np.round(stretched[valid] * 255).astype(np.uint8)
    return output


def celestial_header(source: fits.Header) -> fits.Header:
    header = WCS(source).celestial.to_header(relax=True)
    for key in ("RADESYS", "EQUINOX", "LONPOLE", "LATPOLE"):
        if key in source and key not in header:
            header[key] = source[key]
    return header


def normalize(job: dict[str, Any], preferred_band: str, output_dir: Path, preview_dir: Path) -> dict[str, Any]:
    cache = job["cache"]
    source = ROOT / cache["path"]
    if sha256(source) != cache["sha256"]:
        raise ValueError("source checksum does not match acquisition evidence")

    with fits.open(source, memmap=False, checksum=True) as hdus:
        if len(hdus) < 2 or hdus[0].data is None or hdus[1].data is None:
            raise ValueError("expected image and inverse-variance cubes")
        images = np.asarray(hdus[0].data, dtype=np.float32)
        inverse_variance = np.asarray(hdus[1].data, dtype=np.float32)
        bands = str(hdus[0].header.get("BANDS", job.get("band", ""))).strip()
        if images.ndim != 3 or inverse_variance.shape != images.shape:
            raise ValueError("image and inverse-variance cubes are not matching 3-D arrays")
        if len(bands) != images.shape[0] or preferred_band not in bands:
            raise ValueError(f"declared bands {bands!r} do not index requested {preferred_band!r}")
        choices = list(dict.fromkeys((preferred_band, "r", "z", "g")))
        band = ""
        band_index = -1
        for candidate in choices:
            if candidate not in bands:
                continue
            index = bands.index(candidate)
            candidate_valid = np.isfinite(images[index]) & np.isfinite(inverse_variance[index]) & (inverse_variance[index] > 0)
            if candidate_valid.any():
                band, band_index = candidate, index
                break
        if band_index < 0:
            raise ValueError("cutout has no finite positive-weight pixels in the preferred or fallback bands")
        image = images[band_index]
        ivar = inverse_variance[band_index]
        header = celestial_header(hdus[0].header)
        if not WCS(header).has_celestial:
            raise ValueError("source cutout lacks celestial WCS")

    valid = np.isfinite(image) & np.isfinite(ivar) & (ivar > 0)
    if not valid.any():
        raise ValueError("cutout has no finite positive-weight pixels")
    mask = valid.astype(np.uint8)

    region_id = job["region"]["id"]
    tract = int(job["region"].get("tract"))
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{region_id}-legacy-dr10-{band}.fits"
    preview = preview_dir / f"{region_id}-legacy-dr10-{band}.png"

    primary = fits.PrimaryHDU()
    primary.header["ORIGIN"] = "Layers"
    primary.header["SURVEY"] = "DESI Legacy Imaging Surveys"
    primary.header["RELEASE"] = "DR10"
    primary.header["BAND"] = band
    primary.header["DERIVED"] = True
    primary.header["SCIRDY"] = True
    primary.header["CMPRDY"] = False

    image_header = header.copy()
    image_header["BUNIT"] = "nanomaggy/pixel"
    image_header["SRCBAND"] = band
    ivar_header = header.copy()
    ivar_header["BUNIT"] = "1/(nanomaggy/pixel)^2"
    ivar_header["SRCBAND"] = band
    mask_header = header.copy()
    mask_header["MASKDEF"] = "1=finite image and finite positive inverse variance"
    coverage_header = header.copy()
    coverage_header["COVDEF"] = "1=positive inverse-variance support"

    fits.HDUList([
        primary,
        fits.ImageHDU(image, header=image_header, name="IMAGE"),
        fits.ImageHDU(ivar, header=ivar_header, name="IVAR"),
        fits.ImageHDU(mask, header=mask_header, name="VALID_MASK"),
        fits.ImageHDU(mask, header=coverage_header, name="COVERAGE"),
    ]).writeto(output, overwrite=True, checksum=True)
    Image.fromarray(display_uint8(image, valid), mode="L").save(preview, optimize=True)

    return {
        "regionId": region_id,
        "tract": tract,
        "center": [job["region"]["ra_deg"], job["region"]["dec_deg"]],
        "status": "validated-science-input",
        "preferredBand": preferred_band,
        "band": band,
        "unit": "nanomaggy/pixel",
        "validPixelFraction": round(float(valid.mean()), 8),
        "shape": list(image.shape),
        "source": {
            "path": relative(source),
            "sha256": cache["sha256"],
            "bytes": source.stat().st_size,
            "archiveBandCube": bands,
            "selectedBandIndex": band_index,
        },
        "normalizedFits": {"path": relative(output), "sha256": sha256(output), "bytes": output.stat().st_size},
        "preview": "/" + relative(preview).removeprefix("public/"),
        "supportPlanes": {"image": True, "inverseVariance": True, "validMask": True, "coverage": True, "celestialWcs": True},
        "scienceReady": True,
        "comparisonReady": False,
        "comparisonBlockers": ["Rubin pixels", "common WCS grid", "PSF match", "filter transfer", "sky/background QA", "resampling covariance"],
    }


def validate_record(record: dict[str, Any]) -> None:
    path = ROOT / record["normalizedFits"]["path"]
    if sha256(path) != record["normalizedFits"]["sha256"]:
        raise ValueError("normalized checksum mismatch")
    with fits.open(path, memmap=False, checksum=True) as hdus:
        names = {hdu.name for hdu in hdus}
        if not {"IMAGE", "IVAR", "VALID_MASK", "COVERAGE"}.issubset(names):
            raise ValueError("normalized FITS is missing required support planes")
        if hdus["IMAGE"].header.get("BUNIT") != "nanomaggy/pixel":
            raise ValueError("normalized image unit is missing")
        if not WCS(hdus["IMAGE"].header).has_celestial:
            raise ValueError("normalized FITS lacks celestial WCS")
        shape = hdus["IMAGE"].data.shape
        if any(hdus[name].data.shape != shape for name in ("IVAR", "VALID_MASK", "COVERAGE")):
            raise ValueError("support planes do not share image shape")


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    clean = json.loads(json.dumps(record))
    if isinstance(clean.get("source"), dict):
        clean["source"].pop("path", None)
    if isinstance(clean.get("normalizedFits"), dict):
        clean["normalizedFits"].pop("path", None)
    return clean


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--detailed-manifest", type=Path, default=DEFAULT_DETAILED)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--previews", type=Path, default=DEFAULT_PREVIEWS)
    parser.add_argument("--band", default="i", choices=list("griz"))
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    selected = {region["id"]: region for region in plan["regions"]}
    acquired = {
        job["region"]["id"]: job for job in plan["jobs"]
        if job.get("surveyId") == "legacy-surveys-dr10"
        and job.get("phase") == "acquire"
        and job.get("status") in {"cached", "fetched"}
        and job.get("cache", {}).get("path")
    }

    records: list[dict[str, Any]] = []
    for region_id, region in selected.items():
        if region_id not in acquired:
            records.append({
                "regionId": region_id,
                "tract": int(region["tract"]),
                "center": region["center"],
                "status": "not-acquired",
                "scienceReady": False,
                "comparisonReady": False,
                "reason": "No successful bounded Legacy DR10 FITS acquisition is present in the evidence plan.",
            })
            continue
        try:
            record = normalize(acquired[region_id], args.band, args.output, args.previews)
            validate_record(record)
            records.append(record)
            print(f"[validated] {region_id}", flush=True)
        except Exception as error:
            records.append({
                "regionId": region_id,
                "tract": int(region["tract"]),
                "center": region["center"],
                "status": "validation-failed",
                "scienceReady": False,
                "comparisonReady": False,
                "reason": f"{type(error).__name__}: {error}",
            })
            print(f"[failed] {region_id}: {error}", flush=True)

    validated = [record for record in records if record["status"] == "validated-science-input"]
    payload = {
        "schemaVersion": "layers-legacy-normalized-v1",
        "generatedAt": utc_now(),
        "survey": "DESI Legacy Imaging Surveys",
        "release": "DR10",
        "preferredBand": args.band,
        "sourcePlan": relative(args.plan),
        "documentation": [
            "https://www.legacysurvey.org/dr10/description/",
            "https://www.legacysurvey.org/viewer/urls",
            "https://www.legacysurvey.org/svtips/",
        ],
        "method": "Select the preferred i-band plane, falling back deterministically to r, z, then g only when that field has no positive i-band weight; derive validity from finite image and positive finite inverse variance; preserve celestial WCS.",
        "counts": {
            "selectedRegions": len(records),
            "archiveInputs": len(acquired),
            "validatedScienceInputs": len(validated),
            "comparisonReady": 0,
            "notAcquiredOrFailed": len(records) - len(validated),
        },
        "sciencePolicy": {
            "archivePixelsUnchanged": True,
            "validMaskDerivedFromInverseVariance": True,
            "scienceInputReady": True,
            "comparisonReady": False,
            "displayStretchQuantitative": False,
        },
        "regions": records,
    }
    args.detailed_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.detailed_manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    public_payload = {
        **{key: value for key, value in payload.items() if key not in {"sourcePlan", "regions"}},
        "sourceEvidenceSha256": sha256(args.plan),
        "regions": [public_record(record) for record in records],
    }
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(public_payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
