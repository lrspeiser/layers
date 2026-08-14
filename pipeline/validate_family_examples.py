#!/usr/bin/env python3
"""Validate local evidence products emitted by build_family_examples.py."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from astropy.table import Table


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public" / "data" / "layers" / "family-examples"
REPORT = ROOT / "pipeline" / "results" / "family-examples" / "validation.json"


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    records = {
        key: json.loads((PUBLIC / f"{key}.json").read_text(encoding="utf-8"))
        for key in ("spectroscopy", "xray", "neutralGas", "lensing")
    }
    artifact_checks: list[dict[str, object]] = []
    for key in ("spectroscopy", "xray", "neutralGas"):
        for item in records[key]["artifacts"]:
            path = ROOT / item["path"]
            artifact_checks.append(
                {
                    "family": key,
                    "path": item["path"],
                    "exists": path.is_file(),
                    "sizeMatches": path.is_file() and path.stat().st_size == item["bytes"],
                    "sha256Matches": path.is_file() and checksum(path) == item["sha256"],
                }
            )
    manifest = json.loads((PUBLIC / "tract-manifest.json").read_text(encoding="utf-8"))
    for route in manifest["routes"]:
        for evidence in route["evidence"]:
            for item in evidence["downloads"]:
                path = ROOT / "public" / item["href"].lstrip("/")
                artifact_checks.append(
                    {
                        "family": evidence["family"],
                        "path": path.relative_to(ROOT).as_posix(),
                        "exists": path.is_file(),
                        "sizeMatches": path.is_file() and path.stat().st_size == item["bytes"],
                        "sha256Matches": path.is_file() and checksum(path) == item["sha256"],
                    }
                )
    spec_path = ROOT / records["spectroscopy"]["artifacts"][0]["path"]
    spectrum = Table.read(spec_path)
    checks = {
        "allArtifactsVerified": all(
            item["exists"] and item["sizeMatches"] and item["sha256Matches"]
            for item in artifact_checks
        ),
        "spectrumColumnsPresent": set(("wavelength", "flux", "ivar", "mask", "model")).issubset(spectrum.colnames),
        "spectrumRowCountMatches": len(spectrum) == records["spectroscopy"]["spectrum"]["samples"],
        "xrayCountMatches": records["xray"]["recordCount"] == len(records["xray"]["records"]),
        "neutralGasCountMatches": records["neutralGas"]["recordCount"] == len(records["neutralGas"]["records"]),
        "usesSelectedRubinTracts": {
            records[key]["selectedRubinRegion"]["tract"]
            for key in ("spectroscopy", "xray", "neutralGas", "lensing")
        } == {9813, 5061},
        "lensingExplicitlyUnresolved": records["lensing"]["status"] == "unresolved",
        "tractRoutesMatchEvidence": [
            (route["tract"], route["href"]) for route in manifest["routes"]
        ] == [(9813, "/tract/9813/evidence"), (5061, "/tract/5061/evidence")],
        "noDifferenceClaims": all(
            records[key]["interpretation"]["comparisonClaim"] is None
            for key in ("spectroscopy", "xray", "neutralGas")
        ),
    }
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "passed": all(checks.values()),
        "checks": checks,
        "artifacts": artifact_checks,
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
