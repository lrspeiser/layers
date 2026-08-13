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
    "three-survey-consistency.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_generated_at(path: Path) -> str:
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8")).get("generatedAt")
        if existing:
            return existing
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=root / "public" / "data" / "layers-catalog.json")
    parser.add_argument("--comparisons", type=Path, default=root / "pipeline" / "output" / "comparisons")
    parser.add_argument("--local-output", type=Path, default=root / "pipeline" / "output" / "packages")
    parser.add_argument("--public-output", type=Path, default=root / "public" / "data" / "comparisons")
    parser.add_argument("--pilot-output", type=Path, default=root / "public" / "data" / "pilot-audits")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    args.public_output.mkdir(parents=True, exist_ok=True)
    args.pilot_output.mkdir(parents=True, exist_ok=True)
    args.local_output.mkdir(parents=True, exist_ok=True)
    expected_public = set()
    expected_pilot = set()
    package_count = 0
    pilot_count = 0
    for target in catalog["targets"]:
        if target.get("pilotAudit"):
            pilot_path = args.pilot_output / f"{target['id']}.json"
            pilot_record = {
                "schemaVersion": 1,
                "product": "Layers pilot audit package",
                "generatedAt": stable_generated_at(pilot_path),
                "target": {
                    "id": target["id"], "name": target["name"], "identifiers": target["identifiers"],
                    "center": target["center"], "region": target["region"], "selection": target["selection"],
                },
                "layers": target["layers"],
                "pilotAudit": target["pilotAudit"],
                "pixelPolicy": "Metadata and checksums are public. Authenticated Rubin pixels remain in the local layer store until redistribution is authorized.",
            }
            pilot_path.write_text(json.dumps(pilot_record, indent=2), encoding="utf-8")
            expected_pilot.add(pilot_path.name)
            local_dir = args.local_output / target["id"]
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / "pilot-audit-package.json").write_text(json.dumps(pilot_record, indent=2), encoding="utf-8")
            pilot_count += 1
        if not target.get("comparisons"):
            continue
        for comparison in target["comparisons"]:
            slug = target["id"]
            comparison_key = comparison.get("comparisonKey", slug)
            source_dir = args.comparisons / comparison_key
            public_path = args.public_output / f"{comparison_key}.json"
            local_dir = args.local_output / comparison_key
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
                "product": "Layers published comparison package" if comparison["status"] == "published" else "Layers comparison QA package",
                "generatedAt": stable_generated_at(public_path),
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
                "pixelPolicy": "Catalog/profile packages contain published values and provenance only; authenticated Rubin pixels and matched FITS remain in local image-comparison bundles until redistribution is authorized.",
            }
            reproduction_audit = comparison.get("products", {}).get("reproductionAudit")
            if reproduction_audit:
                audit_path = root / reproduction_audit
                if not audit_path.is_file():
                    raise RuntimeError(f"Missing declared reproduction audit: {audit_path}")
                audit_record = json.loads(audit_path.read_text(encoding="utf-8"))
                public_record["scienceAudit"] = audit_record
                audit_copy = local_dir / "wise-sparc-transfer-audit.json"
                shutil.copy2(audit_path, audit_copy)
                artifact_records.append({
                    "name": audit_copy.name,
                    "bytes": audit_copy.stat().st_size,
                    "sha256": sha256(audit_copy),
                })
                public_record["localReproductionArtifacts"] = artifact_records
            public_path.write_text(json.dumps(public_record, indent=2), encoding="utf-8")
            expected_public.add(public_path.name)
            local_manifest = {
                **public_record,
                "publicRecordSha256": sha256(public_path),
                "catalogComparisonCanonicalSha256": hashlib.sha256(canonical(comparison).encode("utf-8")).hexdigest(),
            }
            manifest_path = local_dir / "manifest.json"
            manifest_path.write_text(json.dumps(local_manifest, indent=2), encoding="utf-8")
            zip_path = args.local_output / f"{comparison_key}-reproducibility.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for path in sorted(local_dir.iterdir()):
                    if path.is_file():
                        archive.write(path, arcname=f"{comparison_key}/{path.name}")
            package_count += 1
    for path in args.public_output.glob("*.json"):
        if path.name not in expected_public:
            path.unlink()
    for path in args.pilot_output.glob("*.json"):
        if path.name not in expected_pilot:
            path.unlink()
    print(f"Built {package_count} comparison QA package(s), {pilot_count} pilot audit package(s), and full local reproducibility bundles")


if __name__ == "__main__":
    main()
