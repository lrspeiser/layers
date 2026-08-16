import test from "node:test";
import assert from "node:assert/strict";
import { access, readFile, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));

// The explorer draws images the browser fetches by path. A missing file is a
// blank frame with no error anywhere, so every path the page can request is
// checked to exist rather than assumed.
test("every image and peak file the difference explorer can request exists", async () => {
  const index = await readJson("public", "data", "layers", "selected-regions", "difference-index.json");
  assert.ok(index.regions.length > 100, "expected the rendered region set");
  assert.ok(index.previewRoot.startsWith("/"));
  assert.ok(index.peakRoot.startsWith("/"));

  let crossBand = 0;
  for (const region of index.regions) {
    // The band is part of the filename and is not always r. Hardcoding it is
    // exactly the bug this test caught the first time it ran.
    assert.ok(region.rubinBand, `${region.regionId} has no recorded Rubin band`);
    assert.ok(region.referenceBand, `${region.regionId} has no recorded reference band`);
    if (!region.sameNamedBand) crossBand += 1;

    const names = [
      `rubin-${region.rubinBand}`,
      `reference-${region.referenceBand}`,
      "difference",
      "difference-overlay",
    ];
    for (const name of names) {
      await access(join(root, "public", index.previewRoot.slice(1), region.regionId, `${name}.png`));
    }
    await access(join(root, "public", index.peakRoot.slice(1), `${region.regionId}.json`));
  }
  // Cross-band pairs disagree everywhere for a trivial reason, so the page has
  // to be able to label them. Losing the count would hide that.
  assert.equal(crossBand, index.counts.crossBandRegions);
});

test("the index stays small enough to import into a route", async () => {
  // A 525 KB module once broke every tract page by pushing its worker chunk past
  // what the runtime would load. The full difference file is ~0.9 MB and must
  // never be the thing a page imports.
  const info = await stat(join(root, "public", "data", "layers", "selected-regions", "difference-index.json"));
  assert.ok(info.size < 200 * 1024, `index is ${Math.round(info.size / 1024)} KB, too large to import`);
});

test("peak markers carry coordinates a browser can place, and honest labelling", async () => {
  const index = await readJson("public", "data", "layers", "selected-regions", "difference-index.json");
  assert.match(index.caveat, /bandpass/i, "the bandpass caveat must travel with the data");
  assert.equal(index.scaling.perPixelVarianceUsed, false);

  const sample = index.regions.slice(0, 25);
  let offSource = 0;
  for (const region of sample) {
    const peaks = await readJson("public", "data", "layers", "difference-peaks", `${region.regionId}.json`);
    assert.equal(peaks.regionId, region.regionId);
    assert.equal(peaks.peaks.length, region.peakCount);
    for (const peak of peaks.peaks) {
      // Fractional coordinates, or the marker lands outside the frame.
      assert.ok(peak.x >= 0 && peak.x <= 1, `x out of range: ${peak.x}`);
      assert.ok(peak.y >= 0 && peak.y <= 1, `y out of range: ${peak.y}`);
      assert.ok(Number.isFinite(peak.sigma));
      assert.equal(typeof peak.onSource, "boolean");
      assert.match(peak.direction, /^(rubin-brighter|reference-brighter)$/);
      assert.ok(Number.isFinite(peak.sky.raDeg) && Number.isFinite(peak.sky.decDeg));
      if (!peak.onSource) offSource += 1;
    }
    // Direction must agree with the sign, or red and blue mean nothing.
    for (const peak of peaks.peaks) {
      assert.equal(peak.direction === "rubin-brighter", peak.sigma > 0);
    }
  }
  // The on/off-source split is the filter that makes the map browsable; if every
  // peak were on-source the filter would be dead weight.
  assert.ok(index.counts.offSourcePeaks > 0);
  assert.ok(index.counts.offSourcePeaks < index.counts.totalPeaks);
});
