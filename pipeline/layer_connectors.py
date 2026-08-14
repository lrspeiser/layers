#!/usr/bin/env python3
"""Survey-neutral contracts and request builders for the Layers cache.

This module deliberately separates three concepts that are easy to conflate:

* discovery metadata (SIA, TAP, MAST, or an archive search response),
* archive-native science inputs (FITS/table/cube plus support products), and
* display products (JPEG/PNG/HiPS tiles).

A connector can create reproducible requests, but it cannot declare an image
science-ready.  That promotion requires local validation of WCS, units, masks,
uncertainty, calibration, and the comparison-specific PSF/bandpass treatment.
No credential value is ever serialized; only an environment-variable name is.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable


SCHEMA_VERSION = "layers-region-cache-v1"
USER_AGENT = "Layers-science-cache/0.1"
RUBIN_SIA = "https://data.lsst.cloud/api/sia/dp2/query"
RUBIN_DATALINK = "https://data.lsst.cloud/api/datalink/links"

LAYER_KINDS = {"raster", "catalog", "spectrum", "time-series", "cube", "preview"}
READINESS = {"metadata-only", "science-input-candidate", "display-only", "validated-science-input"}

# Minimum archive/cache contract by scientific primitive. These are evidence
# requirements, not assertions that a downloaded product has satisfied them.
LAYER_CONTRACTS: dict[str, dict[str, Any]] = {
    "raster": {
        "required": ["spatialWcs", "pixelUnits", "sciencePlane", "varianceOrWeight", "validityMask", "provenance", "sha256"],
        "comparisonControls": ["commonWcs", "psfOrBeam", "bandpass", "background", "resamplingCovariance", "commonMask"],
    },
    "catalog": {
        "required": ["coordinateFrame", "columnUnits", "qualityFlags", "publisherDatasetId", "provenance", "sha256"],
        "comparisonControls": ["matchRadiusModel", "epochPropagation", "selectionFunction", "falseMatchEstimate"],
    },
    "spectrum": {
        "required": ["spectralWcs", "fluxUnits", "uncertainty", "qualityMask", "apertureMetadata", "provenance", "sha256"],
        "comparisonControls": ["redshiftFrame", "spectralResolution", "apertureMatch", "lineSpreadFunction"],
    },
    "time-series": {
        "required": ["timeSystem", "timeScale", "measurementUnits", "uncertainty", "qualityFlags", "provenance", "sha256"],
        "comparisonControls": ["epochMatch", "filterTransform", "cadenceSelection", "upperLimitSemantics"],
    },
    "cube": {
        "required": ["spatialWcs", "spectralWcs", "voxelUnits", "varianceOrWeight", "validityMask", "beam", "provenance", "sha256"],
        "comparisonControls": ["commonWcs", "beamMatch", "spectralFrame", "channelWidth", "background", "commonMask"],
    },
    "preview": {
        "required": ["displayTransform", "sourceReference", "provenance", "sha256"],
        "comparisonControls": [],
        "quantitativeUseAllowed": False,
    },
}


def _decimal(value: float, places: int) -> str:
    quantum = Decimal(1).scaleb(-places)
    return format(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP), f".{places}f")


def canonical_cache_identity(
    survey_id: str,
    release: str,
    ra_deg: float,
    dec_deg: float,
    size_arcmin: float,
    band: str | None,
    layer_kind: str,
) -> dict[str, str]:
    """Return a platform-independent identity with sub-milliarcsec precision.

    Positions are rounded to 8 decimal degrees (~0.036 mas) and sizes to six
    decimal arcminutes. This removes differences such as 1 vs 1.0 while being
    much finer than any survey used here.
    """

    if layer_kind not in LAYER_KINDS:
        raise ValueError(f"Unsupported layer kind: {layer_kind}")
    return {
        "surveyId": survey_id.strip().lower(),
        "release": release.strip(),
        "raDeg": _decimal(ra_deg % 360.0, 8),
        "decDeg": _decimal(dec_deg, 8),
        "sizeArcmin": _decimal(size_arcmin, 6),
        "band": (band or "all").strip(),
        "layerKind": layer_kind,
    }


def cache_key(**kwargs: Any) -> tuple[str, dict[str, str]]:
    identity = canonical_cache_identity(**kwargs)
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    safe_release = "".join(c.lower() if c.isalnum() else "-" for c in identity["release"]).strip("-")
    safe_release = "-".join(filter(None, safe_release.split("-"))) or "release"
    return f"{identity['surveyId']}/{safe_release}/{digest[:24]}", identity


@dataclass(frozen=True)
class SkyRegion:
    id: str
    ra_deg: float
    dec_deg: float
    size_arcmin: float
    tract: int | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Region id is required")
        if not 0 <= self.ra_deg < 360:
            raise ValueError(f"RA outside [0, 360): {self.ra_deg}")
        if not -90 <= self.dec_deg <= 90:
            raise ValueError(f"Dec outside [-90, 90]: {self.dec_deg}")
        if not 0 < self.size_arcmin <= 120:
            raise ValueError(f"Cutout size outside (0, 120] arcmin: {self.size_arcmin}")


@dataclass(frozen=True)
class QuotaPolicy:
    service: str
    account_requests_per_minute: int | None
    configured_requests_per_minute: int
    maximum_concurrency: int
    retry_http_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    successful_responses_cached: bool = True
    honor_retry_after: bool = True


@dataclass(frozen=True)
class RequestSpec:
    method: str
    url: str
    purpose: str
    accept: str
    credential_env: str | None = None
    body: str | None = None
    content_type: str | None = None


@dataclass(frozen=True)
class ProductContract:
    layer_kind: str
    media_type: str
    readiness: str
    quantitative_use_allowed: bool
    units: str | None
    wcs_required: bool
    variance_required: bool
    mask_required: bool
    coverage_required: bool = False
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.layer_kind not in LAYER_KINDS:
            raise ValueError(self.layer_kind)
        if self.readiness not in READINESS:
            raise ValueError(self.readiness)
        if self.readiness in {"display-only", "metadata-only", "science-input-candidate"} and self.quantitative_use_allowed:
            raise ValueError(f"{self.readiness} cannot allow quantitative use")


@dataclass(frozen=True)
class AcquisitionJob:
    region: SkyRegion
    survey_id: str
    release: str
    band: str | None
    phase: str
    request: RequestSpec
    quota: QuotaPolicy
    product: ProductContract
    provider: str
    source_documentation: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        key, identity = cache_key(
            survey_id=self.survey_id,
            release=self.release,
            ra_deg=self.region.ra_deg,
            dec_deg=self.region.dec_deg,
            size_arcmin=self.region.size_arcmin,
            band=self.band,
            layer_kind=self.product.layer_kind,
        )
        phase_hash = hashlib.sha256(f"{self.phase}\0{self.request.url}".encode("utf-8")).hexdigest()[:12]
        job_id = f"{key}/{self.phase}-{phase_hash}"
        return {
            "schemaVersion": SCHEMA_VERSION,
            "jobId": job_id,
            "cacheKey": key,
            "cacheIdentity": identity,
            "region": asdict(self.region),
            "surveyId": self.survey_id,
            "release": self.release,
            "band": self.band,
            "phase": self.phase,
            "request": asdict(self.request),
            "quotaPolicy": {**asdict(self.quota), "retry_http_statuses": list(self.quota.retry_http_statuses)},
            "productContract": {**asdict(self.product), "notes": list(self.product.notes)},
            "provider": self.provider,
            "sourceDocumentation": list(self.source_documentation),
            "dependsOn": list(self.depends_on),
            "metadata": self.metadata,
            "status": "planned",
            "cache": {
                "path": None,
                "bytes": None,
                "sha256": None,
                "retrievedAt": None,
                "responseContentType": None,
            },
            "validation": {
                "wcsPresent": None,
                "unitsVerified": None,
                "variancePresent": None,
                "maskPresent": None,
                "coveragePresent": None,
                "scienceReady": False,
                "comparisonReady": False,
            },
            "provenance": {
                "sourceUrl": self.request.url,
                "provider": self.provider,
                "release": self.release,
                "requestPurpose": self.request.purpose,
                "publisherDatasetIds": [],
            },
        }


class LayerConnector:
    survey_id: str
    release: str

    def jobs(self, region: SkyRegion, bands: Iterable[str] | None = None) -> list[AcquisitionJob]:
        raise NotImplementedError


class RubinDP2Connector(LayerConnector):
    survey_id = "rubin-dp2"
    release = "DP2"
    quota = QuotaPolicy("rubin-sia", 70, 55, 1)
    datalink_quota = QuotaPolicy("rubin-datalink", 250, 120, 1)

    def jobs(self, region: SkyRegion, bands: Iterable[str] | None = None) -> list[AcquisitionJob]:
        radius_deg = region.size_arcmin / 60.0 / math.sqrt(2.0)
        params = {
            "POS": f"CIRCLE {region.ra_deg:.10f} {region.dec_deg:.10f} {radius_deg:.8f}",
            "CALIB": "3",
            "DPTYPE": "image",
            "DPSUBTYPE": "lsst.deep_coadd",
            "MAXREC": "10000",
        }
        url = f"{RUBIN_SIA}?{urllib.parse.urlencode(params)}"
        return [
            AcquisitionJob(
                region=region,
                survey_id=self.survey_id,
                release=self.release,
                band=None,
                phase="discover",
                request=RequestSpec("GET", url, "Discover overlapping deep-coadd patch-band datasets", "application/x-votable+xml", "RUBIN_RSP_TOKEN"),
                quota=self.quota,
                product=ProductContract("catalog", "application/x-votable+xml", "metadata-only", False, None, False, False, False),
                provider="Rubin Science Platform",
                source_documentation=("https://dp2.lsst.io/products/images/deep_coadd.html", "https://rsp.lsst.io/guides/api/"),
                metadata={
                    "nextStep": "Create one DataLink request per obs_publisher_did; retain #this FITS plus immutable publisher id.",
                    "dataLinkEndpoint": RUBIN_DATALINK,
                    "requestedBands": sorted(set(bands or ("u", "g", "r", "i", "z", "y"))),
                },
            )
        ]

    def datalink_jobs(self, region: SkyRegion, publisher_ids: Iterable[str]) -> list[AcquisitionJob]:
        identifiers = sorted(set(filter(None, publisher_ids)))
        if not identifiers:
            return []
        url = f"{RUBIN_DATALINK}?{urllib.parse.urlencode([('ID', value) for value in identifiers])}"
        return [
            AcquisitionJob(
                region=region,
                survey_id=self.survey_id,
                release=self.release,
                band=None,
                phase="datalink",
                request=RequestSpec("GET", url, "Resolve archive-native FITS and support-product links in one batch", "application/x-votable+xml;content=datalink", "RUBIN_RSP_TOKEN"),
                quota=self.datalink_quota,
                product=ProductContract("catalog", "application/x-votable+xml", "metadata-only", False, None, False, False, False),
                provider="Rubin Science Platform",
                source_documentation=("https://rsp.lsst.io/guides/api/using-datalink.html",),
                metadata={"publisherDatasetIds": identifiers, "selectSemantics": "#this", "batchedRequest": True},
            )
        ]


class LegacySurveyConnector(LayerConnector):
    survey_id = "legacy-surveys-dr10"
    release = "DR10"
    endpoint = "https://www.legacysurvey.org/viewer/fits-cutout"
    quota = QuotaPolicy("legacy-fits-cutout", None, 30, 2)

    def jobs(self, region: SkyRegion, bands: Iterable[str] | None = None) -> list[AcquisitionJob]:
        selected = "".join(sorted(set(bands or ("g", "r", "i", "z"))))
        # 0.4 arcsec/pixel gives a bounded request; the service is capped at 512.
        size_pixels = min(512, max(32, math.ceil(region.size_arcmin * 60 / 0.4)))
        params = {
            "ra": f"{region.ra_deg:.10f}",
            "dec": f"{region.dec_deg:.10f}",
            "size": size_pixels,
            "layer": "ls-dr10",
            "pixscale": "0.4",
            "bands": selected,
            "invvar": "",
        }
        url = f"{self.endpoint}?{urllib.parse.urlencode(params)}"
        return [
            AcquisitionJob(
                region, self.survey_id, self.release, selected, "acquire", RequestSpec("GET", url, "Acquire bounded FITS science and inverse-variance planes", "application/fits"), self.quota,
                ProductContract("raster", "application/fits", "science-input-candidate", False, "nanomaggy/pixel (archive native; verify header)", True, True, True,
                    notes=("For fields larger than 512 pixels, build deterministic overlapping tiles and mosaic locally.", "Pixel-area scaling and archive mask handling must pass before validation.")),
                "DESI Legacy Imaging Surveys", ("https://www.legacysurvey.org/dr10/description/", "https://www.legacysurvey.org/viewer"),
                metadata={"requestedPixelScaleArcsec": 0.4, "serviceSizePixels": size_pixels, "needsTiling": region.size_arcmin * 60 / 0.4 > 512},
            )
        ]


class PanStarrsConnector(LayerConnector):
    survey_id = "panstarrs-dr2"
    release = "DR2"
    endpoint = "https://ps1images.stsci.edu/cgi-bin/ps1filenames.py"
    quota = QuotaPolicy("panstarrs-image-list", None, 30, 2)

    def jobs(self, region: SkyRegion, bands: Iterable[str] | None = None) -> list[AcquisitionJob]:
        selected = "".join(sorted(set(bands or ("g", "r", "i", "z", "y"))))
        params = {
            "ra": f"{region.ra_deg:.10f}", "dec": f"{region.dec_deg:.10f}", "filters": selected,
            "type": "stack,stack.wt,stack.mask", "sep": ",",
        }
        url = f"{self.endpoint}?{urllib.parse.urlencode(params)}"
        return [
            AcquisitionJob(
                region, self.survey_id, self.release, selected, "discover", RequestSpec("GET", url, "Discover full stack, weight, and mask skycells", "text/csv"), self.quota,
                ProductContract("catalog", "text/csv", "metadata-only", False, None, False, False, False),
                "MAST Pan-STARRS", ("https://outerspace.stsci.edu/display/PANSTARRS/PS1+Image+Cutout+Service",),
                metadata={"requiredProductTypes": ["stack", "stack.wt", "stack.mask"], "nextStep": "Download all three archive-native FITS products for each selected skycell; do not use JPEG cutouts for analysis."},
            )
        ]


class GenericSIAConnector(LayerConnector):
    # Discovery only needs a bounded set of candidate products. Some services
    # (notably 2MASS) can return thousands of overlapping atlas products even
    # for a small cone, so callers must paginate deliberately if they need an
    # exhaustive product inventory.
    maximum_records = 500

    def __init__(self, survey: dict[str, Any], endpoint: dict[str, Any]) -> None:
        self.survey = survey
        self.survey_id = survey["id"]
        self.release = survey["release"]
        self.endpoint = endpoint["url"]
        self.authentication = endpoint.get("authentication", "none")
        account_limit = 70 if self.survey_id == "rubin-dp2" else None
        self.quota = QuotaPolicy(f"{self.survey_id}-sia", account_limit, 30, 1)

    def jobs(self, region: SkyRegion, bands: Iterable[str] | None = None) -> list[AcquisitionJob]:
        radius_deg = region.size_arcmin / 60.0 / math.sqrt(2.0)
        url = f"{self.endpoint}?{urllib.parse.urlencode({'POS': f'CIRCLE {region.ra_deg:.10f} {region.dec_deg:.10f} {radius_deg:.8f}', 'MAXREC': str(self.maximum_records)})}"
        credential = "RUBIN_RSP_TOKEN" if self.authentication not in {"none", "public", None} else None
        kinds = set(self.survey.get("layerPrimitives", []))
        return [
            AcquisitionJob(
                region, self.survey_id, self.release, None, "discover", RequestSpec("GET", url, "Discover records intersecting the requested sky region", "application/x-votable+xml", credential), self.quota,
                ProductContract("catalog", "application/x-votable+xml", "metadata-only", False, None, False, False, False),
                self.survey.get("organization", self.survey.get("name", self.survey_id)), tuple(self.survey.get("provenanceUrls", [])),
                metadata={
                    "declaredLayerPrimitives": sorted(kinds),
                    "requestedBands": sorted(set(bands or ())),
                    "coverageMustBeConfirmedPerProduct": True,
                    "maximumRecords": self.maximum_records,
                    "paginationRequiredForExhaustiveInventory": True,
                },
            )
        ]


class RegistryDiscoveryConnector(LayerConnector):
    """A reproducible manual/API discovery plan for non-SIA archives.

    The request is never executed automatically unless it is an HTTP GET with
    a fully specified sky-query URL. This keeps a registry entry from being
    mistaken for a science product merely because it has a homepage URL.
    """

    def __init__(self, survey: dict[str, Any], endpoint: dict[str, Any] | None) -> None:
        self.survey = survey
        self.survey_id = survey["id"]
        self.release = survey["release"]
        self.endpoint = endpoint
        self.quota = QuotaPolicy(f"{self.survey_id}-discovery", None, 20, 1)

    def jobs(self, region: SkyRegion, bands: Iterable[str] | None = None) -> list[AcquisitionJob]:
        endpoint = self.endpoint or {"url": "", "protocol": "manual", "authentication": "unknown"}
        base = endpoint.get("url", "")
        query = urllib.parse.urlencode({"ra": f"{region.ra_deg:.10f}", "dec": f"{region.dec_deg:.10f}", "radius_arcmin": _decimal(region.size_arcmin / 2, 6)})
        # Mark the URL as a discovery recipe. Only explicitly supported SIA and
        # archive connectors are eligible for network execution by the runner.
        url = f"{base}{'&' if '?' in base else '?'}{query}" if base else f"manual://{self.survey_id}/{region.id}"
        return [
            AcquisitionJob(
                region, self.survey_id, self.release, None, "discover-manual", RequestSpec("GET", url, "Archive-specific overlap discovery; adapter required", "application/octet-stream"), self.quota,
                ProductContract("catalog", "application/octet-stream", "metadata-only", False, None, False, False, False),
                self.survey.get("organization", self.survey.get("name", self.survey_id)), tuple(self.survey.get("provenanceUrls", [])),
                metadata={"protocol": endpoint.get("protocol"), "automaticExecutionAllowed": False, "requestedBands": sorted(set(bands or ())), "adapterStatus": "planned"},
            )
        ]


class HiPSPreviewConnector(LayerConnector):
    """Display fallback only. HiPS renders must never enter quantitative QA."""

    def __init__(self, survey_id: str, release: str, hips_id: str) -> None:
        self.survey_id = survey_id
        self.release = release
        self.hips_id = hips_id
        self.quota = QuotaPolicy(f"{survey_id}-hips", 1000 if survey_id == "rubin-dp2" else None, 120, 4)

    def jobs(self, region: SkyRegion, bands: Iterable[str] | None = None) -> list[AcquisitionJob]:
        params = {
            "hips": self.hips_id, "ra": f"{region.ra_deg:.10f}", "dec": f"{region.dec_deg:.10f}",
            "fov": f"{region.size_arcmin / 60:.8f}", "width": "1024", "height": "1024", "projection": "TAN", "format": "jpg",
        }
        url = f"https://alasky.u-strasbg.fr/hips-image-services/hips2fits?{urllib.parse.urlencode(params)}"
        return [
            AcquisitionJob(
                region, self.survey_id, self.release, None, "preview", RequestSpec("GET", url, "Display-only sky preview", "image/jpeg"), self.quota,
                ProductContract("preview", "image/jpeg", "display-only", False, None, False, False, False, notes=("Rendered pixels are not photometric evidence.",)),
                "CDS HiPS2FITS", ("https://aladin.cds.unistra.fr/hips/",), metadata={"hipsId": self.hips_id, "automaticExecutionAllowed": True},
            )
        ]


def connectors_from_registry(registry: dict[str, Any]) -> dict[str, LayerConnector]:
    connectors: dict[str, LayerConnector] = {"rubin-dp2": RubinDP2Connector()}
    for survey in registry.get("surveys", []):
        survey_id = survey["id"]
        if survey_id == "legacy-surveys-dr10":
            connectors[survey_id] = LegacySurveyConnector()
            continue
        if survey_id == "panstarrs-dr2":
            connectors[survey_id] = PanStarrsConnector()
            continue
        endpoints = survey.get("endpoints", [])
        sia = next((endpoint for endpoint in endpoints if endpoint.get("protocol") in {"sia", "sia2"}), None)
        connectors[survey_id] = GenericSIAConnector(survey, sia) if sia else RegistryDiscoveryConnector(survey, endpoints[0] if endpoints else None)
    return connectors


def parse_votable_rows(payload: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(payload)
    table = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "TABLE"), None)
    if table is None:
        return []
    fields = [node.attrib.get("name", "") for node in table if node.tag.rsplit("}", 1)[-1] == "FIELD"]
    rows: list[dict[str, str]] = []
    for tr in table.iter():
        if tr.tag.rsplit("}", 1)[-1] != "TR":
            continue
        cells = [td.text or "" for td in tr if td.tag.rsplit("}", 1)[-1] == "TD"]
        rows.append(dict(zip(fields, cells, strict=False)))
    return rows


def response_summary(job: dict[str, Any], payload: bytes) -> dict[str, Any]:
    accept = job["request"]["accept"]
    if "votable" in accept or payload.lstrip().startswith(b"<?xml"):
        rows = parse_votable_rows(payload)
        publisher_ids = sorted({row.get("obs_publisher_did", "") for row in rows if row.get("obs_publisher_did")})
        return {"format": "votable", "rowCount": len(rows), "publisherDatasetIds": publisher_ids}
    if "csv" in accept:
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig", errors="replace"))))
        return {"format": "csv", "rowCount": len(rows), "columns": list(rows[0]) if rows else []}
    return {"format": "binary", "bytes": len(payload)}


def public_connector_contracts(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Small serializable connector inventory for manifests and validators."""

    result = []
    for survey_id, connector in sorted(connectors_from_registry(registry).items()):
        result.append({
            "surveyId": survey_id,
            "release": connector.release,
            "connector": type(connector).__name__,
            "supportsAutomaticExecution": not isinstance(connector, RegistryDiscoveryConnector),
            "wholeArchiveDownload": False,
        })
    return result
