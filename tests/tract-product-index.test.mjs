import test from "node:test";
import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

test("tract product index exposes real files and never promotes a display to a science comparison", async () => {
  const path = join(root, "public", "data", "layers", "tract-product-index.json");
  const text = await readFile(path, "utf8");
  const index = JSON.parse(text);
  // Was pinned at 50 from the original region set. The index now covers the
  // 200-tract acquisition, so this guards that it is populated rather than
  // that it has one exact size. The assertions that actually protect
  // correctness are below: comparisonReady stays 0, no science claim is
  // allowed, no local paths or credentials leak, and every referenced
  // preview file exists on disk.
  assert.ok(index.summary.tractCount >= 50);
  assert.ok(index.summary.familyCounts.optical >= 50);
  assert.ok(index.summary.familyCounts.radio >= 3);
  assert.ok(index.summary.familyCounts["uv-ir"] >= 50);
  assert.ok(index.summary.familyCounts["time-domain"] >= 1);
  assert.ok(index.summary.familyCounts["high-energy"] >= 1);
  assert.ok(index.summary.familyCounts["neutral-gas"] >= 1);
  assert.ok(index.summary.familyCounts["cmb-large-scale-structure"] >= 1);
  assert.equal(index.summary.comparisonReadyCount, 0);
  assert.equal(index.policy.scienceClaimAllowed, false);
  assert.doesNotMatch(text, /pipeline\/results|Authorization|RUBIN_RSP_TOKEN|X-Amz-Signature/i);
  for (const product of index.products.filter((item) => item.viewerReady)) {
    assert.equal(product.displayReady, true);
    assert.equal(product.comparisonReady, false);
    assert.ok(product.blockers.length > 0);
    for (const key of ["rubinImage", "referenceImage", "coverageImage", "overlayImage"]) {
      assert.match(product[key], /^\//);
      await access(join(root, "public", product[key]));
    }
  }
});
