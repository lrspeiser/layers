import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render(pathname = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${pathname}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${pathname}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the data-first atlas and honest ingest state", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /Rubin Missing Light Atlas/);
  assert.match(html, /Compare verified pixels, galaxy by galaxy/);
  assert.match(html, /Real legacy pixels are ready/);
  assert.match(html, /\/legacy\/ngc-300\/spitzer-irac1\.png/);
  assert.match(html, /No published pixels/);
  assert.doesNotMatch(html, /rubin-virgo\.jpg/);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|react-loading-skeleton/);
});

test("server-renders a gated permanent object record", async () => {
  const response = await render("/galaxy/ngc-300");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /NGC 300/);
  assert.match(html, /PERMANENT TARGET RECORD/);
  assert.match(html, /No numerical discrepancy is asserted/);
  assert.match(html, /Honest empty state/);
  assert.match(html, /real public Spitzer\/IRAC 3\.6 μm cutout/);
  assert.doesNotMatch(html, /rubin-virgo\.jpg/);
});

test("ships four unique public Spitzer previews and a real overlap audit", async () => {
  const slugs = ["ngc-300", "ngc-55", "ngc-7793", "ngc-24"];
  const buffers = await Promise.all(slugs.map((slug) => readFile(new URL(`../public/legacy/${slug}/spitzer-irac1.png`, import.meta.url))));
  const hashes = buffers.map((buffer) => createHash("sha256").update(buffer).digest("hex"));
  assert.equal(new Set(hashes).size, slugs.length);

  const audit = JSON.parse(await readFile(new URL("../public/data/public-legacy-overlap.json", import.meta.url), "utf8"));
  assert.equal(audit.rubinCoverageActuallyQueried, false);
  assert.equal(audit.targets["ngc-300"].spitzerSeip.coverage, "covered");
  assert.equal(audit.targets["eso-116-g012"].spitzerSeip.coverage, "not-covered");
});

test("comparison code is manifest-gated and pipeline checks duplicates", async () => {
  const [layout, comparison, manifestContract, publisher, validator] = await Promise.all([
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/GalaxyComparison.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/atlas-manifest.ts", import.meta.url), "utf8"),
    readFile(new URL("../pipeline/publish_verified.py", import.meta.url), "utf8"),
    readFile(new URL("../pipeline/validate_release.py", import.meta.url), "utf8"),
  ]);

  assert.match(layout, /summary_large_image/);
  assert.match(layout, /og\.png/);
  assert.match(comparison, /manifestUrl/);
  assert.match(manifestContract, /manifest\.json/);
  assert.match(comparison, /verified === true/);
  assert.doesNotMatch(comparison, /rubin-virgo/);
  assert.match(publisher, /byte-identical/);
  assert.match(validator, /duplicates/);
});
