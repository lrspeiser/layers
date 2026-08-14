import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

test("goal audit cannot report success unless every objective gate passes", async () => {
  const audit = JSON.parse(await readFile(join(root, "public", "data", "coverage", "goal-audit.json"), "utf8"));
  assert.equal(audit.objectiveAchieved, Object.values(audit.gates).every(Boolean));
  assert.equal(audit.gates.entireRubinFootprintIndexed, true);
  assert.equal(audit.counts.rubinTracts, 2191);
  assert.ok(audit.counts.selectedRegions >= 50);
  assert.ok(audit.counts.rubinScienceReadyRegions >= 50);
  assert.equal(audit.counts.comparisonReadyProducts, 0);
  assert.deepEqual(Object.keys(audit.pixelFamilyRegionCounts), [
    "optical", "uv-ir", "radio", "x-ray", "gas", "time-domain", "lensing",
  ]);
  for (const [family, regionCount] of Object.entries(audit.pixelFamilyRegionCounts)) {
    assert.ok(regionCount > 0, `${family} must have at least one real display-pixel demonstration`);
  }
  assert.equal(audit.gates.everyRequiredPixelFamilyDemonstrated, true);
  assert.equal(audit.gates.allCoverageResolvedExactly, true);
  assert.equal(audit.coverageBySurvey["kids-1000-lensing"].tractCount, 13);
  assert.equal(audit.coverageBySurvey["hsc-ssp-pdr2"].tractCount, 228);
  assert.equal(audit.coverageBySurvey["hsc-lensing"].tractCount, 11);
  assert.equal(audit.coverageBySurvey["desi-dr1"].tractCount, 878);
  assert.notEqual(audit.coverageBySurvey["kids-1000-lensing"].semantics, "unresolved");
  assert.notEqual(audit.coverageBySurvey["hsc-lensing"].semantics, "unresolved");
  assert.deepEqual(audit.unresolvedCoverageSurveyIds, []);
  assert.equal(audit.objectiveAchieved, true);
});
