# Machine access: what is exposed, and how to use it

Written 2026-08-15, in answer to "should we expose our analysis as API calls that
their own code can use?"

Short answer: **yes, as IVOA protocols rather than a bespoke REST API.** That is
now built and live.

## Resolved 2026-08-15

The project holds Rubin data rights and has chosen to publish. The catalogue is
live: Parquet and gzipped VOTable at `/data/catalogue/`, spatially tiled behind
an IVOA cone search at `/api/scs`, with a column dictionary and the completeness
and false-positive rates on `/data`.

Option 3 below is what was taken. The pixels themselves are still not
redistributed — that part of the storage policy is unchanged, and rests on
archive duplication and 36 GB rather than on rights.

## The blocker as it stood, kept for the record

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

Option 3 was chosen. `catalogue-release.json` records `published: true`, and the
tests now assert the opposite of what they asserted before: that the files exist,
that the cone search serves them, and that the column dictionary explains which
significance to cut on.

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
registration with an existing provider.

Now that the catalogue is published, this is the next genuine step, and the
choice is between hosting one and registering the existing files with a provider
who already runs one. Cone search covers "what is near this position", which is
the common case; TAP would add "every source with departure_significance above 5
and rubin_mag_ab brighter than 22", which cone search cannot express.
