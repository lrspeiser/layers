import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// The site had thirteen peer routes and no entry point -- the home page was a
// re-export of the coverage viewer, so a reader had to already know which page
// answered their question. /story is the way in: one argument, with the detail
// opened inline rather than scattered across navigation.

const story = readFileSync("app/story/page.tsx", "utf8");
const home = readFileSync("app/page.tsx", "utf8");

test("the home page is the story, not a dashboard", () => {
  assert.match(home, /\.\/story\/page/);
});

test("detail opens inline rather than on another route", () => {
  // Native details/summary: no JavaScript, keyboard accessible, and it degrades
  // to visible text if the CSS fails to load.
  const opens = story.match(/<details/g) ?? [];
  assert.ok(opens.length >= 4, `expected several expandable details, found ${opens.length}`);
  assert.match(story, /<summary>/);
});

test("it still routes to the specialist views", () => {
  for (const href of ["/data", "/explorer", "/coverage", "/goals"]) {
    assert.ok(story.includes(`href="${href}"`), `story should link to ${href}`);
  }
});

test("the numbers come from manifests, not prose", () => {
  // A narrative page is the easiest place for a number to go stale. These are
  // read from the same manifests the pipeline writes.
  assert.match(story, /threeWay\.after/);
  assert.match(story, /segments\.overall\.medianErrorBarUnderstatedBy/);
  assert.match(story, /release\.rows/);
  assert.match(story, /vetting\.counts/);
});

test("it says what is unresolved, not only what worked", () => {
  assert.match(story, /cannot explain/i);
  assert.match(story, /No astrophysical claim stands/i);
  // The mistakes stay on the page. A narrative that reports only the successes
  // is the failure mode this project has spent its time avoiding.
  assert.match(story, /our own mistake/i);
  assert.match(story, /0\.244/);
});

test("it does not import the 200 kB reliability manifest", () => {
  // Page imports are bundled; a 525 kB module has already broken every tract
  // page on this site once.
  assert.ok(
    !/catalogue-reliability/.test(story),
    "use the release manifest's summary rather than bundling the full file",
  );
});
