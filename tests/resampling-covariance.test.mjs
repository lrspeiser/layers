import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, statSync } from "node:fs";

// "resampling covariance" was one of three blockers retained on all 190 regions
// and the only one never measured. It is now measured, and the measurement says
// the catalogue's quoted flux errors are about half what they should be.

const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const slim = read("public/data/layers/selected-regions/resampling-covariance-slim.json");

test("measured across the full region set, not a pilot", () => {
  // The pilot inverted the control. Eight regions was enough to reach the
  // opposite conclusion, so the size of the run is part of the claim.
  assert.ok(
    slim.regionsMeasured >= 150,
    `measured on ${slim.regionsMeasured} regions; the 8-region pilot gave the wrong ordering`,
  );
});

test("pixel noise is correlated in both frames", () => {
  for (const frame of ["rubin", "reference"]) {
    const lag1 = slim.summary[frame].medianLag1Autocorrelation;
    assert.ok(lag1 > 0.3, `${frame} lag-1 autocorrelation ${lag1} should show real correlation`);
  }
});

test("the resampled frame is the more correlated one", () => {
  // This is the control that ties part of the effect to reconciliation. If it
  // ever inverts again, the conclusion in the docstring has to change with it.
  assert.ok(
    slim.summary.reference.medianLag1Autocorrelation >
      slim.summary.rubin.medianLag1Autocorrelation,
    "resampling should add correlation, not remove it",
  );
});

test("but resampling is not the whole of it", () => {
  // Rubin was never resampled by this project and is still strongly correlated,
  // which is why the blocker's name understates the problem.
  assert.ok(
    slim.summary.rubin.medianLag1Autocorrelation > 0.5,
    "Rubin's own noise arrived correlated from DP2's warping and stacking",
  );
  assert.match(slim.control.verdict, /understates the problem/);
});

test("error bars are understated by a factor worth acting on", () => {
  const r3 = slim.summary.reference.byRadius["r3.0"];
  assert.ok(r3.medianVarianceInflation > 2, "variance inflation should be substantial");
  assert.ok(
    r3.medianErrorBarUnderstatedBy > 1.5,
    "if this fell to ~1 the blocker would be closeable rather than reportable",
  );
});

test("it names which columns it invalidates and which it does not", () => {
  const affected = slim.affectedColumns;
  assert.ok(affected.understated.includes("rubin_flux_err_njy"));
  assert.ok(affected.overstated.includes("rubin_snr"));
  // The recommended column must stay in the unaffected list: it divides by
  // measured scatter, not by a propagated error.
  assert.ok(affected.unaffected.includes("departure_significance"));
  assert.match(affected.why, /segment_fluxerr/);
});

test("the page imports the slim file, not the 750 kB one", () => {
  const page = readFileSync("app/data/page.tsx", "utf8");
  assert.match(page, /resampling-covariance-slim\.json/);
  assert.ok(
    !/"@\/public\/data\/layers\/selected-regions\/resampling-covariance\.json"/.test(page),
    "importing the full file would ship the per-region block into the worker bundle",
  );
  assert.ok(statSync("public/data/layers/selected-regions/resampling-covariance-slim.json").size < 50_000);
});
