#!/usr/bin/env python3
"""Publish conservative overlap evidence without changing full-footprint claims."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path("pipeline/results/coverage/resolution-audit/resolution-audit.json"),
    )
    parser.add_argument(
        "--footprint",
        type=Path,
        default=Path("public/data/coverage/rubin-dp2-footprint.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("public/data/coverage/conservative-subset-evidence.json"),
    )
    args = parser.parse_args()

    audit = load_json(args.audit)
    footprint = load_json(args.footprint)
    if audit["rubinIndex"]["tractCount"] != 2191 or footprint["counts"]["tracts"] != 2191:
        raise RuntimeError("The audit and public footprint must both contain exactly 2,191 Rubin tracts")
    footprint_tracts = {int(row[0]) for row in footprint["tracts"]}
    if len(footprint_tracts) != 2191:
        raise RuntimeError("The public Rubin footprint contains duplicate or missing tract IDs")

    surveys: list[dict[str, Any]] = []
    by_tract: dict[int, dict[str, list[str]]] = defaultdict(
        lambda: {"fullFootprintSurveyIds": [], "conservativeSubsetSurveyIds": []}
    )
    for item in audit["audited"]:
        tract_ids = [int(tract) for tract in item["confirmedRubinTractIds"]]
        if not set(tract_ids).issubset(footprint_tracts):
            raise RuntimeError(f"{item['surveyId']} references a tract absent from the public Rubin footprint")
        if len(tract_ids) != len(set(tract_ids)) or len(tract_ids) != item["confirmedRubinTractCount"]:
            raise RuntimeError(f"{item['surveyId']} has inconsistent tract IDs/count")
        full_footprint = bool(item["eligibleAsFullRegistryFootprint"])
        bucket = "fullFootprintSurveyIds" if full_footprint else "conservativeSubsetSurveyIds"
        for tract in tract_ids:
            by_tract[tract][bucket].append(item["surveyId"])
        surveys.append(
            {
                "surveyId": item["surveyId"],
                "status": item["auditStatus"],
                "coverageSemantics": item["coverageSemantics"],
                "eligibleAsFullRegistryFootprint": full_footprint,
                "confirmedRubinTractCount": len(tract_ids),
                "confirmedRubinTractIds": tract_ids,
                "evidence": [
                    {
                        "id": component["id"],
                        "title": component["title"],
                        "sourceUrl": component["sourceUrl"],
                        "sha256": component["sha256"],
                        "bytes": component["bytes"],
                        "mocOrder": component["mocOrder"],
                        "skyFraction": component["skyFraction"],
                        "sourceMocType": component["sourceMocType"],
                    }
                    for component in item["components"]
                ],
                "derivedMoc": {
                    "sha256": item["unionMoc"]["sha256"],
                    "bytes": item["unionMoc"]["bytes"],
                    "mocOrder": item["unionMoc"]["mocOrder"],
                    "skyFraction": item["unionMoc"]["skyFraction"],
                },
                "note": item["note"],
            }
        )

    unresolved = [
        {
            "surveyId": item["surveyId"],
            "status": "unresolved",
            "blocker": item["blocker"],
            "nextAction": item["nextAction"],
            "evidenceUrls": item["evidenceUrls"],
        }
        for item in audit["unresolved"]
    ]
    public_value = {
        "schemaVersion": 1,
        "generatedAt": audit["generatedAt"],
        "product": "Conservative cross-survey overlap evidence",
        "rubinIndex": {
            "surveyId": "rubin-dp2",
            "release": footprint["release"],
            "tractCount": 2191,
            "intersectionOrder": audit["rubinIndex"]["intersectionOrder"],
            "footprintSha256": sha256(args.footprint),
        },
        "semantics": {
            "fullFootprintSurveyIds": sorted(
                item["surveyId"] for item in surveys if item["eligibleAsFullRegistryFootprint"]
            ),
            "conservativeSubsetSurveyIds": sorted(
                item["surveyId"] for item in surveys if not item["eligibleAsFullRegistryFootprint"]
            ),
            "unresolvedSurveyIds": sorted(item["surveyId"] for item in unresolved),
            "warning": "Conservative subset overlap proves released detections or named-program coverage only; it is not a full survey footprint and must not be merged into full-footprint overlap counts.",
        },
        "surveyEvidence": sorted(surveys, key=lambda item: item["surveyId"]),
        "unresolved": sorted(unresolved, key=lambda item: item["surveyId"]),
        "tractFields": ["tract", "fullFootprintSurveyIds", "conservativeSubsetSurveyIds"],
        "tracts": [
            [tract, sorted(values["fullFootprintSurveyIds"]), sorted(values["conservativeSubsetSurveyIds"])]
            for tract, values in sorted(by_tract.items())
        ],
        "cautions": audit["cautions"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(public_value, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": args.output.as_posix(),
                "fullFootprints": public_value["semantics"]["fullFootprintSurveyIds"],
                "conservativeSubsets": public_value["semantics"]["conservativeSubsetSurveyIds"],
                "unresolved": public_value["semantics"]["unresolvedSurveyIds"],
                "tractRows": len(public_value["tracts"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
