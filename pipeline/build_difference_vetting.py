"""Say, per region, whether the anomaly scanner trusted the field or refused it.

The difference explorer ranks regions by how much of the image disagrees, raw and
unfiltered. That is deliberate -- it is a "look here" list, and filtering it would
hide what the pipeline is actually doing. But it means a region can sit near the
top of the ranking while the anomaly scanner has already thrown it out, and a
reader has no way to tell.

Tract 11411 is the case that prompted this. It ranks 4th of 190 against Legacy,
with 3.2% of pixels above 5 sigma and three off-source peaks. The scanner skipped
it: "flux transfer not corroborated", because it matched only 15 stars where 20
are required before the brightness calibration can be trusted. Both facts are
true and only one was visible.

So this joins the two. For every region and every reference it records whether
the scanner scanned or skipped, why it skipped, and how many candidates survived.
A region the scanner refused is not thereby uninteresting -- it is unvetted, and
the difference between those is the whole point.
"""

from __future__ import annotations

import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SELECTED = ROOT / "public/data/layers/selected-regions"
OUTPUT = SELECTED / "difference-vetting.json"

# Anomaly-scan manifests keyed by the reference they scanned against, matched to
# the explorer's own reference ids.
SCANS = {
    "legacy": SELECTED / "region-anomalies-200.json",
    "hsc": SELECTED / "region-anomalies-hsc.json",
}


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = parser.parse_args()

    vetting: dict[str, dict[str, dict]] = {}
    for reference, path in SCANS.items():
        payload = load(path)
        if not payload:
            continue
        for region in payload.get("regions", []):
            rid = region.get("regionId")
            if not rid:
                continue
            vetting.setdefault(rid, {})[reference] = {
                "verdict": "scanned",
                "candidates": region.get("candidateCount")
                or len(region.get("candidates") or []),
            }
        for skip in payload.get("skipped", []):
            rid = skip.get("regionId")
            if not rid:
                continue
            vetting.setdefault(rid, {})[reference] = {
                "verdict": "refused",
                "reason": skip.get("reason"),
            }

    scanned = sum(1 for v in vetting.values() for x in v.values() if x["verdict"] == "scanned")
    refused = sum(1 for v in vetting.values() for x in v.values() if x["verdict"] == "refused")
    reasons: dict[str, int] = {}
    for v in vetting.values():
        for x in v.values():
            if x["verdict"] == "refused":
                reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1

    payload = {
        "schemaVersion": "layers-difference-vetting-v1",
        "purpose": (
            "The difference explorer ranks by raw disagreeing area and does not filter. This "
            "records, per region and reference, whether the anomaly scanner trusted the field "
            "enough to look for candidates in it, so a high rank and an unvetted field can be "
            "told apart."
        ),
        "note": (
            "A refused region is not uninteresting. It is unvetted: the scanner could not "
            "verify something it needs before a residual means anything, most often the flux "
            "calibration. Tract 11411 ranks 4th of 190 against Legacy and was refused for "
            "exactly that reason, on 15 matched stars against a floor of 20."
        ),
        "counts": {"scanned": scanned, "refused": refused, "byReason": reasons},
        "regions": vetting,
        "reproduce": "python pipeline/build_difference_vetting.py",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"scanned {scanned}, refused {refused}")
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {count:4d}  {reason}")
    print(f"wrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
