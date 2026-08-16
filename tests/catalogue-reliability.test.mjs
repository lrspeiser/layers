import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));

// Completeness and false-positive rate are the numbers that make a catalogue
// usable by someone else. They are measured by injection here, and the shape of
// the result is what proves the measurement was not broken.
test("the selection function behaves like a selection function", async () => {
  const r = await readJson("public", "data", "layers", "selected-regions", "catalogue-reliability.json");
  const bins = r.completenessByMagnitude;
  assert.ok(bins.length >= 8);

  // Completeness must fall as sources get fainter. If it did not, the injection
  // or the recovery matching would be wrong.
  const bright = bins.filter((b) => b.magAB <= 22).map((b) => b.completeness);
  const faint = bins.filter((b) => b.magAB >= 25).map((b) => b.completeness);
  assert.ok(Math.min(...bright) > 0.95, "bright injections should almost all be recovered");
  assert.ok(Math.max(...faint) < 0.5, "faint injections should mostly be missed");
  assert.ok(r.counts.median90PercentCompleteMagAB > 20);

  // A false-positive rate of exactly 1 means the null is wrong, which is what
  // this measurement did on its first run: the injected ratio came from the
  // per-pixel median, dominated by sky, instead of from detected sources.
  assert.ok(r.counts.falsePositiveRate < 0.1, "a rate this high means a broken null");
  assert.ok(r.counts.falsePositiveRate >= 0);
  // Zero observed events still needs a quotable bound.
  if (r.counts.rateIsAnUpperLimit) {
    assert.ok(r.counts.falsePositiveRate95UpperLimit > 0);
    assert.ok(r.counts.falsePositiveRate95UpperLimit < 0.05);
  }

  // False positives should concentrate where measurement is hard, not at the
  // bright end where the ratio is well determined.
  const brightFp = bins.filter((b) => b.magAB <= 22).map((b) => b.falsePositiveRate ?? 0);
  assert.equal(Math.max(...brightFp), 0, "bright sources should not be flagged by chance");
});

test("the rate is applied to the catalogue rather than left abstract", async () => {
  const r = await readJson("public", "data", "layers", "selected-regions", "catalogue-reliability.json");
  const applied = r.applicationToCatalogue;
  assert.ok(applied, "the measured rate must be multiplied by something");

  // With no false positive observed, the expectation must use the 95% upper
  // limit rather than a rate of zero: zero events does not mean a zero rate, and
  // "0 of 622 are noise" would be a stronger claim than 2,157 trials support.
  assert.ok(Number.isFinite(applied.rateUsed));
  assert.equal(
    Math.round(applied.expectedFalsePositives),
    Math.round(applied.cleanSources * applied.rateUsed),
  );
  if (applied.rateIsUpperLimit) {
    assert.equal(applied.falsePositiveRate, 0);
    assert.ok(applied.rateUsed > 0, "an upper limit must be greater than the zero it replaces");
    assert.match(applied.reading, /upper limit/i);
  }
  assert.ok(applied.excessOverNoise < applied.flaggedAbove5Sigma);
  // Excess must never be described as a detection count: the injection carries
  // identical colours, so it cannot produce a bandpass-driven departure.
  assert.match(applied.reading, /not the same as real|upper bound/i);
  assert.match(applied.reading, /bandpass/i);
});

test("regions that drop out of the measurement are explained", async () => {
  const r = await readJson("public", "data", "layers", "selected-regions", "catalogue-reliability.json");
  assert.ok(r.counts.regionsYieldingAMeasurement <= r.counts.regionsAttempted);
  // Fewer regions measured than attempted is fine; unexplained is not.
  if (r.counts.regionsYieldingAMeasurement < r.counts.regionsAttempted) {
    assert.ok(r.counts.whySomeRegionsDropOut.length > 40);
  }
});
