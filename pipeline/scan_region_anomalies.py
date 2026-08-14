#!/usr/bin/env python3
"""Scan reconciled regions for residuals that a known effect does not explain.

An anomaly is not "a big number in a difference image". It is an observation
that departs from a stated expectation by more than the measured uncertainty,
after everything that could produce it for a boring reason has been accounted
for. This scanner is built around that definition:

**Expectation.** After PSF matching, background matching, and flux-unit
transfer, the Rubin and reference planes should agree within the noise. The
matched-pair difference should be consistent with zero.

**Uncertainty.** Scored against the empirical blank-position scatter measured by
``validate_region_recovery.py``, never against the per-pixel variance planes.
Those understate the true uncertainty by a median factor of about 7 on these
products, so a significance computed from them would be inflated by roughly that
factor and every candidate would look real.

**Estimator identity.** Candidate amplitudes are measured with the same
``fit_template_amplitude`` call, at the same template sizes, that produced the
null distribution. A matched filter with different normalisation would not be
comparable to the calibrated threshold, so the scan grid-searches with the
identical estimator rather than convolving.

**Explanations before discoveries.** Every candidate carries the boring
explanations it survives or fails: proximity to a mask edge, proximity to a
bright source whose PSF wings are not modelled by the Gaussian match, and
consistency with the field's unreconciled bandpass colour term. Nothing here is
a detection; the output is a ranked list of places to look, with the reason each
one is interesting and the test that would kill it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import binary_dilation, distance_transform_edt, gaussian_filter, label

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_layer_registration import robust_sigma
from validate_diffuse_recovery import exponential_template, fit_template_amplitude
from validate_region_recovery import mask_sources_in_variance

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECONCILED = ROOT / "pipeline/results/reconciled-regions/manifest.json"
DEFAULT_RECOVERY = ROOT / "pipeline/results/region-recovery"
DEFAULT_BANDPASS = ROOT / "pipeline/results/bandpass-transfer/manifest.json"
DEFAULT_OUTPUT = ROOT / "pipeline/results/region-anomalies"
DEFAULT_PUBLIC = ROOT / "public/data/layers/selected-regions/region-anomalies.json"

# Report anything past this empirical significance. Deliberately lower than a
# discovery threshold: the point is to rank places to look, and the explanation
# fields decide what survives.
CANDIDATE_SIGMA = 4.0
GRID_STRIDE_FRACTION = 0.5
MAX_CANDIDATES_PER_REGION = 40

# A residual within this many pixels of an invalid pixel is edge-affected; the
# normalised convolution renormalises near mask boundaries and leaves structure.
EDGE_PIXELS = 6
# Gaussian PSF matching does not reproduce PSF wings, so a residual sitting on a
# bright source is the expected failure mode rather than new light.
BRIGHT_SOURCE_PERCENTILE = 99.5
BRIGHT_SOURCE_RADIUS_PIXELS = 10


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def bright_source_mask(image: np.ndarray, valid: np.ndarray) -> np.ndarray:
    finite = image[valid & np.isfinite(image)]
    if not finite.size:
        return np.zeros(image.shape, dtype=bool)
    cutoff = float(np.percentile(finite, BRIGHT_SOURCE_PERCENTILE))
    return binary_dilation(
        np.isfinite(image) & (image > cutoff), iterations=BRIGHT_SOURCE_RADIUS_PIXELS
    )


def scan_scale(
    difference: np.ndarray,
    variance: np.ndarray,
    valid: np.ndarray,
    template: np.ndarray,
    null_sigma: float,
    null_median: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Grid-search the identical estimator used to build the null distribution."""
    radius = template.shape[0] // 2
    stride = max(2, int(radius * GRID_STRIDE_FRACTION))
    height, width = difference.shape
    amplitude = np.full(difference.shape, np.nan, dtype=np.float64)
    covered = np.zeros(difference.shape, dtype=bool)
    for y in range(radius + 2, height - radius - 2, stride):
        for x in range(radius + 2, width - radius - 2, stride):
            patch = valid[y - radius : y + radius + 1, x - radius : x + radius + 1]
            if patch.mean() < 0.95:
                continue
            try:
                value, _ = fit_template_amplitude(difference, variance, valid, template, x, y)
            except np.linalg.LinAlgError:
                continue
            if not np.isfinite(value):
                continue
            amplitude[y, x] = value
            covered[y, x] = True
    significance = np.full(difference.shape, np.nan, dtype=np.float64)
    if null_sigma > 0:
        significance[covered] = (amplitude[covered] - null_median) / null_sigma
    return amplitude, significance


def scan_region(
    record: dict[str, Any],
    recovery: dict[str, Any],
    bandpass: dict[str, Any] | None,
    output: Path,
) -> dict[str, Any]:
    matched_path = ROOT / record["products"]["matchedPair"]
    with fits.open(matched_path, memmap=False, checksum=True) as hdus:
        rubin = np.asarray(hdus["RUBIN"].data, dtype=np.float64)
        reference = np.asarray(hdus["REFERENCE"].data, dtype=np.float64)
        rubin_variance = np.asarray(hdus["RUBIN_VARIANCE"].data, dtype=np.float64)
        reference_variance = np.asarray(hdus["REFERENCE_VARIANCE"].data, dtype=np.float64)
        common = np.asarray(hdus["COMMON_MASK"].data, dtype=np.uint8) > 0
        wcs = WCS(hdus["RUBIN"].header).celestial
    pixel_scale = float(record["pixelScaleArcsec"])
    psf_fwhm = float(record["psf"]["targetFwhmArcsec"])

    # Put the two planes on a common compact-source scale so a residual cannot be
    # a zero-point artefact. Only fields whose flux transfer is corroborated are
    # scanned, so this scale is measured, not assumed.
    scale = record["units"]["empiricalPointSourceScale"]["scale"]
    difference = rubin - scale * reference
    variance = rubin_variance + (scale**2) * reference_variance
    valid = common & np.isfinite(difference) & np.isfinite(variance) & (variance > 0)

    # Estimator identity. The null distribution was calibrated with detected
    # sources excluded through the variance plane, so the scan must exclude them
    # the same way or the two are not comparable. This makes the scan a search
    # for diffuse residual light *between* sources, which is the missing-light
    # question. Residuals sitting on a source need their own calibration and are
    # reported separately as the PSF-wing class rather than folded in here.
    variance, source_mask = mask_sources_in_variance(difference, variance, valid)

    edge_distance = distance_transform_edt(valid)
    bright = bright_source_mask(rubin, valid) | bright_source_mask(reference, valid)

    sizes = {
        item["effectiveRadiusArcsec"]: item
        for item in recovery["layers"]["rubin"]["sizes"]
        if item.get("status") == "measured" and item.get("nullRobustSigmaNjy")
    }
    if not sizes:
        raise ValueError("no calibrated noise scale available for this region")

    candidates: list[dict[str, Any]] = []
    for effective_radius, calibration in sorted(sizes.items()):
        template, enclosed = exponential_template(effective_radius, pixel_scale, psf_fwhm)
        # The null distribution was measured on the individual layers; the
        # difference of two independent planes has a larger scatter, so combine
        # them rather than reusing a single-layer sigma.
        rubin_sigma = calibration["nullRobustSigmaNjy"]
        reference_entry = next(
            (
                item
                for item in recovery["layers"]["reference"]["sizes"]
                if item.get("effectiveRadiusArcsec") == effective_radius and item.get("nullRobustSigmaNjy")
            ),
            None,
        )
        reference_sigma = reference_entry["nullRobustSigmaNjy"] if reference_entry else rubin_sigma
        null_sigma = math.sqrt(rubin_sigma**2 + (scale * reference_sigma) ** 2)

        amplitude, significance = scan_scale(difference, variance, valid, template, null_sigma, 0.0)
        detected = np.isfinite(significance) & (np.abs(significance) >= CANDIDATE_SIGMA)
        if not detected.any():
            continue
        structures, count = label(binary_dilation(detected, iterations=2))
        for index in range(1, count + 1):
            pixels = np.argwhere(structures == index)
            local = [(abs(significance[y, x]), y, x) for y, x in pixels if np.isfinite(significance[y, x])]
            if not local:
                continue
            peak_sigma, y, x = max(local)
            signed = float(significance[y, x])
            sky = wcs.pixel_to_world_values(float(x), float(y))
            explanations = []
            if edge_distance[y, x] <= EDGE_PIXELS:
                explanations.append("within %d px of a mask edge" % EDGE_PIXELS)
            if bright[y, x]:
                explanations.append("on a bright source where Gaussian PSF matching leaves wing residuals")
            candidates.append({
                "effectiveRadiusArcsec": effective_radius,
                "pixel": {"x": int(x), "y": int(y)},
                "sky": {"raDeg": float(sky[0]), "decDeg": float(sky[1])},
                "amplitudeNjy": float(amplitude[y, x]),
                "empiricalSigma": abs(signed),
                "direction": "rubin-excess" if signed > 0 else "reference-excess",
                "templateEnclosedFluxFraction": enclosed,
                "nullSigmaNjy": null_sigma,
                "edgeDistancePixels": float(edge_distance[y, x]),
                "couldBeExplainedBy": explanations,
            })

    # Scale coherence. Real extended structure has a size, so it registers at more
    # than one template scale at the same sky position. A residual that appears at
    # exactly one scale is far more likely to be a PSF or noise artefact. This is
    # the cheapest discriminant that separates "structure" from "spike".
    coherence_radius = max(4.0, 6.0 / pixel_scale)
    for candidate in candidates:
        others = [
            other
            for other in candidates
            if other is not candidate
            and other["effectiveRadiusArcsec"] != candidate["effectiveRadiusArcsec"]
            and math.hypot(
                other["pixel"]["x"] - candidate["pixel"]["x"],
                other["pixel"]["y"] - candidate["pixel"]["y"],
            )
            <= coherence_radius
            and other["direction"] == candidate["direction"]
        ]
        candidate["scalesDetected"] = 1 + len({item["effectiveRadiusArcsec"] for item in others})
        if candidate["scalesDetected"] == 1:
            candidate["couldBeExplainedBy"].append(
                "appears at only one template scale, so it has no measured size"
            )

    candidates.sort(key=lambda item: item["empiricalSigma"], reverse=True)
    truncated = len(candidates) > MAX_CANDIDATES_PER_REGION
    kept = candidates[:MAX_CANDIDATES_PER_REGION]

    # A field whose bandpass colour term is large can produce residuals purely
    # from unreconciled throughput, so the field-level term is attached to every
    # candidate rather than being silently ignored.
    colour_context = None
    if bandpass:
        colour_context = {
            "colourTerm": bandpass["fit"]["colourTerm"],
            "colourTermUncertainty": bandpass["fit"]["colourTermUncertainty"],
            "compactSourceResidualMag": bandpass["residual"]["afterTransferMag"],
            "note": (
                "Bandpass transfer is not reconciled. A source whose colour differs from the field "
                "average will show a residual of roughly this size for that reason alone."
            ),
        }

    unexplained = [item for item in kept if not item["couldBeExplainedBy"]]
    payload = {
        "schemaVersion": "layers-region-anomalies-v1",
        "regionId": record["regionId"],
        "tract": record["tract"],
        "generatedAt": utc_now(),
        "rubinBand": record["rubinBand"],
        "referenceBand": record["referenceBand"],
        "referenceSurveyId": record["referenceSurveyId"],
        "expectation": (
            "After PSF, background, and flux-unit matching the two planes should agree within the "
            "empirically measured noise, so the matched-pair difference should be consistent with zero."
        ),
        "uncertaintyModel": {
            "source": "injection/recovery blank-position scatter",
            "perPixelVarianceUsed": False,
            "note": (
                "Per-pixel variance planes understate the true uncertainty by a median factor of about "
                "7 on these products, so significance is computed against the measured blank-position "
                "scatter at each template scale instead."
            ),
        },
        "appliedCompactSourceScale": scale,
        "sourceMask": source_mask,
        "candidateSigmaThreshold": CANDIDATE_SIGMA,
        "bandpassContext": colour_context,
        "counts": {
            "candidates": len(candidates),
            "reported": len(kept),
            "truncated": truncated,
            "withoutBoringExplanation": len(unexplained),
        },
        "candidates": kept,
        "interpretation": (
            "These are ranked places to look, not detections. A candidate with no listed explanation "
            "has survived the mask-edge and PSF-wing checks only; it has not survived the unreconciled "
            "bandpass, and it has not been confirmed in an independent survey or epoch."
        ),
        "falsificationTests": [
            "Re-measure in the second Rubin band: a real structure persists, a filter artefact moves with colour.",
            "Re-measure against a third survey: a real structure appears against DES or HSC as well as Legacy.",
            "Inject a synthetic source of the same amplitude at the same position and confirm it is recovered.",
            "Re-run after the extended-source bandpass transfer is validated.",
        ],
    }
    region_dir = output / record["regionId"]
    region_dir.mkdir(parents=True, exist_ok=True)
    (region_dir / "anomalies.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
    parser.add_argument("--recovery", type=Path, default=DEFAULT_RECOVERY)
    parser.add_argument("--bandpass", type=Path, default=DEFAULT_BANDPASS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--only-region", action="append", default=[])
    parser.add_argument(
        "--require-corroborated-flux",
        action="store_true",
        default=True,
        help="Only scan fields whose flux transfer is corroborated by the point-source ratio.",
    )
    args = parser.parse_args()

    reconciled = json.loads(args.reconciled.read_text(encoding="utf-8"))
    bandpass_by_region: dict[str, Any] = {}
    if args.bandpass.is_file():
        bandpass_by_region = {
            item["regionId"]: item
            for item in json.loads(args.bandpass.read_text(encoding="utf-8"))["regions"]
        }

    only = {value.strip() for value in args.only_region if value.strip()}
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for record in reconciled["regions"]:
        region_id = record["regionId"]
        if only and region_id not in only:
            continue
        if args.require_corroborated_flux and not record["units"]["empiricalPointSourceScale"].get(
            "corroboratesDocumentedChains"
        ):
            skipped.append({"regionId": region_id, "reason": "flux transfer not corroborated"})
            continue
        if not record["psf"]["matched"]:
            skipped.append({"regionId": region_id, "reason": "PSF matching failed"})
            continue
        recovery_path = args.recovery / region_id / "diffuse-recovery.json"
        if not recovery_path.is_file():
            skipped.append({"regionId": region_id, "reason": "no injection/recovery calibration"})
            continue
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        try:
            result = scan_region(record, recovery, bandpass_by_region.get(region_id), args.output)
        except Exception as error:  # noqa: BLE001 - report, never silently drop
            skipped.append({"regionId": region_id, "reason": f"{type(error).__name__}: {error}"})
            print(f"[failed] {region_id}: {type(error).__name__}: {error}", flush=True)
            continue
        records.append(result)
        print(
            f"[scanned] {region_id} candidates={result['counts']['candidates']} "
            f"unexplained={result['counts']['withoutBoringExplanation']}",
            flush=True,
        )

    # Field-level clustering. A field that yields far more surviving candidates
    # than its peers is almost certainly carrying a field-level systematic rather
    # than several independent discoveries, so its candidates are demoted rather
    # than allowed to dominate the ranking.
    surviving = {
        item["regionId"]: sum(1 for c in item["candidates"] if not c["couldBeExplainedBy"])
        for item in records
    }
    counts = np.array(sorted(surviving.values()), dtype=float)
    if counts.size >= 5:
        outlier_cut = float(np.median(counts) + 3.0 * max(robust_sigma(counts), 1.0))
    else:
        outlier_cut = float("inf")
    crowded = {region for region, n in surviving.items() if n > outlier_cut}
    for item in records:
        if item["regionId"] not in crowded:
            continue
        for candidate in item["candidates"]:
            if not candidate["couldBeExplainedBy"]:
                candidate["couldBeExplainedBy"].append(
                    "field yields %d surviving candidates against a %.0f cutoff, indicating a "
                    "field-level systematic rather than independent detections"
                    % (surviving[item["regionId"]], outlier_cut)
                )
        item["counts"]["withoutBoringExplanation"] = 0
        item["counts"]["demotedAsCrowdedField"] = True

    ranked = sorted(
        (
            {**candidate, "regionId": item["regionId"], "tract": item["tract"],
             "referenceSurveyId": item["referenceSurveyId"]}
            for item in records
            for candidate in item["candidates"]
            if not candidate["couldBeExplainedBy"]
        ),
        key=lambda item: item["empiricalSigma"],
        reverse=True,
    )
    summary = {
        "schemaVersion": "layers-region-anomalies-summary-v1",
        "generatedAt": utc_now(),
        "candidateSigmaThreshold": CANDIDATE_SIGMA,
        "counts": {
            "regionsScanned": len(records),
            "regionsSkipped": len(skipped),
            "totalCandidates": sum(item["counts"]["candidates"] for item in records),
            "withoutBoringExplanation": len(ranked),
        },
        "policy": {
            "theseAreNotDetections": True,
            "bandpassStillUnreconciled": True,
            "note": (
                "Every candidate is a place to look. The bandpass transfer is not validated, so a "
                "colour difference alone can produce a residual of the reported size. No candidate "
                "may be published as a difference until it survives an independent survey or epoch."
            ),
        },
        "crowdedFieldsDemoted": sorted(crowded),
        "skipped": skipped,
        "topCandidates": ranked[:100],
        "regions": [{k: v for k, v in item.items() if k != "candidates"} for item in records],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"\nscanned {len(records)} regions, {len(skipped)} skipped, "
        f"{summary['counts']['totalCandidates']} candidates, "
        f"{len(ranked)} with no boring explanation",
        flush=True,
    )


if __name__ == "__main__":
    main()
