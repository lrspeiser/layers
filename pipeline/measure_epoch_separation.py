"""How many years apart are the images we are calling different?

The anomaly scanner lists two boring explanations for a residual: proximity to a
mask edge, and sitting on a bright source where Gaussian PSF matching leaves wing
residuals. Both are about the images. Neither is about *time*, and the images are
not contemporaneous.

Rubin's effective coadd epoch is fitted from Gaia proper motions at **2025.5**,
with 0.37 yr of field-to-field scatter -- which is itself the evidence that the
fit measures an epoch rather than absorbing some other systematic, and it agrees
with DP2's stated April 2025 to January 2026 observing window. The references
were taken years earlier. So every difference map in this project compares sky
separated by roughly a decade.

That has three consequences a residual cannot distinguish from a discovery
without being told about them:

  proper motion   a star at 50 mas/yr moves 0.5 arcsec over 10 years, comparable
                  to the PSF width, and a shifted point source differences into a
                  dipole -- bright on one side, dark on the other
  variability     an intrinsically variable star simply is a different brightness
                  in the two epochs, and that is a real change on the sky rather
                  than an instrumental one
  moving objects  an asteroid is present in one epoch and absent in the other

None of these is "Rubin disagrees with Legacy". All of them look exactly like it.

This records the separations so the scanner and the site can say so. The
reference epochs are the published observing spans of each survey, quoted as
ranges because a coadd has no single date -- which is also why the cutouts carry
no usable DATE-OBS: Rubin's says when we fetched the file, HSC's is a J2000
placeholder, and Legacy's has no date keyword at all.
"""

from __future__ import annotations

import argparse
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SELECTED = ROOT / "public/data/layers/selected-regions"
GAIA = ROOT / "public/data/layers/gaia-crossmatch/comparison.json"
OUTPUT = SELECTED / "epoch-separation.json"

# Published observing spans. A coadd stacks many nights, so these are ranges and
# the midpoint is what a difference image effectively sees.
REFERENCE_EPOCHS = {
    "legacy-surveys-dr10": {
        "label": "Legacy Surveys DR10",
        "spanFrom": 2014.0,
        "spanTo": 2019.0,
        "basis": "DECaLS, BASS and MzLS observing seasons feeding DR10",
    },
    "panstarrs-dr2": {
        "label": "Pan-STARRS1 DR2",
        "spanFrom": 2010.0,
        "spanTo": 2014.0,
        "basis": "PS1 3-pi survey observing span",
    },
    "hsc-ssp-pdr2": {
        "label": "HSC-SSP PDR2",
        "spanFrom": 2014.0,
        "spanTo": 2019.0,
        "basis": "HSC-SSP wide observing seasons through PDR2",
    },
    "des-dr2": {
        "label": "DES DR2",
        "spanFrom": 2013.0,
        "spanTo": 2019.0,
        "basis": "DES six-year survey",
    },
}

# A proper motion large enough to move a source by an appreciable fraction of the
# PSF over the epoch gap. Rubin's pixels are 0.2 arcsec.
PSF_ARCSEC = 0.8


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = parser.parse_args()

    gaia = json.loads(GAIA.read_text(encoding="utf-8")) if GAIA.is_file() else {}
    consistency = gaia.get("epochConsistency") or {}
    rubin_epoch = consistency.get("medianFittedJyear")
    rubin_scatter = consistency.get("scatterYears")
    if not rubin_epoch:
        raise SystemExit("no fitted Rubin epoch; run build_gaia_crossmatch.py first")

    pairs = {}
    for survey, info in REFERENCE_EPOCHS.items():
        midpoint = (info["spanFrom"] + info["spanTo"]) / 2
        gap = rubin_epoch - midpoint
        # Proper motion that shifts a source by half the PSF over this gap.
        mas_per_year = (PSF_ARCSEC / 2) * 1000 / gap if gap else None
        pairs[survey] = {
            **info,
            "referenceMidpointJyear": midpoint,
            "yearsFromRubin": round(gap, 1),
            "properMotionMovingHalfAPsfMasPerYear": round(mas_per_year, 1) if mas_per_year else None,
        }

    payload = {
        "schemaVersion": "layers-epoch-separation-v1",
        "question": (
            "The difference maps compare Rubin against surveys taken years earlier. How many "
            "years, and what can that alone produce?"
        ),
        "rubinEpochJyear": rubin_epoch,
        "rubinEpochScatterYears": rubin_scatter,
        "rubinEpochBasis": (
            "Fitted from Gaia proper motions rather than read from a header. The cutouts carry no "
            "usable observation date: Rubin's DATE is when the file was written, HSC's DATE-OBS is "
            "a J2000 placeholder, and the Legacy cutouts have no date keyword at all. The "
            "field-to-field scatter of 0.37 yr is the evidence the fit measures an epoch, and it "
            "agrees with DP2's stated April 2025 to January 2026 window."
        ),
        "pairs": pairs,
        "whatTimeAloneProduces": [
            {
                "cause": "proper motion",
                "effect": (
                    "A moving star is in two places. Differencing a shifted point source gives a "
                    "dipole -- bright on one side, dark on the other -- which is a distinctive "
                    "signature and is not a change in brightness."
                ),
            },
            {
                "cause": "variability",
                "effect": (
                    "An intrinsically variable star is genuinely a different brightness in the "
                    "two epochs. This is a real change on the sky and not an instrumental "
                    "artefact, but it is also not evidence that the two surveys disagree."
                ),
            },
            {
                "cause": "moving objects",
                "effect": "An asteroid is present in one epoch and absent in the other.",
            },
        ],
        "consequence": (
            "The anomaly scanner's boring explanations are proximity to a mask edge and "
            "wing residuals on a bright source. Neither covers time. A variable star, a "
            "high-proper-motion star or an asteroid would currently be ranked as having no "
            "boring explanation, which is the highest-interest category. Nothing in the "
            "candidate lists should be read as a discovery until an epoch check is applied."
        ),
        "reproduce": "python pipeline/measure_epoch_separation.py",
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Rubin epoch {rubin_epoch} (scatter {rubin_scatter} yr)\n")
    print(f"{'reference':22s} {'span':>14s} {'years from Rubin':>17s} {'PM moving half a PSF':>21s}")
    for survey, info in pairs.items():
        span = f"{info['spanFrom']:.0f}-{info['spanTo']:.0f}"
        print(f"{survey:22s} {span:>14s} {info['yearsFromRubin']:17.1f} "
              f"{info['properMotionMovingHalfAPsfMasPerYear']:19.1f} mas/yr")
    print(f"\nwrote {args.output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
