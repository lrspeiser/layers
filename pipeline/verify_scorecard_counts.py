"""Recount every goal's delivered figure from its own evidence file.

G0's published delivery was 167. Counted from the acquisition manifests it is
161: the 167 had been derived as the 173 ceiling minus 6 failures rather than
counted, and an inferred number is always consistent with the numbers it was
inferred from, so every check that added it back up passed.

That failure mode is not specific to G0. Any `delivered` figure that was computed
rather than counted carries the same risk, and the scorecard cites an evidence
file for each one. This opens each of those files and counts.

Where a goal's figure can be recounted the result is compared and any
disagreement reported. Where it cannot -- because the evidence is a rendered page
rather than a manifest, or the count is a sum the evidence does not itself carry
-- that is stated rather than silently skipped, because "not checkable here" is a
different claim from "checked and correct" and the difference is the whole point.

Run with --check to exit non-zero on any disagreement.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAYERS = ROOT / "public/data/layers"
SCORECARD = LAYERS / "goal-scorecard.json"


def load(relative: str) -> dict | None:
    path = LAYERS / relative
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def count_pairs(payload: dict) -> int | None:
    """Regions that produced a measurement, by the manifest's own bookkeeping."""
    if payload is None:
        return None
    counts = payload.get("counts") or {}
    for key in ("regionsMeasured", "pairsMeasured", "regionsCompared", "regionsWithPairs"):
        if isinstance(counts.get(key), int):
            return counts[key]
    regions = payload.get("regions")
    if isinstance(regions, list) and regions:
        return len(regions)
    return None


def recount(goal_id: str) -> tuple[int | None, str]:
    """Return (counted, how). counted is None when the figure is not recountable here."""
    if goal_id == "G0":
        # Already has a dedicated verifier; reuse its rule.
        import glob

        complete: dict[str, set[str]] = {}
        for m in sorted(glob.glob(str(ROOT / "pipeline/results/rubin-pixels-200*/manifest.json"))):
            for r in json.loads(pathlib.Path(m).read_text(encoding="utf-8")).get("regions", []):
                if r.get("status") == "complete" and r.get("regionId") and r.get("band"):
                    complete.setdefault(r["regionId"], set()).add(r["band"])
        return sum(1 for v in complete.values() if len(v) >= 2), "regions with two complete bands"

    if goal_id == "G1":
        # "reconciled optical pairs" -- count regions in each reference's
        # reconciliation manifest. Summing pixelsValidated instead gives 652,
        # which is the *ceiling* (regions with pixels), a different quantity that
        # happens to sit nearby. That mistake produced a false disagreement on
        # the first run of this script.
        total, parts = 0, []
        for name in ("rubin-reference-reconciliation-200", "rubin-des-reconciliation",
                     "rubin-ps1-reconciliation", "rubin-hsc-reconciliation"):
            payload = load(f"selected-regions/{name}.json")
            n = len(payload.get("regions") or []) if payload else 0
            total += n
            parts.append(f"{n}")
        return total, "reconciled regions summed over references (" + "+".join(parts) + ")"

    if goal_id == "G2":
        return count_pairs(load("gaia-crossmatch/comparison.json")), "regions measured"

    if goal_id == "G3":
        payload = load("sed/consistency.json")
        c = (payload or {}).get("counts") or {}
        return c.get("sedSources"), "counts.sedSources"

    if goal_id == "G4":
        c = (load("hi-gas/baryonic-tully-fisher.json") or {}).get("counts") or {}
        return c.get("attempted"), "counts.attempted (H I detections tested)"

    if goal_id == "G5":
        c = (load("lensing-light/correlation.json") or {}).get("counts") or {}
        return c.get("pairs"), "counts.pairs"

    if goal_id == "G6":
        x = (load("xray-counterparts/comparison.json") or {}).get("counts") or {}
        r = (load("radio-counterparts/comparison.json") or {}).get("counts") or {}
        xn, rn = x.get("regionsQueried"), r.get("fieldsSearched")
        if xn is None or rn is None:
            return None, "evidence missing"
        return xn + rn, f"xray regionsQueried {xn} + radio fieldsSearched {rn}"

    if goal_id == "G7":
        c = (load("ztf-variability/coverage-truth.json") or {}).get("counts") or {}
        return c.get("measured"), "counts.measured (regions with light curves)"

    if goal_id == "G8":
        # "verdicts delivered" -- summed across the high-resolution surveys.
        # Candidates with a verdict, NOT verdicts summed over surveys. Exactly one
        # candidate has high-resolution pixels across HST, JWST and Euclid Q1
        # combined, and both survey groups return a verdict on that same object.
        # Summing gives 2 and double-counts it.
        per = (load("highres-followup/verification-truth.json") or {}).get("perSurvey") or {}
        verifiable = [
            v.get("verifiable") for v in per.values()
            if isinstance(v, dict) and isinstance(v.get("verifiable"), int)
        ]
        return (max(verifiable) if verifiable else None), "distinct candidates verifiable at high resolution"

    if goal_id == "G9":
        evaluated = (load("anomaly-register.json") or {}).get("comparisonsEvaluated") or {}
        # The register carries a total AND its parts; check they agree rather than
        # trusting the total, since a stated total is exactly the kind of derived
        # number this script exists to catch.
        # Keys ending in -regions are region counts recorded alongside the
        # comparison counts, not addends. Including them adds 293 and invents a
        # disagreement.
        parts = {
            k: v for k, v in evaluated.items()
            if isinstance(v, int) and k != "total" and not k.endswith("-regions")
        }
        summed = sum(parts.values())
        stated = evaluated.get("total")
        if stated is not None and summed != stated:
            return summed, f"parts sum to {summed} but register states {stated}"
        return stated, f"comparisonsEvaluated.total, parts sum to {summed}"

    return None, "not recountable from a single manifest"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    card = json.loads(SCORECARD.read_text(encoding="utf-8"))
    print(f"{'id':4s} {'published':>10s} {'counted':>9s}  how")
    disagreements = []
    unchecked = []
    for goal in card.get("goals", []):
        goal_id = goal.get("id")
        published = goal.get("delivered")
        counted, how = recount(goal_id)
        if counted is None:
            unchecked.append((goal_id, how))
            print(f"{goal_id:4s} {str(published):>10s} {'-':>9s}  {how}")
            continue
        flag = "" if counted == published else "   <-- DISAGREES"
        if counted != published:
            disagreements.append((goal_id, published, counted, how))
        print(f"{goal_id:4s} {str(published):>10s} {counted:>9d}  {how}{flag}")

    if unchecked:
        print(f"\nnot recountable here ({len(unchecked)}): " + ", ".join(g for g, _ in unchecked))
        print("  These are not verified by this script. That is a weaker statement than correct.")
    if disagreements:
        print(f"\n{len(disagreements)} disagreement(s):")
        for goal_id, published, counted, how in disagreements:
            print(f"  {goal_id}: scorecard {published}, counted {counted} ({how})")
        if args.check:
            sys.exit(1)
    else:
        print("\nevery recountable figure matches its evidence")


if __name__ == "__main__":
    main()
