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

test("server-renders Layers as a survey-neutral science workspace", async () => {
  const response = await render();
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
  assert.equal(catalog.targets.length, 175);
  assert.equal(new Set(catalog.targets.map((target) => target.id)).size, 175);
  assert.equal(catalog.summary.rubinSiaMatches, 4);
  assert.equal(catalog.summary.rubinUsableLocal, 3);
  assert.equal(catalog.summary.rubinFootprintFalsePositives, 1);
  assert.equal(catalog.summary.legacySurveyUsableLocal, 3);
  assert.equal(catalog.summary.panStarrsUsableLocal, 3);
  assert.equal(catalog.summary.externalImageLayers, 4);
  assert.equal(catalog.summary.externalCatalogLayers, 111);
  assert.equal(catalog.summary.allWisePublished, 4);
  assert.equal(catalog.summary.localImageLayers, 13);
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
  for (const target of catalog.targets) {
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
  for (const target of catalog.targets) {
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
