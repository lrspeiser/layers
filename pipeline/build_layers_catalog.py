#!/usr/bin/env python3
"""Build the public, survey-neutral Layers target and layer index.

The catalog intentionally contains metadata and provenance only.  Restricted
Rubin pixels remain in the local layer store until a publication policy and
comparison QA explicitly allow an image product to be published.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def external_image_records(paths: list[Path]) -> dict[str, list[dict]]:
    records: dict[str, list[dict]] = {}
    for path in paths:
        if not path.is_file():
            continue
        manifest = load_json(path)
        if manifest.get("schemaVersion") != 1 or manifest.get("adapterContract") != "layers-image-layer-v1":
            raise RuntimeError(f"Unsupported external image-layer manifest: {path}")
        for item in manifest.get("targets", []):
            records.setdefault(item["targetId"], []).append(item["layer"])
    return records


def external_catalog_records(paths: list[Path]) -> dict[str, dict[str, list[dict]]]:
    records: dict[str, dict[str, list[dict]]] = {}
    for path in paths:
        if not path.is_file():
            continue
        manifest = load_json(path)
        if manifest.get("schemaVersion") != 1 or manifest.get("adapterContract") != "layers-catalog-layer-v1":
            raise RuntimeError(f"Unsupported external catalog-layer manifest: {path}")
        for item in manifest.get("targets", []):
            target = records.setdefault(item["targetId"], {"layers": [], "comparisons": []})
            target["layers"].append(item["layer"])
            target["comparisons"].append(item["comparison"])
    return records


def synchronize_external_catalog_records(root: Path, targets: list[dict]) -> None:
    """Keep public source records aligned after release-wide audit ranking."""
    for target in targets:
        for layer in target["layers"]:
            if layer.get("kind") != "catalog" or not layer.get("assets", {}).get("data"):
                continue
            public_path = root / "public" / layer["assets"]["data"].lstrip("/")
            if not public_path.is_file():
                continue
            record = load_json(public_path)
            if record.get("product") != "Layers external catalog-layer record":
                continue
            comparison = next(
                (
                    item for item in target["comparisons"]
                    if layer["id"] in item.get("layerIds", []) and item.get("comparisonMode") == "catalog-profile"
                ),
                None,
            )
            if not comparison:
                continue
            record["layer"] = layer
            record["comparison"] = comparison
            public_path.write_text(json.dumps(record, indent=2), encoding="utf-8")


def registration_comparison(path: Path, layer_ids: set[str]) -> dict | None:
    if not path.is_file():
        return None
    audit = load_json(path)
    comparison_key = audit.get("comparisonKey", path.parent.name)
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
    extended_path = path.parent / "extended-source-filter-audit.json"
    extended_audit = load_json(extended_path) if extended_path.is_file() else None
    three_survey_path = path.parent / "three-survey-consistency.json"
    three_survey_audit = load_json(three_survey_path) if three_survey_path.is_file() else None
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
    assumption_audits = []
    if extended_audit and extended_audit.get("status") == "qa-failed":
        thresholds = extended_audit.get("thresholds", {})
        median_residual = extended_audit.get("medianAbsoluteResidualMag")
        scatter = extended_audit.get("robustResidualScatterMag")
        median_limit = thresholds.get("maximumMedianAbsoluteResidualMag")
        scatter_limit = thresholds.get("maximumRobustResidualScatterMag")
        support_fraction = extended_audit.get("colorSupportFraction")
        qualified = extended_audit.get("qualifiedCells")
        supported = extended_audit.get("cellsWithinStellarColorSupport")
        minimum_cells = thresholds.get("minimumResolvedCells", 20)
        if all(value is not None for value in (median_residual, scatter, median_limit, scatter_limit)) and qualified >= minimum_cells:
            priority_score = max(median_residual / median_limit, scatter / scatter_limit)
            assumption_audits.append(
                {
                    "id": f"{audit['objectId']}-stellar-to-resolved-filter-transfer",
                    "title": "A stellar color transform also calibrates resolved galaxy light.",
                    "priorAssumption": "A color relation validated on isolated field stars can be transferred to resolved galaxy cells after WCS, PSF, sky, units, and masks are reconciled.",
                    "newEvidence": (
                        f"Point sources pass at {reconciliation_filter.get('heldOutRmsMag', 0):.3f} mag held-out RMS, "
                        f"but {qualified} resolved cells yield a {median_residual:.3f} mag median absolute residual "
                        f"and {scatter:.3f} mag robust scatter. Only {supported} cells "
                        f"({support_fraction * 100:.0f}%) lie inside the stellar training-color range."
                    ),
                    "affectedInference": "No Rubin-minus-reference outer-light, stellar-mass, baryonic-mass, lensing, or baryonic-acceleration inference is supported for this target until the transfer is resolved.",
                    "confidence": "candidate",
                    "priorityScore": priority_score,
                    "evidenceMagnitude": {
                        "metric": "resolved median absolute filter-transfer residual",
                        "value": median_residual,
                        "unit": "mag",
                        "passThreshold": median_limit,
                        "thresholdMultiple": median_residual / median_limit,
                        "qualifiedCells": qualified,
                        "cellsWithinTrainingSupport": supported,
                    },
                    "systematicAlternatives": [
                        "Galaxy color gradients, dust, emission lines, or composite stellar populations make a stellar relation inappropriate.",
                        "Residual sky structure, PSF wings, resampling covariance, masks, or contaminating sources bias the resolved cells.",
                        "Survey passband or calibration differences require full synthetic photometry rather than an empirical linear color term.",
                    ],
                    "recommendedFollowUp": [
                        "Run synthetic photometry through the Rubin and reference throughput curves over galaxy SED templates.",
                        "Fit resolved multi-band SEDs and repeat the transfer test with spatial covariance and PSF-wing models.",
                        "Compare an independent deeper image layer and inspect the residual map before publishing any astrophysical difference.",
                    ],
                    "provenance": [
                        extended_audit.get("supportingProducts", {}).get("matchedPairSha256", ""),
                        reconciliation_filter.get("extendedSourceAuditSha256", ""),
                    ],
                    "caveat": "This ranking identifies a calibration assumption worth rechecking; it is not evidence that either survey or the galaxy is wrong.",
                    **({
                        "independentCheck": {
                            "survey": "Pan-STARRS1 DR1",
                            "status": three_survey_audit["status"],
                            "gate": "filter-calibration",
                            "registrationP95Arcsec": three_survey_audit.get("registrationsToRubin", {}).get("panstarrs", {}).get("residualP95Arcsec"),
                            "passThresholdArcsec": three_survey_audit.get("astrometryThresholdArcsec"),
                            "registrationPass": three_survey_audit.get("status") == "diagnostic",
                            "qualifiedForArbitration": False,
                            "note": "Gaia epoch-corrected registration passes, but the independent layer still uses only scalar stellar normalization; it cannot arbitrate resolved galaxy light until color-dependent filter transfer is validated.",
                            "provenance": [sha256(three_survey_path)],
                        }
                    } if three_survey_audit else {}),
                }
            )
    return {
        "id": f"{comparison_key}-registration-audit",
        "comparisonKey": comparison_key,
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
                "qaPackage": f"/data/comparisons/{comparison_key}.json",
            },
        } if matched_product else {}),
        "measurements": measurements,
        "inferences": [],
        "assumptionAudits": assumption_audits,
    }


def pilot_audit(target: dict, mosaic: dict | None, comparison: dict | None, audit_dir: Path) -> dict | None:
    if int(target.get("deep_coadd_rows", 0)) == 0:
        return None
    target_id = target["slug"]
    if mosaic and not mosaic.get("science_coverage"):
        products = [
            {
                "path": f"pipeline/output/dp2-sparc/{target_id}/{Path(product['mosaic']).name}",
                "sha256": product["mosaic_sha256"],
            }
            for product in mosaic.get("bands", {}).values()
            if product.get("mosaic") and product.get("mosaic_sha256")
        ]
        return {
            "id": f"{target_id}-pixel-coverage-audit",
            "outcome": "no-valid-pixels",
            "stage": "pixel-coverage",
            "observation": "Two authenticated Rubin DP2 deep-coadd records intersect the requested field, but the calibrated mosaic contains zero valid science pixels; every intersecting pixel is masked NO_DATA.",
            "metric": {"label": "valid calibrated-pixel fraction", "value": 0.0, "unit": "fraction", "passThreshold": 0.0, "comparison": "must be greater than"},
            "claimStatus": "blocked",
            "evidence": products,
            "nextAction": "Re-query a future Rubin release or expanded coadd footprint; do not count this metadata footprint match as usable coverage.",
        }
    if not comparison:
        return None
    registration_path = audit_dir / target_id / "registration-audit.json"
    if comparison.get("qa", {}).get("astrometryPass") is False:
        return {
            "id": f"{target_id}-registration-limit",
            "outcome": "registration-blocked",
            "stage": "registration",
            "observation": "Rubin DP2 and Pan-STARRS1 have authentic local pixels, but their source-based astrometric residual exceeds the predeclared registration tolerance.",
            "metric": {"label": "source-registration p95 residual", "value": comparison["qa"]["astrometricResidualP95Arcsec"], "unit": "arcsec", "passThreshold": comparison["registration"]["qaThresholdArcsec"], "comparison": "must be less than or equal to"},
            "claimStatus": "blocked",
            "evidence": [{"path": f"pipeline/output/comparisons/{target_id}/registration-audit.json", "sha256": sha256(registration_path)}],
            "nextAction": "Diagnose spatially varying astrometry or adopt an independently validated reference layer; do not force-warp the pixels into compliance.",
        }
    if comparison.get("qa", {}).get("extendedSourceTransferStatus") == "qa-failed":
        extended_path = audit_dir / target_id / "extended-source-filter-audit.json"
        extended_audit = load_json(extended_path) if extended_path.is_file() else {}
        qualified_cells = extended_audit.get("qualifiedCells", 0)
        required_cells = extended_audit.get("thresholds", {}).get("minimumResolvedCells", 20)
        residual = comparison["qa"]["extendedSourceMedianAbsoluteResidualMag"]
        residual_limit = extended_audit.get("thresholds", {}).get("maximumMedianAbsoluteResidualMag", 0.08)
        sample_pass = qualified_cells >= required_cells
        return {
            "id": f"{target_id}-filter-transfer-limit",
            "outcome": "filter-transfer-blocked",
            "stage": "filter-response",
            "observation": (
                "Astrometry, PSF/sky reconciliation, point-source color calibration, and diffuse recovery pass, but the resolved-galaxy transfer does not. "
                f"Only {qualified_cells}/{required_cells} required cells survive the common mask, and their {residual:.3f} mag median residual exceeds the {residual_limit:.2f} mag tolerance."
            ),
            "metric": (
                {"label": "resolved median absolute filter-transfer residual", "value": residual, "unit": "mag", "passThreshold": residual_limit, "comparison": "must be less than or equal to"}
                if sample_pass
                else {"label": "qualified resolved galaxy cells", "value": qualified_cells, "unit": "cells", "passThreshold": required_cells, "comparison": "must be greater than or equal to"}
            ),
            "claimStatus": "blocked",
            "evidence": [{"path": f"pipeline/output/comparisons/{target_id}/extended-source-filter-audit.json", "sha256": sha256(extended_path)}],
            "nextAction": "Add a less fragmented independent image layer or model the Pan-STARRS masks and spatial covariance, then run full synthetic photometry and resolved multi-band SED checks before any missing-light or mass inference.",
        }
    if comparison.get("qa", {}).get("extendedSourceTransferStatus") == "blocked":
        filter_path = audit_dir / target_id / "filter-response-audit.json"
        filter_audit = load_json(filter_path) if filter_path.is_file() else {}
        retained_stars = filter_audit.get("sample", {}).get("retainedCalibrationStars", 0)
        required_stars = filter_audit.get("thresholds", {}).get("minimumCalibrationStars", 50)
        held_out_rms = filter_audit.get("crossValidation", {}).get("rmsMag") if filter_audit.get("crossValidation") else None
        color_span = filter_audit.get("sample", {}).get("colorSpanMag")
        return {
            "id": f"{target_id}-filter-adapter-limit",
            "outcome": "filter-adapter-blocked",
            "stage": "filter-response",
            "observation": (
                "Epoch-aware Gaia registration, PSF/sky reconciliation, and diffuse recovery pass. "
                f"The independent Pan-STARRS DR2 catalog relation spans {color_span:.2f} mag and reaches {held_out_rms:.3f} mag held-out RMS, "
                "but robust outlier rejection leaves fewer stars than the predeclared publication minimum."
                if held_out_rms is not None and color_span is not None
                else "The field lacks enough retained stars to validate the Pan-STARRS-to-Rubin i-band color relation."
            ),
            "metric": {"label": "retained color-calibration stars", "value": retained_stars, "unit": "stars", "passThreshold": required_stars, "comparison": "must be greater than or equal to"},
            "claimStatus": "blocked",
            "evidence": [{"path": f"pipeline/output/comparisons/{target_id}/filter-response-audit.json", "sha256": sha256(filter_path)}],
            "nextAction": "Acquire a wider Rubin calibration field around UGC00891, rerun the unchanged 50-star gate, then test whether the stellar relation transfers to resolved galaxy light.",
        }
    return None


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=root / "pipeline" / "results" / "dp2-sparc-coverage.json")
    parser.add_argument("--mosaics", type=Path, default=root / "pipeline" / "output" / "dp2-sparc" / "mosaic-summary.json")
    parser.add_argument("--downloads", type=Path, default=root / "pipeline" / "output" / "dp2-sparc" / "download-manifest.json")
    parser.add_argument("--legacy", type=Path, default=root / "pipeline" / "output" / "legacy-survey" / "manifest.json")
    parser.add_argument("--panstarrs", type=Path, default=root / "pipeline" / "output" / "panstarrs" / "manifest.json")
    parser.add_argument(
        "--external-image-manifest",
        action="append",
        type=Path,
        default=[root / "pipeline" / "output" / "wise-allwise" / "manifest.json"],
    )
    parser.add_argument(
        "--external-catalog-manifest",
        action="append",
        type=Path,
        default=[root / "pipeline" / "output" / "wise-stellar-masses" / "manifest.json"],
    )
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
    external_records = external_image_records(args.external_image_manifest)
    external_catalogs = external_catalog_records(args.external_catalog_manifest)
    targets = []
    for source in coverage["targets"]:
        mosaic = mosaics.get(source["slug"])
        layers = [sparc_layer(source, coverage["sparc_bibcode"], sparc_profiles.get(source["slug"])), rubin_layer(source, mosaic, selected_ids.get(source["slug"], []))]
        if source["slug"] in legacy_records:
            layers.append(legacy_survey_layer(legacy_records[source["slug"]]))
        if source["slug"] in panstarrs_records:
            layers.append(panstarrs_layer(panstarrs_records[source["slug"]]))
        layers.extend(external_records.get(source["slug"], []))
        layers.extend(external_catalogs.get(source["slug"], {}).get("layers", []))
        comparison_paths = sorted(args.registration_audits.glob("*/registration-audit.json"))
        comparisons = [
            comparison
            for path in comparison_paths
            if load_json(path).get("objectId") == source["slug"]
            for comparison in [registration_comparison(path, {layer["id"] for layer in layers})]
            if comparison
        ]
        comparisons.extend(external_catalogs.get(source["slug"], {}).get("comparisons", []))
        comparison = next((item for item in comparisons if item.get("comparisonKey") == source["slug"]), comparisons[0] if comparisons else None)
        target_record = {
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
                "comparisons": comparisons,
            }
        audit = pilot_audit(source, mosaic, comparison, args.registration_audits)
        if audit:
            target_record["pilotAudit"] = audit
        targets.append(target_record)

    ranked_audits = sorted(
        (
            audit
            for target in targets
            for comparison in target["comparisons"]
            for audit in comparison["assumptionAudits"]
        ),
        key=lambda item: item["priorityScore"],
        reverse=True,
    )
    for rank, audit in enumerate(ranked_audits, start=1):
        audit["rank"] = rank
    synchronize_external_catalog_records(root, targets)

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
    external_images = sum(len(items) for items in external_records.values())
    external_catalog_layers = sum(len(item["layers"]) for item in external_catalogs.values())
    allwise_published = sum(
        1 for items in external_records.values() for layer in items if layer.get("id") == "wise-allwise-atlas"
    )
    registration_audits = sum(len(target["comparisons"]) for target in targets)
    pilot_audits = sum("pilotAudit" in target for target in targets)
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
            "externalImageLayers": external_images,
            "externalCatalogLayers": external_catalog_layers,
            "allWisePublished": allwise_published,
            "localImageLayers": usable + legacy_local + panstarrs_local + external_images,
            "registrationAudits": registration_audits,
            "pilotAudits": pilot_audits,
            "assumptionsWorthRechecking": len(ranked_audits),
            "publishedComparisons": sum(
                comparison["status"] == "published"
                for target in targets
                for comparison in target["comparisons"]
            ),
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
