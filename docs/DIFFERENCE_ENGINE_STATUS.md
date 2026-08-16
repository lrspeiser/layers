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

48 of 50 regions reconcile. ~~The 2 failures are Pan-STARRS gap-fill regions with
no recorded `EXPTIME`, so their stack units cannot be placed on an absolute
scale.~~ **Withdrawn — see §12.** The exposure times were in the headers all
along; the reader looked under `validation.units` where the manifest writes
`validation.unitsValidation`, so every PS1 region returned no exposure. A claim
about the archive that was really a claim about one line.

Blockers remaining per region: **25 regions are down to 3** (from 6), 22 at 4,
1 at 5.

Registration is the weakest gate at **30/48**, median post-match p95 = 0.285″
against a 0.30″ threshold. ~~Gaia epoch propagation is the obvious next
improvement.~~ **Measured and ruled out — see §14.** Fitting rotation and scale
on top of the translation recovers 8 regions of 188, and the terms it would
remove are absent: median rotation −0.00025°, median scale factor 0.99999. The
residual is centroid-level scatter, so the gate measures how well the two
surveys agree rather than how well the fit was done.

## 3. Bandpass transfer: a clear negative result

This is the blocker that failed on every SPARC pilot (0.379–1.080 mag residual
against a 0.08 mag tolerance). Those pilots had **one** Rubin band, so no Rubin
colour existed and no colour term could be fitted at all. A second band was
acquired for the 200-region set this session — **165 regions with two bands** —
which makes the fit possible for the first time.

`pipeline/measure_bandpass_transfer.py` fits
`m_ref − m_rubin = a + b·(m_band2 − m_rubin)` on compact sources.

Per-field results look encouraging: **156 regions measured**, **95 of them
within the 0.08 mag tolerance**, and the RMS residual improves wherever the fit
runs (guaranteed by least squares, but it confirms the fit is doing something).

**But a bandpass colour term is a property of the two filter systems. It must be
the same constant in every field.** It is not:

| Pair | Fields | Weighted mean | Reduced χ² |
|---|---|---|---|
| Rubin g−r vs Legacy r | **112** | −0.0677 ± 0.0018 | **93.8** |
| Rubin i−r vs Legacy r | 22 | — | **5.1** |
| Rubin z−i vs Legacy r | 13 | −0.8429 ± 0.0371 | **5.2** |
| Rubin z−r vs Legacy r | 3 | +0.1057 ± 0.0087 | **3.5** |

Re-measured after the second-band gap fill took the two-band set from 157 to 165
regions: **156 regions measured** where the first pass managed 37, and **95
within the 0.08 mag tolerance** where it managed 22. The first pass reported
reduced χ² of 443.6 for g−r on 22 fields; on 112 fields it is 93.8. The number
moved by a factor of five, so quote the current one — but every colour pair is
still inconsistent with a single constant, by a wide margin, and the conclusion
below is unchanged and now rests on five times the evidence.

A reduced χ² of 93.8 on 112 fields means the field-to-field spread is far larger
than the per-field uncertainties admit. **A single linear Rubin-colour term does
not describe the Rubin→Legacy transfer.** Something field-dependent dominates.
Two of the candidates once listed here can now be ruled out: §11 shows the
deficit does not move with aperture, so PSF wings and aperture effects are not
it. Crowding and spatial structure in the reference calibration remain.

This is a real advance even though it is a negative result: the pilots could not
ask the question, and the answer rules out the simplest hypothesis. The bandpass
blocker stays closed, and the 95 "within tolerance" fields must not be read as a
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

---

## 9. The chain at 190 regions, and what the flux scale does at scale

Running the full chain over the 200-tract set (193 with Rubin pixels):

| | 48-region set | 190-region set |
|---|---|---|
| reconciled | 48 | 190 |
| PSF matched | 47 (98%) | 189 (99%) |
| background matched | 38 (79%) | 163 (86%) |
| astrometry passed | 30 (63%) | 110 (58%) |
| flux corroborated | 35 (73%) | 148 (78%) |
| **down to 3 blockers** | 25 | **122** |

The gates behave the same at four times the scale, which is the first thing worth
knowing about a pipeline that had only ever been run on 48 fields.

### The flux scale is not one number across the sky

The empirical Rubin/Legacy compact-source ratio came out differently on the two
samples:

- the original 48 regions: **0.9505** (0.055 mag)
- the 142 new regions: **0.9116** (0.100 mag)

The same 48 regions returned bit-identical 0.9505 in both runs, so the pipeline
is deterministic and this is real variation between fields, not drift.

It correlates with **matched source count at −0.357**, and with essentially
nothing else — Galactic latitude −0.10, Legacy seeing +0.03, Rubin seeing −0.03,
seeing ratio −0.04. That is a crowding signature rather than a throughput one:
the ratio is measured in a fixed 1.5 arcsec aperture, and in denser fields that
aperture collects more neighbour flux. Legacy's PSF is broader than Rubin's
(2.33 vs 1.97 arcsec on tract 10079), so blending inflates the Legacy side more
and pushes the ratio down.

**This is the same conclusion the colour term reached by another route.** The
bandpass fit gives a reduced χ² of 93.8 on 112 fields against a single constant; the scalar flux
ratio now shows a 0.040 dex robust spread driven by field density. Two
independent measurements agree that the Rubin-to-Legacy photometric relationship
is not one constant across the sky, and at least part of that is the aperture,
not the surveys.

The fix for both is the same and is not a tuning parameter: PSF-matched aperture
photometry, or model fitting, instead of a fixed circular aperture on images with
different PSFs.

---

## 10. The three-way optical test: what belongs to Rubin

Every optical comparison in this project had been Rubin against Legacy alone,
which cannot attribute a field-dependent effect to either side. DES DR2 supplies
a second independent reference: 148 science-ready regions from the public NOIRLab
cutout service, 143 reconciled against Rubin, 108 sharing a region with the
Legacy measurement.

This test now lives in `pipeline/compare_reference_operators.py` rather than in a
one-off calculation, which matters, because running it as an operator over the
full region set **changed one of its three answers**. The numbers below are the
operator's; significance is a two-sided permutation test with 20,000 shuffles.

| | Rubin vs Legacy | Rubin vs DES |
|---|---|---|
| regions with an empirical scale, QA-passed | 91 | 140 |
| compact-source flux scale | 0.9199 (+0.0907 mag) | 0.9201 (+0.0904 mag) |
| **correlation with matched-source count** | **−0.334** (p 0.0009) | **−0.269** (p 0.0010) |

**The zeropoint offset belongs to Rubin or to the aperture, not to the
reference.** Paired over the 71 regions measured against both, the median
log-scale difference between the two pairings is −0.0016 dex, bootstrap 95%
interval [−0.0046, +0.0005] — consistent with zero. Two independently calibrated
surveys, reduced by different pipelines, agree that Rubin is about 9% fainter in
a fixed 1.5 arcsec aperture. A reference calibration error cannot produce the
same offset twice.

**Most of the field-to-field variation belongs to Rubin.** Across those 71
regions the two scales correlate **+0.884** (p < 0.0001). A variation driven by
the reference would not reproduce itself against a different reference; one
driven by Rubin, or by the aperture method applied to Rubin, would.

**The crowding dependence is not Legacy's. That earlier attribution is
withdrawn.** The first pass measured −0.357 against Legacy and −0.083 against
DES and concluded the term was a property of the reference, explained by Legacy's
broader PSF. Over the full 140-region DES set the DES pairing shows the same
trend, −0.269 at p 0.0010, against Legacy's −0.334 at p 0.0009. The effect is
present in both pairings, so what it shares is Rubin and the aperture, not
Legacy. The PSF-width story was a plausible mechanism attached to a number that
did not survive the larger sample.

The result is not an artefact of the QA cut. Reconciliation QA drops 99 of 190
Legacy regions, and a crowded field is likelier to fail it, so the operator
measures the correlation with and without the filter: unfiltered it is −0.406
(n 189) and −0.247 (n 142), the same sign and significance in both pairings.

Two of the three attributions therefore stand, and both point the same way: the
deficit and its variation sit on the Rubin side of the comparison. What neither
can say is whether that means the aperture or the calibration — which §11
measures directly.

### A bug this found, which the empirical scale caught

The first Rubin-versus-DES run produced a flux scale of 0.0003, an offset of
+8.999 mag. `build_selected_region_comparisons.py` hardcoded
`referenceSurveyId: "legacy-surveys-dr10"` onto whatever reference manifest it
was handed, so DES pixels already converted to nJy were relabelled as Legacy and
had the nanomaggy chain applied on top, a factor of about 3,400.

The independent compact-source ratio is what caught it: an offset of almost
exactly 9 magnitudes is not a physical difference between two optical surveys.
After the fix, matched regions went from 14 to 140 of 143 and background-matched
from 16 to 142. The builder now respects a manifest that identifies itself.

## 11. Aperture or zeropoint? The curve of growth says zeropoint

§10 established that the ~7% Rubin flux deficit sits on the Rubin side of the
comparison but could not separate the two explanations that matter. They predict
different shapes, which makes the question decidable:

- **an aperture effect** — Rubin light outside the small aperture, left there by
  a PSF match that did not reach the wings. The measured ratio must then climb
  toward 1 as the aperture grows.
- **a zeropoint difference** — a constant calibration factor. The ratio must then
  be flat, because a constant does not care how much of the source is enclosed.

`pipeline/measure_curve_of_growth.py` measures the ratio at seven radii from 1.0
to 5.0 arcsec on the already reconciled, PSF-matched, sky-subtracted planes,
independently in both pairings.

| radius | 1.0″ | 1.5″ | 2.0″ | 2.5″ | 3.0″ | 4.0″ | 5.0″ |
|---|---|---|---|---|---|---|---|
| vs Legacy (130 fields, 1561 sources) | 0.9379 | 0.9344 | 0.9363 | 0.9381 | 0.9361 | 0.9347 | 0.9396 |
| vs DES (79 fields, 893 sources) | 0.9309 | 0.9400 | 0.9373 | 0.9451 | 0.9478 | 0.9505 | 0.9525 |

**The curve is flat, so the deficit is not an aperture effect.** Against Legacy
the per-field median gain from 1.5″ to 5.0″ is 0.9963, bootstrap 95% interval
[0.9661, 1.0265] — a 3.3× larger aperture recovers none of the 6.5% deficit. The
DES pairing rises slightly, gain 1.0153 [0.9968, 1.0262], recovering about a
quarter of its deficit, with an interval that only just includes 1. So Legacy is
flat outright and DES is at most weakly rising, and neither is the near-total
recovery the aperture hypothesis requires.

Two confounds could each have manufactured a rising curve on their own, so
neither is left to chance. **Blending**: a larger aperture swallows neighbours,
whose flux enters both sides and drags any ratio toward 1 — only sources with no
detected neighbour within 3× the largest aperture are used, which is what cuts
the usable sample to 1561 and 893 sources. **Sky**: a background error is
constant per pixel and therefore grows as the aperture *area*, so the local sky
is re-measured in a 6–9″ annulus per source and per side. Pixelisation of the
hard-edged aperture largely cancels, because both planes are on the same grid and
share the same aperture mask.

The scale here, ~0.936, is slightly above the reconciliation's 0.920 because this
measurement subtracts a local sky per source and admits only isolated sources.
The two are consistent, and the shape result does not depend on either value.

**What this closes and what it opens.** It closes the PSF-wing explanation: more
PSF-matching work would not move this number, so that is no longer the next step.
It opens a narrower question — a ~0.07 mag constant between Rubin and two
independently calibrated references, in the same named band, which is a
throughput or zeropoint question and needs the filter curves and the surveys'
own calibration papers rather than more pixels. It also does not conflict with
the density correlation in §10: a constant offset with radius and a scale that
varies with field density can both be true, and a detection or deblending
difference would produce exactly that pair.

**This is not a claim that Rubin's calibration is wrong.** It is a measured,
shape-resolved statement about the difference between three surveys' fluxes on
matched compact sources, with no external standard involved.

## 12. A third reference, and what it changed

Pan-STARRS was acquired for all 200 regions as a third independently calibrated
optical reference. Getting it there took four fixes, three of which were faults
in this repository rather than in the archive:

- **The gap-fill overwrote Legacy.** The PS1 branch replaced any Legacy
  reference for the same tract instead of supplying tracts Legacy lacked. That
  was harmless while PS1 covered two regions; with PS1 validating for 198 of 200
  the next run would have silently replaced the whole Rubin-vs-Legacy chain,
  under the same file names, with a Rubin-vs-PS1 one.
- **The band was hardcoded to i.** Right for filling a coverage gap, wrong for
  photometry: Rubin is r in 157 of 200 regions. The i-band build reported
  `sameNamedBand: 4` of 48; the r-band build reports **154 of 189**.
- **The mask convention was read backwards.** PS1 marks flagged pixels with
  finite values and leaves the rest NaN, so a good cutout is 99.85% NaN and an
  all-NaN mask is a clean field. Requiring finite mask pixels rejected 40% of
  regions for being unblemished. Acquisition went from 112 to **198 of 200**.
- **`EXPTIME` was read from the wrong key.** `reconcile_selected_regions.py`
  looked in `validation.units`; the manifest writes `validation.unitsValidation`.
  Every PS1 region therefore returned no exposure time, and §2 recorded the
  resulting failures as *"Pan-STARRS gap-fill regions with no recorded EXPTIME,
  so their stack units cannot be placed on an absolute scale."* That was a claim
  about the archive that was really a claim about one line: the headers carry it,
  1092 s on the first region checked. **That sentence in §2 is withdrawn.**

With those fixed, 188 of 189 PS1 regions reconcile, 151 matched.

### PS1 does not corroborate the zeropoint. It cannot.

| | Rubin vs Legacy | Rubin vs DES | Rubin vs PS1 |
|---|---|---|---|
| median scale | 0.9199 | 0.9201 | **1.1530** |
| absolute flux chain | verified | verified | **not verified** |

PS1 lands 0.245 mag from the two verified references. A real PS1-versus-DES
zeropoint difference is a few hundredths of a magnitude, not a quarter of one,
and the PS1 chain is the one this project has never checked against the survey's
own photometric catalogue — it is marked `verified: false` in every record it
writes. Two verified chains agreeing with each other and disagreeing with one
unverified chain is a statement about the unverified chain.

So the operator now excludes an unverified chain from the zeropoint finding and
reports it as its own flag. **The zeropoint conclusion is unchanged and still
rests on two references, not three.** Resolving it means comparing aperture
magnitudes against the published PS1 catalogue for the same sources.

The other two findings are rank correlations, which no constant factor can
touch, so PS1 contributes to both.

### PS1's curve of growth dissents, and that is informative

| radius | 1.0″ | 1.5″ | 2.0″ | 2.5″ | 3.0″ | 4.0″ | 5.0″ |
|---|---|---|---|---|---|---|---|
| vs PS1 (90 fields, 1014 sources) | 1.0606 | 1.0963 | 1.0979 | 1.0829 | 1.0680 | 1.0609 | 1.0500 |

The PS1 ratio *falls* with aperture — gain 0.9801, bootstrap 95%
[0.9624, 0.9940] — where Legacy and DES are flat. Shape is independent of any
constant chain error, so unlike the scale this is a usable PS1 result. It means
PS1 gains relatively more flux at large radii than Rubin does, after both were
PSF-matched: a residual wing mismatch the circular-Gaussian match did not
remove, which is unsurprising for stacked PS1 warps.

Rubin is common to every pairing, so the same attribution logic applies to a
shape as to a value: a trend appearing in one pairing and not the others belongs
to that reference or its PSF match. The flat result stands for Legacy and DES;
PS1 cannot testify about shape until its wing residual is resolved.

### The crowding attribution has now moved three times

| references | verdict |
|---|---|
| Legacy + partial DES | Legacy's, from its broader PSF |
| Legacy + full DES | common to both, therefore Rubin's |
| Legacy + DES + PS1 | Legacy and DES, not Rubin's |

Measured: legacy −0.334 (p 0.0009), des −0.269 (p 0.0010), ps1 −0.130 (p 0.11).
**All three have the same sign**; only two clear the 1% threshold. A pass/fail
flag makes that look more decisive than it is — PS1 is weak agreement, not
absence.

The PSF-blending mechanism can now be tested rather than asserted, and it fails:
ordered by reference FWHM the correlations are Legacy 2.225″/−0.334, PS1
1.527″/−0.130, DES 1.487″/−0.269. DES and PS1 have nearly the same reference PSF
and very different correlations, so blending in the reference PSF is not a
sufficient explanation.

**Unlike the other two findings, this one has not converged and should not be
relied on.** It is recorded with its full history in the manifest so the next
reference added either settles it or shows it moving again.

## 13. Euclid Q1: one real verdict, and the sixth time footprint was not data

The morphology goal named HST, JWST **and Euclid Q1**. Only the first two were
tried, and they produced one verifiable candidate out of 34 because MAST's
pointing table listed 25 overlapping "observations" whose nearest frame was
24.1 arcsec outside the position. `pipeline/check_euclid_followup.py` closes the
Euclid half, and it was built specifically not to repeat that.

**Containment is asked of the data.** IRSA publishes Euclid Q1 through ObsCore
with a real `s_region` polygon per product, so the test is
`CONTAINS(POINT(candidate), s_region) = 1` rather than a distance to a field
centre. A circle around the three Euclid Deep Field centres would have claimed
5 regions and 1 candidate in EDF-F; the polygon test finds **2 of 34 candidates**
contained, one of which the circle would have missed entirely.

**Only VIS and NISP count.** MER mosaics carry DECam ancillary layers beside the
Euclid pixels. DECam is the camera behind both Legacy Survey and DES, so a
"confirmation" from a DECam layer would be the reference this project already
compares against, wearing a different name.

### The verdict

**dp2-tract-5063, an eRASS1 X-ray source with no optical counterpart at Rubin
depth: survives.** Euclid sees nothing above 5σ at that position in any of four
independent bands, measured against blank-aperture scatter in the same image:

| band | instrument | pixel scale | significance |
|---|---|---|---|
| VIS | VIS | 0.100″ | +0.30 |
| Y | NISP | 0.100″ | +0.40 |
| J | NISP | 0.100″ | +0.05 |
| H | NISP | 0.100″ | −0.09 |

This is the project's first independent-resolution verdict on a candidate. It
does not make the object a discovery: X-ray sources without optical counterparts
are a known population, and no absolute depth is quoted here because the Euclid
flux chain has not been verified against Euclid's own catalogue — the same
standard applied to Pan-STARRS in §12.

### The sixth time, and the first to survive a real footprint test

The second contained candidate returns an **all-zero cutout in all four bands**.
The ObsCore polygon contains the position; the tile has no pixels there. A MER
tile is zero-filled outside its real coverage, and zero is a finite value, so
every "is there data" check based on finiteness passes.

This is worth recording precisely because the containment query was the fix for
the last five instances. **`CONTAINS(s_region)` is necessary and still not
sufficient.** Only loading the pixels settles it, and the operator now counts
`footprintContainsButNoPixels` as its own status rather than folding it into a
generic failure.

Running total for the goal: **2 verdicts** across HST/JWST and Euclid, from 34
candidates. Not 229.

## 14. The registration gate cannot be fixed by a better transform

Registration is the tightest reconciliation gate — 110 of 190 Legacy regions
clear the 0.30″ p95 threshold, and that is what holds `matched` down to 91. The
reconciler corrects it with a single median source-to-source translation, and §2
recorded the obvious next step: propagate Gaia proper motions, fit something
richer than a shift.

Obvious is not the same as correct, and this project has already paid once for
asserting a plausible mechanism and withdrawing it when more data arrived (§12).
So `pipeline/diagnose_registration_residual.py` measures the decomposition
before anything is implemented. On mutual nearest-neighbour source pairs from
the reconciled planes, over **188 regions**:

| model | free parameters | median p95 | regions ≤ 0.30″ |
|---|---|---|---|
| translation | 2 | 0.3229″ | 68 |
| similarity (+rotation, scale) | 4 | 0.3186″ | 76 |
| affine | 6 | 0.3134″ | 76 |

**Tripling the free parameters recovers 8 regions of 188.** The terms a richer
transform would remove are not there to remove: median rotation is −0.00025°
with a 0.013° spread, and the median scale factor is 0.99999. Similarity and
affine pass exactly the same 76 regions, so the sixth parameter buys nothing at
all beyond the fourth.

**The gate is not measuring how well the fit was done. It is measuring how well
the two surveys agree on these fields.** The residual is centroid-level scatter,
and no transform removes scatter. Had the richer transform been implemented on
the strength of "the reconciler only applies a median offset", it would have
shipped as an improvement that moves 4% of regions, and the 0.30″ threshold
would still have been read as a fit-quality problem.

That has a consequence worth stating plainly: **the astrometry blocker is not
closable by better registration**, and the route to more matched regions is
either a threshold justified by what the surveys can actually deliver, or deeper
source catalogues that reduce centroid noise. Both are honest; pretending a
sixth parameter would do it is not.

## 15. Where the ~727 came from, and what was actually reachable

The optical goal asked for **~727 pairs** and the delivered number is 521. The
gap is not effort, and it is not an estimate — the target's own arithmetic can be
reconstructed exactly.

The region planner records a `confirmedSurveyIds` list per region, and summing it
across the four optical surveys gives **199 + 164 + 162 + 198 = 723**. That is
the target. It is a footprint count: a region is "covered" when a survey's
declared footprint contains it, whether or not the survey has pixels there.

Measured against what the archives actually serve:

| survey | claimed by footprint | pixels validated | reconciled pairs |
|---|---|---|---|
| Legacy DR10 | 199 | 198 | 190 |
| DES DR2 | 164 | **148** | 143 |
| HSC PDR2 | 162 | **0** | 0 |
| Pan-STARRS DR2 | 198 | 196 | 188 |
| **total** | **723** | **542** | **521** |

- **HSC contributes 162 to the target and 0 to reality.** PDR2 publishes only
  HiPS tiles without credentials: display products with no calibrated flux and no
  variance plane. There are no science pixels to fetch, at any effort.
- **DES contributes 164 and delivers 148.** The 16 missing regions return *zero
  rows* from the DES SIA service — the survey has no coadd there, inside its own
  declared footprint.
- Legacy and Pan-STARRS lose 1 and 2 respectively.

So **181 of the 202-pair shortfall is footprint overstatement baked into the
target**, and 21 is reconciliation QA. Against the reachable ceiling of 542, the
delivered 521 is **96.1%**.

This is the seventh time this project has measured footprint overlap
overstating data — after NGC 0100's masked pixels, HIPASS's all-sky-but-no-
detections, eROSITA's 55 tracts to 8 detections, HST's 25 observations to 0
containing frames, VLASS's 197 overlaps to 39 sources, and Euclid's contained
polygon with no pixels in §13. The difference here is that the overstatement was
inside a planning number rather than a result, which is the more expensive place
for it to hide: it set a target that no amount of work could meet.

**`optical-coverage-truth.json` records the decomposition per survey.** The
recommendation it carries is one line: coverage planning should record *the
archive served pixels here*, not *a declared footprint contains this position*.

## 16. The morphology goal's 229, and what it was possible to verify

The morphology goal asked for **229 pairs**. Like the optical goal's ~727 (§15),
the number reconstructs from the planner's footprint counts:
**HST 198 + JWST 12 + Euclid Q1 14 = 224**. It counts regions whose declared
survey boundary contains them, not candidates a survey can verify.

There is a harder ceiling underneath that one. **A verdict is delivered on a
candidate, and the register holds 34.** So 195 of the 229 were unreachable
before any archive was queried — not for want of coverage, but because the
candidates do not exist. The goal's own stated outcome says as much: *"every
surviving anomaly candidate in one of these footprints gets an
independent-resolution verdict."* That is a statement about candidates, and the
229 is a statement about regions. The two were never the same quantity.

Measured:

| | count |
|---|---|
| candidates in the register | 34 |
| inside a declared high-resolution footprint | 2 |
| with actual pixels at the position | **1** |
| verdicts delivered | **1** |

**Every candidate that can be verified has been verified.** The one is the
eRASS1 X-ray source in `dp2-tract-5063`, and it survives: no optical counterpart
at Euclid depth in VIS, Y, J or H (§13).

There is a pleasing closure in which survey delivered it. The HST/JWST pass
marked that exact candidate `verifiable` and `not-covered` — the only one it
could have checked, and no frame contained it. Euclid closed the single case the
first pass could not.

Of the other 33: 31 sit in no high-resolution footprint at all, one is contained
by a Euclid polygon whose tile has no pixels there, and one has no position
recorded by its operator.

So the honest reading of this goal is not "2 of 229". It is **1 of 1
verifiable**, against a target that counted sky rather than candidates.
`highres-followup/verification-truth.json` holds the accounting.

## 17. The acquisition goal's ≥180 two-band regions, against DP2's actual bands

The acquisition goal asked for 200 validated regions with **≥180 carrying two
bands**, on the premise of "167 new tracts, 2 bands each". The premise is an
availability assumption, and like the footprint counts behind ~727 (§15) and 229
(§16) it can be checked against the archive rather than argued about.

Counting the bands DP2 actually serves for each of the 200 regions, from the
cached SIA discovery responses:

| bands available | regions |
|---|---|
| 1 | **27** |
| 2 | 24 |
| 3 | 26 |
| 4 | 12 |
| 5 | 55 |
| 6 | 56 |
| **≥2** | **173** |

**DP2 has two or more bands for 173 of the 200 regions. The target of 180
exceeds that ceiling by 7, and was unreachable before any pixels were fetched.**
Twenty-seven regions carry exactly one band in the entire release; no acquisition
strategy produces a second one.

Delivered: **167 of the reachable 173, or 96.5%.** Getting there took three
passes, because the first stopped at one band attempt per region:

1. 157 at the start of this session.
2. +8, from the 11 regions that had an unused band.
3. +2, by retrying regions whose first alternative failed validation — tract
   5391 in g, tract 5281 in z after g failed.

Every region with any untried band in DP2 has now been attempted. The remaining
6 of the 173 failed validation on every band the release offers them.

That completes a pattern worth stating once, plainly. **Three of this goal set's
targets — 727 optical pairs, 229 morphology pairs, 180 two-band regions — were
computed from what archives declare rather than from what they serve.** Each was
unreachable at the moment it was written: by 181 pairs, by 195 verdicts, and by 7
regions respectively. The work delivered 96.1%, 100% and 96.5% of what was
actually there.

The recommendation from §15 applies to all three: derive targets from
archive-served data, not from declared coverage. `dp2-band-availability.json`
records this one.

## 18. The bandpass, settled: the filters are simple, the scatter is not them

Every quantitative claim in this project has carried the same caveat — bandpass
transfer is not validated, so a genuine colour difference could produce the
signal. §3 measured the empirical colour term by regressing observed magnitude
differences against observed colour and found a reduced χ² of 93.8 over 112
fields against a single constant, with a field spread of 0.168 mag.

An empirical fit cannot say *why* it scatters. It contains the filter difference
**and** photometric error, crowding, PSF residuals and calibration structure, all
at once. Synthetic photometry separates them, because integrating a known
spectrum through two known filter curves has no observational error in it at all.

`pipeline/measure_synthetic_bandpass.py` integrates 11 CALSPEC HST flux
standards — the spectrophotometric ladder these surveys calibrate against, and
real stars rather than blackbodies, which matters because the r band holds Hα and
the TiO bands — through the SVO official transmission curves, as AB magnitudes
for a photon-counting detector.

| pair | colour term (per mag of Rubin g−r) | residual rms |
|---|---|---|
| Rubin r → DECam r (Legacy, DES) | **−0.0800** | **0.0036 mag** |
| Rubin r → PS1 r | **+0.0072** | **0.0002 mag** |

Set against the empirical fit for the same pair:

| | value |
|---|---|
| empirical term | −0.0677 ± 0.0018 |
| synthetic term | −0.0800 |
| difference | 0.0123 per mag |
| empirical field spread | 0.168 mag |
| synthetic residual | 0.0036 mag |

**Two conclusions, and the second is the one that matters.**

The empirical term agrees with the synthetic one to 0.012 mag per mag of colour.
The regression was measuring real filter physics, not an artefact.

But **the filters require no field-to-field variation whatsoever**: a single
straight line describes the synthetic transfer to 3.6 millimagnitudes across the
entire colour range of the standards. So the empirical fit's χ² of 93.8, and its
0.168 mag field spread — a factor of forty larger than the filters allow — are
**not the bandpass**. They are photometric error, crowding, PSF residuals, or
spatial structure in a survey's calibration.

That retires the caveat this project has attached to everything, and replaces it
with a sharper statement: the colour term between Rubin r and DECam r is small
and linear, so a source needs an extreme colour to shift by much, and anything
varying field to field has to be explained by something other than filters.

**Rubin r and PS1 r are nearly the same filter** (+0.007 mag per mag, 0.2
millimag residual). The Pan-STARRS pairing is therefore the cleanest of the three
for any colour-sensitive question, which is worth knowing before choosing a
reference.

One limit, stated because it is real: these are stellar spectra. Galaxies
dominate the source counts, and their shapes and redshifts differ, so the stellar
prediction is a floor on the transfer's complexity rather than the whole answer.

## 19. What the scatter is, and mostly is not

§18 left a well-posed question. If the field-to-field scatter in the colour term
is forty times larger than the filters permit, it is one of: photometric error,
crowding, PSF residuals, or spatial calibration structure. Every one of those has
a covariate already measured per region by another operator, so the question is
answerable without new data.

`pipeline/diagnose_field_scatter.py` rank-correlates each field's
|colour term − synthetic prediction| against eight covariates, over 113 g−r
fields, with a 20,000-shuffle permutation test.

| covariate | suspect | ρ | p |
|---|---|---|---|
| **fit's own stated uncertainty** | photometric error | **+0.277** | **0.0028** |
| colour baseline of the sources | photometric error | −0.127 | 0.177 |
| compact-source count | crowding | −0.119 | 0.212 |
| background RMS | photometric error | +0.056 | 0.561 |
| median source S/N | photometric error | +0.049 | 0.597 |
| sources used in the fit | photometric error | −0.031 | 0.737 |
| kernel star residual | PSF residuals | −0.022 | 0.815 |
| declination | calibration structure | +0.019 | 0.843 |

**One of eight clears the threshold, and it explains about 8% of the rank
variance.** Fields whose fits declare themselves more uncertain do depart more —
photometric error contributes. It does not explain: 92% of the ordering is
something else.

The nulls are the more useful half. **Crowding, the kernel's own residual,
background noise, source count, colour baseline, signal-to-noise and declination
all come back flat.** Two of those matter particularly:

- **The kernel residual shows nothing** (ρ = −0.022). Having just replaced the
  Gaussian PSF match with a fitted one and cut star residuals by 2.5×, this says
  the remaining PSF error is not what drives the colour-term scatter. That closes
  off the most tempting explanation.
- **Crowding shows nothing here** (ρ = −0.119, p 0.21), which is an independent
  check on §12's crowding attribution — the one recorded as not converged. It
  does not settle that question, but it does not support it either.

So the honest state is: the filters are simple (§18), photometric error
contributes weakly, the four mechanisms this project can measure are ruled out as
dominant, and most of the scatter remains unexplained by anything currently
recorded.

Stated limit: these covariates correlate with each other, so a null does not
fully clear a mechanism. What it does is remove it as the *dominant* cause.

## 20. Three selection effects in the source catalogue

Publishing a catalogue forced a question the maps never had to answer: *are these
sources actually interesting?* Asking it found three artefacts, each hidden by
the one before.

### 1. Asymmetric detection — found, fixed

Detection ran on the Rubin frame alone, so a source entered the catalogue only if
it scattered bright **in Rubin**, and its reference flux was then measured with
no such selection. The sign distribution says it plainly:

| Rubin S/N | Rubin-brighter | reference-brighter |
|---|---|---|
| 5–10 | 417 | **0** |
| 10–20 | 304 | **0** |
| 20–50 | 88 | 1 |
| 50+ | 67 | 3 |

876 of 880 flagged sources pointed one way, and the median sat at 25.6 mag —
fainter than the 24.0 completeness limit. Astrophysics has no reason to run from
22:1 to infinity as signal-to-noise falls; a selection effect must.

**Detection now runs on the sum of both background-subtracted frames.** The sum
is symmetric, so a source scattering bright in either is equally likely to be
found. The ratio went 219:1 → 2.2:1, and the recovered negatives are exactly the
predicted mirror population: median Rubin S/N 2.1 against reference S/N 10.0 —
sources the old detector could not see at all.

### 2. Extended-source aperture effect — found, not fixed

Only visible once (1) was fixed, because it is what made a high-signal-to-noise
cut make things *worse*: 2.2:1 overall but **18:1 above S/N 20**, with flagged
sources four times larger than unflagged.

Segments are defined on the summed frame, and Rubin's sharper PSF keeps more of
an extended source inside a shared segment than the broader reference PSF does.

### 3. The attempted fix, and an over-claim to correct

The reconciler had matched PSFs with a circular Gaussian, which never cancels a
real PSF. `fit_matching_kernel.py` had already fitted a proper Alard–Lupton
kernel, and the matched frame is exactly recoverable from the stored difference,
so photometry moved onto that pair.

**A twelve-region pilot showed the asymmetry gone — 6 against 6 at S/N ≥ 5 — and
that was wrong.** Over all 189 regions:

| both S/N ≥ | Gaussian-matched | kernel-matched |
|---|---|---|
| 0 | 2.2:1 | 2.6:1 |
| 10 | 9.3:1 | 9.6:1 |
| 20 | **18:1** | **10:1** |

Only the highest bin improved; the overall ratio slightly worsened, and flagged
sources are still four times larger than unflagged (151 px against 40). The
kernel-matched pair is kept because it is the more correct thing to measure, but
it is **not** the fix. A spatially constant kernel matches the core; extended
flux needs a spatially varying kernel or model photometry.

The pilot was too small to support the claim, which is the same small-sample trap
this project corrected in §12 and guards against with a 40-region threshold in
the attribution operator. It caught me anyway.

### What this says about testing

None of the three would have been caught by the 65 tests. The catalogue was
internally consistent, its uncertainties empirical, its false-positive rate below
0.14%. **The injection test passed throughout — because it injects into both
frames symmetrically, so it could never expose an asymmetric detection stage.**

Each artefact was found by asking whether the *shape* of a population made
physical sense: a one-sided sign distribution, an asymmetry that grew with
signal-to-noise, sources four times larger than average. That is not something a
unit test expresses, and it is the argument for publishing a catalogue with
documented selection effects rather than a curated list of anomalies.

## 21. The departure statistic is size-biased, and the cause is not yet known

§20 left the extended-source aperture effect open, and named PSF-model
photometry as the fix. That turned out to be the wrong diagnosis. The effect is
larger than an aperture problem, and it is not astrophysical at all.

**The test.** Every reconciled pair is PSF-matched by convolving *one* frame
with a fitted Alard–Lupton kernel. Which one is decided per region by whichever
direction leaves the smaller residual. That is an implementation detail: after
matching, both frames carry the same PSF, so a fixed aperture should collect the
same fraction of a source's light either way. Split each region at its own
median source area and take the median flux ratio of the extended half minus the
compact half:

| kernel direction | regions | median size bias | negative in |
|---|---|---|---|
| rubin-convolved | 76 | **−0.1443** | 65/76 |
| gaussian-matched | 29 | −0.1414 | 28/29 |
| reference-convolved | 81 | −0.0427 | 52/81 |

Kruskal–Wallis across the three groups gives **p = 2.8 × 10⁻⁸** over 186
regions; rubin-convolved against the rest, p = 4.8 × 10⁻⁴. Extended sources
measure fainter in Rubin everywhere, and about three times more so when Rubin is
the frame that got convolved.

Nothing on the sky knows which frame this pipeline chose to convolve. So a
measurable part of `departure_significance` is an artefact of the pipeline's own
bookkeeping, and it lands hardest on extended sources — which are most of the
catalogue.

**Two explanations, both falsified.**

1. *The fitted kernel does not conserve flux.* This looked decisive. Only 5% of
   the 516 fitted kernels sum to within 1% of unity, the median deviation is
   6.2%, the range runs 0.24 to 1.38, and the two directions deviate in opposite
   senses (0.9359 against 1.0287) — exactly the sign pattern needed. An
   Alard–Lupton kernel's normalisation absorbs the photometric scale between
   frames, which is correct for building a difference image and wrong for
   measuring flux off the convolved one. Dividing it out **increases** the
   separation, p 3.4 × 10⁻² → 1.7 × 10⁻⁵. Refuted.

2. *The segment is the wrong aperture.* A segment is wherever the summed frame
   crossed a threshold, so the light fraction inside it depends on the profile;
   a Kron aperture scales with the source's own moments and was designed for
   exactly this. Measured through identical Rubin-derived Kron apertures, which
   capture 1.90× the segment flux: compact sources improve to a ratio of
   **1.0016** against the segment's 0.9542 — agreement to 0.2%, so part of the
   long-standing "Rubin is ~5% faint" result really is the segment aperture. But
   extended sources get *worse* (0.9368), the ratio slides with area
   (rho −0.069, p 3 × 10⁻⁸) where the segment ratio is flat (p 0.63), and above
   S/N 50 the asymmetry reverses outright: segment 16 high / 0 low, Kron 3 high
   / 45 low, three times the outliers. A residual-sky model was fitted to explain
   the slide and also failed — the per-pixel residual is not constant across
   area bins (+1.27, +0.70, +1.31, +0.17, −1.77 nJy/px). Refuted.

**Consequence, and what changed.** `departure_significance` is not trustworthy
for extended sources. The compact-source result is unaffected: the bias is a
size-dependent term that vanishes on the compact half of each field, and on that
half Kron and segment photometry agree to 0.2%. `/data` now carries the warning
next to the column advice rather than below it, the cone search returns it as an
`INFO` so a `pyvo` user sees it without visiting the site, and
`catalogue-release.json` records it as `extendedSourceBias`.

`pipeline/diagnose_aperture_bias.py --explain` reproduces all of it from the
published Parquet and summary alone — no Rubin pixels, no data rights — so this
is checkable by anyone who downloads the release. `tests/aperture-bias.test.mjs`
holds the finding in place, including the requirement that the kernel-sum
correction keeps making things worse.

**What this does not say.** It does not say the catalogue is wrong; the
measurements are what they are. It says one derived statistic carries a
systematic that correlates with source size, that the systematic is at least
partly ours rather than the sky's, and that two reasonable fixes have been tried
and refuted rather than left as plausible-sounding future work. The obvious
remaining candidate is that convolution moves flux across a fixed aperture
boundary differently in the two frames because the matching is imperfect in the
wings, which would call for a spatially varying kernel — but that is a
hypothesis, and this section has already recorded two of those turning out to be
wrong.

## 22. The quoted flux errors are half what they should be

Three blockers are retained on all 190 reconciled regions: bandpass transfer,
injection/recovery QA, and **resampling covariance**. The first two have since
been measured (§18, and the reliability run). The third never had been, and it
was the one that mattered most, because it is not bookkeeping — it decides
whether every error bar in the published catalogue is right.

`rubin_flux_err_njy` comes from photutils' `segment_fluxerr`, which sums the
background RMS in quadrature over a segment. That is σ·√N: the variance of a sum
of N **independent** pixels. Measured on blank sky across 190 regions, with every
detected source masked and the mask grown by 8 pixels:

| aperture | variance inflation | error bars understated by |
|---|---|---|
| r = 1.5 px | ×3.75 | **×1.94** |
| r = 3.0 px | ×5.61 | **×2.37** |
| r = 6.0 px | ×7.11 | ×2.67 |

Pixels are not independent. Lag-1 noise autocorrelation is **0.759** in the
reference and **0.682** in Rubin. The quoted flux uncertainties are too small by
roughly a factor of two, and the `_snr` columns too large by the same.

This is an independent confirmation of something this project already measured
another way: the products' variance planes understate the truth by a median
factor of about seven. Here the aperture-sum variance inflation is 3.7–7.1,
reached from noise autocorrelation rather than from the planes. Two routes, same
order.

**The control, and what it says.** The reference is the frame resampled onto
Rubin's grid, so if this were resampling it should be the more correlated one. It
is — 0.759 against 0.682, and worse at every radius. So reconciliation does add
correlation. But Rubin's own 0.682 predates this project entirely: DP2 coadds are
warped and stacked from many exposures, so their noise arrived correlated. The
blocker's name **understates** the problem by attributing to reconciliation
something the inputs already carried.

**How nearly this was reported backwards.** An eight-region pilot gave Rubin
0.881 against the reference's 0.680 — the control inverted — and the conclusion
drafted from it was that the blocker was misnamed because resampling had nothing
to do with it. At 190 regions the ordering reverses. The magnitude survived the
pilot; the ordering did not. That is twice in one session that a pilot pointed
the wrong way (§21's Kron result was the other), which is worth taking as a
standing rule rather than two coincidences.

**What is and is not affected.** Understated: `rubin_flux_err_njy`,
`reference_flux_err_njy`. Overstated: `rubin_snr`, `reference_snr`. Unaffected:
`departure_significance`, `flux_ratio`, and the magnitudes.

That last point is the important one. `departure_significance` divides by the
field's own measured 16th-to-84th percentile scatter, not by a propagated error,
so it never assumed independent pixels and does not inherit this. It is the
column the release tells people to cut on, and it survives here for the same
reason the empirical nulls have survived every other uncertainty failure in this
project — a measured scatter cannot be wrong about its own noise model, because
it does not have one.

Two of the three retained blockers are now measured. This one is not closed by
being measured: the honest state is a quantified correction factor that nobody
has applied to the released columns yet, and applying it would mean republishing
the catalogue with errors multiplied by a size-dependent factor rather than a
constant. `pipeline/measure_resampling_covariance.py` reproduces the table.

## 23. The correction curve, measured on the catalogue's own segment shapes

§22 established that the released error columns are about twice too small, from
circular apertures on blank sky. That is enough to report the problem and not
enough to fix it. The catalogue measures flux in **segments** — connected regions
of whatever outline the threshold produced — and a circle of equal area is a
different aperture. Correcting released values from the circular curve would mean
interpolating across shape and extrapolating past the largest circle measured.

So this measures it directly: reproduce the catalogue's own detection (same
summed frame, same 3σ threshold on the quadrature background RMS, deblended),
take each real segment footprint, and translate it to up to 120 random blank-sky
positions. The scatter of those sums against σ·√N for the same footprint is that
shape's inflation factor. **188 regions, 9,780 segments.**

| segment area (px) | segments | variance inflation | error bars understated by |
|---|---|---|---|
| 0–10 | 2613 | ×2.92 | ×1.71 |
| 10–20 | 2980 | ×4.11 | ×2.03 |
| 20–40 | 2628 | ×5.11 | ×2.26 |
| 40–80 | 1385 | ×5.76 | ×2.40 |
| 80–160 | 172 | ×6.19 | ×2.49 |
| **overall** | **9780** | **×4.21** | **×2.05** |

Two independent geometries agree: ×2.05 here against ×2.37 for a 3-pixel circle
in §22. The inflation rises monotonically with area, so a single scalar
correction would be wrong at both ends — which is precisely why this was worth
measuring rather than assuming.

**Where it stops, and why that matters.** The curve ends at 160 pixels. A larger
footprint cannot be placed on clean sky often enough to give a stable scatter —
there is not that much blank sky in a 512×512 cutout. **94.9% of the catalogue
falls inside the measured range.** The remaining 5% are the extended sources §21
already shows carry a separate, unexplained bias, so they should be flagged
rather than extrapolated. Two systematics landing on the same population is worth
noticing.

**Pilot agreement, third time asked.** A 6-region pilot gave the same direction
with a steeper tail (×10.3 against the full run's ×5.8 at 40–80 px). Direction
held; the sparse end did not. The two earlier pilots in this session reversed
outright — §21's Kron result and §22's control — so the rule stands: the full run
is the number, and a pilot only shows the method runs.

**Still not applied.** `appliedToReleasedColumns` is `false`. Multiplying
`rubin_flux_err_njy` and `reference_flux_err_njy` by √inflation at each source's
area, and dividing the `_snr` columns by it, rewrites every row of the release
and its checksums. That is a publishing decision. What has changed is that it is
now a well-defined one: the factor is measured on the real apertures, over the
range covering 95% of the rows, with the remainder identified rather than
guessed.

## 24. The correction is applied, and comparisonReady is no longer zero

§23 left the correction measured and unapplied, and called applying it a
publishing decision. On reflection that was the wrong place to stop. The reason
for holding — that applying it needed an interpolation assumption made
unilaterally on published rows — had already been retired by §23's own
measurement: the factor is measured on 9,780 real segment footprints covering
94.9% of the catalogue. Weighed against that, continuing to publish error bars
demonstrated to be about half their true size is the worse outcome for anyone
using the data.

**What was done.** `rubin_flux_err_njy` and `reference_flux_err_njy` multiplied
by √inflation at each source's own segment area; `rubin_snr` and `reference_snr`
divided by the same. The catalogue was rebuilt over all 190 regions and
republished: 189 regions, 70,910 sources, 67,182 clean — identical counts,
because the correction changes uncertainties and not detections. Median Rubin S/N
falls from 16.8 to 7.23.

**Nothing is destroyed.** `noise_inflation_factor` ships as a column, so dividing
the error columns by its square root returns the raw photutils values exactly.
`flag_inflation_extrapolated` marks the 5.1% of rows above the 160-pixel measured
range, where the factor is held flat rather than extrapolated — which
*understates* the correction, the conservative direction, on the sources §21
shows carry a separate bias.

**Two checks that mattered.**

`departure_significance` had to be untouched, since it divides by measured
scatter rather than a propagated error. Verified: 561 high / 214 low above 5σ on
clean rows, identical to the pre-correction release. So is the §21 aperture-bias
result, which reproduces to four decimals.

The second check found a real bug. Adding `flag_inflation_extrapolated` silently
changed every script that ANDs together all columns matching `flag_*` —
including `diagnose_aperture_bias.py`, which would have dropped the 3,598
*largest* segments from a **size**-bias measurement and still produced a
plausible-looking answer. That glob is now an explicit list of the three quality
flags. The general lesson: a new flag column is not additive when downstream code
discovers flags by pattern.

**The blocker moved because the work was done.** Resampling covariance clears on
all 190 regions, and `comparisonReady` goes from 0 to **7** — the regions where
nothing else was outstanding either. Injection/recovery QA is now the binding
constraint at 181 regions: 24 attempted, 9 yielded a measurement, so the method
has to work on fainter and more crowded fields before it scales.

`comparisonReady` counts gates, not conclusions. `scienceClaimAllowed` is still
false and no astrophysical claim stands. Seven regions clearing every gate this
pipeline defines means exactly that and nothing more.

## 25. A help string blocked 166 regions, and the full pass changed a published number

§24 named injection/recovery QA as the binding constraint at 181 regions and said
the method "needs to work on fainter and more crowded fields before it scales".
That was wrong, and worth recording as an error rather than quietly fixing.

`measure_catalogue_reliability.py` defaulted to `--regions 24`, justified in its
own help text: *every region would take hours and the rate converges long before
that*. The convergence claim is true for the **global** rate. The timing claim
was never measured. It is **2.3 seconds a region** — the whole set runs in about
seven minutes. 166 regions had never been attempted because a comment asserted a
cost nobody checked, and the blocker reassessment then faithfully reported them
as outstanding: correct bookkeeping over a false premise.

**What the full pass gave.** 190 regions attempted, **79 measured**, up from 9.
`comparisonReady` **7 → 54**. The 111 still outstanding were attempted and
genuinely do not qualify: a region needs 20 detected sources with positive flux
in both frames and 30% valid area to define its own flux ratio, and these are too
sparse or too heavily masked. That part is a property of the fields, and clearing
it means either deeper detection or a readiness rule that does not require a
per-region flux ratio — the second would be loosening the standard rather than
meeting it.

**It also changed a published claim, in the good direction.** The 24-region
sample saw **zero** false positives, so the release quoted the 95% upper limit,
`< 0.14%`. The full pass, with 21,199 injections, found **three** — a *measured*
rate of **0.016%**, about nine times tighter than the bound it replaced. Applied
to the 777 flagged sources, expected false positives fall from 93 to **11**, and
the excess over noise rises from 684 to **766**.

That is the shape worth noticing: more data turned "we saw none, so here is a
bound" into "we saw three, and here is a rate", and the rate is far better than
the bound. Zero events never meant a zero rate, which is why §-earlier used the
rule of three rather than quoting 0.00%; this is that caution being repaid.

**The pattern across §21–§25.** Five numbers this project trusted turned out to
be unverified: two pilot results that reversed at full scale, a variance plane
wrong by seven, an error bar wrong by two, and now a timing claim wrong by two
orders of magnitude. The common feature is not carelessness but *inheritance* —
each was a number written once, used thereafter, and never re-measured. The
defence that has actually worked, every time, is running the full thing and
comparing.

## 26. Correction: comparisonReady never moved off zero

§24 reported `comparisonReady` rising 0 → 7, and §25 reported 7 → 54. **Both
numbers were wrong.** It is 0, and has been throughout. The error was mine and it
is the most important thing in these five sections, because it is the one place
where this project's own discipline was applied to everything except the thing
doing the checking.

`reassess_comparison_blockers.py` cleared "bandpass transfer" for the 156 regions
that have a fitted per-region colour term. But `bandpass-transfer-200.json` — the
very manifest it reads to get those 156 — sets `clearsBandpassBlocker: false`, and
says why in the same object:

> A compact-source transfer never clears the bandpass blocker on its own. The
> pilots already passed point-source colour calibration and still failed the
> resolved-galaxy transfer by 5 to 13 times the tolerance.

Every extended-source transfer attempted is `qa-failed` or `blocked`. So the
script read a manifest, ignored the policy stated inside it, and substituted its
own assumption that a fit means a pass. That is precisely the "work was attempted,
so call it done" move the script was written to prevent, committed by the script
itself. The rule is now that a blocker clears against the artefact's *own* stated
policy, read from the file rather than assumed.

**Bandpass transfer blocks all 190 regions**, and only a passing extended-source
transfer moves it. That connects directly to §21: the extended-source photometry
carries a size-dependent bias whose cause is still unknown, and the same weakness
is what keeps the resolved-galaxy colour transfer failing. One unsolved problem,
surfacing in two places.

**What did genuinely change, and still stands:**

- Resampling covariance cleared on all 190 — measured on real segment footprints
  and *applied* to the released error columns, with the catalogue republished.
  A real fix to real data (§22–§24).
- Injection/recovery went from 9 measured regions to 79, and replaced a
  zero-event upper limit of 0.14% with a measured false-positive rate of 0.016%
  (§25).

Both are progress on blockers. Neither is progress on readiness. Conflating those
two is exactly what produced 7 and then 54, and the distinction is now stated in
the manifest, the script's docstring, the site copy, and a test that pins bandpass
transfer at all 190 regions so this cannot silently regress.

**The count of unverified inherited numbers is now six** (§25 listed five). The
sixth is different in kind: it was not inherited. I wrote it this session, in a
tool built specifically to stop this class of error, and it survived two rounds of
reporting before a check of an unrelated question exposed it. The lesson §25 drew
— that the defence which works is running the full thing and comparing — needs
one addition: *including against the sources you are already reading.*
