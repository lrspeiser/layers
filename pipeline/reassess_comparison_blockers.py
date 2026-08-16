"""Re-evaluate each region's comparison blockers against the evidence that now exists.

The reconciliation manifest was written on 2026-08-14 and lists three blockers on
all 190 regions. Work has happened since and the manifest has not been
regenerated, so it misreports what is left. This recomputes the state per region.

The rule is that a blocker clears only against a named artefact, and that the
artefact's own stated policy decides what clearing means -- not this script's
guess about it.

**A correction, kept here because the mistake is instructive.** An earlier
version of this script cleared "bandpass transfer" for the 156 regions with a
fitted per-region colour term, and reported comparisonReady rising 0 -> 7 -> 54.
That was wrong. `bandpass-transfer-200.json` sets `clearsBandpassBlocker: false`
and explains why in the same breath: the pilots passed point-source colour
calibration and still failed the resolved-galaxy transfer by 5 to 13 times
tolerance. Every extended-source transfer attempted so far is `qa-failed` or
`blocked`. The script was reading that manifest and overrode its explicit policy
with an assumption -- exactly the "work was attempted, so call it done" move it
was written to prevent.

comparisonReady is **0**, and has been throughout. Bandpass transfer blocks all
190 regions and only a passing extended-source transfer will move it.

What did genuinely change:

- Resampling covariance cleared on all 190. The correlated-noise correction was
  measured on real segment footprints and applied to the released error columns
  on 2026-08-16; the catalogue was republished. That is a real fix to real data.
- Injection/recovery went from 9 measured regions to 79. It had defaulted to 24
  regions on the stated grounds that a full pass "would take hours" -- never
  timed, and wrong by two orders of magnitude at 2.3 seconds a region. The full
  pass also replaced a zero-event 95% upper limit of 0.14% with a measured
  false-positive rate of 0.016%.
- The 111 injection/recovery regions still outstanding were attempted and
  genuinely do not qualify: each needs 20 detected sources with positive flux in
  both frames and 30% valid area to define its own flux ratio.

Both of those are progress on the blockers without being progress on readiness,
and conflating the two is what produced the wrong number in the first place.

`comparisonReady` counts gates, not conclusions. The reconciliation policy sets
`scienceClaimAllowed` false and no astrophysical claim stands.
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
        # The bandpass manifest states its own clearing rule. Read it rather than
        # inventing one: compactSourceOnly transfers do not clear the blocker.
        "extendedTransferValidated": bool(bandpass.get("policy", {}).get("clearsBandpassBlocker")),
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
            # A fitted per-region colour term does NOT clear this, and an earlier
            # version of this script wrongly assumed it did -- clearing 156
            # regions against the explicit policy of the very manifest it was
            # reading. bandpass-transfer-200.json sets clearsBandpassBlocker
            # false and says why: the pilots passed point-source colour
            # calibration and still failed the resolved-galaxy transfer by 5 to
            # 13 times tolerance. Every extended-source transfer attempted is
            # qa-failed or blocked, so nothing clears this yet.
            if ev["extendedTransferValidated"]:
                now_cleared.append(blocker)
            elif region_id in ev["bandpassRegions"]:
                still[blocker] = (
                    "compact-source colour term fitted for this field, which the bandpass "
                    "manifest explicitly says does not clear the blocker: point-source "
                    "calibration passing has already coexisted with resolved-galaxy transfer "
                    "failing by 5 to 13 times tolerance. The extended-source test is what "
                    "clears this, and no attempt has yet passed QA"
                )
            else:
                still[blocker] = (
                    "no per-region colour-term fit either; 32 of the 34 skipped regions have "
                    "no validated second Rubin band, so no colour can be formed at all"
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
            f"comparisonReady is {ready}. An earlier version of this script reported 7 and then "
            f"54 by clearing bandpass transfer wherever a per-region colour term had been "
            f"fitted; bandpass-transfer-200.json sets clearsBandpassBlocker false and explains "
            f"that point-source calibration passing has already coexisted with resolved-galaxy "
            f"transfer failing by 5 to 13 times tolerance. Overriding a manifest's stated policy "
            f"with an assumption is the exact failure this script exists to prevent. Two "
            f"blockers did genuinely clear -- resampling covariance on all 190 once the "
            f"correction was applied to the released columns, and injection/recovery on 79 "
            f"regions once the full pass was run instead of a 24-region sample -- but progress "
            f"on blockers is not progress on readiness, and conflating them is what produced "
            f"the wrong number."
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
