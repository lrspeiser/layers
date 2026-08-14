#!/usr/bin/env python3
"""Fetch one validated, bounded SDSS DR19 optical spectrum for a Rubin tract.

The DR19 ALLSPEC table is the discovery authority.  Its ``sas_url`` column is
the release-owned address for the spectrum itself, so this connector does not
guess SAS paths from plate/fiber metadata.  A tract with no optical spectrum
inside the bounded cone is a successful ``none`` result, not an acquisition
error.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests
from astropy.io import fits


SKYSERVER_SQL = "https://skyserver.sdss.org/dr19/SkyServerWS/SearchTools/SqlSearch"
ALLSPEC_RELEASE = "DR19 ALLSPEC 1.0.1"
MAX_ROWS = 250
ALLOWED_SAS_HOSTS = {
    "data.sdss.org",
    "dr17.sdss.org",
    "dr18.sdss.org",
    "dr19.sdss.org",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    cosine = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def sql_ra_predicate(ra_deg: float, radius_deg: float, dec_deg: float) -> str:
    half_width = min(180.0, radius_deg / max(0.05, math.cos(math.radians(dec_deg))))
    low = ra_deg - half_width
    high = ra_deg + half_width
    if low < 0:
        return f"(ra >= {low + 360:.9f} OR ra <= {high:.9f})"
    if high >= 360:
        return f"(ra >= {low:.9f} OR ra <= {high - 360:.9f})"
    return f"ra BETWEEN {low:.9f} AND {high:.9f}"


def build_query(ra_deg: float, dec_deg: float, radius_arcmin: float) -> str:
    radius_deg = radius_arcmin / 60.0
    dec_low = max(-90.0, dec_deg - radius_deg)
    dec_high = min(90.0, dec_deg + radius_deg)
    distance = (
        f"POWER((ra - ({ra_deg:.9f})) * COS(RADIANS({dec_deg:.9f})), 2) + "
        f"POWER(dec - ({dec_deg:.9f}), 2)"
    )
    return f"""
SELECT TOP {MAX_ROWS}
  allspec_id, sdss_phase, instrument, plate_or_fps_field, mjd, run2d,
  coadd, sas_file, sas_url, ra, dec, specobjid
FROM allspec
WHERE instrument IN ('boss', 'sdss')
  AND sas_file LIKE 'spec-%'
  AND sas_url IS NOT NULL
  AND sas_url <> ''
  AND {sql_ra_predicate(ra_deg, radius_deg, dec_deg)}
  AND dec BETWEEN {dec_low:.9f} AND {dec_high:.9f}
ORDER BY {distance}
""".strip()


def query_allspec(query: str, cache_path: Path, refresh: bool) -> list[dict[str, str]]:
    if not cache_path.exists() or refresh:
        response = requests.get(
            SKYSERVER_SQL,
            params={"cmd": query, "format": "csv"},
            timeout=120,
            headers={"User-Agent": "Layers-Rubin-DP2/1.0 bounded-SDSS-spectrum"},
        )
        response.raise_for_status()
        if response.headers.get("content-type", "").lower().startswith("application/json"):
            raise RuntimeError(f"SkyServer rejected the ALLSPEC query: {response.text[:500]}")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
    text = cache_path.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    if not lines:
        return []
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def choose_candidate(rows: list[dict[str, str]], ra_deg: float, dec_deg: float, radius_arcmin: float) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        try:
            row_ra = float(row["ra"])
            row_dec = float(row["dec"])
        except (KeyError, TypeError, ValueError):
            continue
        separation = angular_separation_deg(ra_deg, dec_deg, row_ra, row_dec) * 60.0
        if not math.isfinite(separation) or separation > radius_arcmin:
            continue
        coadd = (row.get("coadd") or "").strip().lower()
        instrument = (row.get("instrument") or "").strip().lower()
        priority = (
            0 if coadd == "daily" else 1 if coadd not in {"epoch", "allepoch"} else 2,
            0 if instrument == "boss" else 1,
            separation,
        )
        candidates.append({**row, "separationArcmin": separation, "_priority": priority})
    if not candidates:
        return None
    selected = min(candidates, key=lambda item: item["_priority"])
    selected.pop("_priority", None)
    return selected


def safe_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    return str(value).strip()


def scalar(row: Any, name: str, default: Any = None) -> Any:
    names = set(row.array.names or [])
    if name not in names:
        return default
    value = row[name]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bytes):
        return safe_text(value)
    if isinstance(value, float) and not math.isfinite(value):
        return default
    return value


def download_spectrum(url: str, destination: Path, refresh: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SAS_HOSTS:
        raise RuntimeError("ALLSPEC returned a spectrum URL outside the official SDSS SAS hosts")
    if destination.exists() and destination.stat().st_size > 2_880 and not refresh:
        return
    response = requests.get(
        url,
        timeout=180,
        allow_redirects=True,
        headers={"User-Agent": "Layers-Rubin-DP2/1.0 bounded-SDSS-spectrum"},
    )
    response.raise_for_status()
    final = urlparse(response.url)
    if final.scheme != "https" or final.hostname not in ALLOWED_SAS_HOSTS:
        raise RuntimeError("SDSS spectrum download redirected outside official SAS hosts")
    if not response.content.startswith(b"SIMPLE"):
        raise RuntimeError("SDSS SAS response is not a FITS spectrum")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)


def validate_and_export(fits_path: Path, csv_path: Path, preview_path: Path) -> dict[str, Any]:
    with fits.open(fits_path, memmap=False, checksum=True) as hdus:
        if "COADD" not in hdus:
            raise RuntimeError("SDSS spectrum has no COADD table")
        table = hdus["COADD"].data
        names = set(table.names or [])
        required = {"FLUX", "LOGLAM", "IVAR", "AND_MASK", "OR_MASK"}
        if not required.issubset(names):
            raise RuntimeError(f"SDSS COADD table is missing {sorted(required - names)}")
        flux = np.asarray(table["FLUX"], dtype=float)
        wavelength = np.power(10.0, np.asarray(table["LOGLAM"], dtype=float))
        ivar = np.asarray(table["IVAR"], dtype=float)
        and_mask = np.asarray(table["AND_MASK"], dtype=np.int64)
        or_mask = np.asarray(table["OR_MASK"], dtype=np.int64)
        model = np.asarray(table["MODEL"], dtype=float) if "MODEL" in names else np.full_like(flux, np.nan)
        valid = np.isfinite(wavelength) & np.isfinite(flux) & (wavelength > 0)
        if valid.sum() < 100 or not np.all(np.diff(wavelength[valid]) > 0):
            raise RuntimeError("SDSS spectrum does not contain a usable monotonic wavelength series")
        spall = hdus["SPALL"].data[0] if "SPALL" in hdus and len(hdus["SPALL"].data) else None
        coadd_header = hdus["COADD"].header
        flux_unit = safe_text(coadd_header.get("TUNIT1", "1e-17 erg s-1 cm-2 Angstrom-1"))

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["wavelength_angstrom", "flux", "inverse_variance", "and_mask", "or_mask", "model"])
        for values in zip(wavelength, flux, ivar, and_mask, or_mask, model, strict=True):
            writer.writerow(values)

    finite_flux = flux[valid]
    low, high = np.nanpercentile(finite_flux, [1, 99])
    if not math.isfinite(low) or not math.isfinite(high) or low == high:
        low, high = float(np.nanmin(finite_flux)), float(np.nanmax(finite_flux))
    pad = max((high - low) * 0.12, 1e-6)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 4.5), dpi=150)
    fig.patch.set_facecolor("#07100d")
    ax.set_facecolor("#07100d")
    ax.plot(wavelength, flux, color="#d9e5df", linewidth=0.55, alpha=0.82, label="SDSS flux")
    if np.isfinite(model).sum() > 100:
        ax.plot(wavelength, model, color="#ff8e78", linewidth=0.9, alpha=0.9, label="pipeline model")
    ax.set_xlim(float(np.nanmin(wavelength[valid])), float(np.nanmax(wavelength[valid])))
    ax.set_ylim(low - pad, high + pad)
    ax.set_xlabel("Observed wavelength (Angstrom)", color="#b7c3bd")
    ax.set_ylabel(f"Flux ({flux_unit})", color="#b7c3bd")
    ax.tick_params(colors="#9fb2a9", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#46544e")
    ax.grid(color="#33443c", alpha=0.35, linewidth=0.45)
    ax.legend(frameon=False, labelcolor="#d9e5df", fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(preview_path, facecolor=fig.get_facecolor())
    plt.close(fig)

    metadata = {
        "samples": int(len(wavelength)),
        "validFluxSamples": int(valid.sum()),
        "positiveInverseVarianceSamples": int(np.count_nonzero(np.isfinite(ivar) & (ivar > 0))),
        "maskedSamples": int(np.count_nonzero(or_mask != 0)),
        "wavelengthRangeAngstrom": [float(np.nanmin(wavelength[valid])), float(np.nanmax(wavelength[valid]))],
        "wavelengthUnit": "Angstrom",
        "fluxUnit": flux_unit,
        "objectClass": scalar(spall, "CLASS", "unknown") if spall is not None else "unknown",
        "objectSubclass": scalar(spall, "SUBCLASS", "") if spall is not None else "",
        "redshift": scalar(spall, "Z") if spall is not None else None,
        "redshiftError": scalar(spall, "Z_ERR") if spall is not None else None,
        "redshiftWarning": scalar(spall, "ZWARNING") if spall is not None else None,
        "fieldQuality": scalar(spall, "FIELDQUALITY", "unknown") if spall is not None else "unknown",
    }
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--regions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--radius-arcmin", type=float, default=60.0)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if not (0 < args.radius_arcmin <= 90):
        raise SystemExit("--radius-arcmin must be in (0, 90]")

    region_document = json.loads(args.regions.read_text(encoding="utf-8"))
    regions = region_document.get("regions", [])
    if not regions:
        raise SystemExit("No regions were supplied")
    args.output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for region in regions:
        region_id = str(region["id"])
        tract = int(region["tract"])
        ra_deg, dec_deg = map(float, region["center"])
        query = build_query(ra_deg, dec_deg, args.radius_arcmin)
        query_path = args.output / "cache" / f"{region_id}-allspec.csv"
        rows = query_allspec(query, query_path, args.refresh)
        candidate = choose_candidate(rows, ra_deg, dec_deg, args.radius_arcmin)
        common = {
            "regionId": region_id,
            "tract": tract,
            "center": [ra_deg, dec_deg],
            "radiusArcmin": args.radius_arcmin,
            "release": ALLSPEC_RELEASE,
            "queryReturnedRowCount": len(rows),
            "queryMaxRows": MAX_ROWS,
        }
        if candidate is None:
            records.append({
                **common,
                "status": "none",
                "reason": "No SDSS optical spec file was found inside the bounded tract-center cone.",
                "caveats": [
                    "A none result applies only to this bounded cone, not to the entire SDSS release.",
                    "ALLSPEC positional support elsewhere in the Rubin tract is tracked independently.",
                ],
            })
            continue

        product_dir = args.output / "products" / region_id
        fits_name = Path(urlparse(candidate["sas_url"]).path).name
        fits_path = product_dir / fits_name
        csv_path = product_dir / f"{Path(fits_name).stem}-samples.csv"
        preview_path = product_dir / f"{Path(fits_name).stem}-preview.png"
        download_spectrum(candidate["sas_url"], fits_path, args.refresh)
        metadata = validate_and_export(fits_path, csv_path, preview_path)
        records.append({
            **common,
            "status": "available",
            "readiness": "spectrum-evidence",
            "scienceReady": True,
            "selection": {
                "allspecId": candidate["allspec_id"],
                "instrument": candidate["instrument"],
                "sdssPhase": int(candidate["sdss_phase"]),
                "coadd": candidate.get("coadd") or "unspecified",
                "run2d": candidate.get("run2d") or "unspecified",
                "field": int(candidate["plate_or_fps_field"]),
                "mjd": int(candidate["mjd"]),
                "raDeg": float(candidate["ra"]),
                "decDeg": float(candidate["dec"]),
                "separationArcmin": float(candidate["separationArcmin"]),
                "sasFile": candidate["sas_file"],
                "sourceUrl": candidate["sas_url"],
            },
            "spectrum": metadata,
            "artifacts": {
                "fits": {"localPath": str(fits_path.resolve()), "bytes": fits_path.stat().st_size, "sha256": sha256(fits_path)},
                "samplesCsv": {"localPath": str(csv_path.resolve()), "bytes": csv_path.stat().st_size, "sha256": sha256(csv_path)},
                "preview": {"localPath": str(preview_path.resolve()), "bytes": preview_path.stat().st_size, "sha256": sha256(preview_path)},
            },
            "caveats": [
                "This is a one-dimensional SDSS spectrum, not an image layer or a pixel-by-pixel Rubin difference.",
                "The nearest preferred optical coadd is not automatically associated with a particular Rubin source.",
                "Redshift and classification values are pipeline outputs and must retain their warning and quality fields.",
            ],
        })

    manifest = {
        "schemaVersion": "layers-sdss-spectrum-tract-v1",
        "generatedAt": utc_now(),
        "discoveryAuthority": {
            "survey": "Sloan Digital Sky Survey",
            "release": ALLSPEC_RELEASE,
            "table": "allspec",
            "endpoint": SKYSERVER_SQL,
            "documentation": "https://www.sdss.org/dr19/data_access/allspec/",
        },
        "regions": records,
        "counts": {
            "regions": len(records),
            "available": sum(item["status"] == "available" for item in records),
            "none": sum(item["status"] == "none" for item in records),
        },
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
