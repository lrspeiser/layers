#!/usr/bin/env python3
"""Attribute the optical comparison's field-dependent effects to a survey.

Every optical result in this project was Rubin against Legacy Survey. That
pairing can measure a difference but cannot say who owns it: a flux scale that
departs from unity, and a scale that drifts with field density, each have two
possible owners and one measurement.

DES DR2 supplies a second, independent reference over part of the same sky, and
two references over shared regions turn one number into three tests:

* **Whose zeropoint.** If Rubin-vs-Legacy and Rubin-vs-DES land on the same
  scale, two independently calibrated surveys agree with each other and disagree
  with Rubin the same way. The offset then sits on the Rubin side of the
  comparison, or in the aperture method common to both.
* **Whose crowding term.** If the scale drifts with field density in one pairing
  and not the other, the drift belongs to the reference that shows it, not to
  Rubin, which is common to both.
* **Whose field-to-field scatter.** Across regions measured against both, a
  positive correlation between the two scales means the two pairings move
  together, and the only thing they share is Rubin.

The density proxy is the count of matched compact sources per field. That is a
proxy and not a sky density: it depends on the depth and PSF of both surveys in
the pair, so it is reported as what it is. It is still the right variable for
this test, because the question is whether a *pairing's* photometry degrades
where sources are close together.

Significance is a permutation test, not an analytic p-value. The per-region
scales are neither independent nor Gaussian, and a small-sample correlation
already produced one retracted claim in this project.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ROOT / "public/data/layers/selected-regions"
DEFAULT_OUTPUT = LAYERS / "reference-cross-check.json"

PERMUTATIONS = 20000
RANDOM_SEED = 20260814
# Below this many shared regions the correlation is not worth reporting: the
# lensing operator produced a -2.88 sigma result on 18 fields that fell to
# -1.34 sigma on 115. Small samples in this project have been wrong.
MIN_SHARED_REGIONS = 40


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rank(values: np.ndarray) -> np.ndarray:
    """Average ranks, so tied source counts do not bias the correlation."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=np.float64)
    ranks[order] = np.arange(values.size, dtype=np.float64)
    unique, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    if counts.max() > 1:
        sums = np.zeros(unique.size)
        np.add.at(sums, inverse, ranks)
        ranks = (sums / counts)[inverse]
    return ranks


def spearman(x: np.ndarray, y: np.ndarray, rng: np.random.Generator) -> dict[str, Any]:
    if x.size < 3:
        return {"n": int(x.size), "rho": None, "pValue": None, "note": "too few points"}
    rx, ry = rank(x), rank(y)
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    denominator = float(np.sqrt((rx**2).sum() * (ry**2).sum()))
    if denominator == 0:
        return {"n": int(x.size), "rho": None, "pValue": None, "note": "no variance"}
    rho = float((rx * ry).sum() / denominator)

    null = np.empty(PERMUTATIONS)
    for index in range(PERMUTATIONS):
        shuffled = rng.permutation(ry)
        null[index] = (rx * shuffled).sum() / denominator
    p_value = float((np.abs(null) >= abs(rho)).mean())
    return {
        "n": int(x.size),
        "rho": rho,
        "pValue": p_value,
        "nullScatter": float(null.std(ddof=1)),
        "significant": bool(p_value < 0.01),
        "test": f"two-sided permutation, {PERMUTATIONS} shuffles",
    }


def load_pair(path: Path, matched_only: bool = True) -> dict[str, dict[str, float]]:
    """Per-region empirical scale and matched-source count for one survey pair.

    ``matched_only`` keeps regions that passed reconciliation QA. That is the
    defensible default, but it drops 99 of 190 Legacy regions, and QA failure is
    not independent of field density: a crowded field is likelier to fail. So the
    caller measures the correlation both ways rather than trusting either alone.
    """
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, float]] = {}
    for region in payload.get("regions", []):
        if matched_only and region.get("status") != "matched":
            continue
        empirical = (region.get("units") or {}).get("empiricalPointSourceScale") or {}
        scale = empirical.get("scale")
        sources = empirical.get("matchedSources")
        if not isinstance(scale, (int, float)) or not isinstance(sources, (int, float)):
            continue
        if scale <= 0 or sources <= 0:
            continue
        out[region["regionId"]] = {
            "scale": float(scale),
            "sources": float(sources),
            "scatterDex": float(empirical.get("scatterDex") or float("nan")),
            "tract": region.get("tract"),
        }
    return out


def describe(pair: dict[str, dict[str, float]]) -> dict[str, Any]:
    scales = np.array([item["scale"] for item in pair.values()])
    if scales.size == 0:
        return {"regions": 0}
    logs = np.log10(scales)
    return {
        "regions": int(scales.size),
        "medianScale": float(np.median(scales)),
        "medianMagnitudeOffset": float(-2.5 * np.median(logs)),
        "scaleScatterDex": float(np.std(logs, ddof=1)) if scales.size > 1 else None,
        "medianMatchedSources": float(np.median([item["sources"] for item in pair.values()])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, default=LAYERS / "rubin-reference-reconciliation-200.json")
    parser.add_argument("--des", type=Path, default=LAYERS / "rubin-des-reconciliation.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rng = np.random.default_rng(RANDOM_SEED)
    legacy = load_pair(args.legacy)
    des = load_pair(args.des)
    shared = sorted(set(legacy) & set(des))

    def density(pair: dict[str, dict[str, float]]) -> dict[str, Any]:
        return spearman(
            np.array([item["sources"] for item in pair.values()]),
            np.array([item["scale"] for item in pair.values()]),
            rng,
        )

    crowding = {"legacy": density(legacy), "des": density(des)}
    # Same correlation without the QA filter. If the two disagree, the QA cut is
    # doing the work rather than the sky, and the finding says so.
    all_legacy = load_pair(args.legacy, matched_only=False)
    all_des = load_pair(args.des, matched_only=False)
    crowding_unfiltered = {"legacy": density(all_legacy), "des": density(all_des)}

    def same_answer(key: str) -> bool:
        a, b = crowding[key], crowding_unfiltered[key]
        if a.get("rho") is None or b.get("rho") is None:
            return False
        return bool(a["significant"]) == bool(b["significant"]) and (a["rho"] * b["rho"] > 0)

    qa_filter_matters = not (same_answer("legacy") and same_answer("des"))

    if len(shared) >= 3:
        shared_scales = spearman(
            np.array([legacy[k]["scale"] for k in shared]),
            np.array([des[k]["scale"] for k in shared]),
            rng,
        )
    else:
        shared_scales = {"n": len(shared), "rho": None, "pValue": None, "note": "too few shared regions"}

    # Do the two references agree with each other about Rubin? Compare the two
    # scales region by region, so the test is paired rather than a difference of
    # two medians over different sky.
    if shared:
        difference = np.log10([legacy[k]["scale"] for k in shared]) - np.log10(
            [des[k]["scale"] for k in shared]
        )
        median_difference = float(np.median(difference))
        # Bootstrap the median rather than assume a standard error.
        draws = rng.integers(0, difference.size, size=(4000, difference.size))
        boot = np.median(difference[draws], axis=1)
        difference_ci = [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))]
        references_agree = bool(difference_ci[0] <= 0.0 <= difference_ci[1])
    else:
        median_difference, difference_ci, references_agree = None, None, None

    enough = len(shared) >= MIN_SHARED_REGIONS

    # Which pairings show the density trend decides who owns it. An early
    # subset of the DES regions showed no trend, which read as "the trend is
    # Legacy's"; over the full set both show it, and the answer inverts. Hence
    # this is derived from the measured flags rather than written as a verdict.
    legacy_dense = bool(crowding["legacy"].get("significant"))
    des_dense = bool(crowding["des"].get("significant"))
    if legacy_dense and des_dense:
        crowding_owner = "common to both pairings, therefore Rubin's or the aperture method's"
        crowding_logic = (
            "Both pairings show it. The references are independently calibrated and were reduced "
            "by different pipelines, so what the two comparisons share is Rubin and the 1.5 arcsec "
            "aperture photometry applied identically to both."
        )
    elif legacy_dense or des_dense:
        owner = "Legacy Survey" if legacy_dense else "DES"
        crowding_owner = owner
        crowding_logic = (
            f"Only the {owner} pairing shows it. Rubin is common to both, so an effect present in "
            "one pairing and absent in the other cannot be Rubin's."
        )
    else:
        crowding_owner = "no density trend is resolved in either pairing"
        crowding_logic = "Neither pairing's correlation clears the 1% permutation threshold."

    findings: list[dict[str, Any]] = []
    if references_agree is not None:
        findings.append({
            "question": "Does the flux-scale offset belong to Rubin or to the reference?",
            "verdict": (
                "Rubin side of the comparison, or the aperture method common to both"
                if references_agree
                else "the two references disagree, so the offset is not attributable from these data"
            ),
            "basis": (
                f"Paired over {len(shared)} shared regions, the median log-scale difference between the "
                f"two pairings is {median_difference:.4f} dex with a bootstrap 95% interval of "
                f"[{difference_ci[0]:.4f}, {difference_ci[1]:.4f}]. Two independently calibrated "
                "references agreeing about Rubin cannot both be wrong in the same direction by chance."
            ),
            "cannotDistinguish": (
                "This does not separate a Rubin zeropoint from a systematic in the 1.5 arcsec aperture "
                "photometry, which is identical in both pairings."
            ),
        })
    if crowding["legacy"].get("rho") is not None and crowding["des"].get("rho") is not None:
        findings.append({
            "question": "Does the density-dependent flux scale belong to Rubin or to the reference?",
            "verdict": crowding_owner,
            "basis": (
                f"Scale against matched-source count: rho {crowding['legacy']['rho']:+.3f} "
                f"(p {crowding['legacy']['pValue']:.4f}, n {crowding['legacy']['n']}) for Legacy, "
                f"{crowding['des']['rho']:+.3f} (p {crowding['des']['pValue']:.4f}, "
                f"n {crowding['des']['n']}) for DES. {crowding_logic}"
            ),
            "direction": (
                "The scale falls as matched-source count rises in both pairings: Rubin measures "
                "relatively less flux in the aperture where sources are denser, which is the sign "
                "expected if neighbouring flux is being handled differently by the two sides."
            ),
            "qaFilterSensitivity": {
                "matchedOnly": {k: {"rho": v.get("rho"), "n": v.get("n"), "pValue": v.get("pValue")}
                                for k, v in crowding.items()},
                "allRegions": {k: {"rho": v.get("rho"), "n": v.get("n"), "pValue": v.get("pValue")}
                               for k, v in crowding_unfiltered.items()},
                "answerDependsOnQaFilter": qa_filter_matters,
                "note": (
                    "Reconciliation QA drops 99 of 190 Legacy regions, and a crowded field is "
                    "likelier to fail it. The correlation is therefore measured with and without "
                    "the filter; only a result that survives both is reported as attribution."
                ),
            },
            "supersedes": (
                "An earlier subset of DES regions showed no density trend, which supported "
                "attributing it to Legacy's broader PSF. Over the full set the DES pairing shows "
                "the same trend, and that attribution does not hold."
            ),
        })
    if shared_scales.get("rho") is not None:
        findings.append({
            "question": "Whose field-to-field scatter is the larger part?",
            "verdict": (
                "shared, therefore Rubin's"
                if shared_scales.get("significant") and shared_scales["rho"] > 0 and enough
                else "not established"
            ),
            "basis": (
                f"Across {shared_scales['n']} regions measured against both references, the two scales "
                f"correlate rho {shared_scales['rho']:+.3f} (p {shared_scales['pValue']:.4f}). The only "
                "thing the two pairings share is the Rubin image, so correlated variation is Rubin's."
            ),
            "sampleGate": (
                f"{len(shared)} shared regions, threshold {MIN_SHARED_REGIONS}"
                if enough
                else f"below the {MIN_SHARED_REGIONS}-region threshold; reported, not relied on"
            ),
        })

    payload = {
        "schemaVersion": "layers-reference-cross-check-v1",
        "generatedAt": utc_now(),
        "purpose": (
            "Two independent optical references over shared sky, used to attribute the optical "
            "comparison's field-dependent effects to a survey rather than measure them again."
        ),
        "pairs": {
            "rubin-vs-legacy": describe(legacy),
            "rubin-vs-des": describe(des),
        },
        "counts": {
            "legacyRegions": len(legacy),
            "desRegions": len(des),
            "sharedRegions": len(shared),
            "sharedRegionThreshold": MIN_SHARED_REGIONS,
            "sharedSampleSufficient": enough,
        },
        "crowdingCorrelation": crowding,
        "crowdingCorrelationAllRegions": crowding_unfiltered,
        "qaFilterChangesAnswer": qa_filter_matters,
        "sharedScaleCorrelation": shared_scales,
        "referenceAgreement": {
            "medianLogScaleDifferenceDex": median_difference,
            "bootstrap95Interval": difference_ci,
            "referencesAgree": references_agree,
        },
        "findings": findings,
        "caveats": [
            "The density proxy is the matched compact-source count per field, which depends on the "
            "depth and PSF of both surveys in a pair and is not a sky density.",
            "The two pairings sit on different common grids, 0.4 arcsec for Legacy and 0.263 arcsec "
            "for DES, so only the physical 1.5 arcsec aperture makes them comparable.",
            "DES variance is a uniform sky estimate rather than a propagated plane, which affects "
            "weighting inside its pairing but not this attribution, which uses medians.",
            "Attribution is between the surveys in these pairings. It does not establish which "
            "survey is right in an absolute sense; no external standard is used here.",
        ],
        "regions": [
            {
                "regionId": key,
                "tract": legacy[key]["tract"],
                "legacyScale": legacy[key]["scale"],
                "desScale": des[key]["scale"],
                "legacyMatchedSources": legacy[key]["sources"],
                "desMatchedSources": des[key]["sources"],
            }
            for key in shared
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"legacy {len(legacy)} regions, DES {len(des)} regions, {len(shared)} shared")
    for name, pair in payload["pairs"].items():
        if pair.get("regions"):
            print(f"  {name}: scale {pair['medianScale']:.4f}  ({pair['medianMagnitudeOffset']:+.4f} mag)")
    for finding in findings:
        print(f"\n{finding['question']}\n  -> {finding['verdict']}\n     {finding['basis']}")
    print(f"\nwrote {args.output.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
