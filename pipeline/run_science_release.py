#!/usr/bin/env python3
"""Run the deterministic post-acquisition Layers science release pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


STAGES = (
    ("sparc-profiles", "build_sparc_profiles.py"),
    ("registration", "audit_layer_registration.py"),
    ("reconciliation", "reconcile_image_layers.py"),
    ("stellar-filter-audit", "audit_filter_response.py"),
    ("diffuse-recovery", "validate_diffuse_recovery.py"),
    ("extended-source-filter-audit", "audit_extended_source_transfer.py"),
    ("three-survey-consistency", "audit_three_survey_consistency.py"),
    ("external-image-layers", "validate_external_image_layers.py"),
    ("wise-sparc-photometry-validation", "validate_wise_sparc_photometry.py"),
    ("wise-sparc-transfer", "audit_wise_sparc_transfer.py"),
    ("wise-stellar-mass-layers", "build_wise_stellar_mass_layers.py"),
    ("external-catalog-layers", "validate_external_catalog_layers.py"),
    ("comparison-previews", "build_comparison_previews.py"),
    ("catalog", "build_layers_catalog.py"),
    ("catalog-validation", "validate_layers_catalog.py"),
    ("local-store", "build_local_layer_store.py"),
    ("comparison-packages", "build_comparison_packages.py"),
    ("cross-surface-validation", "validate_cross_surface_consistency.py"),
    ("flux-regression", "test_flux_conservation.py"),
    ("diffuse-regression", "test_diffuse_recovery.py"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=root / "pipeline" / "output" / "science-release-run.json")
    args = parser.parse_args()
    records = []
    for name, script in STAGES:
        command = [sys.executable, str(root / "pipeline" / script)]
        completed = subprocess.run(command, cwd=root, text=True, capture_output=True)
        record = {
            "name": name,
            "command": [Path(sys.executable).name, f"pipeline/{script}"],
            "exitCode": completed.returncode,
            "stdout": completed.stdout.strip().splitlines(),
            "stderr": completed.stderr.strip().splitlines(),
        }
        records.append(record)
        print(f"[{name}] {'pass' if completed.returncode == 0 else 'FAIL'}", flush=True)
        if completed.returncode != 0:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(
                json.dumps({"schemaVersion": 1, "status": "failed", "stages": records}, indent=2),
                encoding="utf-8",
            )
            raise SystemExit("\n".join(record["stderr"] or record["stdout"]))
    catalog = root / "public" / "data" / "layers-catalog.json"
    package_files = sorted((root / "public" / "data" / "comparisons").glob("*.json"))
    pilot_files = sorted((root / "public" / "data" / "pilot-audits").glob("*.json"))
    manifest = {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "scope": "post-acquisition deterministic science release; source downloads are retained and checksum-addressed",
        "stages": records,
        "outputs": {
            "catalog": {"path": "public/data/layers-catalog.json", "sha256": sha256(catalog)},
            "comparisonPackages": [
                {"path": path.relative_to(root).as_posix(), "sha256": sha256(path)} for path in package_files
            ],
            "pilotAuditPackages": [
                {"path": path.relative_to(root).as_posix(), "sha256": sha256(path)} for path in pilot_files
            ],
        },
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Science release pipeline passed {len(STAGES)} stages")


if __name__ == "__main__":
    main()
