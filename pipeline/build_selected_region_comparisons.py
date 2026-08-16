#!/usr/bin/env python3
"""Build honest common-grid Rubin/Legacy displays for selected DP2 regions.

Inputs must already be validated science-input FITS.  Rubin is reprojected to
the Legacy grid for display and positional inspection.  Because the operation
does not PSF-match, bandpass-match, background-match, or propagate correlated
noise, every output remains explicitly non-quantitative and comparisonReady=false.
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
from reproject import reproject_interp


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUBIN = ROOT / "pipeline/results/rubin-pixels-50/manifest.json"
DEFAULT_LEGACY = ROOT / "pipeline/results/acquisition-50-bounded/legacy-normalized/manifest.json"
DEFAULT_PS1 = ROOT / "pipeline/results/panstarrs-gap-fill/evidence/manifest.json"
DEFAULT_PRODUCTS = ROOT / "pipeline/results/selected-region-comparisons"
DEFAULT_PUBLIC = ROOT / "public/data/layers/selected-regions/rubin-reference-comparisons.json"
DEFAULT_PREVIEWS = ROOT / "public/layer-previews/selected-regions/comparisons"


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


def stretch(data: np.ndarray, valid: np.ndarray) -> np.ndarray:
    values = data[valid]
    output = np.zeros(data.shape, dtype=np.uint8)
    if not values.size:
        return output
    low, high = np.nanpercentile(values, [1.0, 99.7])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low, high = float(np.nanmin(values)), float(np.nanmax(values))
    scaled = np.clip((data - low) / max(high - low, np.finfo(float).eps), 0, 1)
    scaled = np.arcsinh(8 * scaled) / np.arcsinh(8)
    output[valid] = np.round(scaled[valid] * 255).astype(np.uint8)
    return output


def save_png(path: Path, array: np.ndarray) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array).save(path, optimize=True)
    return {"path": "/" + relative(path).removeprefix("public/"), "sha256": sha256(path), "bytes": path.stat().st_size}


def normalize_ps1_reference(record: dict[str, Any], products: Path) -> dict[str, Any]:
    by_role = {product["role"]: product for product in record["products"]}
    if set(by_role) < {"science", "weight", "mask"}:
        raise ValueError("Pan-STARRS evidence lacks science, weight, or mask")
    arrays: dict[str, np.ndarray] = {}
    headers: dict[str, fits.Header] = {}
    for role in ("science", "weight", "mask"):
        source = ROOT / by_role[role]["localPath"]
        if sha256(source) != by_role[role]["sha256"]:
            raise ValueError(f"Pan-STARRS {role} checksum mismatch")
        with fits.open(source, memmap=False, checksum=True) as hdus:
            arrays[role] = np.asarray(hdus[0].data, dtype=np.float64)
            headers[role] = hdus[0].header.copy()
    if len({array.shape for array in arrays.values()}) != 1:
        raise ValueError("Pan-STARRS support planes do not share shape")
    wcs = WCS(headers["science"]).celestial
    if not wcs.has_celestial:
        raise ValueError("Pan-STARRS science plane lacks celestial WCS")
    science, variance, mask = arrays["science"], arrays["weight"], arrays["mask"]
    flagged = np.isfinite(mask) & (mask != 0)
    valid = np.isfinite(science) & np.isfinite(variance) & (variance > 0) & ~flagged
    if not valid.any():
        raise ValueError("Pan-STARRS reference has no unflagged positive-variance pixels")
    ivar = np.zeros(variance.shape, dtype=np.float32)
    ivar[valid] = (1.0 / variance[valid]).astype(np.float32)
    header = wcs.to_header(relax=True)
    image_header = header.copy(); image_header["BUNIT"] = "PS1 linear stack unit"
    ivar_header = header.copy(); ivar_header["BUNIT"] = "1/(PS1 stack unit)^2"
    mask_header = header.copy(); mask_header["MASKDEF"] = "1=finite science, positive variance, no returned flag"
    output_dir = products / "panstarrs-normalized"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{record['regionId']}-panstarrs-i.fits"
    fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(science.astype(np.float32), header=image_header, name="IMAGE"),
        fits.ImageHDU(ivar, header=ivar_header, name="IVAR"),
        fits.ImageHDU(valid.astype(np.uint8), header=mask_header, name="VALID_MASK"),
    ]).writeto(output, overwrite=True, checksum=True)
    return {
        "regionId": record["regionId"], "tract": record["tract"], "band": record["band"],
        "unit": "PS1 linear stack unit", "scienceReady": True,
        "referenceSurveyId": "panstarrs-dr2", "referenceSurvey": "Pan-STARRS1", "referenceRelease": record["release"],
        "normalizedFits": {"path": relative(output), "sha256": sha256(output), "bytes": output.stat().st_size},
    }


def build_pair(rubin: dict[str, Any], reference: dict[str, Any], products: Path, previews: Path) -> dict[str, Any]:
    rubin_path = ROOT / rubin["mosaic"]["path"]
    reference_path = ROOT / reference["normalizedFits"]["path"]
    if sha256(rubin_path) != rubin["mosaic"]["sha256"] or sha256(reference_path) != reference["normalizedFits"]["sha256"]:
        raise ValueError("input checksum mismatch")

    with fits.open(rubin_path, memmap=False, checksum=True) as rh, fits.open(reference_path, memmap=False, checksum=True) as lh:
        rubin_image = np.asarray(rh["IMAGE"].data, dtype=np.float64)
        rubin_variance = np.asarray(rh["VARIANCE"].data, dtype=np.float64)
        rubin_mask = np.asarray(rh["MASK"].data, dtype=np.int32)
        legacy_image = np.asarray(lh["IMAGE"].data, dtype=np.float64)
        legacy_ivar = np.asarray(lh["IVAR"].data, dtype=np.float64)
        legacy_valid = np.asarray(lh["VALID_MASK"].data, dtype=np.uint8) > 0
        rubin_wcs = WCS(rh["IMAGE"].header).celestial
        legacy_wcs = WCS(lh["IMAGE"].header).celestial
        if not rubin_wcs.has_celestial or not legacy_wcs.has_celestial:
            raise ValueError("input lacks celestial WCS")
        output_shape = legacy_image.shape
        output_header = legacy_wcs.to_header(relax=True)

    aligned_image, image_footprint = reproject_interp((rubin_image, rubin_wcs), output_header, shape_out=output_shape, order="bilinear")
    aligned_variance, variance_footprint = reproject_interp((rubin_variance, rubin_wcs), output_header, shape_out=output_shape, order="bilinear")
    aligned_mask_float, mask_footprint = reproject_interp((rubin_mask.astype(np.float64), rubin_wcs), output_header, shape_out=output_shape, order="nearest-neighbor")
    aligned_mask = np.where(np.isfinite(aligned_mask_float), np.rint(aligned_mask_float), 1).astype(np.int32)
    rubin_valid = (
        (image_footprint > 0)
        & (variance_footprint > 0)
        & (mask_footprint > 0)
        & np.isfinite(aligned_image)
        & np.isfinite(aligned_variance)
        & (aligned_variance > 0)
        & ((aligned_mask & ((1 << 0) | (1 << 3))) == 0)
    )
    legacy_valid = legacy_valid & np.isfinite(legacy_image) & np.isfinite(legacy_ivar) & (legacy_ivar > 0)
    common = rubin_valid & legacy_valid
    if not common.any():
        raise ValueError("no common valid pixels after WCS reprojection")

    rubin_display = stretch(aligned_image, common)
    legacy_display = stretch(legacy_image, common)
    coverage = np.zeros((*output_shape, 3), dtype=np.uint8)
    coverage[rubin_valid & ~legacy_valid] = [236, 69, 69]
    coverage[legacy_valid & ~rubin_valid] = [67, 139, 240]
    coverage[common] = [238, 244, 240]
    overlay = np.zeros((*output_shape, 3), dtype=np.uint8)
    overlay[..., 0] = np.maximum(overlay[..., 0], rubin_display)
    overlay[..., 1] = np.maximum((rubin_display * 0.35).astype(np.uint8), (legacy_display * 0.75).astype(np.uint8))
    overlay[..., 2] = np.maximum(overlay[..., 2], legacy_display)
    overlay[~common] = 0

    region_id = rubin["regionId"]
    tract = int(rubin["tract"])
    product_dir = products / region_id
    product_dir.mkdir(parents=True, exist_ok=True)
    fits_path = product_dir / "rubin-reference-display-grid.fits"
    primary = fits.PrimaryHDU()
    primary.header["TRACT"] = tract
    primary.header["RUBBAND"] = rubin["band"]
    primary.header["REFBAND"] = reference["band"]
    primary.header["SCICLAIM"] = False
    primary.header["CMPRDY"] = False
    rubin_header = output_header.copy(); rubin_header["BUNIT"] = "nJy"; rubin_header["DISPLAY"] = True
    reference_header = output_header.copy(); reference_header["BUNIT"] = reference["unit"]
    variance_header = output_header.copy(); variance_header["BUNIT"] = "nJy2"; variance_header["RESAMP"] = "bilinear display"
    ivar_header = output_header.copy(); ivar_header["BUNIT"] = "inverse reference variance"
    coverage_header = output_header.copy(); coverage_header["MASKDEF"] = "1=valid Rubin and Legacy support"
    fits.HDUList([
        primary,
        fits.ImageHDU(aligned_image.astype(np.float32), header=rubin_header, name="RUBIN_IMAGE"),
        fits.ImageHDU(legacy_image.astype(np.float32), header=reference_header, name="REFERENCE_IMAGE"),
        fits.ImageHDU(aligned_variance.astype(np.float32), header=variance_header, name="RUBIN_VARIANCE"),
        fits.ImageHDU(legacy_ivar.astype(np.float32), header=ivar_header, name="REFERENCE_IVAR"),
        fits.ImageHDU(common.astype(np.uint8), header=coverage_header, name="COMMON_COVERAGE"),
    ]).writeto(fits_path, overwrite=True, checksum=True)

    preview_dir = previews / region_id
    products_public = {
        "rubin": save_png(preview_dir / "rubin-r.png", rubin_display),
        "reference": save_png(preview_dir / f"reference-{reference['band']}.png", legacy_display),
        "coverage": save_png(preview_dir / "coverage.png", coverage),
        "positionOverlay": save_png(preview_dir / "position-overlay.png", overlay),
    }
    return {
        "regionId": region_id,
        "tract": tract,
        "center": rubin["center"],
        "status": "display-aligned",
        "rubinBand": rubin["band"],
        "referenceBand": reference["band"],
        "referenceSurveyId": reference["referenceSurveyId"],
        "referenceSurvey": reference["referenceSurvey"],
        "referenceRelease": reference["referenceRelease"],
        "sameNamedBand": rubin["band"] == reference["band"],
        "outputShape": list(output_shape),
        "commonCoverageFraction": round(float(common.mean()), 8),
        "supportFractions": {"rubin": round(float(rubin_valid.mean()), 8), "reference": round(float(legacy_valid.mean()), 8)},
        "inputs": {
            "rubin": {"sha256": rubin["mosaic"]["sha256"], "unit": "nJy", "scienceReady": True},
            "reference": {"sha256": reference["normalizedFits"]["sha256"], "unit": reference["unit"], "scienceReady": True},
        },
        "localFits": {"path": relative(fits_path), "sha256": sha256(fits_path), "bytes": fits_path.stat().st_size},
        "previews": products_public,
        "displayAlignmentAllowed": True,
        "scienceClaimAllowed": False,
        "comparisonReady": False,
        "comparisonBlockers": ["PSF matching", "bandpass transfer", "background matching", "flux-unit transfer", "resampling covariance", "injection/recovery QA"],
    }


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    clean = json.loads(json.dumps(record))
    clean["localFits"].pop("path", None)
    return clean


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubin-manifest", type=Path, default=DEFAULT_RUBIN)
    parser.add_argument("--legacy-manifest", type=Path, default=DEFAULT_LEGACY)
    parser.add_argument("--panstarrs-manifest", type=Path, default=DEFAULT_PS1)
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--previews", type=Path, default=DEFAULT_PREVIEWS)
    args = parser.parse_args()
    # Resolve every path argument against the current directory before use.
    # relative() calls Path.relative_to(ROOT), which raises on a relative input,
    # so passing --previews as a relative path failed every region with a
    # "not in the subpath of" error that looks like a data problem and is not.
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())

    rubin_payload = json.loads(args.rubin_manifest.read_text(encoding="utf-8"))
    legacy_payload = json.loads(args.legacy_manifest.read_text(encoding="utf-8"))
    references: dict[int, dict[str, Any]] = {}
    for record in legacy_payload["regions"]:
        if not record.get("scienceReady"):
            continue
        record = dict(record)
        # Only label as Legacy if the manifest has not already identified itself.
        # Overriding it silently relabelled a DES reference as Legacy, and the
        # reconcile stage then applied the nanomaggy chain to pixels already in
        # nJy, a factor of about 3,400.
        record.setdefault("referenceSurveyId", "legacy-surveys-dr10")
        record.setdefault("referenceSurvey", "Legacy Survey")
        record.setdefault("referenceRelease", "DR10")
        references[int(record["tract"])] = record
    if args.panstarrs_manifest.is_file():
        ps1_payload = json.loads(args.panstarrs_manifest.read_text(encoding="utf-8"))
        for record in ps1_payload["regions"]:
            # Gap-fill means gap-fill: PS1 supplies tracts Legacy does not cover,
            # it does not displace a Legacy reference that exists. This used to
            # overwrite unconditionally, which was harmless while PS1 covered a
            # handful of regions and would have replaced the entire Legacy chain
            # once it covered 198 of 200. To build a PS1-only set, pass a legacy
            # manifest with no regions rather than relying on precedence here.
            if record.get("sourcePixelsValidated") and int(record["tract"]) not in references:
                references[int(record["tract"])] = normalize_ps1_reference(record, args.products)
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for rubin in rubin_payload["regions"]:
        if not rubin.get("validation", {}).get("scienceReady") or int(rubin["tract"]) not in references:
            continue
        try:
            record = build_pair(rubin, references[int(rubin["tract"])], args.products, args.previews)
            records.append(record)
            print(f"[aligned] {record['regionId']} common={record['commonCoverageFraction']:.4f}", flush=True)
        except Exception as error:
            failures.append({"regionId": rubin["regionId"], "tract": rubin["tract"], "error": f"{type(error).__name__}: {error}"})
            print(f"[failed] {rubin['regionId']}: {error}", flush=True)

    payload = {
        "schemaVersion": "layers-selected-region-comparisons-v1",
        "generatedAt": utc_now(),
        "method": "Rubin r-band bilinear reprojection to each validated Legacy or Pan-STARRS native grid for display; masks use nearest-neighbor reprojection.",
        "counts": {
            "selectedRegions": 50,
            "rubinScienceInputs": rubin_payload["summary"]["scienceReadyRegionCount"],
            "referenceScienceInputs": len(references),
            "displayAligned": len(records),
            "sameNamedBand": sum(record["sameNamedBand"] for record in records),
            "comparisonReady": 0,
            "failed": len(failures),
        },
        "policy": {"displayAlignmentAllowed": True, "scienceClaimAllowed": False, "comparisonReady": False},
        "failures": failures,
        "regions": records,
    }
    args.products.mkdir(parents=True, exist_ok=True)
    (args.products / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    public_payload = {**payload, "regions": [public_record(record) for record in records]}
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(public_payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
