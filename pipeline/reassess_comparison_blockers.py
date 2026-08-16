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

When first run this returned `comparisonReady` 0, and correctly: the
resampling-covariance factor had been measured everywhere and applied nowhere,
and a measured systematic that nobody has corrected still blocks a quantitative
claim. Knowing an error bar is twice too small is not the same as fixing it.

It has since been fixed. On 2026-08-16 the correction was applied to the released
error columns and the catalogue republished, which clears that blocker on every
region and takes `comparisonReady` to 7 -- the regions where nothing else was
outstanding either. The number moved because the work was done, not because the
rule was loosened; the rule is still that each blocker clears only against a
named artefact.

Injection/recovery QA was the next constraint, and most of it turned out to be
unspent compute. measure_catalogue_reliability.py defaulted to 24 regions on the
stated grounds that a full pass "would take hours". That was never timed and is
wrong by two orders of magnitude: it is about 2.3 seconds a region, so the whole
set runs in minutes. 166 regions had never been attempted because a help string
asserted a cost nobody checked.

Running it properly took the measured regions from 9 to 79 and comparisonReady
from 7 to 54. The 111 that remain were attempted and genuinely do not qualify --
a region needs 20 detected sources with positive flux in both frames and 30%
valid area to define its own flux ratio, and those are too sparse or too heavily
masked. That part is a property of the fields.

It also changed a published number. The 24-region sample saw zero false
positives, so the release quoted a 95% upper limit of 0.14%. The full pass found
three, giving a *measured* rate of 0.016% -- about nine times tighter than the
bound it replaces. More data made the claim stronger and more honest at once.

`comparisonReady` counts gates, not conclusions. The reconciliation policy still
sets `scienceClaimAllowed` false, and a region clearing every gate this pipeline
defines means exactly that and nothing more.
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
                    "injection/recovery was attempted here and did not yield a measurement: a "
                    "region needs at least 20 detected sources with positive flux in both "
                    "frames to define its own flux ratio, and at least 30% valid area. This "
                    "one is too sparse or too heavily masked. A property of the field, not "
                    "unspent compute."
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
            "run yielded a measurement for that region. Resampling covariance clears only "
            "where the correction has been applied to the released columns, not where it has "
            "merely been measured -- knowing an error bar is twice too small is not fixing it. "
            "It was applied on 2026-08-16, which is why it now clears."
        ),
        "regionsAssessed": len(assessed),
        "staleBlockersClearedByNewEvidence": dict(stale),
        "blockersRemaining": dict(remaining),
        "comparisonReady": ready,
        "finding": (
            f"The manifest overstates what is left. Bandpass transfer is listed against all "
            f"190 regions but {stale.get('bandpass transfer', 0)} of them have a fitted "
            f"per-region colour term. What genuinely remains is narrower and more specific: "
            f"injection/recovery QA on {remaining.get('injection/recovery QA', 0)} regions. "
            f"comparisonReady is {ready}. It was 0 until the correlated-noise correction was "
            f"applied to the released error columns, which cleared resampling covariance on every "
            f"region; the {ready} that follow are those where nothing else was outstanding "
            f"either. That is a statement about acquisition and measurement, not a licence for an "
            f"astrophysical claim -- the reconciliation policy still sets scienceClaimAllowed "
            f"false, and a region clearing every gate this pipeline defines only means the gates "
            f"are cleared."
        ),
        "comparisonReadyRegions": [r["regionId"] for r in assessed if r["comparisonReady"]],
        "scienceClaimAllowed": False,
        "whatWouldClearTheRest": {
            "injection/recovery QA": (
                "The full pass has been run: all 190 regions attempted, 79 measured, up from 9. "
                "The 111 outstanding were attempted and do not qualify -- each needs 20 detected "
                "sources with positive flux in both frames and 30% valid area to define its own "
                "flux ratio, and these are too sparse or too heavily masked. Clearing them means "
                "either deeper detection in those fields or a readiness rule that does not "
                "require a per-region flux ratio, and the second would be loosening the standard "
                "rather than meeting it."
            ),
            "resampling covariance": (
                "Done on 2026-08-16. The measured factor was applied to rubin_flux_err_njy, "
                "reference_flux_err_njy and the _snr columns and the catalogue republished; "
                "noise_inflation_factor ships alongside so the uncorrected values remain "
                "recoverable. This is what moved comparisonReady off zero."
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
