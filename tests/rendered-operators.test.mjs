import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// G10 was the one goal recorded as "not machine-checkable", on the grounds that
// its evidence is rendered pages rather than a manifest. That was a gap in the
// checking, not a property of the goal: a page either renders its operator
// results or it does not.
//
// Making it checkable immediately found a real defect. `/differences` built its
// attribution list by aliasing an imported JSON array and pushing onto it. Node
// caches imported modules, so the push mutated the module object and every
// render of the page appended another entry -- the curve-of-growth question
// appeared ten times on a server that had been up a while, and exactly once in a
// fresh build, which is why no static check had ever caught it.
//
// These tests read the built page source rather than a live server, so they run
// in the normal suite. The mutation bug is caught structurally: the list that
// gets pushed onto must be a copy.

const differences = readFileSync("app/differences/page.tsx", "utf8");

test("lists that are appended to are copied, not aliased", () => {
  // The specific failure: `const x = (json.a ?? [])` then `x.push(...)`.
  // An imported JSON module is shared across renders, so this accumulates.
  const pushed = [...differences.matchAll(/(\w+)\.push\(/g)].map((m) => m[1]);
  for (const name of new Set(pushed)) {
    const declaration = new RegExp(`const ${name} = ([^;]+);`, "s").exec(differences);
    if (!declaration) continue;
    const initialiser = declaration[1];
    // A copy looks like [...x] or x.slice() or a fresh literal.
    const copied =
      initialiser.includes("[...") ||
      initialiser.includes(".slice()") ||
      initialiser.includes(".map(") ||
      /^\s*\[/.test(initialiser);
    assert.ok(
      copied,
      `${name} is pushed onto but initialised by reference: ${initialiser.slice(0, 90)}`,
    );
  }
});

test("the attribution list is built from a copy of the findings", () => {
  assert.match(
    differences,
    /const attribution = \[\.\.\./,
    "attribution must be a copy; aliasing it makes the page grow on every render",
  );
});

test("the operator page still renders the questions it claims", () => {
  // Guarding the content, not only the mechanism: if the attribution block were
  // deleted the copy test above would pass vacuously.
  assert.match(differences, /question:/);
  assert.match(differences, /aperture effect or a zeropoint/);
});

test("the scorecard's G10 claim names where it renders", () => {
  const card = JSON.parse(readFileSync("public/data/layers/goal-scorecard.json", "utf8"));
  const g10 = card.goals.find((g) => g.id === "G10");
  assert.ok(g10, "G10 must be on the scorecard");
  assert.ok(g10.evidence && g10.evidence.length > 0, "G10 must cite where it renders");
  assert.ok(
    typeof g10.delivered === "number" && g10.delivered > 0,
    "G10 must claim a countable number of rendered result sets",
  );
});
