import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

const root = new URL("..", import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1));

async function render(pathname = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${pathname}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request(`http://localhost${pathname}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the calibrated comparison workspace", async () => {
  const response = await render("/workspace");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Layers/);
  assert.match(html, /See the same sky through every credible layer/);
  assert.match(html, /175/);
  assert.match(html, /ASSUMPTIONS WORTH RECHECKING/);
  assert.match(html, /TRIAGE, NOT A VERDICT/);
  assert.match(html, /REGISTRATION QA/);
  assert.match(html, /0\.220/);
  assert.match(html, /AUTHENTIC MATCHED PIXELS/);
  assert.match(html, /Coverage diff/);
  assert.match(html, /Signal candidates/);
  assert.match(html, /Rubin data inventory/);
  assert.match(html, /925,460/);
  assert.match(html, /archive datasets/);
  assert.match(html, /5(?:<!-- -->)?\/(?:<!-- -->)?8/);
  assert.doesNotMatch(html, /rubin-virgo\.jpg/i);
});

test("the primary product indexes every live Rubin tract and distinguishes coverage from pixels", async () => {
  // This used to render "/" because the home page was a re-export of the
  // coverage viewer. The home page is now the narrative entry point, and the
  // tract index lives at its own route -- so the assertion follows the page
  // rather than the position.
  const response = await render("/coverage");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /TRACT-FIRST DATA INDEX/);
  assert.match(html, /2,191/);
  assert.match(html, /28/);
  assert.match(html, /50/);
  assert.match(html, /SELECTED ACQUISITION REGIONS/);
  assert.match(html, /coverage-ranked; pixels are tracked separately/);
  assert.match(html, /ACQUISITION PIPELINE STATUS/);
  assert.match(html, /1,464/);
  assert.match(html, /Comparison-ready/);
  assert.match(html, /CROSS-FAMILY DEMONSTRATION STATUS/);
  assert.match(html, /Real Rubin-versus-survey pixels by science family/);
  assert.match(html, /Coverage is not a science comparison/);
  assert.match(html, /Exact archive\/MOC overlap/);
  assert.match(html, /Pixels cached/);
  assert.match(html, /No Rubin overlap/);
  assert.match(html, /Rubin tract/);
  assert.match(html, /Dataset registry/);
  assert.match(html, /1,101/);
  assert.match(html, /ACT DR6/);
  assert.match(html, /448/);
  assert.match(html, /product subset/);
  assert.match(html, /\/tract\/11162/);
  assert.doesNotMatch(html, /preselected SPARC targets searched/);
});

test("coverage artifacts are complete, real-data derived, and internally consistent", async () => {
  const [footprint, overlaps, selected, registry] = await Promise.all([
    readFile(join(root, "public", "data", "coverage", "rubin-dp2-footprint.json"), "utf8").then(JSON.parse),
    readFile(join(root, "public", "data", "coverage", "external-overlaps.json"), "utf8").then(JSON.parse),
    readFile(join(root, "public", "data", "coverage", "selected-regions.json"), "utf8").then(JSON.parse),
    readFile(join(root, "public", "data", "survey-registry.json"), "utf8").then(JSON.parse),
  ]);
  assert.equal(footprint.validation.completeAgainstLiveTable, true);
  assert.equal(footprint.counts.tracts, 2191);
  assert.equal(footprint.counts.patches, 197105);
  assert.equal(footprint.tracts.length, footprint.counts.tracts);
  assert.equal(new Set(footprint.tracts.map((row) => row[0])).size, footprint.counts.tracts);
  assert.equal(overlaps.index.tractCount, footprint.counts.tracts);
  assert.equal(overlaps.tracts.length, footprint.counts.tracts);
  assert.equal(overlaps.counts.surveys, registry.surveys.length);
  assert.equal(registry.surveys.length, 28);
  assert.equal(selected.requestedCount, 50);
  assert.equal(selected.selectedCount, 50);
  assert.equal(selected.regions.length, 50);
  assert.equal(new Set(selected.regions.map((region) => region.tract)).size, 50);
  const confirmedByTract = new Map(overlaps.tracts.map((row) => [row[0], new Set(row[1])]));
  for (const region of selected.regions) {
    const confirmed = confirmedByTract.get(region.tract);
    assert.ok(confirmed);
    for (const surveyId of region.confirmedSurveyIds) assert.ok(confirmed.has(surveyId));
  }
});

test("the acquisition manifest keeps planned, cached, validated, and comparison-ready states separate", async () => {
  const manifest = JSON.parse(await readFile(join(root, "public", "data", "coverage", "cache-manifest.json"), "utf8"));
  assert.equal(manifest.summary.plannedRegionCount, 50);
  assert.equal(manifest.summary.plannedJobCount, 673);
  assert.equal(manifest.summary.metadataResponseCount, 3);
  assert.equal(manifest.summary.cachedScienceInputCandidateCount, 1);
  assert.equal(manifest.summary.validatedScienceInputCount, 0);
  assert.equal(manifest.summary.comparisonReadyCount, 0);
  assert.equal(manifest.cachedScienceInputCandidates[0].scienceReady, false);
  assert.equal(manifest.cachedScienceInputCandidates[0].comparisonReady, false);
  assert.equal(manifest.cachedScienceInputCandidates[0].supportPlaneChecks.wcsPresent, true);
  assert.equal(manifest.cachedScienceInputCandidates[0].supportPlaneChecks.unitsVerified, false);
  assert.doesNotMatch(JSON.stringify(manifest), /RUBIN_RSP_TOKEN|Authorization|X-Amz-|https?:\/\/.*cutout/i);
});

test("live multi-survey pilots expose real field-specific evidence and display honest layer boundaries", async () => {
  const response = await render("/pilots");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /LIVE MULTI-SURVEY PILOTS/);
  assert.match(html, /UGC00191/);
  assert.match(html, /UGC00634/);
  assert.match(html, /UGC00891/);
  assert.match(html, /Gaia DR3/);
  assert.match(html, /ZTF time series/);
  assert.match(html, /LoTSS DR3 radio/);
  assert.match(html, /Astrometrically aligned, not photometrically comparable/);
  assert.match(html, /Reveal UGC00191 LoTSS radio over Rubin optical/);

  const summary = JSON.parse(await readFile(join(root, "public", "data", "layers", "multisurvey-pilots", "summary.json"), "utf8"));
  const validation = JSON.parse(await readFile(join(root, "public", "data", "layers", "multisurvey-pilots", "validation.json"), "utf8"));
  assert.equal(summary.fieldCount, 3);
  assert.equal(summary.datasetCount, 15);
  assert.deepEqual(summary.statusCounts, { available: 12, none: 3 });
  assert.equal(validation.ok, true);
  assert.equal(validation.artifactsChecksumChecked, 24);
  assert.equal(validation.fitsWcsChecked, 3);
  assert.equal(validation.commonGridFieldsChecked, 3);
  assert.equal(validation.commonGridArtifactsChecked, 6);
  assert.equal(validation.commonGridWcsMatchesChecked, 3);
  for (const slug of ["ugc00191", "ugc00634", "ugc00891"]) {
    const field = JSON.parse(await readFile(join(root, "public", "data", "layers", "multisurvey-pilots", `${slug}.json`), "utf8"));
    assert.equal(field.datasets.length, 5);
    assert.equal(field.datasets.find((item) => item.dataset === "erosita-erass1").status, "none");
    assert.equal(field.datasets.find((item) => item.dataset === "lotss").wcs.present, true);
    await access(join(root, "public", "layer-previews", "multisurvey-pilots", `${slug}-lotss-dr3-common-grid.png`));
    await access(join(root, "public", "layer-previews", "multisurvey-pilots", `${slug}-rubin-i-on-lotss-dr3.png`));
  }
  const commonGrid = JSON.parse(await readFile(join(root, "public", "data", "layers", "multisurvey-pilots", "rubin-lotss-common-grid-summary.json"), "utf8"));
  assert.equal(commonGrid.availableCount, 3);
  assert.equal(commonGrid.scienceClaimAllowed, false);
});

test("an image-ready tract opens its matching real pilot field", async () => {
  const response = await render("/pilots?field=ugc00634&tract=10689");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /aria-pressed="true">UGC00634/);
  assert.match(html, /Reveal UGC00634 LoTSS radio over Rubin optical/);
});

test("tract workspaces distinguish aligned viewers, validated single images, and coverage only", async () => {
  const aligned = await render("/tract/11162");
  assert.equal(aligned.status, 200);
  const alignedHtml = await aligned.text();
  assert.match(alignedHtml, /Rubin tract 11162/);
  assert.match(alignedHtml, /external layers live/);
  assert.match(alignedHtml, /Legacy Survey/);
  assert.match(alignedHtml, /LoTSS/);
  assert.match(alignedHtml, /REAL COMMON-GRID PIXELS/);
  assert.match(alignedHtml, /Available aligned layers for Rubin tract 11162/);
  assert.match(alignedHtml, /Position overlay/);
  assert.match(alignedHtml, /ACT DR6/);
  assert.match(alignedHtml, /SDSS DR19/);
  assert.match(alignedHtml, /EXACT RELEASE \/ PRODUCT INTERSECTIONS/);

  const optical = await render("/tract/5063");
  assert.equal(optical.status, 200);
  const opticalHtml = await optical.text();
  assert.match(opticalHtml, /external layers live/);
  assert.match(opticalHtml, /Legacy Survey/);
  assert.match(opticalHtml, /REAL COMMON-GRID PIXELS/);

  const family = await render("/tract/9813");
  assert.equal(family.status, 200);
  const familyHtml = await family.text();
  assert.match(familyHtml, /REAL NON-IMAGE EVIDENCE/);
  assert.match(familyHtml, /\/tract\/9813\/evidence/);

  const evidenceOnly = await render("/tract/2054");
  assert.equal(evidenceOnly.status, 200);
  const evidenceOnlyHtml = await evidenceOnly.text();
  assert.match(evidenceOnlyHtml, /NO LOCAL IMAGE YET/);
  assert.match(evidenceOnlyHtml, /Fetch real Rubin pixels/);
  assert.match(evidenceOnlyHtml, /authenticated Rubin DP2 gri HiPS tile/i);
  assert.doesNotMatch(evidenceOnlyHtml, /science-ready .* mosaic/i);
});

test("KiDS exact catalogue support is visible but never promoted to a full footprint", async () => {
  const audit = JSON.parse(await readFile(join(root, "public", "data", "coverage", "hsc-kids-gap-audit.json"), "utf8"));
  const kids = audit.products.find((product) => product.surveyId === "kids-1000-lensing");
  assert.equal(kids.status, "resolved-exact-catalogue-positional-support");
  assert.equal(kids.releasedGoldSupport.rubinOverlapTractCount, 13);
  assert.match(kids.coverageSemantics, /not a continuous observing footprint/i);
  const tract = await render("/tract/5061");
  const html = await tract.text();
  assert.match(html, /KiDS-1000 released lensing-source support/);
  assert.match(html, /CONSERVATIVE DETECTION \/ PROGRAM SUBSETS/);
});

test("on-demand Rubin endpoint rejects IDs outside the exact DP2 tract index", async () => {
  const response = await render("/api/tracts/999999/cutout");
  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), { error: "Unknown Rubin tract" });
});

test("cutout worker health endpoint fails closed when no server cache credential is present", async () => {
  const response = await render("/api/cutout-worker");
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { status: "unavailable" });
});

test("normalized Legacy cutouts expose 48 validated inputs without leaking cache paths", async () => {
  const manifestPath = join(root, "public", "data", "layers", "selected-regions", "legacy-dr10.json");
  const manifestText = await readFile(manifestPath, "utf8");
  const manifest = JSON.parse(manifestText);
  assert.equal(manifest.counts.selectedRegions, 50);
  assert.equal(manifest.counts.archiveInputs, 48);
  assert.equal(manifest.counts.validatedScienceInputs, 48);
  assert.equal(manifest.counts.comparisonReady, 0);
  assert.doesNotMatch(manifestText, /pipeline\/results|credential|Authorization|X-Amz-/i);
  const ready = manifest.regions.filter((region) => region.scienceReady);
  assert.equal(new Set(ready.map((region) => region.tract)).size, 48);
  for (const region of ready) {
    assert.equal(region.comparisonReady, false);
    assert.deepEqual(region.supportPlanes, { image: true, inverseVariance: true, validMask: true, coverage: true, celestialWcs: true });
    assert.match(region.normalizedFits.sha256, /^[a-f0-9]{64}$/);
    await access(join(root, "public", region.preview));
  }
});

test("selected-region layers are switchable without a quantitative claim", async () => {
  const response = await render("/tract/10079");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /external layers live/);
  assert.match(html, /Legacy Survey/);
  assert.match(html, /REAL COMMON-GRID PIXELS/);
  assert.match(html, /Reveal Rubin DP2 · r over Legacy Survey/);
  assert.match(html, /Before a quantitative difference/);

  const manifestPath = join(root, "public", "data", "layers", "selected-regions", "rubin-reference-comparisons.json");
  const manifestText = await readFile(manifestPath, "utf8");
  const manifest = JSON.parse(manifestText);
  assert.equal(manifest.counts.selectedRegions, 50);
  assert.equal(manifest.counts.rubinScienceInputs, 50);
  assert.equal(manifest.counts.referenceScienceInputs, 50);
  assert.equal(manifest.counts.displayAligned, 50);
  assert.equal(manifest.counts.displayAligned, manifest.regions.length);
  assert.equal(manifest.counts.comparisonReady, 0);
  assert.equal(manifest.policy.scienceClaimAllowed, false);
  assert.doesNotMatch(manifestText, /pipeline\/results|Authorization|X-Amz-/i);
  for (const region of manifest.regions) {
    assert.equal(region.displayAlignmentAllowed, true);
    assert.equal(region.scienceClaimAllowed, false);
    assert.equal(region.comparisonReady, false);
    assert.match(region.localFits.sha256, /^[a-f0-9]{64}$/);
    for (const preview of Object.values(region.previews)) await access(join(root, "public", preview.path));
  }

  const panStarrsResponse = await render("/tract/5192");
  assert.equal(panStarrsResponse.status, 200);
  const panStarrsHtml = await panStarrsResponse.text();
  assert.match(panStarrsHtml, /external layers live/);
  assert.match(panStarrsHtml, /Pan-STARRS1/);
  assert.match(panStarrsHtml, /Pan-STARRS1 i-band ready/);
  assert.match(panStarrsHtml, /Pixels outside the common valid footprint are black in both views/);
});

test("real-field prototype exposes discoverable overlap and honest QA gates", async () => {
  const response = await render("/prototype");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /FULL RUBIN FIELD/i);
  assert.match(html, /ALIGNED COMPARISON/i);
  assert.match(html, /Rubin \+ Legacy \+ SPARC/);
  assert.match(html, /0\.220/);
  assert.match(html, /This does/);
  assert.match(html, /brightness differences are scientifically comparable/);
  assert.match(html, /PSF \+ sky intermediate/);
  assert.match(html, /Legacy really does fill more of this field/);
  assert.match(html, /Open an official source FITS/);
  assert.match(html, /WHAT IS BEHIND EACH PIXEL/);
  assert.match(html, /No single winner/);
  assert.match(html, /Median formal pixel noise/);
  assert.match(html, /RUBIN DP2 · PUBLIC REAL DATA/);
  assert.match(html, /56/);
  assert.match(html, /VISIBLE NOW/);
  assert.match(html, /unique field-band mosaics in public viewers/);
  assert.doesNotMatch(html, /PRIVATE DATA PREVIEW|protected DP2 cutout|redistribution policy/i);
});

test("catalog contains the complete SPARC sample and generic layer records", async () => {
  const catalog = JSON.parse(await readFile(new URL("../public/data/layers-catalog.json", import.meta.url), "utf8"));
  assert.equal(catalog.product, "Layers");
  assert.equal(catalog.targetSelection.complete, true);
  assert.equal(catalog.targets.length, 176);
  assert.equal(new Set(catalog.targets.map((target) => target.id)).size, 176);
  assert.equal(catalog.summary.sparcTargets, 175);
  assert.equal(catalog.summary.comparisonFields, 1);
  assert.equal(catalog.summary.rubinSiaMatches, 4);
  assert.equal(catalog.summary.rubinUsableLocal, 3);
  assert.equal(catalog.summary.rubinFootprintFalsePositives, 1);
  assert.equal(catalog.summary.legacySurveyUsableLocal, 3);
  assert.equal(catalog.summary.panStarrsUsableLocal, 3);
  assert.equal(catalog.summary.externalImageLayers, 7);
  assert.equal(catalog.summary.externalCatalogLayers, 111);
  assert.equal(catalog.summary.externalLinkedLayers, 15);
  assert.equal(catalog.summary.allWisePublished, 4);
  assert.equal(catalog.summary.localImageLayers, 16);
  assert.equal(catalog.summary.registrationAudits, 121);
  assert.equal(catalog.summary.pilotAudits, 4);
  assert.equal(catalog.summary.assumptionsWorthRechecking, 4);
  assert.equal(catalog.summary.publishedComparisons, 113);
  assert.equal(catalog.targets.find((target) => target.id === "ugc00191").comparisons[0].status, "qa");
  assert.equal(catalog.targets.find((target) => target.id === "ugc00891").comparisons[0].qa.astrometryPass, true);
  assert.equal(catalog.targets.find((target) => target.id === "ugc00891").layers.some((layer) => layer.id === "panstarrs-dr1-stack"), true);
  for (const targetId of ["ngc0100", "ugc00191", "ugc00634", "ugc00891"]) {
    const wise = catalog.targets.find((target) => target.id === targetId).layers.find((layer) => layer.id === "wise-allwise-atlas");
    assert.equal(wise.kind, "image");
    assert.equal(wise.availability, "published");
    assert.equal(wise.hasVariance, true);
    assert.equal(wise.hasMask, true);
    assert.equal(wise.hasWcs, true);
    assert.equal(wise.bandCoverage.W1, 1);
    assert.equal(wise.assets.preview, `/layer-previews/wise-allwise/${targetId}-w1.jpg`);
    const provenance = JSON.parse(await readFile(join(root, "public", wise.assets.data), "utf8"));
    const expectedTransferStatus = ["ugc00191", "ugc00634"].includes(targetId) ? "pass" : "blocked";
    assert.equal(provenance.scienceGate.status, expectedTransferStatus);
    assert.ok(provenance.scienceGate.unsupportedClaims.includes("stellar-mass change"));
    assert.match(provenance.standardProduct.sha256, /^[a-f0-9]{64}$/);
    for (const source of Object.values(provenance.sources)) assert.match(source.sha256, /^[a-f0-9]{64}$/);
  }
  for (const target of catalog.targets.filter((item) => item.selection.sample === "SPARC 2016 master sample")) {
    assert.ok(target.layers.some((layer) => layer.id === "sparc-2016" && layer.kind === "profile"));
    assert.ok(target.layers.some((layer) => layer.id === "rubin-dp2-deep-coadd" && layer.kind === "image"));
  }
  const massComparisons = catalog.targets.flatMap((target) => target.comparisons.filter((comparison) => comparison.layerIds.includes("wise-w1-stellar-mass-2025")));
  assert.equal(massComparisons.length, 111);
  assert.equal(massComparisons.filter((comparison) => comparison.measurements[0].classification === "expected").length, 109);
  assert.equal(massComparisons.filter((comparison) => comparison.measurements[0].classification === "noteworthy").length, 2);
  assert.equal(massComparisons.filter((comparison) => comparison.measurements[0].classification === "large").length, 0);
  for (const targetId of ["ngc0100", "ugc00191", "ugc00634", "ugc00891"]) {
    assert.equal(catalog.targets.find((target) => target.id === targetId).layers.some((layer) => layer.id === "wise-w1-stellar-mass-2025"), false);
  }
  const transfers = catalog.targets.flatMap((target) => target.comparisons.filter((comparison) => comparison.id.endsWith("--wise-sparc-transfer")));
  assert.equal(transfers.length, 4);
  assert.deepEqual(transfers.filter((comparison) => comparison.status === "published").map((comparison) => comparison.id), [
    "ugc00191--wise-sparc-transfer",
    "ugc00634--wise-sparc-transfer",
  ]);
  for (const comparison of transfers.filter((item) => item.status === "published")) {
    assert.equal(comparison.measurements.length, 2);
    assert.ok(comparison.radialSeries.length >= 6);
    assert.equal(comparison.transferSummary.massInferenceStatus, "blocked");
    assert.match(comparison.transferSummary.massInferenceReason, /validated optical-color W1 mass-to-light ratio/);
  }
});

test("permanent target records expose honest pixel-level coverage states", async () => {
  const usable = await render("/target/ugc00191");
  assert.equal(usable.status, 200);
  const usableHtml = await usable.text();
  assert.match(usableHtml, /UGC00191/);
  assert.match(usableHtml, /Local Rubin pixels verified/);
  assert.match(usableHtml, /1 scientific comparison is published/);
  assert.match(usableHtml, /Rubin image comparisons remain QA-only; no Rubin optical scientific difference is published/);
  assert.match(usableHtml, /WISE W1 − SPARC 3.6 µm aperture light/);
  assert.match(usableHtml, /FUNCTIONAL MATCHED-PIXEL EXAMPLE/);
  assert.match(usableHtml, /Rubin DP2[\s\S]*Legacy Survey DR10/);
  assert.match(usableHtml, /Rubin DP2[\s\S]*Pan-STARRS1/);
  assert.match(usableHtml, /WISE[\s\S]*AllWISE Atlas/);
  assert.match(usableHtml, /Pan-STARRS1 evidence for this sky field/);
  assert.match(usableHtml, /ALFALFA detects/);
  assert.match(usableHtml, /DESI target spectrum linked/);
  assert.match(usableHtml, /type="range"/);
  assert.match(usableHtml, /SCIENCE CLAIM[\s\S]*NOT PUBLISHED/);

  const secondLegacy = await render("/target/ugc00634");
  const secondLegacyHtml = await secondLegacy.text();
  assert.match(secondLegacyHtml, /FUNCTIONAL MATCHED-PIXEL EXAMPLE/);
  assert.match(secondLegacyHtml, /Rubin DP2[\s\S]*Legacy Survey DR10/);
  assert.match(secondLegacyHtml, /Rubin DP2[\s\S]*Pan-STARRS1/);

  const footprintOnly = await render("/target/ngc0100");
  assert.equal(footprintOnly.status, 200);
  const footprintHtml = await footprintOnly.text();
  assert.match(footprintHtml, /Footprint false positive/);
  assert.match(footprintHtml, /every intersecting Rubin pixel is masked NO_DATA/);
  assert.match(footprintHtml, /valid calibrated-pixel fraction/);
  assert.match(footprintHtml, /Download pilot audit \+ checksums/);

  const calibrationBlocked = await render("/target/ugc00891");
  const calibrationHtml = await calibrationBlocked.text();
  assert.match(calibrationHtml, /FUNCTIONAL MATCHED-PIXEL EXAMPLE/);
  assert.match(calibrationHtml, /Rubin DP2[\s\S]*Pan-STARRS/);
  assert.match(calibrationHtml, /Rubin DP2[\s\S]*DESI Legacy Imaging Surveys/);
  assert.match(calibrationHtml, /qualified resolved galaxy cells/);
  assert.match(calibrationHtml, /Only 7\/20 required cells survive the common mask/);
  assert.match(calibrationHtml, /must be greater than or equal to[\s\S]{0,40}20/);

  const ultraviolet = await render("/target/ugc00634");
  const ultravioletHtml = await ultraviolet.text();
  assert.match(ultravioletHtml, /GALEX evidence for this sky field/);
  assert.match(ultravioletHtml, /FUV · NUV/);

  const lensing = await render("/target/abell2744");
  assert.equal(lensing.status, 200);
  const lensingHtml = await lensing.text();
  assert.match(lensingHtml, /Abell 2744/);
  assert.match(lensingHtml, /Hubble Frontier Fields evidence for this sky field/);
  assert.match(lensingHtml, /Projected mass from strong \+ weak lensing/);
  assert.match(lensingHtml, /abell2744-kappa\.jpg/);
});

test("new public layers preserve coverage, provenance, and scientific role", async () => {
  const catalog = JSON.parse(await readFile(join(root, "public", "data", "layers-catalog.json"), "utf8"));
  const rubinFields = ["ngc0100", "ugc00191", "ugc00634", "ugc00891"].map((id) => catalog.targets.find((target) => target.id === id));

  for (const target of rubinFields) {
    const hi = target.layers.find((layer) => layer.id === "hi-survey-crossmatch");
    assert.equal(hi.linkedEvidence.status, "detection");
    assert.match(hi.linkedEvidence.headline, /ALFALFA detects/);
    assert.ok(hi.linkedEvidence.facts.some((fact) => fact.label === "H I MASS"));
    await access(join(root, "public", ...hi.assets.data.slice(1).split("/")));
    assert.equal(target.layers.filter((layer) => layer.id.startsWith("desi-") || layer.id.startsWith("sdss-")).length, 2);
  }

  const panStarrsFields = rubinFields.filter((target) => target.id !== "ngc0100");
  for (const target of panStarrsFields) {
    const panstarrs = target.layers.find((layer) => layer.id === "panstarrs-dr1-stack");
    assert.equal(panstarrs.renderMode, "image");
    assert.match(panstarrs.assets.preview, new RegExp(`^/layer-previews/panstarrs/${target.id}-`));
    await access(join(root, "public", ...panstarrs.assets.preview.slice(1).split("/")));
  }

  const galexLayers = rubinFields.flatMap((target) => target.layers.filter((layer) => layer.id === "galex-gr6-7"));
  assert.equal(galexLayers.length, 3);
  for (const galex of galexLayers) {
    assert.deepEqual(galex.bands, ["FUV", "NUV"]);
    assert.equal(galex.hasWcs, true);
    assert.equal(galex.hasMask, true);
    await access(join(root, "public", ...galex.assets.preview.slice(1).split("/")));
  }

  const lensing = catalog.targets.find((target) => target.id === "abell2744");
  assert.equal(lensing.selection.sample, "Lensing demonstration fields");
  const kappa = lensing.layers.find((layer) => layer.id === "hff-merten-v1-kappa");
  assert.equal(kappa.kind, "map");
  assert.equal(kappa.linkedEvidence.status, "model-map");
  assert.match(kappa.datasetIds[0], /merten_v1_kappa\.fits$/);
});

test("every authentic matched pilot has a deterministic preview manifest", async () => {
  const previews = JSON.parse(await readFile(join(root, "public", "data", "comparison-previews.json"), "utf8"));
  assert.deepEqual(previews.comparisons.map((item) => item.comparisonKey), [
    "ugc00191",
    "ugc00191--panstarrs-dr1-stack",
    "ugc00634",
    "ugc00634--panstarrs-dr1-stack",
    "ugc00891",
    "ugc00891--legacy-survey-dr10",
  ]);
  for (const preview of previews.comparisons) {
    assert.match(preview.analysisProductSha256, /^[a-f0-9]{64}$/);
    assert.ok(preview.commonValidPixelFraction > 0);
    assert.match(preview.assets.rubin.path, new RegExp(`^/rubin-data/${preview.comparisonKey}/`));
    assert.match(preview.assets.reference.path, new RegExp(`^/rubin-data/${preview.comparisonKey}/`));
    for (const asset of Object.values(preview.assets)) {
      await access(join(root, "public", ...asset.path.slice(1).split("/")));
    }
    assert.match(preview.notice, /Display stretch only/);
    assert.match(preview.notice, /invalid Rubin pixels and blue hatching marks invalid comparison-survey pixels/);
    assert.match(preview.notice, /Rubin-only \(red\), reference-only \(blue\), and neither usable \(amber\)/);
    const partition = preview.commonValidPixelFraction
      + preview.coverageFractions.rubinOnly
      + preview.coverageFractions.referenceOnly
      + preview.coverageFractions.neither;
    assert.ok(Math.abs(partition - 1) < 1e-9);
  }
});

test("comparison architecture keeps evidence, measurements, inference, and audits separate", async () => {
  const [model, workspace, validator, externalBuilder] = await Promise.all([
    readFile(new URL("../lib/layers.ts", import.meta.url), "utf8"),
    readFile(new URL("../components/AtlasExperience.tsx", import.meta.url), "utf8"),
    readFile(new URL("../pipeline/validate_layers_catalog.py", import.meta.url), "utf8"),
    readFile(new URL("../pipeline/build_layers_catalog.py", import.meta.url), "utf8"),
  ]);
  assert.match(model, /type LayerTarget/);
  assert.match(model, /type Registration/);
  assert.match(model, /type DifferenceMeasurement/);
  assert.match(model, /type Inference/);
  assert.match(model, /type AssumptionAudit/);
  assert.match(model, /filterMatched/);
  assert.match(workspace, /comparisonIsSwipeable/);
  assert.match(workspace, /MIXED DATA TYPES/);
  assert.match(validator, /non-image layer forced into image view/);
  assert.match(validator, /classification disagrees with sigma/);
  assert.match(externalBuilder, /layers-image-layer-v1/);
  assert.match(externalBuilder, /layers-catalog-layer-v1/);
  assert.match(externalBuilder, /layers-comparison-audit-v1/);
  assert.match(workspace, /wise-allwise-atlas/);
  assert.match(workspace, /Mass inference blocked/);
});

test("diffuse recovery limits are exposed as caveated QA measurements", async () => {
  const catalog = JSON.parse(await readFile(join(root, "public", "data", "layers-catalog.json"), "utf8"));
  const comparison = catalog.targets.find((target) => target.id === "ugc00191").comparisons[0];
  assert.equal(comparison.qa.injectionRecoveryStatus, "pass");
  assert.equal(comparison.qa.injectionNullTestPass, true);
  assert.equal(comparison.measurements.length, 8);
  for (const measurement of comparison.measurements) {
    assert.equal(measurement.classification, "expected");
    assert.equal(measurement.significanceSigma, 0);
    assert.ok(measurement.provenance.length >= 2);
    assert.ok(measurement.caveats.some((item) => item.includes("sensitivity limit")));
  }
});

test("all 175 SPARC layers expose real profile records rather than fake images", async () => {
  const [catalog, profileIndex] = await Promise.all([
    readFile(join(root, "public", "data", "layers-catalog.json"), "utf8").then(JSON.parse),
    readFile(join(root, "public", "data", "sparc-profiles.json"), "utf8").then(JSON.parse),
  ]);
  assert.equal(profileIndex.targetCount, 175);
  assert.equal(Object.keys(profileIndex.targets).length, 175);
  for (const target of catalog.targets.filter((item) => item.selection.sample === "SPARC 2016 master sample")) {
    const layer = target.layers.find((item) => item.id === "sparc-2016");
    assert.equal(layer.kind, "profile");
    assert.equal(layer.renderMode, "plot");
    assert.equal(layer.assets.data, `/data/sparc-profiles/${target.id}.json`);
    const record = JSON.parse(await readFile(join(root, "public", layer.assets.data), "utf8"));
    assert.equal(record.target.targetId, target.id);
    assert.ok(record.target.surfaceBrightness.length > 0);
    assert.ok(record.target.rotationCurve.length > 0);
    assert.match(record.target.provenance.surfaceBrightnessMemberSha256, /^[a-f0-9]{64}$/);
    assert.match(record.target.provenance.rotationMemberSha256, /^[a-f0-9]{64}$/);
  }
});

test("resolved galaxy filter-transfer failures remain hard science gates", async () => {
  const catalog = JSON.parse(await readFile(join(root, "public", "data", "layers-catalog.json"), "utf8"));
  for (const targetId of ["ugc00191", "ugc00634"]) {
    const comparison = catalog.targets.find((target) => target.id === targetId).comparisons[0];
    assert.equal(comparison.qa.pointSourceCalibrationPass, true);
    assert.equal(comparison.qa.extendedSourceTransferPass, false);
    assert.equal(comparison.qa.extendedSourceTransferStatus, "qa-failed");
    assert.ok(comparison.qa.extendedSourceResolvedCells >= 20);
    assert.ok(comparison.qa.extendedSourceMedianAbsoluteResidualMag > 0.08);
    assert.equal(comparison.registration.filterMatched, false);
    assert.equal(comparison.status, "qa");
    assert.equal(comparison.assumptionAudits[0].independentCheck.qualifiedForArbitration, false);
    assert.equal(comparison.assumptionAudits[0].independentCheck.registrationPass, true);
    assert.ok(comparison.assumptionAudits[0].independentCheck.registrationP95Arcsec <= 0.3);
    assert.match(comparison.assumptionAudits[0].independentCheck.provenance[0], /^[a-f0-9]{64}$/);
  }
  const pilotTargets = catalog.targets.filter((target) => target.pilotAudit);
  assert.deepEqual(pilotTargets.map((target) => target.id).sort(), ["ngc0100", "ugc00191", "ugc00634", "ugc00891"]);
  assert.equal(catalog.targets.find((target) => target.id === "ngc0100").pilotAudit.evidence[0].sha256, "7e6824743209cf467572c47e2c90624310ac681643629d99af81dd104123643c");
  const registrationPilot = catalog.targets.find((target) => target.id === "ugc00891").pilotAudit;
  assert.equal(registrationPilot.outcome, "filter-transfer-blocked");
  assert.equal(registrationPilot.metric.value < registrationPilot.metric.passThreshold, true);
  const audits = catalog.targets.flatMap((target) => target.comparisons.flatMap((comparison) => comparison.assumptionAudits));
  assert.deepEqual(audits.sort((a, b) => a.rank - b.rank).map((audit) => audit.id), [
    "ugc00191-stellar-to-resolved-filter-transfer",
    "ugc00634-stellar-to-resolved-filter-transfer",
    "ugc02885-stellar-mass-baseline-audit",
    "ugc06917-stellar-mass-baseline-audit",
  ]);
  assert.ok(audits[0].evidenceMagnitude.thresholdMultiple > 5);
  assert.equal(audits[0].confidence, "candidate");
  assert.match(audits[0].caveat, /not evidence that either survey or the galaxy is wrong/);
});

test("published WISE catalog masses retain uncertainties, expected range, and model boundaries", async () => {
  const catalog = JSON.parse(await readFile(join(root, "public", "data", "layers-catalog.json"), "utf8"));
  const target = catalog.targets.find((item) => item.id === "ugc02885");
  const layer = target.layers.find((item) => item.id === "wise-w1-stellar-mass-2025");
  const comparison = target.comparisons.find((item) => item.comparisonMode === "catalog-profile");
  assert.equal(layer.kind, "catalog");
  assert.equal(layer.renderMode, "table");
  assert.equal(comparison.status, "published");
  assert.equal(comparison.registration, undefined);
  for (const gate of ["targetIdentityMatched", "quantityMatched", "unitsMatched", "distanceScaleShared", "modelDeclared"]) assert.equal(comparison.compatibility[gate], true);
  const measurement = comparison.measurements[0];
  assert.equal(measurement.systematicUncertainty, 0.18);
  assert.equal(measurement.expectedCenter, 0.1);
  assert.equal(measurement.classification, "noteworthy");
  assert.ok(measurement.significanceSigma >= 2 && measurement.significanceSigma < 3);
  assert.equal(measurement.expectedRange.length, 2);
  assert.ok(measurement.caveats.some((item) => item.includes("fixed M/L")));
  assert.ok(measurement.caveats.some((item) => item.includes("triage")));
  assert.match(comparison.inferences[0].modelDependentInterpretation, /does not by itself revise the radial baryonic acceleration/);
  const record = JSON.parse(await readFile(join(root, "public", layer.assets.data), "utf8"));
  assert.equal(record.targetId, target.id);
  assert.deepEqual(record.comparison, comparison);
});
