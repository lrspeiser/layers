import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");

test("HSC PDR2 imaging uses a public release-matched MOC", async () => {
  const registry = await readJson("public", "data", "survey-registry.json");
  const hsc = registry.surveys.find((survey) => survey.id === "hsc-ssp-pdr2");
  assert.ok(hsc);
  assert.equal(hsc.release, "PDR2");
  assert.equal(hsc.accessStatus, "public");
  assert.equal(hsc.coverage.machineReadableStatus, "exact");
  assert.equal(hsc.coverage.mocId, "CDS/P/HSC/DR2/*/*");
  assert.equal(registry.surveys.some((survey) => survey.id === "hsc-ssp-pdr3"), false);

  const overlaps = await readJson("public", "data", "coverage", "external-overlaps.json");
  const summary = overlaps.surveySummaries.find((survey) => survey.surveyId === "hsc-ssp-pdr2");
  assert.equal(summary.coverageStatus, "resolved-moc");
  assert.equal(summary.confirmedTractCount, 228);
  assert.equal(overlaps.tracts.filter((row) => row[1].includes("hsc-ssp-pdr2")).length, 228);
});

test("HSC lensing is an exact 65-peak positional subset, not a footprint proxy", async () => {
  const artifact = await readJson("public", "data", "coverage", "hsc-public-products.json");
  const product = artifact.products.find((item) => item.surveyId === "hsc-lensing");
  assert.ok(product);
  assert.equal(product.status, "resolved-exact-lensing-peak-positional-support");
  assert.equal(product.eligibleAsFullRegistryFootprint, false);
  assert.equal(product.sourceRecordCount, 65);
  assert.equal(artifact.peaks.length, 65);
  assert.deepEqual(artifact.peaks.map((peak) => peak.rank), Array.from({ length: 65 }, (_, index) => index + 1));
  assert.deepEqual(product.confirmedRubinTractIds, [9451, 9453, 9454, 9695, 9696, 9697, 9698, 9937, 9938, 9939, 9940]);
  assert.equal(product.confirmedRubinTractCount, 11);
  assert.equal(artifact.hscImaging.surveyId, "hsc-ssp-pdr2");
  assert.match(product.note, /not a continuous shear catalog/i);

  const moc = await readFile(join(root, "public", product.supportMoc.publicPath.replace(/^\//, "")));
  const catalog = await readFile(join(root, "public", product.catalog.publicPath.replace(/^\//, "")));
  assert.equal(digest(moc), product.supportMoc.sha256);
  assert.equal(digest(catalog), product.catalog.sha256);
  assert.equal(catalog.toString("utf8").trim().split(/\r?\n/).length, 66);

  const overlaps = await readJson("public", "data", "coverage", "external-overlaps.json");
  assert.equal(overlaps.tracts.some((row) => row[1].includes("hsc-lensing")), false);
});

test("the acquired 50-region set remains stable across overlap refreshes", async () => {
  const selected = await readJson("public", "data", "coverage", "selected-regions.json");
  const rubin = await readJson("public", "data", "coverage", "rubin-pixels-50.json");
  assert.deepEqual(
    selected.regions.map((region) => region.tract).sort((a, b) => a - b),
    rubin.regions.filter((region) => region.status === "complete").map((region) => region.tract).sort((a, b) => a - b),
  );
  assert.match(selected.selectionMethod, /Acquisition-locked refresh/);
});
