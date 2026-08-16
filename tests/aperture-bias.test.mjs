import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// The catalogue carries a size-dependent bias whose strength depends on which
// frame the PSF-matching kernel convolved -- an implementation detail that
// cannot be astrophysical. These tests exist so that the finding stays stated,
// and so that a future pipeline change which alters it cannot pass silently.

const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const bias = read("public/data/layers/selected-regions/aperture-bias.json");
const release = read("public/data/layers/selected-regions/catalogue-release.json");

test("the bias is measured across the whole catalogue, not a pilot", () => {
  const regions = Object.values(bias.groups).reduce((sum, g) => sum + g.regions, 0);
  assert.ok(
    regions >= 150,
    `measured on ${regions} regions; a claim this consequential needs the full set`,
  );
});

test("the kernel direction still splits the size bias", () => {
  assert.ok(bias.kruskalWallisP !== null && bias.kruskalWallisP < 1e-4, "groups must differ");
  const rubin = bias.groups["rubin-convolved"].medianSizeBias;
  const reference = bias.groups["reference-convolved"].medianSizeBias;
  assert.ok(rubin < reference, "rubin-convolved should carry the stronger deficit");
});

test("extended sources sit low in every group, not just one", () => {
  for (const [name, group] of Object.entries(bias.groups)) {
    assert.ok(group.medianSizeBias < 0, `${name} should show extended sources fainter in Rubin`);
  }
});

test("the falsified explanations stay recorded as falsified", () => {
  // Both were plausible enough to act on. Recording the refutation is what stops
  // either being retried as though it were untested.
  assert.equal(bias.falsifiedHypotheses.length, 2);
  for (const item of bias.falsifiedHypotheses) {
    assert.equal(item.verdict, "falsified");
    assert.ok(item.evidence.length > 20, "a verdict without evidence is an assertion");
  }
  assert.ok(
    bias.kernelSumCorrectedP < bias.kruskalWallisP,
    "the kernel-sum correction must still make the split worse, not better",
  );
});

test("the release tells users about it where they choose a column", () => {
  assert.ok(release.extendedSourceBias, "catalogue-release.json must carry the warning");
  assert.match(release.extendedSourceBias, /compact/i);
  assert.match(release.extendedSourceBias, /diagnose_aperture_bias/);
  assert.match(release.whichSignificance, /compact/i);
  assert.match(release.caveat, /size-dependent/i);
});

test("the cone search warns machine clients too", () => {
  const route = readFileSync("app/api/scs/route.ts", "utf8");
  assert.match(route, /extendedSourceBias/, "a pyvo user never reads the website");
});

test("the finding is reproducible from published files alone", () => {
  const script = readFileSync("pipeline/diagnose_aperture_bias.py", "utf8");
  assert.match(script, /public\/data\/catalogue\/rubin-reference-sources\.parquet/);
  assert.ok(
    !/pipeline\/results/.test(script),
    "must not depend on restricted pixels, or nobody can check it",
  );
});
