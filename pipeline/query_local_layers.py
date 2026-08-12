#!/usr/bin/env python3
"""Query the read-only local Layers SQLite index by target or sky position."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path


def angular_distance_arcmin(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    ra1r, dec1r, ra2r, dec2r = map(math.radians, (ra1, dec1, ra2, dec2))
    cosine = math.sin(dec1r) * math.sin(dec2r) + math.cos(dec1r) * math.cos(dec2r) * math.cos(ra1r - ra2r)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine)))) * 60.0


def target_rows(connection: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    if args.target:
        return connection.execute(
            "SELECT * FROM targets WHERE lower(target_key)=lower(?) OR lower(name)=lower(?) ORDER BY target_key",
            (args.target, args.target),
        ).fetchall()
    if args.ra is None or args.dec is None:
        raise SystemExit("Provide --target or both --ra and --dec.")
    radius_deg = args.radius_arcmin / 60.0
    cos_dec = max(math.cos(math.radians(args.dec)), 0.1)
    ra_radius = radius_deg / cos_dec
    candidates = connection.execute(
        """
        SELECT targets.* FROM target_sky_index
        JOIN targets USING (id)
        WHERE min_ra <= ? AND max_ra >= ? AND min_dec <= ? AND max_dec >= ?
        """,
        (args.ra + ra_radius, args.ra - ra_radius, args.dec + radius_deg, args.dec - radius_deg),
    ).fetchall()
    return [row for row in candidates if angular_distance_arcmin(args.ra, args.dec, row["ra_deg"], row["dec_deg"]) <= args.radius_arcmin]


def assemble(connection: sqlite3.Connection, target: sqlite3.Row, layer_filter: str | None) -> dict:
    query = "SELECT * FROM layers WHERE target_id=?"
    parameters: list[object] = [target["id"]]
    if layer_filter:
        query += " AND lower(layer_key)=lower(?)"
        parameters.append(layer_filter)
    query += " ORDER BY layer_key"
    layers = []
    for row in connection.execute(query, parameters):
        datasets = [dict(item) for item in connection.execute(
            "SELECT dataset_id, obs_id, band, local_path, bytes, sha256 FROM datasets WHERE target_id=? AND layer_key=? ORDER BY band, dataset_id",
            (target["id"], row["layer_key"]),
        )]
        products = [dict(item) for item in connection.execute(
            "SELECT band, science_coverage, valid_pixel_fraction, mosaic_path, mosaic_sha256, preview_path, preview_sha256 FROM products WHERE target_id=? AND layer_key=? ORDER BY band",
            (target["id"], row["layer_key"]),
        )]
        layers.append(
            {
                "id": row["layer_key"],
                "survey": row["survey"],
                "release": row["release"],
                "kind": row["kind"],
                "availability": row["availability"],
                "calibration": row["calibration"],
                "datasets": datasets,
                "products": products,
            }
        )
    comparisons = [json.loads(row["record_json"]) for row in connection.execute(
        "SELECT record_json FROM comparisons WHERE target_id=? ORDER BY comparison_key",
        (target["id"],),
    )]
    return {
        "id": target["target_key"],
        "name": target["name"],
        "center": {"raDeg": target["ra_deg"], "decDeg": target["dec_deg"]},
        "fieldWidthArcmin": target["field_width_arcmin"],
        "layers": layers,
        "comparisons": comparisons,
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=root / "pipeline" / "output" / "layers.sqlite")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--target", help="Target key or SPARC name, for example ugc00891 or UGC00891")
    selector.add_argument("--ra", type=float, help="ICRS right ascension in degrees")
    parser.add_argument("--dec", type=float, help="ICRS declination in degrees; required with --ra")
    parser.add_argument("--radius-arcmin", type=float, default=6.0)
    parser.add_argument("--layer", help="Optional exact layer id filter")
    args = parser.parse_args()
    if args.ra is not None and args.dec is None:
        parser.error("--dec is required with --ra")
    if not args.database.is_file():
        raise SystemExit(f"Local layer store does not exist: {args.database}")

    uri = f"{args.database.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        records = [assemble(connection, target, args.layer) for target in target_rows(connection, args)]
    finally:
        connection.close()
    print(json.dumps({"count": len(records), "targets": records}, indent=2))


if __name__ == "__main__":
    main()
