# Layers — status and remaining work (2026-08-14)

Written after Codex ran out of credits mid-project. Everything below is read off the
repository, the generated audit files, and the live deployment — not from memory.

- Repo: `lrspeiser/layers` (working copy `Documents/Codex/2026-08-03/lrspeiser-rubin-light-atlas-https-github/work/rubin-light-atlas`)
- Live: https://rubin-light-atlas.vercel.app (production, deployed 2026-08-14 02:48)
- Build + 34 tests: pass
- Founding spec: `.codex/attachments/dea9f968-.../pasted-text.txt` ("Rubin Missing Light Atlas")
- Machine audit of the current objective: `public/data/coverage/goal-audit.json`

---

## 1. Where the project actually is

**The acquisition and inventory objective is finished.** `goal-audit.json` reports
`objectiveAchieved: true` with all 9 gates green:

| Metric | Value |
|---|---|
| Rubin DP2 tracts indexed | 2,191 / 2,191 |
| External datasets registered | 28 |
| Selected acquisition regions | 50 / 50 |
| Regions with validated Rubin pixels | 50 |
| Display products built | 513 (of 685 indexed) |
| Viewer-ready products | 261 |
| **Comparison-ready products** | **0** |

**The difference objective has not started.** That last row is the whole gap. Every one of
the 50 Rubin-vs-reference regions carries the same six blockers
(`public/data/layers/selected-regions/rubin-reference-comparisons.json`):

```
PSF matching · bandpass transfer · background matching ·
flux-unit transfer · resampling covariance · injection/recovery QA
```

Status per region is `display-aligned`, with `scienceClaimAllowed: false`. The site says so
honestly — the tract pages print the blocker list, and the homepage shows
`COMPARISON-READY 0`.

The only measured differences that exist anywhere in the product are **catalog**
differences, not pixel differences: 113 published WISE-W1 vs SPARC stellar-mass
comparisons, of which 2 are classified `noteworthy` (UGC 02885 at 2.54σ, UGC 06917 at
2.14σ) and 149 measurements are `expected`. Those come from published tables, not from
Rubin pixels.

The one place a *pixel* difference is visualized is `/prototype` (UGC 00191, z-band): 8
residual regions with empirical peak significance, explicitly labelled "leads, not
discoveries." That page is the right shape for the whole product — it just runs on one
field out of fifty.

### What the four SPARC pilots proved

| Field | Outcome | Detail |
|---|---|---|
| NGC 0100 | `no-valid-pixels` | Footprint metadata matched, but every intersecting pixel is masked `NO_DATA`. Footprint match ≠ coverage. |
| UGC 00191 | `filter-transfer-blocked` | 56 qualified cells, **1.080 mag** median residual vs 0.08 mag tolerance |
| UGC 00634 | `filter-transfer-blocked` | 37 cells, **0.474 mag** residual |
| UGC 00891 | `filter-transfer-blocked` | 7 cells (below the 20-cell minimum), **0.379 mag** residual |

Astrometry, PSF/sky reconciliation, point-source colour calibration and diffuse recovery
all **pass**. The resolved-galaxy filter transfer is what fails — by 5–13× the tolerance,
on every field. This is the real wall, and it is a physics problem, not a plumbing problem.

---

## 2. Immediate risk: 4½ hours of work exists only on this machine

Last commit is `06bcd01` at 2026-08-13 22:11, in sync with `origin/main`. Everything built
after that — 22:11 → 02:48 — is uncommitted: **120 changed paths**, including

- the entire coverage explorer and homepage (`app/coverage`, `lib/coverage.ts`, `components/CoverageExplorer.tsx`)
- the tract workspace (`app/tract/[tract]`, 6 new components)
- the on-demand Rubin cutout service (`lib/rubin-on-demand.ts`, `app/api/cutout-worker`, `scripts/process-rubin-cutout-queue.mjs`)
- 30+ new pipeline scripts, 7 new test files, and the whole `public/data/coverage` tree

That work **is** live on Vercel (deployed directly, not via git), so production is ahead of
GitHub. If this machine is lost, the deployed site cannot be rebuilt from the repo.

Also local-only: **~36 GB** of retrieved science data (`pipeline/results` 29 GB,
`pipeline/output` 7.1 GB), excluded by `.gitignore`/`.vercelignore`. The fetch scripts and
checksums are reproducible, but re-acquiring is rate-limited (30 SODA requests/min).

**Action A1** — commit and push the 120 paths.
**Action A2** — decide on durable storage for the FITS (LFS, R2/S3, or an accepted "re-fetch
from manifest" policy) and write it down.

---

## 3. What remains — the difference engine

Ordered by what unblocks the most. Each item notes what already exists, so nothing gets
rebuilt from scratch.

### B1. PSF matching at tract scale
- **Exists:** `pipeline/reconcile_image_layers.py` does sky subtraction, registration and
  Gaussian-FWHM PSF matching with a 10% tolerance, and propagates variance through the
  convolution. `audit_layer_registration.py` measures empirical PSF widths from sources.
- **Missing:** it has only ever run on the 3 SPARC pilot fields. It has never been run
  across the 50 tracts.
- **Gap to close first:** the cached Rubin cutouts are `MaskedImage` — IMAGE / VARIANCE /
  MASK / WCS only, **no PSF plane**. Either pull PSF models from the RSP Butler alongside
  the cutouts, or measure empirically from stars per field.
- **Known limitation already recorded in the pilot output:** "The Gaussian PSF model does
  not capture spatially varying PSF wings." For low-surface-brightness work the wings *are*
  the signal, so a scalar Gaussian match will need to become a real kernel match.

### B2. Bandpass transfer — the hard blocker
This is the one that has already failed on every pilot, and it has a second problem that
was not present in the pilots:

- **Only one Rubin band was cached per tract** (42 × r, 5 × i, 2 × g, 1 × z). With a single
  band there is no Rubin colour, so there is no way to fit a colour term. Fixing this means
  re-acquiring at least a second band for the 50 tracts.
- 13 of 50 regions do not even have a same-named reference band.
- **Exists:** `pipeline/audit_filter_response.py` (point-source colour calibration — passes)
  and `audit_extended_source_transfer.py` (resolved-galaxy transfer — fails).
- **Needed:** synthetic photometry through the actual Rubin/Legacy/PS1 filter curves, and a
  per-pixel or per-cell SED-based transfer rather than one global colour term. The recorded
  next action is: *"Add a less fragmented independent image layer or model the Pan-STARRS
  masks and spatial covariance, then run full synthetic photometry and resolved multi-band
  SED checks."*
- **Decision worth making explicitly:** whether every comparison must clear 0.08 mag, or
  whether some difference classes (morphology, extent, detection of a feature that is
  simply absent in the older image) can be published under a looser, separately-stated gate.
  Requiring photometric equivalence for a *"this structure is not in the old image at all"*
  claim may be stricter than the claim needs.

### B3. Background / sky matching
- **Exists:** sky-plane modelling inside `reconcile_image_layers.py`, pilot-only.
- **Missing:** tract-scale application, and a *joint* background model. Legacy and PS1 sky
  subtraction over-subtracts extended low-surface-brightness wings — which is precisely the
  light this atlas is supposed to find. Subtracting each survey's own sky independently
  bakes the answer in.

### B4. Flux-unit transfer
- Five different native units are in play: Rubin nJy, Legacy nanomaggy, PS1 counts, 2MASS
  DN, unWISE Vega nanomaggies. Per-fetcher conversions exist; there is no single audited
  zeropoint chain with propagated uncertainty.

### B5. Resampling covariance
- Referenced in 8 places across the pipeline as a caveat; **modelled nowhere.** Reprojection
  correlates neighbouring pixels, so the per-pixel variance planes understate the real
  uncertainty and every residual significance is inflated. The prototype's ">99×" empirical
  peaks are the visible symptom of this.
- **Exists as a workaround:** `validate_diffuse_recovery.py` derives thresholds from the
  empirical distribution of identical fits at blank sky positions, which absorbs covariance
  without modelling it. That approach is defensible and should probably be the standard.

### B6. Injection / recovery QA
- **Exists and works:** `validate_diffuse_recovery.py` injects exponential sources into the
  real reconciled images and measures what comes back. Pilot-only.
- **Missing:** running it per tract and band, and emitting the per-feature fields the
  founding spec calls for — detection probability, false-positive probability, limiting
  surface brightness, sky-model sensitivity, human-review status.
- This is the credibility layer. Without it every residual is arguable as an artifact.

---

## 4. What remains — the science products (nothing built yet)

These are in the founding spec and have **zero implementation**. The SPARC profiles on the
site today are published catalogue values; none are derived from Rubin pixels.

1. **Radial surface-brightness profiles measured from Rubin pixels**, per band, with
   uncertainty — the input to everything else.
2. **Outer detectable radius**, and its comparison against the legacy image. This is the
   headline claim ("Rubin detects the disk N% farther out").
3. **Δg_bar(r) = g_bar,Rubin(r) − g_bar,SPARC(r)** — the highest-value derivative in the
   spec, and the direct link to the dark-matter/MOND question.
4. Morphology set: inclination, position angle, axis ratio, isophote twisting, asymmetry,
   bar/spiral strength, photometric-vs-kinematic centre offset.
5. Faint-feature catalogue: streams, shells, plumes, satellites, diffuse-halo fraction.
6. **Difference catalogue** — one ranked, machine-readable record per detected difference.
   The schema for this already exists and works: the WISE-vs-SPARC comparisons carry
   value / statistical uncertainty / systematic uncertainty / expected range /
   significance σ / classification / caveats / provenance hashes. Reuse it verbatim for
   pixel differences.
7. **Legacy discrepancy cards** — the auto-generated plain-language summary per object.
   This is the artifact the spec identifies as the thing people actually quote.

---

## 5. What remains — showing it on the Vercel site

### What already works
- `/` coverage explorer: 2,191 tracts plotted, filterable by category and by overlap/pixel
  state, 28-dataset registry with per-survey overlap and pixel counts.
- `/tract/[tract]`: swipe viewer, valid-coverage mask mode, position-overlay mode, the
  common-grid layer stack, the "other cached products" shelf, and the exact-intersection
  list. It prints the six blockers inline — honest, and already the right place to show
  progress against them.
- `/prototype`: the difference view, for one field. Zoom/pan, SPARC region pin, ranked
  signal candidates with "what it might mean" / "explore next", cross-linked to viewer
  position.
- `/pilots`, `/workspace`, `/coverage` API, on-demand Rubin cutout worker (HiPS-backed,
  8 tracts cached, scheduled scanner running every 5 min).

### What needs building
1. **A residual raster.** The swipe shows two images; it never shows the difference. Needs
   a signed difference layer with a diverging colormap and a per-pixel significance layer,
   as a mode in the existing `TractImageSwipe` — not a new page.
2. **A site-wide differences index.** Today `COMPARISON-READY 0` is a dead end with no
   visible path. This should become the primary landing surface once B1–B6 clear: ranked by
   significance, filterable by science family and by which gates a product has passed.
3. **Gate progress as UI, not prose.** Every region already carries its
   `comparisonBlockers` array. Render it as a six-step progress indicator per product so a
   visitor can see how close a field is, and so *your own* progress is visible at a glance.
4. **Per-family difference views.** Gas, lensing, X-ray and time-domain products are
   display-only today (`viewerReady` but not aligned into the swipe). Optical is the only
   family with a real comparison path.
5. **Profile and Δg_bar plots** on galaxy pages, once §4 exists.
6. **Discrepancy cards**, rendered per object and shareable — the social/press surface.

---

## 6. Suggested order

1. **A1/A2** — push the 120 uncommitted paths; decide FITS storage. *(blocking, small)*
2. **B2 re-acquisition** — pull a second Rubin band for the 50 tracts, plus PSF models for
   B1. Rate-limited, so start it early and let it run in the background.
3. **B1 + B3 + B4 at tract scale** — run the existing pilot machinery over the 50 regions.
   This is mostly wiring code that already works, and it converts
   `display-aligned → PSF/sky/flux-matched` for all 50.
4. **B6** — run injection/recovery per tract to get real detection limits. This makes any
   residual defensible.
5. **B5** — adopt the empirical blank-field threshold as the standard significance measure.
6. **B2 proper** — synthetic photometry and SED-based transfer. Hardest; also the one that
   decides whether photometric difference claims are possible at all. Resolve the gate
   question in B2 before spending heavily here.
7. **§4 + §5** — profiles, difference catalogue, residual raster, differences index,
   discrepancy cards.

Steps 3–4 are the highest ratio of value to remaining effort: the code exists, it has been
validated on 3 fields, and running it across 50 tracts is what turns "0 comparison-ready"
into a real number.
