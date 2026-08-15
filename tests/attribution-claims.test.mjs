import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join, sep } from "node:path";
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

// A local path leaked into a public manifest in this session, in a file no test
// covered. The existing checks named specific manifests, so a new operator's
// output was unguarded by construction. This walks every published manifest
// instead, so the next new one is covered the moment it is written.
test("no published manifest leaks a local path or a credential", async () => {
  const { readdir } = await import("node:fs/promises");
  const layers = join(root, "public", "data", "layers");

  const walk = async (dir) => {
    const out = [];
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) out.push(...(await walk(full)));
      else if (entry.name.endsWith(".json")) out.push(full);
    }
    return out;
  };

  const files = await walk(layers);
  assert.ok(files.length > 50, "expected the published layer manifests to be present");

  const offenders = [];
  for (const file of files) {
    const text = await readFile(file, "utf8");
    // pipeline/results is gitignored because it holds pixels; publishing a path
    // into it advertises a layout no reader can use.
    if (/pipeline\/results|Authorization:|RUBIN_RSP_TOKEN|X-Amz-Signature/i.test(text)) {
      offenders.push(file.slice(root.length + 1).split(sep).join("/"));
    }
  }
  assert.deepEqual(offenders, [], `public manifests must not carry local paths or credentials`);
});

// The goal scorecard is the only place this repository states which objectives
// were reached. It is checked rather than trusted: a goal must not claim a
// status its own numbers do not support, and a ceiling that is merely the
// delivered value must say so, because "at-ceiling" would otherwise be circular.
test("the goal scorecard cannot claim more than it measured", async () => {
  const card = await readJson("public", "data", "layers", "goal-scorecard.json");
  assert.equal(card.goals.length, 11);

  for (const goal of card.goals) {
    const { id, statedTarget, archiveCeiling, delivered, status } = goal;
    assert.ok(Number.isFinite(delivered), `${id} must report a delivered number`);

    if (status === "met") {
      assert.ok(delivered >= statedTarget, `${id} claims met but delivered ${delivered} < ${statedTarget}`);
    }
    if (status === "at-ceiling") {
      assert.ok(Number.isFinite(archiveCeiling), `${id} claims at-ceiling without a ceiling`);
      assert.ok(delivered >= archiveCeiling, `${id} claims at-ceiling but is below it`);
      // A ceiling taken from the result makes the claim self-fulfilling. It is
      // allowed, but it must be labelled so nobody reads it as independent.
      assert.equal(typeof goal.ceilingIsIndependentOfResult, "boolean");
      if (!goal.ceilingIsIndependentOfResult) {
        assert.equal(goal.ceilingBasis, "the delivered value itself");
        assert.ok(goal.note && goal.note.length > 0, `${id} must explain a self-derived ceiling`);
      }
    }
    if (archiveCeiling !== null && statedTarget > archiveCeiling) {
      assert.equal(goal.targetExceedsCeilingBy, statedTarget - archiveCeiling);
      assert.ok(goal.note && goal.note.length > 0, `${id} must explain why its target exceeded the archive`);
    }
    assert.ok(goal.evidence && goal.evidence.length > 0, `${id} must name the manifest behind it`);
  }

  // Delivered numbers are a floor: these regress only if something broke.
  const byId = Object.fromEntries(card.goals.map((g) => [g.id, g]));
  assert.ok(byId.G0.delivered >= 167, "second-band regions regressed");
  assert.ok(byId.G1.delivered >= 521, "reconciled optical pairs regressed");
  assert.ok(byId.G9.delivered >= 12713, "comparisons evaluated regressed");

  // Reaching a ceiling is never a claim that a comparison is publishable.
  assert.equal(card.policy.comparisonReadyProducts, 0);
  assert.equal(card.policy.astrophysicalClaimsStanding, 0);
});
