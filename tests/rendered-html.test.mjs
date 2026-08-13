import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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
  assert.match(html, /AUTHENTIC LOCAL DP2 \+ DR10/);
  assert.match(html, /Coverage diff/);
  assert.match(html, /Signal candidates/);
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
  assert.equal(catalog.summary.panStarrsUsableLocal, 1);
  assert.equal(catalog.summary.localImageLayers, 7);
  assert.equal(catalog.summary.registrationAudits, 3);
  assert.equal(catalog.summary.pilotAudits, 4);
  assert.equal(catalog.summary.assumptionsWorthRechecking, 2);
  assert.equal(catalog.targets.find((target) => target.id === "ugc00191").comparisons[0].status, "qa");
  assert.equal(catalog.targets.find((target) => target.id === "ugc00891").comparisons[0].qa.astrometryPass, false);
  assert.equal(catalog.targets.find((target) => target.id === "ugc00891").layers.some((layer) => layer.id === "panstarrs-dr1-stack"), true);
  for (const target of catalog.targets) {
    assert.ok(target.layers.some((layer) => layer.id === "sparc-2016" && layer.kind === "profile"));
    assert.ok(target.layers.some((layer) => layer.id === "rubin-dp2-deep-coadd" && layer.kind === "image"));
  }
});

test("permanent target records expose honest pixel-level coverage states", async () => {
  const usable = await render("/target/ugc00191");
  assert.equal(usable.status, 200);
  const usableHtml = await usable.text();
  assert.match(usableHtml, /UGC00191/);
  assert.match(usableHtml, /Local Rubin pixels verified/);
  assert.match(usableHtml, /QA comparison record available; no scientific difference published/);

  const footprintOnly = await render("/target/ngc0100");
  assert.equal(footprintOnly.status, 200);
  const footprintHtml = await footprintOnly.text();
  assert.match(footprintHtml, /Footprint false positive/);
  assert.match(footprintHtml, /every intersecting Rubin pixel is masked NO_DATA/);
  assert.match(footprintHtml, /valid calibrated-pixel fraction/);
  assert.match(footprintHtml, /Download pilot audit \+ checksums/);

  const registrationBlocked = await render("/target/ugc00891");
  const registrationHtml = await registrationBlocked.text();
  assert.match(registrationHtml, /0\.403[\s\S]{0,40}arcsec/);
  assert.match(registrationHtml, /must be less than or equal to[\s\S]{0,40}0\.30/);
});

test("comparison architecture keeps evidence, measurements, inference, and audits separate", async () => {
  const [model, workspace, validator] = await Promise.all([
    readFile(new URL("../lib/layers.ts", import.meta.url), "utf8"),
    readFile(new URL("../components/AtlasExperience.tsx", import.meta.url), "utf8"),
    readFile(new URL("../pipeline/validate_layers_catalog.py", import.meta.url), "utf8"),
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
  }
  const pilotTargets = catalog.targets.filter((target) => target.pilotAudit);
  assert.deepEqual(pilotTargets.map((target) => target.id).sort(), ["ngc0100", "ugc00191", "ugc00634", "ugc00891"]);
  assert.equal(catalog.targets.find((target) => target.id === "ngc0100").pilotAudit.evidence[0].sha256, "7e6824743209cf467572c47e2c90624310ac681643629d99af81dd104123643c");
  const registrationPilot = catalog.targets.find((target) => target.id === "ugc00891").pilotAudit;
  assert.equal(registrationPilot.metric.value > registrationPilot.metric.passThreshold, true);
  const audits = catalog.targets.flatMap((target) => target.comparisons.flatMap((comparison) => comparison.assumptionAudits));
  assert.deepEqual(audits.sort((a, b) => a.rank - b.rank).map((audit) => audit.id), [
    "ugc00191-stellar-to-resolved-filter-transfer",
    "ugc00634-stellar-to-resolved-filter-transfer",
  ]);
  assert.ok(audits[0].evidenceMagnitude.thresholdMultiple > 5);
  assert.equal(audits[0].confidence, "candidate");
  assert.match(audits[0].caveat, /not evidence that either survey or the galaxy is wrong/);
});
