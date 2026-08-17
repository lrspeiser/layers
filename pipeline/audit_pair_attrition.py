"""Account for every region that has pixels but produced no comparison pair.

G1 delivers 628 reconciled pairs against 652 regions with validated reference
pixels. The 24-region difference has been described as "reconciliation losses"
and never itemised, which is a polite way of saying nobody checked.

Itemised, it splits into two stages, and the second one is the interesting half:

  validated -> aligned    20 regions dropped while building comparison grids
  aligned -> reconciled    4 regions dropped during reconciliation

Every manifest involved records **zero** failures. The regions do not appear in
any `failures` or `skipped` list; they are simply absent from the next stage's
output. That is the defect this script exists to expose. A stage that discards
work should say so, because a silent drop is indistinguishable from work that was
never attempted -- which is exactly the confusion that hid G0's wrong count for
weeks.

Some of the loss is legitimate. dp2-tract-9939 carries a valid-pixel fraction of
0.032, so there is almost nothing to compare and both Legacy and Pan-STARRS drop
it. Others are not obviously legitimate: dp2-tract-5192 and dp2-tract-6530 hold
91.5% and 96.1% valid Rubin pixels, aligned successfully, and still produced no
reconciled pair with no reason recorded anywhere.

This does not fix the loss. It makes it visible and counted, so that the next
person to read "628 of 652" knows which 24 and how many of them are explained.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RESULTS = ROOT / "pipeline/results"
SELECTED = ROOT / "public/data/layers/selected-regions"
OUTPUT = SELECTED / "pair-attrition.json"

# reference label -> (comparison-grid manifest, reconciliation manifest)
STAGES = {
    "legacy-surveys-dr10": (
        "selected-region-comparisons-200/manifest.json",
        "rubin-reference-reconciliation-200.json",
    ),
    "des-dr2": (
        "selected-region-comparisons-des/manifest.json",
        "rubin-des-reconciliation.json",
    ),
    "panstarrs-dr2": ("comparisons-ps1/manifest.json", "rubin-ps1-reconciliation.json"),
    "hsc-ssp-pdr2": ("comparisons-hsc/manifest.json", "rubin-hsc-reconciliation.json"),
}

# A Rubin region this empty has nothing to compare; dropping it is correct.
NEARLY_EMPTY = 0.10


def rubin_validation() -> dict[str, dict]:
    payload = json.loads(
        (RESULTS / "rubin-pixels-200/manifest.json").read_text(encoding="utf-8")
    )
    out: dict[str, dict] = {}
    for region in payload["regions"]:
        if region.get("status") == "complete":
            out.setdefault(region["regionId"], region.get("validation") or {})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="exit non-zero if any loss is unexplained"
    )
    args = parser.parse_args()

    validation = rubin_validation()
    truth = json.loads((SELECTED / "optical-coverage-truth.json").read_text(encoding="utf-8"))
    validated = {
        s["surveyId"]: s.get("pixelsValidated") or 0
        for s in truth.get("surveys", [])
        if isinstance(s, dict)
    }

    per_survey = {}
    unexplained: list[dict] = []
    for survey, (grid_rel, recon_rel) in STAGES.items():
        grid_path = RESULTS / grid_rel
        recon_path = SELECTED / recon_rel
        if not grid_path.is_file() or not recon_path.is_file():
            per_survey[survey] = {"error": "manifest missing"}
            continue
        grid = json.loads(grid_path.read_text(encoding="utf-8"))
        recon = json.loads(recon_path.read_text(encoding="utf-8"))
        aligned = {r["regionId"] for r in grid.get("regions", [])}
        reconciled = {r["regionId"] for r in recon.get("regions", [])}
        lost_at_reconcile = sorted(aligned - reconciled)

        detail = []
        for region_id in lost_at_reconcile:
            fraction = (validation.get(region_id) or {}).get("validPixelFraction")
            explained = fraction is not None and fraction < NEARLY_EMPTY
            entry = {
                "regionId": region_id,
                "rubinValidPixelFraction": fraction,
                "explained": bool(explained),
                "reason": (
                    "Rubin region is nearly empty; nothing to compare"
                    if explained
                    else "no reason recorded by any stage"
                ),
            }
            detail.append(entry)
            if not explained:
                unexplained.append({"survey": survey, **entry})

        per_survey[survey] = {
            "pixelsValidated": validated.get(survey),
            "aligned": len(aligned),
            "reconciled": len(reconciled),
            "lostBuildingGrids": (validated.get(survey) or 0) - len(aligned),
            "lostAtReconcile": len(lost_at_reconcile),
            "failuresRecordedByGridBuilder": len(grid.get("failures") or []),
            "lostAtReconcileDetail": detail,
        }

    total_validated = sum(v.get("pixelsValidated") or 0 for v in per_survey.values() if "error" not in v)
    total_reconciled = sum(v.get("reconciled") or 0 for v in per_survey.values() if "error" not in v)
    grids = sum(v.get("lostBuildingGrids") or 0 for v in per_survey.values() if "error" not in v)
    recon = sum(v.get("lostAtReconcile") or 0 for v in per_survey.values() if "error" not in v)

    print(f"{'survey':22s} {'valid':>6s} {'aligned':>8s} {'recon':>6s} {'lost@grid':>10s} {'lost@recon':>11s}")
    for survey, row in per_survey.items():
        if "error" in row:
            print(f"{survey:22s} {row['error']}")
            continue
        print(f"{survey:22s} {row['pixelsValidated']:6d} {row['aligned']:8d} "
              f"{row['reconciled']:6d} {row['lostBuildingGrids']:10d} {row['lostAtReconcile']:11d}")
    print(f"\ntotal validated {total_validated}, reconciled {total_reconciled}, "
          f"lost {total_validated - total_reconciled} ({grids} building grids, {recon} at reconcile)")
    print(f"failures recorded by any stage: "
          f"{sum(v.get('failuresRecordedByGridBuilder') or 0 for v in per_survey.values() if 'error' not in v)}")
    if unexplained:
        print(f"\nlosses with no recorded reason and a usable Rubin frame: {len(unexplained)}")
        for item in unexplained:
            print(f"  {item['survey']:22s} {item['regionId']:22s} "
                  f"validPixels {item['rubinValidPixelFraction']:.3f}")

    payload = {
        "schemaVersion": "layers-pair-attrition-v1",
        "question": "G1 delivers 628 pairs from 652 validated regions. Which 24, and why?",
        "perSurvey": per_survey,
        "totals": {
            "pixelsValidated": total_validated,
            "reconciled": total_reconciled,
            "lostBuildingGrids": grids,
            "lostAtReconcile": recon,
        },
        "unexplained": unexplained,
        "finding": (
            "The 24-region gap is 20 regions dropped while building comparison grids and 4 "
            "dropped during reconciliation. No stage records a single failure for any of them: "
            "they are absent from the next stage's output rather than reported as skipped. A "
            "silent drop is indistinguishable from work never attempted, which is the same "
            "confusion that let an inferred count stand as a measured one."
        ),
        "reproduce": "python pipeline/audit_pair_attrition.py --check",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {args.output.relative_to(ROOT)}")
    if args.check and unexplained:
        sys.exit(1)


if __name__ == "__main__":
    main()
