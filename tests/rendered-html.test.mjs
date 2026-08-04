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

test("server-renders the atlas landing page", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /Rubin Missing Light Atlas/);
  assert.match(html, /What does Rubin see that older telescopes did not\?/);
  assert.match(html, /SPARC audit/);
  assert.match(html, /Prototype release/);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|react-loading-skeleton/);
});

test("server-renders a permanent object record", async () => {
  const response = await render("/galaxy/ngc-300");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /NGC 300/);
  assert.match(html, /PERMANENT OBJECT RECORD/);
  assert.match(html, /Prototype notice/);
});

test("ships social metadata and a machine-readable sample", async () => {
  const [layout, sample] = await Promise.all([
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../public/sample-package.json", import.meta.url), "utf8"),
  ]);

  assert.match(layout, /summary_large_image/);
  assert.match(layout, /og\.png/);
  const parsed = JSON.parse(sample);
  assert.equal(parsed.object_id, "ngc-300");
  assert.match(parsed.notice, /illustrative/);
});
