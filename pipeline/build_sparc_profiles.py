#!/usr/bin/env python3
"""Export the complete SPARC sample as traceable, browser-ready profile data."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path


SPARC_BIBCODE = "2016AJ....152..157L"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def parse_surface_brightness(payload: bytes) -> list[dict]:
    rows = []
    for line in payload.decode("utf-8").splitlines()[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        columns = line.split()
        if len(columns) < 4:
            continue
        rows.append(
            {
                "radiusArcsec": float(columns[0]),
                "surfaceBrightnessMagArcsec2": float(columns[1]),
                "accepted": int(columns[2]) == 1,
                "uncertaintyMag": float(columns[3]) if math.isfinite(float(columns[3])) else None,
            }
        )
    return rows


def parse_rotation_model(payload: bytes) -> tuple[float | None, list[dict]]:
    distance = None
    rows = []
    for raw_line in payload.decode("utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# Distance"):
            distance = float(line.split("=", 1)[1].split()[0])
            continue
        if line.startswith("#"):
            continue
        columns = line.split()
        if len(columns) < 8:
            continue
        rows.append(
            {
                "radiusKpc": float(columns[0]),
                "observedVelocityKmS": float(columns[1]),
                "velocityUncertaintyKmS": float(columns[2]),
                "gasVelocityKmS": float(columns[3]),
                "diskVelocityKmS": float(columns[4]),
                "bulgeVelocityKmS": float(columns[5]),
                "diskSurfaceBrightnessLsunPc2": float(columns[6]),
                "bulgeSurfaceBrightnessLsunPc2": float(columns[7]),
            }
        )
    return distance, rows


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coordinates", type=Path, default=root / "pipeline" / "cache" / "sparc" / "simbad-sparc-paper-objects.csv")
    parser.add_argument("--surface-brightness", type=Path, default=root / "pipeline" / "cache" / "sparc" / "sfb_LTG.zip")
    parser.add_argument("--rotation-models", type=Path, default=root / "pipeline" / "cache" / "sparc" / "Rotmod_LTG.zip")
    parser.add_argument("--output", type=Path, default=root / "public" / "data" / "sparc-profiles.json")
    args = parser.parse_args()

    # The coordinate table defines the exact 175-object target identity used by
    # the Rubin coverage audit. Keep the public profile index on that same key.
    from query_dp2_sia import read_sparc_targets

    targets = read_sparc_targets(args.coordinates)
    records = {}
    with zipfile.ZipFile(args.surface_brightness) as profiles, zipfile.ZipFile(args.rotation_models) as rotations:
        profile_names = set(profiles.namelist())
        rotation_names = set(rotations.namelist())
        for target in targets:
            profile_member = f"{target.sparc_id}.sfb"
            rotation_member = f"{target.sparc_id}_rotmod.dat"
            if profile_member not in profile_names or rotation_member not in rotation_names:
                raise RuntimeError(f"Missing SPARC source product for {target.sparc_id}")
            profile_bytes = profiles.read(profile_member)
            rotation_bytes = rotations.read(rotation_member)
            profile = parse_surface_brightness(profile_bytes)
            distance, rotation = parse_rotation_model(rotation_bytes)
            if not profile or not rotation:
                raise RuntimeError(f"Empty SPARC science profile for {target.sparc_id}")
            accepted = [point for point in profile if point["accepted"]]
            records[slugify(target.sparc_id)] = {
                "targetId": slugify(target.sparc_id),
                "sparcId": target.sparc_id,
                "distanceMpc": distance,
                "surfaceBrightness": profile,
                "rotationCurve": rotation,
                "summary": {
                    "acceptedPhotometryPoints": len(accepted),
                    "maximumAcceptedRadiusArcsec": max(point["radiusArcsec"] for point in accepted),
                    "rotationCurvePoints": len(rotation),
                    "maximumRotationRadiusKpc": max(point["radiusKpc"] for point in rotation),
                },
                "provenance": {
                    "citation": "Lelli, McGaugh & Schombert (2016), AJ 152, 157",
                    "bibcode": SPARC_BIBCODE,
                    "sourceBaseUrl": "https://astroweb.cwru.edu/SPARC/",
                    "surfaceBrightnessArchive": args.surface_brightness.name,
                    "surfaceBrightnessArchiveSha256": sha256(args.surface_brightness),
                    "surfaceBrightnessMember": profile_member,
                    "surfaceBrightnessMemberSha256": hashlib.sha256(profile_bytes).hexdigest(),
                    "rotationArchive": args.rotation_models.name,
                    "rotationArchiveSha256": sha256(args.rotation_models),
                    "rotationMember": rotation_member,
                    "rotationMemberSha256": hashlib.sha256(rotation_bytes).hexdigest(),
                },
            }

    records_dir = args.output.parent / "sparc-profiles"
    records_dir.mkdir(parents=True, exist_ok=True)
    index_targets = {}
    for target_id, record in records.items():
        target_path = records_dir / f"{target_id}.json"
        target_path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "product": "Layers SPARC profile record",
                    "target": record,
                },
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        index_targets[target_id] = {
            "targetId": target_id,
            "sparcId": record["sparcId"],
            "distanceMpc": record["distanceMpc"],
            "summary": record["summary"],
            "data": f"/data/sparc-profiles/{target_id}.json",
            "sha256": sha256(target_path),
        }
    expected = {f"{target_id}.json" for target_id in records}
    for path in records_dir.glob("*.json"):
        if path.name not in expected:
            path.unlink()

    output = {
        "schemaVersion": 1,
        "product": "Layers SPARC profile index",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "targetCount": len(records),
        "targets": index_targets,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Exported {len(records)} traceable SPARC profile records and index to {args.output.parent}")


if __name__ == "__main__":
    main()
