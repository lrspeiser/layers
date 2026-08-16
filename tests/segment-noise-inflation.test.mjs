import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// The circular measurement said the released error columns are about twice too
// small. This one measures the same thing on the catalogue's real segment
// shapes, which is what a correction actually needs. It is deliberately not
// applied to anything.

const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const inflation = read("public/data/layers/selected-regions/segment-noise-inflation.json");
const circular = read("public/data/layers/selected-regions/resampling-covariance-slim.json");

test("measured on the full region set", () => {
  // Two pilots in this session pointed the opposite way from their full runs.
  assert.ok(inflation.regionsMeasured >= 100, `only ${inflation.regionsMeasured} regions`);
  assert.ok(inflation.segmentsMeasured >= 1000, `only ${inflation.segmentsMeasured} segments`);
});

test("detection settings still match the catalogue's", () => {
  // If these drift apart, this stops describing the segments the catalogue uses
  // and the correction curve silently becomes about different apertures.
  const builder = readFileSync("pipeline/build_source_catalogue.py", "utf8");
  const mine = readFileSync("pipeline/measure_segment_noise_inflation.py", "utf8");
  const detectSigma = /DETECT_SIGMA = ([\d.]+)/;
  assert.equal(
    builder.match(detectSigma)?.[1],
    mine.match(detectSigma)?.[1],
    "detection threshold must match build_source_catalogue.py",
  );
  assert.match(mine, /detection on the sum of both frames|sum of both frames/i);
});

test("inflation rises with segment area", () => {
  // A correction that ignored area would be wrong at both ends. The curve has to
  // actually depend on area for a per-source factor to be worth applying.
  const bins = inflation.byArea;
  assert.ok(bins.length >= 3, "need several area bins to see a trend");
  const first = bins[0].medianVarianceInflation;
  const last = bins[bins.length - 1].medianVarianceInflation;
  assert.ok(last > first, "larger segments should show more inflation, not less");
});

test("it agrees with the circular measurement in magnitude", () => {
  // Different geometry, same underlying noise. If these disagreed wildly one of
  // them would be measuring something else.
  const mine = inflation.overall.medianErrorBarUnderstatedBy;
  const theirs = circular.summary.reference.byRadius["r3.0"].medianErrorBarUnderstatedBy;
  assert.ok(mine > 1.3 && mine < 4.5, `overall factor ${mine} outside a believable range`);
  assert.ok(
    Math.abs(mine - theirs) < 1.5,
    `segment factor ${mine} and circular factor ${theirs} should be the same order`,
  );
});

test("it is explicitly not applied to the release", () => {
  assert.equal(inflation.appliedToReleasedColumns, false);
  assert.match(inflation.note, /publishing decision/);
  // And the blocker reassessment must still count it as outstanding.
  const blockers = read("public/data/layers/selected-regions/blocker-reassessment-slim.json");
  assert.ok(
    blockers.blockersRemaining["resampling covariance"] > 0,
    "measuring a systematic does not clear it; applying it does",
  );
});
