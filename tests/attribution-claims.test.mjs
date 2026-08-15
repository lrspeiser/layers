import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));

// The attribution operator is the only place in this project that says a
// measured effect belongs to a named survey. That is the strongest kind of
// statement here, so these tests guard the conditions that make it sayable
// rather than the values it currently produces.
test("cross-survey attribution rests on a shared sample large enough to trust", async () => {
  const check = await readJson("public", "data", "layers", "selected-regions", "reference-cross-check.json");

  // Attribution logic is "Rubin is the only shared term". With no shared
  // regions there is nothing to compare and every verdict is vacuous.
  assert.ok(check.counts.sharedRegions >= check.counts.sharedRegionThreshold);
  assert.equal(check.counts.sharedSampleSufficient, true);

  // An 18-field correlation in this project already had to be retracted at 115
  // fields, which is why the threshold exists at all.
  assert.ok(check.counts.sharedRegionThreshold >= 40);

  // Every finding must carry the evidence that produced it, so a verdict can
  // never be read without its basis.
  assert.ok(check.findings.length >= 3);
  for (const finding of check.findings) {
    assert.ok(finding.question.length > 0);
    assert.ok(finding.verdict.length > 0);
    assert.ok(finding.basis.length > 0);
  }

  // The density correlation is measured with and without the reconciliation QA
  // cut, because QA failure is not independent of field density. A result that
  // only appears on one side of that cut is the cut talking.
  const sensitivity = check.findings.find((item) => item.qaFilterSensitivity)?.qaFilterSensitivity;
  assert.ok(sensitivity, "the density finding must carry its QA-filter sensitivity");
  assert.equal(sensitivity.answerDependsOnQaFilter, false);
  assert.equal(check.qaFilterChangesAnswer, false);
  for (const survey of ["legacy", "des"]) {
    assert.ok(Number.isFinite(sensitivity.matchedOnly[survey].rho));
    assert.ok(Number.isFinite(sensitivity.allRegions[survey].rho));
    // Same sign on both sides of the cut, or the attribution is not stable.
    assert.ok(sensitivity.matchedOnly[survey].rho * sensitivity.allRegions[survey].rho > 0);
  }
});

test("no operator promotes a comparison to publishable, and no credentials leak", async () => {
  for (const relative of [
    ["public", "data", "layers", "selected-regions", "reference-cross-check.json"],
    ["public", "data", "layers", "site-summary.json"],
  ]) {
    const text = await readFile(join(root, ...relative), "utf8");
    assert.doesNotMatch(text, /Authorization|RUBIN_RSP_TOKEN|X-Amz-Signature/i);
  }

  // The register's candidate count is meaningless without the number of
  // comparisons it came out of; the site prints both, so the summary must
  // carry both.
  const summary = await readJson("public", "data", "layers", "site-summary.json");
  assert.ok(summary.register.comparisonsEvaluated > 0);
  assert.ok(summary.register.candidates >= 0);
  assert.ok(summary.register.candidates < summary.register.comparisonsEvaluated);
});
