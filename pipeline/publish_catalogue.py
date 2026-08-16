#!/usr/bin/env python3
"""Publish the source catalogue: bulk files, spatial tiles, and a column dictionary.

The catalogue was held back while its access terms were undecided. They are
decided, so this puts it where people can actually use it, in the three forms
they actually want:

* **Parquet** for bulk work -- 50,233 rows, columnar, one download.
* **VOTable, gzipped** for anyone whose tooling speaks IVOA. TOPCAT, pyvo and
  astroquery open it without being told how.
* **Spatial tiles** so a cone search reads only the sky it was asked about. A
  serverless route cannot hold 50,000 rows in memory per request, and shipping
  the whole table to answer "what is near this position" would be rude to the
  reader's bandwidth and slow besides.

Tiles are one-degree bins in right ascension and declination. That is coarser
than HEALPix and needs no extra dependency, and for a survey of 3.4 arcminute
cutouts a one-degree cell is the right granularity: a cone search of any sane
radius touches one or a few files.

A column dictionary ships beside them. A catalogue whose columns are not
described is a catalogue nobody can check, and three of these columns are
significances that mean different things -- the difference between using the
right one and the wrong one is a factor of fourteen in how many sources look
anomalous.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import shutil
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

warnings.filterwarnings("ignore")

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "pipeline/results/source-catalogue"
DEFAULT_PUBLIC = ROOT / "public/data/catalogue"
DEFAULT_SUMMARY = ROOT / "public/data/layers/selected-regions/catalogue-release.json"

# Columns the tiles carry. The full table is in the bulk files; a cone search
# wants enough to decide whether to download more, not everything.
TILE_COLUMNS = [
    "source_id", "region_id", "ra_deg", "dec_deg",
    "rubin_flux_njy", "rubin_flux_err_njy", "reference_flux_njy",
    "rubin_mag_ab", "reference_mag_ab", "rubin_snr",
    "flux_ratio", "departure_significance",
    "flag_near_edge", "flag_negative_reference", "flag_blended",
]

COLUMN_DICTIONARY: dict[str, dict[str, str]] = {
    "source_id": {"unit": "", "ucd": "meta.id;meta.main", "description": "Region id and segment label."},
    "region_id": {"unit": "", "ucd": "meta.id", "description": "Which reconciled region this source was measured in."},
    "ra_deg": {"unit": "deg", "ucd": "pos.eq.ra;meta.main", "description": "ICRS right ascension of the Rubin centroid."},
    "dec_deg": {"unit": "deg", "ucd": "pos.eq.dec;meta.main", "description": "ICRS declination of the Rubin centroid."},
    "x_pixel": {"unit": "pix", "ucd": "pos.cartesian.x", "description": "Centroid column in the reconciled frame."},
    "y_pixel": {"unit": "pix", "ucd": "pos.cartesian.y", "description": "Centroid row in the reconciled frame."},
    "area_pixels": {"unit": "pix2", "ucd": "phys.area", "description": "Segment area."},
    "semimajor_arcsec": {"unit": "arcsec", "ucd": "phys.size.smajAxis", "description": "Second-moment semimajor axis."},
    "semiminor_arcsec": {"unit": "arcsec", "ucd": "phys.size.sminAxis", "description": "Second-moment semiminor axis."},
    "ellipticity": {"unit": "", "ucd": "src.ellipticity",
                    "description": "1 minus the ratio of second-moment semiminor to semimajor axis. Zero is round; a PSF-like source sits near zero and an edge-on disc near one."},
    "rubin_flux_njy": {"unit": "nJy", "ucd": "phot.flux", "description": "Rubin segment flux on the shared segmentation."},
    "rubin_flux_err_njy": {"unit": "nJy", "ucd": "stat.error;phot.flux",
                           "description": "Background-RMS error only. Excludes source Poisson noise; see departure_significance."},
    "reference_flux_njy": {"unit": "nJy", "ucd": "phot.flux", "description": "Reference segment flux on the same pixels."},
    "reference_flux_err_njy": {"unit": "nJy", "ucd": "stat.error;phot.flux", "description": "As above, for the reference frame."},
    "difference_flux_njy": {"unit": "nJy", "ucd": "phot.flux", "description": "Segment flux of the difference plane."},
    "rubin_mag_ab": {"unit": "mag", "ucd": "phot.mag", "description": "AB magnitude from rubin_flux_njy; NaN when flux is non-positive."},
    "reference_mag_ab": {"unit": "mag", "ucd": "phot.mag", "description": "AB magnitude from reference_flux_njy."},
    "difference_significance": {"unit": "", "ucd": "stat.snr",
                                "description": "Difference over combined propagated error. Dominated by the ~7% Rubin offset; flags most bright sources. Not the column to cut on."},
    "flux_ratio": {"unit": "", "ucd": "arith.ratio", "description": "rubin_flux_njy / reference_flux_njy."},
    "field_flux_ratio": {"unit": "", "ucd": "arith.ratio", "description": "Median flux ratio of this source's field."},
    "expected_rubin_flux_njy": {"unit": "nJy", "ucd": "phot.flux", "description": "reference_flux_njy scaled by field_flux_ratio."},
    "departure_njy": {"unit": "nJy", "ucd": "phot.flux", "description": "rubin_flux_njy minus expected_rubin_flux_njy."},
    "departure_significance_propagated": {"unit": "", "ucd": "stat.snr",
                                          "description": "Departure over propagated error. Understates uncertainty on bright sources; kept for comparison."},
    "log_flux_ratio": {"unit": "", "ucd": "arith.ratio", "description": "log10 of flux_ratio."},
    "field_log_ratio_median": {"unit": "", "ucd": "stat.median", "description": "Median log flux ratio of this source's field."},
    "field_log_ratio_scatter": {"unit": "", "ucd": "stat.stdev", "description": "Robust scatter of log flux ratio in this field."},
    "departure_significance": {"unit": "", "ucd": "stat.snr",
                               "description": "USE THIS ONE. Distance from the field's median log flux ratio in units of that field's own measured scatter. No error model to be wrong."},
    "rubin_snr": {"unit": "", "ucd": "stat.snr", "description": "rubin_flux_njy over its background-RMS error."},
    "reference_snr": {"unit": "", "ucd": "stat.snr", "description": "As above, for the reference."},
    "flag_near_edge": {"unit": "", "ucd": "meta.code.qual", "description": "Centroid within 10 pixels of the frame edge."},
    "flag_negative_reference": {"unit": "", "ucd": "meta.code.qual", "description": "Reference flux is non-positive; ratios are undefined."},
    "flag_blended": {"unit": "", "ucd": "meta.code.qual", "description": "Segment larger than 500 pixels; deblending may have merged neighbours."},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tile_key(ra: float, dec: float) -> str:
    return f"{int(math.floor(ra)):03d}_{int(math.floor(dec)):+03d}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()

    parquet_source = args.source / "rubin-reference-sources.parquet"
    votable_source = args.source / "rubin-reference-sources.vot"
    if not parquet_source.is_file():
        raise SystemExit("run build_source_catalogue.py first")

    args.public.mkdir(parents=True, exist_ok=True)
    parquet_public = args.public / "rubin-reference-sources.parquet"
    shutil.copyfile(parquet_source, parquet_public)

    votable_public = args.public / "rubin-reference-sources.vot.gz"
    if votable_source.is_file():
        # Gzipped: 53.7 MB of XML becomes 12.4 MB, and every HTTP client
        # decompresses it transparently.
        with votable_source.open("rb") as handle, gzip.open(votable_public, "wb", compresslevel=6) as out:
            shutil.copyfileobj(handle, out)

    table = pq.read_table(parquet_source)
    columns = {name: table.column(name).to_pylist() for name in TILE_COLUMNS if name in table.column_names}
    count = len(columns["ra_deg"])

    tiles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped_without_position = 0
    for index in range(count):
        row = {}
        for name, values in columns.items():
            value = values[index]
            if isinstance(value, float):
                if not np.isfinite(value):
                    value = None
                else:
                    value = round(value, 6)
            row[name] = value
        # Key on the *stored* position, not the raw one. Rounding to six decimals
        # can carry a value across a degree boundary -- RA 2.9999996 is written as
        # 3.000000 -- and a tile whose name disagrees with the coordinates inside
        # it sends a cone search to the wrong file.
        ra, dec = row.get("ra_deg"), row.get("dec_deg")
        if ra is None or dec is None:
            skipped_without_position += 1
            continue
        tiles[tile_key(ra, dec)].append(row)

    tile_dir = args.public / "tiles"
    if tile_dir.exists():
        shutil.rmtree(tile_dir)
    tile_dir.mkdir(parents=True, exist_ok=True)
    for key, rows in tiles.items():
        (tile_dir / f"{key}.json").write_text(
            json.dumps({"tile": key, "sources": rows}, separators=(",", ":")) + "\n", encoding="utf-8"
        )

    tile_bytes = sum(p.stat().st_size for p in tile_dir.glob("*.json"))
    tiled_rows = sum(len(v) for v in tiles.values())
    summary_source = json.loads(
        (ROOT / "public/data/layers/selected-regions/source-catalogue.json").read_text(encoding="utf-8")
    )
    reliability_path = ROOT / "public/data/layers/selected-regions/catalogue-reliability.json"
    reliability = json.loads(reliability_path.read_text(encoding="utf-8")) if reliability_path.is_file() else {}

    payload = {
        "schemaVersion": "layers-catalogue-release-v1",
        "generatedAt": utc_now(),
        "published": True,
        "rows": tiled_rows,
        "rowsInBulkFiles": count,
        "skippedWithoutPosition": skipped_without_position,
        "regions": summary_source.get("counts", {}).get("regions"),
        "files": {
            "parquet": {
                "path": "/data/catalogue/rubin-reference-sources.parquet",
                "bytes": parquet_public.stat().st_size,
                "sha256": sha256(parquet_public),
                "use": "bulk analysis; read with pandas, polars or pyarrow",
            },
            "votableGzip": {
                "path": "/data/catalogue/rubin-reference-sources.vot.gz",
                "bytes": votable_public.stat().st_size if votable_public.is_file() else None,
                "sha256": sha256(votable_public) if votable_public.is_file() else None,
                "use": "opens directly in TOPCAT, pyvo and astroquery",
            } if votable_public.is_file() else None,
        },
        "coneSearch": {
            "endpoint": "/api/scs",
            "protocol": "IVOA Simple Cone Search",
            "tiles": {
                "root": "/data/catalogue/tiles",
                "scheme": "one-degree bins in RA and Dec, named RRR_sDD",
                "count": len(tiles),
                "totalBytes": tile_bytes,
                "why": (
                    "A serverless route cannot hold 50,000 rows per request, and sending the whole "
                    "table to answer a positional query wastes the reader's bandwidth. A cone of "
                    "any sane radius touches one or a few tiles."
                ),
            },
        },
        "reliability": {
            "median90PercentCompleteMagAB": reliability.get("counts", {}).get("median90PercentCompleteMagAB"),
            "falsePositiveRate": reliability.get("counts", {}).get("falsePositiveRate"),
            "note": (
                "Multiply a flagged count by the false-positive rate to estimate how many are "
                "noise. The remainder is an upper bound on what could be astrophysical, not a "
                "count of it."
            ),
        },
        "whichSignificance": (
            "Cut on departure_significance. It measures distance from the field's own median flux "
            "ratio in units of that field's own scatter, so no error model can be wrong. "
            "difference_significance is dominated by the roughly 7% offset between Rubin and these "
            "references and flags most bright sources; "
            "departure_significance_propagated divides by an error that omits source Poisson noise."
        ),
        "caveat": (
            "A large departure is not a detection. The filter colour term is measured at -0.080 "
            "mag per mag of Rubin g-r against DECam and +0.007 against Pan-STARRS, linear to under "
            "4 millimagnitudes, so colour alone moves a source very little. What remains "
            "unexplained is the field-to-field scatter, forty times larger than the filters permit."
        ),
        "columns": COLUMN_DICTIONARY,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"parquet  {parquet_public.stat().st_size / 1e6:6.1f} MB")
    if votable_public.is_file():
        print(f"votable  {votable_public.stat().st_size / 1e6:6.1f} MB (gzip)")
    print(f"tiles    {tile_bytes / 1e6:6.1f} MB across {len(tiles)} files")
    print(f"rows     {count}")
    print(f"wrote {display_path(args.summary)}")


if __name__ == "__main__":
    main()
