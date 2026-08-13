#!/usr/bin/env python3
"""Build the public, survey-neutral Layers target and layer index.

The catalog intentionally contains metadata and provenance only.  Restricted
Rubin pixels remain in the local layer store until a publication policy and
comparison QA explicitly allow an image product to be published.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def mosaic_state(summary_path: Path) -> dict[str, dict]:
    if not summary_path.is_file():
        return {}
    return {item["target"]["slug"]: item for item in load_json(summary_path)}


def rubin_layer(target: dict, mosaic: dict | None, selected_dataset_ids: list[str]) -> dict:
    rows = int(target["deep_coadd_rows"])
    if rows == 0:
        availability = "not-covered"
        note = "Authenticated DP2 SIA query returned no deep-coadd datasets."
    elif mosaic is None:
        availability = "metadata-match"
        note = "SIA footprint match exists; calibrated pixel coverage has not been validated."
    elif mosaic.get("science_coverage"):
        availability = "available-local"
        note = "Calibrated image, variance, and mask mosaics exist in the local layer store; pixels are not public."
    else:
        availability = "no-valid-pixels"
        note = mosaic.get("coverage_note") or "Footprint metadata matched, but no usable science pixels intersect the field."

    bands = []
    band_coverage = {}
    if mosaic:
        bands = [name for name, product in mosaic.get("bands", {}).items() if product.get("science_coverage")]
        band_coverage = {name: product["valid_pixel_fraction"] for name, product in mosaic.get("bands", {}).items() if product.get("science_coverage")}
    return {
        "id": "rubin-dp2-deep-coadd",
        "survey": "Vera C. Rubin Observatory",
        "release": "DP2",
        "instrument": "LSSTCam",
        "kind": "image",
        "availability": availability,
        "renderMode": "image" if availability == "published" else "metadata",
        "bands": bands,
        **({"bandCoverage": band_coverage} if band_coverage else {}),
        "datasetCount": len(selected_dataset_ids),
        "datasetIds": selected_dataset_ids,
        "units": {"image": "nJy", "variance": "nJy^2"},
        "calibration": "Rubin Science Pipelines deep coadd",
        "hasVariance": availability == "available-local",
        "hasMask": availability == "available-local",
        "hasWcs": rows > 0,
        "note": note,
        "provenance": {
            "service": "Rubin Science Platform SIA v2 + DataLink",
            "datasetType": "lsst.deep_coadd",
            "queryStatus": "OK",
        },
    }


def sparc_layer(target: dict, bibcode: str, profile: dict | None) -> dict:
    return {
        "id": "sparc-2016",
        "survey": "SPARC",
        "release": "2016 master sample",
        "instrument": "Spitzer photometry + published rotation curves",
        "kind": "profile",
        "availability": "available",
        "renderMode": "plot",
        "bands": ["3.6um"],
        "units": {"surfaceBrightness": "mag arcsec^-2", "velocity": "km s^-1"},
        "calibration": "SPARC published tables",
        "hasVariance": False,
        "hasMask": False,
        "hasWcs": False,
        "note": "A radial photometry and rotation-curve layer; it must be plotted or overlaid, never treated as a sky image.",
        "provenance": {"bibcode": bibcode, "sampleId": target["sparc_id"]},
        **({"assets": {"data": profile["data"]}} if profile else {}),
        **({"profileSummary": profile["summary"]} if profile else {}),
    }


def legacy_survey_layer(record: dict) -> dict:
    usable_bands = [name for name, product in record.get("bands", {}).items() if product.get("science_coverage")]
    return {
        "id": "legacy-survey-dr10",
        "survey": "DESI Legacy Imaging Surveys",
        "release": "DR10",
        "instrument": "DECam + BASS/MzLS",
        "kind": "image",
        "availability": "available-local" if usable_bands else "no-valid-pixels",
        "renderMode": "metadata",
        "bands": usable_bands,
        "bandCoverage": {name: product["valid_pixel_fraction"] for name, product in record.get("bands", {}).items() if product.get("science_coverage")},
        "datasetCount": len(record.get("tiles", [])),
        "datasetIds": [tile["url"] for tile in record.get("tiles", [])],
        "units": {"image": "nanomaggy", "inverseVariance": "nanomaggy^-2"},
        "calibration": "Legacy Survey DR10 coadded calibrated flux",
        "hasVariance": bool(usable_bands),
        "hasMask": bool(usable_bands),
        "hasWcs": True,
        "note": "Calibrated tiled FITS mosaics exist in the local layer store; publication and cross-survey comparison remain QA-gated.",
        "provenance": {
            "service": "Legacy Survey FITS cutout service",
            "layer": "ls-dr10",
            "documentation": "https://www.legacysurvey.org/dr10/description/",
        },
    }


def panstarrs_layer(record: dict) -> dict:
    usable_bands = [name for name, product in record.get("bands", {}).items() if product.get("science_coverage")]
    originals = [item for product in record.get("bands", {}).values() for item in product.get("originals", [])]
    return {
        "id": "panstarrs-dr1-stack",
        "survey": "Pan-STARRS1",
        "release": "DR1 3pi stacks",
        "instrument": "PS1 GPC1",
        "kind": "image",
        "availability": "available-local" if usable_bands else "no-valid-pixels",
        "renderMode": "metadata",
        "bands": usable_bands,
        "bandCoverage": {name: product["valid_pixel_fraction"] for name, product in record.get("bands", {}).items() if product.get("science_coverage")},
        "datasetCount": len(originals),
        "datasetIds": [item["url"] for item in originals],
        "units": {"image": "nJy", "variance": "nJy^2"},
        "calibration": "PS1 DR1 stack calibration, converted per skycell to AB nJy",
        "hasVariance": bool(usable_bands),
        "hasMask": bool(usable_bands),
        "hasWcs": True,
        "note": "Full science, variance, and mask skycells plus a calibrated local mosaic exist; comparison remains registration and QA gated.",
        "provenance": {
            "service": "MAST Pan-STARRS image-list service and full skycell archive",
            "product": "unconvolved stack + stack.wt + stack.mask",
            "documentation": "https://outerspace.stsci.edu/spaces/PANSTARRS/pages/298812251/PS1+Image+Cutout+Service",
        },
    }


def registration_comparison(path: Path, layer_ids: set[str]) -> dict | None:
    if not path.is_file():
        return None
    audit = load_json(path)
    if audit.get("status") != "qa" or len(audit.get("layerIds", [])) != 2:
        return None
    if not set(audit["layerIds"]).issubset(layer_ids):
        return None
    source_registration = audit.get("sourceRegistration", {})
    residual = source_registration.get("residualP95Arcsec")
    threshold = audit.get("astrometryThresholdArcsec")
    reconciliation_path = path.parent / "reconciliation.json"
    reconciliation = load_json(reconciliation_path) if reconciliation_path.is_file() else None
    reconciliation_registration = reconciliation.get("registration", {}) if reconciliation else {}
    reconciliation_psf = reconciliation.get("psf", {}) if reconciliation else {}
    reconciliation_sky = reconciliation.get("sky", {}) if reconciliation else {}
    reconciliation_filter = reconciliation.get("filterResponse", {}) if reconciliation else {}
    recovery = reconciliation.get("injectionRecovery", {}) if reconciliation else {}
    recovery_path = path.parent / "diffuse-recovery.json"
    recovery_audit = load_json(recovery_path) if recovery_path.is_file() else None
    matched_product = reconciliation.get("products", {}) if reconciliation else {}
    effective_status = reconciliation.get("status") if reconciliation else "audit-only"
    measurements = []
    if recovery_audit and recovery_audit.get("status") == "pass":
        for layer_id in audit["layerIds"]:
            layer_recovery = recovery_audit.get("layers", {}).get(layer_id, {})
            for size in layer_recovery.get("sizes", []):
                limit = size.get("faintest90PercentCompleteMu0MagArcsec2")
                if limit is None:
                    continue
                trials = size.get("trials", [])
                trial = next(
                    (
                        item
                        for item in trials
                        if item.get("centralSurfaceBrightnessMagArcsec2") == limit
                    ),
                    None,
                )
                if not trial:
                    continue
                interval = trial.get("completeFractionWilson68", [None, None])
                measurements.append(
                    {
                        "id": f"{layer_id}-diffuse-limit-re-{size['effectiveRadiusArcsec']:g}",
                        "label": f"90% diffuse recovery limit ({size['effectiveRadiusArcsec']:g} arcsec Re)",
                        "quantity": "central surface brightness recovery limit",
                        "value": limit,
                        "unit": "AB mag arcsec^-2",
                        "statisticalUncertainty": 0.5,
                        "systematicUncertainty": 1.0,
                        "expectedRange": [limit - 1.0, limit + 1.0],
                        "significanceSigma": 0.0,
                        "classification": "expected",
                        "provenance": [
                            recovery_audit.get("sourceMatchedPairSha256", ""),
                            recovery.get("auditSha256", ""),
                        ],
                        "caveats": [
                            f"Discrete 1-mag injection grid; statistical grid resolution is ±0.5 mag.",
                            f"Smooth exponential profile with axis ratio {recovery_audit['model']['axisRatio']}; morphology systematic is represented as ±1 mag, not a calibrated universal error.",
                            f"Measured completeness {trial['completeFraction']:.3f}; 68% Wilson interval {interval[0]:.3f}–{interval[1]:.3f}.",
                            f"Empirical blank-position noise is {size['empiricalToFormalNoiseRatio']:.1f}× the formal template-fit uncertainty.",
                            "This is a sensitivity limit, not a cross-layer flux difference or discovery significance.",
                        ],
                    }
                )
    return {
        "id": f"{audit['objectId']}-registration-audit",
        "layerIds": audit["layerIds"],
        "status": "qa",
        "registration": {
            "layerIds": audit["layerIds"],
            "commonWcs": audit.get("commonWcs", False),
            "commonFootprint": audit.get("commonFootprint", False),
            "psfMatched": reconciliation_psf.get("matched", audit.get("psfMatched", False)),
            "skyMatched": reconciliation_sky.get("matched", audit.get("skyMatched", False)),
            "unitsMatched": audit.get("unitsMatched", False),
            "filterMatched": reconciliation_filter.get("matched", audit.get("filterMatched", False)),
            "filterTransform": audit.get("filterTransform"),
            "maxResidualArcsec": residual,
            "qaThresholdArcsec": threshold,
            "limitations": audit.get("limitations", []),
        },
        "qa": {
            "band": audit.get("band"),
            "comparisonLayerLabel": audit.get("comparisonLayerLabel"),
            "commonValidPixelFraction": audit.get("commonValidPixelFraction"),
            "matchedSources": source_registration.get("matchedSources"),
            "astrometricResidualP95Arcsec": residual,
            "astrometryPass": audit.get("astrometryPass", False),
            "rubinMedianFwhmArcsec": source_registration.get("rubinMedianFwhmArcsec"),
            "comparisonMedianFwhmArcsec": source_registration.get("comparisonMedianFwhmArcsec"),
            "reconciliationStatus": effective_status,
            "matchedCommonValidPixelFraction": reconciliation_registration.get("commonValidPixelFraction"),
            "postMatchAstrometricResidualP95Arcsec": reconciliation_registration.get("sourceRegistration", {}).get("residualP95Arcsec"),
            "postMatchFractionalFwhmDifference": reconciliation_psf.get("postMatchFractionalFwhmDifference"),
            "filterMatchBlocking": bool(reconciliation and not reconciliation.get("filterResponse", {}).get("matched", False)),
            "pointSourceCalibrationPass": reconciliation_filter.get("pointSourceCalibrationPass", False),
            "filterHeldOutRmsMag": reconciliation_filter.get("heldOutRmsMag"),
            "extendedSourceTransferPass": reconciliation_filter.get("extendedSourceTransferPass", False),
            "extendedSourceTransferStatus": (
                "pass" if reconciliation_filter.get("extendedSourceTransferPass") is True
                else "qa-failed" if reconciliation_filter.get("resolvedCellCount") is not None
                else "blocked"
            ),
            "extendedSourceResolvedCells": reconciliation_filter.get("resolvedCellCount"),
            "extendedSourceMedianAbsoluteResidualMag": reconciliation_filter.get("resolvedMedianAbsoluteResidualMag"),
            "extendedSourceRobustScatterMag": reconciliation_filter.get("resolvedRobustScatterMag"),
            "injectionRecoveryStatus": recovery.get("status"),
            "injectionNullTestPass": recovery.get("nullTestPass", False),
            "injectionRecoveryGridPass": recovery.get("recoveryGridPass", False),
            "injectionProfile": recovery.get("model", {}).get("profile"),
            "injectionEffectiveRadiiArcsec": recovery.get("model", {}).get("effectiveRadiiArcsec"),
        },
        **({
            "products": {
                "matchedPairSha256": matched_product.get("matchedPairSha256"),
                "sourceRubinSha256": matched_product.get("sourceRubinSha256"),
                "sourceComparisonSha256": matched_product.get("sourceComparisonSha256"),
                "qaPackage": f"/data/comparisons/{audit['objectId']}.json",
            },
        } if matched_product else {}),
        "measurements": measurements,
        "inferences": [],
        "assumptionAudits": [],
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline" / "results" / "dp2-sparc-coverage.json")
    parser.add_argument("--mosaics", type=Path, default=root / "pipeline" / "output" / "dp2-sparc" / "mosaic-summary.json")
    parser.add_argument("--downloads", type=Path, default=root / "pipeline" / "output" / "dp2-sparc" / "download-manifest.json")
    parser.add_argument("--legacy", type=Path, default=root / "pipeline" / "output" / "legacy-survey" / "manifest.json")
    parser.add_argument("--panstarrs", type=Path, default=root / "pipeline" / "output" / "panstarrs" / "manifest.json")
    parser.add_argument("--registration-audits", type=Path, default=root / "pipeline" / "output" / "comparisons")
    parser.add_argument("--sparc-profiles", type=Path, default=root / "public" / "data" / "sparc-profiles.json")
    parser.add_argument("--output", type=Path, default=root / "public" / "data" / "layers-catalog.json")
    args = parser.parse_args()

    coverage = load_json(args.coverage)
    sparc_profiles = load_json(args.sparc_profiles).get("targets", {}) if args.sparc_profiles.is_file() else {}
    mosaics = mosaic_state(args.mosaics)
    selected_ids: dict[str, list[str]] = {}
    if args.downloads.is_file():
        for record in load_json(args.downloads).get("records", []):
            selected_ids.setdefault(record["target_slug"], []).append(record["publisher_id"])
    legacy_records = {}
    if args.legacy.is_file():
        legacy_records = {item["target"]["slug"]: item for item in load_json(args.legacy).get("targets", [])}
    panstarrs_records = {}
    if args.panstarrs.is_file():
        panstarrs_records = {item["target"]["slug"]: item for item in load_json(args.panstarrs).get("targets", [])}
    targets = []
    for source in coverage["targets"]:
        mosaic = mosaics.get(source["slug"])
        layers = [sparc_layer(source, coverage["sparc_bibcode"], sparc_profiles.get(source["slug"])), rubin_layer(source, mosaic, selected_ids.get(source["slug"], []))]
        if source["slug"] in legacy_records:
            layers.append(legacy_survey_layer(legacy_records[source["slug"]]))
        if source["slug"] in panstarrs_records:
            layers.append(panstarrs_layer(panstarrs_records[source["slug"]]))
        comparison = registration_comparison(
            args.registration_audits / source["slug"] / "registration-audit.json",
            {layer["id"] for layer in layers},
        )
        targets.append(
            {
                "id": source["slug"],
                "name": source["sparc_id"],
                "identifiers": {"SPARC": source["sparc_id"], "SIMBAD": source["main_id"]},
                "center": {"raDeg": source["ra_deg"], "decDeg": source["dec_deg"], "frame": "ICRS"},
                "region": {"shape": "square", "widthArcmin": source["field_width_arcmin"]},
                "selection": {
                    "sample": "SPARC 2016 master sample",
                    "bibcode": coverage["sparc_bibcode"],
                    "majorAxisArcmin": source["major_axis_arcmin"],
                },
                "layers": layers,
                "comparisons": [comparison] if comparison else [],
            }
        )

    usable = sum(
        any(layer["id"] == "rubin-dp2-deep-coadd" and layer["availability"] == "available-local" for layer in target["layers"])
        for target in targets
    )
    footprint_only = sum(
        any(layer["id"] == "rubin-dp2-deep-coadd" and layer["availability"] == "no-valid-pixels" for layer in target["layers"])
        for target in targets
    )
    legacy_local = sum(any(layer["id"] == "legacy-survey-dr10" and layer["availability"] == "available-local" for layer in target["layers"]) for target in targets)
    panstarrs_local = sum(any(layer["id"] == "panstarrs-dr1-stack" and layer["availability"] == "available-local" for layer in target["layers"]) for target in targets)
    registration_audits = sum(len(target["comparisons"]) for target in targets)
    catalog = {
        "schemaVersion": 1,
        "product": "Layers",
        "release": "SPARC multi-survey pilot",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "targetSelection": {
            "name": "SPARC 2016 master sample",
            "count": len(targets),
            "complete": len(targets) == coverage["targets_total"],
        },
        "summary": {
            "targets": len(targets),
            "rubinSiaMatches": coverage["targets_with_deep_coadds"],
            "rubinUsableLocal": usable,
            "rubinFootprintFalsePositives": footprint_only,
            "legacySurveyUsableLocal": legacy_local,
            "panStarrsUsableLocal": panstarrs_local,
            "localImageLayers": usable + legacy_local + panstarrs_local,
            "registrationAudits": registration_audits,
            "publishedComparisons": 0,
        },
        "targets": targets,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print(
        f"Wrote {len(targets)} targets to {args.output} "
        f"({usable} usable local Rubin, {footprint_only} footprint-only)"
    )


if __name__ == "__main__":
    main()
