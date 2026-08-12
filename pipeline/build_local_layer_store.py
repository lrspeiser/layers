#!/usr/bin/env python3
"""Build a queryable local SQLite index over Layers files and metadata."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE targets (
  id INTEGER PRIMARY KEY,
  target_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  ra_deg REAL NOT NULL,
  dec_deg REAL NOT NULL,
  field_width_arcmin REAL NOT NULL,
  sample TEXT NOT NULL,
  selection_json TEXT NOT NULL
);
CREATE VIRTUAL TABLE target_sky_index USING rtree(
  id, min_ra, max_ra, min_dec, max_dec
);
CREATE TABLE layers (
  target_id INTEGER NOT NULL REFERENCES targets(id),
  layer_key TEXT NOT NULL,
  survey TEXT NOT NULL,
  release TEXT NOT NULL,
  kind TEXT NOT NULL,
  render_mode TEXT NOT NULL,
  availability TEXT NOT NULL,
  calibration TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  PRIMARY KEY (target_id, layer_key)
);
CREATE TABLE datasets (
  dataset_id TEXT PRIMARY KEY,
  target_id INTEGER NOT NULL REFERENCES targets(id),
  layer_key TEXT NOT NULL,
  obs_id TEXT,
  band TEXT,
  local_path TEXT,
  bytes INTEGER,
  sha256 TEXT,
  FOREIGN KEY (target_id, layer_key) REFERENCES layers(target_id, layer_key)
);
CREATE TABLE products (
  target_id INTEGER NOT NULL REFERENCES targets(id),
  layer_key TEXT NOT NULL,
  band TEXT NOT NULL,
  science_coverage INTEGER NOT NULL,
  valid_pixel_fraction REAL NOT NULL,
  mosaic_path TEXT,
  mosaic_sha256 TEXT,
  preview_path TEXT,
  preview_sha256 TEXT,
  PRIMARY KEY (target_id, layer_key, band),
  FOREIGN KEY (target_id, layer_key) REFERENCES layers(target_id, layer_key)
);
CREATE TABLE comparisons (
  comparison_key TEXT PRIMARY KEY,
  target_id INTEGER NOT NULL REFERENCES targets(id),
  left_layer_key TEXT NOT NULL,
  right_layer_key TEXT NOT NULL,
  status TEXT NOT NULL,
  record_json TEXT NOT NULL
);
CREATE INDEX datasets_target_band ON datasets(target_id, band);
CREATE INDEX layers_availability ON layers(layer_key, availability);
CREATE INDEX products_coverage ON products(science_coverage, valid_pixel_fraction);
"""


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=root / "public" / "data" / "layers-catalog.json")
    parser.add_argument("--downloads", type=Path, default=root / "pipeline" / "output" / "dp2-sparc" / "download-manifest.json")
    parser.add_argument("--mosaics", type=Path, default=root / "pipeline" / "output" / "dp2-sparc" / "mosaic-summary.json")
    parser.add_argument("--legacy", type=Path, default=root / "pipeline" / "output" / "legacy-survey" / "manifest.json")
    parser.add_argument("--panstarrs", type=Path, default=root / "pipeline" / "output" / "panstarrs" / "manifest.json")
    parser.add_argument("--output", type=Path, default=root / "pipeline" / "output" / "layers.sqlite")
    args = parser.parse_args()

    catalog = read_json(args.catalog)
    downloads = read_json(args.downloads).get("records", []) if args.downloads.is_file() else []
    mosaics = read_json(args.mosaics) if args.mosaics.is_file() else []
    download_by_id = {item["publisher_id"]: item for item in downloads}
    mosaic_by_slug = {item["target"]["slug"]: item for item in mosaics}
    legacy_records = {item["target"]["slug"]: item for item in read_json(args.legacy).get("targets", [])} if args.legacy.is_file() else {}
    legacy_tile_by_url = {tile["url"]: tile for item in legacy_records.values() for tile in item.get("tiles", [])}
    panstarrs_records = {item["target"]["slug"]: item for item in read_json(args.panstarrs).get("targets", [])} if args.panstarrs.is_file() else {}
    panstarrs_original_by_url = {
        original["url"]: {**original, "band": band}
        for item in panstarrs_records.values()
        for band, product in item.get("bands", {}).items()
        for original in product.get("originals", [])
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.unlink(missing_ok=True)
    connection = sqlite3.connect(args.output)
    try:
        connection.executescript(SCHEMA)
        for target_index, target in enumerate(catalog["targets"], start=1):
            connection.execute(
                "INSERT INTO targets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    target_index,
                    target["id"],
                    target["name"],
                    target["center"]["raDeg"],
                    target["center"]["decDeg"],
                    target["region"]["widthArcmin"],
                    target["selection"]["sample"],
                    json.dumps(target["selection"], separators=(",", ":")),
                ),
            )
            half_dec = target["region"]["widthArcmin"] / 120.0
            cos_dec = max(math.cos(math.radians(target["center"]["decDeg"])), 0.1)
            half_ra = half_dec / cos_dec
            connection.execute(
                "INSERT INTO target_sky_index VALUES (?, ?, ?, ?, ?)",
                (target_index, target["center"]["raDeg"] - half_ra, target["center"]["raDeg"] + half_ra, target["center"]["decDeg"] - half_dec, target["center"]["decDeg"] + half_dec),
            )
            for layer in target["layers"]:
                connection.execute(
                    "INSERT INTO layers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        target_index,
                        layer["id"],
                        layer["survey"],
                        layer["release"],
                        layer["kind"],
                        layer["renderMode"],
                        layer["availability"],
                        layer["calibration"],
                        json.dumps(layer, separators=(",", ":")),
                    ),
                )
                for dataset_id in layer.get("datasetIds", []):
                    record = download_by_id.get(dataset_id) or legacy_tile_by_url.get(dataset_id) or panstarrs_original_by_url.get(dataset_id, {})
                    connection.execute(
                        "INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (dataset_id, target_index, layer["id"], record.get("obs_id"), record.get("band"), record.get("path"), record.get("bytes"), record.get("sha256")),
                    )
            mosaic = mosaic_by_slug.get(target["id"])
            if mosaic:
                for band, product in mosaic.get("bands", {}).items():
                    connection.execute(
                        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            target_index,
                            "rubin-dp2-deep-coadd",
                            band,
                            int(product["science_coverage"]),
                            product["valid_pixel_fraction"],
                            product.get("mosaic"),
                            product.get("mosaic_sha256"),
                            product.get("preview"),
                            product.get("preview_sha256"),
                        ),
                    )
            legacy = legacy_records.get(target["id"])
            if legacy:
                for band, product in legacy.get("bands", {}).items():
                    connection.execute(
                        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            target_index,
                            "legacy-survey-dr10",
                            band,
                            int(product["science_coverage"]),
                            product["valid_pixel_fraction"],
                            product.get("product"),
                            product.get("product_sha256"),
                            product.get("preview"),
                            product.get("preview_sha256"),
                        ),
                    )
            panstarrs = panstarrs_records.get(target["id"])
            if panstarrs:
                for band, product in panstarrs.get("bands", {}).items():
                    if "product" not in product:
                        continue
                    connection.execute(
                        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            target_index,
                            "panstarrs-dr1-stack",
                            band,
                            int(product["science_coverage"]),
                            product["valid_pixel_fraction"],
                            product.get("product"),
                            product.get("product_sha256"),
                            product.get("preview"),
                            product.get("preview_sha256"),
                        ),
                    )
            for comparison in target.get("comparisons", []):
                connection.execute(
                    "INSERT INTO comparisons VALUES (?, ?, ?, ?, ?, ?)",
                    (comparison["id"], target_index, comparison["layerIds"][0], comparison["layerIds"][1], comparison["status"], json.dumps(comparison, separators=(",", ":"))),
                )
        connection.commit()
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("targets", "layers", "datasets", "products", "comparisons")
        }
    finally:
        connection.close()
    print(f"Built {args.output} ({args.output.stat().st_size / 1024:.1f} KiB): {counts}")


if __name__ == "__main__":
    main()
