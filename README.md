# Layers

Layers is a scientific comparison workspace for discovering what changes when different datasets observe the same region of space. Rubin, SPARC, Spitzer, HSC, Legacy Survey, Pan-STARRS, GALEX, WISE, H I surveys, lensing maps, catalogs, and simulations can all be represented as typed layers tied to a reproducible sky target.

The product deliberately separates four things:

1. acquired evidence and provenance;
2. measured differences with statistical and systematic uncertainty;
3. model-dependent scientific inference;
4. assumptions worth rechecking and proposed follow-up.

A difference is an observation, not an automatic claim that prior science was wrong.

## Current pilot

The first catalog contains the complete 175-object SPARC sample and an authenticated Rubin DP2 coverage audit. Four SPARC fields intersect DP2 footprint metadata, but local calibrated-pixel validation finds:

- usable Rubin mosaics for UGC 00191, UGC 00634, and UGC 00891;
- a footprint false positive for NGC 0100: the intersecting pixels are all masked `NO_DATA`;
- 48 full-resolution Legacy Survey DR10 FITS tiles, mosaicked into three local image layers with science, inverse variance, masks, WCS, URLs, and SHA-256 provenance;
- two full Pan-STARRS1 skycells for UGC 00891, including the complete science, variance, and bitmask products, calibrated to nJy and mosaicked locally;
- registration audits that pass the declared 0.30 arcsec p95 threshold for the two Rubin/Legacy fields (0.220 and 0.203 arcsec) and correctly fail the Rubin/Pan-STARRS field (0.403 arcsec);
- explicit, checksum-backed pilot outcomes for all four fields: NGC 0100 fails pixel coverage, UGC 00891 fails registration, and UGC 00191/UGC 00634 pass astrometry plus PSF/sky and diffuse-recovery checks but fail resolved-galaxy filter transfer;
- no published astrophysical cross-survey differences yet. The site ranks the two filter-transfer assumptions for follow-up while keeping outer-light, stellar-mass, baryonic-mass, lensing, and acceleration claims blocked.

No image or statistic is fabricated, substituted, or reused. Image sliders activate only when two image layers pass the complete comparison gate. Profiles, catalogs, spectra, and maps retain their appropriate plot, table, or overlay representation.

## Architecture

- `lib/layers.ts` defines Target, Layer, Registration, Comparison, Difference Measurement, Inference, and Assumption Audit entities.
- `public/data/layers-catalog.json` is the website and API catalog.
- `/api/catalog` and `/api/targets/:id` expose the same records used by the interface.
- `pipeline/build_layers_catalog.py` rebuilds the public metadata catalog.
- `pipeline/build_local_layer_store.py` builds `pipeline/output/layers.sqlite`, including an R-tree sky index over locally stored files and datasets.
- `pipeline/query_local_layers.py` performs read-only target or coordinate queries against that local store and returns product paths, hashes, coverage, and QA records.
- `pipeline/validate_layers_catalog.py` enforces the generic catalog and publication invariants.
- `pipeline/query_dp2_sia.py` and `pipeline/download_dp2_matches.py` implement quota-aware Rubin discovery and calibrated local ingestion.
- `pipeline/fetch_legacy_survey.py` and `pipeline/fetch_panstarrs.py` acquire reproducible full-resolution reference image layers and support planes.
- `pipeline/audit_layer_registration.py` measures common coverage, WCS agreement, source residuals, empirical PSF widths, and sky models without marking unapplied operations as passed.
- `pipeline/build_visual_prototype.py` makes local-only display stretches and a real reference-coverage mask for `/prototype`; calibrated FITS remain the analysis inputs and authenticated Rubin pixels never enter public deployment artifacts.

See `pipeline/README.md` for the Rubin/SPARC ingest and scientific publication workflow.

## Run and validate

Requires Node.js 22.13 or newer and Python with the pipeline dependencies.

```bash
npm install
npm run dev
npm test
npm run build:vercel
python pipeline/validate_layers_catalog.py
python pipeline/build_local_layer_store.py
python pipeline/build_visual_prototype.py
```

`npm run build` produces the existing Sites-compatible build. `npm run build:vercel` produces the preferred Vercel Next.js build. Restricted Rubin pixels and credentials remain ignored local artifacts and are never included in a public deployment.

Open `/prototype` while the development server is running to review the full-field marker, zoom/pan behavior, and the aligned Rubin/Legacy reveal. The route intentionally shows a private-data notice on public deployments where authenticated Rubin pixels are absent.

This is an independent prototype and is not affiliated with Rubin Observatory or SPARC.
