import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// The difference maps compare sky 9 to 13.5 years apart, and the scanner's
// boring explanations cover mask edges and PSF wings but not time. These two
// checks close that gap. Both return null results, so most of what these tests
// guard is the evidence that the null means something.

const read = (p) => JSON.parse(readFileSync(p, "utf8"));
const epochs = read("public/data/layers/selected-regions/epoch-separation.json");
const motion = read("public/data/layers/selected-regions/candidate-epoch-check.json");
const variability = read("public/data/layers/selected-regions/candidate-variability-check.json");

test("the epoch gaps are real and recorded per reference", () => {
  assert.ok(epochs.rubinEpochJyear > 2024 && epochs.rubinEpochJyear < 2027);
  for (const [id, pair] of Object.entries(epochs.pairs)) {
    assert.ok(pair.yearsFromRubin > 5, `${id} should be years before Rubin`);
    assert.ok(pair.properMotionMovingHalfAPsfMasPerYear > 0);
  }
});

test("both time-domain checks ran on the same candidates", () => {
  assert.equal(motion.candidatesChecked, variability.candidatesChecked);
  assert.ok(motion.candidatesChecked > 0, "a check over zero candidates proves nothing");
});

test("each null result ships a positive control", () => {
  // Every candidate returning zero is also exactly what a broken query or a
  // wrong path looks like. Both nearly happened: the motion check first read
  // ra/dec where the scanner writes raDeg/decDeg and reported a confident
  // "0 of 0". The control is what separates a null from a bug.
  assert.ok(motion.positiveControl.denseFieldSources > 0, "Gaia query must return sources somewhere");
  assert.ok(
    motion.positiveControl.firstCandidateWithin60Arcsec > 0,
    "sparsity, not a dead query, must explain zero within 3 arcsec",
  );
  assert.ok(variability.positiveControl.objectsWithLightCurves > 0);
  assert.ok(
    variability.positiveControl.objectsAboveVariabilityThreshold > 0,
    "a variable must exist in these fields, or finding none proves nothing",
  );
});

test("the variability threshold comes from the data, not from theory", () => {
  // ZTF error bars are optimistic, so a reduced chi-square of 1 is not the line.
  assert.ok(variability.reducedChiSquareThreshold > 5);
  assert.match(variability.thresholdBasis, /99th percentile/i);
});

test("what the checks cannot rule out is recorded with them", () => {
  for (const payload of [motion, variability]) {
    assert.ok(Array.isArray(payload.limitsOfThisTest));
    assert.ok(payload.limitsOfThisTest.length >= 2);
  }
  // Both are catalogue-limited, and both say so.
  assert.match(JSON.stringify(motion.limitsOfThisTest), /G *= *21|complete/i);
  assert.match(JSON.stringify(variability.limitsOfThisTest), /20\.5|complete/i);
  // Solar-system objects stay untested by either.
  assert.match(JSON.stringify(variability.limitsOfThisTest), /asteroid|solar-system/i);
});

test("the candidate list is still not a set of detections", () => {
  const anomalies = read("public/data/layers/selected-regions/region-anomalies-hsc.json");
  assert.equal(anomalies.policy.theseAreNotDetections, true);
});
