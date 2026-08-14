# Difference engine — status after the 2026-08-14 session

Supersedes the "what remains" list in [REMAINING_WORK_2026-08-14.md](REMAINING_WORK_2026-08-14.md)
for the pixel-comparison path. That document's inventory of the site and the
science-product backlog still stands.

Starting point: all 50 selected DP2 regions were `display-aligned` with six
comparison blockers each, and `comparisonReadyProducts: 0`.

**`comparisonReady` is still 0, deliberately.** What changed is that three of the
six blockers are now measured rather than assumed, one turned out to be a real
bug that was silently corrupting every quantitative number, and the blocker that
killed all four SPARC pilots can now be tested for the first time.

---

## 1. The flux-unit bug

This is the most consequential finding of the session, and it invalidated any
quantitative use of the 50-tract pixels before now.

Two independent pixel-area factors were being dropped:

| Plane | Cause | Factor |
|---|---|---|
| Rubin | `build_selected_region_comparisons.py` reprojects with `reproject_interp`, which preserves **surface brightness**, not total flux. Rubin native is 0.2″/px, the display grid is 0.4″/px. | ×4 |
| Legacy | The viewer cutout service rewrites the output WCS when `pixscale=` is requested but preserves the 0.262″ coadd **values**. | ×2.331 |

Measured directly rather than assumed: summing the native Rubin mosaic and the
reprojected plane over the same fixed 60″ sky aperture gives a ratio of
**0.2492**, against the 0.25 that `(0.2/0.4)²` predicts. `sum × pixel_area` is
conserved to 0.3%, which is the signature of surface-brightness preservation.

`fetch_legacy_survey.py` already applies the Legacy factor — it documents it at
line 34 — but that is the *SPARC pilot* path. The 50-tract path runs through
`layer_connectors.py` → `normalize_legacy_cutouts.py`, which never applied it.

**Effect of the fix**, measured as the empirical Rubin/Legacy compact-source flux
ratio, which should sit near 1 if both chains are right:

- before: **0.562** (a 0.63 mag discrepancy — far too large for two r bands)
- after, tract 10079: **0.9646**
- after, median over the 35 fields where it is well measured: **0.9505**, range
  0.86–1.108, i.e. a **0.055 mag** median offset

A 0.055 mag Rubin-r versus DECam-r throughput difference is physically sensible.
0.63 mag was not. The fix is corroborated independently in 35 fields.

## 2. Blocker status

`pipeline/reconcile_selected_regions.py` applies the operations that
`reconcile_image_layers.py` validated on the pilots, to all 50 regions.

| Blocker | Before | Now |
|---|---|---|
| PSF matching | never applied | **47/48 pass**, post-match FWHM agreement within 10% |
| background matching | never applied | **38/48 pass** |
| flux-unit transfer | wrong by 4.3× | **35/48 corroborated** by the independent point-source ratio |
| resampling covariance | unmodelled | **measured** — see §4 |
| injection/recovery QA | never run | **running per region** — see §4 |
| bandpass transfer | untestable | **testable and failing** — see §3 |

48 of 50 regions reconcile. The 2 failures are Pan-STARRS gap-fill regions with
no recorded `EXPTIME`, so their stack units cannot be placed on an absolute
scale. That is reported, not papered over.

Blockers remaining per region: **25 regions are down to 3** (from 6), 22 at 4,
1 at 5.

Registration is the weakest gate at **30/48**, median post-match p95 = 0.285″
against a 0.30″ threshold. The pilots reached 0.086–0.220″ by propagating Gaia
proper motions across the survey epoch baseline; this runner only applies a
median source-to-source offset. Gaia epoch propagation is the obvious next
improvement and is already implemented in `gaia_registration.py`.

## 3. Bandpass transfer: a clear negative result

This is the blocker that failed on every SPARC pilot (0.379–1.080 mag residual
against a 0.08 mag tolerance). Those pilots had **one** Rubin band, so no Rubin
colour existed and no colour term could be fitted at all. A second band was
acquired for 39 regions this session (28 g, 14 i, 2 z, 1 y), which makes the fit
possible for the first time.

`pipeline/measure_bandpass_transfer.py` fits
`m_ref − m_rubin = a + b·(m_band2 − m_rubin)` on compact sources.

Per-field results look encouraging:

- colour term significant at >2σ in **34/37** fields
- RMS residual improves in **37/37** (guaranteed by least squares, but confirms the fit is doing something)
- median residual **0.090 → 0.073 mag**, crossing below the 0.08 tolerance
- **22/37** fields land within tolerance

**But a bandpass colour term is a property of the two filter systems. It must be
the same constant in every field.** It is not:

| Pair | Fields | Weighted mean | Reduced χ² | Field spread ÷ stated uncertainty |
|---|---|---|---|---|
| Rubin g−r vs Legacy r | 22 | +0.0342 ± 0.0041 | **443.6** | 15.8 |
| Rubin i−r vs Legacy r | 12 | +0.1838 ± 0.0074 | **8.6** | 9.0 |

A reduced χ² of 443 means the field-to-field spread is 16× larger than the
per-field uncertainties admit. **A single linear Rubin-colour term does not
describe the Rubin→Legacy transfer.** Something field-dependent dominates —
candidates are PSF-matching residuals in the wings, aperture effects, crowding,
or genuine spatial structure in the Legacy calibration.

This is a real advance even though it is a negative result: the pilots could not
ask the question, and the answer rules out the simplest hypothesis. The bandpass
blocker stays closed, and the 22 "within tolerance" fields must not be read as a
validated transfer.

## 4. Detection limits and the covariance measurement

`pipeline/validate_region_recovery.py` injects exponential sources into the real
matched pixels and refits them, producing per region and per layer:

- `faintest90PercentCompleteMu0MagArcsec2` — the limiting surface brightness,
  without which no residual has a stated detectability
- `empiricalToFormalNoiseRatio` — the measured blank-position scatter divided by
  the propagated per-pixel uncertainty, which **is** the resampling-covariance
  measurement

Two corrections were needed before the numbers meant anything:

1. **A pixel-area factor is a rebinning, not a recalibration.** Summing A
   independent native pixels scales the value by A and the variance by A, not
   A². The unit conversion is a true recalibration and does scale variance by its
   square. The two are now applied separately.
2. **The method assumes blank positions are blank.** A 4′ tract cutout is full of
   real sources, so drawing null positions on top of them measured the galaxy
   population rather than the noise — and returned an identical 22 mag/arcsec²
   limit for every source size on both surveys, which is not physics. Sources are
   now excluded through the variance plane rather than the mask, because
   `choose_positions` requires 95% of a template box to lie inside the mask it is
   given and a scattered source mask makes placement impossible in a crowded
   field. `fit_template_amplitude` independently drops non-finite-variance
   pixels, so this excludes sources from every fit while leaving placement
   contiguous.

After both fixes the limits behave as physics requires — deepening with source
size (e.g. 21.0 / 23.0 / 24.0 mag/arcsec² at Re = 3″ / 6″ / 12″ on tract 10079).

**Measured over 45 regions** (3 failed):

| | Rubin | Reference |
|---|---|---|
| limiting μ₀, median | **25.0** mag/arcsec² | 25.0 |
| limiting μ₀, range | 23–26 | 22–26 |
| empirical/formal noise ratio, median | **6.98** | 6.85 |
| noise ratio, range | 3.4–49.9 | 3.6–19.8 |

**The per-pixel variance planes understate the true uncertainty by a median
factor of about 7**, which is precisely why resampling covariance had to stop
being a footnote. Any significance computed from the variance planes alone is
overstated by roughly that factor — including the ">99×" peaks currently shown
on `/prototype`.

A 25 mag/arcsec² median limit is shallow relative to what Rubin can reach; these
are 4′ cutouts, so the largest templates have little blank sky to sit in. Larger
cutouts are the lever if deeper limits are wanted.

## 5. What still remains

1. **Bandpass transfer.** The linear colour term is ruled out. Next candidates:
   fit per-source-type rather than per-field, use full synthetic photometry
   through the actual filter curves, or accept that a photometric transfer is not
   achievable at this precision and gate photometric claims separately from
   morphological ones.
2. **The extended-source transfer**, which is the one that actually blocks
   missing-light claims. Everything in §3 is compact-source only; point sources
   share the PSF and say nothing about a resolved galaxy where population, dust,
   and surface brightness vary with radius.
3. **Gaia epoch propagation in registration**, to move the astrometry gate from
   30/48 toward the pilots' 0.086–0.220″.
4. **Pan-STARRS EXPTIME**, to recover the 2 failed regions.
5. **The science products** — radial profiles from Rubin pixels, outer detectable
   radius, Δg_bar(r), morphology, the difference catalogue, discrepancy cards.
   None of these have started; see the original remaining-work document.
6. **The site.** None of this session's output is on the Vercel site yet. The
   new public manifests are
   `public/data/layers/selected-regions/rubin-reference-reconciliation.json`,
   `region-diffuse-recovery.json`, and `bandpass-transfer.json`. The tract pages
   already render `comparisonBlockers` as flat text; they should render the
   six-gate progress, the measured limiting surface brightness, and the noise
   ratio.

## 6. A gate decision worth keeping

The background gate measures the **sigma-clipped pedestal**, not the raw
difference median. A real throughput difference multiplies the astrophysical flux
above sky, so gating on the raw median charges the background stage for an effect
it cannot fix — the first run failed tract 10079 on exactly that. Both numbers
are reported: the raw median is bandpass evidence, the clipped pedestal is the
background gate.

Similarly, `flux-unit transfer` is only marked cleared when the empirical
point-source ratio actually corroborates the applied chains (≥20 sources,
scatter ≤ 0.06 dex, |log₁₀ scale| ≤ 0.10). Applying a conversion is not the same
as demonstrating it is right. Well-measured fields sit near 0.036 dex; the 13
fields that disagree run 0.08–0.22 dex and are held open rather than published.

---

## 7. Anomaly discovery (added same session)

`pipeline/scan_region_anomalies.py`. The design principle is that an anomaly
needs a **stated expectation**, not a big number in a difference image.

- **Expectation**: after PSF, background, and flux-unit matching, the two planes
  should agree within the noise.
- **Uncertainty**: the empirical blank-position scatter from injection/recovery,
  never the per-pixel variance planes. Those understate by ~7×, so a variance-based
  significance would inflate every candidate by roughly that factor.
- **Estimator identity**: candidates are measured with the same
  `fit_template_amplitude` call, at the same scales, that produced the null — so
  the calibrated threshold actually applies. This also forced the scan to mask
  sources through the variance plane exactly as the null calibration does;
  without that, 33 of 47 candidates in the first test field were spurious.

**Result over 33 scannable regions**: 214 raw candidates → **13 survive**.

Discriminants, in order of how much they removed:

| discriminant | rationale | effect |
|---|---|---|
| bright-source proximity | Gaussian PSF matching leaves wing residuals | removes the top of the raw list |
| **scale coherence** | real structure has a size, so it registers at more than one template scale | **41 of 54 removed** |
| mask-edge proximity | normalised convolution leaves structure at boundaries | few |
| field crowding | a field with far more survivors than its peers has a systematic | fired on nothing this run |

The 13 survivors sit in 7 fields, all detected at ≥2 scales, split 6 rubin-excess
/ 7 reference-excess — a balanced split, which is what noise and real structure
both give and a systematic would not. The strongest scale-coherent case is
tract 2397 at (65.85587, −48.35300), present at all three scales.

**None of these are detections.** The bandpass transfer is not validated, so a
colour difference alone can produce a residual of this size; 15 regions were not
scannable at all. Each candidate carries its falsification tests: re-measure in
the second Rubin band, re-measure against a third survey, inject a synthetic
source of the same amplitude, and re-run after the extended-source transfer.

### What this does not yet cover

The scanner only searches the one comparison kind that exists: Rubin optical
minus Legacy/PS1 optical. Measured against every exact Rubin×survey overlap in
the index, that is **48 of 22,921 possible comparisons, or 0.21%**, and one of
eleven registered families. The other ten families need different machinery,
because they are not subtraction problems:

| kind | example | comparison is |
|---|---|---|
| same band | Legacy, PS1, DES, HSC | pixel difference (built) |
| different band | GALEX, unWISE, 2MASS | SED consistency |
| different observable | HIPASS, VLASS, eROSITA | positional association |
| mass vs light | ACT, Planck, DES-Y3, KiDS | the dark-matter question |
| static vs variable | ZTF | did it change |
| image vs catalog | Gaia, DESI, SDSS | cross-match, no alignment |

DES DR2 and HSC PDR2 are the cheapest wins: both are directly comparable optical
surveys with confirmed overlaps and **zero pixels fetched**.

---

## 8. A cross-match trap worth recording

The strongest surviving anomaly candidate — tract 2397, present at all three
template scales — sits 29.9 arcsec from the position in that region's eROSITA
record, which also reports `sourceCount: 2`. That looks like an optical residual
with an X-ray counterpart.

It is not. The `upperLimit` block's `ra`/`dec` is the **query position**, and for
tract 2397 it is exactly the region centre, 0.0 arcsec away. The 29.9 arcsec was
simply the candidate's own offset from the tract centre. Cross-matching against
that field would manufacture an apparent X-ray association for every anomaly
that happens to lie near a tract centre, with a separation that encodes nothing
but the anomaly's position.

`sourceCount` does say two eROSITA sources exist in the region, but their
positions are not in this record. Any real counterpart-association operator (G6)
must pull the eRASS1 source list itself and match against catalogued source
positions, never against the upper-limit query position.

**Where to get them:** the eRASS1 main catalogue is on VizieR as
`J/A+A/682/A34/erass1-m`, carrying `RA_ICRS` and `DE_ICRS` per detection. A cone
search around each Rubin position gives the real source list, in the same
pattern already used for HICAT. `IX/70/erass1` does not resolve.
