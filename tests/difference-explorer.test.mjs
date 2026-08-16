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

// The overlay is drawn on top of the sky image. If it were mostly opaque it
// would hide what it is annotating; if it were fully transparent it would show
// nothing. Neither failure raises an error anywhere, and neither is visible in
// the JSON, so the pixels are checked directly.
test("the difference overlay annotates the sky without hiding it", async () => {
  const sharp = (await import("sharp")).default;
  const index = await readJson("public", "data", "layers", "selected-regions", "difference-index.json");
  const previews = join(root, "public", index.previewRoot.slice(1));

  let maxOpaque = 0;
  let sawSignal = false;
  for (const region of index.regions.slice(0, 12)) {
    const overlay = join(previews, region.regionId, "difference-overlay.png");
    const { data, info } = await sharp(overlay)
      .ensureAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });
    const pixels = info.width * info.height;
    let opaque = 0;
    let clear = 0;
    for (let i = 3; i < data.length; i += info.channels) {
      if (data[i] > 127) opaque += 1;
      else if (data[i] === 0) clear += 1;
    }
    const opaqueFraction = opaque / pixels;
    maxOpaque = Math.max(maxOpaque, opaqueFraction);
    if (opaqueFraction > 0) sawSignal = true;
    // Mostly transparent, or the star image underneath is lost.
    assert.ok(
      clear / pixels > 0.5,
      `${region.regionId} overlay is only ${((clear / pixels) * 100).toFixed(1)}% clear`,
    );
  }
  assert.ok(maxOpaque < 0.35, `overlay covers up to ${(maxOpaque * 100).toFixed(1)}% of the frame`);
  assert.ok(sawSignal, "no overlay marked anything at all");
});

test("the difference map diverges in both directions", async () => {
  const sharp = (await import("sharp")).default;
  const index = await readJson("public", "data", "layers", "selected-regions", "difference-index.json");
  const previews = join(root, "public", index.previewRoot.slice(1));

  // Red means Rubin brighter and blue means the reference brighter. A map with
  // only one of them would mean the colour key is lying.
  for (const region of index.regions.slice(0, 8)) {
    const { data, info } = await sharp(join(previews, region.regionId, "difference.png"))
      .removeAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });
    let red = 0;
    let blue = 0;
    for (let i = 0; i < data.length; i += info.channels) {
      if (data[i] > data[i + 2] + 20) red += 1;
      else if (data[i + 2] > data[i] + 20) blue += 1;
    }
    assert.ok(red > 0, `${region.regionId} has no Rubin-brighter pixels`);
    assert.ok(blue > 0, `${region.regionId} has no reference-brighter pixels`);
  }
});

// The Gaussian PSF match never cancels a real core, so its difference maps are
// dominated by subtraction residuals. A fitted kernel is only worth keeping if
// it demonstrably reduces them, and the fit records that per region.
test("the fitted kernel is only kept where it measurably improves the subtraction", async () => {
  const fit = await readJson("public", "data", "layers", "selected-regions", "kernel-matching.json");
  assert.ok(fit.counts.regionsFitted > 100);
  assert.ok(fit.counts.improved > fit.counts.notImproved, "most regions should improve");
  // The residual is measured at star positions against the frame's own scatter,
  // before and after, so the claim is a measurement rather than an assertion.
  assert.ok(fit.counts.medianResidualAfterSigma < fit.counts.medianResidualBeforeSigma);
  assert.ok(fit.counts.medianImprovementFactor > 1.5);

  for (const region of fit.regions) {
    assert.ok(region.stars >= 8, `${region.regionId} fitted on only ${region.stars} stars`);
    assert.equal(typeof region.improved, "boolean");
    // A region marked improved must actually have a smaller residual, and one
    // that is not must carry no improvement factor.
    if (region.improved) {
      assert.ok(region.starResidualAfterSigma < region.starResidualBeforeSigma, region.regionId);
    } else {
      assert.equal(region.improvementFactor, null);
    }
    assert.match(region.direction, /^(rubin-convolved|reference-convolved)$/);
  }

  // Both convolution directions must occur: which frame is sharper varies, and
  // a fit that only ever went one way would mean the choice was not being made.
  const directions = new Set(fit.regions.map((r) => r.direction));
  assert.equal(directions.size, 2, "only one convolution direction was ever chosen");
});

test("difference maps say which plane they were drawn from", async () => {
  const index = await readJson("public", "data", "layers", "selected-regions", "difference-index.json");
  assert.ok(index.counts.kernelMatchedRegions > 0, "no region used the fitted-kernel plane");
  assert.ok(index.counts.kernelMatchedRegions <= index.counts.regionsRendered);
  for (const region of index.regions) {
    assert.equal(typeof region.kernelMatched, "boolean");
  }
});
