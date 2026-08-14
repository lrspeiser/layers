#!/usr/bin/env python3
"""Build reproducible cross-survey coverage and a diverse 50-region pilot.

Rubin tract geometry comes from the live DP2 ``CoaddPatches`` inventory.  A
survey is called a confirmed overlap only when an authoritative external MOC
intersects an exact Rubin patch polygon, or when the registry documents the
product as all-sky.  Static prose footprints are never promoted to confirmed
coverage.  Coverage also never implies valid, unmasked science pixels.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord
from mocpy import MOC


MOCSERVER = "https://alasky.cds.unistra.fr/MocServer/query"
MOC_ORDER = 10
USER_AGENT = "Rubin-Light-Atlas/0.3 (+https://github.com/lrspeiser/rubin-light-atlas)"
TARGET_REGION_COUNT = 50

# Conservative, independently hosted MOCs used when an archive exposes
# queryable observation geometry but the registry has no single footprint URL.
# A subset is useful here: it can prove an overlap without pretending to
# describe observations that are absent from the MOC.  Release mismatches are
# intentionally not substituted (for example, ACT DR5 is not called DR6).
CONSERVATIVE_MOC_IDS = {
    "des-dr2": "CDS/P/DES-DR2/ColorIRG",
    "ztf-dr": "CDS/P/ZTF/DR7/color",
    "desi-dr1": "CDS/V/161/zcatdr1",
    "erosita-erass1": "erosita/dr1/count/024",
    "vlass": "NRAO/P/VLASS-Quicklook-MedianStack",
    "lotss-dr2": "CDS/J/A+A/659/A1/catalog",
    "wallaby-pdr2": "CDS/C/ASKAP/WALLABY-Pilot-DR2",
    "hipass": "CDS/C/HIPASS",
    "hst": "CDS/P/HST/*",
    "euclid-q1": "CDS/P/Euclid/Q1/*",
    "jwst": "CDS/P/JWST/*",
    "alma": "CDS/C/PHANGS-ALMA",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def atomic_write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False) if compact else json.dumps(
        value, indent=2, ensure_ascii=False
    ) + "\n"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_patch_polygons(path: Path) -> dict[int, list[SkyCoord]]:
    grouped: dict[int, list[SkyCoord]] = defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            patch = json.loads(line)
            grouped[int(patch["tract"])].append(
                SkyCoord(
                    [point[0] for point in patch["polygon"]] * u.deg,
                    [point[1] for point in patch["polygon"]] * u.deg,
                    frame="icrs",
                )
            )
    return grouped


def build_tract_mocs(patches: dict[int, list[SkyCoord]]) -> dict[int, MOC]:
    result: dict[int, MOC] = {}
    for index, (tract, polygons) in enumerate(sorted(patches.items()), start=1):
        patch_mocs = MOC.from_polygons(polygons, max_depth=MOC_ORDER)
        if not patch_mocs:
            raise RuntimeError(f"Rubin tract {tract} has no patch polygons")
        result[tract] = patch_mocs[0] if len(patch_mocs) == 1 else patch_mocs[0].union(*patch_mocs[1:])
        if index % 250 == 0:
            print(f"Built exact-polygon MOCs for {index:,}/{len(patches):,} Rubin tracts")
    return result


def moc_url(survey: dict[str, Any], *, order: int = MOC_ORDER) -> str | None:
    coverage = survey["coverage"]
    identifier = coverage.get("mocId") or CONSERVATIVE_MOC_IDS.get(survey["id"])
    if identifier:
        return MOCSERVER + "?" + urllib.parse.urlencode(
            {"ID": identifier, "get": "smoc", "order": str(order)}
        )
    endpoint = coverage.get("footprintEndpoint")
    if endpoint and coverage.get("geometrySource") in {"moc", "hips"}:
        return endpoint
    return None


def fetch_moc(
    survey: dict[str, Any], cache_dir: Path, *, refresh: bool
) -> tuple[MOC | None, dict[str, Any]]:
    url = moc_url(survey)
    retrieved_at = utc_now()
    if not url:
        return None, {
            "method": "unresolved-no-machine-readable-footprint",
            "sourceUrl": None,
            "retrievedAt": retrieved_at,
            "cacheFile": None,
            "sha256": None,
            "note": "No MOC was declared; prose or approximate area was not used to assert overlap.",
        }

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{survey['id']}.moc.fits"
    source = "cache"
    try:
        requested_order = MOC_ORDER
        if refresh or not cache_path.exists():
            payload = None
            for requested_order in (MOC_ORDER, 8, 6):
                candidate_url = moc_url(survey, order=requested_order)
                if not candidate_url:
                    break
                request = urllib.request.Request(
                    candidate_url, headers={"Accept": "application/fits", "User-Agent": USER_AGENT}
                )
                try:
                    with urllib.request.urlopen(request, timeout=180) as response:
                        payload = response.read()
                    url = candidate_url
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code != 413 or requested_order == 6:
                        raise
            if payload is None:
                raise RuntimeError("MOC request produced no payload")
            if not payload:
                raise RuntimeError("empty response")
            temporary = cache_path.with_suffix(".fits.tmp")
            temporary.write_bytes(payload)
            temporary.replace(cache_path)
            source = "network"
        payload = cache_path.read_bytes()
        survey_moc = MOC.from_fits(cache_path)
        if survey_moc.empty():
            raise RuntimeError("empty MOC")
        if survey_moc.max_order > MOC_ORDER:
            survey_moc = survey_moc.degrade_to_order(MOC_ORDER)
        return survey_moc, {
            "method": "moc-intersection",
            "sourceUrl": url,
            "retrievedAt": retrieved_at,
            "cacheFile": cache_path.as_posix(),
            "cacheSource": source,
            "sha256": sha256(payload),
            "mocOrder": int(survey_moc.max_order),
            "requestedMocOrder": requested_order,
            "skyFraction": float(survey_moc.sky_fraction),
            "coverageSubset": survey["id"] in CONSERVATIVE_MOC_IDS,
            "mocId": survey["coverage"].get("mocId") or CONSERVATIVE_MOC_IDS.get(survey["id"]),
            "note": "MOC overlap establishes archive coverage, not valid science pixels in a requested cutout.",
        }
    except Exception as exc:
        return None, {
            "method": "unresolved-moc-error",
            "sourceUrl": url,
            "retrievedAt": retrieved_at,
            "cacheFile": cache_path.as_posix() if cache_path.exists() else None,
            "sha256": sha256(cache_path.read_bytes()) if cache_path.exists() else None,
            "note": f"MOC could not be validated: {type(exc).__name__}: {exc}",
        }


def normalized(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def map_layer_to_survey(layer_id: str, surveys: list[dict[str, Any]]) -> str | None:
    direct = {survey["id"]: survey["id"] for survey in surveys}
    if layer_id in direct:
        return direct[layer_id]
    aliases = {
        "legacy-survey-dr10": ["legacy-dr10", "legacy-surveys-dr10", "desi-legacy-dr10"],
        "panstarrs-dr1-stack": ["panstarrs-dr1", "pan-starrs-dr1", "panstarrs", "panstarrs-dr2"],
        "galex-gr6-7": ["galex", "galex-gr6", "galex-gr7"],
        "unwise": ["wise", "allwise", "wise-allwise"],
    }
    candidates = {normalized(survey["id"]): survey["id"] for survey in surveys}
    value = normalized(layer_id)
    if value in candidates:
        return candidates[value]
    for layer_alias, registry_aliases in aliases.items():
        if layer_id == layer_alias or layer_id in registry_aliases:
            for alias in [layer_alias, *registry_aliases]:
                if normalized(alias) in candidates:
                    return candidates[normalized(alias)]
    return None


def point_tract(ra: float, dec: float, tract_mocs: dict[int, MOC]) -> int | None:
    for tract, moc in tract_mocs.items():
        if bool(moc.contains_lonlat(ra * u.deg, dec * u.deg)):
            return tract
    return None


def discover_local_pixel_products(
    repo_root: Path, surveys: list[dict[str, Any]], tract_mocs: dict[int, MOC]
) -> tuple[dict[int, set[str]], list[dict[str, Any]]]:
    catalog_path = repo_root / "public" / "data" / "layers-catalog.json"
    preview_root = repo_root / "public" / "rubin-data"
    if not catalog_path.exists() or not preview_root.exists():
        return defaultdict(set), []
    targets = {
        target["id"]: (float(target["center"]["raDeg"]), float(target["center"]["decDeg"]))
        for target in load_json(catalog_path).get("targets", [])
    }
    by_tract: dict[int, set[str]] = defaultdict(set)
    products: list[dict[str, Any]] = []
    for preview_path in sorted(preview_root.glob("*/comparison-preview.json")):
        preview = load_json(preview_path)
        object_id = preview.get("objectId")
        if object_id not in targets:
            continue
        external_layers = [
            layer for layer in preview.get("layerIds", []) if layer != "rubin-dp2-deep-coadd"
        ]
        if len(external_layers) != 1:
            continue
        survey_id = map_layer_to_survey(external_layers[0], surveys)
        if not survey_id:
            continue
        reference = preview.get("assets", {}).get("reference", {})
        relative = str(reference.get("path", "")).lstrip("/")
        asset_path = repo_root / "public" / relative
        if not asset_path.is_file():
            continue
        payload = asset_path.read_bytes()
        if reference.get("sha256") and sha256(payload) != reference["sha256"]:
            continue
        ra, dec = targets[object_id]
        tract = point_tract(ra, dec, tract_mocs)
        if tract is None:
            continue
        by_tract[tract].add(survey_id)
        products.append(
            {
                "tract": tract,
                "surveyId": survey_id,
                "objectId": object_id,
                "center": [ra, dec],
                "previewMetadata": preview_path.relative_to(repo_root).as_posix(),
                "pixelAsset": asset_path.relative_to(repo_root).as_posix(),
                "sha256": sha256(payload),
            }
        )
    return by_tract, products


def tract_radius_arcmin(tract_row: list[Any]) -> float:
    bounds = tract_row[2]
    width = float(bounds["ra"]["width"]) * math.cos(math.radians(float(tract_row[1][1])))
    height = float(bounds["dec_max"]) - float(bounds["dec_min"])
    return round(max(1.0, 30.0 * math.hypot(width, height)), 2)


def angular_distance(a: list[float], b: list[float]) -> float:
    ra1, dec1, ra2, dec2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    cosine = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def select_regions(
    tract_rows: list[list[Any]],
    confirmed_by_tract: dict[int, set[str]],
    pixel_cached_by_tract: dict[int, set[str]],
    survey_by_id: dict[str, dict[str, Any]],
    locked_tract_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    candidates = [row for row in tract_rows if confirmed_by_tract[int(row[0])]]
    if len(candidates) < TARGET_REGION_COUNT:
        raise RuntimeError(f"Only {len(candidates)} Rubin tracts have confirmed external coverage")
    row_by_tract = {int(row[0]): row for row in candidates}
    if locked_tract_ids is not None:
        if len(locked_tract_ids) != TARGET_REGION_COUNT or len(set(locked_tract_ids)) != TARGET_REGION_COUNT:
            raise RuntimeError("Locked acquisition set must contain exactly 50 unique Rubin tracts")
        missing = sorted(set(locked_tract_ids) - set(row_by_tract))
        if missing:
            raise RuntimeError(f"Locked acquisition tracts no longer have confirmed coverage: {missing}")
        selected = [row_by_tract[tract] for tract in locked_tract_ids]
    else:
        selected = []
    families = sorted(
        {
            survey_by_id[survey_id]["family"]
            for survey_ids in confirmed_by_tract.values()
            for survey_id in survey_ids
        }
    )
    family_counts: Counter[str] = Counter()
    selected_ids: set[int] = {int(row[0]) for row in selected}
    desired_per_family = max(1, math.ceil(TARGET_REGION_COUNT / max(1, len(families))))

    while locked_tract_ids is None and len(selected) < TARGET_REGION_COUNT:
        best: tuple[float, int, list[Any]] | None = None
        for row in candidates:
            tract = int(row[0])
            if tract in selected_ids:
                continue
            survey_ids = confirmed_by_tract[tract]
            row_families = {survey_by_id[item]["family"] for item in survey_ids}
            scarcity = sum(max(0, desired_per_family - family_counts[family]) for family in row_families)
            rare_bonus = sum(1.0 / (1.0 + family_counts[family]) for family in row_families)
            cache_bonus = 4.0 * len(pixel_cached_by_tract.get(tract, set()))
            separation = min(
                (angular_distance(row[1], selected_row[1]) for selected_row in selected), default=180.0
            )
            score = scarcity * 8.0 + rare_bonus * 3.0 + len(survey_ids) + cache_bonus + min(separation, 30.0) / 10.0
            tie_break = -tract
            candidate = (score, tie_break, row)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        if best is None:
            break
        row = best[2]
        tract = int(row[0])
        selected.append(row)
        selected_ids.add(tract)
        family_counts.update({survey_by_id[item]["family"] for item in confirmed_by_tract[tract]})

    regions: list[dict[str, Any]] = []
    for rank, row in enumerate(selected, start=1):
        tract = int(row[0])
        survey_ids = sorted(confirmed_by_tract[tract])
        row_families = sorted({survey_by_id[item]["family"] for item in survey_ids})
        regions.append(
            {
                "id": f"dp2-tract-{tract}",
                "rank": rank,
                "tract": tract,
                "center": row[1],
                "radiusArcmin": tract_radius_arcmin(row),
                "confirmedSurveyIds": survey_ids,
                "surveyFamilies": row_families,
                "pixelCachedSurveyIds": sorted(pixel_cached_by_tract.get(tract, set())),
                "selectionReasons": [
                    f"confirmed {family} coverage" for family in row_families
                ] + (["existing locally verified comparison pixels"] if pixel_cached_by_tract.get(tract) else []),
            }
        )
    return regions


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=repo_root / "public" / "data" / "survey-registry.json")
    parser.add_argument(
        "--rubin-index", type=Path, default=repo_root / "public" / "data" / "coverage" / "rubin-dp2-footprint.json"
    )
    parser.add_argument(
        "--rubin-patches",
        type=Path,
        default=repo_root / "pipeline" / "results" / "coverage" / "dp2-coadd-patches.jsonl.gz",
    )
    parser.add_argument(
        "--results-dir", type=Path, default=repo_root / "pipeline" / "results" / "coverage"
    )
    parser.add_argument(
        "--lock-selection-to",
        type=Path,
        default=repo_root / "public" / "data" / "coverage" / "rubin-pixels-50.json",
        help="Keep the already acquired 50-tract science set stable while refreshing overlap evidence",
    )
    parser.add_argument("--refresh", action="store_true", help="Refresh external MOC cache")
    args = parser.parse_args()

    registry = load_json(args.registry)
    rubin = load_json(args.rubin_index)
    surveys = registry["surveys"]
    survey_by_id = {survey["id"]: survey for survey in surveys}
    if len(survey_by_id) != len(surveys):
        raise RuntimeError("Duplicate survey IDs in registry")

    patch_polygons = load_patch_polygons(args.rubin_patches)
    tract_mocs = build_tract_mocs(patch_polygons)
    tract_rows = rubin["tracts"]
    tract_ids = {int(row[0]) for row in tract_rows}
    if tract_ids != set(tract_mocs):
        raise RuntimeError("Public tract index and exact patch inventory disagree")

    confirmed_by_tract: dict[int, set[str]] = defaultdict(set)
    approximate_by_tract: dict[int, set[str]] = defaultdict(set)
    unresolved_by_tract: dict[int, set[str]] = defaultdict(set)
    survey_summaries: list[dict[str, Any]] = []
    external_cache = args.results_dir / "cache" / "external-mocs"

    for survey in surveys:
        coverage = survey["coverage"]
        survey_id = survey["id"]
        survey_moc: MOC | None = None
        if coverage["type"] == "all-sky":
            evidence = {
                "method": "registry-all-sky",
                "sourceUrl": survey["provenanceUrls"][0],
                "retrievedAt": registry["generatedAt"],
                "cacheFile": None,
                "sha256": None,
                "note": "Officially documented all-sky product; this confirms archive coverage, not valid pixels or detections.",
            }
            coverage_status = "confirmed-all-sky"
            confirmed_ids = tract_ids
        else:
            survey_moc, evidence = fetch_moc(survey, external_cache, refresh=args.refresh)
            if survey_moc is not None:
                coverage_status = "resolved-moc"
                confirmed_ids = {
                    tract for tract, tract_moc in tract_mocs.items() if not tract_moc.intersection(survey_moc).empty()
                }
            else:
                coverage_status = "unresolved"
                confirmed_ids = set()
        for tract in confirmed_ids:
            confirmed_by_tract[tract].add(survey_id)
        if coverage_status == "unresolved":
            for tract in tract_ids:
                unresolved_by_tract[tract].add(survey_id)
        survey_summaries.append(
            {
                "surveyId": survey_id,
                "family": survey["family"],
                "coverageStatus": coverage_status,
                "confirmedTractCount": len(confirmed_ids),
                "approximateTractCount": 0,
                "unresolvedTractCount": len(tract_ids) if coverage_status == "unresolved" else 0,
                "evidence": evidence,
            }
        )
        print(f"{survey_id}: {coverage_status}, {len(confirmed_ids):,} confirmed Rubin tracts")

    pixel_cached_by_tract, local_products = discover_local_pixel_products(repo_root, surveys, tract_mocs)
    for tract, cached_ids in pixel_cached_by_tract.items():
        invalid = cached_ids - confirmed_by_tract[tract]
        if invalid:
            raise RuntimeError(f"Cached product coverage was not confirmed for tract {tract}: {sorted(invalid)}")

    generated_at = utc_now()
    tract_records = [
        [
            int(row[0]),
            sorted(confirmed_by_tract[int(row[0])]),
            sorted(approximate_by_tract[int(row[0])]),
            sorted(unresolved_by_tract[int(row[0])]),
            sorted(pixel_cached_by_tract[int(row[0])]),
        ]
        for row in tract_rows
    ]
    output = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "index": {
            "surveyId": "rubin-dp2",
            "release": rubin["release"],
            "tractCount": len(tract_rows),
            "patchCount": rubin["counts"]["patches"],
        },
        "fields": [
            "tract",
            "confirmedSurveyIds",
            "approximateSurveyIds",
            "unresolvedSurveyIds",
            "pixelCachedSurveyIds",
        ],
        "tracts": tract_records,
        "surveySummaries": survey_summaries,
        "counts": {
            "surveys": len(surveys),
            "resolvedSurveys": sum(item["coverageStatus"] != "unresolved" for item in survey_summaries),
            "unresolvedSurveys": sum(item["coverageStatus"] == "unresolved" for item in survey_summaries),
            "tractsWithConfirmedExternalCoverage": sum(bool(confirmed_by_tract[item]) for item in tract_ids),
            "tractSurveyConfirmedPairs": sum(len(confirmed_by_tract[item]) for item in tract_ids),
            "tractSurveyApproximatePairs": 0,
            "locallyVerifiedPixelProducts": len(local_products),
        },
        "methodology": {
            "rubinGeometry": "Exact DP2 CoaddPatches polygons rasterized to IVOA MOC order 10.",
            "externalGeometry": "Authoritative CDS MOCServer/registry MOCs or an official all-sky declaration.",
            "coverageIsNotSciencePixels": True,
            "note": "Coverage indicates a geometric archive overlap only. A cutout still requires masks, exposure, variance and QA before scientific comparison.",
            "selectionUsesConfirmedOnly": True,
            "approximateFootprintsUsedForSelection": False,
        },
    }
    locked_tract_ids: list[int] | None = None
    if args.lock_selection_to and args.lock_selection_to.exists():
        locked_manifest = load_json(args.lock_selection_to)
        locked_tract_ids = [
            int(region["tract"])
            for region in locked_manifest.get("regions", [])
            if region.get("status") == "complete" and region.get("scienceReady") is not False
        ]
    selected = select_regions(
        tract_rows,
        confirmed_by_tract,
        pixel_cached_by_tract,
        survey_by_id,
        locked_tract_ids=locked_tract_ids,
    )
    family_counts = Counter(family for region in selected for family in region["surveyFamilies"])
    survey_counts = Counter(survey_id for region in selected for survey_id in region["confirmedSurveyIds"])
    available_families = sorted({summary["family"] for summary in survey_summaries if summary["confirmedTractCount"]})
    selected_output = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "requestedCount": TARGET_REGION_COUNT,
        "selectedCount": len(selected),
        "selectionMethod": (
            "Acquisition-locked refresh of the 50 automatically selected tracts with validated Rubin pixels; "
            "survey memberships are recomputed from current confirmed overlaps."
            if locked_tract_ids is not None else
            "Deterministic greedy coverage of underrepresented survey families, then survey count, "
            "verified local pixels and angular separation; confirmed overlaps only."
        ),
        "availableFamilies": available_families,
        "unrepresentedAvailableFamilies": sorted(set(available_families) - set(family_counts)),
        "familyCounts": dict(sorted(family_counts.items())),
        "surveyCounts": dict(sorted(survey_counts.items())),
        "regions": selected,
        "caveat": "Selection is a coverage-discovery queue, not a claim that any region contains a scientific anomaly.",
    }

    if len(selected) != TARGET_REGION_COUNT or len({item["tract"] for item in selected}) != TARGET_REGION_COUNT:
        raise RuntimeError("50-region selection is incomplete or contains duplicates")
    for region in selected:
        if not set(region["confirmedSurveyIds"]).issubset(confirmed_by_tract[region["tract"]]):
            raise RuntimeError(f"Unconfirmed survey in selected tract {region['tract']}")
    if selected_output["unrepresentedAvailableFamilies"]:
        raise RuntimeError(f"Available families omitted from selection: {selected_output['unrepresentedAvailableFamilies']}")

    public_dir = repo_root / "public" / "data" / "coverage"
    atomic_write_json(public_dir / "external-overlaps.json", output, compact=True)
    atomic_write_json(public_dir / "selected-regions.json", selected_output)
    atomic_write_json(args.results_dir / "external-overlaps.json", output)
    atomic_write_json(args.results_dir / "selected-regions.json", selected_output)
    atomic_write_json(args.results_dir / "local-pixel-products.json", {"generatedAt": generated_at, "products": local_products})
    atomic_write_json(
        args.results_dir / "overlap-validation.json",
        {
            "generatedAt": generated_at,
            "passed": True,
            "checks": {
                "tractIndexMatchesExactPatchInventory": True,
                "selectedRegionCountIs50": True,
                "selectedTractsUnique": True,
                "selectionUsesConfirmedOnly": True,
                "allAvailableFamiliesRepresented": True,
                "pixelCacheClaimsHaveVerifiedLocalAssets": True,
                "coverageExplicitlySeparatedFromValidSciencePixels": True,
            },
            "counts": output["counts"],
        },
    )
    print(f"Wrote {len(tract_records):,} tract overlap records and {len(selected)} selected regions")


if __name__ == "__main__":
    main()
