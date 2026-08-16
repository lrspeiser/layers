import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));

// Synthetic photometry is the only measurement here with no observational error
// in it, which is what makes it able to say whether the empirical scatter is the
// filters. These guard the properties that give it that authority.
test("the synthetic colour term is computed from real filters and real spectra", async () => {
  const s = await readJson("public", "data", "layers", "selected-regions", "synthetic-bandpass.json");
  assert.ok(s.counts.standardsIntegrated >= 8, "too few standards to fit a colour term");
  assert.ok(s.counts.filtersLoaded >= 4);
  assert.match(s.method.spectra, /CALSPEC/);
  // Blackbodies would miss the molecular bands that separate late types.
  assert.match(s.method.spectra, /rather than blackbodies/);
  // A truncated integral silently returns a magnitude for a band the star was
  // never measured through.
  assert.match(s.method.coverageRequirement, /skipped/);

  for (const [, fit] of Object.entries(s.predictedColourTerms)) {
    assert.ok(Number.isFinite(fit.predictedColourTermPerMag));
    // The filters must not require field-to-field variation: if a straight line
    // did not fit the synthetic photometry, the whole conclusion would invert.
    assert.ok(fit.residualRmsMag < 0.02, "a linear term should fit the filters closely");
    assert.ok(fit.standards >= 4);
  }
});

test("the synthetic term is compared to the empirical one, not left standing alone", async () => {
  const s = await readJson("public", "data", "layers", "selected-regions", "synthetic-bandpass.json");
  const c = s.comparisonToEmpirical;
  assert.ok(c, "the point of the synthetic term is the comparison");

  // The two must agree to well under a tenth of a magnitude per magnitude, or
  // the empirical regression was not measuring filter physics at all.
  assert.ok(Math.abs(c.differencePerMag) < 0.05);

  // And the empirical spread must exceed what the filters allow by a wide
  // margin -- that gap is the finding.
  assert.ok(c.empiricalFieldSpread > c.syntheticResidualRms * 10);
  assert.match(c.reading, /not the bandpass/i);

  // The stellar-only limit must travel with the result.
  assert.match(s.caveat, /Galaxies/);
});
