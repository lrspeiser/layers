# Rubin Missing Light Atlas

A public-facing prototype for a measured atlas of faint visible matter around nearby galaxies. The interface demonstrates how Rubin-versus-legacy images, radial measurements, baryonic-acceleration revisions, injection–recovery evidence, and machine-readable object packages can live together in one durable record.

## What is included

- Searchable SPARC audit queue with analysis status and confidence
- Interactive legacy-versus-Rubin comparison viewer
- Permanent object routes such as `/galaxy/ngc-300`
- Legacy discrepancy cards and radial profile visualization
- Injection–recovery credibility layer
- Downloadable demonstration package and social preview metadata
- Responsive layouts for desktop and mobile

All numerical results shown in the interface are marked as illustrative. They are product examples, not published scientific measurements.

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
```

The project uses vinext for Cloudflare-compatible output. Optional Sites infrastructure is declared in `.openai/hosting.json`.

## Image credit

The Virgo Cluster image is from the NSF–DOE Vera C. Rubin Observatory / NOIRLab commissioning release.
