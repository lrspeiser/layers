import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));

// The cone search is the one interface another scientist's code can call. What
// it must never do is serve the source catalogue: those Rubin fluxes measure
// access-restricted DP2 pixels, and DATA_STORAGE.md forbids publishing derived
// science bytes for that reason.
test("the cone search serves only positions the project is allowed to publish", async () => {
  const source = await readFile(join(root, "app", "api", "scs", "route.ts"), "utf8");

  // It may import public layer data, and must not reach for the catalogue.
  assert.doesNotMatch(source, /source-catalogue/);
  assert.doesNotMatch(source, /rubin-reference-sources/);
  assert.doesNotMatch(source, /rubin_flux/);
  assert.doesNotMatch(source, /pipeline\/results/);
  // The restriction is stated in the route itself, so the next person editing it
  // sees why before adding a column.
  assert.match(source, /access-restricted/);

  const catalogue = await readJson("public", "data", "layers", "selected-regions", "source-catalogue.json");
  assert.equal(catalogue.products.published, false);
});

test("the cone search emits a VOTable with the fields a client needs", async () => {
  const source = await readFile(join(root, "app", "api", "scs", "route.ts"), "utf8");
  // pyvo and TOPCAT locate columns by UCD, not by name. Without the main
  // position UCDs the response loads but the client cannot plot it.
  assert.match(source, /pos\.eq\.ra;meta\.main/);
  assert.match(source, /pos\.eq\.dec;meta\.main/);
  assert.match(source, /meta\.id;meta\.main/);
  assert.match(source, /application\/x-votable\+xml/);
  // A cone search that cannot report an error is not a cone search.
  assert.match(source, /QUERY_STATUS" value="ERROR/);
});

test("machine access documents the decision that blocks the rest", async () => {
  const doc = await readFile(join(root, "docs", "MACHINE_ACCESS.md"), "utf8");
  assert.match(doc, /data rights/i);
  assert.match(doc, /MOC/);
  // Three named options, so the decision is presented rather than made here.
  assert.match(doc, /Serve nothing derived from Rubin pixels/);
  assert.match(doc, /behind data-rights authentication/);
});
