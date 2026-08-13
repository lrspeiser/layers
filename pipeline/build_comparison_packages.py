#!/usr/bin/env python3
"""Build public metadata packages and full local reproducibility bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path


AUDIT_FILES = (
    "registration-audit.json",
    "reconciliation.json",
    "filter-response-audit.json",
    "extended-source-filter-audit.json",
    "diffuse-recovery.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=root / "public" / "data" / "layers-catalog.json")
    parser.add_argument("--comparisons", type=Path, default=root / "pipeline" / "output" / "comparisons")
    parser.add_argument("--local-output", type=Path, default=root / "pipeline" / "output" / "packages")
    parser.add_argument("--public-output", type=Path, default=root / "public" / "data" / "comparisons")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    args.public_output.mkdir(parents=True, exist_ok=True)
    args.local_output.mkdir(parents=True, exist_ok=True)
    expected_public = set()
    package_count = 0
    for target in catalog["targets"]:
        if not target.get("comparisons"):
            continue
        for comparison in target["comparisons"]:
            slug = target["id"]
            source_dir = args.comparisons / slug
            local_dir = args.local_output / slug
            local_dir.mkdir(parents=True, exist_ok=True)
            artifact_records = []
            for filename in AUDIT_FILES:
                source = source_dir / filename
                if not source.is_file():
                    continue
                target_path = local_dir / filename
                shutil.copy2(source, target_path)
                artifact_records.append(
                    {"name": filename, "bytes": target_path.stat().st_size, "sha256": sha256(target_path)}
                )
            public_record = {
                "schemaVersion": 1,
                "product": "Layers comparison QA package",
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "target": {
                    "id": slug,
                    "name": target["name"],
                    "identifiers": target["identifiers"],
                    "center": target["center"],
                    "region": target["region"],
                    "selection": target["selection"],
                },
                "layers": [layer for layer in target["layers"] if layer["id"] in comparison["layerIds"]],
                "comparison": comparison,
                "localReproductionArtifacts": artifact_records,
                "pixelPolicy": "Metadata and checksums are public. Authenticated Rubin pixels and matched FITS remain in the local reproducibility bundle until redistribution is authorized.",
            }
            public_path = args.public_output / f"{slug}.json"
            public_path.write_text(json.dumps(public_record, indent=2), encoding="utf-8")
            expected_public.add(public_path.name)
            local_manifest = {
                **public_record,
                "publicRecordSha256": sha256(public_path),
                "catalogComparisonCanonicalSha256": hashlib.sha256(canonical(comparison).encode("utf-8")).hexdigest(),
            }
            manifest_path = local_dir / "manifest.json"
            manifest_path.write_text(json.dumps(local_manifest, indent=2), encoding="utf-8")
            zip_path = args.local_output / f"{slug}-reproducibility.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for path in sorted(local_dir.iterdir()):
                    if path.is_file():
                        archive.write(path, arcname=f"{slug}/{path.name}")
            package_count += 1
    for path in args.public_output.glob("*.json"):
        if path.name not in expected_public:
            path.unlink()
    print(f"Built {package_count} public QA package(s) and full local reproducibility bundle(s)")


if __name__ == "__main__":
    main()
