#!/usr/bin/env python3
"""Fetch public SPARC data and real Spitzer/IRAC 3.6 µm image cutouts.

This does not fetch Rubin data. Rubin EDP2 remains an authenticated RSP step.
The output cache is intentionally gitignored because the source products are
large and should be reproduced from their recorded archive URLs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import astropy.units as u
import numpy as np
import pyvo
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.nddata import Cutout2D
from astropy.visualization import AsinhStretch, ImageNormalize, PercentileInterval
from astropy.wcs import WCS
from PIL import Image

SPARC_BASE = "https://astroweb.cwru.edu/SPARC/"
SPARC_FILES = (
    "SPARC_Lelli2016c.mrt",
    "MassModels_Lelli2016c.mrt",
    "sfb_LTG.zip",
    "Rotmod_LTG.zip",
    "MassModels_LTG.png.zip",
)
SEIP_SIA = "https://irsa.ipac.caltech.edu/SIA"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, path: Path):
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Rubin-Missing-Light-Atlas/0.1"})
    with urllib.request.urlopen(request, timeout=180) as response, path.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def read_targets(path: Path, only: set[str]):
    with path.open(newline="", encoding="utf-8") as handle:
        targets = list(csv.DictReader(handle))
    return [target for target in targets if not only or target["slug"] in only]


def extract_member(archive_path: Path, member: str, output_path: Path):
    with zipfile.ZipFile(archive_path) as archive:
        if member not in archive.namelist():
            raise FileNotFoundError(f"{member} not found in {archive_path.name}")
        output_path.write_bytes(archive.read(member))


def seip_products(service, ra: float, dec: float):
    table = service.search(pos=(ra, dec, 10 * u.arcsec), collection="spitzer_seip").to_table()
    suffixes = {
        "science": "IRAC.1.mosaic.fits",
        "uncertainty": "IRAC.1.unc.fits",
        "coverage": "IRAC.1.cov.fits",
    }
    products = {}
    rows = []
    seen_urls = set()
    for row in table:
        url = str(row["access_url"])
        if "short" in url or url in seen_urls:
            continue
        for kind, suffix in suffixes.items():
            if url.endswith(suffix):
                products[kind] = url
                seen_urls.add(url)
                rows.append({
                    "kind": kind,
                    "url": url,
                    "obsId": str(row["obs_id"]),
                    "publisherDid": str(row["obs_publisher_did"]),
                    "estimatedKbyte": int(row["access_estsize"]),
                    "bandpass": str(row["energy_bandpassname"]),
                })
    return products, rows, len(table)


def cutout_fits(source: Path, destination: Path, center: SkyCoord, width_arcmin: float):
    with fits.open(source, memmap=True) as hdul:
        data = np.asarray(hdul[0].data, dtype=np.float32)
        wcs = WCS(hdul[0].header)
        cutout = Cutout2D(
            data,
            position=center,
            size=(width_arcmin * u.arcmin, width_arcmin * u.arcmin),
            wcs=wcs,
            mode="partial",
            fill_value=np.nan,
        )
        header = hdul[0].header.copy()
        header.update(cutout.wcs.to_header())
        header["NAXIS1"] = cutout.data.shape[1]
        header["NAXIS2"] = cutout.data.shape[0]
        header["ATLSRA"] = center.ra.deg
        header["ATLSDEC"] = center.dec.deg
        header["ATLSWID"] = width_arcmin
        fits.PrimaryHDU(cutout.data.astype(np.float32), header=header).writeto(destination, overwrite=True, checksum=True)
        return float(np.isfinite(cutout.data).sum() / cutout.data.size)


def make_preview(source: Path, destination: Path):
    with fits.open(source) as hdul:
        data = np.asarray(hdul[0].data, dtype=np.float32)
    normalizer = ImageNormalize(data, interval=PercentileInterval(99.5), stretch=AsinhStretch(0.05), clip=True)
    pixels = np.flipud(np.nan_to_num(normalizer(data), nan=0.0))
    Image.fromarray(np.uint8(np.clip(pixels, 0, 1) * 255), mode="L").save(destination, optimize=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=Path, default=Path(__file__).with_name("targets.csv"))
    parser.add_argument("--cache", type=Path, default=Path(__file__).with_name("cache") / "legacy")
    parser.add_argument("--only", action="append", default=[], help="Target slug; repeat to select multiple")
    args = parser.parse_args()

    targets = read_targets(args.targets, set(args.only))
    sparc_dir = args.cache / "sparc"
    seip_dir = args.cache / "seip"
    sparc_dir.mkdir(parents=True, exist_ok=True)
    seip_dir.mkdir(parents=True, exist_ok=True)

    source_hashes = {}
    for filename in SPARC_FILES:
        path = sparc_dir / filename
        download(SPARC_BASE + filename, path)
        source_hashes[filename] = sha256(path)

    service = pyvo.dal.sia2.SIA2Service(SEIP_SIA)
    manifest = {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sparc": {
            "source": SPARC_BASE,
            "citation": "Lelli, McGaugh & Schombert (2016), AJ 152, 157",
            "sourceSha256": source_hashes,
        },
        "spitzer": {
            "collection": "spitzer_seip",
            "service": SEIP_SIA,
            "datasetDoi": "10.26131/IRSA433",
        },
        "targets": {},
    }

    for target in targets:
        slug = target["slug"]
        sparc_id = target["sparc_id"]
        output_dir = args.cache / "targets" / slug
        output_dir.mkdir(parents=True, exist_ok=True)
        extract_member(sparc_dir / "sfb_LTG.zip", f"{sparc_id}.sfb", output_dir / "sparc_surface_brightness.sfb")
        extract_member(sparc_dir / "Rotmod_LTG.zip", f"{sparc_id}_rotmod.dat", output_dir / "sparc_rotation_mass_model.dat")
        extract_member(sparc_dir / "MassModels_LTG.png.zip", f"{sparc_id}_MassModel.png", output_dir / "sparc_mass_model.png")

        ra = float(target["ra_deg"])
        dec = float(target["dec_deg"])
        width = float(target["field_width_arcmin"])
        products, rows, result_count = seip_products(service, ra, dec)
        target_record = {
            "sparcId": sparc_id,
            "center": {"raDeg": ra, "decDeg": dec},
            "fieldWidthArcmin": width,
            "sparcFiles": {
                path.name: sha256(path)
                for path in output_dir.glob("sparc_*")
            },
            "seipQueryResultCount": result_count,
            "seipProducts": rows,
            "seipCoverage": "covered" if "science" in products else "not-covered",
        }

        if "science" in products:
            center = SkyCoord(ra=ra, dec=dec, unit="deg")
            for kind, url in products.items():
                full_path = seip_dir / Path(url).name
                cutout_path = output_dir / f"spitzer_irac1_{kind}.fits"
                download(url, full_path)
                target_record.setdefault("coverageFraction", {})[kind] = cutout_fits(full_path, cutout_path, center, width)
                target_record["sparcFiles"][cutout_path.name] = sha256(cutout_path)
            preview = output_dir / "spitzer_irac1.png"
            make_preview(output_dir / "spitzer_irac1_science.fits", preview)
            target_record["sparcFiles"][preview.name] = sha256(preview)

        manifest["targets"][slug] = target_record
        print(f"[{slug}] SPARC ready; SEIP {target_record['seipCoverage']} ({result_count} SIA rows)")

    manifest_path = args.cache / "public-legacy-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {manifest_path}")


if __name__ == "__main__":
    main()
