#!/usr/bin/env python3
"""Run the full comparison chain over a Rubin region set, in order, once.

The stages depend on each other and each was previously run by hand with its own
paths, which is how a single-region debug pass silently truncated a manifest that
three later stages then read. This runs them in dependency order against one
declared region set and stops at the first failure rather than letting a later
stage consume a stale or partial input.

Order and why:

  1. normalize legacy      raw Legacy cutouts -> validated science inputs
  2. selected comparisons  Rubin + reference onto a shared display grid
  3. reconcile             PSF, background, and flux-unit matching
  4. recovery              injection/recovery limits and the covariance measure
  5. bandpass              colour-term fit, needs the second Rubin band
  6. anomalies             residual scan, needs the recovery null to score against

Stage 6 cannot run before stage 4: without the empirical null it would score
residuals against the per-pixel variance planes, which understate the true
uncertainty by a median factor of about seven on these products.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/Scripts/python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)


def stage(name: str, script: str, arguments: list[str], log: Path) -> tuple[bool, str]:
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            [str(PYTHON), str(ROOT / "pipeline" / script), *arguments],
            stdout=handle,
            stderr=subprocess.STDOUT,
            cwd=ROOT,
        )
    elapsed = time.time() - started
    tail = ""
    if log.exists():
        lines = [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        tail = lines[-1] if lines else ""
    status = "ok" if result.returncode == 0 else f"FAILED ({result.returncode})"
    print(f"[{status}] {name}  {elapsed / 60:.1f} min  {tail[:120]}", flush=True)
    return result.returncode == 0, tail


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", default="200", help="Region set suffix, e.g. 200")
    parser.add_argument("--logs", type=Path, default=ROOT / "pipeline/results/chain-logs")
    parser.add_argument("--skip", action="append", default=[], help="Stage names to skip")
    parser.add_argument("--from-stage", help="Start at this stage name")
    args = parser.parse_args()

    tag = args.set
    acquisition = f"pipeline/results/acquisition-{tag}"
    stages = [
        (
            "normalize-legacy",
            "normalize_legacy_cutouts.py",
            [
                "--plan", f"{acquisition}/science-legacy/acquisition-plan.json",
                "--output", f"{acquisition}/legacy-normalized",
                "--detailed-manifest", f"{acquisition}/legacy-normalized/manifest.json",
                "--public-manifest", f"public/data/layers/selected-regions/legacy-dr10-{tag}.json",
                # Without this the stage writes into the 50-set preview directory
                # and overwrites previews the existing published manifests point at.
                "--previews", f"public/layer-previews/selected-regions-{tag}",
                "--band", "r",
            ],
        ),
        (
            "selected-comparisons",
            "build_selected_region_comparisons.py",
            [
                "--rubin-manifest", f"pipeline/results/rubin-pixels-{tag}/manifest.json",
                "--legacy-manifest", f"{acquisition}/legacy-normalized/manifest.json",
                "--products", f"pipeline/results/selected-region-comparisons-{tag}",
                "--public-manifest", f"public/data/layers/selected-regions/rubin-reference-comparisons-{tag}.json",
                "--previews", f"public/layer-previews/selected-regions-{tag}/comparisons",
            ],
        ),
        (
            "reconcile",
            "reconcile_selected_regions.py",
            [
                "--input", f"pipeline/results/selected-region-comparisons-{tag}/manifest.json",
                "--rubin-manifest", f"pipeline/results/rubin-pixels-{tag}/manifest.json",
                "--products", f"pipeline/results/reconciled-regions-{tag}",
                "--public-manifest", f"public/data/layers/selected-regions/rubin-reference-reconciliation-{tag}.json",
            ],
        ),
        (
            "recovery",
            "validate_region_recovery.py",
            [
                "--input", f"pipeline/results/reconciled-regions-{tag}/manifest.json",
                "--output", f"pipeline/results/region-recovery-{tag}",
                "--public-manifest", f"public/data/layers/selected-regions/region-diffuse-recovery-{tag}.json",
            ],
        ),
        (
            "bandpass",
            "measure_bandpass_transfer.py",
            [
                "--reconciled", f"pipeline/results/reconciled-regions-{tag}/manifest.json",
                "--band2", f"pipeline/results/rubin-pixels-{tag}-band2/manifest.json",
                "--output", f"pipeline/results/bandpass-transfer-{tag}",
                "--public-manifest", f"public/data/layers/selected-regions/bandpass-transfer-{tag}.json",
            ],
        ),
        (
            "anomalies",
            "scan_region_anomalies.py",
            [
                "--reconciled", f"pipeline/results/reconciled-regions-{tag}/manifest.json",
                "--recovery", f"pipeline/results/region-recovery-{tag}",
                "--bandpass", f"pipeline/results/bandpass-transfer-{tag}/manifest.json",
                "--output", f"pipeline/results/region-anomalies-{tag}",
                "--public-manifest", f"public/data/layers/selected-regions/region-anomalies-{tag}.json",
            ],
        ),
    ]

    names = [name for name, _, _ in stages]
    if args.from_stage and args.from_stage not in names:
        raise SystemExit(f"unknown stage {args.from_stage!r}; choose from {', '.join(names)}")
    started = False if args.from_stage else True

    summary = []
    for name, script, arguments in stages:
        if not started:
            if name != args.from_stage:
                continue
            started = True
        if name in args.skip:
            print(f"[skip] {name}", flush=True)
            continue
        ok, tail = stage(name, script, arguments, args.logs / f"{tag}-{name}.log")
        summary.append({"stage": name, "ok": ok, "tail": tail})
        if not ok:
            print(
                f"\nStopped at {name}. Later stages read its output, so running them now would "
                f"consume a stale or partial manifest. See {args.logs / f'{tag}-{name}.log'}",
                flush=True,
            )
            break

    report = args.logs / f"{tag}-chain-summary.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"set": tag, "stages": summary}, indent=2) + "\n", encoding="utf-8")
    completed = sum(1 for item in summary if item["ok"])
    print(f"\n{completed}/{len(stages)} stages completed; summary at {report.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
