import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));
const readText = async (...parts) => readFile(join(root, ...parts), "utf8");

// G10's complaint was that manifests exist and render nowhere. The goal
// scorecard was one of them: it answered "why 167 and not 180" in JSON that no
// visitor could reach. These tests keep it reachable and keep it honest.

test("the goals page renders every goal in the scorecard", async () => {
  const scorecard = await readJson("public", "data", "layers", "goal-scorecard.json");
  const page = await readText("app", "goals", "page.tsx");
  assert.ok(scorecard.goals.length >= 10, "expected the full goal set");
  // The page maps over goals rather than hardcoding them, so a new goal appears
  // without a code change. Assert the mapping rather than each id.
  assert.match(page, /scorecard\.goals/);
  assert.match(page, /goals\.map/);
});

test("it shows the three numbers that keep a shortfall distinguishable", async () => {
  const page = await readText("app", "goals", "page.tsx");
  // Asked-for, archive ceiling, delivered. Collapsing any two of these is what
  // makes an unreachable target look like a failure to deliver.
  assert.match(page, /asked for/);
  assert.match(page, /archives hold/);
  assert.match(page, /delivered/);
  assert.match(page, /statedTarget/);
  assert.match(page, /archiveCeiling/);
  assert.match(page, /goal\.delivered/);
});

test("a self-derived ceiling is marked as such", async () => {
  const scorecard = await readJson("public", "data", "layers", "goal-scorecard.json");
  const page = await readText("app", "goals", "page.tsx");
  const selfDerived = scorecard.goals.filter((g) => g.ceilingIsIndependentOfResult === false);
  assert.ok(selfDerived.length >= 1, "G2's ceiling is the delivered value itself");
  // Showing a self-derived ceiling as though it bounded the result would be the
  // dishonest version of this page.
  assert.match(page, /ceilingIsIndependentOfResult/);
  assert.match(page, /Self-derived/);
});

test("it repeats that no astrophysical claim stands", async () => {
  const scorecard = await readJson("public", "data", "layers", "goal-scorecard.json");
  const page = await readText("app", "goals", "page.tsx");
  assert.equal(scorecard.policy.comparisonReadyProducts, 0);
  assert.equal(scorecard.policy.astrophysicalClaimsStanding, 0);
  assert.match(page, /policy\.note/);
  assert.match(page, /comparisonReadyProducts/);
});

test("the page is reachable from the other pages", async () => {
  // A page nobody links to is the same as a manifest nobody renders. The link
  // now lives in the shared navigation rather than being repeated per page, so
  // this checks the one place that decides it.
  const nav = await readText("components", "SiteNav.tsx");
  assert.match(nav, /href: "\/goals"/, "the shared nav must reach the goals page");
  for (const parts of [
    ["app", "data", "page.tsx"],
    ["app", "explorer", "page.tsx"],
  ]) {
    const source = await readText(...parts);
    assert.match(source, /<SiteNav/, `${parts.join("/")} should use the shared navigation`);
  }
});

test("the goal count headline matches the scorecard", async () => {
  const scorecard = await readJson("public", "data", "layers", "goal-scorecard.json");
  const { met, atCeiling, belowCeiling } = scorecard.counts;
  assert.equal(met + atCeiling + belowCeiling, scorecard.goals.length);
  assert.ok(
    scorecard.counts.targetsExceedingArchiveCeiling > 0,
    "the headline claim is that several targets exceeded the archives",
  );
});
