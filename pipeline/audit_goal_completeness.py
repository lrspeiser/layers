#!/usr/bin/env python3
"""Build an evidence-backed audit of the complete Layers product objective."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
OUT = PUBLIC / "data" / "coverage" / "goal-audit.json"


def load(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def optional(relative: str):
    path = ROOT / relative
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def is_pixel_product(product: dict) -> bool:
    """Return true only for spatially resolved pixel products.

    Catalogs, spectra, and light curves remain useful tract evidence, but they
    cannot satisfy the goal's real-pixel demonstration gate by themselves.
    """
    product_type = str(product.get("productType", "")).lower()
    non_pixel_tokens = ("catalog", "spectrum", "light curve", "timeseries", "time series")
    pixel_tokens = ("image", "map", "cutout", "reconstruction", "moment", "cube", "coadd", "plane")
    return not any(token in product_type for token in non_pixel_tokens) and any(
        token in product_type for token in pixel_tokens
    )


def main() -> None:
    registry = load("public/data/survey-registry.json")["surveys"]
    footprint = load("public/data/coverage/rubin-dp2-footprint.json")
    overlaps = load("public/data/coverage/external-overlaps.json")
    selected = load("public/data/coverage/selected-regions.json")
    rubin_pixels = load("public/data/coverage/rubin-pixels-50.json")
    optical = load("public/data/layers/selected-regions/rubin-reference-comparisons.json")
    pilots = load("public/data/layers/multisurvey-pilots/rubin-lotss-common-grid-summary.json")
    conservative = load("public/data/coverage/conservative-subset-evidence.json")
    large = load("public/data/coverage/large-footprint-resolution.json")
    hsc_kids = load("public/data/coverage/hsc-kids-gap-audit.json")
    hsc_public = load("public/data/coverage/hsc-public-products.json")
    desi = load("public/data/coverage/desi-dr1-resolution.json")
    required_ids = {survey["id"] for survey in registry}

    coverage = {}
    for summary in overlaps["surveySummaries"]:
        exact = summary["coverageStatus"] in {"resolved-moc", "confirmed-all-sky"}
        coverage[summary["surveyId"]] = {
            "status": "resolved-full" if exact else summary["coverageStatus"],
            "semantics": "full-release-footprint" if exact else "unresolved",
            "tractCount": summary["confirmedTractCount"],
            "mapVisible": exact,
            "source": "external-overlaps",
        }
    for product in large["resolved"]:
        full = bool(product["eligibleAsFullRegistryFootprint"])
        coverage[product["surveyId"]] = {
            "status": product["status"],
            "semantics": "full-release-product" if full else "named-product-subset",
            "tractCount": product["confirmedRubinTractCount"],
            "mapVisible": True,
            "source": "large-footprint-resolution",
        }
    for product in conservative["surveyEvidence"]:
        coverage[product["surveyId"]] = {
            "status": product["status"],
            "semantics": "full-release-product" if product["eligibleAsFullRegistryFootprint"] else product["coverageSemantics"],
            "tractCount": product["confirmedRubinTractCount"],
            "mapVisible": True,
            "source": "conservative-subset-evidence",
        }
    for item in large["unresolved"]:
        if item["surveyId"] not in required_ids:
            continue
        if item["surveyId"] not in coverage or coverage[item["surveyId"]]["status"] in {"unresolved", "approximate"}:
            coverage[item["surveyId"]] = {
                "status": item["status"],
                "semantics": "unresolved",
                "tractCount": 0,
                "mapVisible": False,
                "source": "large-footprint-resolution",
                "blocker": item["blocker"],
                "nextAction": item["nextAction"],
            }
    for product in hsc_kids["products"]:
        if product["surveyId"] == "kids-1000-lensing" and product["status"] == "resolved-exact-catalogue-positional-support":
            coverage[product["surveyId"]] = {
                "status": product["status"],
                "semantics": product["coverageSemantics"],
                "tractCount": product["releasedGoldSupport"]["rubinOverlapTractCount"],
                "mapVisible": True,
                "source": "hsc-kids-gap-audit",
            }
        elif product["surveyId"] == "hsc-ssp-pdr3":
            if product["surveyId"] not in required_ids:
                continue
            coverage[product["surveyId"]] = {
                "status": product["status"],
                "semantics": "unresolved",
                "tractCount": 0,
                "mapVisible": False,
                "source": "hsc-kids-gap-audit",
                "blocker": product["blockers"][0],
                "nextAction": "Provide HSC archive credentials and run the documented released-mosaic query.",
            }
    for product in hsc_public["products"]:
        coverage[product["surveyId"]] = {
            "status": product["status"],
            "semantics": product["coverageSemantics"],
            "tractCount": product["confirmedRubinTractCount"],
            "mapVisible": True,
            "source": "hsc-public-products",
        }
    coverage[desi["surveyId"]] = {
        "status": desi["status"],
        "semantics": desi["coverageSemantics"],
        "tractCount": desi["confirmedRubinTractCount"],
        "mapVisible": True,
        "source": "desi-dr1-resolution",
    }

    products = []
    for row in optical["regions"]:
        products.append({
            "regionId": row["regionId"], "tract": row["tract"], "surveyId": row["referenceSurveyId"],
            "family": "optical", "productType": "image", "displayReady": row["displayAlignmentAllowed"],
            "scienceReady": row["inputs"]["reference"]["scienceReady"], "comparisonReady": row["comparisonReady"],
            "source": "rubin-reference-comparisons",
        })
    pilot_tracts = {"ugc00191": 11162, "ugc00634": 10689, "ugc00891": 11411}
    for field in pilots["fields"]:
        if field["status"] == "available":
            products.append({
                "regionId": f"dp2-tract-{pilot_tracts[field['fieldId']]}", "tract": pilot_tracts[field["fieldId"]],
                "surveyId": "lotss-dr3", "family": "radio", "productType": "image", "displayReady": True,
                "scienceReady": True, "comparisonReady": False, "source": "rubin-lotss-common-grid-summary",
            })

    optional_manifests = [
        "public/data/layers/uv-ir-time/manifest.json",
        "public/data/layers/radio-xray-hi/manifest.json",
        "public/data/layers/lensing-cmb/manifest.json",
    ]
    optional_status = []
    for relative in optional_manifests:
        manifest = optional(relative)
        optional_status.append({"path": relative, "present": manifest is not None})
        if manifest:
            for product in manifest.get("products", []):
                normalized = dict(product)
                normalized["source"] = relative
                products.append(normalized)

    aliases = {
        "optical-baseline": "optical", "uv/ir": "uv-ir", "high-energy": "x-ray",
        "neutral-gas": "gas", "time": "time-domain", "cmb": "lensing",
        "microwave": "lensing", "cmb-large-scale-structure": "lensing",
    }
    selected_tracts = {row["tract"] for row in selected["regions"]}
    family_tracts = defaultdict(set)
    display_tracts = set()
    for product in products:
        if (
            not product.get("displayReady")
            or not is_pixel_product(product)
            or product.get("tract") not in selected_tracts
        ):
            continue
        family = aliases.get(product.get("family", ""), product.get("family", ""))
        family_tracts[family].add(product["tract"])
        display_tracts.add(product["tract"])

    required_families = ["optical", "uv-ir", "radio", "x-ray", "gas", "time-domain", "lensing"]
    family_counts = {family: len(family_tracts[family]) for family in required_families}
    unresolved = [
        survey["id"] for survey in registry
        if coverage.get(survey["id"], {}).get("semantics") == "unresolved"
    ]
    not_visible = [
        survey["id"] for survey in registry
        if coverage.get(survey["id"], {}).get("tractCount", 0) > 0 and not coverage.get(survey["id"], {}).get("mapVisible")
    ]
    gates = {
        "entireRubinFootprintIndexed": footprint["counts"]["tracts"] == 2191 and len(footprint["tracts"]) == 2191,
        "allNamedDatasetsRegistered": len(registry) == 28,
        "allCoverageResolvedExactly": not unresolved,
        "allResolvedOverlapsVisibleOnMap": not not_visible,
        "selectedRegionCountAtLeast50": len(selected_tracts) >= 50,
        "rubinPixelRegionsAtLeast50": rubin_pixels["summary"]["scienceReadyRegionCount"] >= 50,
        "displayPixelRegionsAtLeast50": len(display_tracts) >= 50,
        "everyRequiredPixelFamilyDemonstrated": all(family_counts[family] > 0 for family in required_families),
        "scienceClaimsRemainGated": all(not product.get("comparisonReady", False) for product in products),
    }
    achieved = all(gates.values())
    output = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "objectiveAchieved": achieved,
        "gates": gates,
        "counts": {
            "rubinTracts": footprint["counts"]["tracts"],
            "registeredDatasets": len(registry),
            "selectedRegions": len(selected_tracts),
            "rubinScienceReadyRegions": rubin_pixels["summary"]["scienceReadyRegionCount"],
            "displayReadySelectedRegions": len(display_tracts),
            "displayProducts": sum(bool(product.get("displayReady")) for product in products),
            "comparisonReadyProducts": sum(bool(product.get("comparisonReady")) for product in products),
        },
        "pixelFamilyRegionCounts": family_counts,
        "coverageBySurvey": {survey["id"]: coverage.get(survey["id"], {"status": "missing", "semantics": "unresolved", "tractCount": 0, "mapVisible": False}) for survey in registry},
        "unresolvedCoverageSurveyIds": unresolved,
        "resolvedButNotMapVisibleSurveyIds": not_visible,
        "optionalProductManifests": optional_status,
        "nextActions": [
            *(["Acquire real pixels for: " + ", ".join(family for family, count in family_counts.items() if count == 0)] if any(count == 0 for count in family_counts.values()) else []),
            *(["Resolve release-matched exact coverage for: " + ", ".join(unresolved)] if unresolved else []),
        ],
        "interpretation": "Display-ready means real pixels share a tract and, where an overlay exists, a celestial display grid. It does not imply PSF-, bandpass-, noise-, or selection-matched quantitative comparability.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"objectiveAchieved": achieved, "gates": gates, "pixelFamilyRegionCounts": family_counts, "unresolved": unresolved}, indent=2))


if __name__ == "__main__":
    main()
