#!/usr/bin/env python3
"""Fail the release if manifests are unverified, incomplete, or reuse pixels."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("public_root", nargs="?", type=Path, default=Path(__file__).parents[1] / "public" / "atlas")
    args = parser.parse_args()
    seen_rubin = {}
    errors = []

    for path in args.public_root.glob("*/manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        slug = path.parent.name
        if manifest.get("objectId") != slug or manifest.get("verified") is not True:
            errors.append(f"{slug}: identity or verified flag failed")
        registration = manifest.get("registration", {})
        if not all(registration.get(key) is True for key in ("commonWcs", "psfMatched", "skyMatched")):
            errors.append(f"{slug}: registration gates failed")
        if registration.get("maxResidualArcsec", 999) > registration.get("qaThresholdArcsec", 0):
            errors.append(f"{slug}: astrometric residual failed")
        for side in ("rubin", "legacy"):
            url = manifest.get("images", {}).get(side, {}).get("rgb", "")
            asset = args.public_root.parents[0] / url.removeprefix("/")
            if not asset.is_file():
                errors.append(f"{slug}: missing {side} RGB asset {asset}")
                continue
            actual = sha256(asset)
            if side == "rubin":
                if actual in seen_rubin:
                    errors.append(f"{slug}: Rubin RGB duplicates {seen_rubin[actual]}")
                seen_rubin[actual] = slug
        if not manifest.get("provenance", {}).get("butlerDatasetIds"):
            errors.append(f"{slug}: no Butler dataset UUIDs")

    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Validated {len(seen_rubin)} unique published object(s)")


if __name__ == "__main__":
    main()
