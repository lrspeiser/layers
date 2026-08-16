# Machine access: what to expose, and the one decision that blocks the rest

Written 2026-08-15, in answer to "should we expose our analysis as API calls that
their own code can use?"

Short answer: **yes, but as IVOA protocols rather than a bespoke REST API — and
one class of data cannot be exposed at all until you make a call that is yours,
not mine, to make.**

## The blocker, found before building anything

[DATA_STORAGE.md](DATA_STORAGE.md) states the policy in its first line:

> Retrieved and **derived** science bytes are never committed to git and never
> mirrored to public object storage.

The reason is that Rubin DP2 pixels require data rights (US and Chilean
researchers and students), so redistributing them breaches the access condition.

The source catalogue is a derived science product. Its `rubin_flux_njy` column is
a measurement of restricted pixels. Under the policy as written, **it cannot be
served publicly**, and a TAP endpoint over it would be exactly the "public object
storage" the policy rules out.

That is a genuine question about Rubin's data-rights terms rather than an
engineering one, and it has three defensible answers:

1. **Serve nothing derived from Rubin pixels.** Safest. Publishes the reference
   half and the aggregate results, which are already public in this repository.
2. **Serve the catalogue behind data-rights authentication.** Matches how the
   Rubin Science Platform itself works. Most useful to the people who can already
   access DP2, which is the audience for a Rubin comparison anyway.
3. **Confirm that derived catalogues are not restricted and serve it openly.**
   Requires reading Rubin's actual DP2 data-rights policy, not inferring it.

Until one of those is chosen, the catalogue stays local. `published: false` is
recorded in `source-catalogue.json` and a test asserts it.

## What is safe to expose today

Everything already committed under `public/data/layers/` is public by
construction: aggregate statistics, attribution findings, difference-peak
positions, candidate positions, coverage. None of it reproduces restricted
pixels, and the repo-wide leak test keeps local paths and credentials out of it.

That is enough for a genuinely useful machine interface:

| endpoint | protocol | serves |
|---|---|---|
| cone search | IVOA **SCS** | difference peaks, register candidates, confirmed positions |
| coverage | **MOC** | which sky actually has data, as a standard mask |
| products | **ObsCore** | what exists per region, discoverable in registries |
| bulk | VOTable / Parquet | the whole public table in one request |

## Why IVOA and not REST

The five JSON routes this site already has (`/api/coverage`, `/api/catalog`, …)
are fine for the site and useless to anyone else: no astronomer will write a
client for a bespoke schema. They already have clients — TOPCAT, Aladin, `pyvo`,
`astroquery` — and those speak IVOA.

A cone search is a GET returning a VOTable. That single endpoint makes the
results loadable in TOPCAT and Aladin with no code, and queryable from Python
with three lines:

```python
from pyvo.dal import SCSService
service = SCSService("https://rubin-light-atlas.vercel.app/api/scs")
table = service.search(pos=(53.08, -27.49), radius=0.01).to_table()
```

A **MOC** deserves particular emphasis. "Where do you actually have data" is the
question this project has answered the hard way seven times — footprint overlap
overstated reality in every one of them. A MOC is the machine-readable form of
that answer, and publishing one would let someone else avoid the same mistake
without repeating the measurements.

## What a full TAP service would additionally need

TAP means ADQL, which means a query parser and an execution engine over the
table. That is a real service, not a route: either a hosted database with a TAP
front end (CADC's `youcat`, or a Postgres with `pgSphere` behind `vollt`), or
registration with an existing provider. Worth doing once the catalogue's access
question is settled, and not before — building a query service for a table that
cannot be served would be the wrong order.
