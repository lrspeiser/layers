# Goals: 10% comparison coverage

## The target, pinned down

"Coverage" needs a denominator. The one that matters is **Rubin×survey comparison
pairs**: every exact tract-by-survey overlap in the index is one comparison that
could be made.

| | count |
|---|---|
| Possible comparison pairs | 22,921 (2,191 tracts × median 10 surveys) |
| **10% target** | **2,292** |
| Measured today | 48 (0.21%) |

Taking tracts in order of how many surveys overlap them:

| tracts | pairs | share |
|---|---|---|
| 50 (today's set) | 663 | 2.9% |
| 100 | 1,313 | 5.7% |
| **200** | **2,598** | **11.3%** |
| 300 | 3,798 | 16.6% |

**200 tracts, fully compared, clears 10% with margin.** 33 of the current 50 are
already in that top 200, so 167 new tracts are needed.

## What the 200 tracts actually require

| survey | family | tracts | comparison kind | operator |
|---|---|---|---|---|
| legacy-surveys-dr10 | optical | 200 | same-band difference | **exists** |
| panstarrs-dr2 | optical | 197 | same-band difference | exists |
| hsc-ssp-pdr2 | optical | 166 | same-band difference | exists |
| des-dr2 | optical | 164 | same-band difference | exists |
| 2mass | uv-ir | 200 | SED consistency | **new** |
| galex-gr6-7 | uv-ir | 200 | SED consistency | new |
| unwise | uv-ir | 200 | SED consistency | new |
| gaia-dr3 | astrometry | 200 | catalog cross-match | **new** |
| hipass | neutral-gas | 200 | scaling-relation residual | **new** |
| planck-2018 | cmb-lss | 200 | mass vs light | **new** |
| vlass | radio | 197 | counterpart association | **new** |
| ztf-dr | time-domain | 190 | variability | **new** |
| hst / euclid-q1 / jwst | high-resolution | 229 | morphology validation | **new** |
| erosita-erass1 | high-energy | 55 | counterpart association | new |

**The bottleneck is not data volume. It is comparison operators: 1 exists, 6 are
missing.**

Acquisition cost, measured from this session's actual runs (3.6 SODA cutouts and
0.042 GB per tract-band):

| | requests | wall clock @28/min | disk |
|---|---|---|---|
| 167 new tracts × 2 bands | 1,219 | **41 min** | 14 GB |
| 167 new tracts × 3 bands | 1,828 | 61 min | 21 GB |

124 GB free. Rubin acquisition is under an hour and is the easy part.

## The strategic point

**Six of the seven new operators do not depend on the bandpass transfer.** They
are not photometric-difference measurements, so the blocker that has stopped this
project twice does not gate them:

- Gaia cross-match compares positions and proper motions.
- HIPASS compares HI mass and linewidth against optical light — a scaling-relation
  residual, which is *directly* the baryonic-mass question and needs no pixel
  photometry equivalence.
- Planck/ACT compares lensing convergence against stellar light.
- VLASS and eROSITA ask whether a counterpart exists at all.
- ZTF asks whether something changed.
- HST/JWST ask whether a structure seen in Rubin is real at higher resolution.

Only the four optical-baseline surveys need the bandpass work. **Everything else
can produce defensible results while that stays blocked.**

---

## Goals

### G0 — Acquire the 200-tract set
Extend Rubin pixels from 50 to the top 200 tracts by overlap count, two bands each,
plus a second band for the 11 current tracts that lack one.
**Measured outcome:** 200 regions with IMAGE/VARIANCE/MASK/WCS validated and
checksummed, ≥180 with two bands. ~41 min of SODA time, ~14 GB.

### G1 — Same-band optical, all four surveys
DES DR2 and HSC PDR2 currently have **zero pixels fetched** despite 164 and 166
overlapping tracts. Fetch them and Pan-STARRS at scale, and run the existing
reconcile → recovery → anomaly chain.
**Measured outcome:** ~727 optical pairs reconciled with PSF/sky/flux gates,
per-region limiting surface brightness, and an anomaly scan. Also gives the first
three-way optical cross-check, which is the strongest test of whether the
field-dependent colour term is a Legacy artefact or real.

### G2 — Gaia cross-match operator
Cheapest operator: no pixel alignment, catalog only. Also closes the registration
blocker — `gaia_registration.py` already implements epoch propagation and would
move astrometry from 30/48 toward the pilots' 0.086–0.220″.
**Measured outcome:** 200 pairs; per-tract astrometric residual after proper-motion
propagation; a list of Rubin sources with no Gaia counterpart and vice versa.

### G3 — SED-consistency operator (GALEX, 2MASS, unWISE)
Fit a stellar-population plus dust model to the UV–optical–IR points and measure
the departure at the Rubin band, with uncertainty.
**Measured outcome:** 600 pairs; per-source SED residual in magnitudes with a
stated expectation; anomaly classes = AGN excess, unusual dust, photometric-redshift
failure.

### G4 — Neutral-gas scaling-relation operator (HIPASS)
Compare HI mass and linewidth against optical luminosity — a baryonic
Tully-Fisher residual. **This is the goal most directly aimed at the
dark-matter question and it is not blocked by anything.**
**Measured outcome:** 200 pairs; per-object Δ(baryonic Tully-Fisher) with
statistical and systematic uncertainty, in the same schema as the existing
WISE-vs-SPARC comparisons.

### G5 — Mass-vs-light operator (Planck 2018, ACT DR6)
Compare lensing convergence against integrated stellar light per tract.
**Measured outcome:** 200 pairs; convergence-to-light ratio with uncertainty;
ranked departures.

### G6 — Counterpart-association operator (VLASS, eROSITA)
Ask whether a radio or X-ray source has an optical counterpart in Rubin, and at
what significance, using the empirical noise floor already measured.
**Measured outcome:** 252 pairs; per-source counterpart / no-counterpart with a
limiting magnitude for the non-detections. Radio or X-ray sources with no optical
counterpart at Rubin depth are a genuinely interesting anomaly class.

### G7 — Variability operator (ZTF)
Compare the Rubin epoch against the ZTF light curve at the same position.
**Measured outcome:** 190 pairs; per-source variability flag and amplitude;
anomalies = sources that changed between epochs.

### G8 — Morphology validation (HST, JWST, Euclid Q1)
229 pairs, small but high value: the independent check on whether structure seen
in Rubin is real.
**Measured outcome:** every surviving anomaly candidate that falls in an HST,
JWST, or Euclid footprint gets an independent-resolution verdict.

### G9 — Run the anomaly scanner across all of it
The scanner is operator-agnostic in design but currently only consumes the
optical difference. Extend it to score each operator's residual against that
operator's own empirical null.
**Measured outcome:** one ranked anomaly list across ~2,600 comparisons, each
candidate carrying its expectation, empirical significance, boring explanations,
and falsification tests.

### G10 — Put it on the site
None of this session's output renders yet. Three public manifests already exist
and are unused.
**Measured outcome:** a differences index ranked by significance and filterable by
family; the six-gate progress per product; the measured limiting surface
brightness and noise ratio on each tract page; a residual layer in the swipe
viewer.

---

## Two honesty constraints to carry forward

1. **Coverage is not readiness.** Reaching 2,292 measured comparisons does not
   make them comparison-ready. Most optical pairs will remain blocked on bandpass.
   Report the two numbers separately and never let coverage imply a claim.
2. **Every operator needs its own empirical null.** The lesson of this session is
   that formal uncertainties understate the truth by a median factor of 7. Each
   new operator must ship with its own injection/recovery-equivalent calibration
   before any of its residuals are ranked, or the anomaly list will be noise.
