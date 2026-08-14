#!/usr/bin/env python3
"""Publish a URL-free summary of one or more Layers acquisition plans."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fetch_region_layers import public_manifest


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def relative_label(path: Path, root: Path) -> str:
    resolved = path.resolve()
    return resolved.relative_to(root).as_posix() if resolved.is_relative_to(root) else path.name


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-plan", type=Path, required=True)
    parser.add_argument("--evidence-plan", type=Path, action="append", required=True)
    parser.add_argument("--public-summary", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    primary = json.loads(args.primary_plan.read_text(encoding="utf-8"))
    evidence = [
        (path.parent.name, json.loads(path.read_text(encoding="utf-8")))
        for path in args.evidence_plan
    ]
    public = public_manifest(primary, evidence)

    jobs_by_id: dict[str, dict[str, Any]] = {}
    region_records: dict[str, dict[str, Any]] = {}
    for _, plan in evidence:
        for region in plan.get("regions", []):
            region_records[region["id"]] = region
        for job in plan.get("jobs", []):
            jobs_by_id[job["jobId"]] = job

    cached = [
        job for job in jobs_by_id.values()
        if job.get("status") in {"cached", "fetched"} and job.get("cache", {}).get("sha256")
    ]
    per_survey: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "jobCount": 0,
        "statusCounts": Counter(),
        "phaseCounts": Counter(),
        "cachedResponseCount": 0,
        "cachedBytes": 0,
        "successfulRegionIds": set(),
        "errorRegionIds": set(),
        "scienceInputCandidateCount": 0,
        "validatedScienceInputCount": 0,
        "comparisonReadyCount": 0,
        "metadataRowCount": 0,
        "maximumRowsInResponse": 0,
    })
    for job in jobs_by_id.values():
        bucket = per_survey[job["surveyId"]]
        bucket["jobCount"] += 1
        bucket["statusCounts"][job.get("status", "missing")] += 1
        bucket["phaseCounts"][job.get("phase", "missing")] += 1
        if job.get("status") == "error":
            bucket["errorRegionIds"].add(job["region"]["id"])
        if job in cached:
            bucket["cachedResponseCount"] += 1
            bucket["cachedBytes"] += int(job["cache"]["bytes"])
            bucket["successfulRegionIds"].add(job["region"]["id"])
            row_count = int(job.get("responseSummary", {}).get("rowCount") or 0)
            bucket["metadataRowCount"] += row_count
            bucket["maximumRowsInResponse"] = max(bucket["maximumRowsInResponse"], row_count)
            if job.get("productContract", {}).get("readiness") in {"science-input-candidate", "validated-science-input"}:
                bucket["scienceInputCandidateCount"] += 1
                bucket["validatedScienceInputCount"] += int(job.get("validation", {}).get("scienceReady") is True)
                bucket["comparisonReadyCount"] += int(job.get("validation", {}).get("comparisonReady") is True)

    survey_rows = []
    for survey_id, bucket in sorted(per_survey.items()):
        survey_rows.append({
            "surveyId": survey_id,
            "jobCount": bucket["jobCount"],
            "statusCounts": dict(sorted(bucket["statusCounts"].items())),
            "phaseCounts": dict(sorted(bucket["phaseCounts"].items())),
            "cachedResponseCount": bucket["cachedResponseCount"],
            "cachedBytes": bucket["cachedBytes"],
            "successfulRegionCount": len(bucket["successfulRegionIds"]),
            "successfulRegionIds": sorted(bucket["successfulRegionIds"]),
            "errorRegionCount": len(bucket["errorRegionIds"]),
            "errorRegionIds": sorted(bucket["errorRegionIds"]),
            "scienceInputCandidateCount": bucket["scienceInputCandidateCount"],
            "validatedScienceInputCount": bucket["validatedScienceInputCount"],
            "comparisonReadyCount": bucket["comparisonReadyCount"],
            "metadataRowCount": bucket["metadataRowCount"],
            "maximumRowsInResponse": bucket["maximumRowsInResponse"],
        })

    exact = [{
        "jobId": job["jobId"],
        "regionId": job["region"]["id"],
        "tract": job["region"].get("tract"),
        "surveyId": job["surveyId"],
        "phase": job["phase"],
        "status": job["status"],
        "bytes": job["cache"]["bytes"],
        "sha256": job["cache"]["sha256"],
        "rowCount": job.get("responseSummary", {}).get("rowCount"),
        "readiness": job.get("productContract", {}).get("readiness"),
        "scienceReady": job.get("validation", {}).get("scienceReady") is True,
        "comparisonReady": job.get("validation", {}).get("comparisonReady") is True,
    } for job in sorted(cached, key=lambda value: (value["surveyId"], value["region"]["id"], value["phase"], value["jobId"]))]

    successful_regions = {job["region"]["id"] for job in cached}
    report = {
        "schemaVersion": "layers-acquisition-summary-v1",
        "generatedAt": utc_now(),
        "evidencePlans": [relative_label(path, root) for path in args.evidence_plan],
        "scope": {
            "selectedRegionCount": len(region_records),
            "regionsWithCachedEvidence": len(successful_regions),
            "cutoutSizeArcmin": sorted({region["sizeArcmin"] for region in region_records.values()}),
            "wholeArchiveDownloads": False,
            "genericSiaMaximumRecordsPerResponse": 500,
        },
        "summary": {
            "uniqueJobCount": len(jobs_by_id),
            "cachedResponseCount": len(cached),
            "cachedBytes": sum(int(job["cache"]["bytes"]) for job in cached),
            "metadataResponseCount": sum(job["phase"] in {"discover", "datalink"} for job in cached),
            "scienceInputCandidateCount": sum(job.get("productContract", {}).get("readiness") in {"science-input-candidate", "validated-science-input"} for job in cached),
            "validatedScienceInputCount": sum(job.get("validation", {}).get("scienceReady") is True for job in cached),
            "comparisonReadyCount": sum(job.get("validation", {}).get("comparisonReady") is True for job in cached),
        },
        "surveys": survey_rows,
        "cachedResponses": exact,
        "interpretation": {
            "coverageIsNotValidPixels": True,
            "metadataDiscoveryIsNotScienceReady": True,
            "scienceCandidatesRequireSupportPlanes": True,
            "comparisonRequiresAdditionalPsfBandpassBackgroundMaskAndUncertaintyQa": True,
        },
    }

    for destination, payload in ((args.public_summary, public), (args.report, report)):
        serialized = json.dumps(payload, indent=2)
        for forbidden in ("Authorization", "credential_env", "X-Amz-", "Signature=", "Expires=", "http://", "https://", "C:\\\\"):
            if forbidden in serialized:
                raise SystemExit(f"Refusing to publish {destination}: found forbidden material {forbidden}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized, encoding="utf-8")
        print(f"Wrote {destination}")


if __name__ == "__main__":
    main()
