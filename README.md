# Rubin Missing Light Atlas

A manifest-gated atlas for measuring faint visible matter around nearby galaxies and quantifying how Rubin changes older measurements.

The website never substitutes a shared demonstration image for a target. Each comparison activates only when `public/atlas/<object-id>/manifest.json` identifies unique Rubin EDP2 pixels, a registered legacy image, the shared WCS/PSF/sky QA, source checksums, and Butler dataset UUIDs. Unverified targets remain explicit empty states, and no illustrative discrepancy statistics are shown.

## What is included

- Compact, data-first target workspace
- One permanent route per pilot galaxy
- Manifest-driven, per-galaxy Rubin/legacy sliders
- Per-band image controls that expose only ingested products
- “Expected,” “above expected,” and “large” differences tied to uncertainty and σ
- Authenticated EDP2 coadd export code and release-wide duplicate-image checks
- Responsive layouts for desktop and mobile

See [`pipeline/README.md`](pipeline/README.md) for the complete RSP ingest, registration, verification, and publication flow.

## Run locally

Requires Node.js 22.13 or newer.

```bash
npm install
npm run dev
```

Build and test:

```bash
npm run build
npm test
python pipeline/validate_release.py
```

The project uses vinext for Cloudflare-compatible output. Sites infrastructure is declared in `.openai/hosting.json`.

## Data status

Early DP2 was released July 27, 2026 with deep coadds and catalogs. Access currently requires Rubin data rights through the Rubin Science Platform. This public repository contains the pipeline and target contract, but no restricted Rubin pixels or credentials.

This is an independent prototype and is not affiliated with Rubin Observatory.
