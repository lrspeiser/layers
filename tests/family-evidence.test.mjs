import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

const root = new URL("..", import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1));

async function render(pathname) {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${pathname}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request(`http://localhost${pathname}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("tract 9813 exposes real spectroscopy and X-ray evidence without promoting lensing", async () => {
  const response = await render("/tract/9813/evidence");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Rubin tract 9813/);
  assert.match(html, /OVERLAP EVIDENCE, NOT A DIFFERENCE MEASUREMENT/);
  assert.match(html, /7,781/);
  assert.match(html, /0\.121737/);
  assert.match(html, /29(?:<!-- -->)? eRASS1 sources/);
  assert.match(html, /Catalog markers are never rendered as image pixels/);
  assert.match(html, /LENSING · NO VALIDATED PRODUCT/);
  assert.match(html, /Intentionally unresolved/);
  assert.match(html, /desi-edr-tract-9813-spectrum\.csv/);
  assert.match(html, /erass1-tract-9813\.csv/);
});

test("tract 5061 exposes the real HIPASS H I detection as a catalog product", async () => {
  const response = await render("/tract/5061/evidence");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Rubin tract 5061/);
  assert.match(html, /HIPASS records a 21-cm H I line detection/);
  assert.match(html, /J0318-27/);
  assert.match(html, /21\.5(?:<!-- -->)? Jy km\/s/);
  assert.match(html, /12\.47(?:<!-- -->)? arcmin/);
  assert.match(html, /not a spatial H I moment map or spectral cube/);
  assert.doesNotMatch(html, /REAL PRODUCT[\s\S]*LENSING · NO VALIDATED PRODUCT/);
});

test("tract evidence manifest routes and public downloads are checksum-valid", async () => {
  const manifest = JSON.parse(await readFile(join(root, "public", "data", "layers", "family-examples", "tract-manifest.json"), "utf8"));
  assert.deepEqual(manifest.routes.map((route) => [route.tract, route.href]), [
    [9813, "/tract/9813/evidence"],
    [5061, "/tract/5061/evidence"],
  ]);
  assert.equal(manifest.routes[0].unresolved[0].family, "lensing");
  assert.equal(manifest.routes[0].unresolved[0].status, "unresolved");
  for (const route of manifest.routes) {
    for (const evidence of route.evidence) {
      for (const download of evidence.downloads) {
        const path = join(root, "public", ...download.href.split("/").filter(Boolean));
        const data = await readFile(path);
        assert.equal(data.byteLength, download.bytes);
        assert.equal(createHash("sha256").update(data).digest("hex"), download.sha256);
      }
    }
  }
});
