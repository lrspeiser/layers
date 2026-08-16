import test from "node:test";
import assert from "node:assert/strict";
import { access, readFile, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const readJson = async (...parts) => JSON.parse(await readFile(join(root, ...parts), "utf8"));

// The catalogue is published now. These assert the opposite of what the earlier
// tests asserted: that the files exist and are reachable at the paths the site
// and the cone search advertise.
test("every published catalogue file exists at the advertised path", async () => {
  const release = await readJson("public", "data", "layers", "selected-regions", "catalogue-release.json");
  assert.equal(release.published, true);
  assert.ok(release.rows > 10000);

  for (const key of ["parquet", "votableGzip"]) {
    const file = release.files[key];
    if (!file) continue;
    assert.match(file.path, /^\/data\/catalogue\//);
    const onDisk = join(root, "public", file.path.slice(1));
    await access(onDisk);
    const info = await stat(onDisk);
    // A stated byte count that does not match the file would make the checksum
    // meaningless too.
    assert.equal(info.size, file.bytes, `${key} size does not match the manifest`);
    assert.match(file.sha256, /^[0-9a-f]{64}$/);
  }
});

test("the cone search tiles cover every source and are reachable", async () => {
  const release = await readJson("public", "data", "layers", "selected-regions", "catalogue-release.json");
  const { readdir } = await import("node:fs/promises");
  const tileDir = join(root, "public", release.coneSearch.tiles.root.slice(1));
  const files = (await readdir(tileDir)).filter((f) => f.endsWith(".json"));
  assert.equal(files.length, release.coneSearch.tiles.count);

  // Every source must live in exactly one tile, or a cone search silently
  // misses rows that are in the bulk download.
  let total = 0;
  for (const name of files) {
    const tile = JSON.parse(await readFile(join(tileDir, name), "utf8"));
    assert.ok(Array.isArray(tile.sources) && tile.sources.length > 0, `${name} is empty`);
    for (const source of tile.sources) {
      assert.ok(Number.isFinite(source.ra_deg) && Number.isFinite(source.dec_deg));
      // The tile name must match the position, or the lookup returns the wrong file.
      const ra = String(Math.floor(source.ra_deg)).padStart(3, "0");
      const dec = Math.floor(source.dec_deg);
      const expected = `${ra}_${dec < 0 ? "-" : "+"}${String(Math.abs(dec)).padStart(2, "0")}.json`;
      assert.equal(name, expected, `${source.source_id} is in the wrong tile`);
    }
    total += tile.sources.length;
  }
  assert.equal(total, release.rows, "tiles do not contain every published source");
});

test("the release tells a reader which column to trust", async () => {
  const release = await readJson("public", "data", "layers", "selected-regions", "catalogue-release.json");
  assert.match(release.whichSignificance, /departure_significance/);
  // The two misleading columns must be named as misleading, not merely listed.
  assert.match(release.whichSignificance, /difference_significance/);
  assert.match(release.whichSignificance, /Poisson/);
  assert.match(release.caveat, /not a detection/i);

  // Every published column needs a unit, a UCD and a description, or the table
  // cannot be interpreted by anyone who did not write it.
  for (const [name, meta] of Object.entries(release.columns)) {
    assert.equal(typeof meta.unit, "string", `${name} has no unit field`);
    assert.ok(meta.ucd && meta.ucd.length > 0, `${name} has no UCD`);
    assert.ok(meta.description && meta.description.length > 10, `${name} is undescribed`);
  }
  assert.match(release.columns.departure_significance.description, /USE THIS ONE/);
});

test("the cone search route serves the catalogue, not just positions", async () => {
  const source = await readFile(join(root, "app", "api", "scs", "route.ts"), "utf8");
  assert.match(source, /catalogue-release\.json/);
  assert.match(source, /pos\.eq\.ra;meta\.main/);
  assert.match(source, /departure_significance/);
  // An unbounded radius would read every tile and return the whole catalogue.
  assert.match(source, /SR must be between/);
  assert.match(source, /QUERY_STATUS" value="ERROR/);
});
