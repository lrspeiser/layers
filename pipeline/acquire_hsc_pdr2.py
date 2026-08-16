#!/usr/bin/env python3
"""Fetch HSC-SSP PDR2 coadd cutouts for the selected regions, with data-rights credentials.

HSC PDR2 is the single largest gap in this project's optical coverage. The goal
counted 162 regions from footprint overlap and delivered zero, because everything
PDR2 publishes without credentials is HiPS: display tiles carrying neither
calibrated flux nor a variance plane, so no photometry can be done on them. The
science coadds sit behind a STARS account at the HSC release site.

This fetches them the same way `acquire_dp2_pixels.py` fetches Rubin: credentials
come from the environment, are used only for the HTTP request, and are never
written to a manifest, a filename, a log line, or an error message. Add to `.env`
(gitignored):

    HSC_USERNAME=you@example.org
    HSC_PASSWORD=...

Never pass a password on the command line -- it lands in shell history and in the
process table where other users can read it. There is deliberately no
`--password` flag for that reason.

Third parties cannot reproduce this step, and that is a property of the data and
not of the code: PDR2 science pixels require an account. The manifest records
what was fetched with SHA-256 checksums so the *result* stays checkable even
where the *fetch* is not.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGIONS = ROOT / "public/data/layers/selected-regions/legacy-dr10-200.json"
DEFAULT_OUTPUT = ROOT / "public/data/layers/hsc-pdr2/manifest.json"
DEFAULT_CACHE = ROOT / "pipeline/results/hsc-pdr2"

# The PDR2 cutout endpoint. rerun is the PDR2 wide coadd; band names are HSC-*.
CUTOUT_URL = "https://hsc-release.mtk.nao.ac.jp/das_cutout/pdr2/cgi-bin/cutout"
RERUN = "pdr2_wide"
BAND = "HSC-R"
# 4 arcmin square, matching the Rubin and Legacy cutouts this compares against.
SIZE_ARCMIN = 4.0
USER_AGENT = "Rubin-Light-Atlas/0.3 (+https://github.com/lrspeiser/rubin-light-atlas)"

RETRIES = 3
BACKOFF_SECONDS = 4
TIMEOUT_SECONDS = 180


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def credentials(env_path: Path) -> tuple[str, str]:
    """Username and password from the environment. Never returned to a caller that logs."""
    dotenv = read_dotenv(env_path)
    user = os.environ.get("HSC_USERNAME") or dotenv.get("HSC_USERNAME")
    password = os.environ.get("HSC_PASSWORD") or dotenv.get("HSC_PASSWORD")
    if not user or not password:
        raise SystemExit(
            "Missing HSC_USERNAME / HSC_PASSWORD.\n"
            "Put them in .env (which is gitignored) or export them in your shell:\n"
            "    HSC_USERNAME=you@example.org\n"
            "    HSC_PASSWORD=...\n"
            "There is no --password flag on purpose: a password on the command line is "
            "readable from shell history and the process table."
        )
    return user, password


def redact(message: str, secrets: tuple[str, ...]) -> str:
    """Strip anything secret out of text before it reaches a log or a manifest."""
    for secret in secrets:
        if secret:
            message = message.replace(secret, "<redacted>")
    return message


def cutout_url(ra_deg: float, dec_deg: float) -> str:
    query = urllib.parse.urlencode(
        {
            "ra": f"{ra_deg:.6f}",
            "dec": f"{dec_deg:.6f}",
            "sw": f"{SIZE_ARCMIN / 2:.3f}arcmin",
            "sh": f"{SIZE_ARCMIN / 2:.3f}arcmin",
            "type": "coadd",
            "image": "on",
            "mask": "on",
            "variance": "on",
            "filter": BAND,
            "rerun": RERUN,
        }
    )
    return f"{CUTOUT_URL}?{query}"


def fetch(url: str, user: str, password: str) -> bytes:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Basic {token}")
    request.add_header("User-Agent", USER_AGENT)
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            # 401/403 will not improve by retrying, and retrying a bad credential
            # risks locking the account.
            if error.code in (401, 403):
                raise SystemExit(
                    f"HSC rejected the credentials (HTTP {error.code}). Check HSC_USERNAME "
                    "and HSC_PASSWORD, and that the account has PDR2 access."
                ) from None
            last = error
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF_SECONDS * (attempt + 1))
    raise RuntimeError(str(last))


def inspect(path: Path) -> dict[str, Any]:
    """Science-readiness of a fetched cutout: real flux, a variance plane, usable pixels."""
    with fits.open(path) as handle:
        planes = [hdu.name for hdu in handle]
        image = None
        for hdu in handle:
            if hdu.data is not None and hdu.data.ndim == 2:
                image = np.asarray(hdu.data, dtype=float)
                break
        if image is None:
            raise ValueError("no 2-D image plane")
        finite = np.isfinite(image)
        if not finite.any():
            raise ValueError("no finite pixels")
        return {
            "planes": planes,
            "shape": [int(n) for n in image.shape],
            "validPixelFraction": float(finite.mean()),
            "hasVariancePlane": any("VAR" in name.upper() for name in planes),
            "hasMaskPlane": any("MASK" in name.upper() for name in planes),
            "medianPixel": float(np.median(image[finite])),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", type=Path, default=DEFAULT_REGIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--band", default=BAND)
    parser.add_argument(
        "--check-endpoint",
        action="store_true",
        help="Verify the cutout URL without credentials and exit. A 401 is the pass: it means "
             "the service exists and accepted the rerun and filter names, and only the account "
             "is missing. A 404 would mean the URL or rerun is wrong.",
    )
    args = parser.parse_args()

    if args.check_endpoint:
        url = cutout_url(150.0, 2.0)
        request = urllib.request.Request(url)
        request.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                print(f"HTTP {response.status} without credentials -- unexpected but not fatal")
        except urllib.error.HTTPError as error:
            verdict = "reachable, credentials required" if error.code in (401, 403) else "WRONG URL or rerun"
            print(f"HTTP {error.code} {error.reason}  ->  {verdict}")
        except Exception as error:  # noqa: BLE001
            print(f"could not reach the service: {type(error).__name__}: {error}")
        raise SystemExit(0)

    user, password = credentials(args.env)
    secrets = (password, user)

    regions = json.loads(args.regions.read_text(encoding="utf-8"))["regions"]
    if args.limit:
        regions = regions[: args.limit]

    args.cache.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for index, region in enumerate(regions, 1):
        region_id = region.get("regionId")
        centre = region.get("center")
        if not region_id or not centre:
            continue
        ra_deg, dec_deg = float(centre[0]), float(centre[1])
        destination = args.cache / f"{region_id}-{args.band}.fits"
        try:
            if not destination.is_file():
                payload = fetch(cutout_url(ra_deg, dec_deg), user, password)
                # The cutout service answers a miss with an HTML page, not a 404.
                if not payload.startswith(b"SIMPLE"):
                    raise ValueError("no coadd here: the service returned a non-FITS response")
                destination.write_bytes(payload)
            checks = inspect(destination)
            records.append(
                {
                    "regionId": region_id,
                    "tract": region.get("tract"),
                    "center": [ra_deg, dec_deg],
                    "surveyId": "hsc-ssp-pdr2",
                    "band": args.band,
                    "rerun": RERUN,
                    "status": "validated-science-input",
                    "sourcePixelsValidated": True,
                    "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                    "bytes": destination.stat().st_size,
                    **checks,
                }
            )
        except SystemExit:
            raise
        except Exception as error:  # noqa: BLE001 - one region must not end the run
            reason = redact(f"{type(error).__name__}: {error}", secrets)
            failures.append({"regionId": region_id, "reason": reason})
            if destination.is_file() and destination.stat().st_size < 2880:
                destination.unlink()
        if index % 20 == 0:
            print(f"  {index}/{len(regions)} regions, {len(records)} validated")

    payload = {
        "schemaVersion": "layers-hsc-pdr2-v1",
        "generatedAt": utc_now(),
        "surveyId": "hsc-ssp-pdr2",
        "release": "PDR2",
        "rerun": RERUN,
        "band": args.band,
        "cutoutArcmin": SIZE_ARCMIN,
        "regionsAttempted": len(regions),
        "regionsValidated": len(records),
        "regions": records,
        "failures": failures,
        "credentials": {
            "required": True,
            "source": "HSC_USERNAME / HSC_PASSWORD from the environment",
            "serialized": False,
            "why": (
                "PDR2 science coadds need a STARS account. Everything PDR2 serves without one "
                "is HiPS, which carries no calibrated flux and no variance plane, which is why "
                "this survey contributed 162 regions to the goal's footprint count and zero to "
                "reality."
            ),
        },
        "reproducibility": (
            "A third party cannot re-run this fetch without their own PDR2 account. The "
            "per-region SHA-256 checksums let them verify what was fetched even so."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"\nvalidated {len(records)} of {len(regions)} regions")
    if failures:
        print(f"failed {len(failures)}; first few:")
        for item in failures[:5]:
            print(f"  {item['regionId']}: {item['reason'][:90]}")
    print(f"wrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
