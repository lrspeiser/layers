#!/usr/bin/env python3
"""Build a generic external catalog layer from published WISE stellar masses.

The adapter compares Duey et al. (2025) W1 stellar masses with the explicit
SPARC/Lelli et al. (2016) reference assumption M/L[3.6] = 0.5.  It does not
derive masses from the display-only AllWISE cutouts.  Every target record
retains the two published inputs, the model assumption, uncertainty terms,
and source-table checksums.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_OFFSET_DEX = 0.10
EXPECTED_SCATTER_DEX = 0.18
WISE_TABLE_URL = "https://cdsarc.cds.unistra.fr/ftp/J/AJ/169/186/table1.dat"
WISE_README_URL = "https://cdsarc.cds.unistra.fr/viz-bin/ReadMe/J/AJ/169/186?format=html&tex=true"
WISE_PAPER_URL = "https://arxiv.org/abs/2501.10919"
SPARC_PAPER_URL = "https://arxiv.org/abs/1606.09251"
SPARC_ARCHIVE_URL = "https://astroweb.cwru.edu/SPARC/"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_name(value: str) -> str:
    value = re.sub(r"^LSBC\s*", "", value.strip().upper())
    return re.sub(r"[^A-Z0-9]", "", value)


def parse_wise(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(
            {
                "name": line[0:12].strip(),
                "logLuminosityW1Lsun": float(line[13:19]),
                "logStellarMassW1Msun": float(line[20:26]),
                "logStellarMassUncertaintyDex": float(line[27:32]),
                "gMinusW1Mag": float(line[33:37]),
                "massToLightW1": float(line[38:42]),
                "sourceRow": line,
            }
        )
    return rows


def parse_sparc(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 10:
            continue
        try:
            luminosity = float(fields[7])
            luminosity_uncertainty = float(fields[8])
        except ValueError:
            continue
        rows.append(
            {
                "name": fields[0],
                "luminosity36BillionLsun": luminosity,
                "luminosity36UncertaintyBillionLsun": luminosity_uncertainty,
                "sourceRow": line,
            }
        )
    return rows


def classify(significance: float) -> str:
    return "large" if significance >= 3 else "noteworthy" if significance >= 2 else "expected"


def rounded(value: float) -> float:
    return round(value, 6)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--wise", type=Path, default=root / "pipeline/cache/wise-stellar-masses-table1.dat")
    parser.add_argument("--sparc", type=Path, default=root / "pipeline/cache/sparc/SPARC_Lelli2016c.mrt")
    parser.add_argument("--coverage", type=Path, default=root / "pipeline/results/dp2-sparc-coverage.json")
    parser.add_argument("--output", type=Path, default=root / "pipeline/output/wise-stellar-masses/manifest.json")
    parser.add_argument("--public-output", type=Path, default=root / "public/data/layers/wise-stellar-mass")
    args = parser.parse_args()

    for path in (args.wise, args.sparc, args.coverage):
        if not path.is_file():
            raise SystemExit(f"Required published source is missing: {path}")

    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    target_by_name = {canonical_name(item["sparc_id"]): item for item in coverage["targets"]}
    sparc_by_name = {canonical_name(item["name"]): item for item in parse_sparc(args.sparc)}
    wise_rows = parse_wise(args.wise)
    if len(wise_rows) != 111:
        raise SystemExit(f"Expected 111 published WISE rows, found {len(wise_rows)}")

    created_at = datetime.now(timezone.utc).isoformat()
    wise_sha = sha256(args.wise)
    sparc_sha = sha256(args.sparc)
    targets = []
    unmatched = []
    args.public_output.mkdir(parents=True, exist_ok=True)
    for wise in wise_rows:
        key = canonical_name(wise["name"])
        sparc = sparc_by_name.get(key)
        target = target_by_name.get(key)
        if not sparc or not target:
            unmatched.append(wise["name"])
            continue

        baseline_log_mass = math.log10(sparc["luminosity36BillionLsun"] * 1e9 * 0.5)
        sparc_stat = sparc["luminosity36UncertaintyBillionLsun"] / sparc["luminosity36BillionLsun"] / math.log(10)
        statistical = math.sqrt(wise["logStellarMassUncertaintyDex"] ** 2 + sparc_stat ** 2)
        delta = wise["logStellarMassW1Msun"] - baseline_log_mass
        total = math.sqrt(statistical ** 2 + EXPECTED_SCATTER_DEX ** 2)
        significance = abs(delta - EXPECTED_OFFSET_DEX) / total
        category = classify(significance)
        target_id = target["slug"]
        comparison_key = f"{target_id}--wise-stellar-mass"
        data_path = args.public_output / f"{target_id}.json"
        public_data_path = f"/data/layers/wise-stellar-mass/{target_id}.json"
        provenance = [WISE_TABLE_URL, WISE_PAPER_URL, SPARC_PAPER_URL, f"sha256:{wise_sha}", f"sha256:{sparc_sha}"]
        measurement = {
            "id": f"{target_id}-wise-minus-sparc-stellar-mass",
            "label": "WISE W1 − SPARC fixed-M/L stellar mass",
            "quantity": "log stellar mass difference",
            "value": rounded(delta),
            "unit": "dex",
            "statisticalUncertainty": rounded(statistical),
            "systematicUncertainty": EXPECTED_SCATTER_DEX,
            "expectedRange": [rounded(EXPECTED_OFFSET_DEX - 2 * total), rounded(EXPECTED_OFFSET_DEX + 2 * total)],
            "expectedCenter": EXPECTED_OFFSET_DEX,
            "significanceSigma": rounded(significance),
            "classification": category,
            "provenance": provenance,
            "caveats": [
                "The SPARC reference is a model baseline: total 3.6 µm luminosity multiplied by the explicitly declared fixed M/L = 0.5 Msun/Lsun.",
                "The WISE value uses the published g−W1 color-dependent stellar-population model; this is not a mass measured from the Layers display cutout.",
                "The 0.18 dex systematic is the published W1-versus-Spitzer stellar-mass scatter; the paper reports an expected approximately +0.1 dex W1 M/L shift.",
                "Foreground-star contamination, aperture choices, star-formation history, and bulge/disk decomposition can move the WISE estimate.",
                "Classification is triage relative to the cross-survey expectation, not evidence that either catalog is wrong.",
            ],
        }
        audits = []
        if category != "expected":
            audits.append(
                {
                    "id": f"{target_id}-stellar-mass-baseline-audit",
                    "rank": 0,
                    "title": f"Recheck the fixed 3.6 µm stellar-mass baseline for {target['sparc_id']}",
                    "priorAssumption": "A fixed M/L[3.6] = 0.5 provides an adequate total stellar-mass baseline for this galaxy.",
                    "newEvidence": f"The published WISE color-model mass differs by {delta:+.3f} dex, a {significance:.2f}σ deviation from the expected +0.10 dex W1−3.6 offset.",
                    "affectedInference": "Total stellar mass, stellar contribution to baryonic mass, and any downstream acceleration model that adopts this global normalization.",
                    "confidence": "candidate",
                    "priorityScore": rounded(significance),
                    "evidenceMagnitude": {
                        "metric": "deviation from expected W1−3.6 stellar-mass offset",
                        "value": rounded(abs(delta - EXPECTED_OFFSET_DEX)),
                        "unit": "dex",
                        "passThreshold": rounded(2 * total),
                        "thresholdMultiple": rounded(significance / 2),
                        "qualifiedCells": 1,
                        "cellsWithinTrainingSupport": 1,
                    },
                    "systematicAlternatives": [
                        "Foreground-star contamination can bias WISE total flux high.",
                        "A single global 3.6 µm M/L does not model color, star-formation history, or bulge/disk populations.",
                        "Different total-flux apertures and sky estimates can shift either luminosity.",
                    ],
                    "recommendedFollowUp": [
                        "Inspect the WISE field and masks for contaminating foreground stars.",
                        "Repeat matched-aperture W1 and 3.6 µm photometry with independent sky estimates.",
                        "Propagate a resolved or bulge/disk M/L model before changing a baryonic rotation-curve inference.",
                    ],
                    "provenance": provenance,
                    "caveat": "This is a catalog-level model comparison and a follow-up candidate, not a pixel-level missing-light detection or a revised baryonic mass.",
                }
            )

        layer = {
            "id": "wise-w1-stellar-mass-2025",
            "survey": "WISE + SDSS",
            "release": "Duey et al. 2025",
            "instrument": "WISE W1 + SDSS g photometry",
            "kind": "catalog",
            "availability": "published",
            "renderMode": "table",
            "bands": ["W1", "g"],
            "datasetCount": 1,
            "units": {"stellarMass": "log10(Msun)", "massToLight": "Msun/Lsun", "color": "mag"},
            "calibration": "Published g−W1 color-dependent stellar-population mass model",
            "hasVariance": True,
            "hasMask": False,
            "hasWcs": False,
            "note": "Published target-level stellar-mass catalog. Link it to SPARC as a table/plot; never render it as an image.",
            "provenance": {
                "catalog": WISE_TABLE_URL,
                "readme": WISE_README_URL,
                "paper": WISE_PAPER_URL,
                "sourceRow": wise["name"],
                "sourceSha256": wise_sha,
            },
            "assets": {"data": public_data_path},
            "catalogSummary": {
                "logStellarMassMsun": wise["logStellarMassW1Msun"],
                "uncertaintyDex": wise["logStellarMassUncertaintyDex"],
                "gMinusW1Mag": wise["gMinusW1Mag"],
                "massToLight": wise["massToLightW1"],
            },
        }
        comparison = {
            "id": comparison_key,
            "comparisonKey": comparison_key,
            "comparisonMode": "catalog-profile",
            "layerIds": ["sparc-2016", "wise-w1-stellar-mass-2025"],
            "status": "published",
            "compatibility": {
                "targetIdentityMatched": True,
                "quantityMatched": True,
                "unitsMatched": True,
                "distanceScaleShared": True,
                "modelDeclared": True,
                "limitations": [
                    "Global catalog masses only; no radial stellar-mass profile is inferred.",
                    "SPARC fixed-M/L and WISE color-dependent M/L are deliberately different models.",
                ],
            },
            "catalogValues": {
                "wiseLogStellarMassMsun": wise["logStellarMassW1Msun"],
                "wiseStatisticalUncertaintyDex": wise["logStellarMassUncertaintyDex"],
                "wiseMassToLight": wise["massToLightW1"],
                "wiseGMinusW1Mag": wise["gMinusW1Mag"],
                "sparcLuminosity36BillionLsun": sparc["luminosity36BillionLsun"],
                "sparcLuminosity36UncertaintyBillionLsun": sparc["luminosity36UncertaintyBillionLsun"],
                "sparcFixedMassToLight": 0.5,
                "sparcBaselineLogStellarMassMsun": rounded(baseline_log_mass),
            },
            "products": {"qaPackage": f"/data/comparisons/{comparison_key}.json"},
            "measurements": [measurement],
            "inferences": [
                {
                    "id": f"{target_id}-stellar-mass-normalization",
                    "domain": "baryonic-mass",
                    "observation": f"Published WISE and SPARC inputs give a WISE−SPARC fixed-M/L stellar-mass offset of {delta:+.3f} dex.",
                    "modelDependentInterpretation": "If photometric systematics are excluded, the offset quantifies sensitivity of the global stellar-mass normalization to the adopted near-IR M/L model. It does not by itself revise the radial baryonic acceleration.",
                    "confidence": "candidate" if category != "expected" else "supported",
                    "assumptions": [
                        "Both catalogs use the same target distance scale.",
                        "SPARC baseline M/L[3.6] is fixed at 0.5 Msun/Lsun.",
                        "Published WISE g−W1 mass calibration and uncertainty apply to this target.",
                    ],
                }
            ],
            "assumptionAudits": audits,
        }
        public_record = {
            "schemaVersion": 1,
            "product": "Layers external catalog-layer record",
            "generatedAt": created_at,
            "targetId": target_id,
            "targetName": target["sparc_id"],
            "publishedInputs": {"wise": wise, "sparc": sparc},
            "declaredModel": {"sparcMassToLight36": 0.5, "expectedOffsetDex": EXPECTED_OFFSET_DEX, "expectedScatterDex": EXPECTED_SCATTER_DEX},
            "layer": layer,
            "comparison": comparison,
        }
        data_path.write_text(json.dumps(public_record, indent=2), encoding="utf-8")
        targets.append({"targetId": target_id, "layer": layer, "comparison": comparison, "record": public_data_path})

    if unmatched or len(targets) != 111:
        raise SystemExit(f"Catalog identity reconciliation failed: {len(targets)} matched, unmatched={unmatched}")

    pilot_ids = {"ngc0100", "ugc00191", "ugc00634", "ugc00891"}
    pilot_matches = sorted(item["targetId"] for item in targets if item["targetId"] in pilot_ids)
    categories = {name: sum(item["comparison"]["measurements"][0]["classification"] == name for item in targets) for name in ("expected", "noteworthy", "large")}
    manifest = {
        "schemaVersion": 1,
        "adapterContract": "layers-catalog-layer-v1",
        "createdAt": created_at,
        "layerId": "wise-w1-stellar-mass-2025",
        "sources": {
            "wiseCatalog": {"url": WISE_TABLE_URL, "sha256": wise_sha, "records": len(wise_rows)},
            "sparcCatalog": {"url": SPARC_ARCHIVE_URL, "sha256": sparc_sha, "records": len(sparc_by_name)},
            "wisePaper": WISE_PAPER_URL,
            "sparcPaper": SPARC_PAPER_URL,
        },
        "scienceContract": {
            "referenceModel": "log10(L[3.6] * 1e9 * 0.5 Msun/Lsun)",
            "difference": "log10(Mstar_WISE/Msun) - log10(Mstar_SPARC_fixed_ML/Msun)",
            "expectedOffsetDex": EXPECTED_OFFSET_DEX,
            "expectedScatterDex": EXPECTED_SCATTER_DEX,
            "classification": "expected <2 sigma; noteworthy 2-3 sigma; large >=3 sigma",
            "observationVsInference": "Catalog values and their difference are observations/model outputs; baryonic implications remain model-dependent.",
        },
        "cohort": {"matched": len(targets), "classifications": categories, "rubinPilotMeasurements": pilot_matches},
        "targets": targets,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (args.public_output / "summary.json").write_text(json.dumps({key: value for key, value in manifest.items() if key != "targets"}, indent=2), encoding="utf-8")
    print(f"Built {len(targets)} WISE–SPARC stellar-mass comparisons: {categories}; Rubin pilots with measurements={pilot_matches}")


if __name__ == "__main__":
    main()
