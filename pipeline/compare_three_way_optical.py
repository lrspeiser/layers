"""Ask whether the field-dependent flux offset belongs to Rubin or to the reference.

This is what G1 was actually for, and it needed a third independent optical
reference to be answerable at all. With HSC PDR2 fetched there are now four:
Legacy DR10 and DES DR2 (both DECam r), Pan-STARRS DR2 (PS1 r), and HSC PDR2
(HSC r2 and r). Different instruments, different filters, different photometric
calibrations, measured on identical Rubin pixels.

The logic is simple. Every region yields an empirical compact-source flux ratio,
Rubin over reference. If the field-to-field *spread* in that ratio comes from the
reference survey's own calibration, it shrinks when the reference changes. If it
comes from Rubin, it stays put no matter which reference is used.

**The control matters more than the comparison.** Run across each survey's own
region set, the spread ranges over a factor of 6 and looks decisively
reference-driven. Restricted to the regions all three measured -- same sky, same
Rubin pixels -- the factor falls to about 1.35. Most of the apparent
reference-dependence was which sky each survey happens to cover, not the survey.
Comparing surveys on their own footprints answers a different question from the
one being asked, and answers it wrongly.

So the unmatched numbers are reported here too, precisely because they are the
trap: they are what this analysis would have concluded without the control.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
SELECTED = ROOT / "public/data/layers/selected-regions"
OUTPUT = SELECTED / "three-way-optical.json"

# Colour terms from measure_synthetic_bandpass.py: mag per mag of Rubin (g-r).
REFERENCES = {
    "legacy-surveys-dr10": (SELECTED / "rubin-reference-reconciliation-200.json", "DECam r", -0.0800),
    "des-dr2": (SELECTED / "rubin-des-reconciliation.json", "DECam r", -0.0800),
    "panstarrs-dr2": (SELECTED / "rubin-ps1-reconciliation.json", "PS1 r", +0.0072),
    "hsc-ssp-pdr2": (SELECTED / "rubin-hsc-reconciliation.json", "HSC r2 / r", +0.0051),
}
# Whose absolute flux chain this project has independently checked.
CHAIN_VERIFIED = {"legacy-surveys-dr10": True, "des-dr2": True,
                  "panstarrs-dr2": False, "hsc-ssp-pdr2": False}


def scales(path: pathlib.Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for region in payload.get("regions", []):
        measured = (region.get("units") or {}).get("empiricalPointSourceScale") or {}
        value = measured.get("scale")
        if value and value > 0:
            out[region["regionId"]] = float(value)
    return out


def summarise(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    logs = np.log10(array)
    low, high = np.percentile(logs, [15.865, 84.135])
    return {
        "regions": int(array.size),
        "medianScale": float(np.median(array)),
        "fieldSpreadDex": float((high - low) / 2),
        "fieldSpreadMag": float((high - low) / 2 * 2.5),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = parser.parse_args()

    per_survey = {name: scales(path) for name, (path, _, _) in REFERENCES.items()}

    own, matched = {}, {}
    for name, values in per_survey.items():
        if values:
            own[name] = summarise(list(values.values()))

    # The three with enough overlap to compare on identical sky. DES is left out
    # of the matched set: its footprint shares too few regions with HSC to make a
    # three-way intersection worth quoting.
    trio = ("legacy-surveys-dr10", "panstarrs-dr2", "hsc-ssp-pdr2")
    common = set.intersection(*(set(per_survey[n]) for n in trio if per_survey[n]))
    for name in trio:
        if common:
            matched[name] = summarise([per_survey[name][r] for r in sorted(common)])

    own_spreads = [v["fieldSpreadDex"] for v in own.values()]
    matched_spreads = [v["fieldSpreadDex"] for v in matched.values()]
    own_ratio = max(own_spreads) / min(own_spreads) if own_spreads else None
    matched_ratio = max(matched_spreads) / min(matched_spreads) if matched_spreads else None

    print(f"{'reference':22s} {'n':>4s} {'median':>9s} {'spread dex':>11s}   (own footprint)")
    for name, v in own.items():
        print(f"  {name:20s} {v['regions']:4d} {v['medianScale']:9.4f} {v['fieldSpreadDex']:11.4f}")
    print(f"\nmatched on {len(common)} regions measured by all three:")
    for name, v in matched.items():
        print(f"  {name:20s} {v['regions']:4d} {v['medianScale']:9.4f} {v['fieldSpreadDex']:11.4f}")
    print(f"\nspread ratio  own footprints {own_ratio:.2f}x  ->  matched sky {matched_ratio:.2f}x")

    payload = {
        "schemaVersion": "layers-three-way-optical-v1",
        "question": (
            "Does the field-to-field spread in the Rubin/reference flux ratio come from the "
            "reference survey's calibration or from Rubin? A reference artefact shrinks when "
            "the reference changes; a Rubin systematic does not."
        ),
        "references": {
            name: {
                "filter": REFERENCES[name][1],
                "colourTermPerMag": REFERENCES[name][2],
                "absoluteChainVerified": CHAIN_VERIFIED[name],
            }
            for name in REFERENCES
        },
        "ownFootprint": own,
        "matchedSky": matched,
        "matchedRegions": len(common),
        "spreadRatioOwnFootprint": own_ratio,
        "spreadRatioMatchedSky": matched_ratio,
        "control": (
            "The unmatched comparison is reported because it is the trap. Across each survey's "
            "own regions the spread varies by about 6x and reads as decisively "
            "reference-driven. On the sky all three measured it falls to about 1.35x. Most of "
            "the apparent reference-dependence was footprint, not survey."
        ),
        "finding": (
            "On identical sky and identical Rubin pixels the spreads are close, so the "
            "field-to-field scatter is largely COMMON to all three references. That points away "
            "from any one survey's calibration and toward Rubin or the sky itself. HSC is "
            "modestly tighter, consistent with a small reference-side component on top of a "
            "larger shared one. The medians are the clearer result: Legacy and HSC "
            "independently put Rubin about 7-9% faint, using different instruments, filters and "
            "calibrations. Pan-STARRS is the outlier at +16%, and Pan-STARRS is also the one "
            "reference here whose absolute flux chain this project has never verified -- its "
            "conversion rests on an EXPTIME convention taken from a header. The simplest "
            "reading is that the PS1 zeropoint is wrong, not that Rubin is bright."
        ),
        "doesNotShow": (
            "This does not license an astrophysical claim. It is a calibration comparison on "
            "compact sources, the bandpass blocker is still open on every region, and the "
            "extended-source transfer has never passed QA."
        ),
        "reproduce": "python pipeline/compare_three_way_optical.py",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
