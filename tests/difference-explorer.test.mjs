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

// The explorer can switch reference survey, which means it fetches two more
// indexes and their peak files at runtime. Those paths are checked here for the
// same reason as the legacy set: a missing file is a blank frame, silently.
test("every reference pairing the explorer can switch to resolves", async () => {
  for (const file of ["difference-index-des.json", "difference-index-ps1.json"]) {
    const index = await readJson("public", "data", "layers", "selected-regions", file);
    assert.ok(index.regions.length > 100, `${file} looks empty`);
    assert.ok(index.pairing && index.pairing !== "legacy");
    for (const region of index.regions.slice(0, 30)) {
      for (const name of [
        `rubin-${region.rubinBand}`,
        `reference-${region.referenceBand}`,
        "difference",
        "difference-overlay",
      ]) {
        await access(join(root, "public", index.previewRoot.slice(1), region.regionId, `${name}.png`));
      }
      await access(join(root, "public", index.peakRoot.slice(1), `${region.regionId}.json`));
    }
  }
});

test("a confirmed difference names the references that saw it", async () => {
  const agreement = await readJson(
    "public", "data", "layers", "selected-regions", "difference-agreement-slim.json",
  );
  assert.ok(agreement.counts.distinctOffSourcePositions > 0);
  // Confirmation must be rarer than the raw population, or the filter is not
  // filtering anything.
  assert.ok(agreement.counts.confirmedByTwoOrMore < agreement.counts.distinctOffSourcePositions);
  assert.match(agreement.caveat, /bandpass|colour/i);

  for (const item of agreement.confirmed) {
    const names = Object.keys(item.seenIn);
    // Two references, or the word "confirmed" is doing work it has not earned.
    assert.ok(names.length >= 2, `${item.regionId} claims confirmation from ${names.length}`);
    assert.equal(item.referenceCount, names.length);
    const signs = new Set(Object.values(item.seenIn).map((v) => Math.sign(v)));
    assert.equal(item.directionsAgree, signs.size === 1);
    // One reference brighter and another fainter at the same spot confirms nothing.
    assert.equal(item.directionsAgree, true);
  }
});

// Candidate markers are placed by WCS onto the same frames the previews came
// from. A marker outside the frame implies coverage that does not exist, and a
// marker on a region with no image cannot be drawn at all.
test("multi-wavelength candidates land inside frames that exist", async () => {
  const placements = await readJson(
    "public", "data", "layers", "selected-regions", "register-placements.json",
  );
  const index = await readJson("public", "data", "layers", "selected-regions", "difference-index.json");
  const rendered = new Map(index.regions.map((r) => [r.regionId, r]));

  let drawn = 0;
  for (const [regionId, items] of Object.entries(placements.byRegion)) {
    const region = rendered.get(regionId);
    assert.ok(region, `${regionId} has candidates but no rendered frame`);
    await access(join(root, "public", index.previewRoot.slice(1), regionId, `rubin-${region.rubinBand}.png`));
    for (const item of items) {
      assert.ok(item.x >= 0 && item.x <= 1, `${regionId} candidate x out of frame: ${item.x}`);
      assert.ok(item.y >= 0 && item.y <= 1, `${regionId} candidate y out of frame: ${item.y}`);
      assert.ok(Number.isFinite(item.sky.raDeg) && Number.isFinite(item.sky.decDeg));
      assert.ok(item.operator && item.operator.length > 0);
      drawn += 1;
    }
  }
  assert.equal(drawn, placements.counts.placed);

  // Candidates that could not be placed must be recorded with a reason, never
  // dropped: a shrinking marker count with no explanation reads as "fewer
  // anomalies" rather than "less coverage".
  assert.equal(placements.counts.unplaced, placements.unplaced.length);
  for (const item of placements.unplaced) {
    assert.ok(item.reason && item.reason.length > 0);
  }
  assert.equal(
    placements.counts.placed + placements.counts.unplaced,
    placements.counts.candidates,
  );
  assert.match(placements.meaning, /not that anything is there/i);
});
