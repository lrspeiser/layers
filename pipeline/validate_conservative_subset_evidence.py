#!/usr/bin/env python3
"""Validate public conservative-subset coverage semantics and provenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED_FULL = {"wallaby-pdr2": 0}
EXPECTED_SUBSETS = {
    "alfalfa-alpha100": 448,
    "resolved-hi-archives": 8,
    "alma": 7,
}
EXPECTED_UNRESOLVED = {
    "act-dr6",
    "des-y3-lensing",
    "hsc-lensing",
    "hsc-ssp-pdr3",
    "kids-1000-lensing",
    "sdss-dr19",
    "spt-3g",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("public/data/coverage/conservative-subset-evidence.json"),
    )
    args = parser.parse_args()
    value = json.loads(args.path.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == 1
    assert value["rubinIndex"]["tractCount"] == 2191
    assert set(value["semantics"]["fullFootprintSurveyIds"]) == set(EXPECTED_FULL)
    assert set(value["semantics"]["conservativeSubsetSurveyIds"]) == set(EXPECTED_SUBSETS)
    assert set(value["semantics"]["unresolvedSurveyIds"]) == EXPECTED_UNRESOLVED
    assert "must not be merged" in value["semantics"]["warning"]

    evidence = {item["surveyId"]: item for item in value["surveyEvidence"]}
    assert set(evidence) == set(EXPECTED_FULL) | set(EXPECTED_SUBSETS)
    for survey_id, count in EXPECTED_FULL.items():
        item = evidence[survey_id]
        assert item["eligibleAsFullRegistryFootprint"] is True
        assert item["status"] == "resolved-full"
        assert item["confirmedRubinTractCount"] == count == len(item["confirmedRubinTractIds"])
    for survey_id, count in EXPECTED_SUBSETS.items():
        item = evidence[survey_id]
        assert item["eligibleAsFullRegistryFootprint"] is False
        assert item["status"] == "resolved-conservative-subset"
        assert item["confirmedRubinTractCount"] == count == len(item["confirmedRubinTractIds"])
        assert len(set(item["confirmedRubinTractIds"])) == count
    for item in evidence.values():
        assert item["evidence"]
        for source in item["evidence"]:
            assert source["sourceUrl"].startswith("https://")
            assert len(source["sha256"]) == 64
            assert source["bytes"] > 0
        assert len(item["derivedMoc"]["sha256"]) == 64

    unresolved = {item["surveyId"]: item for item in value["unresolved"]}
    assert set(unresolved) == EXPECTED_UNRESOLVED
    for item in unresolved.values():
        assert item["status"] == "unresolved"
        assert item["blocker"] and item["nextAction"] and item["evidenceUrls"]

    rows = value["tracts"]
    assert len({row[0] for row in rows}) == len(rows)
    full_from_rows = {survey_id for _, full, _ in rows for survey_id in full}
    subset_from_rows = {survey_id for _, _, subset in rows for survey_id in subset}
    # WALLABY has a valid full footprint with zero Rubin intersections, so it
    # correctly has no tract row. Subset IDs must all occur in the tract rows.
    assert full_from_rows == set()
    assert subset_from_rows == set(EXPECTED_SUBSETS)
    print(
        f"Validated {len(evidence)} evidence layers, {len(rows)} tract rows, "
        f"and {len(unresolved)} explicit blockers"
    )


if __name__ == "__main__":
    main()
