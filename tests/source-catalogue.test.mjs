import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));

// The catalogue is the first product here another scientist could analyse
// directly, so what it claims about itself has to hold.
test("the source catalogue reports a consistent, honestly-scoped summary", async () => {
  const summary = await readJson("public", "data", "layers", "selected-regions", "source-catalogue.json");

  assert.ok(summary.counts.sources > 10000);
  assert.ok(summary.counts.regions > 100);
  assert.ok(summary.counts.clean <= summary.counts.sources);

  // Two significance columns exist and mean different things. The one measured
  // against the field's own scatter must flag far fewer sources than the one
  // measured from zero, because the zero-based column is dominated by the ~7%
  // offset between Rubin and these references.
  assert.ok(
    summary.counts.cleanAbove5SigmaFromFieldRatio < summary.counts.cleanAbove5SigmaFromZero,
    "the field-relative cut should be stricter than the from-zero cut",
  );
  assert.ok(summary.counts.cleanAbove5SigmaFromFieldRatio < summary.counts.clean * 0.1);
  assert.match(summary.method.whichSignificanceToUse, /departure_significance is the one to use/);

  // The propagated error is documented as understating, not quietly preferred.
  assert.match(summary.method.whichSignificanceToUse, /Poisson/);
  assert.match(summary.method.uncertainty, /never the propagated variance planes/);

  // A catalogue built from access-restricted pixels must not be published, and
  // must not print the local paths it was written to either.
  assert.equal(summary.products.published, false);
  assert.match(summary.products.note, /not published in this repository/i);
  assert.doesNotMatch(JSON.stringify(summary.products), /pipeline\/results/);
  assert.match(summary.caveat, /not a detection/i);
});

test("no flag in the catalogue is unreachable", async () => {
  const summary = await readJson("public", "data", "layers", "selected-regions", "source-catalogue.json");
  // A flag that never fires advertises a check that is not happening. The
  // signal-to-noise flag was exactly that -- detection at 3 sigma over 5 pixels
  // guarantees a higher integrated ratio -- so it became a column instead.
  assert.equal(summary.counts.flag_low_snr, undefined, "the dead S/N flag should be gone");
  assert.ok(summary.counts.medianRubinSnr > 5, "S/N is published as a measurement");
  assert.ok(summary.counts.flaggedNearEdge > 0, "edge flag never fires");
  assert.ok(summary.counts.flaggedNegativeReference > 0, "negative-reference flag never fires");
  assert.match(summary.method.flagsNotFiltering, /never fired/);
});

test("every region in the catalogue records which difference plane it used", async () => {
  const summary = await readJson("public", "data", "layers", "selected-regions", "source-catalogue.json");
  for (const region of summary.regions) {
    assert.match(region.differencePlane, /^(KERNEL_DIFFERENCE|DIFFERENCE)$/);
    assert.ok(region.sources > 0);
    assert.ok(region.backgroundRmsMedianNjy > 0);
  }
  // The fitted kernel should be what most regions used, since it measurably
  // improved the subtraction in 158 of 188.
  const kernel = summary.regions.filter((r) => r.differencePlane === "KERNEL_DIFFERENCE").length;
  assert.ok(kernel > summary.regions.length / 2);
});
