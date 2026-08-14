#!/usr/bin/env python3
"""Build and optionally execute bounded, quota-aware Layers acquisition jobs.

The default mode is a dry run. It creates a complete, deterministic plan from
the 50 selected Rubin regions and their confirmed overlap survey IDs without
downloading pixels. Use ``--mode discovery`` to query supported metadata APIs,
or ``--mode science`` to also fetch explicitly bounded archive-native FITS
inputs. Successful responses are immutable cache entries and are reused.

Examples:
    python pipeline/fetch_region_layers.py
    python pipeline/fetch_region_layers.py --mode discovery --only-region dp2-tract-1234
    python pipeline/fetch_region_layers.py --mode science --only-survey legacy-surveys-dr10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from layer_connectors import (
    AcquisitionJob,
    LAYER_CONTRACTS,
    ProductContract,
    QuotaPolicy,
    RequestSpec,
    RubinDP2Connector,
    SkyRegion,
    connectors_from_registry,
    public_connector_contracts,
    response_summary,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def find_regions_file(root: Path) -> Path:
    candidates = (
        root / "pipeline/results/coverage/selected-regions.json",
        root / "pipeline/results/selected-regions.json",
        root / "public/data/coverage/selected-regions.json",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit("No selected-regions.json exists yet; run the overlap/region selector first or pass --regions.")


def _float(record: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = record.get(name)
        if value is not None:
            return float(value)
    return None


def load_regions(path: Path) -> list[tuple[SkyRegion, list[str], dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload if isinstance(payload, list) else next(
        (payload[key] for key in ("regions", "selectedRegions", "targets") if isinstance(payload.get(key), list)), []
    )
    result = []
    for index, record in enumerate(records):
        center = record.get("center")
        ra = float(center[0]) if isinstance(center, list) and len(center) >= 2 else _float(record, "raDeg", "ra_deg", "ra")
        dec = float(center[1]) if isinstance(center, list) and len(center) >= 2 else _float(record, "decDeg", "dec_deg", "dec")
        if ra is None or dec is None:
            raise ValueError(f"Region {index} lacks a center")
        radius = _float(record, "radiusArcmin", "radius_arcmin")
        size = _float(record, "sizeArcmin", "size_arcmin", "field_width_arcmin")
        if size is None:
            size = 2.0 * radius if radius is not None else 12.0
        # Selected-region records describe tract-scale coverage (often more
        # than two degrees across). Acquisition jobs are deliberately bounded
        # science cutouts, not downloads of the whole tract.
        cutout_size = min(size, 30.0)
        region = SkyRegion(
            id=str(record.get("id") or record.get("slug") or f"region-{index + 1:03d}"),
            ra_deg=ra % 360,
            dec_deg=dec,
            size_arcmin=cutout_size,
            tract=int(record["tract"]) if record.get("tract") is not None else None,
        )
        confirmed = record.get("confirmedSurveyIds") or record.get("confirmed_survey_ids") or record.get("surveyIds") or []
        result.append((region, sorted(set(str(value) for value in confirmed)), record))
    if not result:
        raise ValueError(f"No regions found in {path}")
    ids = [item[0].id for item in result]
    if len(ids) != len(set(ids)):
        raise ValueError("Region IDs must be unique")
    return result


def extension_for(job: dict[str, Any]) -> str:
    accept = job["request"]["accept"].lower()
    if "votable" in accept or "xml" in accept:
        return ".vot"
    if "csv" in accept:
        return ".csv"
    if "fits" in accept:
        return ".fits"
    if "json" in accept:
        return ".json"
    if "jpeg" in accept:
        return ".jpg"
    if "png" in accept:
        return ".png"
    return ".bin"


def safe_cache_path(cache_root: Path, job: dict[str, Any]) -> Path:
    # cacheKey components come from normalized identifiers and a hex digest.
    directory = cache_root.joinpath(*job["cacheKey"].split("/"))
    phase_digest = hashlib.sha256(job["request"]["url"].encode("utf-8")).hexdigest()[:12]
    return directory / f"{job['phase']}-{phase_digest}{extension_for(job)}"


class RequestExecutor:
    def __init__(self, dotenv: dict[str, str]) -> None:
        self.dotenv = dotenv
        self.last_request: dict[str, float] = {}

    def _credential(self, env_name: str | None) -> str | None:
        if not env_name:
            return None
        return os.environ.get(env_name) or self.dotenv.get(env_name)

    def _pace(self, policy: dict[str, Any]) -> None:
        service = policy["service"]
        interval = 60.0 / float(policy["configured_requests_per_minute"])
        elapsed = time.monotonic() - self.last_request.get(service, -1e9)
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self.last_request[service] = time.monotonic()

    def fetch(self, job: dict[str, Any], path: Path) -> tuple[str, str | None]:
        if path.is_file() and path.stat().st_size > 0:
            return "cached", None
        request_record = job["request"]
        credential = self._credential(request_record.get("credential_env"))
        if request_record.get("credential_env") and not credential:
            return "blocked-missing-credential", request_record["credential_env"]
        self._pace(job["quotaPolicy"])
        headers = {"Accept": request_record["accept"], "User-Agent": "Layers-science-cache/0.1"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        body = request_record.get("body")
        if request_record.get("content_type"):
            headers["Content-Type"] = request_record["content_type"]
        data = body.encode("utf-8") if body is not None else None
        request_url = job.get("_runtimeUrl", request_record["url"])
        request = urllib.request.Request(request_url, data=data, headers=headers, method=request_record["method"])
        retry_codes = set(job["quotaPolicy"]["retry_http_statuses"])
        last_error: Exception | None = None
        for attempt in range(1, 5):
            try:
                with urllib.request.urlopen(request, timeout=240) as response:
                    content_type = response.headers.get_content_type()
                    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as handle:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                        temporary = Path(handle.name)
                if temporary.stat().st_size == 0:
                    temporary.unlink(missing_ok=True)
                    raise RuntimeError("Archive returned an empty response")
                temporary.replace(path)
                return "fetched", content_type
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code not in retry_codes or attempt == 4:
                    break
                retry_after = float(error.headers.get("Retry-After", 0) or 0)
                time.sleep(max(retry_after, min(2**attempt, 30)))
            except urllib.error.URLError as error:
                last_error = error
                if attempt == 4:
                    break
                time.sleep(min(2**attempt, 30))
        return "error", f"{type(last_error).__name__}: {last_error}"


def rubric_data_jobs(region: SkyRegion, publisher_ids: list[str]) -> list[dict[str, Any]]:
    """Expand authenticated Rubin SIA discoveries into DataLink jobs."""

    return [job.to_record() for job in RubinDP2Connector().datalink_jobs(region, publisher_ids)]


def datalink_download_jobs(parent: dict[str, Any], payload: bytes) -> list[dict[str, Any]]:
    """Convert DataLink #this rows into bounded archive-native FITS jobs."""

    from layer_connectors import parse_votable_rows

    result = []
    for index, row in enumerate(parse_votable_rows(payload)):
        semantics = row.get("semantics", "")
        access_url = row.get("access_url", "")
        content_type = row.get("content_type", "") or "application/fits"
        if not access_url or semantics.rstrip("/").split("/")[-1] not in {"#this", "this"}:
            continue
        region = SkyRegion(**parent["region"])
        publisher_id = parent.get("metadata", {}).get("publisherDatasetId") or row.get("ID") or f"row-{index}"
        split_url = urllib.parse.urlsplit(access_url)
        provenance_url = urllib.parse.urlunsplit((split_url.scheme, split_url.netloc, split_url.path, "", ""))
        job = AcquisitionJob(
            region=region,
            survey_id="rubin-dp2",
            release="DP2",
            band=row.get("bandpass") or None,
            phase="acquire",
            request=RequestSpec("GET", provenance_url, "Acquire immutable Rubin deep-coadd FITS selected by DataLink #this; refresh signed URL through DataLink", content_type, "RUBIN_RSP_TOKEN"),
            quota=QuotaPolicy("rubin-object-storage", None, 120, 4),
            product=ProductContract(
                "raster", content_type, "science-input-candidate", False, "archive FITS BUNIT; verify header", True, True, True,
                notes=("Retain the complete archive artifact and publisher dataset identifier.", "Validate image, variance, mask, WCS, photometric calibration, and checksums before use."),
            ),
            provider="Rubin Science Platform",
            source_documentation=("https://dp2.lsst.io/products/images/deep_coadd.html",),
            metadata={"publisherDatasetId": publisher_id, "dataLinkSemantics": semantics, "signedAccessUrlPersisted": False, "refreshViaDataLink": True},
        ).to_record()
        # The time-limited query string is used in memory only and removed
        # before writing the acquisition plan.
        job["_runtimeUrl"] = access_url
        job["provenance"]["publisherDatasetIds"] = [publisher_id]
        result.append(job)
    return result


def should_execute(job: dict[str, Any], mode: str) -> bool:
    if mode == "dry-run":
        return False
    if job.get("metadata", {}).get("automaticExecutionAllowed") is False:
        return False
    if job["phase"] in {"discover", "datalink"}:
        return True
    return mode == "science" and job["phase"] == "acquire"


def load_evidence_plans(paths: list[Path], root: Path) -> list[tuple[str, dict[str, Any]]]:
    evidence = []
    for path in paths:
        if not path.is_file():
            continue
        label = path.parent.name or path.stem
        evidence.append((label, json.loads(path.read_text(encoding="utf-8"))))
    return evidence


def public_manifest(plan: dict[str, Any], evidence_plans: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    statuses = Counter(job["status"] for job in plan["jobs"])
    surveys = Counter(job["surveyId"] for job in plan["jobs"])
    phases = Counter(job["phase"] for job in plan["jobs"])
    evidence_jobs: dict[str, dict[str, Any]] = {}
    for _, evidence_plan in evidence_plans:
        for job in evidence_plan.get("jobs", []):
            if job.get("status") in {"cached", "fetched"} and job.get("cache", {}).get("sha256"):
                evidence_jobs[job["jobId"]] = job
    metadata_responses = [job for job in evidence_jobs.values() if job.get("phase") in {"discover", "datalink"}]
    cached_candidates = [
        job for job in evidence_jobs.values()
        if job.get("productContract", {}).get("readiness") in {"science-input-candidate", "validated-science-input"}
    ]
    validated = [job for job in cached_candidates if job.get("validation", {}).get("scienceReady") is True]
    comparison_ready = [job for job in cached_candidates if job.get("validation", {}).get("comparisonReady") is True]
    return {
        "schemaVersion": "layers-public-cache-manifest-v1",
        "generatedAt": plan["generatedAt"],
        "sourceRegions": "/data/coverage/selected-regions.json",
        "sourceEvidencePlans": [label for label, _ in evidence_plans],
        "summary": {
            "plannedRegionCount": len(plan["regions"]),
            "plannedJobCount": len(plan["jobs"]),
            "metadataResponseCount": len(metadata_responses),
            "cachedScienceInputCandidateCount": len(cached_candidates),
            "validatedScienceInputCount": len(validated),
            "comparisonReadyCount": len(comparison_ready),
        },
        "honestStatus": (
            f"{len(plan['regions'])} regions have acquisition plans. "
            f"{len(metadata_responses)} metadata responses are cached. "
            f"{len(cached_candidates)} archive-native science-input candidate{' is' if len(cached_candidates) == 1 else 's are'} cached. "
            f"{len(validated)} inputs have passed support-plane validation and {len(comparison_ready)} are comparison-ready."
        ),
        "regionCount": len(plan["regions"]),
        "regions": plan["regions"],
        "plannedJobs": {
            "total": len(plan["jobs"]), "byStatus": dict(sorted(statuses.items())),
            "byPhase": dict(sorted(phases.items())), "bySurvey": dict(sorted(surveys.items())),
        },
        "metadataResponses": [
            {
                "jobId": job["jobId"], "regionId": job["region"]["id"], "surveyId": job["surveyId"],
                "release": job["release"], "phase": job["phase"], "status": job["status"],
                "format": job.get("responseSummary", {}).get("format"), "rowCount": job.get("responseSummary", {}).get("rowCount"),
                "bytes": job["cache"]["bytes"], "sha256": job["cache"]["sha256"], "retrievedAt": job["cache"]["retrievedAt"],
            }
            for job in sorted(metadata_responses, key=lambda value: (value["surveyId"], value["phase"], value["jobId"]))
        ],
        "cachedScienceInputCandidates": [
            {
                "jobId": job["jobId"], "cacheKey": job["cacheKey"], "regionId": job["region"]["id"],
                "surveyId": job["surveyId"], "release": job["release"], "band": job["band"],
                "sha256": job["cache"]["sha256"], "bytes": job["cache"]["bytes"],
                "readiness": job["productContract"]["readiness"],
                "scienceReady": job["validation"]["scienceReady"], "comparisonReady": job["validation"]["comparisonReady"],
                "supportPlaneChecks": {
                    "wcsPresent": job["validation"].get("wcsPresent"), "unitsVerified": job["validation"].get("unitsVerified"),
                    "variancePresent": job["validation"].get("variancePresent"), "maskPresent": job["validation"].get("maskPresent"),
                    "coveragePresent": job["validation"].get("coveragePresent"),
                },
            }
            for job in sorted(cached_candidates, key=lambda value: (value["surveyId"], value["region"]["id"], value["jobId"]))
        ],
        "policy": {
            "wholeArchiveDownloads": False,
            "cutoutsFetchedOnDemand": True,
            "displayOnlyNeverQuantitative": True,
            "scienceReadyRequiresLocalValidation": True,
            "coverageDoesNotImplyValidPixels": True,
        },
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", type=Path)
    parser.add_argument("--registry", type=Path, default=root / "public/data/survey-registry.json")
    parser.add_argument("--output", type=Path, default=root / "pipeline/results/region-cache")
    parser.add_argument("--cache", type=Path, default=root / "pipeline/output/region-cache")
    parser.add_argument("--public-manifest", type=Path, default=root / "public/data/coverage/cache-manifest.json")
    parser.add_argument("--env", type=Path, default=root / ".env")
    parser.add_argument("--mode", choices=("dry-run", "discovery", "science"), default="dry-run")
    parser.add_argument("--only-region", action="append", default=[])
    parser.add_argument("--only-survey", action="append", default=[])
    parser.add_argument("--bands", default="")
    parser.add_argument("--max-regions", type=int)
    parser.add_argument(
        "--cutout-size-arcmin",
        type=float,
        help="Override the bounded acquisition footprint while preserving selected region centers and tract IDs.",
    )
    parser.add_argument("--evidence-plan", type=Path, action="append", default=[])
    args = parser.parse_args()

    regions_path = args.regions or find_regions_file(root)
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    connectors = connectors_from_registry(registry)
    selected_regions = load_regions(regions_path)
    if args.cutout_size_arcmin is not None:
        if not 0 < args.cutout_size_arcmin <= 120:
            raise SystemExit("--cutout-size-arcmin must be in (0, 120]")
        selected_regions = [
            (
                SkyRegion(
                    id=region.id,
                    ra_deg=region.ra_deg,
                    dec_deg=region.dec_deg,
                    size_arcmin=args.cutout_size_arcmin,
                    tract=region.tract,
                ),
                confirmed_ids,
                source,
            )
            for region, confirmed_ids, source in selected_regions
        ]
    only_regions = set(args.only_region)
    only_surveys = set(args.only_survey)
    if only_regions:
        selected_regions = [item for item in selected_regions if item[0].id in only_regions]
    if args.max_regions is not None:
        selected_regions = selected_regions[: args.max_regions]
    requested_bands = [value.strip() for value in args.bands.split(",") if value.strip()]

    jobs: list[dict[str, Any]] = []
    region_records = []
    for region, confirmed_ids, source in selected_regions:
        survey_ids = sorted(set(["rubin-dp2", *confirmed_ids]))
        if only_surveys:
            survey_ids = [survey_id for survey_id in survey_ids if survey_id in only_surveys]
        region_records.append({
            "id": region.id, "tract": region.tract, "center": [region.ra_deg, region.dec_deg], "sizeArcmin": region.size_arcmin,
            "confirmedSurveyIds": confirmed_ids,
            "selectionReasons": source.get("selectionReasons", []),
        })
        for survey_id in survey_ids:
            connector = connectors.get(survey_id)
            if connector is None:
                continue
            jobs.extend(job.to_record() for job in connector.jobs(region, requested_bands or None))

    # Stable order makes plan diffs and cache audits meaningful.
    for job in jobs:
        job.pop("_runtimeUrl", None)
    jobs.sort(key=lambda job: (job["region"]["id"], job["surveyId"], job["phase"], job["jobId"]))
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    executor = RequestExecutor(read_dotenv(args.env))
    extra_jobs: list[dict[str, Any]] = []
    for job in jobs:
        if not should_execute(job, args.mode):
            if job.get("metadata", {}).get("automaticExecutionAllowed") is False:
                job["status"] = "planned-adapter-required"
            continue
        path = safe_cache_path(args.cache, job)
        path.parent.mkdir(parents=True, exist_ok=True)
        status, note = executor.fetch(job, path)
        job["status"] = status
        if status in {"cached", "fetched"}:
            payload = path.read_bytes()
            summary = response_summary(job, payload)
            job["cache"].update({
                "path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_bytes(payload),
                "retrievedAt": utc_now() if status == "fetched" else datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "responseContentType": note or job["request"]["accept"],
            })
            job["responseSummary"] = summary
            job["provenance"]["publisherDatasetIds"] = summary.get("publisherDatasetIds", job["provenance"]["publisherDatasetIds"])
            if job["surveyId"] == "rubin-dp2" and job["phase"] == "discover":
                extra_jobs.extend(rubric_data_jobs(SkyRegion(**job["region"]), summary.get("publisherDatasetIds", [])))
            if job["surveyId"] == "rubin-dp2" and job["phase"] == "datalink":
                extra_jobs.extend(datalink_download_jobs(job, payload))
        elif note:
            job["statusDetail"] = note
        print(f"[{job['status']}] {job['region']['id']} {job['surveyId']} {job['phase']}", flush=True)

    # DataLink expansion is intentionally a second sequential stage.
    for job in extra_jobs:
        if should_execute(job, args.mode):
            path = safe_cache_path(args.cache, job)
            path.parent.mkdir(parents=True, exist_ok=True)
            status, note = executor.fetch(job, path)
            job["status"] = status
            if status in {"cached", "fetched"}:
                payload = path.read_bytes()
                summary = response_summary(job, payload)
                job["cache"].update({"path": path.relative_to(root).as_posix(), "bytes": len(payload), "sha256": sha256_bytes(payload), "retrievedAt": utc_now(), "responseContentType": note or job["request"]["accept"]})
                job["responseSummary"] = summary
                if job["phase"] == "datalink":
                    jobs.extend(datalink_download_jobs(job, payload))
            elif note:
                job["statusDetail"] = note
        jobs.append(job)

    for job in jobs:
        job.pop("_runtimeUrl", None)
    jobs.sort(key=lambda job: (job["region"]["id"], job["surveyId"], job["phase"], job["jobId"]))
    generated_at = utc_now()
    plan = {
        "schemaVersion": "layers-acquisition-plan-v1",
        "generatedAt": generated_at,
        "mode": args.mode,
        "sourceRegions": regions_path.relative_to(root).as_posix() if regions_path.is_relative_to(root) else str(regions_path),
        "surveyRegistry": args.registry.relative_to(root).as_posix() if args.registry.is_relative_to(root) else str(args.registry),
        "regions": region_records,
        "connectors": public_connector_contracts(registry),
        "layerContracts": LAYER_CONTRACTS,
        "jobs": jobs,
        "quotaExecution": {
            "sequentialAcrossServices": True,
            "rubinLimits": {"siaPerMinute": 70, "voCutoutsPerMinute": 35, "tapPerMinute": 1000, "hipsPerMinute": 1000, "heraldPerMinute": 250},
            "configuredRubin": {"siaPerMinute": 55, "voCutoutsPerMinute": 30, "maximumDatalinkConcurrency": 1, "maximumObjectStorageConcurrency": 4},
            "cacheBeforeRequest": True,
            "honorRetryAfter": True,
        },
        "sciencePolicy": {
            "wholeArchiveDownloads": False,
            "displayProductsAreScienceInputs": False,
            "coverageRequiresPerProductAndValidPixelConfirmation": True,
            "promotionToScienceReady": "Only validate_region_cache.py may confirm archive support planes; comparison readiness additionally requires WCS/PSF/bandpass/background/mask/uncertainty QA.",
        },
    }
    plan_path = args.output / "acquisition-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    evidence_paths = args.evidence_plan or [
        root / "pipeline/results/region-cache/live-smoke/acquisition-plan.json",
        root / "pipeline/results/region-cache/legacy-smoke/acquisition-plan.json",
    ]
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.write_text(json.dumps(public_manifest(plan, load_evidence_plans(evidence_paths, root)), indent=2), encoding="utf-8")
    print(f"Wrote {len(jobs)} jobs for {len(region_records)} regions to {plan_path}")


if __name__ == "__main__":
    main()
