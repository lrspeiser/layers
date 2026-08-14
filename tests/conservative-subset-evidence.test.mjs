import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const load = (path) => readFile(join(root, path), "utf8").then(JSON.parse);

const expectedCounts = new Map([
  ["wallaby-pdr2", 0],
  ["alfalfa-alpha100", 448],
  ["resolved-hi-archives", 8],
  ["alma", 7],
]);

test("conservative evidence remains separate from full-footprint overlap claims", async () => {
  const [evidence, overlaps, footprint] = await Promise.all([
    load("public/data/coverage/conservative-subset-evidence.json"),
    load("public/data/coverage/external-overlaps.json"),
    load("public/data/coverage/rubin-dp2-footprint.json"),
  ]);

  assert.equal(evidence.schemaVersion, 1);
  assert.equal(evidence.rubinIndex.tractCount, 2191);
  assert.equal(footprint.counts.tracts, 2191);
  assert.deepEqual(evidence.semantics.fullFootprintSurveyIds, ["wallaby-pdr2"]);
  assert.deepEqual(evidence.semantics.conservativeSubsetSurveyIds, [
    "alfalfa-alpha100",
    "alma",
    "resolved-hi-archives",
  ]);
  assert.match(evidence.semantics.warning, /must not be merged into full-footprint overlap counts/);

  const footprintTracts = new Set(footprint.tracts.map((row) => row[0]));
  const bySurvey = new Map(evidence.surveyEvidence.map((item) => [item.surveyId, item]));
  assert.deepEqual(new Set(bySurvey.keys()), new Set(expectedCounts.keys()));
  for (const [surveyId, expectedCount] of expectedCounts) {
    const item = bySurvey.get(surveyId);
    assert.equal(item.confirmedRubinTractCount, expectedCount);
    assert.equal(item.confirmedRubinTractIds.length, expectedCount);
    assert.equal(new Set(item.confirmedRubinTractIds).size, expectedCount);
    assert.ok(item.confirmedRubinTractIds.every((tract) => footprintTracts.has(tract)));
    assert.match(item.derivedMoc.sha256, /^[a-f0-9]{64}$/);
    assert.ok(item.evidence.length > 0);
    for (const source of item.evidence) {
      assert.match(source.sourceUrl, /^https:\/\//);
      assert.match(source.sha256, /^[a-f0-9]{64}$/);
      assert.ok(source.bytes > 0);
    }
  }

  assert.equal(bySurvey.get("wallaby-pdr2").eligibleAsFullRegistryFootprint, true);
  for (const surveyId of evidence.semantics.conservativeSubsetSurveyIds) {
    assert.equal(bySurvey.get(surveyId).eligibleAsFullRegistryFootprint, false);
  }

  const conservativeIds = new Set(evidence.semantics.conservativeSubsetSurveyIds);
  for (const row of overlaps.tracts) {
    assert.ok(row[1].every((surveyId) => !conservativeIds.has(surveyId)),
      `production full-footprint overlaps overstate conservative evidence in tract ${row[0]}`);
  }
});

test("all unresolved surveys have a specific blocker and next action", async () => {
  const evidence = await load("public/data/coverage/conservative-subset-evidence.json");
  const expected = new Set([
    "act-dr6",
    "des-y3-lensing",
    "hsc-lensing",
    "hsc-ssp-pdr3",
    "kids-1000-lensing",
    "sdss-dr19",
    "spt-3g",
  ]);
  assert.deepEqual(new Set(evidence.semantics.unresolvedSurveyIds), expected);
  assert.deepEqual(new Set(evidence.unresolved.map((item) => item.surveyId)), expected);
  for (const item of evidence.unresolved) {
    assert.equal(item.status, "unresolved");
    assert.ok(item.blocker.length > 40);
    assert.ok(item.nextAction.length > 40);
    assert.ok(item.evidenceUrls.length > 0);
    assert.ok(item.evidenceUrls.every((url) => url.startsWith("https://")));
  }
});
