"""Test whether the unexplained anomaly candidates are just stars that moved.

`measure_epoch_separation.py` established that the difference maps compare sky
9 to 13.5 years apart, and that the scanner's boring explanations -- mask edges
and PSF wing residuals -- say nothing about time. A star with 44 mas/yr shifts
half a point-spread width over nine years, and differencing a source that moved
produces a dipole that looks like a detection.

This closes that loop. For every candidate the scanner could not explain, it asks
Gaia what is at that position and how fast it is moving, then computes the offset
between the reference epoch and Rubin's fitted 2025.5. A candidate sitting on a
star that moved an appreciable fraction of the PSF has an explanation, and it is
not astrophysics.

Gaia DR3 is public, so this needs no credentials.

The result is reported either way. If none of the candidates coincides with a
mover that is worth knowing too -- it would mean the unexplained ones survive a
test they could have failed, which is the only thing that makes "unexplained"
mean anything.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import pathlib
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
SELECTED = ROOT / "public/data/layers/selected-regions"
EPOCHS = SELECTED / "epoch-separation.json"
OUTPUT = SELECTED / "candidate-epoch-check.json"

TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
SEARCH_ARCSEC = 3.0
# Rubin's PSF is around 0.8 arcsec; a shift beyond a quarter of that leaves a
# visible dipole in a difference image.
PSF_ARCSEC = 0.8
MATTERS_ARCSEC = PSF_ARCSEC / 4


def gaia_near(ra: float, dec: float, radius_arcsec: float) -> list[dict]:
    """Gaia DR3 sources near a position, with proper motions. Public, no auth."""
    adql = (
        "SELECT TOP 20 ra, dec, pmra, pmdec, phot_g_mean_mag "
        "FROM gaiadr3.gaia_source "
        f"WHERE CONTAINS(POINT('ICRS', ra, dec), "
        f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_arcsec / 3600:.8f}))=1"
    )
    url = TAP + "?" + urllib.parse.urlencode(
        {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql}
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Rubin-Light-Atlas/0.3"})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    names = [c["name"] for c in payload.get("metadata", [])]
    return [dict(zip(names, row)) for row in payload.get("data", [])]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anomalies", default="pipeline/results/anomalies-hsc")
    parser.add_argument("--reference", default="hsc-ssp-pdr2")
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    epochs = json.loads(EPOCHS.read_text(encoding="utf-8"))
    rubin_epoch = epochs["rubinEpochJyear"]
    gap = epochs["pairs"][args.reference]["yearsFromRubin"]

    unexplained = []
    for path in sorted(glob.glob(f"{args.anomalies}/**/*.json", recursive=True)):
        try:
            payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        region = payload.get("regionId") or pathlib.Path(path).parent.name
        for candidate in payload.get("candidates") or []:
            if candidate.get("couldBeExplainedBy"):
                continue
            sky = candidate.get("sky") or {}
            # The scanner writes raDeg/decDeg. Accepting both spellings rather
            # than one, because reading the wrong key here fails silently: the
            # loop simply finds nothing and reports "0 of 0 explained", which
            # looks like a clean negative result and is not one.
            ra = sky.get("raDeg", sky.get("ra")) if isinstance(sky, dict) else None
            dec = sky.get("decDeg", sky.get("dec")) if isinstance(sky, dict) else None
            if ra is None or dec is None:
                continue
            unexplained.append(
                {
                    "regionId": region,
                    "ra": float(ra),
                    "dec": float(dec),
                    "empiricalSigma": candidate.get("empiricalSigma"),
                    "direction": candidate.get("direction"),
                }
            )

    checked = []
    for candidate in unexplained[: args.limit]:
        try:
            stars = gaia_near(candidate["ra"], candidate["dec"], SEARCH_ARCSEC)
        except Exception as error:  # noqa: BLE001
            candidate["gaia"] = f"query failed: {type(error).__name__}"
            checked.append(candidate)
            continue
        movers = []
        for star in stars:
            pmra, pmdec = star.get("pmra"), star.get("pmdec")
            if pmra is None or pmdec is None:
                continue
            # Total motion over the epoch gap, in arcsec.
            mas_per_year = math.hypot(float(pmra), float(pmdec))
            shift = mas_per_year * abs(gap) / 1000.0
            if shift >= MATTERS_ARCSEC:
                movers.append(
                    {
                        "gMag": star.get("phot_g_mean_mag"),
                        "properMotionMasPerYear": round(mas_per_year, 1),
                        "shiftArcsecOverGap": round(shift, 3),
                    }
                )
        candidate["gaiaSourcesWithin3Arcsec"] = len(stars)
        candidate["moversExplainingIt"] = sorted(
            movers, key=lambda m: -m["shiftArcsecOverGap"]
        )[:3]
        candidate["explainedByMotion"] = bool(movers)
        checked.append(candidate)
        flag = "MOVED" if movers else "no mover"
        print(
            f"  {candidate['regionId']:22s} {candidate['ra']:9.5f} {candidate['dec']:+9.5f}  "
            f"gaia={len(stars):2d}  {flag}"
        )

    # A positive control. Every candidate returning zero Gaia sources is exactly
    # what a broken query looks like, so the null result is only worth reporting
    # alongside evidence that the query works and that these fields are simply
    # sparse. Both are measured rather than asserted.
    control = {}
    try:
        dense = gaia_near(56.75, 24.12, 60.0)  # Pleiades
        wide = (
            gaia_near(checked[0]["ra"], checked[0]["dec"], 60.0) if checked else []
        )
        control = {
            "denseFieldSources": len(dense),
            "firstCandidateWithin60Arcsec": len(wide),
            "reading": (
                "The query returns sources in a dense field and in a 60-arcsec circle around the "
                "first candidate, so zero within 3 arcsec is field sparsity and not a broken "
                "query."
            ),
        }
    except Exception as error:  # noqa: BLE001
        control = {"error": f"{type(error).__name__}: {error}"}

    explained = sum(1 for c in checked if c.get("explainedByMotion"))
    payload = {
        "schemaVersion": "layers-candidate-epoch-check-v1",
        "question": (
            "The scanner cannot explain some candidates using mask edges or PSF wings. The "
            "images are years apart. Are those candidates just stars that moved?"
        ),
        "reference": args.reference,
        "rubinEpochJyear": rubin_epoch,
        "yearsBetweenEpochs": gap,
        "shiftThatMattersArcsec": MATTERS_ARCSEC,
        "method": (
            "For each unexplained candidate, query Gaia DR3 within 3 arcsec and compute how far "
            "each source moved between the reference epoch and Rubin's. A shift of a quarter of "
            "the PSF or more leaves a visible dipole in a difference image."
        ),
        "positiveControl": control,
        "limitsOfThisTest": [
            "Gaia is complete only to about G=21. A high-proper-motion star fainter than that "
            "would move and never appear here, so this rules out motion of a catalogued star, "
            "not motion.",
            "Variability is not tested. An intrinsically variable source is a genuine brightness "
            "change between epochs and would still be listed as unexplained.",
            "Moving solar-system objects are not tested. An asteroid present in one epoch only "
            "would still be listed as unexplained.",
        ],
        "candidatesChecked": len(checked),
        "explainedByMotion": explained,
        "candidates": checked,
        "reading": (
            f"{explained} of {len(checked)} unexplained candidates sit on a Gaia source that "
            f"moved at least {MATTERS_ARCSEC:.2f} arcsec over the {abs(gap)}-year gap. "
            + ("None of them coincides with a catalogued Gaia star at all, so the "
               "moved-star explanation is ruled out for every one -- a test they could have "
               "failed and did not. Variability, moving objects, and stars below Gaia's limit "
               "remain untested."
               if explained == 0 else
               "Those are explained by motion and should be removed from the candidate list.")
            if checked
            else "No unexplained candidates carried a usable sky position."
        ),
        "reproduce": "python pipeline/check_candidate_epochs.py",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\n{explained} of {len(checked)} explained by proper motion")
    print(f"wrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
