import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// optical-coverage-truth.json labelled 21 pairs "lostInReconciliation" without
// saying which regions or why. This project has twice been bitten by losses that
// were invisible in a total, so the label had to become a list of named regions
// with a reason each. These tests keep it that way.

const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const losses = read("public/data/layers/selected-regions/reconciliation-losses.json");
const truth = read("public/data/layers/selected-regions/optical-coverage-truth.json");

test("every loss has a reason", () => {
  assert.equal(
    losses.unexplainedTotal,
    0,
    "a region that had pixels and produced no pair must say why, or it is a silent drop",
  );
});

test("the audit reproduces the established validated counts", () => {
  // If it cannot reproduce these it is measuring something else, and its loss
  // counts mean nothing. An earlier version counted 200 DES regions against a
  // true 148 and invented 57 losses.
  // HSC added 2026-08-16 once PDR2 science coadds were fetched with data rights.
  const expected = {
    "legacy-surveys-dr10": 198,
    "des-dr2": 148,
    "panstarrs-dr2": 196,
    "hsc-ssp-pdr2": 110,
  };
  for (const survey of losses.surveys) {
    assert.equal(
      survey.validatedPixels,
      expected[survey.surveyId],
      `${survey.surveyId} validated count must match optical-coverage-truth`,
    );
  }
  const total = losses.surveys.reduce((sum, s) => sum + s.validatedPixels, 0);
  assert.equal(total, truth.totals.pixelsValidated);
});

test("each loss is attributed to a recorded failure or a missing product", () => {
  for (const survey of losses.surveys) {
    assert.equal(
      survey.lost,
      survey.explainedByRecordedFailure + survey.explainedByMissingProduct + survey.unexplained,
      `${survey.surveyId} loss accounting must balance`,
    );
    for (const [region, reason] of Object.entries(survey.lostRegions)) {
      assert.notEqual(reason, "unresolved", `${region} still has no reason`);
      assert.ok(reason.length > 10, `${region} needs a reason, not a token`);
    }
  }
});

test("the four Rubin-side gaps are named, since they are not an archive gap", () => {
  // These fail against all three references. Surviving a change of reference is
  // what makes them Rubin-side rather than a hole in someone else's survey.
  assert.deepEqual(losses.regionsWithNoRubinProduct, [
    "dp2-tract-8999",
    "dp2-tract-9241",
    "dp2-tract-9935",
    "dp2-tract-9936",
  ]);
  // A Rubin-side gap can only show up as a loss for a survey that actually
  // reached that region. HSC covers 110 of the 200 and never attempted 8999, so
  // requiring it there would assert a loss that could not have happened. The
  // claim being tested is that these regions fail against every reference that
  // *tried* them, and that at least two independent references did.
  for (const region of losses.regionsWithNoRubinProduct) {
    const attempted = losses.surveys.filter((s) => region in s.lostRegions);
    assert.ok(
      attempted.length >= 2,
      `${region} should fail against at least two references, not ${attempted.length}`,
    );
    for (const survey of attempted) {
      assert.match(survey.lostRegions[region], /no Rubin product/);
    }
  }
});

test("the finding is reproducible from published files alone", () => {
  const script = readFileSync("pipeline/audit_reconciliation_losses.py", "utf8");
  assert.ok(
    !/pipeline\/results/.test(script),
    "must not depend on restricted pixels, or nobody outside can check it",
  );
  assert.match(losses.reproduce, /audit_reconciliation_losses/);
});
