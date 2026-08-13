#!/usr/bin/env python3
"""Validate generic Layers image-adapter manifests and their local evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED_LAYER_FIELDS = {
    "id", "survey", "release", "instrument", "kind", "availability", "renderMode",
    "bands", "units", "calibration", "hasVariance", "hasMask", "hasWcs", "note", "provenance", "assets",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifests", nargs="*", type=Path,
        default=[root / "pipeline/output/wise-allwise/manifest.json"],
    )
    args = parser.parse_args()
    errors = []
    count = 0
    for path in args.manifests:
        if not path.is_file():
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("schemaVersion") != 1 or manifest.get("adapterContract") != "layers-image-layer-v1":
            errors.append(f"{path}: unsupported adapter contract")
            continue
        seen = set()
        for item in manifest.get("targets", []):
            count += 1
            key = (item.get("targetId"), item.get("layer", {}).get("id"))
            if key in seen:
                errors.append(f"{path}: duplicate target/layer {key}")
            seen.add(key)
            layer = item.get("layer", {})
            missing = REQUIRED_LAYER_FIELDS - layer.keys()
            if missing:
                errors.append(f"{path}/{key}: missing layer fields {sorted(missing)}")
            if layer.get("kind") != "image" or layer.get("renderMode") != "image":
                errors.append(f"{path}/{key}: image adapter emitted a non-image view")
            if layer.get("datasetCount") != len(layer.get("datasetIds", [])):
                errors.append(f"{path}/{key}: dataset count and identifiers disagree")
            for source in item.get("sources", {}).values():
                source_path = Path(source.get("localPath", ""))
                if not source_path.is_file() or sha256(source_path) != source.get("sha256"):
                    errors.append(f"{path}/{key}: source product missing or checksum mismatch")
            for product_key in ("standardProduct", "preview", "publicRecord"):
                product = item.get(product_key, {})
                product_path = Path(product.get("path", ""))
                if not product_path.is_file() or sha256(product_path) != product.get("sha256"):
                    errors.append(f"{path}/{key}: {product_key} missing or checksum mismatch")
            if item.get("scienceGate", {}).get("status") not in {"pass", "blocked"}:
                errors.append(f"{path}/{key}: explicit science gate missing")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Validated {count} external image-layer records")


if __name__ == "__main__":
    main()
