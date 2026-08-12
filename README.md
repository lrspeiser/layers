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
- no published cross-survey comparisons yet, because legacy registration, PSF/sky reconciliation, uncertainty analysis, and validation are still required.

No image or statistic is fabricated, substituted, or reused. Image sliders activate only when two image layers pass the complete comparison gate. Profiles, catalogs, spectra, and maps retain their appropriate plot, table, or overlay representation.

## Architecture

- `lib/layers.ts` defines Target, Layer, Registration, Comparison, Difference Measurement, Inference, and Assumption Audit entities.
- `public/data/layers-catalog.json` is the website and API catalog.
- `/api/catalog` and `/api/targets/:id` expose the same records used by the interface.
- `pipeline/build_layers_catalog.py` rebuilds the public metadata catalog.
- `pipeline/build_local_layer_store.py` builds `pipeline/output/layers.sqlite`, including an R-tree sky index over locally stored files and datasets.
- `pipeline/validate_layers_catalog.py` enforces the generic catalog and publication invariants.
- `pipeline/query_dp2_sia.py` and `pipeline/download_dp2_matches.py` implement quota-aware Rubin discovery and calibrated local ingestion.

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
```

`npm run build` produces the existing Sites-compatible build. `npm run build:vercel` produces the preferred Vercel Next.js build. Restricted Rubin pixels and credentials remain ignored local artifacts and are never included in a public deployment.

This is an independent prototype and is not affiliated with Rubin Observatory or SPARC.
