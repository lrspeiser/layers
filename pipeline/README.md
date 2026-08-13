# EDP2 per-galaxy ingest

This EDP2 adapter now feeds the survey-neutral **Layers** product. Rubin is one typed image layer; SPARC is a profile/rotation-curve layer. New surveys should produce the same generic target/layer/provenance records without adding survey-specific UI code.

After mosaicking, rebuild the public metadata catalog and local query store:

```bash
python pipeline/build_layers_catalog.py
python pipeline/validate_layers_catalog.py
python pipeline/build_local_layer_store.py
```

`pipeline/output/layers.sqlite` indexes all 175 targets, sky bounding boxes, layers, dataset identifiers, local FITS paths and hashes, per-band products, and future comparisons. The `target_sky_index` R-tree supports local coordinate intersection queries. Raw pixels remain local and ignored; the public catalog contains metadata only.

Query the store without opening or mutating it:

```bash
python pipeline/query_local_layers.py --target UGC00891
python pipeline/query_local_layers.py --ra 20.32875 --dec 12.41194 --radius-arcmin 2 --layer panstarrs-dr1-stack
```

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

## 0.5. Audit all 175 SPARC coordinates against DP2

Create a local, Git-ignored `.env` containing an RSP token with `read:image` scope:

```dotenv
RUBIN_RSP_TOKEN=your-token
```

Then run the resumable SIAv2 audit from a local machine:

```bash
python pipeline/query_dp2_sia.py
```

The script obtains the 175 coordinate-resolved SPARC paper objects from SIMBAD, queries only `lsst.deep_coadd` metadata, caches every successful VOTable, and writes `pipeline/results/dp2-sparc-coverage.json`. It is sequential and defaults to 55 SIA requests per minute, below the account limit of 70. Its hard cap is 60 requests per minute. Restarting the command reuses successful cached responses; use `--refresh` only when a new Rubin release makes a repeat audit intentional.

Pixel downloads are a later, match-only step. Keep them sequential and at or below 30 cutout requests per minute, below the account limit of 35.

For the actual matches, download calibrated full patches through one batched DataLink request and mosaic them locally:

```bash
python pipeline/download_dp2_matches.py
```

This route uses no SODA cutout calls. The downloader follows DataLink's `#this` links to the immutable FITS patches, verifies each file, records SHA-256 digests and publisher dataset identifiers, and reprojects the image/variance/mask planes to one 0.4 arcsec/pixel target WCS. A maximum of four object-storage downloads run concurrently by default; use `--workers 1` for a strictly serial transfer.

## 0.75. Acquire full-resolution reference image layers

Acquire Legacy Survey DR10 data on the exact Rubin target WCS:

```bash
python pipeline/fetch_legacy_survey.py
```

The Legacy cutout service currently caps responses at 512 pixels. The adapter therefore downloads a deterministic overlapping tile grid, retains every original FITS response and hash, and mosaics the science and inverse-variance planes locally. For the three usable Rubin targets this is 48 retained tiles. Because DR10 coadds store integrated nanomaggies per native 0.262-arcsec pixel, the adapter explicitly scales flux by output/native pixel area and inverse variance by the inverse square of that factor.

For a Rubin field without a sufficiently covered Legacy band, acquire Pan-STARRS1:

```bash
python pipeline/fetch_panstarrs.py
```

This adapter queries a 3 by 3 grid across the declared field, downloads every unique full DR1 skycell selected by the official image-list service, and retains the complete unconvolved stack, variance, and bitmask files. It converts the full-stack asinh encoding back to linear flux, applies the documented per-skycell AB calibration, and mosaics to nJy on the Rubin WCS. Overlapping skycells are not coadded because they reuse observations; the adapter selects the lower-variance unmasked sample instead.

Audit registration readiness:

```bash
python pipeline/audit_layer_registration.py
```

The audit chooses the best-covered common band, measures matched-source residuals, common valid coverage, empirical PSF widths, and robust sky planes. It leaves `psfMatched`, `filterMatched`, and `skyMatched` false because measuring those quantities does not perform the matching operation. Its predeclared astrometric publication threshold is 0.30 arcsec p95.

Create the calibrated reconciliation intermediates:

```bash
python pipeline/reconcile_image_layers.py
```

This stage applies the measured translational astrometric correction, subtracts independently fitted robust sky planes, convolves both images and their variance planes to a common Gaussian PSF target, erodes masks across every convolution kernel, and writes one checksum-protected multi-extension `matched-pair.fits` per passing target. The file contains both matched images, both variance planes, the common mask, and a QA-only difference plane.

The stage intentionally leaves `filterResponse.matched` false and `quantitativeDifferenceAllowed` false. Similar band names are not proof of equal throughput; a documented color transformation or synthetic-photometry model, correlated-noise treatment, and injection/recovery must pass before the QA difference plane can be interpreted as missing light. A target whose astrometric residual exceeds 0.30 arcsec p95 is recorded as blocked rather than silently warped into compliance.

Constrain the field-specific stellar color term with held-out stars:

```bash
python pipeline/audit_filter_response.py
```

The audit fits `Rubin z - Legacy z` as a function of Legacy `r-z`, uses five spatial cross-validation folds, bootstraps the coefficients, and enforces predeclared sample, color-span, and held-out-scatter thresholds. A pass validates point-source calibration only. Extended-source color transfer and injection/recovery remain mandatory.

Every image reprojection conserves integrated flux with the target/source pixel-area ratio, while variance uses its square. Run the synthetic regression independently with:

```bash
python pipeline/test_flux_conservation.py
```

Measure the empirical diffuse-source limit on the real matched fields:

```bash
python pipeline/validate_diffuse_recovery.py
```

This injects deterministic, PSF-convolved exponential sources with 3, 6, 12, and 24 arcsec effective radii into blank common-mask positions. A local plane and source amplitude are fit simultaneously. Detection thresholds come from identical fits at blank positions, so confusion, resampling covariance, sky residuals, and artifacts contribute to the limit. The recorded 90% recovery boundary requires both detection and flux recovery within 25%; it validates these smooth profiles only and does not prove that streams, shells, cirrus, or filter-dependent galaxy structure are recoverable.

All acquired pixels, support planes, derived mosaics, manifests, and the SQLite store live under ignored `pipeline/output/`. Only metadata suitable for public release is copied into the catalog.

After authenticated/public acquisition products exist, reproduce every science QA stage, catalog, local database, public comparison package, and regression check with:

```bash
python pipeline/run_science_release.py
```

The command stops at the first failed stage and records the commands, outputs, checksums, and final status in the ignored local release manifest. Acquisition remains a separate cached step so a validation rerun does not consume Rubin API quota or redownload hundreds of megabytes of retained source products.

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

For an early visual review using the acquired local FITS products:

```bash
python pipeline/build_visual_prototype.py
```

This writes ignored display assets for the site's `/prototype` route: a Rubin field, a Legacy Survey view on the identical WCS, and an alpha mask encoding actual Legacy valid pixels. These are visual stretches only. They are excluded from Git and deployment uploads, and must not be used as quantitative inputs.

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
