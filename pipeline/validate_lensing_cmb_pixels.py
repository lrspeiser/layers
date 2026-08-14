#!/usr/bin/env python3
"""Independent structural/scientific-label validation for lensing/CMB products."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
from astropy.io import fits


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public/data/layers/lensing-cmb/manifest.json"
DETAILED = ROOT / "pipeline/results/lensing-cmb-pixels/manifest.json"
VALIDATION = ROOT / "pipeline/results/lensing-cmb-pixels/validation.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def check(name: str, passed: bool, evidence) -> dict:
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def main() -> None:
    public = json.loads(PUBLIC.read_text())
    detailed = json.loads(DETAILED.read_text())
    products = public["products"]
    detailed_products = {(p["regionId"], p["surveyId"]): p for p in detailed["products"]}
    checks: list[dict] = []

    required = {
        "regionId", "tract", "surveyId", "surveyName", "family", "release", "productType",
        "status", "scienceReady", "displayReady", "comparisonReady", "bandOrObservable", "unit",
        "previewPath", "alignedRubinPreviewPath", "coveragePreviewPath", "overlayPreviewPath",
        "provenanceUrls", "checksum", "blockers",
    }
    checks.append(check("normalized product contract", all(required <= set(p) for p in products), len(products)))
    checks.append(check("exact aggregate counts", public["summary"]["availableProductCount"] == len(products)
                        and public["summary"]["availableProductCount"] + public["summary"]["noneCount"]
                        + public["summary"]["errorCount"] == 200, public["summary"]))
    by_survey = {s: sum(p["surveyId"] == s for p in products) for s in {p["surveyId"] for p in products}}
    expected_available = {s: v["availableCount"] for s, v in public["summary"]["bySurvey"].items() if v["availableCount"]}
    checks.append(check("survey aggregates match products", by_survey == expected_available, by_survey))
    checks.append(check("all four released surveys represented", set(by_survey) == {
        "act-dr6", "des-y3-lensing", "planck-2018", "spt-3g",
    }, by_survey))
    audit = public["availabilityAudit"]
    audit_counts = {s: sum(a["status"] == s for a in audit) for s in ("available", "none", "error")}
    checks.append(check("complete 50 by 4 availability audit", len(audit) == 200 and audit_counts == {
        "available": public["summary"]["availableProductCount"],
        "none": public["summary"]["noneCount"],
        "error": public["summary"]["errorCount"],
    }, audit_counts))
    checks.append(check("no cross-field science claim", all(not p["comparisonReady"] and any("no cross-field subtraction" in b for b in p["blockers"]) for p in products), len(products)))
    checks.append(check("masks separate from science", public["method"]["maskSemantics"].startswith("COVERAGE is a separate"), public["method"]["maskSemantics"]))
    checks.append(check("KiDS and HSC remain unresolved", {x["surveyId"] for x in public["unresolved"]} == {"kids-1000-lensing", "hsc-lensing"}, public["unresolved"]))

    file_errors = []
    for p in products:
        fits_path = ROOT / detailed_products[(p["regionId"], p["surveyId"])]["localScienceProduct"]
        if not fits_path.exists() or sha256(fits_path) != p["checksum"]:
            file_errors.append(f"{p['regionId']}:{p['surveyId']}:fits")
            continue
        for key in ("previewPath", "coveragePreviewPath", "overlayPreviewPath", "alignedRubinPreviewPath"):
            target = ROOT / "public" / p[key].lstrip("/")
            if not target.exists():
                file_errors.append(f"{p['regionId']}:{p['surveyId']}:{key}")
        try:
            with fits.open(fits_path, checksum=True) as hdul:
                names = [x.name for x in hdul]
                science = np.asarray(hdul["SCIENCE"].data)
                mask = np.asarray(hdul["COVERAGE"].data)
                if names != ["PRIMARY", "SCIENCE", "COVERAGE"]:
                    file_errors.append(f"{p['regionId']}:{p['surveyId']}:planes")
                if science.shape != (64, 64) or mask.shape != science.shape:
                    file_errors.append(f"{p['regionId']}:{p['surveyId']}:shape")
                if hdul["SCIENCE"].header.get("BUNIT") != p["unit"]:
                    file_errors.append(f"{p['regionId']}:{p['surveyId']}:unit")
                if not np.isfinite(science[mask > 0]).any():
                    file_errors.append(f"{p['regionId']}:{p['surveyId']}:finite")
                if not set(np.unique(mask)).issubset({0, 1}):
                    file_errors.append(f"{p['regionId']}:{p['surveyId']}:mask")
        except Exception as exc:
            file_errors.append(f"{p['regionId']}:{p['surveyId']}:{exc}")
    checks.append(check("FITS, WCS display artifacts and checksums", not file_errors, file_errors[:20]))

    public_text = PUBLIC.read_text()
    leak = re.search(r"(?:[A-Za-z]:\\|pipeline/results|Authorization|Bearer\s|token)", public_text, re.I)
    checks.append(check("redacted public manifest", leak is None, None if leak is None else leak.group(0)))
    source_ok = all(s["bytes"] > 0 and len(s["sha256"]) == 64 and s["publisherUrl"].startswith("https://") for s in public["sources"].values())
    checks.append(check("publisher provenance and source checksums", source_ok, list(public["sources"])))

    result = {
        "schemaVersion": "layers-lensing-cmb-validation-v1",
        "passed": all(x["passed"] for x in checks), "checkCount": len(checks), "checks": checks,
    }
    VALIDATION.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
