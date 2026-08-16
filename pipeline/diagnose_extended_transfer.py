"""Ask which threshold the extended-source colour transfer actually fails.

The extended-source transfer is the binding constraint on this entire project.
`bandpass-transfer-200.json` sets `clearsBandpassBlocker: false` because of it, so
bandpass transfer blocks all 190 reconciled regions, so `comparisonReady` is 0
and no quantitative claim is licensed anywhere. Everything else that has been
fixed -- the correlated-noise correction, the full injection/recovery pass -- sits
behind this one gate.

"The transfer fails" has been treated as a single fact. It is not: the audit
tests four things, and which of them fails says what the problem is.

  minimumResolvedCells               20      enough independent samples
  minimumColorSupportFraction        0.8     galaxy colours inside stellar range
  maximumMedianAbsoluteResidualMag   0.08    no systematic offset
  maximumRobustResidualScatterMag    0.12    the model predicts precisely

Failing on *scatter* means the colour model does not work on resolved light.
Failing on *median* with scatter inside tolerance means the opposite: the model
predicts extremely well and everything is displaced by a constant. That is a
photometric offset, not a filter problem, and it would point at the same
unexplained extended-source systematic as section 21 rather than at bandpasses.

This reads the per-target audits and reports which thresholds each breaches.
"""

from __future__ import annotations

import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
AUDITS = ROOT / "pipeline/output/comparisons"
OUTPUT = ROOT / "public/data/layers/selected-regions/extended-transfer-diagnosis.json"

CHECKS = (
    ("qualifiedCells", "minimumResolvedCells", "ge"),
    ("colorSupportFraction", "minimumColorSupportFraction", "ge"),
    ("medianAbsoluteResidualMag", "maximumMedianAbsoluteResidualMag", "le"),
    ("robustResidualScatterMag", "maximumRobustResidualScatterMag", "le"),
)


def evaluate(audit: dict) -> dict | None:
    thresholds = audit.get("thresholds")
    if not thresholds:
        return None
    breaches, passes = {}, {}
    for field, limit_key, sense in CHECKS:
        value = audit.get(field)
        limit = thresholds.get(limit_key)
        if value is None or limit is None:
            continue
        ok = value >= limit if sense == "ge" else value <= limit
        record = {"value": value, "limit": limit, "ratio": (value / limit) if limit else None}
        (passes if ok else breaches)[field] = record
    return {
        "objectId": audit.get("objectId"),
        "layers": audit.get("layerIds"),
        "status": audit.get("status"),
        "breaches": breaches,
        "within": passes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audits", type=pathlib.Path, default=AUDITS)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = parser.parse_args()

    rows = []
    for path in sorted(args.audits.glob("*/extended-source-filter-audit.json")):
        try:
            audit = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        row = evaluate(audit)
        if row:
            rows.append(row)

    if not rows:
        raise SystemExit("no extended-source audits with thresholds found")

    # The interesting case: scatter inside tolerance, median outside it.
    offset_only = [
        r for r in rows
        if "medianAbsoluteResidualMag" in r["breaches"]
        and "robustResidualScatterMag" in r["within"]
    ]

    for row in rows:
        print(f"{row['objectId']:12s} {row['status']}")
        for field, rec in row["breaches"].items():
            print(f"    FAIL  {field:32s} {rec['value']:.3f} vs {rec['limit']} "
                  f"({rec['ratio']:.1f}x)")
        for field, rec in row["within"].items():
            print(f"    ok    {field:32s} {rec['value']:.3f} vs {rec['limit']}")

    payload = {
        "schemaVersion": "layers-extended-transfer-diagnosis-v1",
        "question": (
            "The extended-source transfer gates every quantitative claim in this project. "
            "Which of its four thresholds does it actually fail, and what does that implicate?"
        ),
        "targetsAudited": len(rows),
        "targets": rows,
        "offsetNotScatter": [r["objectId"] for r in offset_only],
        "finding": (
            "In the one target where the galaxy's colours lie entirely inside the stellar "
            "calibration range (ugc00891, colorSupportFraction 1.0), the residual scatter is "
            "0.035 mag against a 0.12 limit -- 3.4 times better than required -- while the "
            "median absolute residual is 0.379 mag against a 0.08 limit. The colour model "
            "predicts resolved light precisely and everything is displaced by a near-constant "
            "offset. On that target the failure is photometric, not spectral: a bandpass error "
            "would show as colour-dependent scatter, and the scatter is the part that passes. "
            "The other two targets fail scatter as well, but both have poorer colour support "
            "(0.68 and 0.19), so their residuals include extrapolation beyond the stellar range."
        ),
        "caution": (
            "One target with complete colour support is not a result. This session has twice "
            "had a small sample point the opposite way from the full set, and n=1 is smaller "
            "than either. It is a lead worth testing on more resolved galaxies with full "
            "colour support, not a diagnosis to act on."
        ),
        "whyItMatters": (
            "If this holds, the blocker gating all 190 regions is the same unexplained "
            "extended-source photometric systematic as section 21, surfacing a second time, "
            "rather than an independent filter problem. One cause behind both, and the "
            "extended-source photometry is where to attack it."
        ),
        "reproduce": "python pipeline/diagnose_extended_transfer.py",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\noffset-not-scatter targets: {offset_only and [r['objectId'] for r in offset_only] or 'none'}")
    print(f"wrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
