#!/usr/bin/env python3
"""Attach position-matched DESI DR1 and SDSS DR17 spectra to Rubin fields."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pyvo


TAP_URL = "https://datalab.noirlab.edu/tap"
FIELD_RADIUS_DEG = 0.1


def scalar(value):
    if value is None:
        return None
    if hasattr(value, "mask") and value.mask:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    dra = (ra1 - ra2) * math.cos(math.radians((dec1 + dec2) / 2.0))
    return math.hypot(dra, dec1 - dec2) * 3600.0


def query_box(service: pyvo.dal.TAPService, table: str, columns: str, ra_col: str, dec_col: str, ra: float, dec: float, where: str) -> list[dict]:
    dra = FIELD_RADIUS_DEG / max(math.cos(math.radians(dec)), 0.2)
    query = f"""
        SELECT TOP 500 {columns}
        FROM {table}
        WHERE {ra_col} BETWEEN {ra - dra} AND {ra + dra}
          AND {dec_col} BETWEEN {dec - FIELD_RADIUS_DEG} AND {dec + FIELD_RADIUS_DEG}
          AND {where}
    """
    result = service.search(query)
    rows = [{name: scalar(row[name]) for name in result.fieldnames} for row in result]
    for row in rows:
        row["separationArcsec"] = separation_arcsec(float(row[ra_col]), float(row[dec_col]), ra, dec)
    return sorted((row for row in rows if row["separationArcsec"] <= FIELD_RADIUS_DEG * 3600), key=lambda row: row["separationArcsec"])


def unsigned_id(value: int) -> str:
    return str(value if value >= 0 else value + 2**64)


def spectrum_layer(survey: str, release: str, instrument: str, row: dict | None, field_count: int, association_radius: float, data_path: str, search_url: str) -> dict:
    direct = row is not None
    if survey == "DESI" and row:
        identity = str(row["targetid"])
        link = f"https://www.legacysurvey.org/viewer/desi-spectrum/dr1/targetid{identity}"
        facts = [
            {"label": "REDSHIFT", "value": f"{float(row['z']):.6f}", "unit": "z"},
            {"label": "CLASS", "value": str(row["spectype"]), "unit": "Redrock"},
            {"label": "OFFSET", "value": f"{float(row['separationArcsec']):.1f}", "unit": "arcsec"},
            {"label": "EXPOSURE", "value": f"{float(row['coadd_exptime']):.0f}", "unit": "seconds"},
        ]
    elif survey == "SDSS" and row:
        identity = unsigned_id(int(row["specobjid"]))
        link = f"https://skyserver.sdss.org/dr17/VisualTools/explore/summary?sid={identity}"
        facts = [
            {"label": "REDSHIFT", "value": f"{float(row['z']):.6f}", "unit": "z"},
            {"label": "CLASS", "value": str(row["class"]), "unit": "pipeline"},
            {"label": "OFFSET", "value": f"{float(row['separationArcsec']):.1f}", "unit": "arcsec"},
            {"label": "PLATE-MJD-FIBER", "value": f"{row['plate']}-{row['mjd']}-{row['fiberid']}", "unit": "SDSS"},
        ]
    else:
        identity = "none"
        link = search_url
        facts = [
            {"label": "FIELD SPECTRA", "value": str(field_count), "unit": "within 6 arcmin"},
            {"label": "TARGET MATCH", "value": "NONE", "unit": f"within {association_radius:.0f} arcsec"},
        ]
    return {
        "id": f"{survey.lower()}-{release.lower().replace(' ', '-')}-spectrum",
        "survey": survey,
        "release": release,
        "instrument": instrument,
        "kind": "spectrum",
        "availability": "published" if direct else "metadata-match",
        "renderMode": "plot" if direct else "metadata",
        "bands": ["360-982 nm"] if survey == "DESI" else ["optical spectrum"],
        "datasetCount": 1 if direct else 0,
        "datasetIds": [identity] if direct else [],
        "units": {"wavelength": "Angstrom", "flux": "survey-calibrated spectral flux", "redshift": "dimensionless"},
        "calibration": f"Published {survey} pipeline classification and redshift",
        "hasVariance": direct,
        "hasMask": direct,
        "hasWcs": True,
        "note": "A target-associated spectrum is linked only when a galaxy-classified fiber lies within the declared optical association radius." if direct else "Spectra exist in the field, but none is safely associated with the target galaxy; nearby background spectra are not substituted.",
        "scienceRole": "Redshift, distance consistency, emission-line activity, stellar population, and environmental context.",
        "provenance": {"service": "NOIRLab Astro Data Lab TAP", "table": f"{survey} public spectroscopic catalog"},
        "assets": {"data": data_path},
        "linkedEvidence": {
            "status": "target-spectrum" if direct else "field-only",
            "headline": f"{survey} target spectrum linked" if direct else f"No secure {survey} target spectrum",
            "summary": f"{field_count} primary {survey} spectra were found within the 6-arcminute Rubin field. " + ("The nearest galaxy-classified spectrum falls inside the target association radius." if direct else "None of the galaxy spectra falls inside the target association radius."),
            "facts": facts,
            "links": [{"label": "Open official spectrum" if direct else "Search official archive", "href": link}],
        },
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline/results/dp2-sparc-coverage.json")
    parser.add_argument("--output", type=Path, default=root / "pipeline/output/spectra-crossmatches")
    parser.add_argument("--public", type=Path, default=root / "public/data/layers/spectra-crossmatches")
    args = parser.parse_args()

    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    targets = [item for item in coverage["targets"] if int(item.get("deep_coadd_rows", 0)) > 0]
    service = pyvo.dal.TAPService(TAP_URL)
    created = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schemaVersion": 1,
        "adapterContract": "layers-external-layer-v1",
        "createdAt": created,
        "source": {"service": "NOIRLab Astro Data Lab TAP", "endpoint": TAP_URL},
        "targets": [],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    args.public.mkdir(parents=True, exist_ok=True)

    for target in targets:
        slug = target["slug"]
        ra, dec = float(target["ra_deg"]), float(target["dec_deg"])
        association_radius = max(12.0, min(180.0, float(target.get("major_axis_arcmin") or 0.5) * 45.0))
        desi = query_box(
            service,
            "desi_dr1.zpix",
            "targetid,mean_fiber_ra,mean_fiber_dec,z,zerr,zwarn,spectype,subtype,program,survey,coadd_exptime,healpix",
            "mean_fiber_ra",
            "mean_fiber_dec",
            ra,
            dec,
            "zcat_primary='t'",
        )
        sdss = query_box(
            service,
            "sdss_dr17.specobj",
            "specobjid,ra,dec,z,zerr,zwarning,class,subclass,plate,mjd,fiberid",
            "ra",
            "dec",
            ra,
            dec,
            "scienceprimary=1",
        )
        desi_match = next((row for row in desi if row.get("spectype") == "GALAXY" and row["separationArcsec"] <= association_radius and int(row.get("zwarn") or 0) == 0), None)
        sdss_match = next((row for row in sdss if row.get("class") == "GALAXY" and row["separationArcsec"] <= association_radius and int(row.get("zwarning") or 0) == 0), None)
        public_record = {
            "schemaVersion": 1,
            "product": "Layers spectroscopic cross-match",
            "createdAt": created,
            "targetId": slug,
            "center": {"raDeg": ra, "decDeg": dec, "frame": "ICRS"},
            "fieldRadiusArcmin": FIELD_RADIUS_DEG * 60,
            "associationRadiusArcsec": association_radius,
            "desi": {"fieldCount": len(desi), "targetMatch": desi_match, "nearest": desi[:10]},
            "sdss": {"fieldCount": len(sdss), "targetMatch": sdss_match, "nearest": sdss[:10]},
            "caveat": "Angular association is a reproducible candidate link, not a source-identity proof; redshift and morphology must agree before physical inference.",
        }
        record_path = args.public / f"{slug}.json"
        record_path.write_text(json.dumps(public_record, indent=2), encoding="utf-8")
        data_path = f"/data/layers/spectra-crossmatches/{slug}.json"
        search_url = f"https://skyserver.sdss.org/dr17/VisualTools/navi?ra={ra:.8f}&dec={dec:.8f}"
        layers = [
            spectrum_layer("DESI", "DR1", "DESI 5000-fiber spectrograph", desi_match, len(desi), association_radius, data_path, "https://data.desi.lbl.gov/doc/releases/dr1/"),
            spectrum_layer("SDSS", "DR17", "SDSS optical spectrographs", sdss_match, len(sdss), association_radius, data_path, search_url),
        ]
        manifest["targets"].append({"targetId": slug, "layers": layers})
        print(f"[{slug}] DESI target={'yes' if desi_match else 'no'} ({len(desi)} field); SDSS target={'yes' if sdss_match else 'no'} ({len(sdss)} field)")

    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Complete: linked DESI/SDSS evidence for {len(targets)} Rubin fields")


if __name__ == "__main__":
    main()
