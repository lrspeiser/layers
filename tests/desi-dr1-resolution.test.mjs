import test from "node:test";
import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

test("DESI DR1 coverage uses the official good-data product index instead of a false zero", async () => {
  const path = join(root, "public", "data", "coverage", "desi-dr1-resolution.json");
  const text = await readFile(path, "utf8");
  const evidence = JSON.parse(text);
  assert.equal(evidence.surveyId, "desi-dr1");
  assert.equal(evidence.source.sha256, "cbfcc85ffecc78e3338e022c7fdc013c0efea1603a039a0381047e85e0f6e5ff");
  assert.equal(evidence.source.healpixOrder, 6);
  assert.ok(evidence.confirmedRubinTractCount > 0);
  assert.ok(evidence.confirmedRubinTractIds.includes(9813));
  assert.match(evidence.coverageSemantics, /not continuous target sampling/i);
  assert.doesNotMatch(text, /pipeline\/results|Authorization|token/i);
  await access(join(root, "public", evidence.moc.href));
});
