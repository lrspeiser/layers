"""Epoch-aware source registration helpers backed by cached Gaia DR3."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
from scipy.spatial import cKDTree

GAIA_MATCH_LIMIT_ARCSEC = 0.80
GAIA_MIN_STARS = 10


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def product_epochs(root: Path, slug: str, band: str) -> dict:
    ps1_mjd = []
    for path in (root / "pipeline/output/panstarrs" / slug / "raw").glob(
        f"*stk.{band}.unconv.fits"
    ):
        with fits.open(path, memmap=False) as hdus:
            header = next(hdu.header for hdu in hdus if hdu.data is not None)
            if header.get("MJD-OBS") is not None:
                ps1_mjd.append(float(header["MJD-OBS"]))
    rubin_days = []
    for path in (root / "pipeline/output/dp2-sparc" / slug / "patches" / band).glob(
        "*.fits"
    ):
        with fits.open(path, memmap=False) as hdus:
            if "PROVENANCE/INPUTS" in hdus:
                rubin_days.extend(
                    int(value) for value in hdus["PROVENANCE/INPUTS"].data["day_obs"]
                )
    if not ps1_mjd or not rubin_days:
        return {}
    day = str(int(np.median(np.unique(rubin_days))))
    return {
        "panstarrsEffectiveJyear": float(
            Time(np.median(ps1_mjd), format="mjd").jyear
        ),
        "panstarrsEpochMethod": "median MJD-OBS across contributing full skycells",
        "rubinEffectiveJyear": float(
            Time(f"{day[:4]}-{day[4:6]}-{day[6:]}", format="iso").jyear
        ),
        "rubinEpochMethod": "median unique day_obs across deep-coadd provenance inputs",
    }


def gaia_epoch_registration(
    rubin_sources: list[dict],
    comparison_sources: list[dict],
    wcs: WCS,
    pixel_scale: float,
    gaia_path: Path,
    epochs: dict,
    root: Path,
) -> dict | None:
    if not gaia_path.is_file() or not epochs or not comparison_sources:
        return None
    with gaia_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows = [
        row
        for row in rows
        if row.get("pmra")
        and row.get("pmdec")
        and row.get("ruwe")
        and float(row["ruwe"]) < 1.4
    ]
    if not rows:
        return None
    ra = np.asarray([float(row["ra"]) for row in rows])
    dec = np.asarray([float(row["dec"]) for row in rows])
    pmra = np.asarray([float(row["pmra"]) for row in rows])
    pmdec = np.asarray([float(row["pmdec"]) for row in rows])
    reference_epoch = np.asarray([float(row["ref_epoch"]) for row in rows])

    def positions(epoch: float) -> np.ndarray:
        propagated_ra = ra + pmra * (epoch - reference_epoch) / (
            3.6e6 * np.cos(np.deg2rad(dec))
        )
        propagated_dec = dec + pmdec * (epoch - reference_epoch) / 3.6e6
        return np.column_stack(wcs.world_to_pixel_values(propagated_ra, propagated_dec))

    rubin_gaia = positions(epochs["rubinEffectiveJyear"])
    comparison_gaia = positions(epochs["panstarrsEffectiveJyear"])
    rubin_tree = cKDTree(rubin_gaia)
    comparison_tree = cKDTree(
        np.asarray([[source["x"], source["y"]] for source in comparison_sources])
    )
    match_limit_pixels = GAIA_MATCH_LIMIT_ARCSEC / pixel_scale
    vectors = []
    fwhm = []
    used_gaia = set()
    for rubin_source in rubin_sources:
        rubin_distance, gaia_index = rubin_tree.query(
            [rubin_source["x"], rubin_source["y"]],
            distance_upper_bound=match_limit_pixels,
        )
        if not np.isfinite(rubin_distance) or int(gaia_index) in used_gaia:
            continue
        comparison_distance, comparison_index = comparison_tree.query(
            comparison_gaia[int(gaia_index)], distance_upper_bound=match_limit_pixels
        )
        if not np.isfinite(comparison_distance):
            continue
        used_gaia.add(int(gaia_index))
        comparison_source = comparison_sources[int(comparison_index)]
        observed = np.asarray(
            [
                rubin_source["x"] - comparison_source["x"],
                rubin_source["y"] - comparison_source["y"],
            ]
        )
        predicted_motion = rubin_gaia[int(gaia_index)] - comparison_gaia[int(gaia_index)]
        vectors.append(observed - predicted_motion)
        fwhm.append(
            [rubin_source["fwhmArcsec"], comparison_source["fwhmArcsec"]]
        )
    if len(vectors) < GAIA_MIN_STARS:
        return None
    array = np.asarray(vectors)
    fwhm_array = np.asarray(fwhm)
    offset_pixels = np.median(array, axis=0)
    residual = np.hypot(
        array[:, 0] - offset_pixels[0], array[:, 1] - offset_pixels[1]
    ) * pixel_scale
    return {
        "method": "Gaia DR3 proper-motion propagation to each survey epoch",
        "matchedSources": int(len(array)),
        "retainedSources": int(len(array)),
        "rejectedOutliers": 0,
        "medianOffsetArcsec": {
            "x": float(offset_pixels[0] * pixel_scale),
            "y": float(offset_pixels[1] * pixel_scale),
        },
        "residualRmsArcsec": float(np.sqrt(np.mean(residual**2))),
        "residualP95Arcsec": float(np.percentile(residual, 95)),
        "rubinMedianFwhmArcsec": float(np.median(fwhm_array[:, 0])),
        "comparisonMedianFwhmArcsec": float(np.median(fwhm_array[:, 1])),
        "gaiaMatchLimitArcsec": GAIA_MATCH_LIMIT_ARCSEC,
        "gaiaRuweLimit": 1.4,
        "minimumGaiaStars": GAIA_MIN_STARS,
        "epochs": epochs,
        "catalog": {
            "release": "Gaia DR3",
            "path": gaia_path.relative_to(root).as_posix(),
            "sha256": sha256(gaia_path),
        },
    }
