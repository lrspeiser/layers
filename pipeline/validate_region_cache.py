#!/usr/bin/env python3
"""Validate Layers acquisition plans, cached bytes, and science-readiness gates.

The validator is intentionally stricter than a successful HTTP response. A
FITS raster is a validated science input only when its checksum matches and
the required WCS, units, variance/weight, mask, and coverage planes exist.
Even then it is *not* comparison-ready: PSF/beam, passband, sky, resampling
covariance, and common-mask QA are properties of a particular comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from layer_connectors import LAYER_CONTRACTS, LAYER_KINDS, READINESS, SCHEMA_VERSION, cache_key


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_fits(path: Path) -> dict[str, Any]:
    try:
        from astropy.io import fits
        from astropy.wcs import WCS
    except ImportError:
        return {"readable": False, "inspectionError": "astropy-not-installed"}
    try:
        with fits.open(path, memmap=True, checksum=True) as hdus:
            headers = [hdu.header for hdu in hdus]
            extnames = {str(header.get("EXTNAME", "PRIMARY")).strip().upper() for header in headers}
            two_d = [hdu for hdu in hdus if hdu.data is not None and getattr(hdu.data, "ndim", 0) >= 2]
            wcs_present = False
            for header in headers:
                try:
                    if WCS(header).has_celestial:
                        wcs_present = True
                        break
                except Exception:
                    continue
            units = sorted({str(header.get("BUNIT", "")).strip() for header in headers if str(header.get("BUNIT", "")).strip()})
            variance = any(name in extnames for name in {"VARIANCE", "IVAR", "INVERSE_VARIANCE", "WEIGHT", "ERROR", "ERR", "UNCERTAINTY", "NOISE"})
            mask = any(name in extnames for name in {"MASK", "VALID_MASK", "DQ", "QUALITY"})
            coverage = any(name in extnames for name in {"COVERAGE", "EXPOSURE", "NEXP", "WEIGHT"})
            return {
                "readable": True,
                "hduCount": len(hdus),
                "imagePlaneCount": len(two_d),
                "extensionNames": sorted(extnames),
                "wcsPresent": wcs_present,
                "units": units,
                "unitsVerified": bool(units),
                "variancePresent": variance,
                "maskPresent": mask,
                "coveragePresent": coverage,
            }
    except Exception as error:
        return {"readable": False, "inspectionError": f"{type(error).__name__}: {error}"}


class Audit:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def validate_job(job: dict[str, Any], root: Path, audit: Audit) -> None:
    label = job.get("jobId", "<missing-job-id>")
    if job.get("schemaVersion") != SCHEMA_VERSION:
        audit.error(f"{label}: unsupported job schemaVersion")
    required = ("cacheKey", "cacheIdentity", "region", "surveyId", "release", "phase", "request", "quotaPolicy", "productContract", "provenance", "cache", "validation")
    for name in required:
        if name not in job:
            audit.error(f"{label}: missing {name}")
            return
    region = job["region"]
    try:
        expected_key, expected_identity = cache_key(
            survey_id=job["surveyId"], release=job["release"], ra_deg=float(region["ra_deg"]), dec_deg=float(region["dec_deg"]),
            size_arcmin=float(region["size_arcmin"]), band=job.get("band"), layer_kind=job["productContract"]["layer_kind"],
        )
    except (KeyError, TypeError, ValueError) as error:
        audit.error(f"{label}: invalid cache identity ({error})")
        return
    if job["cacheKey"] != expected_key or job["cacheIdentity"] != expected_identity:
        audit.error(f"{label}: cache key is not the deterministic canonical value")
    contract = job["productContract"]
    if contract.get("layer_kind") not in LAYER_KINDS:
        audit.error(f"{label}: invalid layer kind")
    if contract.get("readiness") not in READINESS:
        audit.error(f"{label}: invalid readiness")
    if contract.get("readiness") in {"display-only", "metadata-only", "science-input-candidate"} and contract.get("quantitative_use_allowed"):
        audit.error(f"{label}: nonvalidated/display product allows quantitative use")
    if contract.get("readiness") == "display-only" and job["validation"].get("scienceReady"):
        audit.error(f"{label}: display-only product labeled science-ready")
    if job["validation"].get("comparisonReady"):
        audit.error(f"{label}: cache validation cannot prove comparison readiness")
    quota = job["quotaPolicy"]
    configured = int(quota.get("configured_requests_per_minute", 0))
    account = quota.get("account_requests_per_minute")
    if configured <= 0:
        audit.error(f"{label}: invalid configured request rate")
    if account is not None and configured > int(account):
        audit.error(f"{label}: configured rate exceeds account quota")
    if int(quota.get("maximum_concurrency", 0)) <= 0:
        audit.error(f"{label}: invalid concurrency")
    if not job["provenance"].get("sourceUrl") or not job.get("provider"):
        audit.error(f"{label}: incomplete provenance")
    if job["request"].get("credential_env") and "Authorization" in json.dumps(job["request"]):
        audit.error(f"{label}: request serialized an authorization header")

    cache_record = job["cache"]
    path_value = cache_record.get("path")
    if not path_value:
        if job["status"] in {"cached", "fetched"}:
            audit.error(f"{label}: cached status without a cache path")
        return
    path = root / path_value
    if not path.is_file():
        audit.error(f"{label}: missing cached file {path_value}")
        return
    actual_bytes = path.stat().st_size
    actual_hash = sha256_file(path)
    if cache_record.get("bytes") != actual_bytes:
        audit.error(f"{label}: cached byte count mismatch")
    if cache_record.get("sha256") != actual_hash:
        audit.error(f"{label}: cached SHA-256 mismatch")
    if contract.get("layer_kind") == "raster" and (path.suffix.lower() in {".fits", ".fit", ".fts"} or "fits" in contract.get("media_type", "")):
        inspection = inspect_fits(path)
        job["validation"].update({key: value for key, value in inspection.items() if key in {
            "wcsPresent", "unitsVerified", "variancePresent", "maskPresent", "coveragePresent"
        }})
        job["validation"]["fitsInspection"] = inspection
        required_checks = [inspection.get("readable", False)]
        for required_name, observed_name in (
            ("wcs_required", "wcsPresent"), ("variance_required", "variancePresent"),
            ("mask_required", "maskPresent"), ("coverage_required", "coveragePresent"),
        ):
            if contract.get(required_name):
                required_checks.append(bool(inspection.get(observed_name)))
        if contract.get("units"):
            required_checks.append(bool(inspection.get("unitsVerified")))
        passed = all(required_checks)
        job["validation"]["scienceReady"] = passed
        job["validation"]["comparisonReady"] = False
        if passed:
            job["productContract"]["readiness"] = "validated-science-input"
            job["productContract"]["quantitative_use_allowed"] = True
        else:
            audit.warning(f"{label}: cached FITS is retained but failed one or more science-input support-plane gates")


def validate_public_summary(path: Path, evidence_paths: list[Path], audit: Audit) -> None:
    if not path.is_file():
        audit.error(f"Missing public cache summary: {path}")
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in ("http://", "https://", "Authorization", "credential_env", "X-Amz-", "Signature=", "Expires=", "C:\\\\"):
        if forbidden in serialized:
            audit.error(f"Public cache summary contains forbidden credential, URL, or absolute-path material: {forbidden}")
    evidence_jobs: dict[str, dict[str, Any]] = {}
    for evidence_path in evidence_paths:
        if not evidence_path.is_file():
            continue
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        for job in evidence.get("jobs", []):
            if job.get("status") in {"cached", "fetched"} and job.get("cache", {}).get("sha256"):
                evidence_jobs[job["jobId"]] = job
    metadata = [job for job in evidence_jobs.values() if job.get("phase") in {"discover", "datalink"}]
    candidates = [job for job in evidence_jobs.values() if job.get("productContract", {}).get("readiness") in {"science-input-candidate", "validated-science-input"}]
    expected = {
        "metadataResponseCount": len(metadata),
        "cachedScienceInputCandidateCount": len(candidates),
        "validatedScienceInputCount": sum(job.get("validation", {}).get("scienceReady") is True for job in candidates),
        "comparisonReadyCount": sum(job.get("validation", {}).get("comparisonReady") is True for job in candidates),
    }
    summary = payload.get("summary", {})
    for key, value in expected.items():
        if summary.get(key) != value:
            audit.error(f"Public cache summary {key}={summary.get(key)!r}; evidence proves {value}")
    if len(payload.get("metadataResponses", [])) != expected["metadataResponseCount"]:
        audit.error("Public metadata response list does not match its evidence count")
    if len(payload.get("cachedScienceInputCandidates", [])) != expected["cachedScienceInputCandidateCount"]:
        audit.error("Public cached science-input candidate list does not match its evidence count")
    if any(item.get("scienceReady") and not item.get("sha256") for item in payload.get("cachedScienceInputCandidates", [])):
        audit.error("Public summary claims science readiness without a checksum")
    if any(item.get("comparisonReady") and not item.get("scienceReady") for item in payload.get("cachedScienceInputCandidates", [])):
        audit.error("Public summary claims comparison readiness before science-input validation")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=root / "pipeline/results/region-cache/acquisition-plan.json")
    parser.add_argument("--report", type=Path, default=root / "pipeline/results/region-cache/validation-report.json")
    parser.add_argument("--require-regions", type=int)
    parser.add_argument("--update-plan", action="store_true", help="Persist FITS validation fields and science-input promotions")
    parser.add_argument("--public-summary", type=Path, default=root / "public/data/coverage/cache-manifest.json")
    parser.add_argument("--evidence-plan", type=Path, action="append", default=[])
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    audit = Audit()
    if plan.get("schemaVersion") != "layers-acquisition-plan-v1":
        audit.error("Unsupported acquisition-plan schemaVersion")
    regions = plan.get("regions", [])
    jobs = plan.get("jobs", [])
    required_regions = args.require_regions
    if required_regions is not None and len(regions) != required_regions:
        audit.error(f"Expected {required_regions} regions, found {len(regions)}")
    if len({record.get("id") for record in regions}) != len(regions):
        audit.error("Region IDs are not unique")
    job_ids = [job.get("jobId") for job in jobs]
    if len(job_ids) != len(set(job_ids)):
        audit.error("Job IDs are not unique")
    for job in jobs:
        validate_job(job, root, audit)

    # Enforce the user's published Rubin rate limits at plan level too.
    rubin_limits = plan.get("quotaExecution", {}).get("rubinLimits", {})
    expected_limits = {"siaPerMinute": 70, "voCutoutsPerMinute": 35, "tapPerMinute": 1000, "hipsPerMinute": 1000, "heraldPerMinute": 250}
    if rubin_limits != expected_limits:
        audit.error(f"Rubin quota declaration differs from account limits: {rubin_limits}")
    if plan.get("sciencePolicy", {}).get("wholeArchiveDownloads") is not False:
        audit.error("Plan does not prohibit whole-archive downloads")
    if plan.get("sciencePolicy", {}).get("displayProductsAreScienceInputs") is not False:
        audit.error("Plan does not separate display pixels from science inputs")
    if plan.get("layerContracts") != LAYER_CONTRACTS:
        audit.error("Plan lacks the complete raster/catalog/spectrum/time-series/cube contract definitions")
    evidence_paths = args.evidence_plan or [
        root / "pipeline/results/region-cache/live-smoke/acquisition-plan.json",
        root / "pipeline/results/region-cache/legacy-smoke/acquisition-plan.json",
    ]
    validate_public_summary(args.public_summary, evidence_paths, audit)

    status_counts = Counter(job.get("status", "missing") for job in jobs)
    report = {
        "schemaVersion": "layers-region-cache-validation-v1",
        "validatedAt": utc_now(),
        "plan": args.plan.relative_to(root).as_posix() if args.plan.is_relative_to(root) else str(args.plan),
        "passed": not audit.errors,
        "regionCount": len(regions),
        "jobCount": len(jobs),
        "statusCounts": dict(sorted(status_counts.items())),
        "validatedScienceInputs": sum(bool(job.get("validation", {}).get("scienceReady")) for job in jobs),
        "comparisonReadyProducts": sum(bool(job.get("validation", {}).get("comparisonReady")) for job in jobs),
        "errors": audit.errors,
        "warnings": audit.warnings,
        "invariants": {
            "deterministicCacheKeys": not any("cache key" in message for message in audit.errors),
            "checksumsVerifiedForCachedFiles": not any("cached SHA-256" in message or "missing cached" in message for message in audit.errors),
            "displayOnlyExcludedFromScience": not any("display-only" in message for message in audit.errors),
            "quotaPoliciesWithinLimits": not any("quota" in message or "request rate" in message for message in audit.errors),
            "wholeArchiveDownloadDisabled": plan.get("sciencePolicy", {}).get("wholeArchiveDownloads") is False,
            "publicEvidenceSummaryVerified": not any("Public" in message or "forbidden" in message for message in audit.errors),
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.update_plan:
        args.plan.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    if audit.errors:
        for message in audit.errors:
            print(f"ERROR: {message}")
        raise SystemExit(1)
    print(f"PASS: {len(regions)} regions, {len(jobs)} jobs, {len(audit.warnings)} warnings")


if __name__ == "__main__":
    main()
