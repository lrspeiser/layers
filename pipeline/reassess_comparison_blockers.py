"""Re-evaluate each region's comparison blockers against the evidence that now exists.

`comparisonReady` is 0 across all 190 reconciled regions, and the reconciliation
manifest lists three blockers retained on every one of them: bandpass transfer,
injection/recovery QA, and resampling covariance.

That list was written on 2026-08-14. All three have been worked on since, and the
manifest has not been regenerated, so it now misreports what is actually left.
"Bandpass transfer blocks all 190 regions" is simply not true any more, and a
scientist reading it to decide what this dataset needs gets the wrong answer.

This recomputes the blocker state per region from the current evidence. It does
not clear anything on the grounds that work was attempted -- each blocker is
checked against a specific artefact, and the rule for clearing it is stated in
CHECKS below so it can be argued with rather than trusted.

The expected result is that `comparisonReady` stays 0. Two blockers genuinely
remain: injection/recovery QA yielded a measurement for 9 regions of 24
attempted, not 190, and the resampling-covariance factor has been measured but
not applied to the released error columns. A measured systematic that nobody has
corrected still blocks a quantitative claim -- knowing an error bar is twice too
small is not the same as fixing it.

The point is not to move the number. It is to make the remaining work legible:
which blocker, on how many regions, and what specifically would clear it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
SELECTED = ROOT / "public/data/layers/selected-regions"
OUTPUT = SELECTED / "blocker-reassessment.json"

RECONCILIATION = SELECTED / "rubin-reference-reconciliation-200.json"


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def evidence() -> dict[str, set[str] | dict]:
    bandpass = load(SELECTED / "bandpass-transfer-200.json")
    reliability = load(SELECTED / "catalogue-reliability.json")
    covariance = load(SELECTED / "resampling-covariance-slim.json")
    synthetic = load(SELECTED / "synthetic-bandpass.json")
    return {
        # A region clears bandpass transfer when its own colour term was fitted,
        # not merely because the filters were characterised globally.
        "bandpassRegions": {
            r["regionId"] for r in (bandpass.get("regions") or []) if r.get("regionId")
        },
        "bandpassGlobal": bool(synthetic.get("predictedColourTerms")),
        # Injection/recovery is per region and most regions have none.
        "injectionRegions": {
            r["regionId"] for r in (reliability.get("regions") or []) if r.get("regionId")
        },
        # Covariance is measured everywhere, but measuring is not correcting.
        "covarianceMeasured": bool(covariance.get("summary")),
        "covarianceApplied": bool(covariance.get("appliedToReleasedColumns")),
    }


def assess(region: dict, ev: dict) -> dict:
    region_id = region.get("regionId")
    retained = list(region.get("comparisonBlockers") or [])
    cleared_before = list(region.get("clearedBlockers") or [])

    now_cleared: list[str] = []
    still: dict[str, str] = {}

    for blocker in retained:
        if blocker == "bandpass transfer":
            if region_id in ev["bandpassRegions"]:
                now_cleared.append(blocker)
            else:
                still[blocker] = (
                    "no per-region colour-term fit; the filters are characterised globally "
                    "but this field's own transfer was never measured"
                    if ev["bandpassGlobal"]
                    else "not measured"
                )
        elif blocker == "injection/recovery QA":
            if region_id in ev["injectionRegions"]:
                now_cleared.append(blocker)
            else:
                still[blocker] = (
                    "no injection/recovery run yielded a measurement for this region"
                )
        elif blocker == "resampling covariance":
            if ev["covarianceMeasured"] and ev["covarianceApplied"]:
                now_cleared.append(blocker)
            elif ev["covarianceMeasured"]:
                still[blocker] = (
                    "measured but not corrected: flux errors are understated by about a "
                    "factor of two and the released columns still carry the old values"
                )
            else:
                still[blocker] = "not measured"
        else:
            still[blocker] = "unchanged"

    return {
        "regionId": region_id,
        "clearedPreviously": cleared_before,
        "clearedByNewEvidence": now_cleared,
        "stillBlocked": still,
        "comparisonReady": not still,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = parser.parse_args()

    manifest = load(RECONCILIATION)
    regions = [r for r in (manifest.get("regions") or []) if isinstance(r, dict)]
    if not regions:
        raise SystemExit("no reconciled regions found")

    ev = evidence()
    assessed = [assess(region, ev) for region in regions]

    stale = Counter()
    remaining = Counter()
    for row in assessed:
        for blocker in row["clearedByNewEvidence"]:
            stale[blocker] += 1
        for blocker in row["stillBlocked"]:
            remaining[blocker] += 1
    ready = sum(1 for row in assessed if row["comparisonReady"])

    print(f"regions: {len(assessed)}\n")
    print("blockers the manifest still lists but current evidence clears:")
    for blocker, count in stale.most_common() or [("none", 0)]:
        print(f"  {count:4d}  {blocker}")
    print("\nblockers that genuinely remain:")
    for blocker, count in remaining.most_common():
        print(f"  {count:4d}  {blocker}")
    print(f"\ncomparisonReady: {ready}")

    payload = {
        "schemaVersion": "layers-blocker-reassessment-v1",
        "question": (
            "The reconciliation manifest lists three blockers on all 190 regions and was "
            "written before two of them were worked on. Which of them actually still stand?"
        ),
        "method": (
            "Per region, each retained blocker is checked against a named artefact. Bandpass "
            "transfer clears only where that region's own colour term was fitted, not because "
            "the filters were characterised globally. Injection/recovery clears only where a "
            "run yielded a measurement for that region. Resampling covariance does not clear "
            "on being measured, because the released error columns still carry uncorrected "
            "values -- knowing an error bar is twice too small is not fixing it."
        ),
        "regionsAssessed": len(assessed),
        "staleBlockersClearedByNewEvidence": dict(stale),
        "blockersRemaining": dict(remaining),
        "comparisonReady": ready,
        "finding": (
            f"The manifest overstates what is left. Bandpass transfer is listed against all "
            f"190 regions but {stale.get('bandpass transfer', 0)} of them have a fitted "
            f"per-region colour term. What genuinely remains is narrower and more specific: "
            f"injection/recovery QA on {remaining.get('injection/recovery QA', 0)} regions, "
            f"and a resampling-covariance correction that has been measured everywhere and "
            f"applied nowhere. comparisonReady stays {ready}, which is the correct answer -- "
            f"this reassessment exists to say what the remaining work is, not to shrink it."
        ),
        "whatWouldClearTheRest": {
            "injection/recovery QA": (
                "Run injection/recovery on the regions that lack it. 24 were attempted and 9 "
                "yielded a measurement, so the method needs to work on fainter and more "
                "crowded fields before this scales to 190."
            ),
            "resampling covariance": (
                "Apply the measured factor to rubin_flux_err_njy, reference_flux_err_njy and "
                "the _snr columns, then republish. The factor is size-dependent rather than "
                "constant, so this changes every row and the release checksums with it."
            ),
        },
        "reproduce": "python pipeline/reassess_comparison_blockers.py",
        "regions": assessed,
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output.relative_to(ROOT)}")

    # Slim companion for the site, same reason as resampling-covariance-slim: a
    # page that imports the per-region block ships it into the worker bundle.
    slim = {k: v for k, v in payload.items() if k != "regions"}
    slim_path = args.output.with_name(args.output.stem + "-slim.json")
    slim_path.write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {slim_path.relative_to(ROOT)} ({slim_path.stat().st_size / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()
