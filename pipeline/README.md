# EDP2 per-galaxy ingest

The atlas does not download or subdivide one enormous release image. Early Data Preview 2 is a collection of tract/patch `deep_coadd` datasets. This pipeline queries only the coadd patches that overlap each predeclared galaxy field, mosaics them onto a common target-centered WCS, and records the Butler dataset UUID for every input.

The July 27, 2026 early release contains deep coadds and catalogs. Raw, visit, template, and difference images are planned for the complete DP2 release later in 2026. EDP2 access currently requires Rubin data rights and an authenticated Rubin Science Platform session.

## 0. Fetch the public SPARC and Spitzer side

This step does not require Rubin credentials:

```bash
python pipeline/fetch_public_legacy.py --cache pipeline/cache
python pipeline/audit_overlap.py --legacy-cache pipeline/cache
```

`fetch_public_legacy.py` downloads the official SPARC sample table, surface-brightness profiles, rotation/mass models, and published diagnostic plots. It then queries NASA/IPAC IRSA's SIAv2 service for real Spitzer Enhanced Imaging Products at each target coordinate, downloads the IRAC channel 1 science/uncertainty/coverage mosaics, and makes target-centered FITS cutouts. The cache is ignored by Git; every source URL and SHA-256 digest is recorded.

The SPARC mass-model PNG is a plot, not a sky image. It must never be put in an image slider or subtracted from Rubin pixels. The registered legacy image comes from the Spitzer FITS mosaic; the SPARC profile and rotation curve are separate measurement references.

`audit_overlap.py` checks whether the SPARC radial profile lies within the declared field and whether valid Spitzer pixels exist at its outer accepted radius. It consumes the authenticated EDP2 coverage summary when one exists. Until that summary exists, it reports the precise Rubin blocker instead of inferring footprint coverage.

## 1. Export Rubin data inside the RSP

Open an RSP Notebook Aspect session, clone or upload this repository, and run:

```bash
python -m pip install reproject pillow
python pipeline/edp2_export.py
```

For one target:

```bash
python pipeline/edp2_export.py --only ngc-300
```

The default output pixel scale is 0.4 arcsec/pixel to keep the large pilot fields tractable. Select `--pixel-scale 0.2` intentionally if native-like sampling is required and memory permits. The recorded WCS and scale always travel with the product.

The script:

1. opens `Butler("dp2", collections=["dp2"])`;
2. queries `deep_coadd` by band and overlapping sky points;
3. de-duplicates patch dataset references;
4. reprojects image, variance, and mask planes to one WCS;
5. combines overlaps using inverse-variance weighting;
6. writes per-band FITS/PNG products and `edp2_provenance.json`;
7. writes `coverage-summary.json`, including bands with no EDP2 coverage.

No password or token is read from a file, command-line flag, or repository variable. Authentication comes from the active RSP session.

## 2. Register a named legacy product

Obtain the scientifically appropriate older-survey image for the same field and document its release and source identifier. Then:

- calibrate it to a compatible photometric quantity or state the conversion used;
- reproject it to the exact Rubin output WCS and array shape;
- reconcile filter response before interpreting a flux difference;
- convolve the sharper image to the broader PSF;
- fit and reconcile the sky background outside the measurement region;
- propagate masks and variance;
- measure registration residuals from matched, unsaturated sources;
- create a web preview from the matched arrays, not from an unrelated survey JPEG.

Literal pixel subtraction before these operations is not a valid cross-survey measurement.

Record the results using `registration-qa.example.json`. Boolean values are assertions backed by the analyst's QA artifacts, not switches that perform the work. The residual threshold must be declared before publication.

## 3. Publish only a verified object

```bash
python pipeline/publish_verified.py \
  --edp2 pipeline/output/ngc-300 \
  --qa /path/to/ngc-300-registration-qa.json

python pipeline/validate_release.py
```

`publish_verified.py` refuses publication unless WCS, PSF, sky, and residual checks pass. It also refuses byte-identical Rubin/legacy previews and a Rubin preview already used by another object. It copies the accepted assets into `public/atlas/<slug>/` and writes the manifest consumed by the website.

`validate_release.py` repeats the release-wide checks and detects cross-object image reuse. Run it in CI before every deployment.

## 4. Interpret differences

Each published metric must contain:

- the change itself;
- measurement uncertainty;
- a declared expected cross-survey range;
- the resulting significance in σ;
- one label: `expected` (<2σ), `above` (2–3σ), or `large` (≥3σ).

Those thresholds are presentation categories, not a discovery standard. Injection–recovery completeness, sky-model sensitivity, multiple-testing control, and independent review still belong in the scientific record.

## Official references

- [EDP2 documentation](https://dp2.lsst.io/)
- [EDP2 data products](https://dp2.lsst.io/products/index.html)
- [Deep coadd image product](https://dp2.lsst.io/products/images/deep_coadd.html)
- [Data access through the Rubin Science Platform](https://dp2.lsst.io/access/index.html)
- [EDP2 observing footprint](https://dp2.lsst.io/overview/observations.html)
- [Official deep-coadd tutorial notebook](https://github.com/lsst/tutorial-notebooks/blob/main/DP2/200_Data_products/202_Images/202_1_Deep_coadds.ipynb)
