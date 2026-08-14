#!/usr/bin/env python3
"""Build the survey-neutral Layers coverage and access registry.

This script is deliberately offline. It records official discovery services and
provenance, but it does not query or download survey data.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "public" / "data" / "survey-registry.json"

SEMANTICS: dict[str, tuple[str, str, list[str]]] = {
    "rubin-only-detection": ("Rubin-only signal", "Does Rubin recover a supported source or diffuse structure absent from the reference?", ["common WCS", "PSF or beam matching", "depth model", "mask propagation", "background matching"]),
    "reference-only-detection": ("Reference-only signal", "Is a reference detection absent from Rubin after accounting for bandpass, epoch, depth, and masks?", ["common WCS", "bandpass model", "depth model", "epoch check", "mask propagation"]),
    "flux-residual": ("Matched flux residual", "Where does calibrated Rubin surface brightness differ beyond combined uncertainty?", ["photometric calibration", "PSF matching", "filter transform", "sky matching", "variance propagation"]),
    "color-excess": ("Spectral-energy-distribution excess", "Does Rubin expose an unexpected color, dust component, or stellar population?", ["matched apertures", "Galactic extinction", "zeropoint system", "epoch consistency", "upper limits"]),
    "morphology-change": ("Morphology or deblending change", "Does Rubin reveal faint structure or resolve a previously blended source?", ["common segmentation", "PSF model", "surface-brightness completeness", "artifact masks"]),
    "astrometric-motion": ("Astrometric motion", "Does the Rubin epoch extend a significant proper-motion or parallax solution?", ["reference-frame transform", "epoch propagation", "covariance propagation", "blend rejection"]),
    "temporal-change": ("Temporal change", "Did the source vary, appear, disappear, or move between the reference and Rubin epochs?", ["epoch metadata", "forced photometry", "bandpass model", "non-detection limits", "artifact rejection"]),
    "counterpart-association": ("Counterpart association", "Does deeper Rubin imaging identify or revise the optical counterpart of a non-optical source?", ["positional uncertainty", "chance-alignment probability", "source morphology", "multi-band priors"]),
    "gas-star-offset": ("Gas-to-starlight offset", "Is gas displaced from, or unsupported by, Rubin-detected starlight?", ["beam matching", "velocity association", "column-density sensitivity", "inclination model"]),
    "mass-light-offset": ("Mass-to-light offset", "Does Rubin change visible-light structure relative to lensing, SZ, or CMB-inferred mass?", ["redshift selection", "calibration", "PSF systematics", "mass-model uncertainty", "foreground subtraction"]),
    "redshift-consistency": ("Redshift consistency", "Do spectra confirm that Rubin-resolved objects or structures are physically associated?", ["secure-redshift flags", "target identity", "aperture effects", "selection function"]),
    "foreground-rejection": ("Foreground rejection", "Can astrometry distinguish foreground stars from extragalactic sources or diffuse light?", ["astrometric significance", "epoch propagation", "quality flags", "crowding model"]),
}

FAMILIES: dict[str, dict[str, Any]] = {
    "optical-baseline": {
        "roles": ["historical optical baseline", "source completeness", "morphology and diffuse-light comparison"],
        "adds": ["fainter six-band detections", "new low-surface-brightness structure", "longer temporal baseline"],
        "render": ["swipe", "rgb", "single-band", "difference", "markers", "coverage"],
        "diff": ["rubin-only-detection", "reference-only-detection", "flux-residual", "morphology-change", "color-excess"],
    },
    "uv-ir": {
        "roles": ["spectral-energy-distribution context", "stellar mass, star formation, or dust", "non-optical counterpart identification"],
        "adds": ["deep resolved optical counterparts", "six-band colors", "faint visible morphology"],
        "render": ["single-band", "rgb", "contours", "markers", "coverage"],
        "diff": ["color-excess", "counterpart-association", "rubin-only-detection", "reference-only-detection"],
    },
    "astrometry": {
        "roles": ["foreground rejection", "astrometric reference frame", "motion and distance"],
        "adds": ["fainter six-band sources", "longer astrometric time baseline", "deeper crowded-field context"],
        "render": ["markers", "vectors", "light-curve", "spectrum", "coverage"],
        "diff": ["foreground-rejection", "astrometric-motion", "temporal-change", "color-excess"],
    },
    "time-domain": {
        "roles": ["historical variability", "transient precovery", "moving-object history"],
        "adds": ["deeper variability measurements", "fainter hosts", "new temporal baseline"],
        "render": ["swipe", "difference", "markers", "light-curve", "coverage"],
        "diff": ["temporal-change", "astrometric-motion", "rubin-only-detection", "reference-only-detection"],
    },
    "spectroscopy": {
        "roles": ["secure redshifts", "physical association", "stellar populations and emission-line diagnostics"],
        "adds": ["deeper imaging around spectroscopic targets", "deblended counterparts", "faint environment"],
        "render": ["markers", "spectrum", "coverage"],
        "diff": ["redshift-consistency", "counterpart-association", "color-excess"],
    },
    "high-energy": {
        "roles": ["AGN and compact objects", "hot gas and clusters", "X-ray counterpart identification"],
        "adds": ["fainter optical counterpart candidates", "host morphology and colors", "better association probabilities"],
        "render": ["single-band", "contours", "markers", "coverage"],
        "diff": ["counterpart-association", "rubin-only-detection", "reference-only-detection", "color-excess"],
    },
    "radio": {
        "roles": ["radio AGN and jets", "radio star formation", "optical host identification"],
        "adds": ["faint optical hosts", "resolved environment", "improved radio-component association"],
        "render": ["single-band", "contours", "markers", "difference", "coverage"],
        "diff": ["counterpart-association", "rubin-only-detection", "reference-only-detection", "temporal-change"],
    },
    "neutral-gas": {
        "roles": ["neutral-gas mass", "gas kinematics", "gas-star offsets and environmental stripping"],
        "adds": ["faint stellar counterparts and outskirts", "stellar morphology against H I", "updated baryonic context"],
        "render": ["contours", "moment-map", "channel-map", "vectors", "markers", "spectrum", "coverage"],
        "diff": ["gas-star-offset", "counterpart-association", "mass-light-offset", "redshift-consistency"],
    },
    "high-resolution": {
        "roles": ["high-resolution morphology", "blend truth", "resolved gas, dust, stars, or spectra"],
        "adds": ["wide-field faint context", "uniform six-band surroundings", "new temporal baseline"],
        "render": ["swipe", "rgb", "single-band", "contours", "markers", "spectrum", "channel-map", "coverage"],
        "diff": ["morphology-change", "counterpart-association", "color-excess", "rubin-only-detection", "reference-only-detection"],
    },
    "lensing": {
        "roles": ["weak-lensing shear", "mass maps", "mass-light comparison"],
        "adds": ["deeper lens-source photometry", "new faint foreground light", "independent visible-mass structure"],
        "render": ["vectors", "contours", "markers", "coverage"],
        "diff": ["mass-light-offset", "counterpart-association", "morphology-change"],
    },
    "cmb-large-scale-structure": {
        "roles": ["CMB lensing or thermal SZ", "large-scale mass", "cluster and foreground context"],
        "adds": ["optical members and counterparts", "photometric redshifts", "visible structure below the microwave beam"],
        "render": ["contours", "markers", "single-band", "coverage"],
        "diff": ["mass-light-offset", "counterpart-association", "foreground-rejection"],
    },
}


def ep(label: str, protocol: str, url: str, purpose: str, auth: str = "none") -> dict[str, str]:
    return {"label": label, "protocol": protocol, "url": url, "authentication": auth, "purpose": purpose}


def cov(
    kind: str,
    area: float | None,
    geometry: str,
    status: str,
    notes: str,
    *,
    endpoint: str | None = None,
    moc: str | None = None,
    fallback: str = "Mark coverage unknown until official footprints are materialized.",
) -> dict[str, Any]:
    return {
        "type": kind,
        "approximateAreaSqDeg": area,
        "geometrySource": geometry,
        "footprintEndpoint": endpoint,
        "mocId": moc,
        "coverageEndpoint": endpoint,
        "machineReadableStatus": status,
        "fallback": fallback,
        "notes": notes,
    }


def add(
    id: str, name: str, short: str, org: str, release: str, family: str, priority: int, access: str,
    primitives: list[str], waves: list[str], bands: list[str], coverage: dict[str, Any],
    endpoints: list[dict[str, str]], provenance: list[str], caveat: str,
    cache_strategy: str = "cutout-on-demand", cache_products: list[str] | None = None,
    max_arcmin: int | None = 30,
) -> dict[str, Any]:
    family_data = FAMILIES[family]
    return {
        "id": id, "name": name, "shortName": short, "organization": org, "release": release,
        "family": family, "priority": priority, "accessStatus": access,
        "layerPrimitives": primitives, "wavelengthDomains": waves, "bandsOrProducts": bands,
        "scienceRoles": family_data["roles"], "rubinAdds": family_data["adds"], "coverage": coverage,
        "endpoints": endpoints,
        "cachePolicy": {
            "strategy": cache_strategy, "downloadWholeArchive": False,
            "cacheProducts": cache_products or ["bounded science product", "quality and variance metadata", "provenance manifest"],
            "revalidateDays": 30 if priority == 1 else 180, "maximumCutoutArcmin": max_arcmin,
        },
        "uiRenderModes": family_data["render"],
        "differenceSemantics": [
            {"id": key, "label": SEMANTICS[key][0], "rubinQuestion": SEMANTICS[key][1], "requiredControls": SEMANTICS[key][2]}
            for key in family_data["diff"]
        ],
        "provenanceUrls": provenance,
        "caveats": [caveat, "Coverage is not a comparability claim; product masks, depth, resolution, and selection must be evaluated per region."],
    }


S: list[dict[str, Any]] = []

# Historical optical baselines.
S += [
    add("des-dr2", "Dark Energy Survey Data Release 2", "DES DR2", "NOIRLab / DES", "DR2", "optical-baseline", 1, "public",
        ["raster", "catalog"], ["optical", "near-infrared"], ["g", "r", "i", "z", "Y", "coadds", "catalogs"],
        cov("wide-area", 5000, "sia-query", "queryable", "Exact image records can be intersected by SIA.", endpoint="https://datalab.noirlab.edu/sia/des_dr2",
            fallback="Use the published DES footprint polygon and label per-tract status approximate."),
        [ep("NOIRLab SIA", "sia2", "https://datalab.noirlab.edu/sia/des_dr2", "Image discovery"), ep("NOIRLab TAP", "tap", "https://datalab.noirlab.edu/tap", "Catalog query")],
        ["https://datalab.noirlab.edu/data/des.html", "https://www.darkenergysurvey.org/the-des-project/data-access/"], "Filter transfer and varying coadd PSF are required controls."),
    add("legacy-surveys-dr10", "DESI Legacy Imaging Surveys", "Legacy DR10", "NOIRLab / DESI Legacy Surveys", "DR10", "optical-baseline", 1, "public",
        ["raster", "catalog"], ["optical", "near-infrared"], ["g", "r", "i", "z", "WISE forced photometry", "model", "residual", "maskbits"],
        cov("wide-area", 20000, "hips", "queryable", "HiPS/brick metadata supports exact tract intersection.", endpoint="https://www.legacysurvey.org/viewer/",
            moc="CDS/P/DESI-Legacy-Surveys/DR10/color", fallback="Intersect published DR10 brick bounds."),
        [ep("FITS cutout", "http", "https://www.legacysurvey.org/viewer/cutout.fits", "Calibrated cutout"), ep("DR10 files", "bulk-download", "https://portal.nersc.gov/cfs/cosmo/data/legacysurvey/dr10/", "Bricks and Tractor catalogs")],
        ["https://www.legacysurvey.org/dr10/description/", "https://www.legacysurvey.org/dr10/files/"], "DR10 combines multiple instruments with spatially varying response."),
    add("panstarrs-dr2", "Pan-STARRS1 3pi Survey", "Pan-STARRS DR2", "STScI / Pan-STARRS", "DR2", "optical-baseline", 1, "public",
        ["raster", "catalog", "time-series"], ["optical", "near-infrared"], ["g", "r", "i", "z", "y", "stack", "warp", "mean object", "detections"],
        cov("wide-area", 30000, "hips", "queryable", "PS1 stack HiPS and filename service provide tract-level coverage.", endpoint="https://ps1images.stsci.edu/cgi-bin/ps1filenames.py",
            moc="CDS/P/PanSTARRS/DR1/color-z-zg-g", fallback="Use declination > -30 degrees only as approximate coverage."),
        [ep("Filename service", "http", "https://ps1images.stsci.edu/cgi-bin/ps1filenames.py", "Discover images"), ep("FITS cutout", "http", "https://ps1images.stsci.edu/cgi-bin/fitscut.cgi", "Image cutouts"), ep("MAST catalogs", "mast-api", "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs", "Catalog query")],
        ["https://outerspace.stsci.edu/spaces/PANSTARRS/pages/298812201/Pan-STARRS1+data+archive+home+page", "https://outerspace.stsci.edu/spaces/PANSTARRS/pages/298812251/PS1+Image+Cutout+Service"], "Stacks and warp time-series have different selection functions."),
    add("hsc-ssp-pdr2", "Hyper Suprime-Cam Subaru Strategic Program", "HSC PDR2", "NAOJ / HSC-SSP", "PDR2", "optical-baseline", 1, "public",
        ["raster", "catalog"], ["optical", "near-infrared"], ["g", "r", "i", "z", "y", "narrow bands", "photo-z", "CAMIRA clusters"],
        cov("regional", None, "hips", "exact", "Public CDS HiPS MOCs describe the released PDR2 wide and deep image pixels by band.",
            endpoint="https://alasky.cds.unistra.fr/HSC/DR2/CDS_P_HSC_DR2_wide_i", moc="CDS/P/HSC/DR2/*/*",
            fallback="Query the release-matched PDR2 HiPS MOCs; never substitute the PDR3 tract grid or rectangular field bounds."),
        [ep("CDS PDR2 HiPS", "hips", "https://alasky.cds.unistra.fr/HSC/DR2/CDS_P_HSC_DR2_wide_i", "Public image tiles and exact MOC"), ep("HSC data search", "archive-api", "https://hsc-release.mtk.nao.ac.jp/datasearch/", "Catalog and native image products", "account")],
        ["https://hsc-release.mtk.nao.ac.jp/doc/index.php/sample-page/pdr2/", "https://alasky.cds.unistra.fr/MocServer/query?ID=CDS%2FP%2FHSC%2FDR2%2F%2A&get=record&fmt=json"], "The public HiPS is PDR2 imaging support; it is not PDR3 imaging and does not imply weak-lensing shape coverage."),
]

# UV, infrared, astrometry, and time domain.
S += [
    add("galex-gr6-7", "Galaxy Evolution Explorer", "GALEX GR6/7", "NASA / MAST", "GR6/GR7", "uv-ir", 1, "public",
        ["raster", "catalog"], ["ultraviolet"], ["FUV", "NUV", "intensity", "exposure", "background"],
        cov("wide-area", 26000, "observation-footprints", "exact", "MAST observation polygons give exact visit coverage.", endpoint="https://mast.stsci.edu/api/v0/invoke",
            moc="CDS/P/GALEXGR6/AIS/color", fallback="Query MAST; a GALEX all-sky nominal footprint is insufficient."),
        [ep("MAST API", "mast-api", "https://mast.stsci.edu/api/v0/invoke", "Observation and product discovery")],
        ["https://galex.stsci.edu/GR6/", "https://archive.stsci.edu/missions-and-data/galex"], "FUV and NUV coverage and depth differ by visit."),
    add("unwise", "unWISE Coadds and Catalog", "unWISE", "unWISE / NASA IRSA", "unWISE and CatWISE2020", "uv-ir", 1, "public",
        ["raster", "catalog", "time-series"], ["mid-infrared"], ["W1", "W2", "time-resolved coadds", "CatWISE"],
        cov("all-sky", 41253, "hips", "exact", "All-sky; tile geometry is machine-readable.", endpoint="https://unwise.me/cutout_fits",
            moc="CDS/P/unWISE/color-W2-W1W2-W1", fallback="Treat as all-sky, then verify cutout validity."),
        [ep("unWISE cutouts", "http", "https://unwise.me/cutout_fits", "Coadd cutouts"), ep("IRSA TAP", "tap", "https://irsa.ipac.caltech.edu/TAP", "CatWISE query")],
        ["https://unwise.me/", "https://catalog.unwise.me/"], "WISE resolution and confusion are much coarser than Rubin."),
    add("2mass", "Two Micron All Sky Survey", "2MASS", "IPAC / NASA IRSA", "All-Sky Release", "uv-ir", 1, "public",
        ["raster", "catalog"], ["near-infrared"], ["J", "H", "Ks", "PSC", "XSC"],
        cov("all-sky", 41253, "sia-query", "exact", "All-sky atlas; SIA returns exact image records.", endpoint="https://irsa.ipac.caltech.edu/SIA",
            moc="CDS/P/2MASS/color", fallback="Treat as all-sky, then verify returned atlas coverage."),
        [ep("IRSA SIA", "sia2", "https://irsa.ipac.caltech.edu/SIA", "Atlas image discovery"), ep("IRSA TAP", "tap", "https://irsa.ipac.caltech.edu/TAP", "Catalog query")],
        ["https://irsa.ipac.caltech.edu/Missions/2mass.html", "https://irsa.ipac.caltech.edu/applications/2MASS/IM/"], "2MASS is shallow and has very different extended-source processing."),
    add("gaia-dr3", "Gaia", "Gaia DR3", "ESA / Gaia DPAC", "DR3", "astrometry", 1, "public",
        ["catalog", "time-series", "spectrum"], ["optical", "non-photonic"], ["G", "BP", "RP", "parallax", "proper motion", "BP/RP spectra", "radial velocity"],
        cov("all-sky", 41253, "catalog-derived", "catalog-derived", "All-sky source catalog; query and epoch-propagate sources by Rubin tract.", endpoint="https://gea.esac.esa.int/tap-server/tap",
            fallback="Treat catalog coverage as all-sky but preserve source-level scanning-law completeness."),
        [ep("Gaia TAP", "tap", "https://gea.esac.esa.int/tap-server/tap", "ADQL catalog query")],
        ["https://www.cosmos.esa.int/web/gaia/dr3", "https://gea.esac.esa.int/archive/"], "Full covariance and quality flags must be propagated to the Rubin epoch.", "metadata-local", ["tract source subset", "covariance", "quality flags"], None),
    add("ztf-dr", "Zwicky Transient Facility Public Data", "ZTF", "Caltech / NASA IRSA", "Public data releases", "time-domain", 1, "public",
        ["raster", "catalog", "time-series"], ["optical"], ["g", "r", "i", "science", "reference", "difference", "light curves"],
        cov("wide-area", 30000, "observation-footprints", "queryable", "IRSA product records provide exposure-level coverage.", endpoint="https://irsa.ipac.caltech.edu/ibe/search/ztf/products",
            fallback="Use the public ZTF field grid and label per-epoch completeness unknown."),
        [ep("IRSA IBE", "irsa-api", "https://irsa.ipac.caltech.edu/ibe/search/ztf/products", "Image product discovery"), ep("IRSA TAP", "tap", "https://irsa.ipac.caltech.edu/TAP", "Catalog query")],
        ["https://irsa.ipac.caltech.edu/Missions/ztf.html", "https://www.ztf.caltech.edu/"], "Per-epoch artifacts and varying limiting magnitude require quality cuts.", "pin-cache"),
]

# Spectroscopy, high energy, and radio continuum.
S += [
    add("desi-dr1", "Dark Energy Spectroscopic Instrument", "DESI DR1", "DESI Collaboration / DOE", "DR1", "spectroscopy", 1, "public",
        ["catalog", "spectrum"], ["optical"], ["spectra", "redshifts", "targeting", "emission lines", "value-added catalogs"],
        cov("wide-area", 14000, "catalog-derived", "catalog-derived", "Build exact tract coverage from public tile geometry and target coordinates.", endpoint="https://data.desi.lbl.gov/public/dr1/",
            fallback="Use published footprint masks; never assume every imaged source has spectroscopy."),
        [ep("DESI public files", "bulk-download", "https://data.desi.lbl.gov/public/dr1/", "Spectra and catalogs")],
        ["https://data.desi.lbl.gov/doc/releases/", "https://data.desi.lbl.gov/public/dr1/"], "Targets are a selected, incomplete subset of Rubin sources.", "pin-cache", ["matched spectrum", "redshift and quality", "target metadata"], None),
    add("sdss-dr19", "Sloan Digital Sky Survey", "SDSS DR19", "SDSS Collaboration", "DR19", "spectroscopy", 1, "public",
        ["raster", "catalog", "spectrum", "cube"], ["optical", "near-infrared"], ["ugriz", "optical spectra", "APOGEE", "MaNGA cubes", "redshifts"],
        cov("wide-area", 14500, "observation-footprints", "queryable", "Imaging and spectroscopy have distinct machine-readable product footprints.", endpoint="https://skyserver.sdss.org/dr19/",
            fallback="Materialize SDSS field, plate, and IFU footprints separately."),
        [ep("SkyServer", "archive-api", "https://skyserver.sdss.org/dr19/en/tools/search/sql.aspx", "Catalog query"), ep("Science Archive", "http", "https://data.sdss.org/sas/dr19/", "Images, spectra, and cubes")],
        ["https://www.sdss.org/dr19/", "https://skyserver.sdss.org/dr19/"], "Fiber aperture, program selection, and product footprints differ."),
    add("erosita-erass1", "eROSITA All-Sky Survey", "eROSITA eRASS1", "eROSITA-DE / MPE", "eRASS1", "high-energy", 1, "mixed",
        ["raster", "catalog"], ["x-ray"], ["0.2-0.6 keV", "0.6-2.3 keV", "2.3-5 keV", "catalog", "exposure", "background"],
        cov("regional", 20626, "hips", "queryable", "Public German-sky hemisphere; catalog service and HiPS support intersection.", endpoint="https://erosita.mpe.mpg.de/dr1/erodat/catalogue/SCS/",
            fallback="Use the published eRASS1 western-hemisphere boundary and label other sky unavailable."),
        [ep("eRASS1 cone search", "cone-search", "https://erosita.mpe.mpg.de/dr1/erodat/catalogue/SCS/", "Source query")],
        ["https://erosita.mpe.mpg.de/dr1/", "https://erosita.mpe.mpg.de/dr1/AllSkySurveyData_dr1/Catalogues_dr1/"], "Public access is sky-region dependent; counterpart matching is probabilistic."),
    add("vlass", "Very Large Array Sky Survey", "VLASS", "NRAO / CADC / CIRADA", "Epochs 1-3 products", "radio", 1, "public",
        ["raster", "catalog"], ["radio"], ["2-4 GHz", "Stokes I", "spectral index", "component catalog"],
        cov("wide-area", 33885, "sia-query", "queryable", "CADC SIA records provide product footprints.", endpoint="https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus/sync",
            fallback="Use declination > -40 degrees only as a nominal footprint."),
        [ep("CADC SIA", "sia2", "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus/sync", "Image discovery"), ep("CIRADA cutouts", "http", "https://cutouts.cirada.ca/", "Radio cutouts")],
        ["https://science.nrao.edu/vlass/vlass-data", "https://science.nrao.edu/vlass/vlass-data/basic-data-products"], "Quick Look products have known artifacts and dynamic-range limits."),
    add("lotss-dr2", "LOFAR Two-metre Sky Survey", "LoTSS DR2", "LOFAR Surveys KSP", "DR2", "radio", 1, "public",
        ["raster", "catalog"], ["radio"], ["120-168 MHz", "Stokes I", "rms", "source catalog", "optical IDs"],
        cov("wide-area", 5720, "hips", "queryable", "Release mosaics and pointing metadata support tract intersection.", endpoint="https://lofar-surveys.org/dr2_release.html",
            fallback="Load published DR2 mosaic polygons; do not use a simple RA/Dec box."),
        [ep("LoTSS cutouts", "http", "https://lofar-surveys.org/cutout/", "Image cutouts"), ep("DR2 release", "http", "https://lofar-surveys.org/dr2_release.html", "Mosaics and catalogs")],
        ["https://lofar-surveys.org/dr2_release.html", "https://lofar-surveys.org/"], "Extended radio components require grouping and local-noise controls."),
]

# Neutral hydrogen and resolved gas.
S += [
    add("wallaby-pdr2", "ASKAP WALLABY", "WALLABY PDR2", "CSIRO / WALLABY", "Pilot Data Release 2", "neutral-gas", 1, "public",
        ["cube", "raster", "catalog", "vector"], ["radio"], ["21-cm cube", "moment 0/1/2", "catalog", "kinematic models"],
        cov("regional", None, "sia-query", "queryable", "CASDA SIA supplies exact observation/product footprints.", endpoint="https://casda.csiro.au/casda_vo_tools/sia2/query",
            fallback="Only mark fields covered after a CASDA record is returned."),
        [ep("CASDA SIA2", "sia2", "https://casda.csiro.au/casda_vo_tools/sia2/query", "Cube discovery"), ep("CASDA DataLink", "datalink", "https://casda.csiro.au/casda_vo_tools/datalink/links", "Product resolution")],
        ["https://wallaby-survey.org/data/", "https://research.csiro.au/casda/"], "Beam, velocity channels, and column-density sensitivity must accompany every map.", "cutout-on-demand", ["spatial-spectral subcube", "moment maps", "noise", "kinematic model"], 60),
    add("hipass", "H I Parkes All Sky Survey", "HIPASS", "CSIRO / ATNF", "HICAT / HIPASS", "neutral-gas", 2, "public",
        ["cube", "catalog", "spectrum"], ["radio"], ["21-cm cube", "spectrum", "integrated flux", "velocity width"],
        cov("wide-area", 29300, "static-footprint", "fallback-static", "Southern sky to about +25 degrees; no stable per-product VO endpoint recorded.",
            fallback="Use the published southern footprint and mark cube availability unverified."),
        [ep("ATNF HIPASS release", "http", "https://www.atnf.csiro.au/research/multibeam/release/", "Catalogs and cubes")],
        ["https://www.atnf.csiro.au/research/multibeam/release/", "https://www.atnf.csiro.au/people/Baerbel.Koribalski/HIPASS/"], "The roughly 15.5 arcmin beam creates counterpart ambiguity.", "pin-cache", ["spectrum or subcube", "catalog row", "beam footprint"], 120),
    add("alfalfa-alpha100", "Arecibo Legacy Fast ALFA Survey", "ALFALFA alpha.100", "Cornell / ALFALFA", "alpha.100", "neutral-gas", 2, "public",
        ["catalog", "spectrum"], ["radio"], ["21-cm spectrum", "integrated flux", "velocity width", "reliability code"],
        cov("regional", 7000, "static-footprint", "fallback-static", "Two northern high-latitude regions; materialize published footprint polygons.",
            fallback="Use published region polygons and mark source completeness catalog-derived."),
        [ep("ALFALFA public data", "http", "https://egg.astro.cornell.edu/alfalfa/data/", "Catalogs and spectra")],
        ["https://egg.astro.cornell.edu/alfalfa/data/", "https://egg.astro.cornell.edu/alfalfa/"], "H I flux selection and Arecibo beam confusion require explicit modeling.", "metadata-local", ["tract catalog subset", "matched spectra", "beam footprint"], None),
    add("resolved-hi-archives", "Resolved Nearby-Galaxy H I Surveys", "THINGS + LITTLE THINGS", "NRAO / survey teams", "Public releases", "neutral-gas", 2, "public",
        ["cube", "raster", "vector"], ["radio"], ["21-cm cubes", "moment maps", "velocity fields"],
        cov("object-targeted", None, "observation-footprints", "fallback-static", "Small named-galaxy samples; derive polygons from FITS WCS.",
            fallback="Seed named targets from official release tables, then compute footprints from downloaded FITS headers."),
        [ep("THINGS data", "http", "https://www2.mpia-hd.mpg.de/THINGS/Data.html", "Resolved H I products"), ep("LITTLE THINGS", "http", "https://science.nrao.edu/science/surveys/littlethings", "Survey products")],
        ["https://www2.mpia-hd.mpg.de/THINGS/Data.html", "https://science.nrao.edu/science/surveys/littlethings"], "Processing, beams, and velocity resolution are heterogeneous.", "pin-cache", ["subcube", "moment maps", "beam and noise", "rotation products"], 60),
]

# Pointed high-resolution archives.
S += [
    add("hst", "Hubble Space Telescope", "HST", "NASA / ESA / STScI", "MAST public holdings", "high-resolution", 1, "mixed",
        ["raster", "catalog", "spectrum"], ["ultraviolet", "optical", "near-infrared"], ["ACS", "WFC3", "WFPC2", "COS", "STIS", "HSC"],
        cov("pointed", None, "observation-footprints", "exact", "MAST supplies exact observation polygons and data-rights metadata.", endpoint="https://mast.stsci.edu/api/v0/invoke",
            fallback="Never infer HST coverage from target names; require a returned observation polygon."),
        [ep("MAST API", "mast-api", "https://mast.stsci.edu/api/v0/invoke", "Observation and product discovery"), ep("Hubble Source Catalog", "tap", "https://catalogs.mast.stsci.edu/hsc", "Merged source catalog")],
        ["https://archive.stsci.edu/hst/", "https://archive.stsci.edu/hst/hsc/"], "Public/proprietary status, filters, PSFs, and depths are product-specific.", "pin-cache", ["drizzled cutout", "weight and data quality", "catalog rows", "spectrum"], 15),
    add("euclid-q1", "Euclid Quick Release 1", "Euclid Q1", "ESA / Euclid Consortium", "Q1", "high-resolution", 1, "public",
        ["raster", "catalog", "spectrum"], ["optical", "near-infrared"], ["VIS", "Y", "J", "H", "morphology", "photometry", "spectra"],
        cov("regional", None, "observation-footprints", "queryable", "Euclid archive TAP exposes Q1 product metadata and footprints.", endpoint="https://eas.esac.esa.int/tap-server/tap",
            fallback="Use official Q1 field polygons and label products unavailable until TAP confirms."),
        [ep("Euclid TAP", "tap", "https://eas.esac.esa.int/tap-server/tap", "Q1 metadata and catalog query")],
        ["https://euclid.esac.esa.int/dr/q1/expsup/", "https://www.cosmos.esa.int/web/euclid/euclid-q1-data-release"], "Q1 is a limited early footprint, not the final wide survey.", "pin-cache", ["VIS and NISP cutouts", "catalog rows", "mask", "public spectra"], 20),
    add("jwst", "James Webb Space Telescope", "JWST", "NASA / ESA / CSA / STScI", "MAST public holdings", "high-resolution", 1, "mixed",
        ["raster", "spectrum", "cube", "catalog"], ["near-infrared", "mid-infrared"], ["NIRCam", "NIRISS", "NIRSpec", "MIRI", "IFU cubes"],
        cov("pointed", None, "observation-footprints", "exact", "MAST supplies exact polygons and public-release dates.", endpoint="https://mast.stsci.edu/api/v0/invoke",
            fallback="Require a returned MAST observation polygon and public data-rights state."),
        [ep("MAST API", "mast-api", "https://mast.stsci.edu/api/v0/invoke", "Observation and product discovery")],
        ["https://archive.stsci.edu/missions-and-data/jwst", "https://jwst-docs.stsci.edu/accessing-jwst-data"], "Public/proprietary state is exposure-specific and fields are small.", "pin-cache", ["calibrated cutout", "quality and weight", "spectrum", "IFU subcube"], 10),
    add("alma", "Atacama Large Millimeter/submillimeter Array", "ALMA", "ALMA Partnership", "Science Archive public holdings", "high-resolution", 1, "mixed",
        ["cube", "raster", "catalog", "spectrum"], ["millimeter", "radio"], ["continuum", "spectral-line cubes", "moment maps", "measurement sets"],
        cov("pointed", None, "observation-footprints", "exact", "ALMA TAP supplies exact footprints and data-rights metadata.", endpoint="https://almascience.eso.org/tap",
            fallback="Require a TAP-returned public observation footprint."),
        [ep("ALMA TAP", "tap", "https://almascience.eso.org/tap", "Observation discovery"), ep("ALMA archive", "archive-api", "https://almascience.eso.org/asax/", "Public product access")],
        ["https://almascience.nrao.edu/alma-data/archive", "https://almascience.eso.org/documents-and-tools"], "Interferometric products can resolve out diffuse flux; beam and setup vary.", "pin-cache", ["continuum cutout", "spectral subcube", "beam and noise", "moment maps"], 5),
]

# Lensing and microwave mass maps.
S += [
    add("des-y3-lensing", "DES Year 3 Weak Lensing", "DES Y3 lensing", "DES Collaboration", "Y3 cosmology products", "lensing", 1, "public",
        ["catalog", "raster", "vector"], ["non-photonic", "optical"], ["shear catalog", "source redshift", "convergence", "masks"],
        cov("wide-area", 4143, "static-footprint", "fallback-static", "Use the official Y3 lensing mask, not the broader DES imaging footprint.",
            fallback="Rasterize the released lensing mask to a MOC before claiming tract overlap."),
        [ep("DES Y3 release", "http", "https://des.ncsa.illinois.edu/releases/y3a2", "Lensing and cosmology products")],
        ["https://des.ncsa.illinois.edu/releases/y3a2", "https://www.darkenergysurvey.org/des-year-3-cosmology-results-papers/"], "Shear calibration, tomography, and correlated map uncertainty are mandatory.", "derived-products-only", ["shear subset", "convergence tile", "redshift distribution", "mask"], 120),
    add("kids-1000-lensing", "Kilo-Degree Survey Weak Lensing", "KiDS-1000", "KiDS Collaboration / ESO", "KiDS-1000", "lensing", 2, "public",
        ["catalog", "raster", "vector"], ["non-photonic", "optical", "near-infrared"], ["shear", "photo-z", "convergence", "ugri+VIKING"],
        cov("regional", 1006, "static-footprint", "fallback-static", "Use released lensing masks for the north/south patches.",
            fallback="Rasterize release masks; do not use rectangular survey bounds."),
        [ep("KiDS DR4", "http", "https://kids.strw.leidenuniv.nl/DR4/", "Catalogs, images, and lensing products")],
        ["https://kids.strw.leidenuniv.nl/DR4/", "https://kids.strw.leidenuniv.nl/KiDS-1000.php"], "Lensing selection and correlated mass-map pixels must be preserved.", "derived-products-only", ["shear subset", "mass-map tile", "redshift distribution", "mask"], 120),
    add("hsc-lensing", "HSC Public Weak-Lensing Products", "HSC lensing", "HSC Collaboration / NAOJ", "S16A and PDR2-associated releases", "lensing", 2, "public",
        ["catalog", "raster", "vector"], ["non-photonic", "optical"], ["shape catalog", "photo-z", "calibration", "masks"],
        cov("regional", 160, "catalog-derived", "catalog-derived", "The public S16A shear-selected peak catalog supplies exact released-product positions; continuous mass-map support remains a distinct product.",
            endpoint="https://academic.oup.com/pasj/article/70/SP1/S27/4714784",
            fallback="Use only the published shear-selected peak coordinates or an authenticated released mass-map mask; never substitute the optical imaging footprint."),
        [ep("HSC S16A public products", "http", "https://hsc-release.mtk.nao.ac.jp/doc/index.php/s16a-shape-catalog-data-products-pdr2/", "Released mass-map and cosmic-shear products"), ep("Shear-selected peak catalog", "catalog", "https://academic.oup.com/pasj/article/70/SP1/S27/4714784", "Published weak-lensing peak coordinates")],
        ["https://hsc-release.mtk.nao.ac.jp/doc/index.php/s16a-shape-catalog-data-products-pdr2/", "https://academic.oup.com/pasj/article/70/SP1/S27/4714784"], "The map uses exact positions of 65 released shear-selected peaks as conservative product support, not a continuous shear or convergence footprint.", "derived-products-only", ["shear-selected peak subset", "shape subset", "photo-z", "mask", "derived convergence"], 120),
    add("planck-2018", "Planck Legacy Release", "Planck 2018", "ESA / Planck", "PR3 2018", "cmb-large-scale-structure", 2, "public",
        ["raster", "catalog"], ["microwave", "far-infrared"], ["30-857 GHz", "CMB lensing", "Compton-y", "compact sources", "dust"],
        cov("all-sky", 41253, "hips", "exact", "All-sky HEALPix products with product-specific masks.", endpoint="https://pla.esac.esa.int/",
            moc="CDS/P/PLANCK/R3/HFI/color", fallback="Treat as all-sky but require the product mask before analysis."),
        [ep("Planck Legacy Archive", "http", "https://pla.esac.esa.int/", "Maps, masks, and catalogs")],
        ["https://pla.esac.esa.int/", "https://www.cosmos.esa.int/web/planck/publications"], "Planck beams are far broader than Rubin and foregrounds are correlated.", "derived-products-only", ["HEALPix tile", "mask", "catalog subset"], 300),
    add("act-dr6", "Atacama Cosmology Telescope", "ACT DR6", "ACT Collaboration / NASA LAMBDA", "DR6 products", "cmb-large-scale-structure", 2, "public",
        ["raster", "catalog"], ["microwave"], ["90 GHz", "150 GHz", "220 GHz", "CMB lensing", "Compton-y", "clusters"],
        cov("wide-area", 18000, "static-footprint", "fallback-static", "Product-specific ACT regions and masks must be materialized.",
            fallback="Rasterize official release masks before showing tract overlap."),
        [ep("LAMBDA ACT archive", "bulk-download", "https://lambda.gsfc.nasa.gov/product/act/", "Maps, beams, masks, catalogs")],
        ["https://lambda.gsfc.nasa.gov/product/act/", "https://act.princeton.edu/"], "Beam and correlated atmospheric noise preclude optical-scale subtraction.", "derived-products-only", ["map tile", "beam and mask", "catalog subset"], 300),
    add("spt-3g", "South Pole Telescope", "SPT", "SPT Collaboration", "Public survey products", "cmb-large-scale-structure", 2, "public",
        ["raster", "catalog"], ["microwave"], ["95 GHz", "150 GHz", "220 GHz", "SZ clusters", "CMB lensing"],
        cov("wide-area", 2500, "static-footprint", "fallback-static", "Core SPT-SZ and release-dependent SPT-3G masks.",
            fallback="Materialize official product masks; do not assume all SPT releases share coverage."),
        [ep("SPT public data", "http", "https://pole.uchicago.edu/public/data/", "Maps and catalogs")],
        ["https://pole.uchicago.edu/public/data/", "https://pole.uchicago.edu/"], "Footprint and access vary across releases; SZ mass is model-dependent.", "derived-products-only", ["map tile", "beam and mask", "cluster subset"], 300),
]

REQUIRED = {
    "des-dr2", "legacy-surveys-dr10", "panstarrs-dr2", "hsc-ssp-pdr2", "galex-gr6-7", "unwise", "2mass",
    "gaia-dr3", "ztf-dr", "desi-dr1", "sdss-dr19", "erosita-erass1", "vlass", "lotss-dr2", "wallaby-pdr2",
    "hipass", "alfalfa-alpha100", "resolved-hi-archives", "hst", "euclid-q1", "jwst", "alma", "des-y3-lensing",
    "kids-1000-lensing", "hsc-lensing", "planck-2018", "act-dr6", "spt-3g",
}


def build() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "product": "Layers",
        "generatedAt": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "indexSurvey": {
            "id": "rubin-dp2", "name": "Vera C. Rubin Observatory Data Preview 2",
            "release": "Early DP2 / full DP2 when available", "role": "spatial-index", "coverageUnit": "tract",
            "provenanceUrls": ["https://dp2.lsst.io/", "https://dp2.lsst.io/products/images/deep_coadd.html"],
        },
        "policies": {
            "discovery": "Materialize exact survey-to-Rubin-tract intersections from MOC, SIA, TAP, or observation polygons; never infer coverage from a target list.",
            "storage": "Store geometry, provenance, and compact catalog subsets locally; fetch bounded science-ready cutouts or subcubes on demand.",
            "comparison": "A difference claim requires common WCS, PSF or beam and bandpass treatment, masks, background matching, and propagated uncertainty.",
            "colorSemantics": {"rubinOnly": "red", "referenceOnly": "blue", "consistent": "white-or-purple", "invalid": "amber-hatching"},
        },
        "surveys": S,
    }


def validate(registry: dict[str, Any]) -> None:
    ids = [item["id"] for item in registry["surveys"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Survey ids must be unique")
    if REQUIRED - set(ids):
        raise ValueError(f"Missing required surveys: {sorted(REQUIRED - set(ids))}")
    for item in registry["surveys"]:
        assert item["cachePolicy"]["downloadWholeArchive"] is False
        assert item["endpoints"] and item["provenanceUrls"]
        assert all(url.startswith("https://") for url in item["provenanceUrls"])
        assert item["coverage"]["machineReadableStatus"] in {"exact", "queryable", "fallback-static", "catalog-derived"}
        assert item["coverage"]["fallback"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    registry = build()
    validate(registry)
    if args.check:
        if not args.output.exists():
            raise SystemExit(f"Missing {args.output}")
        checked = json.loads(args.output.read_text(encoding="utf-8"))
        checked["generatedAt"] = registry["generatedAt"]
        if checked != registry:
            raise SystemExit("survey-registry.json is stale")
        print(f"Validated {len(S)} surveys in {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(S)} surveys to {args.output}")


if __name__ == "__main__":
    main()
