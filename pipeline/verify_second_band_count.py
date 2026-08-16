"""Recount the validated second bands from the pixel manifests themselves.

`dp2-band-availability.json` published `secondBandValidated: 167` and
`unreachableAfterAcquisition: 6`. Those sum to the measured ceiling of 173, which
is what made them look right. Counted from the acquisition manifests, the split
is **161 validated and 12 failed** -- the same total, a different story, and the
delivered figure was overstated by six.

The likely origin is that 167 was derived as 173 minus 6 rather than counted. A
number that is inferred from two other numbers will always be self-consistent
with them, which is exactly why it survived: every check that added it back up
passed.

This counts instead. A region has a validated second band when two distinct
bands appear for it with `status: "complete"` across every
`rubin-pixels-200*` manifest -- the primary run plus each gap run. Anything else
is a failed attempt or an absent band, and the two are distinguished because they
mean different things:

  never attempted   DP2 holds exactly one band for that region, so no strategy
                    yields a second. 26 regions, confirmed against the cached SIA
                    discovery responses.
  attempted, failed  a second band exists and its pixels did not pass validation.
                    12 regions. These are the real shortfall against the ceiling.

Run with --check to fail when the published manifests disagree with the count.
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFESTS = "pipeline/results/rubin-pixels-200*/manifest.json"
PLAN = ROOT / "pipeline/results/acquisition-200/discovery/acquisition-plan.json"
AVAILABILITY = ROOT / "public/data/layers/selected-regions/dp2-band-availability.json"
SCORECARD = ROOT / "public/data/layers/goal-scorecard.json"


def bands_by_region() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Complete and failed bands per region, across every acquisition pass."""
    complete: dict[str, set[str]] = {}
    failed: dict[str, set[str]] = {}
    for path in sorted(glob.glob(str(ROOT / MANIFESTS))):
        payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        for region in payload.get("regions", []):
            region_id, band, status = (
                region.get("regionId"),
                region.get("band"),
                region.get("status"),
            )
            if not region_id or not band:
                continue
            target = complete if status == "complete" else failed
            target.setdefault(region_id, set()).add(band)
    return complete, failed


def bands_offered() -> dict[str, set[str]]:
    """What DP2 actually holds per region, from the cached SIA discovery responses."""
    if not PLAN.is_file():
        return {}
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    offered: dict[str, set[str]] = {}
    for job in plan.get("jobs", []):
        if job.get("phase") != "discover":
            continue
        region_id = (job.get("region") or {}).get("id")
        cache = ROOT / ((job.get("cache") or {}).get("path") or "")
        if not region_id or not cache.is_file():
            continue
        text = cache.read_text(encoding="utf-8", errors="replace")
        offered[region_id] = set(re.findall(r"<TD>([ugrizy])</TD>", text))
    return offered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit non-zero on disagreement")
    args = parser.parse_args()

    complete, failed = bands_by_region()
    offered = bands_offered()

    two_band = sorted(r for r, v in complete.items() if len(v) >= 2)
    one_band = {r for r, v in complete.items() if len(v) == 1}
    attempted_and_failed = sorted(one_band & set(failed))
    never_attempted = sorted(one_band - set(failed))

    # A region only counts as genuinely capped if DP2 offers it nothing else.
    capped = [r for r in never_attempted if len(offered.get(r, set())) <= 1]
    surprising = [r for r in never_attempted if len(offered.get(r, set())) > 1]

    print(f"regions with pixels            : {len(complete)}")
    print(f"validated two-band             : {len(two_band)}")
    print(f"one band, second band failed   : {len(attempted_and_failed)}")
    print(f"one band, DP2 holds only one   : {len(capped)}")
    if surprising:
        print(f"one band, but DP2 offers more  : {len(surprising)}  <-- recoverable: {surprising[:8]}")
    print(f"\nceiling check: {len(two_band)} + {len(attempted_and_failed)} = "
          f"{len(two_band) + len(attempted_and_failed)}")

    published = json.loads(AVAILABILITY.read_text(encoding="utf-8"))
    mismatches = []
    if published.get("secondBandValidated") != len(two_band):
        mismatches.append(
            f"dp2-band-availability.secondBandValidated is "
            f"{published.get('secondBandValidated')}, counted {len(two_band)}"
        )
    if published.get("unreachableAfterAcquisition") != len(attempted_and_failed):
        mismatches.append(
            f"dp2-band-availability.unreachableAfterAcquisition is "
            f"{published.get('unreachableAfterAcquisition')}, counted {len(attempted_and_failed)}"
        )
    if SCORECARD.is_file():
        scorecard = json.loads(SCORECARD.read_text(encoding="utf-8"))
        for goal in scorecard.get("goals", []):
            if goal.get("id") == "G0" and goal.get("delivered") != len(two_band):
                mismatches.append(
                    f"goal-scorecard G0.delivered is {goal.get('delivered')}, "
                    f"counted {len(two_band)}"
                )

    if mismatches:
        print("\nDISAGREEMENT with published manifests:")
        for line in mismatches:
            print(f"  {line}")
        if args.check:
            sys.exit(1)
    else:
        print("\npublished manifests agree with the count")


if __name__ == "__main__":
    main()
