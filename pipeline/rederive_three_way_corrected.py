"""Re-derive the three-way optical comparison with the PS1 zeropoint corrected.

§28 concluded that the field-to-field scatter is "largely common to all three
references", which pointed away from any single survey's calibration. §29 then
found that this project's Pan-STARRS flux chain is about 0.28 mag too faint. So
§28 was measured with a known error present in one of its three legs, and its
conclusion has to be re-derived rather than assumed to survive.

The correction is not a constant. §29 measured offsets ranging 0.19 to 0.39 mag
across regions, and that matters for exactly the quantity §28 was reporting: a
uniform zeropoint shift moves a median and leaves a spread alone, but a
*field-dependent* zeropoint error inflates the spread. If PS1's apparent
field-to-field scatter is partly its own zeropoint wandering, then correcting per
region should shrink PS1's spread -- and "the scatter is common to all three"
would turn out to have been an artefact of leaving one leg uncalibrated.

So this applies the per-region offset where one was measured, and reports what
happens to both the median and the spread. Regions without a measured offset are
excluded rather than given the global median, because substituting an average for
a missing measurement is what makes a field-dependent effect look constant.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
SELECTED = ROOT / "public/data/layers/selected-regions"
OUTPUT = SELECTED / "three-way-optical-corrected.json"

SOURCES = {
    "legacy-surveys-dr10": SELECTED / "rubin-reference-reconciliation-200.json",
    "panstarrs-dr2": SELECTED / "rubin-ps1-reconciliation.json",
    "hsc-ssp-pdr2": SELECTED / "rubin-hsc-reconciliation.json",
}
PS1_VERIFICATION = SELECTED / "ps1-flux-chain-verification.json"


def scales(path: pathlib.Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for region in payload.get("regions", []):
        measured = (region.get("units") or {}).get("empiricalPointSourceScale") or {}
        value = measured.get("scale")
        if value and value > 0:
            out[region["regionId"]] = float(value)
    return out


def summarise(values) -> dict[str, float]:
    array = np.asarray(list(values), dtype=float)
    logs = np.log10(array)
    low, high = np.percentile(logs, [15.865, 84.135])
    return {
        "regions": int(array.size),
        "medianScale": float(np.median(array)),
        "fieldSpreadDex": float((high - low) / 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = parser.parse_args()

    per = {name: scales(path) for name, path in SOURCES.items()}

    verification = json.loads(PS1_VERIFICATION.read_text(encoding="utf-8"))
    # Our measured magnitude minus PS1's published one. Positive means our
    # conversion makes the reference too faint, so the reference flux must be
    # multiplied up, and the Rubin/reference ratio comes down.
    offsets = {
        r["regionId"]: float(r["medianOffsetMagVsR"])
        for r in verification.get("regions", [])
        if r.get("medianOffsetMagVsR") is not None
    }
    print(f"per-region PS1 offsets measured: {len(offsets)}")

    corrected = {}
    for region, ratio in per["panstarrs-dr2"].items():
        offset = offsets.get(region)
        if offset is None:
            continue
        corrected[region] = ratio * 10 ** (-0.4 * offset)

    # Compare on the sky all three measured, before and after.
    trio_common = set(per["legacy-surveys-dr10"]) & set(per["hsc-ssp-pdr2"]) & set(corrected)
    print(f"regions with legacy + HSC + a corrected PS1: {len(trio_common)}\n")

    before, after = {}, {}
    for name in ("legacy-surveys-dr10", "hsc-ssp-pdr2"):
        stat = summarise(per[name][r] for r in sorted(trio_common))
        before[name] = after[name] = stat
    before["panstarrs-dr2"] = summarise(per["panstarrs-dr2"][r] for r in sorted(trio_common))
    after["panstarrs-dr2"] = summarise(corrected[r] for r in sorted(trio_common))

    print(f"{'reference':22s} {'median before':>14s} {'median after':>13s} "
          f"{'spread before':>14s} {'spread after':>13s}")
    for name in before:
        print(f"{name:22s} {before[name]['medianScale']:14.4f} {after[name]['medianScale']:13.4f} "
              f"{before[name]['fieldSpreadDex']:14.4f} {after[name]['fieldSpreadDex']:13.4f}")

    b = [v["fieldSpreadDex"] for v in before.values()]
    a = [v["fieldSpreadDex"] for v in after.values()]
    bm = [v["medianScale"] for v in before.values()]
    am = [v["medianScale"] for v in after.values()]
    print(f"\nspread ratio  before {max(b)/min(b):.2f}x   after {max(a)/min(a):.2f}x")
    print(f"median agreement  before {max(bm)-min(bm):.4f}   after {max(am)-min(am):.4f}")

    ps1_shrink = (before["panstarrs-dr2"]["fieldSpreadDex"]
                  - after["panstarrs-dr2"]["fieldSpreadDex"])
    payload = {
        "schemaVersion": "layers-three-way-corrected-v1",
        "question": (
            "Section 28 said the field-to-field scatter is largely common to all three "
            "references. It was measured with this project's PS1 chain 0.28 mag out. Does the "
            "conclusion survive correcting it per region?"
        ),
        "method": (
            "Apply each region's own measured PS1 zeropoint offset to that region's flux ratio, "
            "then recompute median and spread on the sky all three references measured. Regions "
            "without a measured offset are dropped rather than given the global median: "
            "substituting an average for a missing measurement is precisely what would make a "
            "field-dependent effect look constant."
        ),
        "regionsCompared": len(trio_common),
        "perRegionOffsetsAvailable": len(offsets),
        "before": before,
        "after": after,
        "spreadRatioBefore": max(b) / min(b),
        "spreadRatioAfter": max(a) / min(a),
        "ps1SpreadReductionDex": ps1_shrink,
        "medianAgreementBefore": max(bm) - min(bm),
        "medianAgreementAfter": max(am) - min(am),
        "reproduce": "python pipeline/rederive_three_way_corrected.py",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nPS1 spread changed by {ps1_shrink:+.4f} dex once its own zeropoint is removed")
    print(f"wrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
