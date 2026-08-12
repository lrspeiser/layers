import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

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
  assert.match(html, /0\.237/);
  assert.doesNotMatch(html, /rubin-virgo\.jpg/i);
});

test("real-field prototype exposes discoverable overlap and honest QA gates", async () => {
  const response = await render("/prototype");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /FULL RUBIN FIELD/i);
  assert.match(html, /ALIGNED COMPARISON/i);
  assert.match(html, /Rubin \+ Legacy \+ SPARC/);
  assert.match(html, /0\.237/);
  assert.match(html, /This does/);
  assert.match(html, /brightness differences are scientifically comparable/);
  assert.match(html, /PSF \+ filter response/);
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
  assert.match(usableHtml, /No publishable cross-layer comparison yet/);

  const footprintOnly = await render("/target/ngc0100");
  assert.equal(footprintOnly.status, 200);
  const footprintHtml = await footprintOnly.text();
  assert.match(footprintHtml, /Footprint false positive/);
  assert.match(footprintHtml, /every intersecting Rubin pixel is masked NO_DATA/);
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
