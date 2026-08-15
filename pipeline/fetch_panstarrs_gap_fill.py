#!/usr/bin/env python3
"""Fetch and validate bounded PS1 DR2 cutouts for two optical-gap tracts.

The script consumes the already-cached MAST ``ps1filenames.py`` discovery
responses, requests one native-scale i-band cutout each for the stack, weight,
and mask planes, validates their FITS/WCS relationship, and writes detailed
local evidence plus a redacted public manifest and PNG previews.

This deliberately does *not* call the products comparison-ready.  A common
Rubin/PS1 pixel grid and astrometric/photometric alignment QA are separate,
downstream gates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.visualization import AsinhStretch, ImageNormalize, PercentileInterval
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales


ROOT = Path(__file__).resolve().parents[1]
# The band is a parameter, not a constant. It was hardcoded to i, which is the
# right gap-fill band but the wrong one for a photometric comparison: Rubin is r
# in 157 of the 200 regions, and an r-versus-i ratio measures source colour as
# much as it measures throughput.
BAND = "i"
RESULT_ROOT = ROOT / "pipeline" / "results" / "panstarrs-gap-fill"
FITS_ROOT = RESULT_ROOT / "fits"
PREVIEW_ROOT = ROOT / "public" / "images" / "layers" / "panstarrs-gap-fill"
PUBLIC_ROOT = ROOT / "public" / "data" / "layers" / "panstarrs-gap-fill"
FITSCUT = "https://ps1images.stsci.edu/cgi-bin/fitscut.cgi"
FILENAME_SERVICE = "https://ps1images.stsci.edu/cgi-bin/ps1filenames.py"
DOC_URL = (
    "https://outerspace.stsci.edu/spaces/PANSTARRS/pages/298812251/"
    "PS1+Image+Cutout+Service"
)
STACK_DOC_URL = (
    "https://outerspace.stsci.edu/spaces/PANSTARRS/pages/298812339/"
    "PS1+Stack+images"
)
PRODUCT_DOC_URL = (
    "https://outerspace.stsci.edu/spaces/PANSTARRS/pages/298812333/"
    "PS1+Image+data+products"
)
PIXEL_SCALE_ARCSEC = 0.25
CUTOUT_SIZE_PIXELS = 960
CUTOUT_SIZE_ARCMIN = CUTOUT_SIZE_PIXELS * PIXEL_SCALE_ARCSEC / 60.0

TARGETS = (
    {
        "regionId": "dp2-tract-5192",
        "tract": 5192,
        "raDeg": 267.096743,
        "decDeg": -27.519591,
        "discovery": ROOT
        / "pipeline/results/acquisition-50-bounded/cache/panstarrs-dr2/dr2/13d70c7dc8e431fe0380c533/discover-a5c1d2b9ed7f.csv",
    },
    {
        "regionId": "dp2-tract-6530",
        "tract": 6530,
        "raDeg": 250.909062,
        "decDeg": -18.594341,
        "discovery": ROOT
        / "pipeline/results/acquisition-50-bounded/cache/panstarrs-dr2/dr2/17e4d2bf7940e3e8975abf48/discover-5b62dd6fb226.csv",
    },
)

PRODUCT_LABELS = {
    "stack": "science",
    "stack.wt": "weight",
    "stack.mask": "mask",
}


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


def download(url: str, output: Path) -> tuple[str, int, str]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.stat().st_size > 2880:
        return sha256(output), output.stat().st_size, "cache-hit"

    headers = {"User-Agent": "Layers-Rubin-Light-Atlas/1.0 (bounded science cutouts)"}
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with requests.get(url, headers=headers, timeout=(30, 180), stream=True) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                with output.open("wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            with output.open("rb") as handle:
                signature = handle.read(30)
            if b"SIMPLE" not in signature:
                output.unlink(missing_ok=True)
                raise RuntimeError(
                    f"MAST response was not FITS (content-type={content_type!r}, signature={signature!r})"
                )
            return sha256(output), output.stat().st_size, "downloaded"
        except Exception as error:  # retries cover transient shared-service failures
            last_error = error
            output.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def header_value(header: fits.Header, key: str) -> Any:
    value = header.get(key)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def inspect_fits(path: Path, target: dict[str, Any], label: str) -> dict[str, Any]:
    with fits.open(path, memmap=False) as hdul:
        hdul.verify("exception")
        if hdul[0].data is None:
            raise ValueError(f"No primary image in {path}")
        data = np.asarray(hdul[0].data)
        header = hdul[0].header
        wcs = WCS(header)
        if not wcs.has_celestial:
            raise ValueError(f"No celestial WCS in {path}")
        if data.ndim != 2 or data.shape != (CUTOUT_SIZE_PIXELS, CUTOUT_SIZE_PIXELS):
            raise ValueError(f"Unexpected shape {data.shape} in {path}")

        center = wcs.pixel_to_world((data.shape[1] - 1) / 2, (data.shape[0] - 1) / 2)
        target_coord = SkyCoord(target["raDeg"], target["decDeg"], unit="deg")
        center_offset = float(center.separation(target_coord).arcsec)
        scales = np.abs(proj_plane_pixel_scales(wcs.celestial) * 3600.0)
        finite = np.isfinite(data)
        finite_values = data[finite]
        # The PS1 mask plane is sparse and inverted relative to the image planes:
        # NaN marks an unflagged pixel, and a finite value marks a flag. Typical
        # good cutouts are 99.85% NaN, so an all-NaN mask is a field with nothing
        # flagged, which is the best case rather than a failure. Treating it as
        # one rejected 40% of regions on the first pass through the 200-region set.
        if finite_values.size == 0 and label != "mask":
            raise ValueError(f"No finite pixels in {path}")

        stats: dict[str, Any] = {
            "finitePixelCount": int(finite.sum()),
            "finiteFraction": float(finite.mean()),
            "minimum": float(np.min(finite_values)) if finite_values.size else None,
            "maximum": float(np.max(finite_values)) if finite_values.size else None,
            "median": float(np.median(finite_values)) if finite_values.size else None,
        }
        if label == "weight":
            stats["negativePixelCount"] = int(np.sum(finite_values < 0))
            stats["positivePixelFraction"] = float(np.mean(finite_values > 0))
        if label == "mask":
            stats["maskConvention"] = "finite value = flagged pixel; NaN = unflagged"
            stats["flaggedPixelCount"] = int(finite.sum())
            stats["nonzeroPixelCount"] = int(np.count_nonzero(finite_values))
            stats["nonzeroPixelFractionOfAllPixels"] = float(
                np.count_nonzero(finite & (data != 0)) / data.size
            )
            stats["integerValued"] = bool(np.all(finite_values == np.floor(finite_values)))

        fpa_zero_point = header_value(header, "FPA.ZP")
        exposure_time = header_value(header, "EXPTIME")
        if label == "science":
            units_validation = {
                "interpretationVerified": bool(
                    fpa_zero_point is not None and exposure_time is not None
                ),
                "bunitHeaderPresent": "BUNIT" in header,
                "pixelUnit": "PS1 linearized stack data unit",
                "physicalFluxDensityUnit": None,
                "magnitudeCalibration": {
                    "zeroPointMag": fpa_zero_point,
                    "exposureTimeSeconds": exposure_time,
                    "formula": (
                        "MAG = -2.5*log10(sum(data-units)) + 25 + "
                        "2.5*log10(EXPTIME)"
                    ),
                },
                "note": (
                    "The MAST FITS-cutout service has already reversed the full-stack "
                    "asinh encoding. The header omits BUNIT, so these are calibrated PS1 "
                    "linear stack data units, not Jy or electrons."
                ),
            }
        elif label == "weight":
            units_validation = {
                "interpretationVerified": True,
                "bunitHeaderPresent": "BUNIT" in header,
                "pixelUnit": "variance of PS1 linearized stack data units",
                "physicalFluxDensityUnit": None,
                "note": (
                    "MAST defines stack.wt as propagated variance. Resampling of input "
                    "warps introduces covariance, so independent-pixel uncertainty is an "
                    "approximation."
                ),
            }
        else:
            mask_bits = [
                {
                    "name": str(header[f"MSKNAM{index:02d}"]).strip(),
                    "value": int(header[f"MSKVAL{index:02d}"]),
                }
                for index in range(int(header.get("MSKNUM", 0)))
                if f"MSKNAM{index:02d}" in header and f"MSKVAL{index:02d}" in header
            ]
            units_validation = {
                "interpretationVerified": bool(mask_bits),
                "bunitHeaderPresent": "BUNIT" in header,
                "pixelUnit": "dimensionless PS1 bitmask",
                "physicalFluxDensityUnit": None,
                "maskBits": mask_bits,
                "observedCutoutEncoding": (
                    "Finite pixels are non-zero integer flag codes; all other returned "
                    "pixels are NaN. Preserve finite/non-zero semantics downstream."
                ),
            }

        return {
            "fitsStructureValid": True,
            "hduCount": len(hdul),
            "shape": [int(data.shape[0]), int(data.shape[1])],
            "dtype": str(data.dtype),
            "wcsPresent": True,
            "celestialAxes": list(wcs.celestial.wcs.ctype),
            "radesys": header_value(header, "RADESYS"),
            "pixelScaleArcsec": [float(value) for value in scales],
            "targetCenterOffsetArcsec": center_offset,
            "bunit": header_value(header, "BUNIT"),
            "unitsValidation": units_validation,
            "photometricKeywords": {
                key: header_value(header, key)
                for key in ("BOFFSET", "BSOFTEN", "MAGZP", "ZPT_0000", "EXPTIME")
                if key in header
            },
            "stats": stats,
            "wcsHeader": {
                key: header_value(header, key)
                for key in (
                    "CTYPE1",
                    "CTYPE2",
                    "CRVAL1",
                    "CRVAL2",
                    "CRPIX1",
                    "CRPIX2",
                    "CDELT1",
                    "CDELT2",
                    "PC1_1",
                    "PC1_2",
                    "PC2_1",
                    "PC2_2",
                    "PC001001",
                    "PC001002",
                    "PC002001",
                    "PC002002",
                    "CD1_1",
                    "CD1_2",
                    "CD2_1",
                    "CD2_2",
                )
                if key in header
            },
        }


def compare_wcs(product_records: list[dict[str, Any]]) -> dict[str, Any]:
    paths = [ROOT / record["localPath"] for record in product_records]
    arrays: list[np.ndarray] = []
    wcs_objects: list[WCS] = []
    for path in paths:
        with fits.open(path, memmap=False) as hdul:
            arrays.append(np.asarray(hdul[0].data))
            wcs_objects.append(WCS(hdul[0].header).celestial)

    reference = wcs_objects[0]
    height, width = arrays[0].shape
    x = np.array([0.0, width - 1.0, (width - 1.0) / 2])
    y = np.array([0.0, height - 1.0, (height - 1.0) / 2])
    ref_world = reference.pixel_to_world(x, y)
    max_offsets: list[float] = []
    for wcs in wcs_objects[1:]:
        world = wcs.pixel_to_world(x, y)
        max_offsets.append(float(np.max(ref_world.separation(world).arcsec)))
    maximum = max(max_offsets, default=0.0)
    return {
        "sameShape": len({array.shape for array in arrays}) == 1,
        "maximumPlaneWcsOffsetArcsec": maximum,
        "planesPixelRegistered": maximum < 1e-5,
    }


def preview_region(region: dict[str, Any]) -> list[dict[str, str]]:
    arrays: dict[str, np.ndarray] = {}
    for record in region["products"]:
        with fits.open(ROOT / record["localPath"], memmap=False) as hdul:
            arrays[record["role"]] = np.asarray(hdul[0].data, dtype=float)

    out_dir = PREVIEW_ROOT / region["regionId"]
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[dict[str, str]] = []

    science = arrays["science"].copy()
    science[~np.isfinite(science)] = np.nan
    norm = ImageNormalize(science, interval=PercentileInterval(99.5), stretch=AsinhStretch())
    science_path = out_dir / "ps1-dr2-i-science.png"
    plt.imsave(science_path, norm(science), cmap="gray", origin="lower", vmin=0, vmax=1)
    outputs.append({"role": "science", "path": "/" + science_path.relative_to(ROOT / "public").as_posix()})

    weight = arrays["weight"].copy()
    weight[~np.isfinite(weight) | (weight <= 0)] = np.nan
    log_weight = np.log10(weight)
    weight_path = out_dir / "ps1-dr2-i-weight.png"
    plt.imsave(weight_path, log_weight, cmap="viridis", origin="lower")
    outputs.append({"role": "weight", "path": "/" + weight_path.relative_to(ROOT / "public").as_posix()})

    mask = arrays["mask"]
    mask_path = out_dir / "ps1-dr2-i-mask.png"
    flagged_mask = np.isfinite(mask) & (mask != 0)
    plt.imsave(mask_path, flagged_mask, cmap="magma", origin="lower", vmin=0, vmax=1)
    outputs.append({"role": "mask", "path": "/" + mask_path.relative_to(ROOT / "public").as_posix()})

    overlay_path = out_dir / "ps1-dr2-i-science-mask-overlay.png"
    fig, ax = plt.subplots(figsize=(6, 6), dpi=160)
    ax.imshow(science, cmap="gray", origin="lower", norm=norm)
    flagged = np.ma.masked_where(~np.isfinite(mask) | (mask == 0), mask)
    ax.imshow(flagged, cmap="autumn", alpha=0.42, origin="lower")
    ax.set_axis_off()
    fig.tight_layout(pad=0)
    fig.savefig(overlay_path, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    outputs.append({"role": "science-mask-overlay", "path": "/" + overlay_path.relative_to(ROOT / "public").as_posix()})
    return outputs


def discover_dynamic_targets(region_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(region_path.read_text(encoding="utf-8"))
    targets: list[dict[str, Any]] = []
    discovery_root = RESULT_ROOT / "evidence" / "discovery"
    discovery_root.mkdir(parents=True, exist_ok=True)
    for item in payload.get("regions", []):
        region_id = str(item["id"])
        center = item["center"]
        discovery = discovery_root / f"{region_id}-{BAND}.csv"
        if not discovery.is_file() or discovery.stat().st_size == 0:
            params = {
                "ra": f"{float(center[0]):.10f}",
                "dec": f"{float(center[1]):.10f}",
                "filters": BAND,
                "type": "stack,stack.wt,stack.mask",
                "sep": ",",
            }
            # Retried like the cutout downloads are. A single connection reset at
            # region 188 of 200 ended a whole run that had no way to resume past
            # it, and a shared archive resets connections as a matter of course.
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    response = requests.get(
                        FILENAME_SERVICE, params=params,
                        headers={"User-Agent": "Layers-Rubin-Light-Atlas/1.0 (bounded science cutouts)"},
                        timeout=(30, 120),
                    )
                    response.raise_for_status()
                    if "projcell" not in response.text or "filename" not in response.text:
                        raise RuntimeError(
                            f"Pan-STARRS discovery returned an unexpected response for {region_id}"
                        )
                    discovery.write_bytes(response.content)
                    last_error = None
                    break
                except Exception as error:
                    last_error = error
                    if attempt < 2:
                        time.sleep(2**attempt)
            if last_error is not None:
                # Recorded and skipped, not raised. One unreachable region must
                # not discard the 199 that resolved.
                print(f"[discovery-failed] {region_id}: {type(last_error).__name__}: {last_error}", flush=True)
                continue
        targets.append({
            "regionId": region_id,
            "tract": int(item["tract"]),
            "raDeg": float(center[0]),
            "decDeg": float(center[1]),
            "discovery": discovery,
        })
    if not targets:
        raise ValueError("Expected at least one region")
    return targets



def build_region(target: dict[str, Any], validated_at: str) -> dict[str, Any]:
    """Acquire and validate one region's three PS1 planes."""
    with target["discovery"].open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    selected = {
        row["type"]: row
        for row in rows
        if row["filter"] == BAND and row["type"] in PRODUCT_LABELS
    }
    if set(selected) != set(PRODUCT_LABELS):
        raise ValueError(
            f"Discovery for tract {target['tract']} lacks required planes: {sorted(selected)}"
        )

    products: list[dict[str, Any]] = []
    for product_type in ("stack", "stack.wt", "stack.mask"):
        row = selected[product_type]
        role = PRODUCT_LABELS[product_type]
        params = {
            "ra": f"{target['raDeg']:.8f}",
            "dec": f"{target['decDeg']:.8f}",
            "size": str(CUTOUT_SIZE_PIXELS),
            "format": "fits",
            "red": row["filename"],
        }
        request = requests.Request("GET", FITSCUT, params=params).prepare()
        url = request.url
        if not url:
            raise RuntimeError("Could not construct FITS-cutout URL")
        output = (
            FITS_ROOT
            / target["regionId"]
            / f"ps1-dr2-{BAND}-{role}-{CUTOUT_SIZE_PIXELS}px.fits"
        )
        digest, size, transfer = download(url, output)
        inspection = inspect_fits(output, target, role)
        products.append(
            {
                "role": role,
                "archiveProductType": product_type,
                "sourceSkycell": f"{int(row['projcell']):04d}.{int(row['subcell']):03d}",
                "sourceProductId": row["shortname"],
                "sourceFilename": row["filename"],
                "sourceUrl": url,
                "localPath": relative(output),
                "bytes": size,
                "sha256": digest,
                "transfer": transfer,
                "retrievedAt": datetime.fromtimestamp(
                    output.stat().st_mtime, timezone.utc
                ).isoformat(),
                "validatedAt": validated_at,
                "validation": inspection,
            }
        )
        time.sleep(0.5)

    plane_registration = compare_wcs(products)
    source_pixel_gate = bool(
        plane_registration["sameShape"]
        and plane_registration["planesPixelRegistered"]
        and all(
            product["validation"]["wcsPresent"]
            and product["validation"]["fitsStructureValid"]
            and product["validation"]["unitsValidation"]["interpretationVerified"]
            and product["validation"]["targetCenterOffsetArcsec"] < 1.0
            # A mask with no finite pixels has nothing flagged, so the coverage
            # requirement applies to the two image planes only.
            and (
                product["role"] == "mask"
                or product["validation"]["stats"]["finiteFraction"] > 0
            )
            for product in products
        )
        and products[1]["validation"]["stats"]["negativePixelCount"] == 0
        and products[2]["validation"]["stats"]["integerValued"]
    )
    region = {
        "regionId": target["regionId"],
        "tract": target["tract"],
        "center": {"raDeg": target["raDeg"], "decDeg": target["decDeg"]},
        "surveyId": "panstarrs-dr2",
        "release": "DR1 stack product served by the current PS1 archive",
        "catalogReleaseContext": "DR2",
        "band": BAND,
        "cutout": {
            "sizePixels": CUTOUT_SIZE_PIXELS,
            "nativePixelScaleArcsec": PIXEL_SCALE_ARCSEC,
            "nominalSizeArcmin": CUTOUT_SIZE_ARCMIN,
            "resampled": False,
        },
        "discovery": {
            "localPath": relative(target["discovery"]),
            "sha256": sha256(target["discovery"]),
            "rowCount": len(rows),
        },
        "products": products,
        "supportPlaneValidation": plane_registration,
        "sourcePixelsValidated": source_pixel_gate,
        "comparisonReady": False,
        "comparisonReadinessReason": (
            "Authentic PS1 stack and support pixels are validated, but no Rubin/PS1 "
            "common-grid reprojection, PSF matching, photometric normalization, or "
            "alignment QA has been performed."
        ),
    }
    region["previews"] = preview_region(region)
    return region


def build(targets: tuple[dict[str, Any], ...] | list[dict[str, Any]] = TARGETS) -> dict[str, Any]:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)
    validated_at = utc_now()
    detailed_regions: list[dict[str, Any]] = []
    failed_regions: list[dict[str, Any]] = []

    for target in targets:
        try:
            region = build_region(target, validated_at)
        except Exception as error:
            # One region's cutout must not end the run. A 200-region fetch died at
            # region 3 because a mask plane came back all-NaN, throwing away every
            # region behind it. A region that cannot be validated is a recorded
            # status; the manifest reports how many, so a quiet loss is impossible.
            failed_regions.append({
                "regionId": target["regionId"],
                "tract": target["tract"],
                "status": "acquisition-failed",
                "error": f"{type(error).__name__}: {error}",
            })
            print(f"[failed] {target['regionId']}: {type(error).__name__}: {error}", flush=True)
            continue
        detailed_regions.append(region)
        print(f"[ok] {region['regionId']}  sourcePixelsValidated={region['sourcePixelsValidated']}", flush=True)

    detailed = {
        "schemaVersion": "layers-panstarrs-gap-fill-evidence-v1",
        "generatedAt": utc_now(),
        "provider": "MAST Pan-STARRS",
        "service": FITSCUT,
        "documentation": [DOC_URL, STACK_DOC_URL, PRODUCT_DOC_URL],
        "method": (
            "Official PS1 filename discovery followed by native-scale FITS cutouts. "
            "The service supports stack, mask, and weight images and linearizes stack cutouts."
        ),
        "regions": detailed_regions,
        "failedRegions": failed_regions,
        "readiness": {
            "sourcePixelsValidatedCount": sum(
                bool(region["sourcePixelsValidated"]) for region in detailed_regions
            ),
            "acquisitionFailedCount": len(failed_regions),
            "comparisonReadyCount": 0,
            "alignmentQa": "not-run",
        },
        "limitations": [
            "FITS-cutout output is archive-authentic but is a bounded extraction, not the full skycell file.",
            "The science cutout is linearized by the MAST service; full-stack asinh-scaling keywords therefore do not apply to the returned pixel array.",
            "The weight plane is the PS1 propagated stack variance product; reprojection will introduce additional pixel covariance.",
            "The FITS headers omit BUNIT. Science pixels use the documented PS1 linear-stack photometric convention and must not be labeled Jy.",
            "PS1 stack images are DR1 image products even when accessed through the current archive alongside DR2 catalogs and warps.",
            "MAST documents small stack zero-point systematics and warns that non-PSF i-band magnitudes can have substantially larger errors; quantitative photometry needs its own QA.",
            "No Rubin/PS1 comparison result may be reported until common-grid, mask, PSF, and photometric QA passes.",
        ],
    }
    evidence_path = RESULT_ROOT / "evidence" / "manifest.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(detailed, indent=2) + "\n", encoding="utf-8")

    public_regions: list[dict[str, Any]] = []
    for region in detailed_regions:
        public_regions.append(
            {
                "regionId": region["regionId"],
                "tract": region["tract"],
                "center": region["center"],
                "surveyId": region["surveyId"],
                "release": region["release"],
                "catalogReleaseContext": region["catalogReleaseContext"],
                "band": region["band"],
                "cutout": region["cutout"],
                "products": [
                    {
                        "role": product["role"],
                        "archiveProductType": product["archiveProductType"],
                        "sourceSkycell": product["sourceSkycell"],
                        "sourceProductId": product["sourceProductId"],
                        "bytes": product["bytes"],
                        "sha256": product["sha256"],
                        "shape": product["validation"]["shape"],
                        "wcsPresent": product["validation"]["wcsPresent"],
                        "bunit": product["validation"]["bunit"],
                        "unitsValidation": product["validation"]["unitsValidation"],
                        "finiteFraction": product["validation"]["stats"]["finiteFraction"],
                    }
                    for product in region["products"]
                ],
                "previews": region["previews"],
                "sourcePixelsValidated": region["sourcePixelsValidated"],
                "comparisonReady": False,
                "comparisonReadinessReason": region["comparisonReadinessReason"],
            }
        )
    public = {
        "schemaVersion": "layers-panstarrs-gap-fill-public-v1",
        "generatedAt": detailed["generatedAt"],
        "provider": detailed["provider"],
        "documentation": detailed["documentation"],
        "regions": public_regions,
        "readiness": detailed["readiness"],
        "limitations": detailed["limitations"],
    }
    public_path = PUBLIC_ROOT / "manifest.json"
    public_path.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    return detailed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", type=Path, help="Optional one-or-more-region JSON; discovers PS1 products live.")
    parser.add_argument("--result-root", type=Path, default=RESULT_ROOT)
    parser.add_argument("--preview-root", type=Path, default=PREVIEW_ROOT)
    parser.add_argument("--public-root", type=Path, default=PUBLIC_ROOT)
    parser.add_argument("--band", default=BAND, choices=("g", "r", "i", "z", "y"),
                        help="PS1 filter. Use the band Rubin was observed in, or the "
                             "comparison measures colour rather than throughput.")
    args = parser.parse_args()
    BAND = args.band
    RESULT_ROOT = args.result_root
    FITS_ROOT = RESULT_ROOT / "fits"
    PREVIEW_ROOT = args.preview_root
    PUBLIC_ROOT = args.public_root
    selected_targets = discover_dynamic_targets(args.regions) if args.regions else list(TARGETS)
    result = build(selected_targets)
    print(
        json.dumps(
            {
                "regions": len(result["regions"]),
                "sourcePixelsValidated": result["readiness"]["sourcePixelsValidatedCount"],
                "comparisonReady": result["readiness"]["comparisonReadyCount"],
                "evidence": relative(RESULT_ROOT / "evidence" / "manifest.json"),
                "public": relative(PUBLIC_ROOT / "manifest.json"),
            },
            indent=2,
        )
    )
