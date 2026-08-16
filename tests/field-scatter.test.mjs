import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));

// This diagnosis is the kind of result most easily oversold: one significant
// correlation among eight, quoted as "the cause". These guard against that.
test("a weak correlation is not reported as an explanation", async () => {
  const d = await readJson("public", "data", "layers", "selected-regions", "field-scatter-diagnosis.json");
  assert.ok(d.counts.fields >= 30);
  assert.ok(d.counts.covariatesTested >= 6);

  // Share of variance must be published alongside rho, or a rho of 0.28 reads
  // as an explanation when it accounts for 8%.
  const shares = d.shareOfRankVarianceExplained;
  assert.ok(shares && Object.keys(shares).length > 0);
  for (const [key, share] of Object.entries(shares)) {
    const rho = d.covariates[key].rho;
    assert.ok(Math.abs(share - rho * rho) < 1e-3, `${key} share should be rho squared`);
  }

  // The verdict must say how much is explained, not only what correlates.
  assert.match(d.verdict, /rank variance/);
  assert.match(d.verdict, /does not explain|still unaccounted/i);
});

test("null covariates are reported as ruling out, not as absent", async () => {
  const d = await readJson("public", "data", "layers", "selected-regions", "field-scatter-diagnosis.json");
  const nulls = Object.entries(d.covariates).filter(([, v]) => v.rho !== null && !v.significant);
  assert.ok(nulls.length >= 3, "expected several null covariates");
  assert.ok(d.whatThisRulesOut && d.whatThisRulesOut.length > 40);

  // Every covariate must name which suspect it tests, or a null means nothing.
  for (const [key, value] of Object.entries(d.covariates)) {
    assert.ok(value.suspect, `${key} does not say what it tests`);
    assert.ok(value.describes, `${key} has no description`);
  }
  // The limit of a null result must travel with it.
  assert.match(d.limits, /correlated with each other|not what causes/i);
});
