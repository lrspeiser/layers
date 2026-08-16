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

test("the page shows the data, it does not only describe it", () => {
  // The first version of this page was prose with expandable footnotes and no
  // images -- "a blog post with no punchline, no way to look at the data".
  // The fix was to lead with the worked example, so the assertion is about
  // images being present and the interactive view being reachable, not about
  // how many paragraphs fold open.
  const images = story.match(/<img/g) ?? [];
  assert.ok(images.length >= 3, `expected the comparison frames, found ${images.length} images`);
  assert.match(story, /rubin-r\.png/, "should show the Rubin frame");
  assert.match(story, /reference-r\.png/, "should show the reference frame");
  assert.match(story, /difference\.png/, "should show the difference");
  assert.match(story, /alt=/, "frames need alt text");
  assert.match(story, /href="\/explorer"/, "the reader must be able to go and look for themselves");
});

test("the worked example carries its own numbers", () => {
  // The punchline is that the same sky gives two different answers depending on
  // the reference. That only lands if both rankings are on the page.
  assert.match(story, /comparisons-ps1/, "needs the second reference's frames");
  assert.match(story, /rank: 4\b/);
  assert.match(story, /rank: 49\b/);
});

test("detail still opens inline rather than on another route", () => {
  assert.match(story, /<details/);
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
  // Matching intent rather than a phrase: an earlier version of this test broke
  // when a heading was reworded, which tests the copy editor and not the claim.
  assert.match(story, /unexplained|cannot explain|still open/i);
  assert.match(story, /No astrophysical claim stands/i);
  // The mistakes stay on the page. A narrative that reports only the successes
  // is the failure mode this project has spent its time avoiding.
  assert.match(story, /our own mistake/i);
  assert.match(story, /0\.244/);
});

test("it warns that the images are years apart", () => {
  // The scanner's boring explanations cover mask edges and PSF wings, not time.
  // A variable star, a moving star or an asteroid would currently rank as
  // unexplained, so the page has to say so where a reader will see it.
  assert.match(story, /yearsFromRubin/, "the epoch gaps should be read from the manifest");
  assert.match(story, /dipole/i, "proper motion has a distinctive signature worth naming");
  assert.match(story, /asteroid/i);
  assert.match(story, /should be read as a discovery|not.*discovery/i);
});

test("it does not import the 200 kB reliability manifest", () => {
  // Page imports are bundled; a 525 kB module has already broken every tract
  // page on this site once.
  assert.ok(
    !/catalogue-reliability/.test(story),
    "use the release manifest's summary rather than bundling the full file",
  );
});
