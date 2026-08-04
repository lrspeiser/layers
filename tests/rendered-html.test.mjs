import assert from "node:assert/strict";
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
  assert.match(html, /No substitute image shown/);
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
  assert.doesNotMatch(html, /rubin-virgo\.jpg/);
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
