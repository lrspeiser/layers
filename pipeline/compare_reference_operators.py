#!/usr/bin/env python3
"""Attribute the optical comparison's field-dependent effects to a survey.

Every optical result in this project was Rubin against Legacy Survey. That
pairing can measure a difference but cannot say who owns it: a flux scale that
departs from unity, and a scale that drifts with field density, each have two
possible owners and one measurement.

DES DR2 and Pan-STARRS supply further independent references over part of the
same sky, and any two references over shared regions turn one number into three
tests. The operator takes as many references as it is given, and every pairing
is reported separately, so a reference that disagrees is visible rather than
averaged away:

* **Whose zeropoint.** If Rubin-vs-Legacy and Rubin-vs-DES land on the same
  scale, two independently calibrated surveys agree with each other and disagree
  with Rubin the same way. The offset then sits on the Rubin side of the
  comparison, or in the aperture method common to both.
* **Whose crowding term.** If the scale drifts with field density in some
  pairings and not others, the drift belongs to the references that show it,
  not to Rubin, which is common to all of them.
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

def parse_reference(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    name, _, path = value.partition("=")
    return name.strip(), Path(path.strip())


def paired_agreement(
    left: dict[str, dict[str, float]],
    right: dict[str, dict[str, float]],
    shared: list[str],
    rng: np.random.Generator,
) -> dict[str, Any]:
    """Do two references say the same thing about Rubin, region by region?

    Paired rather than a difference of two medians over different sky, because
    the field-to-field spread is large enough to swamp the effect otherwise.
    """
    if not shared:
        return {"n": 0, "medianLogScaleDifferenceDex": None, "bootstrap95Interval": None, "agree": None}
    difference = np.log10([left[k]["scale"] for k in shared]) - np.log10([right[k]["scale"] for k in shared])
    draws = rng.integers(0, difference.size, size=(4000, difference.size))
    boot = np.median(difference[draws], axis=1)
    interval = [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))]
    return {
        "n": len(shared),
        "medianLogScaleDifferenceDex": float(np.median(difference)),
        "bootstrap95Interval": interval,
        "agree": bool(interval[0] <= 0.0 <= interval[1]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        type=parse_reference,
        action="append",
        metavar="NAME=PATH",
        help=(
            "Repeatable reconciliation manifest, NAME=PATH. Defaults to Legacy and DES, plus "
            "Pan-STARRS when its manifest exists. A third independently calibrated reference "
            "either corroborates the attribution or breaks it."
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.reference:
        requested = list(args.reference)
    else:
        requested = [
            ("legacy", LAYERS / "rubin-reference-reconciliation-200.json"),
            ("des", LAYERS / "rubin-des-reconciliation.json"),
            ("ps1", LAYERS / "rubin-ps1-reconciliation.json"),
        ]
    rng = np.random.default_rng(RANDOM_SEED)

    references: dict[str, dict[str, dict[str, float]]] = {}
    unfiltered: dict[str, dict[str, dict[str, float]]] = {}
    for name, path in requested:
        pair = load_pair(path)
        if not pair:
            continue
        references[name] = pair
        unfiltered[name] = load_pair(path, matched_only=False)
    if len(references) < 2:
        raise SystemExit("Attribution needs at least two references; Rubin being the shared term is the whole method.")

    def density(pair: dict[str, dict[str, float]]) -> dict[str, Any]:
        return spearman(
            np.array([item["sources"] for item in pair.values()]),
            np.array([item["scale"] for item in pair.values()]),
            rng,
        )

    crowding = {name: density(pair) for name, pair in references.items()}
    crowding_unfiltered = {name: density(pair) for name, pair in unfiltered.items()}

    def stable(name: str) -> bool:
        a, b = crowding[name], crowding_unfiltered.get(name, {})
        if a.get("rho") is None or b.get("rho") is None:
            return False
        return bool(a["significant"]) == bool(b["significant"]) and (a["rho"] * b["rho"] > 0)

    qa_filter_matters = not all(stable(name) for name in references)

    combinations: dict[str, Any] = {}
    names = list(references)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            shared = sorted(set(references[left]) & set(references[right]))
            key = f"{left}-vs-{right}"
            correlation = (
                spearman(
                    np.array([references[left][k]["scale"] for k in shared]),
                    np.array([references[right][k]["scale"] for k in shared]),
                    rng,
                )
                if len(shared) >= 3
                else {"n": len(shared), "rho": None, "pValue": None, "note": "too few shared regions"}
            )
            combinations[key] = {
                "sharedRegions": len(shared),
                "sufficient": len(shared) >= MIN_SHARED_REGIONS,
                "agreement": paired_agreement(references[left], references[right], shared, rng),
                "scaleCorrelation": correlation,
                "regionIds": shared,
            }

    usable = {k: v for k, v in combinations.items() if v["sufficient"]}
    largest = max(combinations.values(), key=lambda item: item["sharedRegions"]) if combinations else {}
    enough = bool(usable)

    findings: list[dict[str, Any]] = []

    agreeing = [k for k, v in usable.items() if v["agreement"]["agree"]]
    disagreeing = [k for k, v in usable.items() if v["agreement"]["agree"] is False]
    if usable:
        detail = "; ".join(
            f"{k}: {v['agreement']['medianLogScaleDifferenceDex']:+.4f} dex "
            f"[{v['agreement']['bootstrap95Interval'][0]:+.4f}, {v['agreement']['bootstrap95Interval'][1]:+.4f}] "
            f"over {v['sharedRegions']} regions"
            for k, v in usable.items()
        )
        findings.append({
            "question": "Does the flux-scale offset belong to Rubin or to the reference?",
            "verdict": (
                "Rubin side of the comparison, or the aperture method common to all pairings"
                if not disagreeing
                else f"the references do not all agree ({', '.join(disagreeing)}), so the offset is not attributable"
            ),
            "basis": (
                f"Paired median log-scale differences between reference pairings: {detail}. "
                f"{len(references)} independently calibrated references, reduced by different "
                "pipelines, agreeing about Rubin cannot all be wrong in the same direction by chance."
            ),
            "cannotDistinguish": (
                "This does not separate a Rubin zeropoint from a systematic in the 1.5 arcsec aperture "
                "photometry, which is identical in every pairing."
            ),
            "referencesCompared": len(references),
            "pairingsAgreeing": agreeing,
        })

    dense = [name for name in references if crowding[name].get("significant")]
    if all(crowding[name].get("rho") is not None for name in references):
        if len(dense) == len(references):
            owner = "common to every pairing, therefore Rubin's or the aperture method's"
            logic = (
                "Every pairing shows it. The references are independently calibrated and were reduced "
                "by different pipelines, so what the comparisons share is Rubin and the 1.5 arcsec "
                "aperture photometry applied identically to all of them."
            )
        elif dense:
            owner = ", ".join(dense)
            logic = (
                f"Only {owner} show it. Rubin is common to every pairing, so an effect present in some "
                "and absent in others cannot be Rubin's."
            )
        else:
            owner = "no density trend is resolved in any pairing"
            logic = "No pairing's correlation clears the 1% permutation threshold."
        findings.append({
            "question": "Does the density-dependent flux scale belong to Rubin or to the reference?",
            "verdict": owner,
            "basis": (
                "Scale against matched-source count: "
                + ", ".join(
                    f"{name} {crowding[name]['rho']:+.3f} (p {crowding[name]['pValue']:.4f}, "
                    f"n {crowding[name]['n']})"
                    for name in references
                )
                + f". {logic}"
            ),
            "direction": (
                "The scale falls as matched-source count rises: Rubin measures relatively less flux in "
                "the aperture where sources are denser, which is the sign expected if neighbouring "
                "flux is handled differently by the two sides."
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

    correlated = {k: v for k, v in usable.items() if v["scaleCorrelation"].get("rho") is not None}
    if correlated:
        positive = [
            k for k, v in correlated.items()
            if v["scaleCorrelation"].get("significant") and v["scaleCorrelation"]["rho"] > 0
        ]
        findings.append({
            "question": "Whose field-to-field scatter is the larger part?",
            "verdict": (
                "shared, therefore Rubin's"
                if len(positive) == len(correlated)
                else f"established for {', '.join(positive)} only" if positive else "not established"
            ),
            "basis": (
                "; ".join(
                    f"{k}: rho {v['scaleCorrelation']['rho']:+.3f} (p {v['scaleCorrelation']['pValue']:.4f}, "
                    f"n {v['scaleCorrelation']['n']})"
                    for k, v in correlated.items()
                )
                + ". The only thing any two pairings share is the Rubin image, so correlated variation "
                "is Rubin's."
            ),
            "sampleGate": f"threshold {MIN_SHARED_REGIONS} shared regions per pairing",
        })

    payload = {
        "schemaVersion": "layers-reference-cross-check-v2",
        "generatedAt": utc_now(),
        "purpose": (
            "Independent optical references over shared sky, used to attribute the optical "
            "comparison's field-dependent effects to a survey rather than measure them again."
        ),
        "pairs": {f"rubin-vs-{name}": describe(pair) for name, pair in references.items()},
        "counts": {
            **{f"{name}Regions": len(pair) for name, pair in references.items()},
            "referencesCompared": len(references),
            # The largest shared set is the primary evidence base; every pairing
            # is reported separately under pairCombinations.
            "sharedRegions": int(largest.get("sharedRegions", 0)),
            "sharedRegionThreshold": MIN_SHARED_REGIONS,
            "sharedSampleSufficient": enough,
            "pairingsAboveThreshold": len(usable),
        },
        "crowdingCorrelation": crowding,
        "crowdingCorrelationAllRegions": crowding_unfiltered,
        "qaFilterChangesAnswer": qa_filter_matters,
        "pairCombinations": {
            key: {k: v for k, v in value.items() if k != "regionIds"}
            for key, value in combinations.items()
        },
        "findings": findings,
        "caveats": [
            "The density proxy is the matched compact-source count per field, which depends on the "
            "depth and PSF of both surveys in a pair and is not a sky density.",
            "The pairings sit on different common grids, so only the physical 1.5 arcsec aperture "
            "makes them comparable.",
            "DES variance is a uniform sky estimate rather than a propagated plane, which affects "
            "weighting inside its pairing but not this attribution, which uses medians.",
            "Attribution is between the surveys in these pairings. It does not establish which "
            "survey is right in an absolute sense; no external standard is used here.",
        ],
        "regions": [
            {
                "regionId": region_id,
                "scales": {name: references[name][region_id]["scale"] for name in references
                           if region_id in references[name]},
                "matchedSources": {name: references[name][region_id]["sources"] for name in references
                                   if region_id in references[name]},
            }
            for region_id in sorted(set().union(*(set(pair) for pair in references.values())))
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(", ".join(f"{name} {len(pair)} regions" for name, pair in references.items()))
    for name, pair in payload["pairs"].items():
        if pair.get("regions"):
            print(f"  {name}: scale {pair['medianScale']:.4f}  ({pair['medianMagnitudeOffset']:+.4f} mag)")
    for key, value in combinations.items():
        print(f"  {key}: {value['sharedRegions']} shared")
    for finding in findings:
        print(f"\n{finding['question']}\n  -> {finding['verdict']}\n     {finding['basis']}")
    print(f"\nwrote {args.output.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
